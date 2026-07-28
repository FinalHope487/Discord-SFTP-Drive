import asyncio
import logging
import os
import stat
import sys

import asyncssh

from src.config import SFTP_HOST_KEY_PATH, ConfigError, sftp_port, validate
from src.db import db
from src.discord_api import ReachabilityError, discord_api
from src.sftp import DiscordSFTPServer, DiscordSSHServer
from src.vfs import DiscordVFS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# A private key readable by anyone who can reach the file is a key anyone can
# use to impersonate this server, and clients cannot tell the difference.
_HOST_KEY_MODE = 0o600


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

        vfs = DiscordVFS()
        await vfs.ensure_root()

        ensure_host_key(SFTP_HOST_KEY_PATH)

        port = sftp_port()
        server = await asyncssh.listen(
            "",
            port,
            server_factory=DiscordSSHServer,
            sftp_factory=lambda chan: DiscordSFTPServer(chan, vfs),
            server_host_keys=[SFTP_HOST_KEY_PATH],
        )
        logger.info("SFTP server listening on port %s", port)

        await asyncio.Event().wait()
    finally:
        if server is not None:
            server.close()
        await discord_api.close()
        await db.close()


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
