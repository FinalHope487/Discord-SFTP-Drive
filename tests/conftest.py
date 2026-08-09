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

# Either KDF at its production cost is ~200ms, and a login happens in almost
# every test in this suite. The stored key record carries the parameters it was
# made with, so the trivial costs here are a property of these fixtures, not of
# the code under test.
#
# The *algorithm* is left at the production default on purpose -- only the cost
# is turned down. Pinning the suite to PBKDF2 while the server wraps with
# Argon2id would leave the path that actually runs in production untested
# everywhere except the handful of tests that ask for it by name.
os.environ["PBKDF2_ITERATIONS"] = "1000"
os.environ["ARGON2_TIME_COST"] = "1"
os.environ["ARGON2_MEMORY_KIB"] = "64"
os.environ["ARGON2_PARALLELISM"] = "1"

# The web tests talk plain HTTP to 127.0.0.1, and a correct client will not
# send a Secure cookie over that -- so leaving the production default on would
# not test stricter behaviour, it would test a client that never authenticates.
# The flag itself is asserted directly in `test_web.py` instead, under both
# settings, which is the part that could actually regress.
os.environ["WEB_COOKIE_SECURE"] = "0"

from pathlib import Path  # noqa: E402

import asyncssh  # noqa: E402
import pytest  # noqa: E402

from src.db import Database  # noqa: E402
from tests.fakes import FakeDB, FakeDiscord  # noqa: E402

# Small enough that a 150-300KB payload still spans several Discord messages,
# which is where the chunk-boundary bugs live.
TEST_CHUNK_SIZE = 64 * 1024

SFTP_CREDENTIALS = {"username": TEST_USER, "password": TEST_PASSWORD}


def pytest_addoption(parser):
    parser.addoption(
        "--db", action="store", default="fake", choices=("fake", "sqlite"),
        help="which metadata store the suite runs against: the in-memory fake "
             "(default) or the real SQLite backend the standalone build uses")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "mongo_only: exercises MongoDB-specific behaviour and is skipped "
        "under --db=sqlite")


def pytest_collection_modifyitems(config, items):
    """Skip the MongoDB-only tests when the suite is running on SQLite.

    Skipped rather than adapted. The three tests this touches drive the index
    *migration* path -- MongoDB refusing to change an index in place, and the
    recovery `db.py` performs when that happens -- by injecting
    `pymongo.errors.OperationFailure`. SQLite has no such behaviour to have,
    and a shim that pretended to would be testing the shim.

    Following `test_compose_coverage.py`: a skip says the question was not
    asked, while a pass would answer it wrongly.
    """
    if config.getoption("--db") != "sqlite":
        return
    skip = pytest.mark.skip(reason="MongoDB-specific; --db=sqlite is running")
    for item in items:
        if "mongo_only" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def isolated_directory_locks():
    """Empty the VFS's directory-lock registry between tests.

    `src.vfs._dir_locks` is module-level on purpose -- the coroutines it
    serialises are on different connections, and a connection gets its own
    `DiscordVFS`, so a per-instance lock would protect nothing. In the server
    that is exactly right: one process, one event loop, and the registry
    empties itself as soon as nobody wants an entry.

    Under pytest it is neither. Every test gets a fresh event loop, and an
    `asyncio.Lock` belongs to the loop it was awaited on -- the same hazard
    `src/db.py` documents for Motor. A test that ends while a task is still
    inside `_locked_dirs` (an aborted SSH connection whose cleanup has not
    finished, say) leaves that lock *held*, keyed by `root`, which is the same
    id the next test uses. Every test after it then blocks for ever on a lock
    whose owner belongs to a loop that no longer exists.

    Cleared before rather than after, so a test is protected from its
    predecessors rather than relying on each test to tidy up after itself.
    """
    import src.vfs as vfs_mod

    vfs_mod._dir_locks.clear()
    vfs_mod._dirs_held_by.clear()
    return None


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
async def fake_db(request, monkeypatch, tmp_path):
    """A fresh empty metadata store per test, so tests cannot leak into each
    other through it.

    Under `--db=sqlite` this is the real SQLite backend on a file in the
    test's own tmp directory, not a stand-in. Running the whole suite that way
    is what proves `src/sqlitedb.py` agrees with MongoDB about the things this
    project depends on, using the assertions that were written against
    MongoDB's behaviour rather than a second set written against my reading of
    it.

    The indexes are built for real in that mode, which makes it *stricter*
    than the fake: `tests/fakes.py` says outright that it does not enforce
    uniqueness, so nothing in this suite has ever proved that two live
    siblings cannot share a name. Here they cannot.
    """
    if request.config.getoption("--db") == "sqlite":
        from tests.sqlite_support import SqliteTestDB

        db = SqliteTestDB(str(tmp_path / "metadata.sqlite3"))
        monkeypatch.setattr(Database, "db", db)
        await Database._ensure_indexes()
        return db

    db = FakeDB()
    monkeypatch.setattr(Database, "db", db)
    return db


@pytest.fixture
async def account(fake_db):
    """The environment's account and its key, exactly as startup builds them.

    Both halves, in the order `main.start_server` does them: the row first,
    then the wrapped key under that row's id. A fixture that bootstrapped the
    keystore alone would leave every login in this suite failing at the
    account lookup, and one that stubbed the row would leave the password
    hashing untested everywhere.
    """
    from src import keystore, users
    from src.config import kdf_settings
    from src.vfs import ROOT_ID

    user = await users.sync_env_user(TEST_USER, TEST_PASSWORD, root_id=ROOT_ID)
    await keystore.ensure_usable(users.keystore_id(user), TEST_PASSWORD,
                                 settings=kdf_settings())
    return user


@pytest.fixture
async def master_key(account):
    """This account's master key, opened the way a login opens it.

    Real wrap/unwrap rather than a hard-coded key: the password path is what
    every connection in this suite exercises, so a fixture that bypassed it
    would leave it untested everywhere.
    """
    from src import keystore, users

    return await keystore.open_master_key(users.keystore_id(account),
                                          TEST_PASSWORD)


@pytest.fixture
async def vfs(fake_db, fake_discord, master_key, account):
    import src.vfs as vfs_mod

    instance = vfs_mod.DiscordVFS(master_key, account["root_id"])
    await instance.ensure_root()
    return instance


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


# ------------------------------------------------------------------ the browser
#
# The layer the user actually touches. Everything above this line drives the
# VFS or the HTTP API directly, which is exactly the shape of test `SOP.md`
# keeps recording as proving less than it looks like: it cannot see a button
# that was never wired to the endpoint it names.
#
# Only the outermost dependency is a stand-in. Real browser, real aiohttp
# process, real VFS, real (or SQLite) database, `fake_discord` at the edge.


def _client_dist():
    return Path(__file__).resolve().parent.parent / "client" / "app" / "dist"


@pytest.fixture
def built_client():
    """The built SPA, refusing to run against a stale bundle.

    Deliberately not building it here. A fixture that ran `npm run build`
    would hide a broken build inside a test failure ten seconds later, and
    would do it once per session on a machine that may not have npm on PATH.
    """
    dist = _client_dist()
    index = dist / "index.html"
    if not index.is_file():
        pytest.fail("client/app/dist 不存在。先跑：cd client/app && npm run build")

    newest_source = max(
        (p.stat().st_mtime for p in (dist.parent / "src").rglob("*") if p.is_file()),
        default=0,
    )
    newest_build = max(
        (p.stat().st_mtime for p in dist.rglob("*") if p.is_file()), default=0
    )
    if newest_source > newest_build:
        pytest.fail(
            "client/app/dist 比 client/app/src 舊，這些測試會打到過期的介面。"
            "先跑：cd client/app && npm run build"
        )
    return dist


@pytest.fixture
async def live_server(fake_db, fake_discord, account, built_client):
    """The whole application on a real port, serving the real bundle."""
    from aiohttp.test_utils import TestServer

    from src import web as web_mod

    server = TestServer(web_mod.create_app(static_dir=str(built_client)))
    await server.start_server()
    try:
        yield str(server.make_url("/")).rstrip("/")
    finally:
        await server.close()


@pytest.fixture
async def page(live_server):
    """A Chromium page pointed at that server, already on the sign-in screen.

    Function-scoped on purpose. A session-scoped browser would have to own a
    session-scoped event loop, and this suite pins every loop to one test
    (`asyncio_default_fixture_loop_scope = function`) because module-level
    asyncio state leaking across loops is the exact failure `SOP.md` has an
    entry for.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as driver:
        browser = await driver.chromium.launch()
        try:
            context = await browser.new_context(base_url=live_server)
            tab = await context.new_page()
            await tab.goto(live_server)
            yield tab
        finally:
            await browser.close()
