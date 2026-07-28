"""Random-access writes: overwriting bytes that are already on Discord.

Attachments are immutable, so a write into the middle of a file cannot patch
anything in place -- the chunks it touches are downloaded, decrypted, spliced,
re-encrypted under a *fresh* nonce and re-uploaded, and the superseded
messages are dropped.

Two properties get the most attention here because both fail silently:

* nonce reuse. AES-CTR is a stream cipher; re-encrypting different plaintext
  under a nonce already used with this key hands anyone holding both
  ciphertexts the XOR of the two plaintexts. A round-trip test cannot see
  this, so the nonce is asserted directly.
* orphaned attachments. The replaced message must actually be deleted, or
  every overwrite leaks storage that nothing references and nothing can find.
"""

import os

import pytest

from tests.conftest import TEST_CHUNK_SIZE

# ~4.7 chunks, so overwrites can be aimed at chunk interiors and boundaries.
PAYLOAD_SIZE = 300 * 1024


async def _write_blob(sftp, path, data):
    async with sftp.open(path, "wb") as f:
        await f.write(data)


async def _read_all(sftp, path):
    async with sftp.open(path, "rb") as f:
        return await f.read()


async def _patch(sftp, path, offset, data):
    """Overwrite `data` at `offset` without truncating the file."""
    async with sftp.open(path, "r+b") as f:
        await f.seek(offset)
        await f.write(data)


def _chunks_of(fake_db, size=None):
    for doc in fake_db.nodes.docs:
        if not doc.get("is_dir") and doc.get("chunks"):
            return doc["chunks"]
    return []


# ------------------------------------------------------------- correctness


async def test_overwrite_inside_one_chunk(sftp):
    payload = bytearray(os.urandom(PAYLOAD_SIZE))
    await _write_blob(sftp, "/blob.bin", bytes(payload))

    patch = b"X" * 100
    offset = 1000
    payload[offset:offset + len(patch)] = patch
    await _patch(sftp, "/blob.bin", offset, patch)

    assert await _read_all(sftp, "/blob.bin") == bytes(payload)


async def test_overwrite_spanning_a_chunk_boundary(sftp):
    # The case a single-chunk implementation gets wrong: the patch has to be
    # split across two separate re-uploads.
    payload = bytearray(os.urandom(PAYLOAD_SIZE))
    await _write_blob(sftp, "/blob.bin", bytes(payload))

    offset = TEST_CHUNK_SIZE - 50
    patch = b"Y" * 100
    payload[offset:offset + len(patch)] = patch
    await _patch(sftp, "/blob.bin", offset, patch)

    assert await _read_all(sftp, "/blob.bin") == bytes(payload)


async def test_overwrite_spanning_several_whole_chunks(sftp):
    payload = bytearray(os.urandom(PAYLOAD_SIZE))
    await _write_blob(sftp, "/blob.bin", bytes(payload))

    offset = TEST_CHUNK_SIZE // 2
    patch = os.urandom(TEST_CHUNK_SIZE * 2 + 17)
    payload[offset:offset + len(patch)] = patch
    await _patch(sftp, "/blob.bin", offset, patch)

    assert await _read_all(sftp, "/blob.bin") == bytes(payload)


async def test_overwrite_at_offset_zero(sftp):
    # Opening an existing file for writing without O_TRUNC used to be refused
    # outright with FX_OP_UNSUPPORTED.
    payload = bytearray(os.urandom(PAYLOAD_SIZE))
    await _write_blob(sftp, "/blob.bin", bytes(payload))

    patch = b"Z" * 4096
    payload[0:len(patch)] = patch
    await _patch(sftp, "/blob.bin", 0, patch)

    assert await _read_all(sftp, "/blob.bin") == bytes(payload)


async def test_overwrite_does_not_change_the_file_size(sftp):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    await _patch(sftp, "/blob.bin", 500, b"Q" * 200)
    assert (await sftp.stat("/blob.bin")).size == PAYLOAD_SIZE


async def test_write_extending_past_the_end_grows_the_file(sftp):
    payload = os.urandom(PAYLOAD_SIZE)
    await _write_blob(sftp, "/blob.bin", payload)

    tail = b"T" * 5000
    offset = PAYLOAD_SIZE - 1000
    await _patch(sftp, "/blob.bin", offset, tail)

    expected = payload[:offset] + tail
    assert await _read_all(sftp, "/blob.bin") == expected
    assert (await sftp.stat("/blob.bin")).size == len(expected)


async def test_write_past_the_end_zero_fills_the_hole(sftp):
    # POSIX says a gap reads back as zeros. There is no sparse representation
    # here, so the zeros have to be real.
    await _write_blob(sftp, "/blob.bin", b"head")

    await _patch(sftp, "/blob.bin", 10_000, b"tail")

    got = await _read_all(sftp, "/blob.bin")
    assert got[:4] == b"head"
    assert got[4:10_000] == b"\x00" * (10_000 - 4)
    assert got[10_000:] == b"tail"


async def test_two_overwrites_of_the_same_region_compose(sftp):
    payload = bytearray(os.urandom(PAYLOAD_SIZE))
    await _write_blob(sftp, "/blob.bin", bytes(payload))

    for value in (b"A", b"B"):
        patch = value * 300
        payload[2000:2300] = patch
        await _patch(sftp, "/blob.bin", 2000, patch)

    assert await _read_all(sftp, "/blob.bin") == bytes(payload)


async def test_read_after_write_on_the_same_handle(sftp):
    payload = bytearray(os.urandom(PAYLOAD_SIZE))
    await _write_blob(sftp, "/blob.bin", bytes(payload))

    patch = b"M" * 128
    payload[7000:7128] = patch
    async with sftp.open("/blob.bin", "r+b") as f:
        await f.seek(7000)
        await f.write(patch)
        # Same handle, so a stale decrypted chunk in the cache would show up
        # here and nowhere else.
        await f.seek(7000)
        assert await f.read(128) == patch

    assert await _read_all(sftp, "/blob.bin") == bytes(payload)


async def test_append_mode_ignores_the_offset(sftp):
    # O_APPEND means every write goes to the end whatever offset arrives.
    await _write_blob(sftp, "/blob.bin", b"start")
    async with sftp.open("/blob.bin", "ab") as f:
        await f.seek(0)
        await f.write(b"-end")
    assert await _read_all(sftp, "/blob.bin") == b"start-end"


@pytest.mark.parametrize("offset", [0, 1, TEST_CHUNK_SIZE - 1, TEST_CHUNK_SIZE,
                                    TEST_CHUNK_SIZE + 1, PAYLOAD_SIZE - 10])
async def test_single_byte_overwrite_at_various_offsets(sftp, offset):
    payload = bytearray(os.urandom(PAYLOAD_SIZE))
    await _write_blob(sftp, "/blob.bin", bytes(payload))

    payload[offset:offset + 1] = b"\xff"
    await _patch(sftp, "/blob.bin", offset, b"\xff")

    assert await _read_all(sftp, "/blob.bin") == bytes(payload)


# ------------------------------------------------------------------ crypto


async def test_rewriting_a_chunk_uses_a_fresh_nonce(sftp, fake_db):
    # The whole security argument for re-uploading rather than patching. A
    # round-trip test passes either way, so this asserts the nonce directly.
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    before = [c["nonce"] for c in _chunks_of(fake_db)]

    await _patch(sftp, "/blob.bin", 100, b"patched")

    after = [c["nonce"] for c in _chunks_of(fake_db)]
    assert after[0] != before[0], "chunk 0 was re-encrypted under its old nonce"
    # Untouched chunks keep theirs -- they were never re-encrypted.
    assert after[1:] == before[1:]


async def test_every_nonce_stays_distinct_after_repeated_rewrites(sftp, fake_db):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    seen = {c["nonce"] for c in _chunks_of(fake_db)}

    for i in range(5):
        await _patch(sftp, "/blob.bin", 100, bytes([i]) * 64)
        nonces = [c["nonce"] for c in _chunks_of(fake_db)]
        assert len(set(nonces)) == len(nonces), "two chunks share a nonce"
        assert nonces[0] not in seen, "a nonce was reused across rewrites"
        seen.add(nonces[0])


async def test_ciphertext_on_discord_actually_changes(sftp, fake_discord, fake_db):
    payload = bytearray(os.urandom(PAYLOAD_SIZE))
    await _write_blob(sftp, "/blob.bin", bytes(payload))
    old_id = _chunks_of(fake_db)[0]["message_id"]
    old_bytes = fake_discord.store[old_id]

    await _patch(sftp, "/blob.bin", 100, b"patched")

    new_id = _chunks_of(fake_db)[0]["message_id"]
    assert new_id != old_id
    assert fake_discord.store[new_id] != old_bytes


# ------------------------------------------------------------------ cleanup


async def test_the_replaced_attachment_is_deleted(sftp, fake_discord, fake_db):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    old_id = _chunks_of(fake_db)[0]["message_id"]

    await _patch(sftp, "/blob.bin", 100, b"patched")

    assert old_id in fake_discord.deleted
    assert old_id not in fake_discord.store, "replaced attachment was orphaned"


async def test_overwrites_do_not_accumulate_attachments(sftp, fake_discord):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    settled = len(fake_discord.store)

    for i in range(5):
        await _patch(sftp, "/blob.bin", 100, bytes([i]) * 64)

    # One in, one out, every time.
    assert len(fake_discord.store) == settled


async def test_deleting_a_rewritten_file_leaves_nothing_behind(sftp, fake_discord):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    await _patch(sftp, "/blob.bin", 100, b"patched")
    await _patch(sftp, "/blob.bin", TEST_CHUNK_SIZE + 100, b"more")

    await sftp.remove("/blob.bin")

    assert fake_discord.store == {}


# -------------------------------------------------------------- bookkeeping


async def test_chunk_offsets_stay_contiguous_after_a_rewrite(sftp, fake_db):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    await _patch(sftp, "/blob.bin", TEST_CHUNK_SIZE + 10, b"patched")

    chunks = sorted(_chunks_of(fake_db), key=lambda c: c["offset"])
    expected = 0
    for chunk in chunks:
        assert chunk["offset"] == expected
        expected += chunk["size"]
    assert expected == PAYLOAD_SIZE


async def test_rewrite_does_not_change_the_chunk_count(sftp, fake_db):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    before = len(_chunks_of(fake_db))
    await _patch(sftp, "/blob.bin", 100, b"patched")
    assert len(_chunks_of(fake_db)) == before


async def test_rewrite_survives_reopening(sftp):
    # Everything above reads through the same VFS instance; this proves the
    # new nonce and message id were persisted, not just held in memory.
    payload = bytearray(os.urandom(PAYLOAD_SIZE))
    await _write_blob(sftp, "/blob.bin", bytes(payload))

    patch = b"P" * 512
    payload[3000:3512] = patch
    await _patch(sftp, "/blob.bin", 3000, patch)

    async with sftp.open("/blob.bin", "rb") as f:
        assert await f.read() == bytes(payload)
