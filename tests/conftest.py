"""Shared fixtures.

The environment block below must stay above the `src` imports. `src.config`
reads the environment at import time and no longer has fallback defaults, so
importing anything under `src` with a bare environment leaves `AES_SECRET_KEY`
as `None` and the crypto layer fails with a confusing TypeError instead of a
configuration error.

Assigned unconditionally, not `setdefault`: the credentials here have to match
what the client fixture logs in with, so a developer who happens to export
`SFTP_USER` in their shell would otherwise get authentication failures across
the whole suite. `load_dotenv()` never overrides variables already present, so
a real `.env` cannot leak in either.
"""

import os

TEST_USER = "testuser"
TEST_PASSWORD = "testpass"

os.environ["AES_SECRET_KEY"] = "test-key-0123456789abcdef01234567"
os.environ["DISCORD_BOT_TOKEN"] = "test-token"
os.environ["DISCORD_USER_ID"] = "100000000000000000"
os.environ["SFTP_USER"] = TEST_USER
os.environ["SFTP_PASSWORD"] = TEST_PASSWORD

import asyncssh  # noqa: E402
import pytest  # noqa: E402

from src.db import Database  # noqa: E402
from tests.fakes import FakeDB, FakeDiscord  # noqa: E402

# Small enough that a 150-300KB payload still spans several Discord messages,
# which is where the chunk-boundary bugs live.
TEST_CHUNK_SIZE = 64 * 1024

SFTP_CREDENTIALS = {"username": TEST_USER, "password": TEST_PASSWORD}


@pytest.fixture(scope="session")
def host_key(tmp_path_factory):
    # Key generation costs about a second, so it is generated once and shared;
    # nothing in the suite depends on a per-test host identity.
    path = tmp_path_factory.mktemp("ssh") / "host_key"
    asyncssh.generate_private_key("ssh-rsa", key_size=2048).write_private_key(
        str(path)
    )
    return str(path)


@pytest.fixture
def fake_discord(monkeypatch):
    """Swap the module-level Discord client the VFS holds, and shrink chunks."""
    import src.vfs as vfs_mod

    fake = FakeDiscord()
    monkeypatch.setattr(vfs_mod, "discord_api", fake)
    monkeypatch.setattr(vfs_mod, "MAX_CHUNK_SIZE", TEST_CHUNK_SIZE)
    return fake


@pytest.fixture
def fake_db(monkeypatch):
    """A fresh empty node collection per test, so tests cannot leak into each
    other through the metadata store."""
    db = FakeDB()
    monkeypatch.setattr(Database, "db", db)
    return db


@pytest.fixture
async def vfs(fake_db, fake_discord):
    import src.vfs as vfs_mod

    instance = vfs_mod.DiscordVFS()
    await instance.ensure_root()
    return instance


@pytest.fixture
async def sftp(vfs, host_key):
    """A live SFTP client talking to a real asyncssh server over loopback.

    Deliberately the real protocol stack rather than direct VFS calls: the
    bugs this suite exists to catch were server-side method signatures that
    asyncssh never invoked, which no VFS-level test would have noticed.
    """
    import src.sftp as sftp_mod

    server = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_factory=sftp_mod.DiscordSSHServer,
        sftp_factory=lambda chan: sftp_mod.DiscordSFTPServer(chan, vfs),
        server_host_keys=[host_key],
    )
    port = server.get_addresses()[0][1]

    try:
        async with asyncssh.connect(
            "127.0.0.1", port, known_hosts=None, **SFTP_CREDENTIALS
        ) as conn:
            async with conn.start_sftp_client() as client:
                yield client
    finally:
        server.close()
        await server.wait_closed()
