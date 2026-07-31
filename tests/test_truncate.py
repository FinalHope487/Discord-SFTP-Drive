"""Resizing a file: SFTP `setstat` / `fsetstat` with a size.

Both used to return FX_OP_UNSUPPORTED, which broke the clients that set the
final size before writing a byte.

Shrinking and growing are asymmetric on purpose:

* Shrinking touches Discord. Attachments are immutable, so the one chunk the
  new end falls inside is re-uploaded trimmed under a fresh nonce and the
  chunks past it are deleted outright.
* Growing touches nothing. The recorded size runs past the last chunk and the
  gap is a hole that reads back as zeros.

The hole is not an optimisation for its own sake. Materialising those zeros
would put real chunks under the whole file, so a client that pre-allocates and
*then* uploads would have every subsequent write land mid-file and rewrite a
full chunk per SFTP packet. `test_presetting_the_size_does_not_change_the_
upload_count` is the regression that pins that down; it fails loudly if the
hole is ever materialised.
"""

import os

import asyncssh
import pytest

from tests.conftest import TEST_CHUNK_SIZE

# ~4.7 chunks, so a new end can be aimed at chunk interiors and boundaries.
PAYLOAD_SIZE = 300 * 1024


async def _write_blob(sftp, path, data):
    async with sftp.open(path, "wb") as f:
        await f.write(data)


async def _read_all(sftp, path):
    async with sftp.open(path, "rb") as f:
        return await f.read()


def _file_doc(fake_db):
    for doc in fake_db.nodes.docs:
        if not doc.get("is_dir") and "chunks" in doc:
            return doc
    raise AssertionError("no file node in the database")


def _chunks_of(fake_db):
    return sorted(_file_doc(fake_db)["chunks"], key=lambda c: c["offset"])


# ------------------------------------------------------------------ shrinking


@pytest.mark.parametrize("new_size", [
    0,                          # everything goes
    1,                          # one byte of the first chunk survives
    TEST_CHUNK_SIZE - 1,        # ends just inside the first chunk
    TEST_CHUNK_SIZE,            # exactly on a boundary: no chunk to trim
    TEST_CHUNK_SIZE + 1,        # one byte into the second
    PAYLOAD_SIZE - 1,           # trims only the last chunk
])
async def test_shrink_keeps_the_surviving_prefix(sftp, new_size):
    payload = os.urandom(PAYLOAD_SIZE)
    await _write_blob(sftp, "/blob.bin", payload)

    await sftp.truncate("/blob.bin", new_size)

    assert (await sftp.stat("/blob.bin")).size == new_size
    assert await _read_all(sftp, "/blob.bin") == payload[:new_size]


async def test_shrink_drops_the_chunks_past_the_new_end(sftp, fake_db):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    assert len(_chunks_of(fake_db)) > 2

    await sftp.truncate("/blob.bin", TEST_CHUNK_SIZE)

    chunks = _chunks_of(fake_db)
    assert len(chunks) == 1
    assert chunks[0]["size"] == TEST_CHUNK_SIZE


async def test_shrink_releases_the_discarded_attachments(sftp, fake_discord, fake_db):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    doomed = [c["message_id"] for c in _chunks_of(fake_db)[1:]]

    await sftp.truncate("/blob.bin", TEST_CHUNK_SIZE)

    for message_id in doomed:
        assert message_id in fake_discord.deleted
        assert message_id not in fake_discord.store, "attachment was orphaned"


async def test_shrink_to_zero_leaves_no_attachments(sftp, fake_discord):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))

    await sftp.truncate("/blob.bin", 0)

    assert fake_discord.store == {}
    assert await _read_all(sftp, "/blob.bin") == b""


async def test_trimming_a_chunk_replaces_its_attachment(sftp, fake_discord, fake_db):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    before = _chunks_of(fake_db)[0]["message_id"]

    await sftp.truncate("/blob.bin", 1000)

    after = _chunks_of(fake_db)[0]["message_id"]
    assert after != before, "the trimmed chunk kept its old attachment"
    assert before in fake_discord.deleted
    assert before not in fake_discord.store, "the superseded attachment was orphaned"
    assert len(fake_discord.store[after]) == 1000, "the stored ciphertext was not trimmed"


async def test_trimming_a_chunk_uses_a_fresh_nonce(sftp, fake_db):
    # Same argument as the random-write path: AES-CTR keystream reuse hands
    # anyone holding both ciphertexts the XOR of both plaintexts. A round-trip
    # test passes either way, so assert the nonce directly.
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    before = _chunks_of(fake_db)[0]["nonce"]

    await sftp.truncate("/blob.bin", 1000)

    assert _chunks_of(fake_db)[0]["nonce"] != before


async def test_trimming_a_chunk_updates_its_tag(sftp, fake_db):
    # A stale HMAC would make the trimmed chunk unreadable, and the failure
    # would land on whoever reads it next rather than here.
    payload = os.urandom(PAYLOAD_SIZE)
    await _write_blob(sftp, "/blob.bin", payload)
    before = _chunks_of(fake_db)[0]["hmac"]

    await sftp.truncate("/blob.bin", 1000)

    assert _chunks_of(fake_db)[0]["hmac"] != before
    # Reads go through verify_chunk, so this only succeeds if the tag matches.
    assert await _read_all(sftp, "/blob.bin") == payload[:1000]


async def test_chunk_offsets_stay_contiguous_after_a_shrink(sftp, fake_db):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))

    await sftp.truncate("/blob.bin", TEST_CHUNK_SIZE + 777)

    expected = 0
    for chunk in _chunks_of(fake_db):
        assert chunk["offset"] == expected
        expected += chunk["size"]
    assert expected == TEST_CHUNK_SIZE + 777


async def test_shrink_survives_reopening(sftp):
    # Proves the new nonce, tag and message id were persisted rather than only
    # held in the handle that did the truncation.
    payload = os.urandom(PAYLOAD_SIZE)
    await _write_blob(sftp, "/blob.bin", payload)

    await sftp.truncate("/blob.bin", 5000)

    async with sftp.open("/blob.bin", "rb") as f:
        assert await f.read() == payload[:5000]


# ------------------------------------------------------------------- growing


async def test_grow_reads_back_as_zeros(sftp):
    await _write_blob(sftp, "/blob.bin", b"head")

    await sftp.truncate("/blob.bin", 10_000)

    got = await _read_all(sftp, "/blob.bin")
    assert got == b"head" + bytes(10_000 - 4)
    assert (await sftp.stat("/blob.bin")).size == 10_000


async def test_grow_from_empty_reads_back_as_zeros(sftp):
    await _write_blob(sftp, "/blob.bin", b"")

    await sftp.truncate("/blob.bin", TEST_CHUNK_SIZE * 2 + 5)

    assert await _read_all(sftp, "/blob.bin") == bytes(TEST_CHUNK_SIZE * 2 + 5)


async def test_grow_uploads_nothing(sftp, fake_discord):
    await _write_blob(sftp, "/blob.bin", b"head")
    before = fake_discord.uploads

    await sftp.truncate("/blob.bin", 10 * TEST_CHUNK_SIZE)

    assert fake_discord.uploads == before, "the hole was materialised on Discord"


async def test_grow_survives_reopening(sftp):
    await _write_blob(sftp, "/blob.bin", b"head")
    await sftp.truncate("/blob.bin", 10_000)

    async with sftp.open("/blob.bin", "rb") as f:
        assert await f.read() == b"head" + bytes(10_000 - 4)


async def test_reading_past_the_end_returns_nothing(sftp):
    await _write_blob(sftp, "/blob.bin", b"head")
    await sftp.truncate("/blob.bin", 10_000)

    async with sftp.open("/blob.bin", "rb") as f:
        await f.seek(10_000)
        assert await f.read(100) == b""


async def test_shrink_then_grow_does_not_resurrect_the_old_bytes(sftp):
    # The whole reason a hole may not simply be "size is bigger now": the
    # discarded chunks must be gone, not merely unreferenced.
    payload = os.urandom(PAYLOAD_SIZE)
    await _write_blob(sftp, "/blob.bin", payload)

    await sftp.truncate("/blob.bin", 1000)
    await sftp.truncate("/blob.bin", PAYLOAD_SIZE)

    got = await _read_all(sftp, "/blob.bin")
    assert got == payload[:1000] + bytes(PAYLOAD_SIZE - 1000)


# ---------------------------------------------------- writing around a hole


async def test_presetting_the_size_does_not_change_the_upload_count(sftp, fake_discord):
    """The case this whole feature exists for.

    A client that declares the size up front and then uploads sequentially
    must cost exactly what an ordinary upload costs. If the hole were ever
    materialised, every write would land mid-file and re-upload a whole chunk
    per SFTP packet — the count here would go up by two orders of magnitude.
    """
    payload = os.urandom(PAYLOAD_SIZE)

    await _write_blob(sftp, "/plain.bin", payload)
    baseline = fake_discord.uploads

    fake_discord.uploads = 0
    async with sftp.open("/preset.bin", "wb") as f:
        await f.truncate(len(payload))
        await f.write(payload)

    assert fake_discord.uploads == baseline
    assert await _read_all(sftp, "/preset.bin") == payload


async def test_writing_at_the_start_of_a_hole_fills_it_in_place(sftp):
    await _write_blob(sftp, "/blob.bin", b"head")
    await sftp.truncate("/blob.bin", 10_000)

    async with sftp.open("/blob.bin", "r+b") as f:
        await f.seek(4)
        await f.write(b"body")

    got = await _read_all(sftp, "/blob.bin")
    assert got == b"headbody" + bytes(10_000 - 8)
    assert (await sftp.stat("/blob.bin")).size == 10_000


async def test_writing_inside_a_hole_materialises_the_gap(sftp):
    await _write_blob(sftp, "/blob.bin", b"head")
    await sftp.truncate("/blob.bin", 10_000)

    async with sftp.open("/blob.bin", "r+b") as f:
        await f.seek(5_000)
        await f.write(b"body")

    expected = b"head" + bytes(5_000 - 4) + b"body" + bytes(10_000 - 5_004)
    assert await _read_all(sftp, "/blob.bin") == expected
    assert (await sftp.stat("/blob.bin")).size == 10_000


async def test_writing_past_a_hole_extends_the_file(sftp):
    await _write_blob(sftp, "/blob.bin", b"head")
    await sftp.truncate("/blob.bin", 10_000)

    async with sftp.open("/blob.bin", "r+b") as f:
        await f.seek(12_000)
        await f.write(b"tail")

    got = await _read_all(sftp, "/blob.bin")
    assert got == b"head" + bytes(12_000 - 4) + b"tail"
    assert (await sftp.stat("/blob.bin")).size == 12_004


async def test_append_mode_lands_past_the_hole(sftp):
    # O_APPEND goes to end-of-*file*, which a hole puts well past the end of
    # the data. Appending at the start of the hole instead would silently
    # write the bytes thousands of positions too early.
    await _write_blob(sftp, "/blob.bin", b"head")
    await sftp.truncate("/blob.bin", 10_000)

    async with sftp.open("/blob.bin", "ab") as f:
        await f.write(b"tail")

    got = await _read_all(sftp, "/blob.bin")
    assert got == b"head" + bytes(10_000 - 4) + b"tail"
    assert (await sftp.stat("/blob.bin")).size == 10_004


# ------------------------------------------------------- on an open handle


async def test_truncate_on_a_handle_flushes_what_it_buffered(sftp):
    # Buffered bytes belong to no chunk yet. Resizing around them would drop
    # them, and the file would come back short.
    async with sftp.open("/blob.bin", "wb") as f:
        await f.write(b"A" * 100)
        await f.truncate(60)

    assert await _read_all(sftp, "/blob.bin") == b"A" * 60


async def test_reading_a_trimmed_chunk_the_handle_had_cached(sftp):
    # The handle caches decrypted chunks. Trimming one shortens it, so a
    # surviving cache entry hands back the bytes that were just cut -- and
    # only a read on that same handle can see it.
    payload = os.urandom(PAYLOAD_SIZE)
    await _write_blob(sftp, "/blob.bin", payload)

    async with sftp.open("/blob.bin", "r+b") as f:
        await f.seek(TEST_CHUNK_SIZE)
        assert await f.read(100) == payload[TEST_CHUNK_SIZE:TEST_CHUNK_SIZE + 100]

        await f.truncate(TEST_CHUNK_SIZE + 10)

        await f.seek(TEST_CHUNK_SIZE)
        assert await f.read() == payload[TEST_CHUNK_SIZE:TEST_CHUNK_SIZE + 10]


async def test_appending_after_a_truncate_that_freed_a_chunk_index(sftp):
    # Indices are handed out as len(chunks), so dropping the tail makes the
    # next append reuse an index the cache may still be holding. The new
    # chunk then reads back as the deleted one.
    payload = os.urandom(PAYLOAD_SIZE)
    await _write_blob(sftp, "/blob.bin", payload)
    keep = TEST_CHUNK_SIZE * 3

    async with sftp.open("/blob.bin", "r+b") as f:
        await f.seek(keep)
        await f.read(100)           # caches the chunk that is about to go

        await f.truncate(keep)
        await f.seek(keep)
        await f.write(b"tail")      # takes the freed index

        await f.seek(keep)
        assert await f.read() == b"tail"

    assert await _read_all(sftp, "/blob.bin") == payload[:keep] + b"tail"


async def test_truncating_then_appending_on_the_same_handle(sftp):
    payload = os.urandom(PAYLOAD_SIZE)
    await _write_blob(sftp, "/blob.bin", payload)

    async with sftp.open("/blob.bin", "r+b") as f:
        await f.truncate(1000)
        await f.seek(1000)
        await f.write(b"tail")

    assert await _read_all(sftp, "/blob.bin") == payload[:1000] + b"tail"


async def test_truncating_a_read_only_handle_is_refused(sftp):
    await _write_blob(sftp, "/blob.bin", b"payload")

    async with sftp.open("/blob.bin", "rb") as f:
        with pytest.raises(asyncssh.SFTPError):
            await f.truncate(3)

    assert await _read_all(sftp, "/blob.bin") == b"payload"


# ------------------------------------------------------------------ refusals


async def test_setstat_without_a_size_still_succeeds(sftp):
    # Permissions and timestamps are not modelled but must not fail the
    # client -- plenty of them chmod straight after uploading.
    await _write_blob(sftp, "/blob.bin", b"payload")

    await sftp.chmod("/blob.bin", 0o600)

    assert await _read_all(sftp, "/blob.bin") == b"payload"


async def test_setstat_on_a_missing_path_fails(sftp):
    with pytest.raises(asyncssh.SFTPError):
        await sftp.chmod("/nope.bin", 0o600)


async def test_truncating_a_missing_path_fails(sftp):
    with pytest.raises(asyncssh.SFTPError):
        await sftp.truncate("/nope.bin", 10)


async def test_truncating_a_directory_is_refused(sftp):
    await sftp.mkdir("/dir")

    with pytest.raises(asyncssh.SFTPError):
        await sftp.truncate("/dir", 10)


async def test_negative_size_is_refused(vfs):
    # Driven at the VFS layer: the size field is unsigned on the wire, so a
    # negative value cannot be sent through a real client.
    from src.vfs import VFSError

    await vfs.open("/blob.bin", read=False, write=True, create=True)

    with pytest.raises(VFSError):
        await vfs.truncate("/blob.bin", -1)


# ----------------------------------------------------------- reported size
# Resizing is only half of the size contract; `fstat` is the other half, and
# the bug below was found by a truncate test rather than by a truncate bug.


async def test_fstat_counts_bytes_that_are_still_buffered(sftp):
    # A write smaller than a chunk sits in the handle's buffer until there is
    # enough for an upload. Reporting the committed size would tell a client
    # that the write it just made did not happen.
    async with sftp.open("/blob.bin", "wb") as f:
        await f.write(b"A" * 100)
        assert (await f.stat()).size == 100


async def test_reading_back_an_unflushed_write_on_the_same_handle(sftp):
    # asyncssh sizes a length-less read() from fstat, so an under-reported
    # size does not merely look wrong -- the client is handed EOF and the
    # data silently never arrives.
    async with sftp.open("/blob.bin", "w+b") as f:
        await f.write(b"A" * 100)
        await f.seek(0)
        assert await f.read() == b"A" * 100


# -------------------------------------------------------------- no-op resize


async def test_truncating_to_the_current_size_changes_nothing(sftp, fake_discord):
    payload = os.urandom(PAYLOAD_SIZE)
    await _write_blob(sftp, "/blob.bin", payload)
    before = dict(fake_discord.store)

    await sftp.truncate("/blob.bin", PAYLOAD_SIZE)

    assert fake_discord.store == before, "a no-op resize touched Discord"
    assert await _read_all(sftp, "/blob.bin") == payload
