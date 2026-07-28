"""AES-256-CTR encryption plus per-chunk HMAC authentication.

CTR replaces the previous CBC stream for two reasons:

* ciphertext length == plaintext length, so no padding is needed and the
  size we record in MongoDB is exactly the size the user wrote;
* any byte range can be decrypted on its own, which is what SFTP needs —
  the protocol is offset-based, not sequential.

Each chunk carries its own random 16-byte nonce (stored in that chunk's
metadata). The nonce doubles as the initial counter block: seeking inside a
chunk just means advancing the counter by the block index.

CTR provides confidentiality and nothing else: flipping a bit in the
ciphertext flips exactly that bit in the plaintext, undetectably. Each chunk
therefore also carries an HMAC-SHA256 tag, and the layout is deliberate:

* **Encrypt-then-MAC.** The tag covers the ciphertext, so a forged chunk is
  rejected before the cipher ever touches it. MAC-then-encrypt would require
  decrypting attacker-controlled bytes first.
* **The tag lives in MongoDB, not on Discord.** Discord stores the
  ciphertext; the tag sits with the metadata. Whoever can alter what Discord
  serves still cannot produce a matching tag. This is the main reason HMAC
  was chosen over AES-GCM, whose tag would travel with the ciphertext.
* **The nonce is covered too.** Tagging the ciphertext alone would let
  someone swap a chunk's stored nonce and change the decrypted output while
  the tag still verified.
* **The MAC key is derived, not shared.** Using one key for both AES and
  HMAC is a reuse smell; HKDF splits the configured secret into two
  independent keys.
"""

import hmac
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

BLOCK_SIZE = 16
NONCE_SIZE = 16
MAC_SIZE = 32

_COUNTER_MODULO = 1 << (NONCE_SIZE * 8)

# Distinct label so the MAC key cannot collide with any other key derived
# from the same secret later. Versioned: changing the scheme means changing
# the label, which changes every tag and makes the break loud instead of
# silent.
_MAC_INFO = b"discord-drive/chunk-hmac/v1"

_mac_key_cache = {}


class IntegrityError(Exception):
    """A chunk's tag did not match, or there was no tag to check."""


def derive_mac_key(key: bytes) -> bytes:
    """Independent HMAC key from the configured secret.

    Cached because this runs on every chunk read and HKDF is not free.
    """
    cached = _mac_key_cache.get(key)
    if cached is None:
        cached = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=_MAC_INFO,
        ).derive(key)
        _mac_key_cache[key] = cached
    return cached


def chunk_tag(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    """Authentication tag over a stored chunk."""
    mac = hmac.new(derive_mac_key(key), digestmod="sha256")
    mac.update(nonce)
    mac.update(ciphertext)
    return mac.digest()


def verify_chunk(key: bytes, nonce: bytes, ciphertext: bytes, tag_hex: str):
    """Raise `IntegrityError` unless the tag matches.

    A missing tag is a failure, not a pass. Treating untagged chunks as
    acceptable would leave a downgrade path: strip the tag, and verification
    silently stops happening.
    """
    if not tag_hex:
        raise IntegrityError("chunk has no integrity tag")
    try:
        expected = bytes.fromhex(tag_hex)
    except ValueError:
        raise IntegrityError("chunk integrity tag is malformed") from None

    # Constant time: a byte-at-a-time comparison leaks how much of a forged
    # tag was correct, which is enough to construct one.
    if not hmac.compare_digest(expected, chunk_tag(key, nonce, ciphertext)):
        raise IntegrityError("chunk failed integrity verification")


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
