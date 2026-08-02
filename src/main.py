import asyncio
import logging
import os
import signal
import stat
import sys

import asyncssh

from src import keystore, users
from src.config import (
    SFTP_HOST_KEY_PATH,
    SFTP_PASSWORD,
    SFTP_PASSWORD_OLD,
    SFTP_USER,
    ConfigError,
    kdf_settings,
    kdf_upgrade,
    sftp_port,
    validate,
)
from src.db import db
from src.discord_api import ReachabilityError, discord_api
from src.sftp import (
    DiscordSFTPServer,
    DiscordSSHServer,
    active_connections,
    open_files,
)
from src.vfs import ROOT_ID

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# A private key readable by anyone who can reach the file is a key anyone can
# use to impersonate this server, and clients cannot tell the difference.
_HOST_KEY_MODE = 0o600

# How long a stopping server waits for live sessions before closing them.
# Must stay under docker-compose's `stop_grace_period`, or the container is
# killed part way through this and the wait bought nothing.
SHUTDOWN_GRACE_SECONDS = 20

# How long to wait for a connection to actually finish closing, once asked.
CLOSE_TIMEOUT_SECONDS = 5


def ensure_host_key(path: str):
    if os.path.exists(path):
        _restrict_host_key(path)
        return

    directory = os.path.dirname(path) or "."
    if not os.access(directory, os.W_OK):
        # Typical cause: a host_key_data volume created by an older build that
        # ran as root, now mounted into a container that does not. Without
        # this the failure is a bare PermissionError from deep inside asyncssh.
        # getuid() is POSIX-only; this module is imported on Windows too.
        uid = getattr(os, "getuid", lambda: "n/a")()
        raise ConfigError(
            f"No host key at {path} and {directory} is not writable by this "
            f"user (uid {uid}). If this volume was created by an earlier "
            "root-running build, either chown it to uid 10001 or recreate it "
            "-- note that recreating changes the host key, so clients will "
            "report a mismatch."
        )

    logger.info("Generating new host key at %s", path)
    key = asyncssh.generate_private_key("ssh-rsa", key_size=3072)
    key.write_private_key(path)
    _restrict_host_key(path)


def _restrict_host_key(path: str):
    """Force the key to owner-only, repairing keys written by older builds.

    asyncssh writes through the process umask, which in the container left the
    key world-readable.
    """
    try:
        current = stat.S_IMODE(os.stat(path).st_mode)
        if current != _HOST_KEY_MODE:
            os.chmod(path, _HOST_KEY_MODE)
            logger.info("Tightened host key permissions from %o to %o",
                        current, _HOST_KEY_MODE)
    except OSError as exc:
        # Not fatal on its own -- the server still works -- but the operator
        # should know the key is more exposed than intended.
        logger.warning("Could not restrict permissions on %s: %s", path, exc)


async def check_discord_reachable():
    """Fail before listening if the Discord credentials cannot be used.

    A configuration problem (revoked token, bot not in the server, missing
    Attach Files) is fatal: the server would accept SFTP logins and then fail
    every single upload, which looks like data loss to the client.

    A transport failure is not fatal. Discord being briefly unreachable at
    container start would otherwise burn the restart budget and leave the
    service down long after the network recovered.
    """
    try:
        problems = await discord_api.check_reachability()
    except ReachabilityError as exc:
        logger.warning(
            "Skipping the Discord reachability check: %s. Starting anyway; "
            "upload and download errors will surface per request.", exc,
        )
        return

    if problems:
        raise ConfigError(
            "Discord credentials are unusable; refusing to start:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )
    logger.info("Discord reachability check passed")


async def start_server():
    # Everything below runs on this one loop for the process's lifetime.
    # Motor and aiohttp both bind to the running loop, so there is exactly one.
    await db.connect()
    logger.info("Connected to MongoDB")

    # The `finally` starts here rather than around the serve loop: the
    # reachability check can abort startup, and an aiohttp session left open
    # by that path prints an "Unclosed client session" traceback that buries
    # the actual configuration error.
    server = None
    try:
        await check_discord_reachable()

        # The environment's credentials become a row before anything consults
        # them, so `validate_password` has one code path whether an account
        # came from `.env` or (later) from an admin tool. `ROOT_ID` is the
        # tree this account already owns; keeping that id is what made trees
        # per-account without touching a single integrity tag.
        user = await users.sync_env_user(SFTP_USER, SFTP_PASSWORD,
                                         root_id=ROOT_ID)
        record_id = users.keystore_id(user)

        # A rename of one field, not a re-wrap -- see the function's docstring
        # for why it is safe to do without asking and without the password.
        await keystore.adopt_legacy_record(record_id)

        # Before the socket opens: a password that cannot open the master key
        # means every read fails after a successful login, which reads as data
        # loss from the client side.
        try:
            await keystore.ensure_usable(
                record_id,
                SFTP_PASSWORD,
                old_password=SFTP_PASSWORD_OLD,
                settings=kdf_settings(),
                upgrade=kdf_upgrade(),
            )
        except keystore.KeystoreError as exc:
            raise ConfigError(str(exc)) from exc

        # The root directory is no longer created here. It carries integrity
        # tags now, and there is no master key at this point in startup --
        # the key belongs to a connection, not to the process. The first
        # authenticated session creates it; see DiscordSSHServer.
        ensure_host_key(SFTP_HOST_KEY_PATH)

        port = sftp_port()
        server = await asyncssh.listen(
            "",
            port,
            server_factory=DiscordSSHServer,
            # No VFS is passed: each connection builds its own around the
            # master key that its own login unwrapped.
            sftp_factory=DiscordSFTPServer,
            server_host_keys=[SFTP_HOST_KEY_PATH],
            # Password is the only authentication this server implements, so
            # GSSAPI/Kerberos is turned off rather than left at its default.
            # It is inert as things stand (asyncssh reports gss_available
            # False without the optional gssapi/sspi packages), but leaving it
            # on would mean a future dependency that happens to pull one in
            # silently adds an auth path that never goes through
            # validate_password.
            #
            # It also costs real time: working out the default gss_host calls
            # socket.getfqdn(), whose reverse lookup takes about a second here.
            gss_host=None,
        )
        logger.info("SFTP server listening on port %s", port)

        await _wait_for_shutdown()
    finally:
        if server is not None:
            await _drain(server)
        await discord_api.close()
        await db.close()


async def _wait_for_shutdown():
    """Block until the process is asked to stop.

    Without this the container's SIGTERM killed the process outright: whatever
    a client had written but not yet filled a chunk with was still sitting in
    a buffer, and nothing flushed it.
    """
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows' proactor loop has no add_signal_handler. SIGINT still
            # arrives there as KeyboardInterrupt, which __main__ handles.
            logger.debug("No signal handler for %s on this platform", sig)

    await stop.wait()
    logger.info("Shutdown requested; no longer accepting connections")


async def _drain(server, grace: float = SHUTDOWN_GRACE_SECONDS):
    """Stop listening, then let live sessions finish before closing them.

    Closing a connection makes asyncssh run its SFTP cleanup, which calls
    `close()` on every open handle and so flushes any buffered bytes to
    Discord. Waiting first is what gives an upload already in flight the
    chance to finish rather than being cut in half.
    """
    server.close()

    loop = asyncio.get_running_loop()
    deadline = loop.time() + grace
    while active_connections() and loop.time() < deadline:
        await asyncio.sleep(0.2)

    # Flush before closing, not as a side effect of closing. asyncssh does call
    # close() on every open handle when a session ends, but that cleanup is not
    # awaited by `conn.wait_closed()`, so the process exited while the upload
    # was still in flight and the client's last partial chunk was lost. Doing
    # it explicitly here makes the flush something this function waits for.
    # `DiscordFile.close()` is idempotent, so asyncssh's later call is a no-op.
    handles = open_files()
    if handles:
        logger.info("Flushing %d open file handle(s)", len(handles))
        for file_obj in handles:
            try:
                await _bounded(file_obj.close(), "a file handle to flush")
            except Exception:
                logger.exception("Failed to flush an open file during shutdown")

    remaining = active_connections()
    if remaining:
        logger.warning("Closing %d connection(s) still active after %.0fs",
                       len(remaining), grace)
    for conn in remaining:
        conn.close()

    # Every wait from here on is bounded. Blocking on a close that never
    # completes would hold the process open until the container kills it
    # outright -- the abrupt shutdown this whole function exists to avoid --
    # so one stuck connection must not be able to prevent the orderly exit.
    if remaining:
        await _bounded(
            asyncio.wait([asyncio.create_task(conn.wait_closed())
                          for conn in remaining],
                         timeout=CLOSE_TIMEOUT_SECONDS),
            "connections to close")

    await _bounded(server.wait_closed(), "the listener to close")
    logger.info("Shutdown complete")


async def _bounded(awaitable, what: str):
    try:
        await asyncio.wait_for(awaitable, CLOSE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("Gave up waiting %.0fs for %s", CLOSE_TIMEOUT_SECONDS, what)


if __name__ == "__main__":
    # Before anything opens a socket or a connection: a misconfigured server
    # that starts is far more dangerous than one that refuses to.
    try:
        validate()
    except ConfigError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    try:
        asyncio.run(start_server())
    except KeyboardInterrupt:
        logger.info("Shutting down")
    except ConfigError as exc:
        # Raised by the reachability check, which needs a running loop and so
        # cannot sit next to validate() above.
        logger.error("%s", exc)
        sys.exit(1)
    except (OSError, asyncssh.Error) as exc:
        logger.error("Error starting server: %s", exc)
        sys.exit(1)
