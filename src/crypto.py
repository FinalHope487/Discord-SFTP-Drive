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
It is stored wrapped: the SFTP password goes through a memory-hard KDF to make
a key-encryption key, and that wraps the master key with the same
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
migration. That is not a hypothetical: new wraps use Argon2id, and the records
written earlier under PBKDF2-HMAC-SHA256 still open, because each one says
which function made it and at what cost. Argon2id is the default because the
password is the only secret outside the database, and PBKDF2's work is pure
arithmetic that a GPU runs thousands of at a time, while Argon2id's cost is
memory that a GPU cannot parallelise away.
"""

import hmac
import os
import unicodedata

from argon2.low_level import Type as Argon2Type
from argon2.low_level import hash_secret_raw
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
KDF_ARGON2ID = "argon2id"

# What a *new* wrap uses. Reading is unaffected: every stored record names the
# function that produced it, so the ones written under PBKDF2 keep opening.
DEFAULT_KDF = KDF_ARGON2ID

# OWASP's floor for PBKDF2-HMAC-SHA256. Overridable because the stored record
# carries whatever was used; see `config.kdf_settings`.
DEFAULT_PBKDF2_ITERATIONS = 600_000

# Comfortably above OWASP's Argon2id floor (19 MiB / t=2 / p=1). The cost that
# matters here is memory, which is the whole point: PBKDF2 at 600k iterations
# is roughly 200ms of pure arithmetic and a GPU runs thousands of those at
# once, whereas 64 MiB per guess is what a GPU cannot parallelise its way out
# of. Wall time lands in the same order as the PBKDF2 it replaces, so the
# login path does not get slower.
#
# p=1 rather than the core count: parallelism has to be recorded and matched
# exactly on the way back, so tying it to whatever CPU happened to wrap the key
# would be a portability trap for no security gain at this memory size.
DEFAULT_ARGON2_TIME_COST = 3
DEFAULT_ARGON2_MEMORY_KIB = 64 * 1024
DEFAULT_ARGON2_PARALLELISM = 1


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


def _name(value: str) -> bytes:
    """A filename as the tag sees it: NFC-normalised UTF-8.

    Without this, the same name typed on macOS (which hands over NFD) and on
    Linux (NFC) produces two different tags, and one of the two clients gets
    a file that reads back as corrupt. That failure would look like tampering
    and only appear on one platform, which is the worst way to learn about a
    normalisation bug.
    """
    return unicodedata.normalize("NFC", value or "").encode("utf-8")


def node_tag(key: bytes, *, file_id: str, parent_id: str, filename: str,
             size: int, chunk_tags) -> bytes:
    """Authentication tag over a file: its shape, its name and its place.

    Covers the ordered chunk tags rather than the chunk bytes, so it costs the
    same whatever the file's size, and any change to a chunk changes this too.

    `parent_id` and `filename` are in here because contents alone were not
    enough. Whoever could write to MongoDB could rename a file, or move it to
    another directory, and every check still passed -- so the bytes of
    `report-2024.pdf` could be served under the name `report-2026.pdf` with a
    valid tag on them. Authenticating content while leaving identity
    unauthenticated is a guarantee that misleads.
    """
    mac = hmac.new(_subkey(key, _MAC_INFO), digestmod="sha256")
    mac.update(b"node2")
    mac.update(_length_prefixed(file_id.encode("utf-8")))
    mac.update(_length_prefixed((parent_id or "").encode("utf-8")))
    mac.update(_length_prefixed(_name(filename)))
    mac.update(size.to_bytes(8, "big"))
    mac.update(len(chunk_tags).to_bytes(8, "big"))
    for tag in chunk_tags:
        mac.update(_length_prefixed(bytes.fromhex(tag)))
    return mac.digest()


def verify_node(key: bytes, *, file_id: str, parent_id: str, filename: str,
                size: int, chunk_tags, tag_hex: str):
    """Raise `IntegrityError` unless the file's shape is the one recorded."""
    expected = _decode_tag(tag_hex, "file")
    actual = node_tag(key, file_id=file_id, parent_id=parent_id,
                      filename=filename, size=size, chunk_tags=chunk_tags)
    if not hmac.compare_digest(expected, actual):
        raise IntegrityError("file failed integrity verification")


# ----------------------------------------------------------- directory tags
#
# Two tags, deliberately separate, because they cost very different amounts
# to check.
#
# `dir_tag` covers a directory's own identity. Path resolution walks one
# segment at a time and checks this on every segment, so it has to be O(1) --
# it reads nothing but the directory's own document.
#
# `dir_entries_tag` covers the set of children. Checking it means listing
# them, so it is checked only when something is listing them anyway. Putting
# it on the traversal path would turn `/a/b/c/x` into three full directory
# listings per open.


def dir_tag(key: bytes, *, dir_id: str, parent_id: str, filename: str) -> bytes:
    """Authentication tag over a directory's identity and place.

    Renaming a directory used to change nothing a child could notice: a child
    records its parent's *id*, not its name, so moving `/private` to `/public`
    left every tag underneath it valid.
    """
    mac = hmac.new(_subkey(key, _MAC_INFO), digestmod="sha256")
    mac.update(b"dir")
    mac.update(_length_prefixed(dir_id.encode("utf-8")))
    mac.update(_length_prefixed((parent_id or "").encode("utf-8")))
    mac.update(_length_prefixed(_name(filename)))
    return mac.digest()


def verify_dir(key: bytes, *, dir_id: str, parent_id: str, filename: str,
               tag_hex: str):
    """Raise `IntegrityError` unless the directory is where it says it is."""
    expected = _decode_tag(tag_hex, "directory")
    actual = dir_tag(key, dir_id=dir_id, parent_id=parent_id, filename=filename)
    if not hmac.compare_digest(expected, actual):
        raise IntegrityError("directory failed integrity verification")


def dir_entries_tag(key: bytes, *, dir_id: str, entries) -> bytes:
    """Authentication tag over the set of names a directory contains.

    `entries` is any iterable of `(child_id, child_filename)`. Sorted here so
    the tag depends on the set and not on what order MongoDB handed it back.

    Deliberately *not* covering each child's own tag: that would make every
    write to any file rewrite its directory's tag, and computing it would mean
    reading all its siblings. Content is the child's own business; this covers
    membership.

    What this buys, precisely, is detecting a *deleted* entry. A renamed or
    moved one is already caught by the child's own tag, and a forged one
    cannot be produced without the key. It does not catch a child being
    restored from an older copy of both documents -- that is whole-file
    rollback, which this project has accepted as a residual risk.
    """
    mac = hmac.new(_subkey(key, _MAC_INFO), digestmod="sha256")
    mac.update(b"dirents")
    mac.update(_length_prefixed(dir_id.encode("utf-8")))
    items = sorted((child_id, _name(name)) for child_id, name in entries)
    mac.update(len(items).to_bytes(8, "big"))
    for child_id, name in items:
        mac.update(_length_prefixed(child_id.encode("utf-8")))
        mac.update(_length_prefixed(name))
    return mac.digest()


def verify_dir_entries(key: bytes, *, dir_id: str, entries, tag_hex: str,
                       pending_hex: str = None):
    """Raise `IntegrityError` unless the directory holds exactly these entries.

    Two tags are accepted, and that is the point. A directory's tag and its
    children live in different documents, so a structural change cannot write
    both at once; the process could die between them. The writer therefore
    stores the *next* tag as `pending` first, makes the change, and only then
    promotes it. Whichever side of the crash the reader arrives on, one of the
    two matches the children that are actually there.

    It does not weaken anything: both values are produced by code holding the
    key, so an attacker still cannot make a set of their choosing verify.
    """
    actual = dir_entries_tag(key, dir_id=dir_id, entries=entries)
    for candidate in (tag_hex, pending_hex):
        if candidate and hmac.compare_digest(_decode_tag(candidate, "directory"),
                                             actual):
            return
    if not tag_hex and not pending_hex:
        raise IntegrityError("directory has no entry integrity tag")
    raise IntegrityError("directory entries failed integrity verification")


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


# Which cost parameters each function takes, and what each one is called in
# the stored record. Driving both directions off one table is what keeps a
# parameter from being written under one name and read back under another --
# which would not fail loudly, it would just derive a different key.
_KDF_PARAMS = {
    KDF_PBKDF2_SHA256: {"iterations": "kdf_iterations"},
    KDF_ARGON2ID: {
        "time_cost": "kdf_time_cost",
        "memory_kib": "kdf_memory_kib",
        "parallelism": "kdf_parallelism",
    },
}

_KDF_DEFAULTS = {
    KDF_PBKDF2_SHA256: {"iterations": DEFAULT_PBKDF2_ITERATIONS},
    KDF_ARGON2ID: {
        "time_cost": DEFAULT_ARGON2_TIME_COST,
        "memory_kib": DEFAULT_ARGON2_MEMORY_KIB,
        "parallelism": DEFAULT_ARGON2_PARALLELISM,
    },
}


def kdf_settings(kdf: str = DEFAULT_KDF, **overrides) -> dict:
    """A complete, validated description of one derivation.

    The shape everything else passes around: `{"kdf": name, ...costs}`, with
    anything unspecified filled in from that function's defaults. Overrides
    belonging to a different function are ignored rather than rejected, so a
    deployment can keep both sets of variables in its environment and switch
    between them by changing one name.
    """
    if kdf not in _KDF_PARAMS:
        raise KeyUnwrapError(f"unsupported key derivation function: {kdf!r}")

    settings = {"kdf": kdf}
    for name, default in _KDF_DEFAULTS[kdf].items():
        value = overrides.get(name)
        settings[name] = default if value is None else int(value)
    return settings


def _settings_to_record(settings: dict) -> dict:
    """The stored spelling of a settings dict."""
    fields = _KDF_PARAMS[settings["kdf"]]
    record = {"kdf": settings["kdf"]}
    record.update({stored: settings[name] for name, stored in fields.items()})
    return record


def _settings_from_record(record: dict) -> dict:
    """The settings a stored record describes, or `KeyUnwrapError`.

    Every parameter is required. Falling back to a default for a missing one
    would mean a record silently opening under costs it was not made with,
    which cannot work -- the derivation would produce a different key and the
    failure would surface as a wrong password.
    """
    kdf = record.get("kdf")
    if kdf not in _KDF_PARAMS:
        raise KeyUnwrapError(f"unsupported key derivation function: {kdf!r}")

    settings = {"kdf": kdf}
    for name, stored in _KDF_PARAMS[kdf].items():
        if stored not in record:
            raise KeyUnwrapError(
                f"stored key record is missing {stored!r}, which {kdf} needs")
        try:
            settings[name] = int(record[stored])
        except (TypeError, ValueError) as exc:
            raise KeyUnwrapError(
                f"stored key record has a malformed {stored!r}: {exc}") from exc
    return settings


def record_matches_settings(record: dict, settings: dict) -> bool:
    """Whether a stored record was made with exactly these settings.

    A record naming an unknown function is simply "no match" rather than an
    error: the caller asking this is deciding whether to rewrite it, and a
    record nobody can read is the strongest possible case for rewriting.
    """
    try:
        return _settings_from_record(record) == settings
    except KeyUnwrapError:
        return False


def derive_kek(password: str, salt: bytes, settings: dict) -> bytes:
    """The key-encryption key for a password.

    Deliberately takes the algorithm name rather than assuming it: the stored
    record names what produced it, so an old record keeps opening after the
    default changes.
    """
    kdf = settings.get("kdf")
    secret = password.encode("utf-8")

    if kdf == KDF_PBKDF2_SHA256:
        return PBKDF2HMAC(algorithm=hashes.SHA256(), length=KEY_SIZE, salt=salt,
                          iterations=settings["iterations"]).derive(secret)

    if kdf == KDF_ARGON2ID:
        # Type.ID, not Type.I or Type.D: the hybrid is the one to use unless
        # there is a specific reason otherwise, and it is what the name in the
        # record commits to. Reading the type from the record rather than the
        # constant would let someone downgrade a stored record to Argon2i.
        return hash_secret_raw(
            secret=secret,
            salt=salt,
            time_cost=settings["time_cost"],
            memory_cost=settings["memory_kib"],
            parallelism=settings["parallelism"],
            hash_len=KEY_SIZE,
            type=Argon2Type.ID,
        )

    raise KeyUnwrapError(f"unsupported key derivation function: {kdf!r}")


def wrap_master_key(password: str, master_key: bytes, *,
                    settings: dict = None) -> dict:
    """Encrypt `master_key` under `password`, with everything needed to undo it.

    The salt is fresh on every call, so re-wrapping after a password change
    does not reuse the previous derivation.
    """
    if len(master_key) != KEY_SIZE:
        raise ValueError(f"master key must be {KEY_SIZE} bytes")

    settings = kdf_settings() if settings is None else settings

    salt = os.urandom(16)
    kek = derive_kek(password, salt, settings)

    nonce = generate_nonce()
    ciphertext = transform(_subkey(kek, _WRAP_ENC_INFO), nonce, master_key)
    mac = hmac.new(_subkey(kek, _WRAP_MAC_INFO), digestmod="sha256")
    mac.update(nonce)
    mac.update(ciphertext)

    return {
        **_settings_to_record(settings),
        "kdf_salt": salt.hex(),
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
    settings = _settings_from_record(record)
    try:
        salt = bytes.fromhex(record["kdf_salt"])
        nonce = bytes.fromhex(record["nonce"])
        ciphertext = bytes.fromhex(record["ciphertext"])
        expected = bytes.fromhex(record["hmac"])
    except (KeyError, TypeError, ValueError) as exc:
        raise KeyUnwrapError(f"stored key record is malformed: {exc}") from exc

    kek = derive_kek(password, salt, settings)

    mac = hmac.new(_subkey(kek, _WRAP_MAC_INFO), digestmod="sha256")
    mac.update(nonce)
    mac.update(ciphertext)
    if not hmac.compare_digest(expected, mac.digest()):
        raise KeyUnwrapError("password does not open the stored key")

    return transform(_subkey(kek, _WRAP_ENC_INFO), nonce, ciphertext)
