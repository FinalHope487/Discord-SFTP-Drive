"""The trash: deleting in two steps, and the tag that makes it honest.

Deleting used to destroy. Now `remove` marks a node and `purge` destroys it,
which buys a way back and costs one field -- `trashed_at` -- that decides
whether a file is visible at all. A field with that much power sitting outside
the integrity tags would have handed anybody with database access a silent
delete, which is exactly what `dir_entries_tag` was added to prevent, so it is
inside them. The tampering tests at the bottom are the ones that prove it; the
rest would pass with the whole tag change reverted.

Tampering writes to `fake_db.nodes.docs` directly, the same threat model as
`test_node_identity.py`: database access, no key.
"""

import os

import pytest

from src.crypto import IntegrityError
from src.vfs import AlreadyExists, NotEmpty, NotFound, Unsupported
from tests.conftest import TEST_CHUNK_SIZE

PAYLOAD = os.urandom(TEST_CHUNK_SIZE + 500)


async def _write(vfs, path, data=b"hello"):
    handle = await vfs.open(path, read=False, write=True, create=True)
    await handle.write_at(0, data)
    await handle.close()


def _doc(fake_db, **match):
    for doc in fake_db.nodes.docs:
        if all(doc.get(k) == v for k, v in match.items()):
            return doc
    raise AssertionError(f"no node matching {match}")


def _names(entries):
    return sorted(e["filename"] for e in entries)


# --------------------------------------------------------------- the basics


async def test_removing_a_file_hides_it_but_keeps_it(vfs):
    await _write(vfs, "/notes.txt")
    await vfs.remove("/notes.txt")

    assert _names(await vfs.list_dir("/")) == []
    assert await vfs.get_node("/notes.txt") is None

    [item] = await vfs.list_trash()
    assert item["node"]["filename"] == "notes.txt"
    assert item["path"] == "/notes.txt"
    assert item["node"]["trashed_at"] > 0


async def test_the_original_path_survives_nesting(vfs):
    await vfs.makedir("/a")
    await vfs.makedir("/a/b")
    await _write(vfs, "/a/b/deep.txt")

    await vfs.remove("/a/b/deep.txt")

    [item] = await vfs.list_trash()
    assert item["path"] == "/a/b/deep.txt"


async def test_restoring_puts_it_back(vfs):
    await _write(vfs, "/notes.txt", b"original")
    await vfs.remove("/notes.txt")

    [item] = await vfs.list_trash()
    result = await vfs.restore(item["node"]["id"])

    assert result == {"restored": True, "conflict": False, "path": "/notes.txt"}
    assert _names(await vfs.list_dir("/")) == ["notes.txt"]
    assert await vfs.list_trash() == []

    handle = await vfs.open("/notes.txt", read=True, write=False)
    try:
        assert await handle.read_at(0, 8) == b"original"
    finally:
        await handle.close()


async def test_a_trashed_name_is_free_for_reuse(vfs):
    """The trashed node keeps its name and its place, so this has to work."""
    await _write(vfs, "/notes.txt", b"first")
    await vfs.remove("/notes.txt")
    await _write(vfs, "/notes.txt", b"second")

    assert _names(await vfs.list_dir("/")) == ["notes.txt"]
    assert len(await vfs.list_trash()) == 1

    handle = await vfs.open("/notes.txt", read=True, write=False)
    try:
        assert await handle.read_at(0, 6) == b"second"
    finally:
        await handle.close()


# ------------------------------------------------------- restore conflicts


async def _trashed_then_retaken(vfs):
    await _write(vfs, "/notes.txt", b"old")
    await vfs.remove("/notes.txt")
    await _write(vfs, "/notes.txt", b"new")
    [item] = await vfs.list_trash()
    return item["node"]["id"]


async def test_restore_refuses_a_taken_name_by_default(vfs):
    node_id = await _trashed_then_retaken(vfs)
    with pytest.raises(AlreadyExists):
        await vfs.restore(node_id)


async def test_restore_skip_changes_nothing(vfs):
    node_id = await _trashed_then_retaken(vfs)

    result = await vfs.restore(node_id, on_conflict="skip")

    assert result["restored"] is False
    assert len(await vfs.list_trash()) == 1
    handle = await vfs.open("/notes.txt", read=True, write=False)
    try:
        assert await handle.read_at(0, 3) == b"new"
    finally:
        await handle.close()


async def test_restore_replace_sends_the_occupant_to_the_trash(vfs):
    """Replace must not be the one operation that loses data outright."""
    node_id = await _trashed_then_retaken(vfs)

    result = await vfs.restore(node_id, on_conflict="replace")

    assert result["restored"] is True and result["conflict"] is True
    handle = await vfs.open("/notes.txt", read=True, write=False)
    try:
        assert await handle.read_at(0, 3) == b"old"
    finally:
        await handle.close()

    # The file that was standing there is recoverable, not destroyed.
    [displaced] = await vfs.list_trash()
    assert displaced["node"]["id"] != node_id


async def test_restore_keep_both_renames_the_arrival(vfs):
    node_id = await _trashed_then_retaken(vfs)

    result = await vfs.restore(node_id, on_conflict="keep_both")

    assert result["path"] == "/notes (2).txt"
    assert _names(await vfs.list_dir("/")) == ["notes (2).txt", "notes.txt"]
    assert await vfs.list_trash() == []


async def test_keep_both_skips_names_already_in_use(vfs):
    await _write(vfs, "/notes (2).txt", b"taken")
    node_id = await _trashed_then_retaken(vfs)

    result = await vfs.restore(node_id, on_conflict="keep_both")

    assert result["path"] == "/notes (3).txt"


async def test_keep_both_survives_a_name_with_no_extension(vfs):
    await _write(vfs, "/README", b"old")
    await vfs.remove("/README")
    await _write(vfs, "/README", b"new")
    [item] = await vfs.list_trash()

    result = await vfs.restore(item["node"]["id"], on_conflict="keep_both")

    assert result["path"] == "/README (2)"


# ------------------------------------------------------------- directories


async def test_trashing_a_directory_hides_everything_under_it(vfs):
    await vfs.makedir("/project")
    await _write(vfs, "/project/a.txt")
    await _write(vfs, "/project/b.txt")

    await vfs.trash("/project")

    assert _names(await vfs.list_dir("/")) == []
    assert await vfs.get_node("/project/a.txt") is None
    with pytest.raises(NotFound):
        await vfs.list_dir("/project")

    # One item in the trash, not three: the subtree went in with its parent.
    [item] = await vfs.list_trash()
    assert item["node"]["filename"] == "project"


async def test_restoring_a_directory_brings_the_subtree_back(vfs):
    await vfs.makedir("/project")
    await _write(vfs, "/project/a.txt", b"kept")
    await vfs.trash("/project")

    [item] = await vfs.list_trash()
    await vfs.restore(item["node"]["id"])

    assert _names(await vfs.list_dir("/project")) == ["a.txt"]


async def test_removedir_still_refuses_a_directory_with_things_in_it(vfs):
    await vfs.makedir("/project")
    await _write(vfs, "/project/a.txt")

    with pytest.raises(NotEmpty):
        await vfs.removedir("/project")


async def test_removedir_accepts_a_directory_emptied_into_the_trash(vfs):
    """`rm *` then `rmdir` is how SFTP clears a directory. It has to keep working."""
    await vfs.makedir("/project")
    await _write(vfs, "/project/a.txt")

    await vfs.remove("/project/a.txt")
    await vfs.removedir("/project")

    assert _names(await vfs.list_dir("/")) == []

    # One entry, not two. The file went in first, but the directory that
    # followed it is now above it, so the trash shows the directory and
    # restoring that is what brings the file back into view.
    [item] = await vfs.list_trash()
    assert item["path"] == "/project"


async def test_a_child_trashed_before_its_parent_is_not_listed_twice(vfs):
    await vfs.makedir("/project")
    await _write(vfs, "/project/a.txt")
    await vfs.remove("/project/a.txt")
    await vfs.trash("/project")

    # Only the directory: the child is underneath something already trashed.
    [item] = await vfs.list_trash()
    assert item["node"]["filename"] == "project"

    # Restoring the parent brings the child back into view as its own item,
    # so nothing can be deleted, invisible and unreachable at once.
    await vfs.restore(item["node"]["id"])
    [child] = await vfs.list_trash()
    assert child["path"] == "/project/a.txt"


async def test_restoring_into_a_trashed_parent_is_refused(vfs):
    await vfs.makedir("/project")
    await _write(vfs, "/project/a.txt")
    await vfs.remove("/project/a.txt")
    child_id = (await vfs.list_trash())[0]["node"]["id"]
    await vfs.trash("/project")

    with pytest.raises(Unsupported):
        await vfs.restore(child_id)


async def test_the_root_cannot_be_trashed(vfs):
    with pytest.raises(Unsupported):
        await vfs.trash("/")


# ------------------------------------------------------------------ purging


async def test_purge_destroys_the_subtree_and_its_attachments(vfs, fake_discord):
    await vfs.makedir("/project")
    await _write(vfs, "/project/big.bin", PAYLOAD)
    assert fake_discord.store != {}

    await vfs.trash("/project")
    [item] = await vfs.list_trash()
    result = await vfs.purge(item["node"]["id"])

    assert result["nodes"] == 2
    assert result["attachments"] >= 1
    assert fake_discord.store == {}
    assert await vfs.list_trash() == []
    assert _names(await vfs.list_dir("/")) == []


async def test_purge_refuses_a_live_node(vfs):
    await _write(vfs, "/notes.txt")
    node = await vfs.get_node("/notes.txt")

    with pytest.raises(Unsupported):
        await vfs.purge(node["id"])


async def test_the_directory_still_lists_after_a_purge(vfs):
    """The entry tag has to end up agreeing with what is actually left."""
    await _write(vfs, "/gone.txt")
    await _write(vfs, "/stays.txt")

    await vfs.remove("/gone.txt")
    [item] = await vfs.list_trash()
    await vfs.purge(item["node"]["id"])

    assert _names(await vfs.list_dir("/")) == ["stays.txt"]


async def test_purge_expired_only_takes_what_is_due(vfs, fake_db):
    await _write(vfs, "/old.txt")
    await _write(vfs, "/recent.txt")
    await vfs.remove("/old.txt")
    await vfs.remove("/recent.txt")

    # Backdate one of them past the retention window. The tag covers
    # `trashed_at`, so this goes through the VFS rather than the document.
    old = next(i for i in await vfs.list_trash()
               if i["node"]["filename"] == "old.txt")
    await vfs._set_trashed(old["node"], old["node"]["trashed_at"] - 10_000)

    result = await vfs.purge_expired(retention=5_000)

    assert result["purged"] == 1
    assert [i["node"]["filename"] for i in await vfs.list_trash()] \
        == ["recent.txt"]


async def test_purge_expired_stops_at_the_batch_limit(vfs):
    for name in ("a.txt", "b.txt", "c.txt"):
        await _write(vfs, f"/{name}")
        await vfs.remove(f"/{name}")

    for item in await vfs.list_trash():
        await vfs._set_trashed(item["node"], item["node"]["trashed_at"] - 10_000)

    result = await vfs.purge_expired(retention=5_000, limit=2)

    assert result["purged"] == 2
    assert result["remaining"] == 1
    assert len(await vfs.list_trash()) == 1


# ------------------------------------------------------------- the tampering
#
# The point of putting `trashed_at` inside the tags. Each of these would
# succeed silently if it were left outside.


async def test_hiding_a_live_file_by_marking_it_deleted_is_caught(vfs, fake_db):
    """The attack the whole design is aimed at: a silent delete.

    Setting this field takes the file out of every listing, and the node is
    unreachable by path afterwards, so nothing would ever fetch it again to
    check its tag. `entries_of` verifies the children it filters out for
    exactly this reason.
    """
    await _write(vfs, "/secret.txt", PAYLOAD)
    _doc(fake_db, filename="secret.txt")["trashed_at"] = 1

    with pytest.raises(IntegrityError):
        await vfs.list_dir("/")


async def test_hiding_a_whole_directory_is_caught(vfs, fake_db):
    """Worse than one file: one field takes a subtree out of view."""
    await vfs.makedir("/project")
    await _write(vfs, "/project/a.txt")

    _doc(fake_db, filename="project")["trashed_at"] = 1

    with pytest.raises(IntegrityError):
        await vfs.list_dir("/")


async def test_reviving_a_trashed_file_behind_the_servers_back_is_caught(
        vfs, fake_db):
    """The other direction: putting something back without asking."""
    await _write(vfs, "/notes.txt")
    await vfs.remove("/notes.txt")

    _doc(fake_db, filename="notes.txt")["trashed_at"] = None

    with pytest.raises(IntegrityError):
        await vfs.get_node("/notes.txt")


async def test_backdating_a_deletion_to_force_an_early_purge_is_caught(
        vfs, fake_db):
    """Moving `trashed_at` earlier is how you make the sweeper destroy it now."""
    await _write(vfs, "/notes.txt")
    await vfs.remove("/notes.txt")

    _doc(fake_db, filename="notes.txt")["trashed_at"] = 1

    with pytest.raises(IntegrityError):
        await vfs.list_trash()


async def test_a_trashed_node_still_counts_as_a_member_of_its_directory(
        vfs, fake_db):
    """Trashing must not disturb the entry tag, or every listing would break.

    Membership is what that tag covers, and a trashed child is still a member.
    Removing its document is still a deletion and still caught -- which is the
    property this asserts has survived the change.
    """
    await _write(vfs, "/notes.txt")
    await vfs.remove("/notes.txt")

    assert _names(await vfs.list_dir("/")) == []

    fake_db.nodes.docs.remove(_doc(fake_db, filename="notes.txt"))

    with pytest.raises(IntegrityError):
        await vfs.list_dir("/")
