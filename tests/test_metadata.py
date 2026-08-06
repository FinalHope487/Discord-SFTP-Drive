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
#
# These three read `fake_db.nodes.indexes` -- the fake's record of what was
# asked for, not indexes that exist -- and the last two drive MongoDB's
# refusal to change an index in place by injecting `OperationFailure`. None of
# that has a SQLite counterpart, so they are marked `mongo_only` and skipped
# under `--db=sqlite`, where the same indexes are instead built for real and
# enforced. What `_ensure_indexes` asks for is pinned backend-independently in
# `test_db_indexes.py`.


@pytest.mark.mongo_only
async def test_the_name_index_is_created_unique(fake_db):
    """Two files sharing a name under one parent make lookups order-dependent.

    Enforcement is MongoDB's; asking for it is ours, so that is what is
    checked here. The live deployment covers the enforcement itself.
    """
    from src.db import Database

    await Database._ensure_indexes()
    by_name = [opts for keys, opts in fake_db.nodes.indexes
               if keys == [("parent_id", 1), ("filename", 1)]]
    assert by_name and all(o.get("unique") for o in by_name)

    # And partial, over live nodes only. A trashed node keeps its name and its
    # place, so a full unique index would turn "delete notes.txt, write a new
    # notes.txt" into a duplicate key error -- a failure the fake cannot
    # produce, because uniqueness is the one thing it deliberately does not
    # enforce.
    #
    # This line used to assert `{"$exists": False}`, which is what the code
    # asked for and what MongoDB rejects outright: `$exists: false` is outside
    # the grammar a partialFilterExpression accepts, so the index was never
    # created and the server would not start. The fake validates nothing, so
    # this test passed against an index no deployment could build. An equality
    # against null is the same set of documents -- it matches a null and a
    # missing field -- and is inside the grammar. See `test_db_indexes.py`.
    assert all(o.get("partialFilterExpression") == {"trashed_at": None}
               for o in by_name)


@pytest.mark.mongo_only
async def test_an_existing_non_unique_index_is_upgraded(fake_db):
    """What an upgrade actually hits.

    MongoDB will not change an index in place: asking for `unique=True` where
    a plain index of the same shape exists fails with IndexKeySpecsConflict.
    Left unhandled the server simply refuses to start -- which is how this was
    found, on a real deployment, after the unit tests were green.
    """
    from pymongo.errors import OperationFailure

    from src.db import Database

    # The index that is in the way has to actually be there: the upgrade path
    # finds it by key spec in order to drop it by name, which is the only way
    # that works once the difference is more than uniqueness alone.
    fake_db.nodes.indexes.append(([("parent_id", 1), ("filename", 1)], {}))
    conflict = OperationFailure("index exists", code=86)
    fake_db.nodes.create_index_errors = [conflict]

    await Database._ensure_indexes()

    assert fake_db.nodes.dropped_indexes == ["parent_id_1_filename_1"]
    by_name = [opts for keys, opts in fake_db.nodes.indexes
               if keys == [("parent_id", 1), ("filename", 1)]]
    assert any(o.get("unique") for o in by_name), "the index was never upgraded"


@pytest.mark.mongo_only
async def test_duplicates_block_the_upgrade_with_a_readable_error(fake_db):
    # The one case that cannot be resolved automatically: which duplicate to
    # keep is not the server's decision.
    from pymongo.errors import OperationFailure

    from src.db import Database

    fake_db.nodes.indexes.append(([("parent_id", 1), ("filename", 1)], {}))
    fake_db.nodes.create_index_errors = [
        OperationFailure("index exists", code=86),
        OperationFailure("dup key", code=11000),
    ]

    with pytest.raises(OperationFailure, match="already contains duplicates"):
        await Database._ensure_indexes()

    # And the collection is not left without an index at all.
    assert any(keys == [("parent_id", 1), ("filename", 1)]
               for keys, _ in fake_db.nodes.indexes)


async def test_files_and_directories_share_the_namespace(sftp):
    await sftp.mkdir("/thing")
    with pytest.raises(asyncssh.SFTPError):
        async with sftp.open("/thing", "wb") as f:
            await f.write(b"x")
