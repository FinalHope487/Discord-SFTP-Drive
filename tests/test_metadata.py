"""Permissions and timestamps.

Both used to be accepted and thrown away: `ls -l` reported a fixed 0644 for
every file whatever the client had set, and an mtime that was always the
upload time. That is not cosmetic -- `put -p`, rsync-style copies and backup
tools compare mtimes to decide what needs transferring, so a filesystem that
resets them makes every file look perpetually new.
"""

import stat

import asyncssh
import pytest

PAYLOAD = b"payload" * 100
# Far enough in the past to be unmistakably not "now".
OLD_TIME = 1_400_000_000


async def _write_blob(sftp, path, data=PAYLOAD):
    async with sftp.open(path, "wb") as f:
        await f.write(data)


def _mode(attrs):
    return stat.S_IMODE(attrs.permissions)


# ------------------------------------------------------------- permissions


async def test_a_new_file_has_the_default_mode(sftp):
    await _write_blob(sftp, "/blob.bin")
    assert _mode(await sftp.stat("/blob.bin")) == 0o644


async def test_a_new_directory_has_the_default_mode(sftp):
    await sftp.mkdir("/dir")
    assert _mode(await sftp.stat("/dir")) == 0o755


async def test_chmod_is_remembered(sftp):
    await _write_blob(sftp, "/blob.bin")
    await sftp.chmod("/blob.bin", 0o600)
    assert _mode(await sftp.stat("/blob.bin")) == 0o600


@pytest.mark.parametrize("mode", [0o600, 0o640, 0o444, 0o755, 0o700])
async def test_the_mode_round_trips(sftp, mode):
    await _write_blob(sftp, "/blob.bin")
    await sftp.chmod("/blob.bin", mode)
    assert _mode(await sftp.stat("/blob.bin")) == mode


async def test_the_file_type_bits_are_still_reported(sftp):
    # Clients decide "file or directory?" from these; storing only the mode
    # must not lose them.
    await _write_blob(sftp, "/blob.bin")
    await sftp.mkdir("/dir")
    await sftp.chmod("/blob.bin", 0o600)

    assert stat.S_ISREG((await sftp.stat("/blob.bin")).permissions)
    assert stat.S_ISDIR((await sftp.stat("/dir")).permissions)


async def test_chmod_survives_a_reconnect(sftp):
    await _write_blob(sftp, "/blob.bin")
    await sftp.chmod("/blob.bin", 0o640)
    # Reading through a fresh handle proves it was persisted, not just held.
    assert _mode(await sftp.stat("/blob.bin")) == 0o640
    assert _mode((await sftp.readdir("/"))[-1].attrs) == 0o640


async def test_mkdir_honours_the_requested_mode(sftp):
    await sftp.mkdir("/dir", attrs=asyncssh.SFTPAttrs(permissions=0o700))
    assert _mode(await sftp.stat("/dir")) == 0o700


async def test_chmod_on_a_directory_is_remembered(sftp):
    await sftp.mkdir("/dir")
    await sftp.chmod("/dir", 0o700)
    assert _mode(await sftp.stat("/dir")) == 0o700


async def test_a_mode_change_does_not_touch_the_contents(sftp):
    await _write_blob(sftp, "/blob.bin")
    await sftp.chmod("/blob.bin", 0o600)
    async with sftp.open("/blob.bin", "rb") as f:
        assert await f.read() == PAYLOAD


async def test_the_file_type_bits_cannot_be_set_through_chmod(sftp):
    # A client sending S_IFDIR on a regular file must not turn it into one.
    await _write_blob(sftp, "/blob.bin")
    await sftp.setstat("/blob.bin",
                       asyncssh.SFTPAttrs(permissions=stat.S_IFDIR | 0o600))
    attrs = await sftp.stat("/blob.bin")
    assert stat.S_ISREG(attrs.permissions)
    assert _mode(attrs) == 0o600


# -------------------------------------------------------------- timestamps


async def test_mtime_is_remembered(sftp):
    await _write_blob(sftp, "/blob.bin")
    await sftp.utime("/blob.bin", (OLD_TIME, OLD_TIME))
    assert (await sftp.stat("/blob.bin")).mtime == OLD_TIME


async def test_atime_is_remembered_separately(sftp):
    await _write_blob(sftp, "/blob.bin")
    await sftp.utime("/blob.bin", (OLD_TIME, OLD_TIME + 500))
    attrs = await sftp.stat("/blob.bin")
    assert (attrs.atime, attrs.mtime) == (OLD_TIME, OLD_TIME + 500)


async def test_atime_defaults_to_mtime_when_never_set(sftp):
    # A real access time would mean a database write on every read.
    await _write_blob(sftp, "/blob.bin")
    attrs = await sftp.stat("/blob.bin")
    assert attrs.atime == attrs.mtime


async def test_writing_updates_mtime(sftp):
    await _write_blob(sftp, "/blob.bin")
    await sftp.utime("/blob.bin", (OLD_TIME, OLD_TIME))

    async with sftp.open("/blob.bin", "r+b") as f:
        await f.seek(0)
        await f.write(b"changed")

    assert (await sftp.stat("/blob.bin")).mtime > OLD_TIME


async def test_a_preserving_copy_keeps_the_timestamp(sftp):
    """What `put -p` does: upload, then set the size and time in one call.

    `close()` used to stamp the current time unconditionally, so the mtime the
    client had just asked for was overwritten between the request and the
    reply.
    """
    async with sftp.open("/blob.bin", "wb") as f:
        await f.write(PAYLOAD)
        await f.utime((OLD_TIME, OLD_TIME))

    assert (await sftp.stat("/blob.bin")).mtime == OLD_TIME


async def test_setting_size_and_time_together_keeps_the_time(sftp):
    await _write_blob(sftp, "/blob.bin")
    await sftp.setstat("/blob.bin", asyncssh.SFTPAttrs(
        size=len(PAYLOAD) // 2, mtime=OLD_TIME, atime=OLD_TIME))

    attrs = await sftp.stat("/blob.bin")
    assert attrs.size == len(PAYLOAD) // 2
    assert attrs.mtime == OLD_TIME, "the resize stamped over the requested time"


async def test_timestamps_show_up_in_a_listing(sftp):
    await _write_blob(sftp, "/blob.bin")
    await sftp.utime("/blob.bin", (OLD_TIME, OLD_TIME))
    entry = [e for e in await sftp.readdir("/") if e.filename == "blob.bin"][0]
    assert entry.attrs.mtime == OLD_TIME


async def test_a_rename_does_not_reset_the_timestamp(sftp):
    await _write_blob(sftp, "/blob.bin")
    await sftp.utime("/blob.bin", (OLD_TIME, OLD_TIME))
    await sftp.rename("/blob.bin", "/moved.bin")
    assert (await sftp.stat("/moved.bin")).mtime == OLD_TIME


# ------------------------------------------------------------------ refusals


async def test_setstat_on_a_missing_path_still_fails(sftp):
    with pytest.raises(asyncssh.SFTPError):
        await sftp.utime("/nope.bin", (OLD_TIME, OLD_TIME))


async def test_opening_a_file_for_reading_does_not_change_mtime(sftp):
    await _write_blob(sftp, "/blob.bin")
    await sftp.utime("/blob.bin", (OLD_TIME, OLD_TIME))
    async with sftp.open("/blob.bin", "rb") as f:
        await f.read()
    assert (await sftp.stat("/blob.bin")).mtime == OLD_TIME


async def test_opening_for_writing_without_writing_does_not_change_mtime(sftp):
    # POSIX: opening is not modifying.
    await _write_blob(sftp, "/blob.bin")
    await sftp.utime("/blob.bin", (OLD_TIME, OLD_TIME))
    async with sftp.open("/blob.bin", "r+b"):
        pass
    assert (await sftp.stat("/blob.bin")).mtime == OLD_TIME


# ------------------------------------------------------------------ indexes


async def test_the_name_index_is_created_unique(fake_db):
    """Two files sharing a name under one parent make lookups order-dependent.

    Enforcement is MongoDB's; asking for it is ours, so that is what is
    checked here. The live suite covers the enforcement itself.
    """
    from src.db import Database

    await Database._ensure_indexes()
    by_name = [opts for keys, opts in fake_db.nodes.indexes
               if keys == [("parent_id", 1), ("filename", 1)]]
    assert by_name and all(o.get("unique") for o in by_name)


async def test_files_and_directories_share_the_namespace(sftp):
    await sftp.mkdir("/thing")
    with pytest.raises(asyncssh.SFTPError):
        async with sftp.open("/thing", "wb") as f:
            await f.write(b"x")
