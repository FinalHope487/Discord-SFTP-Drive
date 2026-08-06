"""Two structural writes at once, in one directory.

The gap this closes is not subtle once you look for it, and nothing in the
other 578 tests went near it: every one of them drives a single caller. A
directory's entry tag is staged from the children visible at staging time and
parked in one field, `entries_mac_pending`, so a second stager arriving before
the first commits simply overwrites what the first parked. Both then commit,
and the directory ends up signed for a child set that never existed. `list_dir`
refuses it from then on, there is no API that puts it back, and in the root
that is the whole drive.

FileZilla and WinSCP open several transfer connections by default, so "two
uploads into one folder" is the ordinary case, not an exotic one.

These tests only mean something because `tests/fakes.py` suspends on every
database call the way a real one does. Against a fake that returned without
yielding, both coroutines would run start to finish in turn and every
assertion here would pass on the broken code.
"""

import asyncio

import pytest

from src import vfs as vfs_mod
from src.crypto import dir_entries_tag
from src.vfs import IntegrityError


async def _listing(vfs):
    return sorted(entry["filename"] for entry in await vfs.list_dir("/"))


async def test_parallel_makedir_in_one_directory_keeps_it_listable(vfs):
    await asyncio.gather(*(vfs.makedir(f"/d{i}") for i in range(4)))

    assert await _listing(vfs) == ["d0", "d1", "d2", "d3"]


async def test_parallel_uploads_into_one_directory_keep_it_listable(vfs):
    async def upload(name, payload):
        handle = await vfs.open(f"/{name}", read=False, write=True, create=True)
        await handle.write_at(0, payload)
        await handle.close()

    await asyncio.gather(*(upload(f"f{i}", bytes([i]) * 1024) for i in range(4)))

    assert await _listing(vfs) == ["f0", "f1", "f2", "f3"]
    for i in range(4):
        handle = await vfs.open(f"/f{i}", read=True, write=False)
        assert await handle.read_at(0, 1024) == bytes([i]) * 1024
        await handle.close()


async def test_a_mix_of_structural_writes_keeps_the_directory_listable(vfs):
    """Create, rename and purge all racing in the same directory.

    Each of these stages the same field. Running one kind at a time would miss
    the pairs that only conflict across kinds -- a `purge` removing an entry
    while a `makedir` adds one is the same overwrite as two `makedir`s, and it
    is reachable from the trash sweeper without any client doing anything
    unusual at all.
    """
    await vfs.makedir("/old")
    await vfs.makedir("/doomed")
    await vfs.trash("/doomed")
    doomed_id = (await vfs.list_trash())[0]["node"]["id"]

    await asyncio.gather(
        vfs.makedir("/fresh"),
        vfs.rename("/old", "/renamed"),
        vfs.purge(doomed_id),
    )

    assert await _listing(vfs) == ["fresh", "renamed"]


async def test_an_unwinding_upload_does_not_corrupt_a_concurrent_write(
        vfs, fake_discord):
    """The unwind path stages entries too, and it runs when things are worst.

    An upload fails at its first chunk and rolls its node back out of the
    directory while a `mkdir` is committing itself into it. Both touch the same
    staged field. The other side is deliberately a `mkdir` rather than a second
    upload: the injected Discord outage is global, so a racing upload would
    fail too and there would be no successful writer left to protect.
    """
    fake_discord.fail_uploads_from = 1

    async def failing():
        handle = await vfs.open("/bad", read=False, write=True, create=True)
        with pytest.raises(Exception):
            await handle.write_at(0, b"x" * (vfs_mod.MAX_CHUNK_SIZE + 1))

    await asyncio.gather(failing(), vfs.makedir("/fresh"))

    assert await _listing(vfs) == ["fresh"]


async def test_staging_without_the_lock_is_refused(vfs):
    """The guard, tested directly.

    Every call site is wrapped today. The one that is not written yet is the
    one worth protecting against, and its symptom -- a directory that stops
    listing, for whoever happened to be uploading two files at the time -- is a
    long way from the line that caused it.
    """
    with pytest.raises(RuntimeError, match="without holding its lock"):
        await vfs._stage_entries(vfs.root_id, add=[("x", "x")])


async def test_the_lock_registry_does_not_grow(vfs):
    """Locks are dropped once nobody holds or wants them.

    `_node_versions` is deliberately unbounded because a missing entry there
    reads as "unchanged", which would be a lie. A missing lock reads as
    "uncontended", which is true, so this registry may be emptied -- and has to
    be, or it is a per-directory leak for the life of the process.
    """
    await asyncio.gather(*(vfs.makedir(f"/d{i}") for i in range(8)))

    assert vfs_mod._dir_locks == {}
    assert vfs_mod._dirs_held_by == {}


async def test_two_renames_crossing_the_same_pair_of_directories(vfs):
    """Opposite directions at once, which is where lock ordering shows up.

    One caller wants (a, b) and the other wants (b, a). Taken in the order the
    caller happens to name them, both hold one and wait for the other for ever;
    `_locked_dirs` sorts, so they queue instead.
    """
    await vfs.makedir("/a")
    await vfs.makedir("/b")
    for path in ("/a/one", "/b/two"):
        handle = await vfs.open(path, read=False, write=True, create=True)
        await handle.close()

    await asyncio.wait_for(asyncio.gather(
        vfs.rename("/a/one", "/b/one"),
        vfs.rename("/b/two", "/a/two"),
    ), timeout=10)

    assert sorted(e["filename"] for e in await vfs.list_dir("/a")) == ["two"]
    assert sorted(e["filename"] for e in await vfs.list_dir("/b")) == ["one"]


async def test_a_directory_signed_for_a_child_it_does_not_have_is_refused(vfs):
    """The failure mode itself, arranged by hand rather than by racing.

    This is what makes the assertions above load-bearing: if `list_dir` let a
    mismatched entry tag through, every test in this file would pass whatever
    the concurrency did.
    """
    await vfs.makedir("/a")
    await vfs_mod.db.get_db().nodes.update_one(
        {"id": vfs.root_id},
        {"$set": {"entries_mac": dir_entries_tag(
            vfs.key, dir_id=vfs.root_id, entries=[("ghost", "ghost")]).hex()}})

    with pytest.raises(IntegrityError):
        await vfs.list_dir("/")
