"""Overwriting a file, which is no longer a truncate.

`open(..., truncate=True)` used to empty the node in place: chunks dropped,
attachments deleted, committed, all before the first new byte arrived. Whatever
happened next, the old contents were gone. That made an interrupted overwrite
-- a closed tab, a dropped connection, a Discord outage -- a way to destroy a
file you were only trying to replace, with nothing in the trash to go back to.

The new bytes now go into a *detached* node: one with no `parent_id` and no
`filename`, so it sits in no directory and appears in no listing, and the file
being replaced is untouched until there is a whole new one to swap in. At
`close` the occupant of the name goes to the trash and the incoming node takes
its place.

Two consequences are worth stating rather than discovering: both copies are on
Discord until the trash is swept, and a process killed mid-upload leaves a
detached node that only `sweep_incoming` will ever collect.
"""

import pytest

from src import vfs as vfs_mod
from src.vfs import IsADirectory, ROOT_ID, DiscordVFS
from tests.conftest import TEST_CHUNK_SIZE
from tests.fakes import DiscordFailure

OLD = b"o" * (TEST_CHUNK_SIZE + 11)
NEW = b"n" * (TEST_CHUNK_SIZE + 7)


async def _write(vfs, path, data, **kwargs):
    handle = await vfs.open(path, read=False, write=True, **kwargs)
    await handle.write_at(0, data)
    await handle.close()
    return handle


async def _read(vfs, path):
    handle = await vfs.open(path, read=True, write=False)
    try:
        return await handle.read_at(0, handle.size)
    finally:
        await handle.close()


# --------------------------------------------------------- the ordinary path


async def test_an_overwrite_replaces_the_contents(vfs):
    await _write(vfs, "/f.bin", OLD, create=True)
    await _write(vfs, "/f.bin", NEW, truncate=True)

    assert await _read(vfs, "/f.bin") == NEW


async def test_the_replaced_copy_goes_to_the_trash_and_can_be_restored(vfs):
    await _write(vfs, "/f.bin", OLD, create=True)
    original = (await vfs.get_node("/f.bin"))["id"]

    await _write(vfs, "/f.bin", NEW, truncate=True)

    [item] = await vfs.list_trash()
    assert item["node"]["id"] == original
    assert (await vfs.get_node("/f.bin"))["id"] != original

    # `replace` rather than the default, because the name is taken -- by the
    # very file that replaced it.
    await vfs.restore(original, on_conflict="replace")
    assert await _read(vfs, "/f.bin") == OLD


async def test_an_overwrite_keeps_the_mode_it_found(vfs):
    """O_TRUNC does not reset a file's mode, and the node is new underneath."""
    await _write(vfs, "/f.bin", OLD, create=True)
    await vfs.set_metadata("/f.bin", permissions=0o640)

    await _write(vfs, "/f.bin", NEW, truncate=True)

    assert (await vfs.get_node("/f.bin"))["permissions"] & 0o777 == 0o640


async def test_a_reader_sees_the_old_bytes_until_the_overwrite_commits(vfs):
    """Not merely "the old file survives a failure" -- it stays *readable*.

    A truncate made the file zero-length for everyone the moment it was
    opened. Anything reading the path while an upload was running got an empty
    file and no indication that it was watching a replacement in progress.
    """
    await _write(vfs, "/f.bin", OLD, create=True)

    writer = await vfs.open("/f.bin", read=False, write=True, truncate=True)
    await writer.write_at(0, NEW)

    assert await _read(vfs, "/f.bin") == OLD
    assert [e["filename"] for e in await vfs.list_dir("/")] == ["f.bin"]

    await writer.close()
    assert await _read(vfs, "/f.bin") == NEW


async def test_the_incoming_node_is_in_no_listing_while_it_is_in_flight(vfs):
    """Detached, not hidden.

    A reserved filename filtered out on the way to the client would be the
    "visible to one path, missing from another" side channel that `scandir`
    bypassing `list_dir` and `search` bypassing `entries_of` both were. This
    node is in no directory at all, so nothing has to remember to hide it.
    """
    await _write(vfs, "/f.bin", OLD, create=True)

    writer = await vfs.open("/f.bin", read=False, write=True, truncate=True)
    await writer.write_at(0, NEW)
    try:
        assert [e["filename"] for e in await vfs.list_dir("/")] == ["f.bin"]
        assert await vfs.list_trash() == []
        assert (await vfs.search("f"))["results"][0]["path"] == "/f.bin"
        assert len((await vfs.search("f"))["results"]) == 1
    finally:
        await writer.close()


async def test_an_overwrite_of_an_empty_file_still_works(vfs):
    handle = await vfs.open("/empty.bin", read=False, write=True, create=True)
    await handle.close()

    await _write(vfs, "/empty.bin", NEW, truncate=True)
    assert await _read(vfs, "/empty.bin") == NEW


async def test_overwriting_a_file_that_does_not_exist_creates_it(vfs):
    """`truncate=True` with `create=True` on a free name is not an overwrite.

    Every HTTP upload passes both, so this is the common case, and it must not
    go anywhere near the detached-node path.
    """
    await _write(vfs, "/fresh.bin", NEW, create=True, truncate=True)

    assert await _read(vfs, "/fresh.bin") == NEW
    assert await vfs.list_trash() == []


# ------------------------------------------------------------ the swap itself


async def test_two_overwrites_of_one_path_do_not_corrupt_the_directory(vfs):
    """Last writer wins, which is why the occupant is resolved at close time.

    Each handle remembers where its file has to go, not which node it is
    replacing -- by the time the second one closes, the node it opened over is
    already in the trash and the live occupant is the first one's work.
    """
    await _write(vfs, "/f.bin", OLD, create=True)

    first = await vfs.open("/f.bin", read=False, write=True, truncate=True)
    second = await vfs.open("/f.bin", read=False, write=True, truncate=True)
    await first.write_at(0, b"first")
    await second.write_at(0, b"second")
    await first.close()
    await second.close()

    assert await _read(vfs, "/f.bin") == b"second"
    assert [e["filename"] for e in await vfs.list_dir("/")] == ["f.bin"]
    assert len(await vfs.list_trash()) == 2


async def test_an_overwrite_refuses_to_trash_a_directory_that_took_the_name(vfs):
    """Landing the file would take a whole subtree out of view to do it."""
    await _write(vfs, "/f.bin", OLD, create=True)

    writer = await vfs.open("/f.bin", read=False, write=True, truncate=True)
    await writer.write_at(0, NEW)

    await vfs.remove("/f.bin")
    await vfs.makedir("/f.bin")

    with pytest.raises(IsADirectory):
        await writer.close()

    assert (await vfs.get_node("/f.bin"))["is_dir"]
    assert await vfs.get_node_by_id(writer.node["id"]) is None, (
        "a swap that could not happen must still unwind its own node")


async def test_a_failed_swap_releases_what_it_uploaded(vfs, fake_discord):
    await _write(vfs, "/f.bin", OLD, create=True)
    kept = set(fake_discord.store)

    writer = await vfs.open("/f.bin", read=False, write=True, truncate=True)
    await writer.write_at(0, NEW)
    await vfs.remove("/f.bin")
    await vfs.makedir("/f.bin")

    with pytest.raises(IsADirectory):
        await writer.close()

    assert set(fake_discord.store) == kept


# ------------------------------------------------------------ sweep_incoming


async def _abandon(vfs, path, *, age):
    """Leave a detached node behind the way a killed process would.

    The handle is never closed and never aborted -- which is precisely what
    `close()`/`abort()` cannot be relied on for, since the case this covers is
    the one where neither gets to run.
    """
    writer = await vfs.open(path, read=False, write=True, truncate=True)
    await writer.write_at(0, NEW)

    node_id = writer.node["id"]
    # Backdating is safe: `modified_at` is deliberately outside the tag, so
    # this does not invalidate the node the way editing its size would.
    await vfs_mod.db.get_db().nodes.update_one(
        {"id": node_id}, {"$set": {"modified_at": vfs_mod._now() - age}})
    return node_id


async def test_the_sweep_collects_an_abandoned_overwrite(vfs, fake_discord):
    await _write(vfs, "/f.bin", OLD, create=True)
    kept = set(fake_discord.store)
    abandoned = await _abandon(vfs, "/f.bin", age=3600 * 48)

    result = await vfs.sweep_incoming(max_age=3600 * 24)

    assert result["swept"] == 1
    assert result["attachments"] > 0
    assert await vfs.get_node_by_id(abandoned) is None
    assert set(fake_discord.store) == kept
    assert await _read(vfs, "/f.bin") == OLD, "the live file is not its business"


async def test_the_sweep_leaves_an_upload_that_is_still_moving(vfs):
    """`modified_at` moves with every committed chunk, so a slow transfer of
    something enormous is never old while it is making progress."""
    await _write(vfs, "/f.bin", OLD, create=True)
    fresh = await _abandon(vfs, "/f.bin", age=60)

    assert (await vfs.sweep_incoming(max_age=3600 * 24))["swept"] == 0
    assert await vfs.get_node_by_id(fresh) is not None


async def test_the_sweep_refuses_a_node_whose_tag_does_not_verify(
        vfs, fake_db, fake_discord, caplog):
    """It deletes by the node's own `chunks`, so an unverified one is a way to
    nominate somebody else's attachments for destruction.

    Checked against the raw documents rather than through `get_node_by_id`,
    which verifies tags and so cannot tell "left alone" from "deleted".
    """
    await _write(vfs, "/f.bin", OLD, create=True)
    survivors = set(fake_discord.store)
    tampered = await _abandon(vfs, "/f.bin", age=3600 * 48)

    await vfs_mod.db.get_db().nodes.update_one(
        {"id": tampered}, {"$set": {"mac": "00" * 32}})

    assert (await vfs.sweep_incoming(max_age=3600 * 24))["swept"] == 0
    assert any(doc["id"] == tampered for doc in fake_db.nodes.docs)
    assert survivors <= set(fake_discord.store)
    assert any("failed verification" in record.message
               for record in caplog.records)


async def test_the_sweep_honours_its_batch_limit(vfs):
    await _write(vfs, "/f.bin", OLD, create=True)
    for _ in range(3):
        await _abandon(vfs, "/f.bin", age=3600 * 48)

    assert (await vfs.sweep_incoming(max_age=3600 * 24, limit=2))["swept"] == 2
    assert (await vfs.sweep_incoming(max_age=3600 * 24, limit=2))["swept"] == 1


async def test_the_sweep_never_touches_a_live_file(vfs):
    """Every live node has a parent, so none of them can match the query. Worth
    an assertion of its own: this sweep deletes without asking anybody."""
    await _write(vfs, "/f.bin", OLD, create=True)
    await vfs.makedir("/d")

    assert (await vfs.sweep_incoming(max_age=0))["swept"] == 0
    assert await _read(vfs, "/f.bin") == OLD
    assert sorted(e["filename"] for e in await vfs.list_dir("/")) == \
        ["d", "f.bin"]


# --------------------------------------------------------------- integration


async def test_a_discord_outage_mid_overwrite_leaves_the_file_readable(
        vfs, fake_discord, master_key):
    """The end-to-end claim, read back through a VFS that shares no state."""
    await _write(vfs, "/f.bin", OLD, create=True)
    fake_discord.fail_uploads_from = fake_discord.uploads + 1

    writer = await vfs.open("/f.bin", read=False, write=True, truncate=True)
    with pytest.raises(DiscordFailure):
        await writer.write_at(0, NEW)

    assert await _read(DiscordVFS(master_key, ROOT_ID), "/f.bin") == OLD
