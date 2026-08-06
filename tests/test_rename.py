"""Rename and move.

This is what unblocks FileZilla / WinSCP / rsync: their upload flow writes to
`name.filepart` and renames it into place, so a server that answers rename
with FX_OP_UNSUPPORTED cannot receive a file from any of them.

Renames move metadata only. Discord attachments are addressed by message id,
so nothing is re-uploaded -- which the content assertions below are really
checking.
"""

import os

import asyncssh
import pytest

PAYLOAD_SIZE = 150 * 1024  # spans several chunks


@pytest.fixture
async def tree(sftp):
    """`/a/orig.bin` holding a multi-chunk payload, plus an empty `/b`."""
    payload = os.urandom(PAYLOAD_SIZE)
    await sftp.mkdir("/a")
    await sftp.mkdir("/b")
    async with sftp.open("/a/orig.bin", "wb") as f:
        await f.write(payload)
    return payload


# ------------------------------------------------------- the real client flow


async def test_filepart_rename_flow(sftp, tree):
    async with sftp.open("/b/report.pdf.filepart", "wb") as f:
        await f.write(b"pretend pdf")
    await sftp.rename("/b/report.pdf.filepart", "/b/report.pdf")

    listing = await sftp.listdir("/b")
    assert "report.pdf" in listing
    assert "report.pdf.filepart" not in listing


# ----------------------------------------------------- rename within a folder


async def test_rename_removes_the_old_name(sftp, tree):
    await sftp.rename("/a/orig.bin", "/a/renamed.bin")
    assert "orig.bin" not in await sftp.listdir("/a")


async def test_rename_preserves_size(sftp, tree):
    await sftp.rename("/a/orig.bin", "/a/renamed.bin")
    st = await sftp.stat("/a/renamed.bin")
    assert st.size == len(tree)


async def test_rename_preserves_content(sftp, tree):
    await sftp.rename("/a/orig.bin", "/a/renamed.bin")
    async with sftp.open("/a/renamed.bin", "rb") as f:
        assert await f.read() == tree


# --------------------------------------------------------- move across folders


async def test_move_leaves_the_source_directory(sftp, tree):
    await sftp.rename("/a/orig.bin", "/b/moved.bin")
    assert "orig.bin" not in await sftp.listdir("/a")


async def test_move_appears_in_the_target_directory(sftp, tree):
    await sftp.rename("/a/orig.bin", "/b/moved.bin")
    assert "moved.bin" in await sftp.listdir("/b")


async def test_move_preserves_content(sftp, tree):
    await sftp.rename("/a/orig.bin", "/b/moved.bin")
    async with sftp.open("/b/moved.bin", "rb") as f:
        assert await f.read() == tree


# ----------------------------------------------------------- directory rename


async def test_directory_rename(sftp, tree):
    await sftp.rename("/a", "/archive")
    assert "archive" in await sftp.listdir("/")


async def test_nested_children_follow_a_directory_rename(sftp, tree):
    # Children are found by parent_id, so they need no rewriting -- this is
    # the assertion that proves it.
    await sftp.mkdir("/a/inner")
    async with sftp.open("/a/inner/deep.txt", "wb") as f:
        await f.write(b"deep")

    await sftp.rename("/a", "/archive")

    async with sftp.open("/archive/inner/deep.txt", "rb") as f:
        assert await f.read() == b"deep"


# ---------------------------------------------------------------- guard rails


async def test_rename_onto_existing_target_is_rejected(sftp, tree):
    # SFTP v3 semantics: rename must not clobber. posix_rename is the
    # opt-in overwrite, tested below.
    async with sftp.open("/b/taken.bin", "wb") as f:
        await f.write(b"occupied")
    with pytest.raises(asyncssh.SFTPError):
        await sftp.rename("/a/orig.bin", "/b/taken.bin")


async def test_rename_of_missing_source_is_no_such_file(sftp, tree):
    with pytest.raises(asyncssh.SFTPNoSuchFile):
        await sftp.rename("/nope.bin", "/b/x.bin")


async def test_directory_cannot_move_into_its_own_subtree(sftp, tree):
    # Otherwise the subtree is orphaned: reachable from nothing, deletable by
    # nothing.
    await sftp.mkdir("/a/inner")
    with pytest.raises(asyncssh.SFTPError):
        await sftp.rename("/a", "/a/inner/loop")


# -------------------------------------------------------------- posix_rename


async def test_posix_rename_consumes_the_source(sftp, tree):
    async with sftp.open("/b/target.bin", "wb") as f:
        await f.write(b"old target")
    await sftp.posix_rename("/a/orig.bin", "/b/target.bin")
    assert "orig.bin" not in await sftp.listdir("/a")


async def test_posix_rename_replaces_target_content(sftp, tree):
    async with sftp.open("/b/target.bin", "wb") as f:
        await f.write(b"old target")
    await sftp.posix_rename("/a/orig.bin", "/b/target.bin")
    async with sftp.open("/b/target.bin", "rb") as f:
        assert await f.read() == tree


async def test_posix_rename_releases_the_replaced_attachments(sftp, fake_discord, tree):
    async with sftp.open("/b/target.bin", "wb") as f:
        await f.write(b"old target")
    before = len(fake_discord.store)

    await sftp.posix_rename("/a/orig.bin", "/b/target.bin")

    assert len(fake_discord.store) < before


# ------------------------------------------- overwriting a directory's remains


async def _names(sftp, path):
    """`listdir` without the two entries every directory has."""
    return sorted(name for name in await sftp.listdir(path)
                  if name not in (".", ".."))


async def test_overwriting_a_directory_destroys_its_trashed_children(
        sftp, vfs, fake_discord, tree):
    """`rm *` then `posix_rename` over the top, which is an ordinary sequence.

    Emptiness is judged on live children only -- deliberately, since that is
    what lets a client clear a directory over SFTP and then `rmdir` it. What
    the overwrite branch then did was delete the directory's own document and
    nothing else, so every child still sitting in the trash was left pointing
    at a parent that no longer existed.

    Nothing could reach them after that. `_ancestors_of` could not walk to the
    root, so `list_trash` did not list them, the sweeper never came for them,
    and `purge` could not be asked for them by name. Their Discord attachments
    were unreachable for good -- a leak with no tool that could even report it.
    """
    await sftp.mkdir("/victim")
    untouched = set(fake_discord.store)
    async with sftp.open("/victim/doomed.bin", "wb") as f:
        await f.write(os.urandom(PAYLOAD_SIZE))
    doomed = set(fake_discord.store) - untouched
    assert len(doomed) > 1, "the payload should span several attachments"

    # Into the trash, not destroyed: this is what `rm` does now.
    await sftp.remove("/victim/doomed.bin")
    assert len(await vfs.list_trash()) == 1
    assert doomed <= set(fake_discord.store), "the trash must keep the chunks"

    await sftp.posix_rename("/a", "/victim")

    assert await vfs.list_trash() == [], (
        "a trashed child survived the overwrite of its parent, and nothing "
        "can reach it any more")
    assert not (doomed & set(fake_discord.store)), (
        "the trashed child's attachments were left on Discord with no node "
        "anywhere pointing at them")
    assert untouched <= set(fake_discord.store), (
        "the purge reached past the directory it was overwriting")


async def test_overwriting_a_directory_with_live_children_is_still_refused(
        sftp, vfs, tree):
    """The purge is for what the emptiness check ignores, not a way around it."""
    await sftp.mkdir("/victim")
    async with sftp.open("/victim/alive.bin", "wb") as f:
        await f.write(b"still here")

    with pytest.raises(asyncssh.SFTPError):
        await sftp.posix_rename("/a", "/victim")

    assert await _names(sftp, "/victim") == ["alive.bin"]
    assert "a" in await _names(sftp, "/")


async def test_the_directory_the_overwrite_happened_in_still_lists(sftp, vfs, tree):
    """Two staged tags on one directory in one operation is how this breaks.

    The purge stages and promotes the target's removal, and the rename then
    stages the same directory again. Staging recomputes from the children on
    disk, so an extra `remove` for something already gone would sign for a set
    that is not there -- and the directory would stop listing, which is the
    failure mode with no recovery path.
    """
    await sftp.mkdir("/victim")
    await sftp.posix_rename("/a", "/victim")

    assert await _names(sftp, "/") == ["b", "victim"]
    assert await _names(sftp, "/victim") == ["orig.bin"]
