"""Login, the per-session key, and what happens when a session ends badly.

Three things converge here:

* the password is now the only way to reach the encryption key, so a login is
  not merely an access check -- a session that authenticates but cannot open
  the key would be a session that reads nothing;
* the key belongs to the connection, not the process, so it has to arrive
  through the login and leave with the connection;
* a connection that ends without an orderly close still has to flush what the
  client wrote. Anything under one chunk sits in a buffer until then, so
  getting this wrong loses the tail of every interrupted upload -- which is
  also what makes resuming one possible.
"""

import asyncio
import os

import asyncssh
import pytest

from src import keystore
from src.main import _drain
from src.sftp import active_connections
from tests.conftest import TEST_CHUNK_SIZE, TEST_PASSWORD, TEST_USER, connect

SMALL = b"a partial chunk" * 100          # far under TEST_CHUNK_SIZE


async def _closed(conn, timeout=5):
    """Wait for a connection to finish closing, but never for ever.

    A test that hangs reports as a suite timeout with no indication of which
    assertion was wrong, so the failure mode has to be a failure.
    """
    await asyncio.wait_for(conn.wait_closed(), timeout)


# ------------------------------------------------------------------- login


async def test_the_right_credentials_get_in(sftp_port):
    async with connect(sftp_port) as conn:
        async with conn.start_sftp_client() as client:
            assert await client.listdir("/") == [".", ".."]


@pytest.mark.parametrize("username,password", [
    (TEST_USER, "wrong-password-entirely"),
    ("wronguser", TEST_PASSWORD),
    (TEST_USER, ""),
])
async def test_bad_credentials_are_refused(sftp_port, username, password):
    with pytest.raises(asyncssh.Error):
        async with asyncssh.connect("127.0.0.1", sftp_port, known_hosts=None,
                                    gss_host=None, username=username,
                                    password=password):
            pass


async def test_a_login_cannot_succeed_without_the_key(sftp_port, fake_db, caplog):
    """The keystore going missing must close the door, not open it.

    The password check alone would still pass here -- it is comparing against
    the same configured value it always did. Only the unwrap notices.
    """
    fake_db.keystore.docs.clear()

    with pytest.raises(asyncssh.Error):
        async with connect(sftp_port):
            pass


async def test_the_session_key_is_the_stored_master_key(sftp_port, master_key):
    """The session must use the key from the keystore, not one of its own.

    Asserting the keystore is unchanged is not enough: a login that invented
    a key would leave the keystore untouched and still round-trip data
    happily, because every session in the test would invent the same one.
    Reading the file back through a VFS built from the *stored* key is what
    ties the two together -- a different key fails the integrity check.
    """
    import src.vfs as vfs_mod

    async with connect(sftp_port) as conn:
        async with conn.start_sftp_client() as client:
            async with client.open("/blob.bin", "wb") as f:
                await f.write(SMALL)

    assert await keystore.open_master_key(TEST_PASSWORD) == master_key

    handle = await vfs_mod.DiscordVFS(master_key).open(
        "/blob.bin", read=True, write=False)
    assert await handle.read_at(0, len(SMALL)) == SMALL


async def test_a_file_written_in_one_session_reads_in_the_next(sftp_port):
    payload = os.urandom(TEST_CHUNK_SIZE + 1000)

    async with connect(sftp_port) as conn:
        async with conn.start_sftp_client() as client:
            async with client.open("/blob.bin", "wb") as f:
                await f.write(payload)

    async with connect(sftp_port) as conn:
        async with conn.start_sftp_client() as client:
            async with client.open("/blob.bin", "rb") as f:
                assert await f.read() == payload


async def test_the_session_key_is_dropped_when_the_connection_ends(sftp_port):
    # Best effort -- Python cannot wipe a bytes object -- but the connection
    # must not still be holding a reference once it is gone.
    async with connect(sftp_port) as conn:
        async with conn.start_sftp_client() as client:
            await client.listdir("/")
        server_side = next(iter(active_connections()))
        assert server_side.get_extra_info("session_key")

    await asyncio.sleep(0.05)
    assert server_side.get_extra_info("session_key") is None


# ------------------------------------------------- ending without a close


async def test_a_dropped_connection_flushes_buffered_bytes(sftp_port):
    """What makes an interrupted upload resumable rather than truncated.

    The client never closes the handle: it writes less than one chunk, so the
    bytes are still in a buffer, and then the connection dies. asyncssh runs
    its session cleanup, which is what gets them to Discord.
    """
    conn = await connect(sftp_port)
    client = await conn.start_sftp_client()
    handle = await client.open("/blob.bin", "wb")
    await handle.write(SMALL)

    conn.abort()                 # no close, no disconnect
    await _closed(conn)
    await asyncio.sleep(0.1)     # let the server finish its cleanup

    async with connect(sftp_port) as conn2:
        async with conn2.start_sftp_client() as client2:
            assert (await client2.stat("/blob.bin")).size == len(SMALL)
            async with client2.open("/blob.bin", "rb") as f:
                assert await f.read() == SMALL


async def test_an_interrupted_upload_can_be_resumed_by_appending(sftp_port):
    """`todo.md` asks for progress records in MongoDB to make this possible.

    It already is, and without them: chunks are committed as they upload, so
    the file's size *is* the resume point. This is that claim as a test rather
    than an assertion in a document.
    """
    payload = os.urandom(TEST_CHUNK_SIZE * 2 + 500)
    cut = TEST_CHUNK_SIZE + 200

    conn = await connect(sftp_port)
    client = await conn.start_sftp_client()
    handle = await client.open("/blob.bin", "wb")
    await handle.write(payload[:cut])
    conn.abort()
    await _closed(conn)
    await asyncio.sleep(0.1)

    async with connect(sftp_port) as conn2:
        async with conn2.start_sftp_client() as client2:
            resume_from = (await client2.stat("/blob.bin")).size
            assert resume_from == cut, "the resume point is not the bytes kept"

            async with client2.open("/blob.bin", "ab") as f:
                await f.write(payload[resume_from:])

            async with client2.open("/blob.bin", "rb") as f:
                assert await f.read() == payload


# ------------------------------------------------------------- shutting down


async def test_drain_closes_live_connections(sftp_port, host_key):
    import src.sftp as sftp_mod

    server = await asyncssh.listen(
        "127.0.0.1", 0, server_factory=sftp_mod.DiscordSSHServer,
        sftp_factory=sftp_mod.DiscordSFTPServer, server_host_keys=[host_key],
        gss_host=None)
    port = server.get_addresses()[0][1]

    conn = await connect(port)
    client = await conn.start_sftp_client()
    await client.listdir("/")
    assert active_connections()

    await _drain(server, grace=0.1)

    assert not active_connections()
    await _closed(conn)


async def test_drain_flushes_what_a_live_session_had_buffered(sftp_port, host_key):
    # The reason shutdown waits at all: a client that has written less than a
    # chunk has nothing on Discord yet.
    import src.sftp as sftp_mod

    server = await asyncssh.listen(
        "127.0.0.1", 0, server_factory=sftp_mod.DiscordSSHServer,
        sftp_factory=sftp_mod.DiscordSFTPServer, server_host_keys=[host_key],
        gss_host=None)
    port = server.get_addresses()[0][1]

    conn = await connect(port)
    client = await conn.start_sftp_client()
    handle = await client.open("/blob.bin", "wb")
    await handle.write(SMALL)

    await _drain(server, grace=0.1)
    await _closed(conn)

    async with connect(sftp_port) as conn2:
        async with conn2.start_sftp_client() as client2:
            async with client2.open("/blob.bin", "rb") as f:
                assert await f.read() == SMALL


async def test_drain_returns_promptly_when_nothing_is_connected(sftp_port, host_key):
    import src.sftp as sftp_mod

    server = await asyncssh.listen(
        "127.0.0.1", 0, server_factory=sftp_mod.DiscordSSHServer,
        sftp_factory=sftp_mod.DiscordSFTPServer, server_host_keys=[host_key],
        gss_host=None)

    loop = asyncio.get_running_loop()
    started = loop.time()
    await _drain(server, grace=5)
    assert loop.time() - started < 1, "an idle server waited out the grace period"
