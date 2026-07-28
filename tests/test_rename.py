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
