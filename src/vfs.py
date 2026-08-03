"""Async virtual filesystem: MongoDB holds the metadata, Discord holds the bytes.

Everything here is async on purpose. The previous version implemented
PyFilesystem2's synchronous `fs.FS` interface and bridged back to asyncio with
a per-call `asyncio.run()`. That could not work: Motor and aiohttp bind their
objects to the loop that created them, and `asyncio.run()` builds a throwaway
loop on every call. asyncssh is async end to end, so the VFS is too and the
bridge is gone.

Node schema (collection `nodes`):

    id          str   uuid4, or the literal "root"
    parent_id   str   parent's id; None only for root
    filename    str   single path segment; "" for root
    is_dir      bool
    size        int   plaintext length (0 for directories); see "holes"
    created_at  int   unix epoch seconds
    modified_at int   unix epoch seconds
    chunks      list  files only, ordered by `index`:
        index       int   position in the chunk sequence
        message_id  str   Discord message holding the attachment
        nonce       str   hex, this chunk's AES-CTR initial counter block
        hmac        str   hex, tag over this chunk's bytes and its position
        offset      int   plaintext offset of this chunk within the file
        size        int   plaintext length of this chunk
    mac         str   files only; hex, tag over (id, size, ordered chunk tags)

Both tags are required, and both live here rather than alongside the
ciphertext on Discord on purpose: that is what stops whoever serves the bytes
from also producing a tag for them. Anything missing one is rejected rather
than read unverified.

They cover different failures. A chunk tag says "these bytes belong at this
offset in this file"; it cannot notice a chunk that is simply absent, because
there is nothing left to check. `mac` covers the file's shape, so deleting a
trailing chunk or replacing one with a hole fails too -- see `crypto`.

`mac` is checked on single-node lookups (`stat`, `open`, `rename`, `remove`),
not while listing a directory. Listing is bulk metadata, and one tampered file
should not make its whole directory unlistable; anything that actually reads
or opens the file goes through a checked path.

Holes
-----
Chunks are contiguous from offset 0, but `size` may run past the end of the
last one. The stretch in between is a hole: it holds no attachment and reads
back as zeros. Only the tail can be a hole, which is exactly the shape a
grow-`truncate` produces; a write landing past the end of the chunks
materialises the gap instead, because a hole in the middle has no
representation here.

Not storing those zeros is what keeps pre-allocating clients usable. Some
set the final size before uploading a byte; materialising the zeros would
put real chunks under the whole file, so every subsequent write would land
mid-file and rewrite a full chunk *per SFTP packet* -- hundreds of 9MB
re-uploads for one chunk. With the hole, those writes still land at the end
of the data and take the ordinary buffered append path.
"""

import asyncio
import bisect
import logging
import time
import uuid
from collections import OrderedDict
from typing import Optional

from src.config import CHUNK_CACHE_SIZE, MAX_CHUNK_SIZE
from src.crypto import (
    IntegrityError,
    chunk_tag,
    dir_entries_tag,
    dir_tag,
    generate_nonce,
    node_tag,
    transform,
    verify_chunk,
    verify_dir,
    verify_dir_entries,
    verify_node,
)
from src.db import db
from src.discord_api import discord_api

logger = logging.getLogger(__name__)

# The root of the account that `SFTP_USER` / `SFTP_PASSWORD` describes. Not
# "the" root any more: a tree belongs to an account, and `DiscordVFS` is told
# which one it is serving. This constant is only the id the single pre-existing
# account keeps, so that making roots per-account needed no migration -- the
# tag over a directory covers its id, and leaving that id alone left every tag
# under it valid.
ROOT_ID = "root"

# What shape of tag this code writes and is willing to read. Stored on every
# node so that "made by an older version" and "tampered with" are different
# errors -- they are the two things that must never be confused, since one is
# an upgrade step and the other is an attack.
#
# It also makes the next change to what the tags cover cheap to detect, the
# way the `kdf` field on a key record made the move to Argon2id need no
# migration at all.
#
# Version 1 covered (file id, size, chunk tags) and gave directories no tag.
# Version 2 adds the parent and the filename, tags directories, and tags the
# set of names a directory holds. There is no compatibility path: refusing an
# old record outright is the same fail-closed rule the chunk tags follow.
TAG_VERSION = 3

# node id -> the mac last committed for it, kept for the lifetime of the
# process. Lets an open handle tell whether another handle changed the node
# without a database round trip: a matching mac means nothing to do, and only
# a mismatch pays for a refetch. Not a source of truth -- every refetch still
# re-verifies the tag against Mongo -- so a process restart clearing it is
# harmless.
#
# Deliberately unbounded rather than an LRU. A missing entry reads as "nothing
# is known to have changed", so evicting one would quietly put the handle that
# owns it back to serving its stale copy -- the exact bug this exists to close,
# reintroduced under memory pressure and impossible to reproduce on demand. The
# cost of keeping every entry is about 100 bytes per node ever touched.
_node_versions: dict = {}


class VFSError(Exception):
    """Base class for everything the SFTP layer translates into status codes."""


class NotFound(VFSError):
    pass


class NotADirectory(VFSError):
    pass


class IsADirectory(VFSError):
    pass


class AlreadyExists(VFSError):
    pass


class NotEmpty(VFSError):
    pass


class Unsupported(VFSError):
    pass


def normalize_path(path: str) -> str:
    """Collapse a client-supplied path into an absolute, `..`-free form.

    Clients send `.`, relative paths and `..` freely; resolving them here is
    what keeps every lookup anchored at the VFS root instead of leaking to the
    host filesystem.
    """
    parts = []
    for part in path.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/" + "/".join(parts)


def split_path(path: str):
    """Return (parent_path, name) for an already-normalized path."""
    normalized = normalize_path(path)
    if normalized == "/":
        return None, ""
    parent, _, name = normalized.rpartition("/")
    return parent or "/", name


def _join(parent_path: str, name: str) -> str:
    """The absolute path of `name` inside an already-normalized parent."""
    return f"/{name}" if parent_path == "/" else f"{parent_path}/{name}"


def _now() -> int:
    return int(time.time())


# Mode bits without the file-type bits, which are the server's to decide.
PERMISSION_MASK = 0o7777
DEFAULT_FILE_MODE = 0o644
DEFAULT_DIR_MODE = 0o755


async def _apply_metadata(node: dict, *, permissions=None, mtime=None,
                          atime=None):
    """Persist the POSIX metadata this filesystem models.

    Deliberately outside the file tag. That tag authenticates contents, and a
    mode or timestamp is not content; covering them would mean every `chmod`
    rewrote an integrity tag over bytes it did not touch. Someone who can
    rewrite the database can still change these -- the same limit as the
    recorded rollback gap.
    """
    update = {}
    if permissions is not None:
        update["permissions"] = int(permissions) & PERMISSION_MASK
    if mtime is not None:
        update["modified_at"] = int(mtime)
    if atime is not None:
        update["accessed_at"] = int(atime)
    if not update:
        return

    node.update(update)
    await db.get_db().nodes.update_one({"id": node["id"]}, {"$set": update})


class DiscordFile:
    """An open file handle.

    Reads are true random access: the chunk covering the requested offset is
    fetched and decrypted on its own.

    Writes come in two shapes. A write landing exactly at end-of-file is an
    append: it accumulates in a buffer and uploads a chunk once there is a
    full one, which is the path every ordinary upload takes. A write landing
    anywhere else rewrites the chunks it overlaps — Discord attachments are
    immutable, so "modifying" a chunk means uploading a replacement and
    dropping the old message.
    """

    def __init__(self, vfs: "DiscordVFS", node: dict, *, readable: bool,
                 writable: bool, append: bool = False):
        self._vfs = vfs
        self._key = vfs.key
        self._node = node
        self._readable = readable
        self._writable = writable
        self._append_mode = append

        self._buffer = bytearray()
        self._chunk_cache: "OrderedDict[int, bytes]" = OrderedDict()
        self._lock = asyncio.Lock()
        self._closed = False
        self._failed = False

        # An mtime the client asked for on this handle, still unspent. See
        # `_effective_mtime`.
        self._pinned_mtime = None

        self._reindex()

    @property
    def node(self) -> dict:
        return self._node

    @property
    def size(self) -> int:
        """The file's length as this handle sees it.

        Buffered bytes are part of the file from the client's point of view
        even though no chunk holds them yet, so the committed size would tell
        a client that its own last write never happened. asyncssh asks for
        exactly this before a length-less `read()`, and answers such a client
        with EOF.

        Once the handle has failed they stop counting. They are never going to
        land -- every write path refuses now -- so including them would report
        a length made partly of bytes that do not exist anywhere.
        """
        if self._failed:
            return self._node["size"]
        return self._end_of_file()

    def _reindex(self):
        self._chunks = sorted(self._node.get("chunks", []), key=lambda c: c["offset"])
        self._chunk_starts = [c["offset"] for c in self._chunks]

    def _covered_end(self) -> int:
        """Offset just past the last chunk — where real data stops.

        Not the same as the file's size once a grow-`truncate` has left a
        hole at the tail.
        """
        return _covered_end(self._node)

    def _end_of_file(self) -> int:
        """The file's logical end, buffered bytes included."""
        return max(self._node["size"], self._covered_end() + len(self._buffer))

    async def refresh(self):
        """Pull this handle up to date if another handle changed the node.

        Public for `SFTPServer.fstat`, which otherwise reads `.size` off a
        handle that may not have touched the node since it was opened.
        """
        async with self._lock:
            await self._sync()

    async def _sync(self):
        """The mismatch check behind `refresh` -- call with `_lock` held.

        Comparing against `_node_versions` is a dict lookup, not a database
        call, so an idle node costs nothing here. Only a real mismatch --
        another handle actually committed a change -- pays for a refetch.
        """
        current = _node_versions.get(self._node["id"])
        if current is None or current == self._node.get("mac"):
            return

        latest = await db.get_db().nodes.find_one({"id": self._node["id"]})
        if latest is None:
            raise NotFound(self._node["id"])
        _verify_node(self._key, latest)

        self._node["size"] = latest["size"]
        self._node["chunks"] = latest["chunks"]
        self._node["mac"] = latest["mac"]
        self._node["modified_at"] = latest.get("modified_at")
        # The tag covers these now, so a rename by another connection moves
        # the mac. Copying them keeps this handle's next commit from
        # recomputing the tag against the name the file no longer has.
        self._node["parent_id"] = latest.get("parent_id")
        self._node["filename"] = latest.get("filename")
        # Indices are reused as chunks are trimmed and replaced, so a cached
        # entry keyed by index could now hand back a different chunk's bytes.
        self._chunk_cache.clear()
        self._reindex()

    # ---------------------------------------------------------------- reading

    async def read_at(self, offset: int, size: int) -> bytes:
        if not self._readable:
            raise Unsupported("file is not open for reading")

        async with self._lock:
            await self._sync()

            # Anything still buffered has not been uploaded yet, so a reader
            # would silently miss it. Flushing keeps read-after-write honest.
            #
            # Not on a failed handle. Every write path has already refused,
            # and the client has already been told its write failed; if
            # Discord recovered in the meantime this would quietly upload and
            # commit those bytes anyway, resurrecting a write that was
            # reported as lost. Reads of what did commit still work.
            if self._buffer and not self._failed:
                await self._flush_buffer()

            out = bytearray()
            pos = offset
            remaining = size

            while remaining > 0:
                if pos < 0 or pos >= self._node["size"]:
                    break

                chunk = self._locate_chunk(pos)
                if chunk is None:
                    # Inside the file but past the last chunk: a hole. POSIX
                    # says it reads back as zeros, and there is nothing on
                    # Discord to fetch.
                    take = min(remaining, self._node["size"] - pos)
                    out += bytes(take)
                    pos += take
                    remaining -= take
                    continue

                data = await self._chunk_bytes(chunk)
                start = pos - chunk["offset"]
                if start >= len(data):
                    break

                take = min(remaining, len(data) - start)
                out += data[start:start + take]
                pos += take
                remaining -= take

            return bytes(out)

    def _locate_chunk(self, pos: int) -> Optional[dict]:
        if pos < 0 or not self._chunks:
            return None
        index = bisect.bisect_right(self._chunk_starts, pos) - 1
        if index < 0:
            return None
        chunk = self._chunks[index]
        if pos >= chunk["offset"] + chunk["size"]:
            return None
        return chunk

    async def _chunk_bytes(self, chunk: dict) -> bytes:
        key = chunk["index"]
        cached = self._chunk_cache.get(key)
        if cached is not None:
            self._chunk_cache.move_to_end(key)
            return cached

        plaintext = await _fetch_chunk(self._key, self._node["id"], chunk)

        self._chunk_cache[key] = plaintext
        self._chunk_cache.move_to_end(key)
        while len(self._chunk_cache) > CHUNK_CACHE_SIZE:
            self._chunk_cache.popitem(last=False)

        return plaintext

    # ---------------------------------------------------------------- writing

    def _append_position(self) -> int:
        """Where an appending write would land.

        Buffered bytes are already part of the file from the client's point of
        view even though no chunk holds them yet, so this is the end of the
        chunks plus whatever is still in the buffer. On a file with no hole
        that is end-of-file; on one with a hole it is where the hole starts,
        and a write there fills the hole from the left rather than paying for
        a rewrite.
        """
        return self._covered_end() + len(self._buffer)

    async def write_at(self, offset: int, data: bytes) -> int:
        if not self._writable:
            raise Unsupported("file is not open for writing")

        async with self._lock:
            if self._failed:
                raise VFSError("file is in a failed state from an earlier error")
            if not data:
                return 0
            if offset < 0:
                raise Unsupported(f"negative write offset: {offset}")

            await self._sync()

            # A write after the client set a time is a genuine new
            # modification, so the requested time no longer applies.
            self._pinned_mtime = None

            # O_APPEND means the offset the client sends is advisory; POSIX
            # requires every write to go to the end regardless. That is the
            # end of the *file*, which a hole puts past the end of the data.
            if self._append_mode:
                offset = self._end_of_file()

            if offset == self._append_position():
                return await self._append(data)

            return await self._write_random(offset, data)

    async def _append(self, data: bytes) -> int:
        self._buffer += data
        while len(self._buffer) >= MAX_CHUNK_SIZE:
            await self._upload_chunk(bytes(self._buffer[:MAX_CHUNK_SIZE]))
            del self._buffer[:MAX_CHUNK_SIZE]
        return len(data)

    async def _write_random(self, offset: int, data: bytes) -> int:
        """Overwrite `data` into the middle of the file.

        Costly by nature: every chunk the range touches is downloaded,
        decrypted, patched, re-encrypted and re-uploaded. Sequential uploads
        never come through here, which is why the append path above stays a
        plain buffer.
        """
        # Buffered bytes belong to no chunk yet. Patching around them and then
        # flushing would put the stale copy back on top of the new one.
        await self._flush_buffer()

        # A write starting past the end of the data leaves a gap, which POSIX
        # says reads back as zeros. Only the tail can be a hole here, and this
        # write is about to put chunks beyond the gap, so materialise it.
        if offset > self._covered_end():
            await self._append_now(bytes(offset - self._covered_end()))

        view = memoryview(data)
        written = 0
        pos = offset

        while written < len(data) and pos < self._covered_end():
            chunk = self._locate_chunk(pos)
            if chunk is None:
                break
            start = pos - chunk["offset"]
            take = min(len(data) - written, chunk["size"] - start)

            patched = bytearray(await self._chunk_bytes(chunk))
            patched[start:start + take] = view[written:written + take]
            await self._replace_chunk(chunk, bytes(patched))

            written += take
            pos += take

        # Anything past the last existing chunk simply extends the file.
        if written < len(data):
            await self._append_now(bytes(view[written:]))

        return len(data)

    async def _append_now(self, data: bytes):
        """Append `data` as chunks immediately, leaving nothing buffered.

        `_write_random` reads `self._node["size"]` as it walks, so bytes that
        are only in the buffer would make it mis-locate the next chunk.
        """
        buf = memoryview(data)
        while buf:
            take = min(MAX_CHUNK_SIZE, len(buf))
            await self._upload_chunk(bytes(buf[:take]))
            buf = buf[take:]

    async def _replace_chunk(self, chunk: dict, plaintext: bytes):
        """Swap a chunk's contents, keeping its offset and length.

        The new nonce is not optional. AES-CTR encrypts by XOR-ing against a
        keystream fixed by (key, nonce); encrypting different plaintext under
        a nonce that has already been used would let anyone holding both
        ciphertexts XOR them together and recover both plaintexts.
        """
        nonce = generate_nonce()
        ciphertext = transform(self._key, nonce, plaintext)
        tag = chunk_tag(self._key, nonce, ciphertext, file_id=self._node["id"],
                        index=chunk["index"], offset=chunk["offset"],
                        size=len(plaintext))
        filename = f"{self._node['id']}_chunk_{chunk['index']}.bin"

        message_id, _url, _size = await discord_api.upload_chunk(ciphertext, filename)

        previous = (chunk["message_id"], chunk["nonce"], chunk.get("hmac"))
        chunk["message_id"] = message_id
        chunk["nonce"] = nonce.hex()
        chunk["hmac"] = tag.hex()

        try:
            await _commit_content(self._key, self._node, mtime=self._effective_mtime())
        except Exception:
            # All three move together: a chunk left with the new nonce and the
            # old tag would fail verification on every subsequent read.
            chunk["message_id"], chunk["nonce"], chunk["hmac"] = previous
            await _safe_delete_message(message_id)
            raise

        # Only now does nothing reference the old attachment. Deleting it
        # before the metadata write would turn a failed update into data loss;
        # doing it after can at worst leave an orphan.
        await _safe_delete_message(previous[0])

        self._chunk_cache[chunk["index"]] = plaintext
        self._chunk_cache.move_to_end(chunk["index"])
        self._reindex()

    async def _flush_buffer(self):
        if self._buffer:
            await self._upload_chunk(bytes(self._buffer))
            self._buffer.clear()

    async def _upload_chunk(self, data: bytes):
        if not data:
            return

        # New chunks land where the existing ones stop, which is not the file
        # size when a hole follows them.
        offset = self._covered_end()
        previous_size = self._node["size"]
        index = len(self._node["chunks"])

        nonce = generate_nonce()
        ciphertext = transform(self._key, nonce, data)
        tag = chunk_tag(self._key, nonce, ciphertext, file_id=self._node["id"],
                        index=index, offset=offset, size=len(data))
        filename = f"{self._node['id']}_chunk_{index}.bin"

        try:
            message_id, _url, _size = await discord_api.upload_chunk(ciphertext, filename)
        except Exception:
            await self._rollback()
            raise

        chunk = {
            "index": index,
            "message_id": message_id,
            "nonce": nonce.hex(),
            "hmac": tag.hex(),
            "offset": offset,
            "size": len(data),
        }
        self._node["chunks"].append(chunk)
        # Filling a hole from the left leaves the recorded size alone; only
        # writing past it makes the file longer.
        self._node["size"] = max(previous_size, offset + len(data))

        try:
            await _commit_content(self._key, self._node, mtime=self._effective_mtime())
        except Exception:
            # The attachment exists but nothing references it — drop it before
            # unwinding so it does not become an orphan.
            await _safe_delete_message(message_id)
            self._node["chunks"].pop()
            self._node["size"] = previous_size
            await self._rollback()
            raise

        self._reindex()

    async def _rollback(self):
        """Give up on this handle after a write failed part way through.

        A file this handle created is removed outright, attachments included:
        it had no contents before, so a partially uploaded one is worth
        nothing to anybody.

        **A file that already existed is left exactly as its last successful
        commit, and nothing is deleted.** That includes a file this handle
        truncated -- its previous contents were already gone, and committed
        gone, before the failure.

        There is nothing to reclaim here because both callers release their
        own attachment before unwinding: `_upload_chunk` either never got one
        (the upload itself raised) or deletes it and restores `chunks`/`size`
        before calling in. Everything still referenced by the node is
        committed, readable, and -- on a file opened for appending or random
        writing without O_TRUNC -- was never this handle's to discard.

        This method used to walk `chunks` unconditionally and zero the node,
        which on exactly that path deleted the file's entire pre-existing
        contents from Discord, unrecoverably, in response to a Discord outage.
        A new caller must therefore keep releasing its own attachment; this
        method will not do it.
        """
        if self._failed:
            return
        self._failed = True

        if not self._node.get("_created_by_handle"):
            return

        for chunk in self._node.get("chunks", []):
            await _safe_delete_message(chunk["message_id"])

        self._node["chunks"] = []
        self._node["size"] = 0

        try:
            await db.get_db().nodes.delete_one({"id": self._node["id"]})
            _node_versions.pop(self._node["id"], None)
        except Exception:
            logger.exception("Rollback bookkeeping failed for node %s", self._node["id"])

        self._reindex()

    # -------------------------------------------------------------- resizing

    def _effective_mtime(self):
        """The timestamp a content write on this handle should record.

        Normally "now". But small writes sit in the buffer until close, so a
        client that writes, sets the mtime, and closes -- which is exactly
        `put -p` -- would have that final flush stamp over the time it just
        asked for. The flush is finishing a write that already happened, not
        making a new one, so a pinned time wins.

        A further write *after* the request does make a new modification, and
        clears the pin.
        """
        return self._pinned_mtime

    async def set_metadata(self, **fields):
        """`fchmod` / `futimes` on this handle."""
        async with self._lock:
            await _apply_metadata(self._node, **fields)
            if fields.get("mtime") is not None:
                self._pinned_mtime = int(fields["mtime"])

    async def truncate_to(self, size: int):
        """`ftruncate` on this handle."""
        if not self._writable:
            raise Unsupported("file is not open for writing")

        async with self._lock:
            if self._failed:
                raise VFSError("file is in a failed state from an earlier error")

            await self._sync()

            # Buffered bytes are part of the file but belong to no chunk yet,
            # so a resize computed around them would either drop them or
            # place them past the new end.
            await self._flush_buffer()

            await _resize_node(self._key, self._node, size)

            # Indices are handed out as `len(chunks)` and so get reused once
            # the tail is dropped; a stale entry would then hand back another
            # chunk's plaintext. The trimmed chunk's entry is stale too.
            # Truncation is rare enough that dropping the lot is the cheap
            # way to be sure.
            self._chunk_cache.clear()
            self._reindex()

    # ---------------------------------------------------------------- closing

    async def close(self):
        async with self._lock:
            if self._closed:
                return
            self._closed = True

            if not self._writable or self._failed:
                return

            # Only matters if there is something to place: a stale
            # `_covered_end()` would land the final flush at the wrong offset.
            if self._buffer:
                await self._sync()

            # Just the flush. Closing is not a modification -- every path that
            # actually changes the file stamps `modified_at` itself -- and
            # stamping here would discard an mtime the client set through
            # fsetstat just before closing, which is exactly what `put -p`
            # does.
            await self._flush_buffer()


async def _safe_delete_message(message_id: str):
    try:
        await discord_api.delete_message(message_id)
    except Exception:
        logger.warning("Could not delete Discord message %s", message_id, exc_info=True)


def _covered_end(node: dict) -> int:
    """Offset just past the node's last chunk.

    Equal to the file's size unless a hole follows the chunks — see the
    module docstring.
    """
    chunks = node.get("chunks") or []
    if not chunks:
        return 0
    last = max(chunks, key=lambda c: c["offset"])
    return last["offset"] + last["size"]


def _chunk_tags(node: dict):
    """This file's chunk tags, in offset order -- the node tag's input."""
    return [c["hmac"] for c in sorted(node.get("chunks") or [],
                                      key=lambda c: c["offset"])]


def _content_update(key: bytes, node: dict, *, mtime: int = None) -> dict:
    """The `$set` for any change to a file's contents.

    `size`, `chunks` and `mac` always move together. A tag written a moment
    behind either of the other two is a file that fails to open.

    `mtime` exists for the case where the client already said what the time
    should be -- see `DiscordFile._effective_mtime`.
    """
    return {
        "size": node["size"],
        "chunks": node["chunks"],
        "mac": _file_mac(key, node),
        "tag_version": TAG_VERSION,
        "modified_at": _now() if mtime is None else int(mtime),
    }


def _file_mac(key: bytes, node: dict) -> str:
    """The tag for a file node as it currently stands."""
    return node_tag(key, file_id=node["id"], parent_id=node.get("parent_id") or "",
                    filename=node.get("filename") or "", size=node["size"],
                    chunk_tags=_chunk_tags(node),
                    trashed_at=node.get("trashed_at")).hex()


def _dir_mac(key: bytes, node: dict) -> str:
    """The identity tag for a directory node as it currently stands."""
    return dir_tag(key, dir_id=node["id"], parent_id=node.get("parent_id") or "",
                   filename=node.get("filename") or "",
                   trashed_at=node.get("trashed_at")).hex()


def _entries_of(children) -> list:
    return [(c["id"], c.get("filename") or "") for c in children]


def _verify_node(key: bytes, node):
    """Check a node's tag. Misses pass through; nothing else does.

    Directories are checked here too, but only for *identity* -- who they are
    and where they sit. Their entry set is checked in `list_dir`, because
    checking it means listing them and this runs once per path segment.
    """
    if node is None:
        return node

    if node.get("tag_version") != TAG_VERSION:
        raise IntegrityError(
            f"node {node.get('id')!r} carries tag version "
            f"{node.get('tag_version')!r}, but this server writes and accepts "
            f"only version {TAG_VERSION}")

    if node.get("is_dir"):
        verify_dir(key, dir_id=node["id"], parent_id=node.get("parent_id") or "",
                   filename=node.get("filename") or "", tag_hex=node.get("mac"),
                   trashed_at=node.get("trashed_at"))
        return node

    verify_node(key, file_id=node["id"], parent_id=node.get("parent_id") or "",
                filename=node.get("filename") or "", size=node.get("size", 0),
                chunk_tags=_chunk_tags(node), tag_hex=node.get("mac"),
                trashed_at=node.get("trashed_at"))
    # Every verified read is a trustworthy point-in-time truth, so it is safe
    # to stamp the version cache here regardless of who is asking.
    _node_versions[node["id"]] = node.get("mac")
    return node


async def _commit_content(key: bytes, node: dict, *, mtime: int = None) -> dict:
    """Write a node's content fields and record the new version.

    Every path that changes size/chunks/mac funnels through here so that a
    handle on the same node held open elsewhere can tell, without a database
    round trip, whether its cached copy is still current -- see
    `DiscordFile._sync`.
    """
    update = _content_update(key, node, mtime=mtime)
    await db.get_db().nodes.update_one({"id": node["id"]}, {"$set": update})
    node["mac"] = update["mac"]
    node["modified_at"] = update["modified_at"]
    _node_versions[node["id"]] = update["mac"]
    return update


async def _fetch_chunk(key: bytes, file_id: str, chunk: dict) -> bytes:
    """Download, authenticate and decrypt one chunk.

    Resolving the URL and fetching it are one call so that an expired
    signature is retried rather than surfacing here as a read failure.
    """
    ciphertext = await discord_api.download_attachment(chunk["message_id"])
    nonce = bytes.fromhex(chunk["nonce"])

    # Before decryption, not after: the whole point of encrypt-then-MAC is
    # that forged bytes never reach the cipher.
    verify_chunk(key, nonce, ciphertext, chunk.get("hmac"), file_id=file_id,
                 index=chunk["index"], offset=chunk["offset"],
                 size=chunk["size"])

    return transform(key, nonce, ciphertext)


async def _resize_node(key: bytes, node: dict, size: int):
    """Set a file's length to exactly `size`.

    Growing is free: the file's recorded length is authoritative and the
    stretch past the last chunk is a hole that reads back as zeros. Only
    shrinking has to touch Discord, and only for the single chunk the new end
    falls inside — attachments are immutable, so trimming one means uploading
    a shorter replacement under a fresh nonce.
    """
    if size < 0:
        # Not FX_OP_UNSUPPORTED: after this change the server *does* resize,
        # and reporting otherwise would have clients disable a feature that
        # works. This is a malformed request, not a missing capability.
        raise VFSError(f"negative size: {size}")

    chunks = node.get("chunks") or []
    keep = []
    drop = []
    straddler = None

    for chunk in sorted(chunks, key=lambda c: c["offset"]):
        if chunk["offset"] >= size:
            drop.append(chunk)
        elif chunk["offset"] + chunk["size"] > size:
            straddler = chunk
        else:
            keep.append(chunk)

    replacement_id = None
    if straddler is not None:
        plaintext = await _fetch_chunk(key, node["id"], straddler)
        trimmed = plaintext[:size - straddler["offset"]]

        nonce = generate_nonce()
        ciphertext = transform(key, nonce, trimmed)
        tag = chunk_tag(key, nonce, ciphertext, file_id=node["id"],
                        index=straddler["index"], offset=straddler["offset"],
                        size=len(trimmed))
        filename = f"{node['id']}_chunk_{straddler['index']}.bin"

        replacement_id, _url, _size = await discord_api.upload_chunk(ciphertext, filename)
        keep.append(dict(
            straddler,
            message_id=replacement_id,
            nonce=nonce.hex(),
            hmac=tag.hex(),
            size=len(trimmed),
        ))

    previous_chunks, previous_size = node["chunks"], node["size"]
    node["chunks"], node["size"] = keep, size

    try:
        await _commit_content(key, node)
    except Exception:
        node["chunks"], node["size"] = previous_chunks, previous_size
        # Nothing points at the replacement yet, so it would be an orphan.
        if replacement_id:
            await _safe_delete_message(replacement_id)
        raise

    # Same ordering as `_replace_chunk`: not until the metadata write lands is
    # anything still referencing these. Deleting first would turn a failed
    # update into real data loss; deleting after can at worst leave an orphan.
    for chunk in drop:
        await _safe_delete_message(chunk["message_id"])
    if straddler is not None:
        await _safe_delete_message(straddler["message_id"])


def _new_dir_doc(key: bytes, *, node_id: str, parent_id, filename: str,
                 permissions: int, now: int) -> dict:
    """A directory document with both of its tags already in place.

    An empty directory still needs an entry tag. Leaving it off until the
    first child would mean "no tag yet" and "someone removed the tag" look
    identical, and the second one is how an attacker would switch the check
    off before deleting things.
    """
    node = {
        "id": node_id,
        "parent_id": parent_id,
        "filename": filename,
        "is_dir": True,
        "size": 0,
        "permissions": permissions,
        "created_at": now,
        "modified_at": now,
        "tag_version": TAG_VERSION,
    }
    node["mac"] = _dir_mac(key, node)
    node["entries_mac"] = dir_entries_tag(key, dir_id=node_id, entries=[]).hex()
    return node


class DiscordVFS:
    """Directory tree operations, bound to one connection's key and root.

    One instance per connection rather than one per process: the key is the
    session's, and it goes away when the session does.

    `root_id` has no default, and that is the point. A default would mean a
    caller who forgot it silently got somebody else's tree, and with one key
    per account the mismatch would not even fail loudly -- path resolution
    would start at a directory this key cannot verify, which reads as
    tampering rather than as a bug here.
    """

    def __init__(self, key: bytes, root_id: str):
        if not key:
            raise ValueError("a DiscordVFS needs the session's master key")
        if not root_id:
            raise ValueError("a DiscordVFS needs the id of the tree it serves")
        self._key = key
        self._root_id = root_id

    @property
    def key(self) -> bytes:
        return self._key

    @property
    def root_id(self) -> str:
        return self._root_id

    async def get_node(self, path: str) -> Optional[dict]:
        """Resolve a path, checking every segment on the way down.

        Every segment, not just the last one. Checking only the destination
        left a renamed directory undetectable from below: `/private/keys.txt`
        served as `/public/keys.txt` verified perfectly, because the file's
        own tag records its parent's *id*, which the rename did not touch.
        The directory whose name actually changed was never looked at.

        This is why a directory's identity tag is separate from its entry
        tag: identity costs one HMAC over the document already in hand, so
        paying it per segment is nothing. The entry tag would mean listing
        every directory on the path, and that is left to `list_dir`.

        Trashed nodes are invisible here, and that single `trashed_at` filter
        is what makes the trash work at all: a trashed file stops resolving,
        so nothing can open it, stat it or rename over it, and a trashed
        directory takes its whole subtree with it without a descendant being
        touched. It is also what lets a new file take a trashed one's name --
        the old node keeps sitting in the same directory under the same name,
        and only the live one is reachable.
        """
        node = _verify_node(
            self._key, await db.get_db().nodes.find_one({"id": self._root_id}))
        for part in [p for p in normalize_path(path).split("/") if p]:
            if not node or not node.get("is_dir"):
                return None
            node = _verify_node(self._key, await db.get_db().nodes.find_one({
                "parent_id": node["id"],
                "filename": part,
                # Matches a missing field as well as an explicit null, which
                # is why a live node never has to carry the field at all.
                "trashed_at": None,
            }))
        return node

    async def get_node_by_id(self, node_id: str) -> Optional[dict]:
        return _verify_node(
            self._key, await db.get_db().nodes.find_one({"id": node_id}))

    async def require_node(self, path: str) -> dict:
        node = await self.get_node(path)
        if not node:
            raise NotFound(normalize_path(path))
        return node

    async def require_dir(self, path: str) -> dict:
        node = await self.require_node(path)
        if not node["is_dir"]:
            raise NotADirectory(normalize_path(path))
        return node

    async def ensure_root(self):
        """Create the tree's root, or refuse to run against an older one.

        This needs the master key, so it happens once a session has
        authenticated rather than at startup. A root created before anyone
        logged in could not carry a tag, and a directory whose missing tag is
        tolerated is a directory whose entries are not protected.
        """
        existing = await db.get_db().nodes.find_one({"id": self._root_id})
        if existing is None:
            await db.get_db().nodes.insert_one(_new_dir_doc(
                self._key, node_id=self._root_id, parent_id=None, filename="",
                permissions=DEFAULT_DIR_MODE, now=_now()))
            return

        if existing.get("tag_version") == TAG_VERSION:
            return

        # A root from before directories were tagged. Tagging it now is only
        # honest while it is empty: computing an entry tag over whatever
        # happens to be there would sign off on a deletion that had already
        # happened, which is the one thing a backfill must never do.
        if await self.children(self._root_id):
            raise VFSError(
                "the root directory predates node tag version "
                f"{TAG_VERSION} and is not empty. Tagging it now would "
                "certify its current contents as authentic without any way "
                "to know they are. Move the data out, let this recreate the "
                "tree, and move it back in."
            )

        await db.get_db().nodes.replace_one(
            {"id": self._root_id},
            _new_dir_doc(self._key, node_id=self._root_id, parent_id=None,
                         filename="", permissions=DEFAULT_DIR_MODE,
                         now=existing.get("created_at") or _now()))

    async def children(self, parent_id: str) -> list:
        """Every child, trashed ones included.

        The entry tag covers membership, and trashing does not change
        membership -- the node stays in its directory, it just stops being
        visible. So this has to keep returning trashed children or
        `verify_dir_entries` would fail on every directory that ever had
        something deleted out of it. Callers that want what a user should
        *see* want `live_children`.
        """
        cursor = db.get_db().nodes.find({"parent_id": parent_id})
        return await cursor.to_list(length=None)

    async def live_children(self, parent_id: str) -> list:
        """The children that are not in the trash."""
        cursor = db.get_db().nodes.find({"parent_id": parent_id,
                                         "trashed_at": None})
        return await cursor.to_list(length=None)

    async def list_dir(self, path: str) -> list:
        """List a directory, checking that nothing has been removed from it.

        This is the one place the entry tag is checked, because it is the one
        place the entries are being read anyway.

        Note what is *not* checked: each child's own tag. A single corrupted
        file therefore still lists fine and only fails when opened or
        stat'ed -- which was the whole reason listing was left unverified
        before. What is verified is membership, so a file deleted by someone
        with database access cannot pass unnoticed.
        """
        return await self.entries_of(await self.require_dir(path))

    async def entries_of(self, node: dict) -> list:
        """The verified children of a directory node already in hand.

        Separate from `list_dir` because the SFTP layer resolves the
        directory itself (it needs the node for `.` and its parent for `..`)
        and would otherwise have to walk the path a second time -- which is
        how it came to call `children()` directly and skip this check
        entirely. Every listing path goes through here now.
        """
        entries = await self.children(node["id"])
        # Verified over *every* child, then filtered. The other order is the
        # trap: checking the tag against a list the trash filter had already
        # thinned would make the check agree with whatever the filter left
        # behind, so setting `trashed_at` on someone else's file would erase
        # it from the listing and from the evidence in the same stroke. The
        # tag has to see the set it actually signed.
        verify_dir_entries(self._key, dir_id=node["id"],
                           entries=_entries_of(entries),
                           tag_hex=node.get("entries_mac"),
                           pending_hex=node.get("entries_mac_pending"))

        live = []
        for child in entries:
            if child.get("trashed_at") is None:
                live.append(child)
                continue
            # Every child that gets filtered out has its own tag checked right
            # here, and that is what makes trash state worth covering at all.
            # Membership is intact either way -- a hidden child is still in
            # the entry tag -- so the tag above cannot tell the difference,
            # and the node itself is never fetched again by anything else: it
            # is unreachable by path by definition. Without this, setting
            # `trashed_at` on somebody's file would make it vanish from every
            # listing and nothing would ever look at the field that did it.
            #
            # Live children get no such treatment on purpose. They are
            # verified when they are opened or stat'ed, and until then they
            # are visible, so nothing is being hidden on the strength of an
            # unchecked field.
            _verify_node(self._key, child)
        return live

    async def _stage_entries(self, dir_id: str, *, add=(), remove=()):
        """Prepare a directory's next entry tag, returning its commit step.

        Two writes, not one, and the order matters. The directory's tag and
        its children are separate documents, so a structural change cannot
        update both atomically -- there are no transactions on a standalone
        MongoDB. Writing the new tag first and the change second would leave a
        crash looking like a deleted entry; writing it second would leave one
        looking like an added entry. Either way `ls` breaks on a directory
        nobody attacked.

        So the new tag is parked in `entries_mac_pending`, the change is made,
        and only then is it promoted. `verify_dir_entries` accepts either
        value, so both sides of a crash read as intact. See its docstring for
        why accepting two does not weaken the check.

        The existing tag is verified first, against the children that are
        actually there. Skipping that would let the next ordinary `mkdir`
        launder an earlier deletion into a freshly signed tag.
        """
        node = await db.get_db().nodes.find_one({"id": dir_id, "is_dir": True})
        if node is None:
            raise NotFound(dir_id)

        current = _entries_of(await self.children(dir_id))
        verify_dir_entries(self._key, dir_id=dir_id, entries=current,
                           tag_hex=node.get("entries_mac"),
                           pending_hex=node.get("entries_mac_pending"))

        dropped = set(remove)
        entries = [e for e in current if e not in dropped] + list(add)
        staged = dir_entries_tag(self._key, dir_id=dir_id, entries=entries).hex()

        # The mtime moves with the staging write: creating, removing or
        # renaming an entry modifies the *directory*, not the entry, which is
        # also why renaming a file leaves the file's own mtime alone.
        await db.get_db().nodes.update_one(
            {"id": dir_id},
            {"$set": {"entries_mac_pending": staged, "modified_at": _now()}})

        async def commit():
            await db.get_db().nodes.update_one(
                {"id": dir_id},
                {"$set": {"entries_mac": staged},
                 "$unset": {"entries_mac_pending": ""}})

        return commit

    async def makedir(self, path: str, *, permissions: int = None) -> dict:
        normalized = normalize_path(path)
        if normalized == "/":
            raise AlreadyExists("/")

        if await self.get_node(normalized):
            raise AlreadyExists(normalized)

        parent_path, name = split_path(normalized)
        parent = await self.require_dir(parent_path)

        node = _new_dir_doc(
            self._key, node_id=str(uuid.uuid4()), parent_id=parent["id"],
            filename=name,
            permissions=(DEFAULT_DIR_MODE if permissions is None
                         else int(permissions) & PERMISSION_MASK),
            now=_now())

        commit = await self._stage_entries(
            parent["id"], add=[(node["id"], name)])
        await db.get_db().nodes.insert_one(node)
        await commit()
        return node

    async def remove(self, path: str):
        """Move a file to the trash.

        Deleting is two steps now, and this is only the first. Nothing is
        released here: the chunks stay on Discord, the node stays in its
        directory, the entry tag never moves. That is what gives `restore`
        something to bring back. `purge` is what actually destroys.

        SFTP `rm` lands here too, deliberately. A client sees the file
        disappear either way, so no protocol expectation is broken, and the
        deletions worth protecting against are exactly the scripted ones that
        arrive over SFTP at three in the morning.
        """
        node = await self.require_node(path)
        if node["is_dir"]:
            raise IsADirectory(normalize_path(path))
        await self._set_trashed(node, _now())

    async def removedir(self, path: str):
        """Move an empty directory to the trash.

        Emptiness counts live children only. `rm *` then `rmdir` is how a
        directory gets cleared over SFTP, and children sitting in the trash --
        which is where that `rm *` just put them -- would otherwise turn the
        `rmdir` into ENOTEMPTY on a directory the client had emptied.

        A non-empty directory is still refused, even though trashing one is
        now a single field write. ENOTEMPTY is a contract scripts read as
        "there is still something in here", and owning a trash bin is not a
        reason to start swallowing whole trees silently. `trash` is where that
        power lives, reached from a UI where a person is reading a dialog.
        """
        node = await self.require_node(path)
        if not node["is_dir"]:
            raise NotADirectory(normalize_path(path))
        if node["id"] == self._root_id:
            raise Unsupported("cannot remove the root directory")
        if await self.live_children(node["id"]):
            raise NotEmpty(normalize_path(path))

        await self._set_trashed(node, _now())

    async def trash(self, path: str):
        """Move anything to the trash, subtree included.

        The one thing `removedir` will not do. A directory with ten thousand
        files under it costs one field write, because `get_node` stops
        resolving through a trashed directory and the whole subtree goes out
        of view without a single descendant being touched.
        """
        node = await self.require_node(path)
        if node["id"] == self._root_id:
            raise Unsupported("cannot remove the root directory")
        await self._set_trashed(node, _now())

    async def _set_trashed(self, node: dict, when):
        """Write a node's trash state, and the tag that now covers it.

        `when=None` is a restore. Both directions go through here so there is
        one place where trash state and the tag over it move together -- the
        same reason `_content_update` exists for size, chunks and mac.
        """
        moved = dict(node, trashed_at=when)
        update = {
            "mac": _dir_mac(self._key, moved) if node["is_dir"]
            else _file_mac(self._key, moved),
            "tag_version": TAG_VERSION,
        }
        # A live node carries no `trashed_at` at all, rather than one set to
        # null, and that is not tidiness -- it is what the unique index on
        # (parent_id, filename) needs. That index is now partial, over
        # documents where the field does not exist, so that a trashed node can
        # keep sitting in its directory under its old name while a new file
        # takes that name. `$exists` is the condition MongoDB supports for
        # this; an explicit null would have to be matched with `$eq: null`,
        # which is not the same thing and does not cover a missing field.
        write = {"$set": update} if when is not None \
            else {"$set": update, "$unset": {"trashed_at": ""}}
        if when is not None:
            update["trashed_at"] = when

        await db.get_db().nodes.update_one({"id": node["id"]}, write)
        node.update(update)
        if when is None:
            node.pop("trashed_at", None)
        if not node["is_dir"]:
            # The mac moved, so any handle open on this file will notice and
            # refetch rather than keep serving a node it thinks is live.
            _node_versions[node["id"]] = update["mac"]

    # ------------------------------------------------------------------ trash

    async def _ancestors_of(self, node: dict, cache: dict):
        """Walk from a node up to this tree's root.

        Returns `(parent_path, blocked_by)` -- where the node would come back
        to, and the first trashed directory above it if there is one. A node
        whose walk never reaches this root returns `(None, None)`: it belongs
        to another account's tree and has no business being listed here.

        The walk itself is deliberately unverified, and that is safe because
        of what it is used for. It only *selects* candidates; every node it
        selects is verified by the caller, and a node's own tag covers its
        `parent_id`. So pointing a foreign node at this tree cannot smuggle it
        into a listing -- it makes that node fail its own check instead, which
        is the alarm rather than the leak. Verifying every ancestor of every
        trash item would mean an HMAC per directory per item for a path string
        that is only ever displayed.
        """
        segments = []
        blocked_by = None
        current_id = node.get("parent_id")
        seen = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            if current_id == self._root_id:
                return "/" + "/".join(reversed(segments)), blocked_by
            if current_id in cache:
                parent = cache[current_id]
            else:
                parent = await db.get_db().nodes.find_one({"id": current_id})
                cache[current_id] = parent
            if parent is None:
                return None, None
            if parent.get("trashed_at") is not None and blocked_by is None:
                blocked_by = parent
            segments.append(parent.get("filename") or "")
            current_id = parent.get("parent_id")
        return None, None

    async def list_trash(self) -> list:
        """The things somebody actually deleted, newest first.

        A node sitting under an already-trashed directory is left out: it went
        in when its parent did, and restoring the parent is what brings it
        back. Once that parent is restored the child shows up here on its own
        if it was separately trashed earlier, so nothing can end up deleted,
        invisible and unreachable at the same time.
        """
        cursor = db.get_db().nodes.find({"trashed_at": {"$ne": None}})
        cache: dict = {}
        items = []
        for candidate in await cursor.to_list(length=None):
            parent_path, blocked_by = await self._ancestors_of(candidate, cache)
            if parent_path is None or blocked_by is not None:
                continue
            node = _verify_node(self._key, candidate)
            items.append({
                "node": node,
                "parent_path": parent_path,
                "path": _join(parent_path, node.get("filename") or ""),
            })
        items.sort(key=lambda item: (-int(item["node"].get("trashed_at") or 0),
                                     (item["node"].get("filename") or "").lower()))
        return items

    async def _free_name(self, parent_id: str, name: str) -> str:
        """`report.pdf` -> `report (2).pdf`, skipping names already taken."""
        stem, dot, ext = name.rpartition(".")
        if not dot:
            stem, ext = name, ""
        for suffix in range(2, 1000):
            candidate = f"{stem} ({suffix}){dot}{ext}"
            clash = await db.get_db().nodes.find_one({
                "parent_id": parent_id, "filename": candidate,
                "trashed_at": None})
            if clash is None:
                return candidate
        raise AlreadyExists(name)

    async def restore(self, node_id: str, *, on_conflict: str = "fail") -> dict:
        """Bring a trashed node back where it was.

        Where it was is simply where it still is -- nothing moved on the way
        in -- so this clears one field and retags. The only thing that can go
        wrong is somebody having taken the name in the meantime, and
        `on_conflict` is the answer to the dialog about it: `replace` puts the
        occupant in the trash rather than destroying it, `keep_both` restores
        under `name (2)`, `skip` does nothing, and the default refuses.
        """
        node = await self.get_node_by_id(node_id)
        if node is None or node.get("trashed_at") is None:
            raise NotFound(node_id)

        parent_path, blocked_by = await self._ancestors_of(node, {})
        if parent_path is None:
            raise NotFound(node_id)
        if blocked_by is not None:
            raise Unsupported(
                f"{blocked_by.get('filename') or 'a parent directory'} is in "
                "the trash as well; restore that first")

        original = node.get("filename") or ""
        name = original
        occupant = await db.get_db().nodes.find_one({
            "parent_id": node["parent_id"], "filename": original,
            "trashed_at": None})

        if occupant is not None:
            if on_conflict == "skip":
                return {"restored": False, "conflict": True, "path": None}
            if on_conflict == "replace":
                # The occupant goes to the trash rather than being destroyed.
                # Windows overwrites outright here, but Windows is answering
                # for a filesystem where the bin already caught the old copy;
                # letting a restore be the one operation that loses data with
                # no way back would be a strange thing for a trash bin to do.
                await self._set_trashed(_verify_node(self._key, occupant), _now())
            elif on_conflict == "keep_both":
                name = await self._free_name(node["parent_id"], original)
            else:
                raise AlreadyExists(_join(parent_path, original))

        # A new name changes the (id, name) pair the parent's entry tag
        # covers, so that tag has to be restaged. Restoring under the original
        # name changes no membership at all and needs none of this.
        commit = None
        if name != original:
            commit = await self._stage_entries(
                node["parent_id"], add=[(node["id"], name)],
                remove=[(node["id"], original)])

        restored = dict(node, filename=name, trashed_at=None)
        update = {
            "filename": name,
            "mac": _dir_mac(self._key, restored) if node["is_dir"]
            else _file_mac(self._key, restored),
            "tag_version": TAG_VERSION,
        }
        # Unset rather than nulled -- see `_set_trashed` for why the partial
        # unique index makes that the difference between a restore working and
        # a duplicate key error.
        await db.get_db().nodes.update_one(
            {"id": node["id"]},
            {"$set": update, "$unset": {"trashed_at": ""}})
        node.update(update)
        node.pop("trashed_at", None)
        if not node["is_dir"]:
            _node_versions[node["id"]] = update["mac"]
        if commit is not None:
            await commit()

        return {"restored": True, "conflict": occupant is not None,
                "path": _join(parent_path, name)}

    async def _subtree(self, node: dict) -> list:
        """This node and everything under it, parents before children."""
        out = [node]
        frontier = [node]
        seen = {node["id"]}
        while frontier:
            following = []
            for parent in frontier:
                if not parent.get("is_dir"):
                    continue
                for child in await self.children(parent["id"]):
                    if child["id"] in seen:
                        continue
                    seen.add(child["id"])
                    following.append(child)
            out.extend(following)
            frontier = following
        return out

    async def purge(self, node_id: str) -> dict:
        """Destroy a trashed node and everything under it. No way back.

        Ordered the same way `remove` used to be: stage the parent's next
        entry tag first, do the destroying, promote the tag last. A crash
        anywhere in the middle leaves the parent holding a tag for each of the
        two possible child sets, and `verify_dir_entries` accepts both, so the
        directory still lists. What a crash can leak is Discord attachments
        whose node is already gone -- which is why the attachments go first
        and the documents last, so the survivor is always a node that can
        still be found and retried rather than an orphan nobody can name.
        """
        node = await self.get_node_by_id(node_id)
        if node is None:
            raise NotFound(node_id)
        if node.get("trashed_at") is None:
            raise Unsupported("only trashed nodes can be purged")
        if node["id"] == self._root_id:
            raise Unsupported("cannot remove the root directory")

        parent_path, _ = await self._ancestors_of(node, {})
        if parent_path is None:
            raise NotFound(node_id)

        subtree = await self._subtree(node)
        commit = await self._stage_entries(
            node["parent_id"],
            remove=[(node["id"], node.get("filename") or "")])

        attachments = 0
        for member in subtree:
            if member.get("is_dir"):
                continue
            for chunk in member.get("chunks") or []:
                await _safe_delete_message(chunk["message_id"])
                attachments += 1

        for member in subtree:
            await db.get_db().nodes.delete_one({"id": member["id"]})
            _node_versions.pop(member["id"], None)

        await commit()
        return {"nodes": len(subtree), "attachments": attachments}

    async def purge_expired(self, *, retention: int, limit: int = 25) -> dict:
        """Purge whatever has sat in the trash longer than `retention`.

        `limit` is what makes this interruptible. Emptying a month of deleted
        files means a Discord call per attachment, and the rate limiter would
        rather be handed twenty-five of them every few minutes than ten
        thousand at once. Whatever is left over is reported, not forgotten --
        the next sweep takes the next batch.
        """
        cutoff = _now() - retention
        due = [item for item in await self.list_trash()
               if int(item["node"].get("trashed_at") or 0) <= cutoff]

        purged = attachments = 0
        for item in due[:limit]:
            result = await self.purge(item["node"]["id"])
            purged += 1
            attachments += result["attachments"]
        return {"purged": purged, "attachments": attachments,
                "remaining": len(due) - purged}

    async def rename(self, old_path: str, new_path: str, *, overwrite: bool = False):
        """Move or rename a node.

        Only metadata moves — the Discord attachments are addressed by message
        id and never need to be touched. `overwrite` distinguishes SFTP v3
        `rename` (must fail if the target exists) from `posix_rename`.
        """
        old = normalize_path(old_path)
        new = normalize_path(new_path)

        if old == "/":
            raise Unsupported("cannot rename the root directory")
        if new == "/":
            raise AlreadyExists("/")

        node = await self.require_node(old)
        if old == new:
            return

        if node["is_dir"] and new.startswith(old + "/"):
            raise Unsupported("cannot move a directory into its own subtree")

        parent_path, name = split_path(new)
        parent = await self.require_dir(parent_path)

        existing = await self.get_node(new)
        if existing:
            if not overwrite:
                raise AlreadyExists(new)
            if existing["is_dir"] and await self.live_children(existing["id"]):
                raise NotEmpty(existing["filename"])

        source_parent = node["parent_id"]
        target_parent = parent["id"]

        # The node's identity is part of its tag now, so a move is a retag.
        # `modified_at` is still deliberately absent: moving a file does not
        # modify it. The directories on either end are what changed, and a
        # client that compares mtimes to decide what to re-transfer would
        # otherwise see every moved file as freshly written.
        moved = dict(node, parent_id=target_parent, filename=name)
        update = {
            "parent_id": target_parent,
            "filename": name,
            "mac": _dir_mac(self._key, moved) if node["is_dir"]
            else _file_mac(self._key, moved),
            "tag_version": TAG_VERSION,
        }

        gone = [(existing["id"], existing["filename"])] if existing else []
        arriving = [(node["id"], name)]

        # One staging call when both ends are the same directory: each call
        # recomputes from the children on disk, so a second one would discard
        # what the first staged.
        if source_parent == target_parent:
            commits = [await self._stage_entries(
                target_parent,
                add=arriving,
                remove=gone + [(node["id"], node["filename"])])]
        else:
            commits = [
                await self._stage_entries(
                    source_parent, remove=[(node["id"], node["filename"])]),
                await self._stage_entries(
                    target_parent, add=arriving, remove=gone),
            ]

        if existing:
            if not existing["is_dir"]:
                for chunk in existing.get("chunks", []):
                    await _safe_delete_message(chunk["message_id"])
            await db.get_db().nodes.delete_one({"id": existing["id"]})
            _node_versions.pop(existing["id"], None)

        await db.get_db().nodes.update_one({"id": node["id"]}, {"$set": update})
        node.update(update)
        if not node["is_dir"]:
            _node_versions[node["id"]] = update["mac"]

        for commit in commits:
            await commit()

    async def open(self, path: str, *, read: bool, write: bool, create: bool = False,
                   truncate: bool = False, append: bool = False,
                   exclusive: bool = False) -> DiscordFile:
        normalized = normalize_path(path)
        node = await self.get_node(normalized)

        if node and node["is_dir"]:
            raise IsADirectory(normalized)

        if node and exclusive:
            raise AlreadyExists(normalized)

        if not node:
            if not (write and create):
                raise NotFound(normalized)
            node = await self._create_file(normalized)
            node["_created_by_handle"] = True
        elif write and truncate:
            await self._truncate(node)

        # Opening an existing file for writing without O_TRUNC used to be
        # refused outright. It now patches in place, which is slow but
        # correct -- see DiscordFile._write_random.
        return DiscordFile(self, node, readable=read, writable=write,
                           append=bool(append))

    async def _create_file(self, normalized: str) -> dict:
        parent_path, name = split_path(normalized)
        parent = await self.require_dir(parent_path)

        now = _now()
        node = {
            "id": str(uuid.uuid4()),
            "parent_id": parent["id"],
            "filename": name,
            "is_dir": False,
            "size": 0,
            "permissions": DEFAULT_FILE_MODE,
            "created_at": now,
            "modified_at": now,
            "chunks": [],
            "tag_version": TAG_VERSION,
        }
        # An empty file still needs a tag, or its first read fails the check
        # that every other file passes.
        node["mac"] = _file_mac(self._key, node)

        commit = await self._stage_entries(parent["id"], add=[(node["id"], name)])
        await db.get_db().nodes.insert_one(node)
        _node_versions[node["id"]] = node["mac"]
        await commit()
        return node

    async def set_metadata(self, path: str, **fields):
        """`chmod` / `utimes` by path, for SFTP `setstat`.

        The lookup happens even when there is nothing to apply, so a setstat
        on a path that does not exist still fails.
        """
        node = await self.require_node(path)
        await _apply_metadata(node, **fields)

    async def truncate(self, path: str, size: int):
        """`truncate` by path, for SFTP `setstat`."""
        node = await self.require_node(path)
        if node["is_dir"]:
            raise IsADirectory(normalize_path(path))
        await _resize_node(self._key, node, size)

    async def _truncate(self, node: dict):
        """Drop the file's contents, releasing the Discord messages too.

        The old code cleared `chunks` in MongoDB only, leaving every previous
        attachment referenced by nothing.
        """
        await _resize_node(self._key, node, 0)
