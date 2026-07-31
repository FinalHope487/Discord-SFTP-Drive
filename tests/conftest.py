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
TEST_PASSWORD = "testpassword-long-enough"

os.environ["DISCORD_BOT_TOKEN"] = "test-token"
os.environ["DISCORD_USER_ID"] = "100000000000000000"
os.environ["SFTP_USER"] = TEST_USER
os.environ["SFTP_PASSWORD"] = TEST_PASSWORD

# PBKDF2 at the production cost is ~200ms, and a login happens in almost every
# test in this suite. The stored key record carries the parameters it was made
# with, so a low count here is a property of these fixtures, not of the code
# under test.
os.environ["PBKDF2_ITERATIONS"] = "1000"

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
async def master_key(fake_db):
    """Bootstrap the keystore the way a first startup would, and open it.

    Real wrap/unwrap rather than a hard-coded key: the password path is what
    every connection in this suite exercises, so a fixture that bypassed it
    would leave it untested everywhere.
    """
    from src import keystore
    from src.config import pbkdf2_iterations

    await keystore.ensure_usable(TEST_PASSWORD, iterations=pbkdf2_iterations())
    return await keystore.open_master_key(TEST_PASSWORD)


@pytest.fixture
async def vfs(fake_db, fake_discord, master_key):
    import src.vfs as vfs_mod

    await vfs_mod.ensure_root()
    return vfs_mod.DiscordVFS(master_key)


@pytest.fixture
async def sftp_port(vfs, host_key):
    """A live server on a loopback port; the caller opens its own connections.

    Depends on `vfs` for its side effect: that fixture bootstraps the keystore,
    without which no login can produce a key.

    Deliberately the real protocol stack rather than direct VFS calls: the
    bugs this suite exists to catch were server-side method signatures that
    asyncssh never invoked, which no VFS-level test would have noticed.

    `gss_host=None` on both ends is what makes a per-test server affordable.
    Left at its default, asyncssh derives the GSS host name via
    `socket.getfqdn()`, and that reverse lookup costs about a second on this
    machine -- twice per test, which was the whole runtime of the suite. It
    matches production, where GSSAPI is off for the same reason.
    """
    import src.sftp as sftp_mod

    # Matches production: no VFS is injected, so the connection's own login
    # has to produce a working key or nothing in the test works.
    server = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_factory=sftp_mod.DiscordSSHServer,
        sftp_factory=sftp_mod.DiscordSFTPServer,
        server_host_keys=[host_key],
        gss_host=None,
    )
    try:
        yield server.get_addresses()[0][1]
    finally:
        server.close()
        await server.wait_closed()


def connect(port):
    """An SSH connection to the test server, as an async context manager."""
    return asyncssh.connect("127.0.0.1", port, known_hosts=None, gss_host=None,
                            **SFTP_CREDENTIALS)


@pytest.fixture
async def sftp(sftp_port):
    """A live SFTP client talking to a real asyncssh server over loopback.

    Deliberately the real protocol stack rather than direct VFS calls: the
    bugs this suite exists to catch were server-side method signatures that
    asyncssh never invoked, which no VFS-level test would have noticed.
    """
    async with connect(sftp_port) as conn:
        async with conn.start_sftp_client() as client:
            yield client
