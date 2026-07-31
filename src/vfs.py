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
    chunk_tag,
    generate_nonce,
    node_tag,
    transform,
    verify_chunk,
    verify_node,
)
from src.db import db
from src.discord_api import discord_api

logger = logging.getLogger(__name__)

ROOT_ID = "root"

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
        """
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
            if self._buffer:
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
        """Discard what this handle uploaded.

        A file created by this handle is removed outright. An existing file
        that was truncated for rewriting is left empty — its previous contents
        were already gone before the failure, so there is nothing to restore.
        """
        if self._failed:
            return
        self._failed = True

        for chunk in self._node.get("chunks", []):
            await _safe_delete_message(chunk["message_id"])

        self._node["chunks"] = []
        self._node["size"] = 0

        try:
            if self._node.get("_created_by_handle"):
                await db.get_db().nodes.delete_one({"id": self._node["id"]})
                _node_versions.pop(self._node["id"], None)
            else:
                await _commit_content(self._key, self._node, mtime=self._effective_mtime())
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
        "mac": node_tag(key, file_id=node["id"], size=node["size"],
                        chunk_tags=_chunk_tags(node)).hex(),
        "modified_at": _now() if mtime is None else int(mtime),
    }


async def _touch_dir(node_id: str):
    """Stamp a directory whose set of entries just changed.

    Creating, removing or renaming an entry modifies the *directory*, not the
    entry -- which is also why renaming a file leaves the file's own mtime
    alone.
    """
    if not node_id:
        return
    await db.get_db().nodes.update_one(
        {"id": node_id, "is_dir": True}, {"$set": {"modified_at": _now()}})


def _verify_node(key: bytes, node):
    """Check a file node's tag, passing directories and misses through."""
    if node is None or node.get("is_dir"):
        return node
    verify_node(key, file_id=node["id"], size=node.get("size", 0),
                chunk_tags=_chunk_tags(node), tag_hex=node.get("mac"))
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


async def ensure_root():
    """Create the tree's root if it is not there.

    Deliberately not a method: it runs at startup, before anyone has
    authenticated, and there is no key at that point. The root is a directory
    and carries no content tag, so none is needed.
    """
    existing = await db.get_db().nodes.find_one({"id": ROOT_ID})
    if existing:
        return
    now = _now()
    await db.get_db().nodes.insert_one({
        "id": ROOT_ID,
        "parent_id": None,
        "filename": "",
        "is_dir": True,
        "size": 0,
        "created_at": now,
        "modified_at": now,
    })


class DiscordVFS:
    """Directory tree operations, bound to one connection's master key.

    One instance per connection rather than one per process: the key is the
    session's, and it goes away when the session does.
    """

    def __init__(self, key: bytes):
        if not key:
            raise ValueError("a DiscordVFS needs the session's master key")
        self._key = key

    @property
    def key(self) -> bytes:
        return self._key

    async def get_node(self, path: str) -> Optional[dict]:
        node = await db.get_db().nodes.find_one({"id": ROOT_ID})
        for part in [p for p in normalize_path(path).split("/") if p]:
            if not node or not node.get("is_dir"):
                return None
            node = await db.get_db().nodes.find_one({
                "parent_id": node["id"],
                "filename": part,
            })
        return _verify_node(self._key, node)

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

    async def children(self, parent_id: str) -> list:
        cursor = db.get_db().nodes.find({"parent_id": parent_id})
        return await cursor.to_list(length=None)

    async def list_dir(self, path: str) -> list:
        node = await self.require_dir(path)
        return await self.children(node["id"])

    async def makedir(self, path: str, *, permissions: int = None) -> dict:
        normalized = normalize_path(path)
        if normalized == "/":
            raise AlreadyExists("/")

        if await self.get_node(normalized):
            raise AlreadyExists(normalized)

        parent_path, name = split_path(normalized)
        parent = await self.require_dir(parent_path)

        now = _now()
        node = {
            "id": str(uuid.uuid4()),
            "parent_id": parent["id"],
            "filename": name,
            "is_dir": True,
            "size": 0,
            "permissions": (DEFAULT_DIR_MODE if permissions is None
                            else int(permissions) & PERMISSION_MASK),
            "created_at": now,
            "modified_at": now,
        }
        await db.get_db().nodes.insert_one(node)
        return node

    async def remove(self, path: str):
        node = await self.require_node(path)
        if node["is_dir"]:
            raise IsADirectory(normalize_path(path))

        for chunk in node.get("chunks", []):
            await _safe_delete_message(chunk["message_id"])

        await db.get_db().nodes.delete_one({"id": node["id"]})
        _node_versions.pop(node["id"], None)
        await _touch_dir(node.get("parent_id"))

    async def removedir(self, path: str):
        node = await self.require_node(path)
        if not node["is_dir"]:
            raise NotADirectory(normalize_path(path))
        if node["id"] == ROOT_ID:
            raise Unsupported("cannot remove the root directory")
        if await self.children(node["id"]):
            raise NotEmpty(normalize_path(path))

        await db.get_db().nodes.delete_one({"id": node["id"]})
        await _touch_dir(node.get("parent_id"))

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
            await self._discard(existing)

        source_parent = node.get("parent_id")

        # `modified_at` is deliberately absent: moving a file does not modify
        # it. The directories on either end are what changed, and a client
        # that compares mtimes to decide what to re-transfer would otherwise
        # see every moved file as freshly written.
        await db.get_db().nodes.update_one(
            {"id": node["id"]},
            {"$set": {"parent_id": parent["id"], "filename": name}},
        )
        await _touch_dir(source_parent)
        await _touch_dir(parent["id"])

    async def _discard(self, node: dict):
        """Remove a node that is being replaced, releasing any attachments."""
        if node["is_dir"]:
            if await self.children(node["id"]):
                raise NotEmpty(node["filename"])
        else:
            for chunk in node.get("chunks", []):
                await _safe_delete_message(chunk["message_id"])

        await db.get_db().nodes.delete_one({"id": node["id"]})
        _node_versions.pop(node["id"], None)

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
        }
        # An empty file still needs a tag, or its first read fails the check
        # that every other file passes.
        node["mac"] = node_tag(self._key, file_id=node["id"], size=0,
                               chunk_tags=[]).hex()
        await db.get_db().nodes.insert_one(node)
        _node_versions[node["id"]] = node["mac"]
        await _touch_dir(parent["id"])
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
