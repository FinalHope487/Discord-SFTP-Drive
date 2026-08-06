"""Core SFTP surface, driven through a real client over loopback."""

import os
import stat as stat_mod

import asyncssh
import pytest

from tests.conftest import TEST_CHUNK_SIZE

# ~4.7 chunks: enough that reads and writes cross boundaries repeatedly.
PAYLOAD_SIZE = 300 * 1024


async def _write_blob(sftp, path, data):
    async with sftp.open(path, "wb") as f:
        await f.write(data)


# ------------------------------------------------------------ path resolution


async def test_realpath_of_cwd_is_root(sftp):
    # The first thing every client does after connecting. This used to return
    # the server's *local* filesystem path.
    assert await sftp.realpath(".") == "/"


async def test_realpath_normalises_dot_segments(sftp):
    assert await sftp.realpath("/a/../b/./c") == "/b/c"


# -------------------------------------------------------------- directory ops


async def test_mkdir_then_listdir(sftp):
    await sftp.mkdir("/docs")
    assert "docs" in await sftp.listdir("/")


async def test_listdir_includes_dot_entries(sftp):
    # Some clients refuse to render a directory that omits them.
    listing = await sftp.listdir("/")
    assert "." in listing and ".." in listing


async def test_stat_reports_directory_mode(sftp):
    await sftp.mkdir("/docs")
    st = await sftp.stat("/docs")
    assert stat_mod.S_ISDIR(st.permissions)


async def test_stat_mtime_is_populated(sftp):
    # Attributes previously carried no mtime, so every file showed as 1970.
    await sftp.mkdir("/docs")
    st = await sftp.stat("/docs")
    assert st.mtime and st.mtime > 0


async def test_rmdir_removes_directory(sftp):
    await sftp.mkdir("/docs")
    await sftp.rmdir("/docs")
    assert "docs" not in await sftp.listdir("/")


# ----------------------------------------------------------- upload/download


async def test_write_spans_multiple_chunks(sftp, fake_discord):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    expected = PAYLOAD_SIZE // TEST_CHUNK_SIZE
    assert fake_discord.uploads >= expected


async def test_reported_size_is_exact(sftp):
    payload = os.urandom(PAYLOAD_SIZE)
    await _write_blob(sftp, "/blob.bin", payload)
    st = await sftp.stat("/blob.bin")
    assert st.size == len(payload)


async def test_full_download_is_byte_identical(sftp):
    payload = os.urandom(PAYLOAD_SIZE)
    await _write_blob(sftp, "/blob.bin", payload)
    async with sftp.open("/blob.bin", "rb") as f:
        assert await f.read() == payload


@pytest.mark.parametrize(
    "offset, size",
    [
        (0, 10),
        (100, 5000),
        (TEST_CHUNK_SIZE - 5, 20),  # straddles a chunk boundary
        (200 * 1024, 4096),
        (PAYLOAD_SIZE - 3, 3),  # right up against EOF
    ],
    ids=["start", "mid-chunk", "chunk-boundary", "late-chunk", "eof-edge"],
)
async def test_random_access_read(sftp, offset, size):
    payload = os.urandom(PAYLOAD_SIZE)
    await _write_blob(sftp, "/blob.bin", payload)
    async with sftp.open("/blob.bin", "rb") as f:
        await f.seek(offset)
        assert await f.read(size) == payload[offset:offset + size]


async def test_read_past_eof_returns_nothing(sftp):
    payload = os.urandom(PAYLOAD_SIZE)
    await _write_blob(sftp, "/blob.bin", payload)
    async with sftp.open("/blob.bin", "rb") as f:
        await f.seek(len(payload))
        assert await f.read(100) in (b"", None)


async def test_stored_bytes_are_encrypted(sftp, fake_discord):
    # The whole point of the project: what lands on Discord must not be the
    # user's plaintext.
    payload = os.urandom(PAYLOAD_SIZE)
    await _write_blob(sftp, "/blob.bin", payload)
    stored = b"".join(fake_discord.store.values())
    assert payload[:64] not in stored


# --------------------------------------------------------------- error codes


async def test_stat_of_missing_file_is_no_such_file(sftp):
    with pytest.raises(asyncssh.SFTPNoSuchFile):
        await sftp.stat("/nope.bin")


async def test_symlink_reports_unsupported(sftp):
    await _write_blob(sftp, "/blob.bin", b"x")
    with pytest.raises(asyncssh.SFTPOpUnsupported):
        await sftp.symlink("/blob.bin", "/link")


async def test_readlink_reports_unsupported(sftp):
    await _write_blob(sftp, "/blob.bin", b"x")
    with pytest.raises(asyncssh.SFTPOpUnsupported):
        await sftp.readlink("/blob.bin")


# ------------------------------------------------------ overwrite and delete


async def test_a_truncating_overwrite_keeps_the_old_copy_until_it_is_purged(
        sftp, vfs, fake_discord):
    """The old copy goes to the trash now, rather than straight to Discord's
    delete endpoint.

    This used to assert the attachments were released the moment the overwrite
    landed, and it cannot any more -- for the same reason `remove` stopped
    emptying Discord when the trash arrived. Overwriting writes the new bytes
    into a node of their own and swaps it in at the end, so that an upload
    which never finishes cannot destroy what it was replacing; the file it
    replaced is then trashed rather than deleted, which is what `restore`
    already does when a name collides.

    The guarantee that mattered -- no attachment outlives the file that owned
    it -- has not gone away. It has moved to `purge`, which is where this now
    checks for it. What it costs is stated plainly: the old copy occupies
    Discord for the retention period, so overwriting a large file repeatedly
    holds every version until the sweeper catches up.
    """
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    old = set(fake_discord.store)
    assert len(old) > 1, "the payload should span several attachments"

    await _write_blob(sftp, "/blob.bin", b"tiny")

    assert old <= set(fake_discord.store), "the trashed copy must survive"
    assert "blob.bin" in await sftp.listdir("/"), "one live file, not two"

    [item] = await vfs.list_trash()
    await vfs.purge(item["node"]["id"])

    assert not (old & set(fake_discord.store)), (
        "purging the replaced copy must release every one of its attachments"
    )


async def test_overwritten_content_is_correct(sftp):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    await _write_blob(sftp, "/blob.bin", b"tiny")
    async with sftp.open("/blob.bin", "rb") as f:
        assert await f.read() == b"tiny"


async def test_remove_deletes_the_entry(sftp):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    await sftp.remove("/blob.bin")
    assert "blob.bin" not in await sftp.listdir("/")


async def test_remove_keeps_attachments_until_the_file_is_purged(
        sftp, vfs, fake_discord):
    """Deleting is two steps, and only the second one destroys anything.

    This used to assert that `remove` emptied Discord. It cannot any more, and
    the reason is the point of the trash: the chunks have to survive the
    delete or there would be nothing to restore. The guarantee that mattered
    -- no attachment outlives the file that owned it -- has not gone away, it
    has moved to `purge`, which is where this now checks for it.
    """
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    await sftp.remove("/blob.bin")

    assert "blob.bin" not in await sftp.listdir("/")
    assert fake_discord.store != {}, "the trash must keep the chunks"

    [item] = await vfs.list_trash()
    await vfs.purge(item["node"]["id"])
    assert fake_discord.store == {}
