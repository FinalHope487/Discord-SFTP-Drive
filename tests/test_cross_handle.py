"""Cross-handle visibility: a handle opened before another handle changed the
same node used to keep serving its own stale copy forever.

Confirmed by hand against real infrastructure (see ROADMAP.md): connection B
truncated a 20MB file to 4096 bytes while connection A held the file open;
connection A's handle kept reporting the old 20MB size and, worse, handed back
1024 bytes of the plaintext that used to live past the new end.

The fix is `vfs._node_versions`, a process-wide `node id -> last committed
mac` cache. A handle compares its own copy against it before trusting
anything -- a dict lookup when nothing changed, a refetch (and re-verify)
only on an actual mismatch. The database-count tests below pin that second
half down: this is meant to cost nothing in the common case.
"""

import os

from src.vfs import ROOT_ID, DiscordVFS
from tests.conftest import TEST_CHUNK_SIZE, connect

PAYLOAD = os.urandom(TEST_CHUNK_SIZE + 1000)


async def _write(vfs, path, data):
    handle = await vfs.open(path, read=False, write=True, create=True)
    await handle.write_at(0, data)
    await handle.close()


# --------------------------------------------------------------- VFS-level


async def test_a_truncate_by_another_handle_is_seen_on_the_next_read(vfs, master_key):
    await _write(vfs, "/blob.bin", PAYLOAD)

    reader = await vfs.open("/blob.bin", read=True, write=False)
    assert await reader.read_at(0, len(PAYLOAD)) == PAYLOAD

    other = DiscordVFS(master_key, ROOT_ID)
    await other.truncate("/blob.bin", 4096)

    # Old behaviour: the reader's cached chunk list still claimed the file was
    # its original length, so this offset -- now past the real end -- handed
    # back 1024 bytes of the plaintext that used to live there instead of EOF.
    assert await reader.read_at(4096, 1024) == b""
    assert reader.size == 4096


async def test_a_truncates_new_size_is_seen_via_refresh(vfs, master_key):
    await _write(vfs, "/blob.bin", PAYLOAD)

    reader = await vfs.open("/blob.bin", read=True, write=False)
    assert reader.size == len(PAYLOAD)

    other = DiscordVFS(master_key, ROOT_ID)
    await other.truncate("/blob.bin", 4096)

    # This is what `fstat` calls before reading `.size` -- see src/sftp.py.
    await reader.refresh()
    assert reader.size == 4096


async def test_an_overwrite_by_another_handle_is_seen_on_the_next_read(vfs, master_key):
    await _write(vfs, "/blob.bin", PAYLOAD)

    reader = await vfs.open("/blob.bin", read=True, write=False)
    assert await reader.read_at(0, 10) == PAYLOAD[:10]

    replacement = os.urandom(10)
    writer = DiscordVFS(master_key, ROOT_ID)
    handle = await writer.open("/blob.bin", read=False, write=True)
    await handle.write_at(0, replacement)
    await handle.close()

    assert await reader.read_at(0, 10) == replacement


async def test_no_change_costs_no_extra_database_round_trip(vfs, fake_db):
    await _write(vfs, "/blob.bin", PAYLOAD)

    reader = await vfs.open("/blob.bin", read=True, write=False)
    await reader.read_at(0, 10)
    before = fake_db.nodes.find_one_calls

    await reader.read_at(10, 10)
    await reader.refresh()

    assert fake_db.nodes.find_one_calls == before, (
        "an idle node should not cost a database read on every handle call"
    )


async def test_an_actual_change_costs_exactly_one_database_round_trip(
    vfs, master_key, fake_db
):
    await _write(vfs, "/blob.bin", PAYLOAD)

    reader = await vfs.open("/blob.bin", read=True, write=False)
    await reader.read_at(0, 10)

    other = DiscordVFS(master_key, ROOT_ID)
    await other.truncate("/blob.bin", 4096)

    before = fake_db.nodes.find_one_calls
    await reader.read_at(0, 10)
    assert fake_db.nodes.find_one_calls == before + 1


# --------------------------------------------------------------- SFTP-level


async def test_truncate_from_another_connection_is_visible_through_fstat(sftp_port):
    async with connect(sftp_port) as conn:
        async with conn.start_sftp_client() as client:
            async with client.open("/blob.bin", "wb") as f:
                await f.write(PAYLOAD)

    async with connect(sftp_port) as conn_a:
        async with conn_a.start_sftp_client() as client_a:
            handle_a = await client_a.open("/blob.bin", "rb")
            assert (await handle_a.stat()).size == len(PAYLOAD)

            async with connect(sftp_port) as conn_b:
                async with conn_b.start_sftp_client() as client_b:
                    await client_b.truncate("/blob.bin", 4096)

            assert (await handle_a.stat()).size == 4096
            await handle_a.close()
