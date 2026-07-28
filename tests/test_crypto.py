"""AES-256-CTR properties the storage layer relies on."""

import os

import pytest

from src.crypto import BLOCK_SIZE, generate_nonce, transform

KEY = b"k" * 32

# Deliberately not a multiple of the block size: the previous CBC
# implementation had no PKCS7 padding at all, so every file whose length was
# not a multiple of 16 -- the overwhelming majority -- failed at finalize().
PLAINTEXT_SIZE = 5000


@pytest.fixture
def sample():
    nonce = generate_nonce()
    plain = os.urandom(PLAINTEXT_SIZE)
    return nonce, plain, transform(KEY, nonce, plain)


def test_ciphertext_length_equals_plaintext_length(sample):
    # This is what lets the recorded `size` be exact rather than padded.
    _, plain, ct = sample
    assert len(ct) == len(plain)


def test_full_round_trip(sample):
    nonce, plain, ct = sample
    assert transform(KEY, nonce, ct) == plain


@pytest.mark.parametrize(
    "offset",
    [0, 1, BLOCK_SIZE - 1, BLOCK_SIZE, BLOCK_SIZE + 1, 4096, PLAINTEXT_SIZE - 1],
    ids=lambda o: f"offset{o}",
)
def test_decrypt_from_arbitrary_offset(sample, offset):
    # SFTP reads are offset-based, so decrypting a tail slice on its own has
    # to work -- including offsets that do not land on a block boundary.
    nonce, plain, ct = sample
    assert transform(KEY, nonce, ct[offset:], offset=offset) == plain[offset:]


def test_empty_input_is_a_no_op():
    assert transform(KEY, generate_nonce(), b"") == b""


def test_nonces_are_not_reused():
    # Counter-mode key reuse across chunks with the same nonce would leak
    # plaintext by XOR, so this is a correctness property, not a nicety.
    assert len({generate_nonce() for _ in range(100)}) == 100
