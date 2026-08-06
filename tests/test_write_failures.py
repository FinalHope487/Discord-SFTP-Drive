"""What a Discord outage part way through a write is allowed to destroy.

Every failure path in `DiscordFile` was untested until now -- `_rollback()`
and all three `_failed` guards -- and the reason was a missing tool rather
than a missing intention: `FakeDiscord` had no way to fail, so writing any of
these tests meant extending the fake first. It can fail now
(`fail_uploads_from`), and the first thing that bought was the bug the second
test below pins down.

That bug: `_rollback()` walked *every* chunk of the node and deleted it from
Discord, on the assumption that a handle only ever holds a file it created or
one it truncated. But `DiscordVFS.open()` also hands out handles on existing
files opened without `O_TRUNC` -- `put -a`, appending to a log, resuming an
interrupted upload -- and for those, `chunks` is the file's own pre-existing
contents. A Discord outage during such a write deleted all of it, from
Discord, unrecoverably. Reproduced before the fix on a 128 KiB two-chunk
file: `size=0`, `chunks=0`, both attachments gone.

The irony was the point: that path is easiest to reach when Discord has been
failing long enough to exhaust the retry budget, which is exactly the moment
nothing more should be lost.
"""

import os

import asyncssh
import pytest

from src.vfs import ROOT_ID, DiscordVFS, VFSError
from tests.conftest import TEST_CHUNK_SIZE, connect
from tests.fakes import DiscordFailure

# Two full chunks, so "the file's existing contents" is more than one
# attachment and a partial deletion would still show up.
PAYLOAD = os.urandom(2 * TEST_CHUNK_SIZE)

# Enough to force an upload on its own rather than sitting in the buffer,
# which is what makes the write fail synchronously.
EXTRA = os.urandom(TEST_CHUNK_SIZE)


async def _seed(vfs, path, data):
    handle = await vfs.open(path, read=False, write=True, create=True)
    await handle.write_at(0, data)
    await handle.close()


async def _read_back(master_key, path):
    """Read `path` through a handle that shares nothing with the failed one.

    Going through `open` re-fetches the node from the database and re-verifies
    its tag, so a file that survived in name only -- dangling chunk
    references, a stale mac -- fails here rather than passing on a cached
    in-memory copy.
    """
    handle = await DiscordVFS(master_key, ROOT_ID).open(path, read=True, write=False)
    return handle.size, await handle.read_at(0, handle.size)


# ------------------------------------------------- a file this handle created


async def test_a_new_file_that_fails_mid_upload_disappears_entirely(
    vfs, fake_discord
):
    # Chunk one lands and commits; chunk two never does.
    fake_discord.fail_uploads_from = 2

    handle = await vfs.open("/new.bin", read=False, write=True, create=True)
    with pytest.raises(DiscordFailure):
        await handle.write_at(0, PAYLOAD)

    assert await vfs.get_node("/new.bin") is None, (
        "a new file that never finished uploading was left behind"
    )
    assert fake_discord.store == {}, (
        "the chunk that did land became an orphan attachment"
    )


# --------------------------------------------- a file that already existed
#
# The regression tests for the bug in this module's docstring.


async def test_appending_to_an_existing_file_destroys_nothing_when_discord_fails(
    vfs, fake_discord, master_key
):
    await _seed(vfs, "/blob.bin", PAYLOAD)
    surviving = dict(fake_discord.store)
    assert len(surviving) == 2

    fake_discord.fail_uploads_from = fake_discord.uploads + 1

    # O_APPEND, no O_TRUNC: `chunks` holds the file's own existing contents.
    handle = await vfs.open("/blob.bin", read=False, write=True, append=True)
    with pytest.raises(DiscordFailure):
        await handle.write_at(0, EXTRA)

    assert fake_discord.store == surviving, (
        "the file's pre-existing attachments were deleted from Discord"
    )
    assert fake_discord.deleted == [], (
        "nothing this handle did not upload should have been deleted"
    )
    assert await _read_back(master_key, "/blob.bin") == (len(PAYLOAD), PAYLOAD)


async def test_a_random_write_that_fails_leaves_the_old_bytes_in_place(
    vfs, fake_discord, master_key
):
    await _seed(vfs, "/blob.bin", PAYLOAD)
    surviving = dict(fake_discord.store)

    fake_discord.fail_uploads_from = fake_discord.uploads + 1

    # Patching a chunk means uploading a replacement; the swap of message id,
    # nonce and tag must not be half-applied when that upload never returns.
    handle = await vfs.open("/blob.bin", read=False, write=True)
    with pytest.raises(DiscordFailure):
        await handle.write_at(100, b"x" * 64)

    assert fake_discord.store == surviving
    assert fake_discord.deleted == []
    assert await _read_back(master_key, "/blob.bin") == (len(PAYLOAD), PAYLOAD)


async def test_a_metadata_failure_releases_only_the_attachment_it_just_made(
    vfs, fake_discord, fake_db, master_key, monkeypatch
):
    """The other way into `_rollback`: the upload works, the commit does not.

    This is the path that has to release its own attachment, since nothing
    references it -- and, now, the only place a delete may happen at all when
    the file already existed.
    """
    await _seed(vfs, "/blob.bin", PAYLOAD)
    surviving = dict(fake_discord.store)

    async def refuse(*args, **kwargs):
        raise RuntimeError("injected metadata write failure")

    # Left in place for the rest of the test: reads only need `find_one`, and
    # undoing it here would also undo the fixtures' own patching.
    monkeypatch.setattr(fake_db.nodes, "update_one", refuse)

    handle = await vfs.open("/blob.bin", read=False, write=True, append=True)
    with pytest.raises(RuntimeError):
        await handle.write_at(0, EXTRA)

    assert fake_discord.store == surviving, (
        "the uploaded-but-unreferenced chunk was left on Discord as an orphan"
    )
    assert len(fake_discord.deleted) == 1, (
        "exactly the attachment nothing referenced should have been deleted"
    )
    assert await _read_back(master_key, "/blob.bin") == (len(PAYLOAD), PAYLOAD)


async def test_an_overwrite_that_fails_leaves_the_old_contents_alone(
    vfs, fake_discord, master_key
):
    """The case `_rollback`'s docstring used to claim it covered.

    It did not cover it; it described it. `O_TRUNC` committed the file empty at
    open time, so by the time a write failed the old contents were already gone
    -- and the docstring's "there is nothing to restore" was true precisely
    because the damage had already been done, unrecoverably, before the upload
    that failed was even attempted. Closing the browser tab half way through
    replacing a file destroyed the file.

    Overwrites now go into a detached node and only take the name at `close`,
    so a failure anywhere before that leaves the original untouched. This
    asserts the whole of it: the bytes still read back, and the file is still
    the same file rather than a restored copy.
    """
    await _seed(vfs, "/blob.bin", PAYLOAD)
    original = (await vfs.get_node("/blob.bin"))["id"]
    fake_discord.fail_uploads_from = fake_discord.uploads + 1

    handle = await vfs.open("/blob.bin", read=False, write=True, truncate=True)
    with pytest.raises(DiscordFailure):
        await handle.write_at(0, EXTRA)

    assert await _read_back(master_key, "/blob.bin") == (len(PAYLOAD), PAYLOAD)
    assert (await vfs.get_node("/blob.bin"))["id"] == original


async def test_a_failed_overwrite_leaves_nothing_behind_on_discord(
    vfs, fake_discord, master_key
):
    """The other half: the abandoned attempt does not leak.

    The failing write is two chunks, the first of which lands. Without an
    unwind that chunk would sit on Discord for ever, referenced by a detached
    node no directory can reach -- invisible to `list_trash`, and invisible to
    `find_orphans` too, since from Discord's side something *does* still point
    at it.
    """
    await _seed(vfs, "/blob.bin", PAYLOAD)
    kept = set(fake_discord.store)
    fake_discord.fail_uploads_from = fake_discord.uploads + 2

    handle = await vfs.open("/blob.bin", read=False, write=True, truncate=True)
    with pytest.raises(DiscordFailure):
        await handle.write_at(0, EXTRA + EXTRA)

    assert set(fake_discord.store) == kept, (
        "the abandoned overwrite left an attachment behind"
    )
    assert await vfs.get_node_by_id(handle.node["id"]) is None, (
        "the detached node outlived the write it was staging"
    )


# ------------------------------------------------------- the failed handle


async def test_a_failed_handle_refuses_everything_that_would_write(
    vfs, fake_discord
):
    await _seed(vfs, "/blob.bin", PAYLOAD)
    fake_discord.fail_uploads_from = fake_discord.uploads + 1

    handle = await vfs.open("/blob.bin", read=False, write=True, append=True)
    with pytest.raises(DiscordFailure):
        await handle.write_at(0, EXTRA)

    # Discord is healthy again; the handle is still not. Its buffer no longer
    # lines up with what actually landed, so resuming on it would place bytes
    # at the wrong offset rather than report the failure the client already
    # saw.
    fake_discord.fail_uploads_from = None

    with pytest.raises(VFSError):
        await handle.write_at(0, b"more")
    with pytest.raises(VFSError):
        await handle.truncate_to(0)

    uploads_before = fake_discord.uploads
    await handle.close()
    assert fake_discord.uploads == uploads_before, (
        "closing a failed handle flushed its buffer after all"
    )


async def test_a_failed_handle_stops_counting_its_stranded_buffer(
    vfs, fake_discord
):
    """Its length is what committed, not what it was holding.

    The buffered bytes are never going to land -- every write path refuses
    now -- so counting them reports a size made partly of bytes that exist
    nowhere. asyncssh sizes a length-less read from this.
    """
    await _seed(vfs, "/blob.bin", PAYLOAD)
    fake_discord.fail_uploads_from = fake_discord.uploads + 1

    handle = await vfs.open("/blob.bin", read=True, write=True, append=True)
    with pytest.raises(DiscordFailure):
        await handle.write_at(0, EXTRA)

    assert handle.size == len(PAYLOAD)


async def test_reading_a_failed_handle_does_not_resurrect_its_writes(
    vfs, fake_discord, master_key
):
    """A read used to flush, and Discord may have recovered by then.

    That would upload and commit bytes whose write the client was already
    told had failed. Reads of what did commit still work.
    """
    await _seed(vfs, "/blob.bin", PAYLOAD)
    fake_discord.fail_uploads_from = fake_discord.uploads + 1

    handle = await vfs.open("/blob.bin", read=True, write=True, append=True)
    with pytest.raises(DiscordFailure):
        await handle.write_at(0, EXTRA)

    fake_discord.fail_uploads_from = None
    uploads_before = fake_discord.uploads

    assert await handle.read_at(0, len(PAYLOAD)) == PAYLOAD
    assert fake_discord.uploads == uploads_before, (
        "a read on a failed handle uploaded the buffer it had refused to write"
    )
    assert await _read_back(master_key, "/blob.bin") == (len(PAYLOAD), PAYLOAD)


# --------------------------------------------------------------- SFTP-level


async def test_a_failed_append_over_sftp_leaves_the_file_intact(
    sftp_port, fake_discord
):
    """The same regression, driven the way a client actually hits it.

    `put -a` against a server whose Discord uploads are failing. The write
    must fail; the file must not.
    """
    async with connect(sftp_port) as conn:
        async with conn.start_sftp_client() as client:
            async with client.open("/blob.bin", "wb") as f:
                await f.write(PAYLOAD)

    fake_discord.fail_uploads_from = fake_discord.uploads + 1

    async with connect(sftp_port) as conn:
        async with conn.start_sftp_client() as client:
            handle = await client.open("/blob.bin", "ab")
            with pytest.raises(asyncssh.SFTPError):
                await handle.write(EXTRA)
                await handle.close()

    fake_discord.fail_uploads_from = None

    async with connect(sftp_port) as conn:
        async with conn.start_sftp_client() as client:
            assert (await client.stat("/blob.bin")).size == len(PAYLOAD)
            async with client.open("/blob.bin", "rb") as f:
                assert await f.read() == PAYLOAD


# ------------------------------------------------------------- the three numbers
#
# `_rollback()` only ever deletes what the handle itself uploaded, which is the
# whole point of the docstring above it. That makes "nothing was left behind on
# Discord" a claim rather than an observation, and these pin the evidence for
# it: what went up, what came back down, and what is still up there referenced
# by nothing.


async def test_the_tally_counts_what_reached_discord_and_what_came_back(
    vfs, fake_discord
):
    # Chunk one lands and commits; chunk two never leaves the ground.
    fake_discord.fail_uploads_from = 2

    handle = await vfs.open("/new.bin", read=False, write=True, create=True)
    with pytest.raises(DiscordFailure):
        await handle.write_at(0, PAYLOAD)

    assert handle.failure_tally == {
        "chunks_uploaded": 1,
        "attachments_released": 1,
        "orphans": 0,
        "stale_node": False,
    }, "the chunk that landed was released, so nothing should be reported orphaned"
    assert fake_discord.store == {}


async def test_a_delete_that_fails_is_reported_as_an_orphan(vfs, fake_discord):
    """The number that means somebody has to go and look.

    An orphan is not a retryable condition: the chunk is on Discord, nothing
    references it, and running the upload again makes a second one rather than
    reclaiming the first.
    """
    fake_discord.fail_uploads_from = 2
    fake_discord.fail_deletes = True

    handle = await vfs.open("/new.bin", read=False, write=True, create=True)
    with pytest.raises(DiscordFailure):
        await handle.write_at(0, PAYLOAD)

    assert handle.failure_tally == {
        "chunks_uploaded": 1,
        "attachments_released": 0,
        "orphans": 1,
        "stale_node": False,
    }
    assert fake_discord.delete_attempts == 1, "the delete was attempted, not skipped"
    assert len(fake_discord.store) == 1, "the orphan really is still on Discord"


async def test_an_existing_file_reports_no_releases_because_it_deletes_nothing(
    vfs, fake_discord
):
    """The counts must not paper over the rule they exist to evidence.

    A handle appending to a file it did not create deletes nothing on purpose
    -- those chunks are the file's last good commit. Reporting them as
    "released" would describe the very deletion this class refuses to do.
    """
    await _seed(vfs, "/blob.bin", PAYLOAD)
    fake_discord.fail_uploads_from = fake_discord.uploads + 1

    handle = await vfs.open("/blob.bin", read=False, write=True, append=True)
    with pytest.raises(DiscordFailure):
        await handle.write_at(0, EXTRA)

    assert handle.failure_tally == {
        "chunks_uploaded": 0,
        "attachments_released": 0,
        "orphans": 0,
        "stale_node": False,
    }
    assert fake_discord.deleted == []


async def test_a_rollback_that_cannot_delete_the_row_says_so(vfs, fake_discord, fake_db):
    """The failure mode a real stack found and the fakes never would have.

    Stopping MongoDB part way through a 30 MiB upload against the dev stack
    produced this: the unwind deleted both attachments from Discord, then
    could not delete the node, because deleting the node needs the database
    that had just gone away. The row survived pointing at message ids Discord
    now answers `10008 Unknown Message` for -- a file listed at 9 MiB that
    fails on its first read.

    The bookkeeping failure was always swallowed here and stays swallowed;
    what changes is that it is no longer swallowed *silently*, because the
    client was telling people the file did not exist.
    """
    fake_discord.fail_uploads_from = 2
    fake_db.nodes.fail_deletes = True

    handle = await vfs.open("/doomed.bin", read=False, write=True, create=True)
    with pytest.raises(DiscordFailure):
        await handle.write_at(0, PAYLOAD)

    tally = handle.failure_tally
    assert tally["stale_node"] is True, (
        "the node could not be deleted, and nothing said so"
    )
    assert tally["attachments_released"] == 1
    assert tally["orphans"] == 0, (
        "the attachment really was released -- the mess is the row, not the chunk"
    )

    # The shape of the mess, stated outright: a node that outlived its bytes.
    left = [d for d in fake_db.nodes.docs if d.get("filename") == "doomed.bin"]
    assert len(left) == 1, "the regression this test exists for"
    assert fake_discord.store == {}, "its chunks are gone from Discord"
