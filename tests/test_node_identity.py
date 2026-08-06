"""Integrity of a node's *identity*: its name, its directory, its siblings.

Until now the tags covered what a file contained and nothing about which file
it was. Whoever could write to MongoDB could rename it, move it, or delete it
outright, and every check still passed. The dangerous one was never the
obvious rename -- it was the swap: give `report-2024.pdf` the name
`report-2026.pdf` and the bytes served under that name are authentic, tagged,
verified, and wrong. A guarantee that covers content but not identity is a
guarantee that misleads.

What is covered now:

* a file's tag binds its `parent_id` and `filename`;
* a directory has an identity tag of its own, so renaming `/private` to
  `/public` no longer leaves every tag underneath it valid;
* a directory has a second tag over the set of names it holds, checked when
  listing it, which is what makes a deletion detectable.

What is still not covered, deliberately: permissions and timestamps (not
content -- see ROADMAP.md), and restoring an older copy of both a child and
its parent, which is whole-file rollback and an accepted residual risk.

The tampering below writes to `fake_db.nodes.docs` directly. That is the
threat model: an attacker with database access and no key.
"""

import os

import asyncssh
import pytest

from src.crypto import IntegrityError
from src.vfs import ROOT_ID, DiscordVFS, TAG_VERSION
from tests.conftest import TEST_CHUNK_SIZE, connect

PAYLOAD = os.urandom(TEST_CHUNK_SIZE + 500)
OTHER = os.urandom(TEST_CHUNK_SIZE + 500)


async def _write(vfs, path, data):
    handle = await vfs.open(path, read=False, write=True, create=True)
    await handle.write_at(0, data)
    await handle.close()


def _doc(fake_db, **match):
    for d in fake_db.nodes.docs:
        if all(d.get(k) == v for k, v in match.items()):
            return d
    raise AssertionError(f"no node matching {match}")


# ------------------------------------------------------------ file identity


async def test_renaming_a_file_behind_the_servers_back_is_caught(vfs, fake_db):
    await _write(vfs, "/secret.txt", PAYLOAD)

    _doc(fake_db, filename="secret.txt")["filename"] = "boring.txt"

    with pytest.raises(IntegrityError):
        await vfs.get_node("/boring.txt")


async def test_moving_a_file_to_another_directory_is_caught(vfs, fake_db):
    await vfs.makedir("/elsewhere")
    await _write(vfs, "/secret.txt", PAYLOAD)

    target = _doc(fake_db, filename="elsewhere")
    _doc(fake_db, filename="secret.txt")["parent_id"] = target["id"]

    with pytest.raises(IntegrityError):
        await vfs.get_node("/elsewhere/secret.txt")


async def test_swapping_two_filenames_is_caught_on_both(vfs, fake_db):
    """The one that matters: content substitution with authentic bytes.

    Both files' contents remain exactly what they always were, and every
    chunk tag still verifies. Only the names moved.
    """
    await _write(vfs, "/report-2024.pdf", PAYLOAD)
    await _write(vfs, "/report-2026.pdf", OTHER)

    a = _doc(fake_db, filename="report-2024.pdf")
    b = _doc(fake_db, filename="report-2026.pdf")

    # Through a spare name, rather than the direct swap this used to do.
    #
    # A direct swap writes one of them onto the other's live name before the
    # other has let it go, and the unique index over live nodes refuses that
    # -- on MongoDB and on SQLite alike. So the original two lines described
    # an attack that could not be carried out against either real database,
    # and passed only because `FakeDB` does not enforce uniqueness. Running
    # this suite under `--db=sqlite`, where the index is real, is what
    # surfaced it.
    #
    # The end state and the assertions below are exactly what they were. Only
    # the route is one an attacker could actually take.
    a["filename"] = "report-swap.tmp"
    b["filename"] = "report-2024.pdf"
    a["filename"] = "report-2026.pdf"

    with pytest.raises(IntegrityError):
        await vfs.get_node("/report-2026.pdf")
    with pytest.raises(IntegrityError):
        await vfs.get_node("/report-2024.pdf")


async def test_a_renamed_file_still_reads_back(vfs, master_key):
    """The other half: the check must not have cost us the feature."""
    await _write(vfs, "/before.txt", PAYLOAD)
    await vfs.rename("/before.txt", "/after.txt")

    reader = await DiscordVFS(master_key, ROOT_ID).open("/after.txt", read=True, write=False)
    assert await reader.read_at(0, len(PAYLOAD)) == PAYLOAD
    assert await vfs.get_node("/before.txt") is None


async def test_a_file_moved_between_directories_still_reads_back(vfs, master_key):
    await vfs.makedir("/archive")
    await _write(vfs, "/note.txt", PAYLOAD)
    await vfs.rename("/note.txt", "/archive/note.txt")

    reader = await DiscordVFS(master_key, ROOT_ID).open("/archive/note.txt", read=True,
                                               write=False)
    assert await reader.read_at(0, len(PAYLOAD)) == PAYLOAD


async def test_a_rename_does_not_restamp_the_files_own_mtime(vfs, fake_db):
    """Moving a file is not modifying it -- pinned so the retag cannot undo it."""
    await _write(vfs, "/note.txt", PAYLOAD)
    before = _doc(fake_db, filename="note.txt")["modified_at"]

    await vfs.rename("/note.txt", "/moved.txt")

    assert _doc(fake_db, filename="moved.txt")["modified_at"] == before


# ------------------------------------------------------- directory identity


async def test_renaming_a_directory_is_caught(vfs, fake_db):
    """Children record their parent's id, not its name, so nothing below
    this directory would have noticed on its own."""
    await vfs.makedir("/private")
    await _write(vfs, "/private/keys.txt", PAYLOAD)

    _doc(fake_db, filename="private")["filename"] = "public"

    with pytest.raises(IntegrityError):
        await vfs.get_node("/public")
    with pytest.raises(IntegrityError):
        await vfs.get_node("/public/keys.txt")


async def test_moving_a_directory_is_caught(vfs, fake_db):
    await vfs.makedir("/a")
    await vfs.makedir("/b")
    await vfs.makedir("/a/inner")

    b = _doc(fake_db, filename="b")
    _doc(fake_db, filename="inner")["parent_id"] = b["id"]

    with pytest.raises(IntegrityError):
        await vfs.get_node("/b/inner")


async def test_a_renamed_directory_still_works(vfs, master_key):
    await vfs.makedir("/private")
    await _write(vfs, "/private/keys.txt", PAYLOAD)
    await vfs.rename("/private", "/public")

    other = DiscordVFS(master_key, ROOT_ID)
    reader = await other.open("/public/keys.txt", read=True, write=False)
    assert await reader.read_at(0, len(PAYLOAD)) == PAYLOAD
    assert len(await other.list_dir("/public")) == 1


# ---------------------------------------------------------- directory entries


async def test_deleting_a_node_behind_the_servers_back_is_caught_on_listing(
    vfs, fake_db
):
    """The whole point of tagging the entry set."""
    await _write(vfs, "/keep.txt", PAYLOAD)
    await _write(vfs, "/vanish.txt", OTHER)

    fake_db.nodes.docs.remove(_doc(fake_db, filename="vanish.txt"))

    with pytest.raises(IntegrityError):
        await vfs.list_dir("/")


async def test_a_tampered_file_does_not_break_listing_its_directory(vfs, fake_db):
    """The reason listing was left unverified before, preserved.

    Entry membership is checked; each child's own tag is not. So one corrupt
    file still fails to open while `ls` keeps working -- which is what lets
    you see what is there and delete it.
    """
    await _write(vfs, "/fine.txt", PAYLOAD)
    await _write(vfs, "/broken.txt", OTHER)

    _doc(fake_db, filename="broken.txt")["mac"] = "00" * 32

    assert len(await vfs.list_dir("/")) == 2
    with pytest.raises(IntegrityError):
        await vfs.get_node("/broken.txt")


async def test_transplanting_a_directorys_entry_tag_is_caught(vfs, fake_db):
    """Entry tags are bound to their directory, so they cannot be swapped."""
    await vfs.makedir("/one")
    await vfs.makedir("/two")
    await _write(vfs, "/one/a.txt", PAYLOAD)

    one = _doc(fake_db, filename="one")
    two = _doc(fake_db, filename="two")
    two["entries_mac"] = one["entries_mac"]
    two.pop("entries_mac_pending", None)

    with pytest.raises(IntegrityError):
        await vfs.list_dir("/two")


async def test_every_structural_change_keeps_the_entry_tag_valid(vfs):
    """mkdir / create / remove / rmdir / rename / overwriting rename."""
    await vfs.makedir("/d")
    await _write(vfs, "/d/a.txt", PAYLOAD)
    await _write(vfs, "/d/b.txt", OTHER)
    assert len(await vfs.list_dir("/d")) == 2

    await vfs.rename("/d/a.txt", "/d/renamed.txt")          # same directory
    assert len(await vfs.list_dir("/d")) == 2

    await vfs.makedir("/e")
    await vfs.rename("/d/renamed.txt", "/e/moved.txt")      # across directories
    assert len(await vfs.list_dir("/d")) == 1
    assert len(await vfs.list_dir("/e")) == 1

    await _write(vfs, "/e/target.txt", PAYLOAD)
    await vfs.rename("/e/moved.txt", "/e/target.txt", overwrite=True)
    assert len(await vfs.list_dir("/e")) == 1

    await vfs.remove("/d/b.txt")
    assert await vfs.list_dir("/d") == []

    await vfs.removedir("/d")
    assert len(await vfs.list_dir("/")) == 1


async def test_a_staged_entry_tag_is_dropped_once_promoted(vfs, fake_db):
    """The two-phase field must not linger.

    Verification accepts the staged value as well as the current one, which is
    what makes a crash between the two writes survivable. A stale one left
    behind would keep an older entry set acceptable indefinitely.
    """
    await vfs.makedir("/d")
    await _write(vfs, "/d/a.txt", PAYLOAD)
    assert "entries_mac_pending" not in _doc(fake_db, filename="d")


async def test_a_crash_between_the_two_writes_still_lists(vfs, fake_db, master_key):
    """What the staged tag exists for.

    Simulated by rolling the directory back to the state it is in between
    staging and promotion: the child is there, but only the staged tag covers
    it. Both sides of that window have to read as intact.
    """
    await vfs.makedir("/d")
    directory = _doc(fake_db, filename="d")
    before = directory["entries_mac"]

    await _write(vfs, "/d/a.txt", PAYLOAD)
    directory = _doc(fake_db, filename="d")

    directory["entries_mac_pending"] = directory["entries_mac"]
    directory["entries_mac"] = before

    assert len(await DiscordVFS(master_key, ROOT_ID).list_dir("/d")) == 1


async def test_a_deletion_cannot_be_laundered_by_a_later_mkdir(vfs, fake_db):
    """A structural change re-signs the entry set, so it verifies first.

    Without that check, deleting a file and waiting for the next ordinary
    `mkdir` would produce a freshly signed tag covering the attacker's set.
    """
    await _write(vfs, "/keep.txt", PAYLOAD)
    await _write(vfs, "/vanish.txt", OTHER)

    fake_db.nodes.docs.remove(_doc(fake_db, filename="vanish.txt"))

    with pytest.raises(IntegrityError):
        await vfs.makedir("/newdir")

    assert not any(d.get("filename") == "newdir" for d in fake_db.nodes.docs)


# ------------------------------------------------------------- tag versions


async def test_a_node_from_an_older_tag_version_is_refused(vfs, fake_db):
    await _write(vfs, "/note.txt", PAYLOAD)
    _doc(fake_db, filename="note.txt")["tag_version"] = 1

    with pytest.raises(IntegrityError, match="tag version"):
        await vfs.get_node("/note.txt")


async def test_a_node_with_no_tag_version_is_refused(vfs, fake_db):
    """Fail closed: stripping the field must not switch the check off."""
    await _write(vfs, "/note.txt", PAYLOAD)
    del _doc(fake_db, filename="note.txt")["tag_version"]

    with pytest.raises(IntegrityError, match="tag version"):
        await vfs.get_node("/note.txt")


async def test_the_root_is_tagged_like_any_other_directory(vfs, fake_db):
    root = _doc(fake_db, id=ROOT_ID)
    assert root["tag_version"] == TAG_VERSION
    assert root["mac"] and root["entries_mac"]

    root["mac"] = "00" * 32
    with pytest.raises(IntegrityError):
        await vfs.get_node("/")


async def test_a_root_predating_tags_is_refused_while_it_holds_data(
    fake_db, master_key
):
    """The one upgrade path there is, and it declines to guess.

    Re-tagging a populated root would certify whatever is in it as authentic
    with no way to know that it is -- exactly the laundering a backfill must
    never do.
    """
    fake_db.nodes.docs.clear()
    fake_db.nodes.docs.append({
        "id": ROOT_ID, "parent_id": None, "filename": "", "is_dir": True,
        "size": 0, "created_at": 1, "modified_at": 1,
    })
    fake_db.nodes.docs.append({
        "id": "orphan", "parent_id": ROOT_ID, "filename": "leftover.txt",
        "is_dir": False, "size": 0, "chunks": [], "created_at": 1,
        "modified_at": 1,
    })

    with pytest.raises(Exception, match="predates node tag version"):
        await DiscordVFS(master_key, ROOT_ID).ensure_root()


async def test_an_empty_root_predating_tags_is_upgraded(fake_db, master_key):
    fake_db.nodes.docs.clear()
    fake_db.nodes.docs.append({
        "id": ROOT_ID, "parent_id": None, "filename": "", "is_dir": True,
        "size": 0, "created_at": 1, "modified_at": 1,
    })

    vfs = DiscordVFS(master_key, ROOT_ID)
    await vfs.ensure_root()

    assert (await vfs.get_node("/"))["tag_version"] == TAG_VERSION
    assert await vfs.list_dir("/") == []


# ------------------------------------------------------------- normalisation


async def test_a_filename_tags_the_same_under_either_unicode_form(vfs, fake_db):
    """NFC and NFD of the same name must not produce different tags.

    macOS clients send NFD and Linux clients send NFC. Without normalising,
    the same file would verify on one and read as corrupt on the other --
    a platform-specific integrity failure, which is the worst kind to debug.
    """
    composed = "café.txt"          # é as one code point
    decomposed = "café.txt"       # e + combining acute

    await _write(vfs, "/" + composed, PAYLOAD)
    _doc(fake_db, filename=composed)["filename"] = decomposed

    node = await vfs.get_node("/" + decomposed)
    assert node is not None


# --------------------------------------------------------------- SFTP-level


async def test_a_deletion_is_caught_over_the_real_protocol(sftp_port, fake_db):
    """The VFS-level version of this passed while the protocol was unprotected.

    `scandir` was calling `children()` directly rather than the verified
    listing, so the entry tag was checked for anyone driving the VFS in
    process and skipped for every actual SFTP client. Live acceptance caught
    it; this pins it.
    """
    async with connect(sftp_port) as conn:
        async with conn.start_sftp_client() as client:
            async with client.open("/keep.txt", "wb") as f:
                await f.write(PAYLOAD)
            async with client.open("/vanish.txt", "wb") as f:
                await f.write(OTHER)

            fake_db.nodes.docs.remove(_doc(fake_db, filename="vanish.txt"))

            with pytest.raises(asyncssh.SFTPError):
                await client.listdir("/")


async def test_listing_and_renaming_work_over_the_real_protocol(sftp_port):
    async with connect(sftp_port) as conn:
        async with conn.start_sftp_client() as client:
            await client.mkdir("/docs")
            async with client.open("/docs/a.txt", "wb") as f:
                await f.write(PAYLOAD)

            assert sorted(await client.listdir("/docs")) == [".", "..", "a.txt"]

            await client.rename("/docs/a.txt", "/docs/b.txt")
            assert sorted(await client.listdir("/docs")) == [".", "..", "b.txt"]

            async with client.open("/docs/b.txt", "rb") as f:
                assert await f.read() == PAYLOAD

            await client.remove("/docs/b.txt")
            await client.rmdir("/docs")
            assert sorted(await client.listdir("/")) == [".", ".."]
