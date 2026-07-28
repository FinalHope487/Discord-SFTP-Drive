"""AES-256-CTR helpers.

CTR replaces the previous CBC stream for two reasons:

* ciphertext length == plaintext length, so no padding is needed and the
  size we record in MongoDB is exactly the size the user wrote;
* any byte range can be decrypted on its own, which is what SFTP needs —
  the protocol is offset-based, not sequential.

Each chunk carries its own random 16-byte nonce (stored in that chunk's
metadata). The nonce doubles as the initial counter block: seeking inside a
chunk just means advancing the counter by the block index.
"""

import os

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

BLOCK_SIZE = 16
NONCE_SIZE = 16

_COUNTER_MODULO = 1 << (NONCE_SIZE * 8)


def generate_nonce() -> bytes:
    """A fresh initial counter block. Must never be reused with the same key."""
    return os.urandom(NONCE_SIZE)


def _counter_block(nonce: bytes, block_index: int) -> bytes:
    value = (int.from_bytes(nonce, "big") + block_index) % _COUNTER_MODULO
    return value.to_bytes(NONCE_SIZE, "big")


def transform(key: bytes, nonce: bytes, data: bytes, offset: int = 0) -> bytes:
    """Encrypt or decrypt `data`, which begins `offset` bytes into the chunk.

    CTR is its own inverse, so one function covers both directions.
    """
    if not data:
        return b""

    block_index, skew = divmod(offset, BLOCK_SIZE)
    cipher = Cipher(algorithms.AES(key), modes.CTR(_counter_block(nonce, block_index)))
    ctx = cipher.encryptor()

    # Feed `skew` filler bytes first so the keystream lines up with `offset`,
    # then drop the corresponding output.
    return ctx.update(bytes(skew) + data)[skew:]
