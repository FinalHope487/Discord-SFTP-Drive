import asyncio
import logging
import os
import sys

import asyncssh

from src.config import SFTP_HOST_KEY_PATH, ConfigError, sftp_port, validate
from src.db import db
from src.discord_api import discord_api
from src.sftp import DiscordSFTPServer, DiscordSSHServer
from src.vfs import DiscordVFS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def ensure_host_key(path: str):
    if os.path.exists(path):
        return
    logger.info("Generating new host key at %s", path)
    key = asyncssh.generate_private_key("ssh-rsa", key_size=3072)
    key.write_private_key(path)


async def start_server():
    # Everything below runs on this one loop for the process's lifetime.
    # Motor and aiohttp both bind to the running loop, so there is exactly one.
    await db.connect()
    logger.info("Connected to MongoDB")

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

    try:
        await asyncio.Event().wait()
    finally:
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
    except (OSError, asyncssh.Error) as exc:
        logger.error("Error starting server: %s", exc)
