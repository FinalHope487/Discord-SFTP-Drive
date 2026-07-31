"""AES-256-CTR encryption, chunk and file authentication, and the
password-wrapped master key.

CTR replaces the previous CBC stream for two reasons:

* ciphertext length == plaintext length, so no padding is needed and the
  size we record in MongoDB is exactly the size the user wrote;
* any byte range can be decrypted on its own, which is what SFTP needs --
  the protocol is offset-based, not sequential.

Each chunk carries its own random 16-byte nonce (stored in that chunk's
metadata). The nonce doubles as the initial counter block: seeking inside a
chunk just means advancing the counter by the block index.

Authentication
--------------
CTR provides confidentiality and nothing else: flipping a bit in the
ciphertext flips exactly that bit in the plaintext, undetectably. Two tags
sit on top of it, at different scopes:

* **`chunk_tag`** covers one chunk's bytes *and where that chunk belongs* --
  the file id, its index, its offset and its length. Covering the bytes alone
  left a chunk valid anywhere: it could be moved within a file, or into a
  different file, and still verify.
* **`node_tag`** covers the file as a whole -- its id, its length, and the
  ordered list of its chunk tags. A per-chunk tag cannot notice a chunk that
  is simply *gone*, because there is nothing left to check. That is what made
  it possible to delete a trailing chunk, or replace one with a hole, and have
  the file read back short or zero-filled with no error.

Both are deliberate about layout:

* **Encrypt-then-MAC.** The tag covers the ciphertext, so a forged chunk is
  rejected before the cipher ever touches it.
* **The tags live in MongoDB, not on Discord.** Discord stores the
  ciphertext; the tags sit with the metadata. Whoever can alter what Discord
  serves still cannot produce a matching tag. This is the main reason HMAC
  was chosen over AES-GCM, whose tag would travel with the ciphertext.
* **The nonce is covered too.** Tagging the ciphertext alone would let
  someone swap a chunk's stored nonce and change the decrypted output while
  the tag still verified.
* **Every MAC input is length-prefixed and domain-separated.** Plain
  concatenation lets two different structures produce the same byte string.

What is still not covered: rolling a whole file back to an earlier, internally
consistent version of *itself*. Detecting that needs a monotonic version
counter kept somewhere the holder of the database cannot reach, and there is
no such place in this design.

The master key
--------------
The key that encrypts data is random and never leaves memory in the clear.
It is stored wrapped: the SFTP password goes through PBKDF2 to make a
key-encryption key, and that wraps the master key with the same
encrypt-then-MAC construction used everywhere else. Three consequences,
all of them the point:

* the password can be changed by re-wrapping, without touching a single
  stored byte -- deriving the data key from the password directly would make
  a password change equivalent to destroying the data;
* a wrong password fails the wrap's MAC, so it is caught as a wrong password
  rather than as unreadable data;
* nothing in `.env` is sufficient to decrypt anything on its own.

The KDF name and its parameters are stored *with* the wrapped key rather than
compiled in, so the cost can be raised, or the algorithm replaced, without a
migration.
"""

import hmac
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

BLOCK_SIZE = 16
NONCE_SIZE = 16
MAC_SIZE = 32
KEY_SIZE = 32

_COUNTER_MODULO = 1 << (NONCE_SIZE * 8)

# Distinct labels so no derived key can collide with another. Versioned:
# changing what a tag covers changes the label, which changes every tag and
# makes the break loud instead of silent. v2 added the chunk's position.
_MAC_INFO = b"discord-drive/content-mac/v2"
_WRAP_ENC_INFO = b"discord-drive/key-wrap-enc/v1"
_WRAP_MAC_INFO = b"discord-drive/key-wrap-mac/v1"

KDF_PBKDF2_SHA256 = "pbkdf2-sha256"

# OWASP's floor for PBKDF2-HMAC-SHA256. Overridable because the stored record
# carries whatever was used; see `config.pbkdf2_iterations`.
DEFAULT_PBKDF2_ITERATIONS = 600_000


class IntegrityError(Exception):
    """A tag did not match, or there was no tag to check."""


class KeyUnwrapError(Exception):
    """The wrapped master key could not be opened with the given password."""


def _subkey(key: bytes, info: bytes) -> bytes:
    """An independent key for one purpose.

    Not cached. An earlier version memoised these in a module-level dict,
    which kept every session's derived keys alive for the life of the
    process -- the opposite of a key that exists only while its connection
    does. HKDF runs once per chunk, not per byte, so the cost is noise.
    """
    return HKDF(algorithm=hashes.SHA256(), length=KEY_SIZE, salt=None,
                info=info).derive(key)


def _length_prefixed(value: bytes) -> bytes:
    return len(value).to_bytes(8, "big") + value


# --------------------------------------------------------------- chunk tags


def chunk_tag(key: bytes, nonce: bytes, ciphertext: bytes, *, file_id: str,
              index: int, offset: int, size: int) -> bytes:
    """Authentication tag over a stored chunk and its place in the file."""
    mac = hmac.new(_subkey(key, _MAC_INFO), digestmod="sha256")
    mac.update(b"chunk")
    mac.update(_length_prefixed(file_id.encode("utf-8")))
    mac.update(index.to_bytes(8, "big"))
    mac.update(offset.to_bytes(8, "big"))
    mac.update(size.to_bytes(8, "big"))
    mac.update(nonce)
    mac.update(_length_prefixed(ciphertext))
    return mac.digest()


def verify_chunk(key: bytes, nonce: bytes, ciphertext: bytes, tag_hex: str, *,
                 file_id: str, index: int, offset: int, size: int):
    """Raise `IntegrityError` unless the tag matches.

    A missing tag is a failure, not a pass. Treating untagged chunks as
    acceptable would leave a downgrade path: strip the tag, and verification
    silently stops happening.
    """
    expected = _decode_tag(tag_hex, "chunk")
    actual = chunk_tag(key, nonce, ciphertext, file_id=file_id, index=index,
                       offset=offset, size=size)
    # Constant time: a byte-at-a-time comparison leaks how much of a forged
    # tag was correct, which is enough to construct one.
    if not hmac.compare_digest(expected, actual):
        raise IntegrityError("chunk failed integrity verification")


# ---------------------------------------------------------------- node tags


def node_tag(key: bytes, *, file_id: str, size: int, chunk_tags) -> bytes:
    """Authentication tag over a file's shape.

    Covers the ordered chunk tags rather than the chunk bytes, so it costs the
    same whatever the file's size, and any change to a chunk changes this too.
    """
    mac = hmac.new(_subkey(key, _MAC_INFO), digestmod="sha256")
    mac.update(b"node")
    mac.update(_length_prefixed(file_id.encode("utf-8")))
    mac.update(size.to_bytes(8, "big"))
    mac.update(len(chunk_tags).to_bytes(8, "big"))
    for tag in chunk_tags:
        mac.update(_length_prefixed(bytes.fromhex(tag)))
    return mac.digest()


def verify_node(key: bytes, *, file_id: str, size: int, chunk_tags,
                tag_hex: str):
    """Raise `IntegrityError` unless the file's shape is the one recorded."""
    expected = _decode_tag(tag_hex, "file")
    actual = node_tag(key, file_id=file_id, size=size, chunk_tags=chunk_tags)
    if not hmac.compare_digest(expected, actual):
        raise IntegrityError("file failed integrity verification")


def _decode_tag(tag_hex: str, what: str) -> bytes:
    if not tag_hex:
        raise IntegrityError(f"{what} has no integrity tag")
    try:
        return bytes.fromhex(tag_hex)
    except ValueError:
        raise IntegrityError(f"{what} integrity tag is malformed") from None


# ------------------------------------------------------------------- cipher


def generate_nonce() -> bytes:
    """A fresh initial counter block. Must never be reused with the same key."""
    return os.urandom(NONCE_SIZE)


def generate_master_key() -> bytes:
    return os.urandom(KEY_SIZE)


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


# ------------------------------------------------------------- key wrapping


def derive_kek(password: str, salt: bytes, *, kdf: str, iterations: int) -> bytes:
    """The key-encryption key for a password.

    Deliberately takes the algorithm name rather than assuming it: the stored
    record names what produced it, so an old record keeps opening after the
    default changes.
    """
    if kdf != KDF_PBKDF2_SHA256:
        raise KeyUnwrapError(f"unsupported key derivation function: {kdf!r}")
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=KEY_SIZE, salt=salt,
                      iterations=iterations).derive(password.encode("utf-8"))


def wrap_master_key(password: str, master_key: bytes, *,
                    iterations: int = DEFAULT_PBKDF2_ITERATIONS) -> dict:
    """Encrypt `master_key` under `password`, with everything needed to undo it.

    The salt is fresh on every call, so re-wrapping after a password change
    does not reuse the previous derivation.
    """
    if len(master_key) != KEY_SIZE:
        raise ValueError(f"master key must be {KEY_SIZE} bytes")

    salt = os.urandom(16)
    kek = derive_kek(password, salt, kdf=KDF_PBKDF2_SHA256, iterations=iterations)

    nonce = generate_nonce()
    ciphertext = transform(_subkey(kek, _WRAP_ENC_INFO), nonce, master_key)
    mac = hmac.new(_subkey(kek, _WRAP_MAC_INFO), digestmod="sha256")
    mac.update(nonce)
    mac.update(ciphertext)

    return {
        "kdf": KDF_PBKDF2_SHA256,
        "kdf_salt": salt.hex(),
        "kdf_iterations": iterations,
        "nonce": nonce.hex(),
        "ciphertext": ciphertext.hex(),
        "hmac": mac.digest().hex(),
    }


def unwrap_master_key(password: str, record: dict) -> bytes:
    """Recover the master key, or raise `KeyUnwrapError`.

    The MAC check *is* the password check. Without it a wrong password would
    yield 32 bytes of garbage that look exactly like a key, and every read
    would then fail as a corrupt file rather than as a bad password.
    """
    try:
        salt = bytes.fromhex(record["kdf_salt"])
        nonce = bytes.fromhex(record["nonce"])
        ciphertext = bytes.fromhex(record["ciphertext"])
        expected = bytes.fromhex(record["hmac"])
        iterations = int(record["kdf_iterations"])
        kdf = record["kdf"]
    except (KeyError, TypeError, ValueError) as exc:
        raise KeyUnwrapError(f"stored key record is malformed: {exc}") from exc

    kek = derive_kek(password, salt, kdf=kdf, iterations=iterations)

    mac = hmac.new(_subkey(kek, _WRAP_MAC_INFO), digestmod="sha256")
    mac.update(nonce)
    mac.update(ciphertext)
    if not hmac.compare_digest(expected, mac.digest()):
        raise KeyUnwrapError("password does not open the stored key")

    return transform(_subkey(kek, _WRAP_ENC_INFO), nonce, ciphertext)
