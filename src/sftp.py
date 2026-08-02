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
import logging
import stat

import asyncssh

from src import keystore, users
from src.config import SFTP_USER
from src.crypto import IntegrityError, KeyUnwrapError
from src.vfs import (
    DEFAULT_DIR_MODE,
    DEFAULT_FILE_MODE,
    PERMISSION_MASK,
    DiscordVFS,
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
    else:
        # Everything else is FX_FAILURE, AlreadyExists / NotEmpty /
        # NotADirectory / IsADirectory included. SFTP v3 has no more precise
        # code for any of them -- v4 and v6 do, but this server speaks v3, so
        # a branch naming them would look like it produced a distinct status
        # and would not.
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


def _attrs(node: dict, size: int = None) -> asyncssh.SFTPAttrs:
    is_dir = node["is_dir"]
    mtime = int(node.get("modified_at") or node.get("created_at") or 0)
    # Access time is only tracked when a client sets it. Maintaining a real
    # one would mean a database write on every read.
    atime = int(node.get("accessed_at") or mtime)

    default_mode = DEFAULT_DIR_MODE if is_dir else DEFAULT_FILE_MODE
    mode = int(node.get("permissions", default_mode)) & PERMISSION_MASK
    file_type = stat.S_IFDIR if is_dir else stat.S_IFREG

    return asyncssh.SFTPAttrs(
        size=int(node.get("size", 0)) if size is None else int(size),
        uid=0,
        gid=0,
        permissions=file_type | mode,
        atime=atime,
        mtime=mtime,
        nlink=1,
    )


class DiscordSFTPServer(asyncssh.SFTPServer):
    def __init__(self, chan, vfs=None):
        super().__init__(chan)
        # The VFS is built per connection around that session's key *and its
        # tree*, both of which `validate_password` put on the connection. The
        # two travel together because a key opened for one account against
        # another account's root is not a recoverable state -- see
        # `users.Session`. Passing a VFS in is for tests that drive it directly.
        session = chan.get_extra_info("session")
        self._vfs = vfs or DiscordVFS(session.key, session.root_id)
        self._username = session.username if session else SFTP_USER

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
        # Never consult the host's account database. The name comes from the
        # session, so a listing shows whoever is logged in rather than
        # whatever `SFTP_USER` happens to say.
        return self._username

    def format_group(self, gid):
        return self._username

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
        # The handle's size, not the node's: the node does not know about
        # bytes that have been written but not yet uploaded. asyncssh calls
        # this to size a length-less read(), so a stale answer here makes a
        # client's own last write read back as EOF. Refresh first so a resize
        # committed by another handle shows up too.
        await file_obj.refresh()
        return _attrs(file_obj.node, file_obj.size)

    @staticmethod
    def _metadata_fields(attrs) -> dict:
        """The parts of an SFTPAttrs this filesystem stores.

        Ownership is not among them: there is one account, so a uid would be
        a number with nothing behind it.
        """
        return {
            "permissions": getattr(attrs, "permissions", None),
            "mtime": getattr(attrs, "mtime", None),
            "atime": getattr(attrs, "atime", None),
        }

    @_translate
    async def setstat(self, path, attrs):
        decoded = self._decode(path)

        size = getattr(attrs, "size", None)
        if size is not None:
            await self._vfs.truncate(decoded, size)

        # After the resize, never before: truncating stamps a new mtime, so
        # doing this first would let it overwrite the time the client asked
        # for. `put -p` sends both in one call.
        await self._vfs.set_metadata(decoded, **self._metadata_fields(attrs))

    @_translate
    async def fsetstat(self, file_obj, attrs):
        size = getattr(attrs, "size", None)
        if size is not None:
            # Goes through the handle rather than the path so that anything
            # the handle is still buffering is accounted for, and so its
            # decrypted chunk cache is invalidated.
            await file_obj.truncate_to(size)

        await file_obj.set_metadata(**self._metadata_fields(attrs))

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
            # `entries_of`, not `children`: the latter skips the check that
            # the directory still holds the entries its tag was made for,
            # which is the only place a deletion by someone with database
            # access shows up. Calling `children` here left that protection
            # working for direct VFS callers and switched off over the actual
            # protocol -- found in live acceptance, not by the unit tests,
            # which drove `list_dir` directly.
            entries = await self._vfs.entries_of(node)
        except asyncssh.SFTPError:
            raise
        except IntegrityError as exc:
            # Same reasoning as `_translate`, which an async generator cannot
            # use: this is a security event, not an unhandled bug.
            logger.error("Integrity check failed in SFTP scandir: %s", exc)
            raise asyncssh.SFTPError(asyncssh.FX_FAILURE, str(exc)) from exc
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
        await self._vfs.makedir(self._decode(path),
                                permissions=getattr(attrs, "permissions", None))

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

        file_obj = await self._vfs.open(
            self._decode(path),
            read=read,
            write=write,
            create=bool(pflags & asyncssh.FXF_CREAT),
            truncate=bool(pflags & asyncssh.FXF_TRUNC),
            append=bool(pflags & asyncssh.FXF_APPEND),
            exclusive=bool(pflags & asyncssh.FXF_EXCL),
        )
        _open_files.add(file_obj)
        return file_obj

    @_translate
    async def read(self, file_obj, offset, size):
        # An empty result is asyncssh's cue to answer the client with EOF.
        return await file_obj.read_at(offset, size)

    @_translate
    async def write(self, file_obj, offset, data):
        return await file_obj.write_at(offset, data)

    @_translate
    async def close(self, file_obj):
        try:
            await file_obj.close()
        finally:
            _open_files.discard(file_obj)

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


# Live connections, so shutdown can let them finish rather than cutting them
# off mid-upload. A set of the connection objects themselves: asyncssh gives
# no registry of its own.
_active_connections = set()

# Open file handles, for the same reason but a more specific one. asyncssh does
# call close() on each handle when a session ends, but that cleanup is not
# awaited by `conn.wait_closed()`, so a shutdown that relied on it exited
# before the flush reached Discord -- which is how a client's last 64KB
# disappeared even though the drain logs looked correct.
_open_files = set()


def active_connections():
    return frozenset(_active_connections)


def open_files():
    return frozenset(_open_files)


class DiscordSSHServer(asyncssh.SSHServer):
    def __init__(self):
        self._conn = None

    def connection_made(self, conn):
        self._conn = conn
        _active_connections.add(conn)
        peer = conn.get_extra_info("peername")
        logger.info("SSH connection received from %s", peer[0] if peer else "unknown")

    def connection_lost(self, exc):
        _active_connections.discard(self._conn)
        # Best effort at not leaving the session's master key reachable for
        # longer than the session. Python cannot overwrite the bytes object
        # itself, so this drops the reference rather than erasing the key.
        if self._conn is not None:
            self._conn.set_extra_info(session=None)
            self._conn = None

        if isinstance(exc, ConnectionResetError) or exc is None:
            # Most clients drop the TCP connection instead of sending an SSH
            # disconnect, which asyncssh reports as an error. Logging that at
            # warning level buried the warnings that mean something.
            logger.info("SSH connection closed")
        else:
            logger.warning("SSH connection error: %s", exc)

    def begin_auth(self, username):
        return True  # authentication is required

    def password_auth_supported(self):
        return True

    async def validate_password(self, username, password):
        """Check the credentials and, if they hold, open this session's key.

        Three steps rather than the two this used to be, because the account
        is a row now instead of a pair of module constants:

        1. look the account up and verify the stored password hash --
           `users.authenticate` spends the same time on a username that does
           not exist, which the old `compare_digest` got for free and a
           database lookup does not;
        2. open *that account's* wrapped master key. This is still the check
           that matters: a password that cannot do it cannot read a byte
           whatever else it satisfies;
        3. make sure the account's own tree exists.
        """
        user = await users.authenticate(username, password)
        if user is None:
            # No detail, deliberately: which of the two failed is exactly what
            # the dummy verification in `users.authenticate` exists to hide.
            logger.info("Password authentication refused for %r", username)
            return False

        try:
            key = await keystore.open_master_key(users.keystore_id(user),
                                                 password)
        except (KeyUnwrapError, keystore.KeystoreError) as exc:
            # Startup already proved this works for the environment's account,
            # so reaching here means the keystore changed underneath a running
            # server.
            logger.error("Could not open the master key for %s: %s", username, exc)
            return False

        # The root directory is tagged, so it cannot be created before a key
        # exists -- which is to say, not at startup. This is the first moment
        # one does. It is a single lookup once the tree is there.
        try:
            await DiscordVFS(key, user["root_id"]).ensure_root()
        except VFSError as exc:
            logger.error("Cannot serve this tree: %s", exc)
            return False

        self._conn.set_extra_info(session=users.Session(
            key=key, root_id=user["root_id"], username=user["username"]))
        return True
