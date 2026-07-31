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
        hmac        str   hex, HMAC-SHA256 over nonce||ciphertext
        offset      int   plaintext offset of this chunk within the file
        size        int   plaintext length of this chunk

`hmac` is required. It lives here rather than alongside the ciphertext on
Discord on purpose: that is what stops whoever serves the bytes from also
producing a tag for them. A chunk without one is rejected rather than read
unverified -- see `crypto.verify_chunk`.

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

from src.config import AES_SECRET_KEY, CHUNK_CACHE_SIZE, MAX_CHUNK_SIZE
from src.crypto import chunk_tag, generate_nonce, transform, verify_chunk
from src.db import db
from src.discord_api import discord_api

logger = logging.getLogger(__name__)

ROOT_ID = "root"


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
        self._node = node
        self._readable = readable
        self._writable = writable
        self._append_mode = append

        self._buffer = bytearray()
        self._chunk_cache: "OrderedDict[int, bytes]" = OrderedDict()
        self._lock = asyncio.Lock()
        self._closed = False
        self._failed = False

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

    # ---------------------------------------------------------------- reading

    async def read_at(self, offset: int, size: int) -> bytes:
        if not self._readable:
            raise Unsupported("file is not open for reading")

        async with self._lock:
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

        plaintext = await _fetch_chunk(chunk)

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
        ciphertext = transform(AES_SECRET_KEY, nonce, plaintext)
        tag = chunk_tag(AES_SECRET_KEY, nonce, ciphertext)
        filename = f"{self._node['id']}_chunk_{chunk['index']}.bin"

        message_id, _url, _size = await discord_api.upload_chunk(ciphertext, filename)

        previous = (chunk["message_id"], chunk["nonce"], chunk.get("hmac"))
        chunk["message_id"] = message_id
        chunk["nonce"] = nonce.hex()
        chunk["hmac"] = tag.hex()

        try:
            await db.get_db().nodes.update_one(
                {"id": self._node["id"]},
                {"$set": {"chunks": self._node["chunks"], "modified_at": _now()}},
            )
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

        nonce = generate_nonce()
        ciphertext = transform(AES_SECRET_KEY, nonce, data)
        tag = chunk_tag(AES_SECRET_KEY, nonce, ciphertext)
        index = len(self._node["chunks"])
        filename = f"{self._node['id']}_chunk_{index}.bin"

        try:
            message_id, _url, _size = await discord_api.upload_chunk(ciphertext, filename)
        except Exception:
            await self._rollback()
            raise

        # New chunks land where the existing ones stop, which is not the file
        # size when a hole follows them.
        offset = self._covered_end()
        previous_size = self._node["size"]

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
            await db.get_db().nodes.update_one(
                {"id": self._node["id"]},
                {"$set": {
                    "size": self._node["size"],
                    "chunks": self._node["chunks"],
                    "modified_at": _now(),
                }},
            )
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

        try:
            if self._node.get("_created_by_handle"):
                await db.get_db().nodes.delete_one({"id": self._node["id"]})
            else:
                await db.get_db().nodes.update_one(
                    {"id": self._node["id"]},
                    {"$set": {"size": 0, "chunks": [], "modified_at": _now()}},
                )
        except Exception:
            logger.exception("Rollback bookkeeping failed for node %s", self._node["id"])

        self._node["chunks"] = []
        self._node["size"] = 0
        self._reindex()

    # -------------------------------------------------------------- resizing

    async def truncate_to(self, size: int):
        """`ftruncate` on this handle."""
        if not self._writable:
            raise Unsupported("file is not open for writing")

        async with self._lock:
            if self._failed:
                raise VFSError("file is in a failed state from an earlier error")

            # Buffered bytes are part of the file but belong to no chunk yet,
            # so a resize computed around them would either drop them or
            # place them past the new end.
            await self._flush_buffer()

            await _resize_node(self._node, size)

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

            await self._flush_buffer()
            await db.get_db().nodes.update_one(
                {"id": self._node["id"]},
                {"$set": {"modified_at": _now()}},
            )


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


async def _fetch_chunk(chunk: dict) -> bytes:
    """Download, authenticate and decrypt one chunk."""
    url = await discord_api.get_attachment_url(chunk["message_id"])
    ciphertext = await discord_api.download_chunk(url)
    nonce = bytes.fromhex(chunk["nonce"])

    # Before decryption, not after: the whole point of encrypt-then-MAC is
    # that forged bytes never reach the cipher.
    verify_chunk(AES_SECRET_KEY, nonce, ciphertext, chunk.get("hmac"))

    return transform(AES_SECRET_KEY, nonce, ciphertext)


async def _resize_node(node: dict, size: int):
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
        plaintext = await _fetch_chunk(straddler)
        trimmed = plaintext[:size - straddler["offset"]]

        nonce = generate_nonce()
        ciphertext = transform(AES_SECRET_KEY, nonce, trimmed)
        tag = chunk_tag(AES_SECRET_KEY, nonce, ciphertext)
        filename = f"{node['id']}_chunk_{straddler['index']}.bin"

        replacement_id, _url, _size = await discord_api.upload_chunk(ciphertext, filename)
        keep.append(dict(
            straddler,
            message_id=replacement_id,
            nonce=nonce.hex(),
            hmac=tag.hex(),
            size=len(trimmed),
        ))

    try:
        await db.get_db().nodes.update_one(
            {"id": node["id"]},
            {"$set": {"size": size, "chunks": keep, "modified_at": _now()}},
        )
    except Exception:
        # Nothing points at the replacement yet, so it would be an orphan.
        if replacement_id:
            await _safe_delete_message(replacement_id)
        raise

    node["chunks"] = keep
    node["size"] = size

    # Same ordering as `_replace_chunk`: not until the metadata write lands is
    # anything still referencing these. Deleting first would turn a failed
    # update into real data loss; deleting after can at worst leave an orphan.
    for chunk in drop:
        await _safe_delete_message(chunk["message_id"])
    if straddler is not None:
        await _safe_delete_message(straddler["message_id"])


class DiscordVFS:
    """Directory tree operations. One instance is shared by every connection."""

    async def ensure_root(self):
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

    async def get_node(self, path: str) -> Optional[dict]:
        node = await db.get_db().nodes.find_one({"id": ROOT_ID})
        for part in [p for p in normalize_path(path).split("/") if p]:
            if not node or not node.get("is_dir"):
                return None
            node = await db.get_db().nodes.find_one({
                "parent_id": node["id"],
                "filename": part,
            })
        return node

    async def get_node_by_id(self, node_id: str) -> Optional[dict]:
        return await db.get_db().nodes.find_one({"id": node_id})

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

    async def makedir(self, path: str) -> dict:
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

    async def removedir(self, path: str):
        node = await self.require_node(path)
        if not node["is_dir"]:
            raise NotADirectory(normalize_path(path))
        if node["id"] == ROOT_ID:
            raise Unsupported("cannot remove the root directory")
        if await self.children(node["id"]):
            raise NotEmpty(normalize_path(path))

        await db.get_db().nodes.delete_one({"id": node["id"]})

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

        await db.get_db().nodes.update_one(
            {"id": node["id"]},
            {"$set": {
                "parent_id": parent["id"],
                "filename": name,
                "modified_at": _now(),
            }},
        )

    async def _discard(self, node: dict):
        """Remove a node that is being replaced, releasing any attachments."""
        if node["is_dir"]:
            if await self.children(node["id"]):
                raise NotEmpty(node["filename"])
        else:
            for chunk in node.get("chunks", []):
                await _safe_delete_message(chunk["message_id"])

        await db.get_db().nodes.delete_one({"id": node["id"]})

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
            "created_at": now,
            "modified_at": now,
            "chunks": [],
        }
        await db.get_db().nodes.insert_one(node)
        return node

    async def truncate(self, path: str, size: int):
        """`truncate` by path, for SFTP `setstat`."""
        node = await self.require_node(path)
        if node["is_dir"]:
            raise IsADirectory(normalize_path(path))
        await _resize_node(node, size)

    async def _truncate(self, node: dict):
        """Drop the file's contents, releasing the Discord messages too.

        The old code cleared `chunks` in MongoDB only, leaving every previous
        attachment referenced by nothing.
        """
        await _resize_node(node, 0)
