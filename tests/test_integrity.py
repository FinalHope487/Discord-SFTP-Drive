"""Per-chunk HMAC authentication.

AES-CTR alone is malleable: flipping a bit of ciphertext flips exactly that
bit of plaintext, and nothing notices. These tests tamper with what Discord
serves and assert the read fails rather than returning corrupted bytes.

The distinction that matters throughout: a *detected* failure is success
here. A test that merely round-trips clean data would pass with the whole
verification step deleted.
"""

import os

import pytest

from src.crypto import IntegrityError, chunk_tag, transform, verify_chunk
from tests.conftest import TEST_CHUNK_SIZE

PAYLOAD_SIZE = 300 * 1024

# Exactly 32 bytes, the same truncation config applies to AES_SECRET_KEY.
# HMAC would accept any length, so an over-long key only surfaces once AES
# sees it.
KEY = b"test-key-0123456789abcdef01234567"[:32]
OTHER_KEY = b"OTHER-key-0123456789abcdef012345"[:32]


async def _write_blob(sftp, path, data):
    async with sftp.open(path, "wb") as f:
        await f.write(data)


def _file_node(fake_db):
    for doc in fake_db.nodes.docs:
        if not doc.get("is_dir") and doc.get("chunks"):
            return doc
    raise AssertionError("no file node with chunks")


# ------------------------------------------------------------- the primitive


def test_a_tag_verifies_against_its_own_input():
    nonce, ct = os.urandom(16), os.urandom(1024)
    verify_chunk(KEY, nonce, ct, chunk_tag(KEY, nonce, ct).hex())


def test_a_modified_ciphertext_is_rejected():
    nonce, ct = os.urandom(16), bytearray(os.urandom(1024))
    tag = chunk_tag(KEY, nonce, bytes(ct)).hex()
    ct[500] ^= 0x01
    with pytest.raises(IntegrityError):
        verify_chunk(KEY, nonce, bytes(ct), tag)


def test_a_swapped_nonce_is_rejected():
    # The nonce is covered by the tag precisely so this cannot work: with a
    # tag over the ciphertext alone, swapping the stored nonce changes the
    # decrypted output while the tag still matches.
    ct = os.urandom(1024)
    tag = chunk_tag(KEY, os.urandom(16), ct).hex()
    with pytest.raises(IntegrityError):
        verify_chunk(KEY, os.urandom(16), ct, tag)


def test_a_tag_from_a_different_key_is_rejected():
    nonce, ct = os.urandom(16), os.urandom(1024)
    tag = chunk_tag(OTHER_KEY, nonce, ct).hex()
    with pytest.raises(IntegrityError):
        verify_chunk(KEY, nonce, ct, tag)


def test_a_missing_tag_is_rejected_not_skipped():
    # Fail closed. Accepting untagged chunks would mean an attacker can
    # disable verification by deleting a field.
    nonce, ct = os.urandom(16), os.urandom(1024)
    for absent in (None, ""):
        with pytest.raises(IntegrityError):
            verify_chunk(KEY, nonce, ct, absent)


def test_a_malformed_tag_is_rejected_cleanly():
    # Not a ValueError escaping from bytes.fromhex.
    nonce, ct = os.urandom(16), os.urandom(1024)
    with pytest.raises(IntegrityError):
        verify_chunk(KEY, nonce, ct, "nothex!!")


def test_the_mac_key_is_not_the_encryption_key():
    # Reusing one key for both cipher and MAC is the mistake this guards.
    # If the MAC key were the AES key, a tag would equal a plain HMAC under
    # that key -- so assert it does not.
    import hmac as hmac_mod
    nonce, ct = os.urandom(16), os.urandom(64)
    naive = hmac_mod.new(KEY, nonce + ct, "sha256").digest()
    assert chunk_tag(KEY, nonce, ct) != naive


def test_tags_differ_per_chunk():
    nonce = os.urandom(16)
    a = chunk_tag(KEY, nonce, b"chunk-a" * 100)
    b = chunk_tag(KEY, nonce, b"chunk-b" * 100)
    assert a != b


# ------------------------------------------------- end to end through SFTP


async def test_a_clean_file_still_round_trips(sftp):
    payload = os.urandom(PAYLOAD_SIZE)
    await _write_blob(sftp, "/blob.bin", payload)
    async with sftp.open("/blob.bin", "rb") as f:
        assert await f.read() == payload


async def test_every_chunk_gets_a_tag(sftp, fake_db):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    chunks = _file_node(fake_db)["chunks"]
    assert len(chunks) > 1
    assert all(c.get("hmac") for c in chunks)


async def test_tampering_with_discord_bytes_fails_the_read(sftp, fake_discord, fake_db):
    # The attack the whole feature exists for: whoever serves the ciphertext
    # alters it. The tag lives in MongoDB, so they cannot fix it up.
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    mid = _file_node(fake_db)["chunks"][0]["message_id"]

    corrupted = bytearray(fake_discord.store[mid])
    corrupted[10] ^= 0xFF
    fake_discord.store[mid] = bytes(corrupted)

    with pytest.raises(Exception) as caught:
        async with sftp.open("/blob.bin", "rb") as f:
            await f.read()
    assert "integrity" in str(caught.value).lower()


async def test_a_tampered_read_returns_no_data_at_all(sftp, fake_discord, fake_db):
    # Detecting corruption but handing the bytes over anyway would be no
    # better than not checking.
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    mid = _file_node(fake_db)["chunks"][0]["message_id"]
    corrupted = bytearray(fake_discord.store[mid])
    corrupted[0] ^= 0x01
    fake_discord.store[mid] = bytes(corrupted)

    with pytest.raises(Exception):
        async with sftp.open("/blob.bin", "rb") as f:
            await f.read(100)


async def test_truncating_the_stored_ciphertext_is_detected(sftp, fake_discord, fake_db):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    mid = _file_node(fake_db)["chunks"][0]["message_id"]
    fake_discord.store[mid] = fake_discord.store[mid][:-64]

    with pytest.raises(Exception):
        async with sftp.open("/blob.bin", "rb") as f:
            await f.read()


async def test_stripping_the_tag_fails_the_read(sftp, fake_db):
    # Fail closed, end to end: no tag means no read, not an unverified read.
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    node = _file_node(fake_db)
    for chunk in node["chunks"]:
        chunk.pop("hmac", None)

    with pytest.raises(Exception):
        async with sftp.open("/blob.bin", "rb") as f:
            await f.read()


async def test_swapping_two_chunks_ciphertext_is_detected(sftp, fake_discord, fake_db):
    # Each tag covers its own nonce, so a chunk moved to another slot fails
    # even though both ciphertexts are individually authentic.
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    chunks = _file_node(fake_db)["chunks"]
    a, b = chunks[0]["message_id"], chunks[1]["message_id"]
    fake_discord.store[a], fake_discord.store[b] = (
        fake_discord.store[b], fake_discord.store[a])

    with pytest.raises(Exception):
        async with sftp.open("/blob.bin", "rb") as f:
            await f.read()


async def test_a_later_chunk_is_verified_too(sftp, fake_discord, fake_db):
    # Guards against verifying only the first chunk a read touches.
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    chunks = _file_node(fake_db)["chunks"]
    mid = chunks[-1]["message_id"]
    corrupted = bytearray(fake_discord.store[mid])
    corrupted[5] ^= 0x80
    fake_discord.store[mid] = bytes(corrupted)

    with pytest.raises(Exception):
        async with sftp.open("/blob.bin", "rb") as f:
            await f.seek(chunks[-1]["offset"])
            await f.read(100)


async def test_an_untouched_chunk_still_reads_after_a_neighbour_is_corrupted(
        sftp, fake_discord, fake_db):
    # Corruption should be contained, not turn the whole file unreadable.
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    chunks = _file_node(fake_db)["chunks"]
    corrupted = bytearray(fake_discord.store[chunks[1]["message_id"]])
    corrupted[5] ^= 0x80
    fake_discord.store[chunks[1]["message_id"]] = bytes(corrupted)

    async with sftp.open("/blob.bin", "rb") as f:
        assert len(await f.read(1024)) == 1024


# ------------------------------------------------- interaction with rewrites


async def test_a_rewritten_chunk_gets_a_new_tag(sftp, fake_db):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    before = [c["hmac"] for c in _file_node(fake_db)["chunks"]]

    async with sftp.open("/blob.bin", "r+b") as f:
        await f.seek(100)
        await f.write(b"patched")

    after = [c["hmac"] for c in _file_node(fake_db)["chunks"]]
    assert after[0] != before[0]
    assert after[1:] == before[1:]


async def test_a_rewritten_file_still_verifies_on_read(sftp):
    # The tag has to be recomputed over the *new* ciphertext and nonce
    # together; getting either wrong makes the file unreadable afterwards.
    payload = bytearray(os.urandom(PAYLOAD_SIZE))
    await _write_blob(sftp, "/blob.bin", bytes(payload))

    patch = b"P" * 512
    payload[TEST_CHUNK_SIZE + 100:TEST_CHUNK_SIZE + 612] = patch
    async with sftp.open("/blob.bin", "r+b") as f:
        await f.seek(TEST_CHUNK_SIZE + 100)
        await f.write(patch)

    async with sftp.open("/blob.bin", "rb") as f:
        assert await f.read() == bytes(payload)


async def test_tampering_after_a_rewrite_is_still_detected(sftp, fake_discord, fake_db):
    await _write_blob(sftp, "/blob.bin", os.urandom(PAYLOAD_SIZE))
    async with sftp.open("/blob.bin", "r+b") as f:
        await f.seek(100)
        await f.write(b"patched")

    mid = _file_node(fake_db)["chunks"][0]["message_id"]
    corrupted = bytearray(fake_discord.store[mid])
    corrupted[20] ^= 0x0F
    fake_discord.store[mid] = bytes(corrupted)

    with pytest.raises(Exception):
        async with sftp.open("/blob.bin", "rb") as f:
            await f.read()


def test_ciphertext_length_is_unchanged_by_authentication():
    # The tag is metadata, so what Discord stores is still exactly the
    # plaintext length -- chunk sizing does not need to account for it.
    nonce, plain = os.urandom(16), os.urandom(4096)
    assert len(transform(KEY, nonce, plain)) == len(plain)
