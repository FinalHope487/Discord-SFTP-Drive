"""An SFTP connection really cut at the socket, mid-upload.

The gap this closes, recorded in ROADMAP.md: both unwind fixes of 2026-08-06
were made and verified on the HTTP path. SFTP handles have a different
lifecycle -- asyncssh owns them, and closes them itself when a session ends --
so "does cutting the connection commit half a file" was an open question there,
not an answered one. The `_rollback` fix is shared (it lives on `DiscordFile`),
but nothing had checked what a dropped SSH connection actually *calls*.

It calls `close()`, which commits. **And on this protocol that is the correct
answer**, which is the part worth writing down, because it is the opposite of
the conclusion the HTTP path reached.

The two are not the same situation. An HTTP PUT declares its length up front,
so a body that stops early is a body the server knows is short, and committing
it produces a file listed at a size nobody ever sent. SFTP declares nothing: a
client writes at offsets and each write is acknowledged, so bytes that reached
the server are bytes the client was told were stored. A file of exactly the
acknowledged length is the honest outcome, and it is what every real filesystem
gives you when a writer dies. `test_session.py` already depends on it -- an
interrupted upload is resumed by appending, because the file's size *is* the
resume point.

So these tests pin the shape of that answer rather than asserting an unwind:
the length is what was acknowledged, the directory still lists, nothing is
orphaned on Discord, and an interrupted overwrite leaves the copy it was
replacing recoverable from the trash.
"""

import asyncio

import pytest

from src import sftp as sftp_mod
from src.vfs import ROOT_ID, DiscordVFS
from tests.conftest import TEST_CHUNK_SIZE, connect

CHUNKS = 2
WRITTEN = b"w" * (TEST_CHUNK_SIZE * CHUNKS)


@pytest.fixture
async def closed(monkeypatch):
    """Set once the server has finished closing a handle it owns.

    Waiting on this rather than on a sleep is what keeps the assertions honest:
    the cleanup is asynchronous and off the connection's own task, so a test
    that guessed a duration would go green on a machine that happened to be
    fast and flake everywhere else.
    """
    event = asyncio.Event()
    original = sftp_mod.DiscordSFTPServer.close

    async def watched(self, file_obj):
        try:
            return await original(self, file_obj)
        finally:
            event.set()

    monkeypatch.setattr(sftp_mod.DiscordSFTPServer, "close", watched)
    return event


async def _cut_mid_write(port, path, *, payload=WRITTEN, pflags="wb"):
    """Write `payload`, then destroy the connection without closing the file.

    `abort()` rather than `close()`: a graceful close would flush and close the
    handle from the client side, which is the ordinary path and not this one.
    Aborting drops the transport, which is what a lost network or a killed
    client looks like from the server.
    """
    conn = await connect(port)
    client = await conn.start_sftp_client()
    handle = await client.open(path, pflags)
    await handle.write(payload)
    conn.abort()
    return conn


async def _survivor(master_key):
    """A VFS sharing no state with the connection that died."""
    return DiscordVFS(master_key, ROOT_ID)


async def test_a_cut_upload_keeps_exactly_what_was_acknowledged(
        sftp_port, closed, master_key, fake_discord):
    await _cut_mid_write(sftp_port, "/cut.bin")
    await asyncio.wait_for(closed.wait(), timeout=30)

    vfs = await _survivor(master_key)
    handle = await vfs.open("/cut.bin", read=True, write=False)
    try:
        assert handle.size == len(WRITTEN), (
            "the file is not the length the client was told had been stored")
        assert await handle.read_at(0, handle.size) == WRITTEN
    finally:
        await handle.close()


async def test_a_cut_upload_leaves_the_directory_listable(
        sftp_port, closed, master_key):
    """The failure with no recovery path, checked on this protocol too.

    Creating a file stages the parent's entry tag before any byte is written.
    Whatever the disconnect does next -- commit or unwind -- the tag has to end
    up describing the children that are actually there.
    """
    await _cut_mid_write(sftp_port, "/cut.bin")
    await asyncio.wait_for(closed.wait(), timeout=30)

    vfs = await _survivor(master_key)
    assert [e["filename"] for e in await vfs.list_dir("/")] == ["cut.bin"]


async def test_a_cut_upload_leaves_no_unreferenced_attachment(
        sftp_port, closed, master_key, fake_discord):
    """Every attachment on Discord has to be one the committed file names."""
    await _cut_mid_write(sftp_port, "/cut.bin")
    await asyncio.wait_for(closed.wait(), timeout=30)

    assert fake_discord.uploads > 0, (
        "nothing reached Discord before the cut, so this would pass without "
        "exercising anything")

    vfs = await _survivor(master_key)
    node = await vfs.get_node("/cut.bin")
    referenced = {c["message_id"] for c in node["chunks"]}
    assert set(fake_discord.store) == referenced


async def test_a_cut_overwrite_leaves_the_old_copy_in_the_trash(
        sftp_port, closed, master_key, fake_discord):
    """The new overwrite path, on the protocol that owns its own handles.

    Committing here is the same call as above and the same answer -- but it now
    swaps in a detached node and trashes what it replaced, so the interrupted
    version of this is recoverable rather than destructive. Before, `O_TRUNC`
    had already destroyed the original at open time and a cut connection left
    nothing to go back to.
    """
    original = b"o" * (TEST_CHUNK_SIZE * 2 + 9)
    async with connect(sftp_port) as conn:
        async with conn.start_sftp_client() as client:
            async with client.open("/keep.bin", "wb") as handle:
                await handle.write(original)

    # The seeding connection closed its handle through the same wrapper, so
    # without this the wait below returns immediately and the assertions run
    # while the cut connection is still half way through its swap -- reading
    # the old bytes and calling it a pass.
    closed.clear()

    await _cut_mid_write(sftp_port, "/keep.bin")
    await asyncio.wait_for(closed.wait(), timeout=30)

    vfs = await _survivor(master_key)
    live = await vfs.open("/keep.bin", read=True, write=False)
    try:
        assert await live.read_at(0, live.size) == WRITTEN
    finally:
        await live.close()

    [item] = await vfs.list_trash()
    await vfs.restore(item["node"]["id"], on_conflict="replace")
    restored = await vfs.open("/keep.bin", read=True, write=False)
    try:
        assert await restored.read_at(0, restored.size) == original, (
            "the copy the interrupted overwrite replaced is not recoverable")
    finally:
        await restored.close()


async def test_a_cut_connection_does_not_take_another_one_down(
        sftp_port, closed, master_key):
    """The damage has to stay on the connection that had it.

    Handles are per connection but `_open_files`, `_node_versions` and the
    directory locks are per process, so a cut connection unwinding through
    shared state is exactly where one client's accident becomes everybody's.
    """
    async with connect(sftp_port) as conn:
        async with conn.start_sftp_client() as bystander:
            await _cut_mid_write(sftp_port, "/cut.bin")
            await asyncio.wait_for(closed.wait(), timeout=30)

            async with bystander.open("/after.bin", "wb") as handle:
                await handle.write(b"still works")

            names = sorted(n for n in await bystander.listdir("/")
                           if n not in (".", ".."))
            assert names == ["after.bin", "cut.bin"]


async def test_the_close_wrapper_actually_wrapped_something(sftp_port, closed):
    """Scaffolding check.

    Without it, a change to how asyncssh cleans up handles would leave every
    test above waiting on an Event nobody sets, and the timeout would read as a
    product bug rather than as a fixture that stopped matching.
    """
    async with connect(sftp_port) as conn:
        async with conn.start_sftp_client() as client:
            async with client.open("/plain.bin", "wb") as handle:
                await handle.write(b"x")

    await asyncio.wait_for(closed.wait(), timeout=30)


async def test_an_open_handle_is_not_left_registered_after_a_cut(
        sftp_port, closed):
    """`_open_files` is what shutdown drains. An entry that never leaves it is
    a handle the drain waits on for ever."""
    await _cut_mid_write(sftp_port, "/cut.bin")
    await asyncio.wait_for(closed.wait(), timeout=30)

    assert sftp_mod.open_files() == frozenset()
