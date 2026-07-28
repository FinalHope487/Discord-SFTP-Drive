"""asyncssh SFTP server bound to the Discord-backed VFS.

Two things the previous version got wrong are worth spelling out, because both
were silent failures rather than obvious ones:

1. asyncssh's *server* API puts the I/O methods on `SFTPServer` itself, with
   the handle as the first argument — `read(file_obj, offset, size)`,
   `write(file_obj, offset, data)`, `close(file_obj)`. Subclassing
   `asyncssh.SFTPFile` (a client-side class) meant those overrides were never
   called, and the inherited defaults tried to `seek()` on the handle.

2. Every `SFTPServer` method left unimplemented falls through to a default
   that operates on the *host* filesystem — `realpath` returns the server's
   working directory, `lstat`/`fstat` call `os.lstat`/`os.fstat`, `statvfs`
   calls `os.statvfs`. Since clients call `realpath(".")` immediately after
   connecting, the session was answering from the wrong filesystem before it
   ever touched the VFS. Everything is overridden below, including the
   operations we do not support, so nothing reaches the host.
"""

import functools
import hmac
import logging
import stat

import asyncssh

from src.config import SFTP_PASSWORD, SFTP_USER
from src.crypto import IntegrityError
from src.vfs import (
    AlreadyExists,
    IsADirectory,
    NotADirectory,
    NotEmpty,
    NotFound,
    Unsupported,
    VFSError,
    normalize_path,
)

logger = logging.getLogger(__name__)

# Discord imposes no practical quota, so report a large synthetic volume
# rather than reaching for the host's statvfs.
_SYNTHETIC_BLOCK_SIZE = 4096
_SYNTHETIC_BLOCKS = 1 << 40


def _to_sftp_error(exc: VFSError) -> asyncssh.SFTPError:
    if isinstance(exc, NotFound):
        code = asyncssh.FX_NO_SUCH_FILE
    elif isinstance(exc, Unsupported):
        code = asyncssh.FX_OP_UNSUPPORTED
    elif isinstance(exc, (AlreadyExists, NotEmpty, NotADirectory, IsADirectory)):
        code = asyncssh.FX_FAILURE
    else:
        code = asyncssh.FX_FAILURE
    return asyncssh.SFTPError(code, str(exc) or exc.__class__.__name__)


def _translate(func):
    """Convert VFS exceptions into SFTP status codes, log the rest."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except asyncssh.SFTPError:
            raise
        except IntegrityError as exc:
            # Handled before the generic branch so it is logged as the
            # security event it is, rather than as an unhandled bug with a
            # traceback. The client gets a failure and no data: returning the
            # bytes anyway would defeat the check entirely.
            logger.error("Integrity check failed in SFTP %s: %s",
                         func.__name__, exc)
            raise asyncssh.SFTPError(asyncssh.FX_FAILURE, str(exc)) from exc
        except VFSError as exc:
            raise _to_sftp_error(exc) from exc
        except Exception as exc:
            logger.exception("Unhandled error in SFTP %s", func.__name__)
            message = str(exc) or exc.__class__.__name__
            raise asyncssh.SFTPError(asyncssh.FX_FAILURE, message) from exc

    return wrapper


def _attrs(node: dict) -> asyncssh.SFTPAttrs:
    is_dir = node["is_dir"]
    mtime = int(node.get("modified_at") or node.get("created_at") or 0)
    permissions = (stat.S_IFDIR | 0o755) if is_dir else (stat.S_IFREG | 0o644)
    return asyncssh.SFTPAttrs(
        size=int(node.get("size", 0)),
        uid=0,
        gid=0,
        permissions=permissions,
        atime=mtime,
        mtime=mtime,
        nlink=1,
    )


class DiscordSFTPServer(asyncssh.SFTPServer):
    def __init__(self, chan, vfs):
        super().__init__(chan)
        self._vfs = vfs

    # ------------------------------------------------------------- utilities

    @staticmethod
    def _decode(path) -> str:
        if isinstance(path, bytes):
            return path.decode("utf-8", errors="surrogateescape")
        return path

    @staticmethod
    def _encode(path: str) -> bytes:
        return path.encode("utf-8", errors="surrogateescape")

    def format_user(self, uid):
        # Never consult the host's account database.
        return SFTP_USER

    def format_group(self, gid):
        return SFTP_USER

    async def _parent_node(self, node: dict) -> dict:
        if not node.get("parent_id"):
            return node
        parent = await self._vfs.get_node_by_id(node["parent_id"])
        return parent or node

    # ------------------------------------------------------------ path layer

    @_translate
    async def realpath(self, path):
        # Called by essentially every client right after connecting, usually
        # with b"." — this is what anchors the session at the VFS root.
        return self._encode(normalize_path(self._decode(path)))

    @_translate
    async def stat(self, path):
        return _attrs(await self._vfs.require_node(self._decode(path)))

    @_translate
    async def lstat(self, path):
        # No symlinks in this filesystem, so lstat and stat coincide.
        return _attrs(await self._vfs.require_node(self._decode(path)))

    @_translate
    async def fstat(self, file_obj):
        return _attrs(file_obj.node)

    @_translate
    async def setstat(self, path, attrs):
        node = await self._vfs.require_node(self._decode(path))
        self._reject_resize(attrs, node)
        # Permissions, ownership and timestamps are not modelled; accepting
        # them silently keeps post-upload chmod/utimes from failing the client.

    @_translate
    async def fsetstat(self, file_obj, attrs):
        self._reject_resize(attrs, file_obj.node)

    @staticmethod
    def _reject_resize(attrs, node):
        size = getattr(attrs, "size", None)
        if size is not None and size != node.get("size", 0):
            raise asyncssh.SFTPError(
                asyncssh.FX_OP_UNSUPPORTED,
                "resizing an existing file is not supported",
            )

    @_translate
    async def statvfs(self, path):
        await self._vfs.require_node(self._decode(path))
        return self._synthetic_vfs_attrs()

    @_translate
    async def fstatvfs(self, file_obj):
        return self._synthetic_vfs_attrs()

    @staticmethod
    def _synthetic_vfs_attrs():
        return asyncssh.SFTPVFSAttrs(
            bsize=_SYNTHETIC_BLOCK_SIZE,
            frsize=_SYNTHETIC_BLOCK_SIZE,
            blocks=_SYNTHETIC_BLOCKS,
            bfree=_SYNTHETIC_BLOCKS,
            bavail=_SYNTHETIC_BLOCKS,
            files=_SYNTHETIC_BLOCKS,
            ffree=_SYNTHETIC_BLOCKS,
            favail=_SYNTHETIC_BLOCKS,
            fsid=0,
            flags=0,
            namemax=255,
        )

    # ------------------------------------------------------------ directories

    async def scandir(self, path):
        """Async iterator of directory entries.

        asyncssh drives this with `async for`, so it must be an async
        generator — it cannot go through `_translate`, and it must not be a
        plain coroutine. (asyncssh still honours a `listdir()` override via a
        backward-compatibility shim, but implementing `scandir` directly is
        the current API and one less indirection.)
        """
        try:
            node = await self._vfs.require_dir(self._decode(path))
            parent = await self._parent_node(node)
            entries = await self._vfs.children(node["id"])
        except asyncssh.SFTPError:
            raise
        except VFSError as exc:
            raise _to_sftp_error(exc) from exc
        except Exception as exc:
            logger.exception("Unhandled error in SFTP scandir")
            raise asyncssh.SFTPError(
                asyncssh.FX_FAILURE, str(exc) or exc.__class__.__name__
            ) from exc

        yield asyncssh.SFTPName(b".", attrs=_attrs(node))
        yield asyncssh.SFTPName(b"..", attrs=_attrs(parent))
        for child in entries:
            yield asyncssh.SFTPName(self._encode(child["filename"]), attrs=_attrs(child))

    @_translate
    async def mkdir(self, path, attrs):
        await self._vfs.makedir(self._decode(path))

    @_translate
    async def rmdir(self, path):
        await self._vfs.removedir(self._decode(path))

    @_translate
    async def remove(self, path):
        await self._vfs.remove(self._decode(path))

    # ------------------------------------------------------------------ files

    @_translate
    async def open(self, path, pflags, attrs):
        read = bool(pflags & asyncssh.FXF_READ)
        write = bool(pflags & asyncssh.FXF_WRITE)
        if not read and not write:
            read = True

        return await self._vfs.open(
            self._decode(path),
            read=read,
            write=write,
            create=bool(pflags & asyncssh.FXF_CREAT),
            truncate=bool(pflags & asyncssh.FXF_TRUNC),
            append=bool(pflags & asyncssh.FXF_APPEND),
            exclusive=bool(pflags & asyncssh.FXF_EXCL),
        )

    @_translate
    async def read(self, file_obj, offset, size):
        # An empty result is asyncssh's cue to answer the client with EOF.
        return await file_obj.read_at(offset, size)

    @_translate
    async def write(self, file_obj, offset, data):
        return await file_obj.write_at(offset, data)

    @_translate
    async def close(self, file_obj):
        await file_obj.close()

    @_translate
    async def fsync(self, file_obj):
        # Chunks are durable in Discord as soon as they upload; nothing to sync.
        return None

    # ----------------------------------------------------------- move/rename

    @_translate
    async def rename(self, oldpath, newpath):
        # SFTP v3 semantics: fail rather than clobber an existing target.
        await self._vfs.rename(self._decode(oldpath), self._decode(newpath),
                               overwrite=False)

    @_translate
    async def posix_rename(self, oldpath, newpath):
        # POSIX semantics: replacing an existing target is allowed.
        await self._vfs.rename(self._decode(oldpath), self._decode(newpath),
                               overwrite=True)

    # ------------------------------------------------- deliberately shut off
    # Each of these would otherwise fall through to a host-filesystem default.

    async def readlink(self, path):
        raise asyncssh.SFTPError(asyncssh.FX_OP_UNSUPPORTED, "symlinks are not supported")

    async def symlink(self, oldpath, newpath):
        raise asyncssh.SFTPError(asyncssh.FX_OP_UNSUPPORTED, "symlinks are not supported")

    async def link(self, oldpath, newpath):
        raise asyncssh.SFTPError(asyncssh.FX_OP_UNSUPPORTED, "hard links are not supported")


class DiscordSSHServer(asyncssh.SSHServer):
    def connection_made(self, conn):
        peer = conn.get_extra_info("peername")
        logger.info("SSH connection received from %s", peer[0] if peer else "unknown")

    def connection_lost(self, exc):
        if exc:
            logger.warning("SSH connection error: %s", exc)
        else:
            logger.info("SSH connection closed")

    def begin_auth(self, username):
        return True  # authentication is required

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        # Constant-time on both fields so neither leaks through timing.
        user_ok = hmac.compare_digest(username.encode(), SFTP_USER.encode())
        pass_ok = hmac.compare_digest(password.encode(), SFTP_PASSWORD.encode())
        return user_ok and pass_ok
