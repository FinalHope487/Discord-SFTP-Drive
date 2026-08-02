"""The HTTP API, driven over a real aiohttp server on loopback.

Deliberately the whole stack rather than handlers called directly. The three
things that make a cookie mean "this is the person who signed in" -- HttpOnly,
SameSite, and the CSRF token -- are all properties of headers and middleware
ordering, and a test that called handlers directly would assert none of them.

`aiohttp.test_utils` rather than `pytest-aiohttp`: aiohttp is already a
dependency, and the plugin is not, so this needs no new package.
"""

import hashlib
import os

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import web as web_mod
from src.webauth import DEVICE_THRESHOLD, LoginGuard
from src.websession import SessionStore
from tests.conftest import TEST_CHUNK_SIZE, TEST_PASSWORD, TEST_USER


def _client_for(app):
    # unsafe=True because the server is reached by IP address, and a cookie
    # jar refuses to store cookies for a bare IP otherwise.
    return TestClient(TestServer(app), cookie_jar=aiohttp.CookieJar(unsafe=True))


@pytest.fixture
async def app(fake_db, fake_discord, account):
    return web_mod.create_app()


@pytest.fixture
async def client(app):
    c = _client_for(app)
    await c.start_server()
    try:
        yield c
    finally:
        await c.close()


async def sign_in(client, **extra):
    body = {"username": TEST_USER, "password": TEST_PASSWORD, **extra}
    response = await client.post("/api/login", json=body)
    assert response.status == 200, await response.text()
    return await response.json()


def csrf(payload):
    return {web_mod.CSRF_HEADER: payload["csrf_token"]}


# ------------------------------------------------------------ signing in


async def test_health_needs_no_session(client):
    response = await client.get("/api/health")
    assert response.status == 200
    assert (await response.json())["ok"] is True


async def test_an_anonymous_session_reports_the_ceilings(client):
    # So the sign-in form can offer a shorter session without offering one the
    # server would silently clamp.
    response = await client.get("/api/session")
    body = await response.json()
    assert body["signed_in"] is False
    assert body["max_idle_seconds"] > 0
    assert body["max_absolute_seconds"] >= body["max_idle_seconds"]


async def test_signing_in_returns_a_csrf_token_and_sets_a_cookie(client):
    payload = await sign_in(client)
    assert payload["username"] == TEST_USER
    assert len(payload["csrf_token"]) > 20

    response = await client.get("/api/session")
    assert (await response.json())["signed_in"] is True


async def test_the_session_cookie_is_not_the_csrf_token(client):
    """They must be separate secrets.

    The cookie is HttpOnly so a script cannot read it; the CSRF token is
    handed to the page precisely so a script *can*. Making them the same value
    would mean the readable one was the credential.
    """
    payload = await sign_in(client)
    jar = {c.key: c.value for c in client.session.cookie_jar}
    assert jar[web_mod.SESSION_COOKIE] != payload["csrf_token"]


async def test_the_wrong_password_is_refused_without_a_session(client):
    response = await client.post("/api/login", json={
        "username": TEST_USER, "password": "not-the-password"})
    assert response.status == 401
    assert (await client.get("/api/session")).status == 200
    assert (await (await client.get("/api/session")).json())["signed_in"] is False


async def test_an_unknown_username_is_refused_the_same_way(client):
    response = await client.post("/api/login", json={
        "username": "nobody", "password": TEST_PASSWORD})
    assert response.status == 401
    # The message must not distinguish the two, or it hands back exactly what
    # the dummy verification in `users.authenticate` exists to withhold.
    assert "password" in (await response.json())["error"]


async def test_signing_out_ends_the_session(client):
    payload = await sign_in(client)
    assert (await client.post("/api/logout", headers=csrf(payload))).status == 200
    assert (await client.get("/api/files?path=/")).status == 401


# ----------------------------------------------------- cookie attributes


@pytest.mark.parametrize("secure", [True, False])
async def test_the_session_cookie_carries_its_protections(app, monkeypatch, secure):
    monkeypatch.setattr(web_mod, "web_cookie_secure", lambda: secure)
    client = _client_for(app)
    await client.start_server()
    try:
        response = await client.post("/api/login", json={
            "username": TEST_USER, "password": TEST_PASSWORD})
        header = response.headers.getall("Set-Cookie")
        session_cookie = next(h for h in header
                              if h.startswith(web_mod.SESSION_COOKIE + "="))
    finally:
        await client.close()

    assert "HttpOnly" in session_cookie, "a script could read the session id"
    assert "SameSite=Strict" in session_cookie, "another origin could send it"
    assert ("Secure" in session_cookie) is secure


async def test_a_device_cookie_is_issued_even_when_the_sign_in_fails(client):
    """Otherwise the precise half of the lockout never engages.

    A browser that only ever fails would never receive an id, so every attempt
    would look like a brand-new device -- and the per-device lockout would be
    aimed at a device that never repeats.
    """
    response = await client.post("/api/login", json={
        "username": TEST_USER, "password": "wrong"})
    assert response.status == 401
    assert any(h.startswith(web_mod.DEVICE_COOKIE + "=")
               for h in response.headers.getall("Set-Cookie"))


# ------------------------------------------------------------------ CSRF


async def test_a_mutating_request_without_a_csrf_token_is_refused(client):
    await sign_in(client)
    response = await client.post("/api/dir", json={"path": "/nope"})
    assert response.status == 403


async def test_a_mutating_request_with_the_wrong_csrf_token_is_refused(client):
    await sign_in(client)
    response = await client.post("/api/dir", json={"path": "/nope"},
                                 headers={web_mod.CSRF_HEADER: "guessed"})
    assert response.status == 403


async def test_reads_do_not_need_a_csrf_token(client):
    # The token defends against another origin *causing* an action. A read
    # cannot be caused into changing anything, and requiring one would only
    # make the API harder to use.
    await sign_in(client)
    assert (await client.get("/api/files?path=/")).status == 200


async def test_no_session_means_no_access_at_all(client):
    assert (await client.get("/api/files?path=/")).status == 401
    assert (await client.get("/api/file?path=/x")).status == 401
    assert (await client.post("/api/dir", json={"path": "/x"})).status == 401


# -------------------------------------------------------------- lifetimes


async def test_a_client_may_ask_for_a_shorter_session(client):
    payload = await sign_in(client, idle_seconds=30, absolute_seconds=60)
    assert payload["idle_seconds"] == 30
    assert payload["absolute_seconds"] == 60


async def test_a_client_cannot_ask_for_a_longer_one(client):
    """The ceiling is the server's to set.

    A browser able to extend its own session hands that control to whoever
    stole the cookie, so this is clamped rather than honoured.
    """
    payload = await sign_in(client, idle_seconds=999999,
                            absolute_seconds=999999)
    anonymous = await (await client.get("/api/session")).json()
    assert payload["idle_seconds"] <= anonymous["idle_seconds"]
    assert payload["absolute_seconds"] <= anonymous["absolute_seconds"]
    assert payload["absolute_seconds"] < 999999


# ---------------------------------------------------------------- lockout


async def test_repeated_failures_lock_the_source_out(app):
    client = _client_for(app)
    await client.start_server()
    try:
        for _ in range(DEVICE_THRESHOLD):
            await client.post("/api/login", json={
                "username": TEST_USER, "password": "wrong"})

        response = await client.post("/api/login", json={
            "username": TEST_USER, "password": "wrong"})
        assert response.status == 429
        assert int(response.headers["Retry-After"]) > 0

        # And the correct password is refused too while the lockout holds --
        # otherwise the lockout would be trivially bypassed by guessing right.
        response = await client.post("/api/login", json={
            "username": TEST_USER, "password": TEST_PASSWORD})
        assert response.status == 429
    finally:
        await client.close()


async def test_a_locked_out_source_never_locks_the_account(app):
    """The whole point of keying on the source.

    A second browser must still be able to sign in, or a lockout becomes a
    denial of service anyone can aim at the owner.
    """
    # Two apps sharing one guard and one store: two browsers against one
    # server, without starting the same Application twice.
    guard = LoginGuard(concurrency=2, queue=16)
    sessions = SessionStore(idle_ceiling=600, absolute_ceiling=7200)

    attacker = _client_for(web_mod.create_app(sessions=sessions, guard=guard))
    await attacker.start_server()
    try:
        for _ in range(DEVICE_THRESHOLD + 1):
            await attacker.post("/api/login", json={
                "username": TEST_USER, "password": "wrong"})
        assert (await attacker.post("/api/login", json={
            "username": TEST_USER, "password": TEST_PASSWORD})).status == 429
    finally:
        await attacker.close()

    # A different device, same account: unaffected. (Same address here, which
    # is the stricter case -- only the device id distinguishes them.)
    owner = _client_for(web_mod.create_app(sessions=sessions, guard=guard))
    await owner.start_server()
    try:
        response = await owner.post("/api/login", json={
            "username": TEST_USER, "password": TEST_PASSWORD})
        assert response.status == 200, await response.text()
    finally:
        await owner.close()


# ------------------------------------------------------------- file access


async def test_the_full_file_lifecycle(client):
    payload = await sign_in(client)
    headers = csrf(payload)

    assert (await client.post("/api/dir", json={"path": "/docs"},
                              headers=headers)).status == 201

    data = os.urandom(TEST_CHUNK_SIZE * 2 + 1234)     # spans three chunks
    response = await client.put("/api/file?path=/docs/report.bin", data=data,
                                headers=headers)
    assert response.status == 201, await response.text()
    assert (await response.json())["size"] == len(data)

    listing = await (await client.get("/api/files?path=/docs")).json()
    assert [e["name"] for e in listing["entries"]] == ["report.bin"]

    digest = hashlib.sha256(data).hexdigest()
    response = await client.get("/api/file?path=/docs/report.bin")
    assert response.status == 200
    assert hashlib.sha256(await response.read()).hexdigest() == digest

    assert (await client.post("/api/rename",
                              json={"from": "/docs/report.bin",
                                    "to": "/docs/final.bin"},
                              headers=headers)).status == 200

    response = await client.get("/api/file?path=/docs/final.bin")
    assert hashlib.sha256(await response.read()).hexdigest() == digest

    assert (await client.delete("/api/file?path=/docs/final.bin",
                                headers=headers)).status == 200
    assert (await client.delete("/api/dir?path=/docs",
                                headers=headers)).status == 200

    listing = await (await client.get("/api/files?path=/")).json()
    assert listing["entries"] == []


async def test_directories_sort_before_files(client):
    payload = await sign_in(client)
    headers = csrf(payload)

    await client.post("/api/dir", json={"path": "/zzz"}, headers=headers)
    await client.put("/api/file?path=/aaa.txt", data=b"x", headers=headers)

    listing = await (await client.get("/api/files?path=/")).json()
    assert [e["name"] for e in listing["entries"]] == ["zzz", "aaa.txt"]


async def test_a_missing_file_is_404(client):
    await sign_in(client)
    assert (await client.get("/api/file?path=/nope.bin")).status == 404
    assert (await client.get("/api/stat?path=/nope.bin")).status == 404


async def test_renaming_onto_an_existing_name_is_refused(client):
    """No silent clobber: a mis-drop in a file manager must not lose data."""
    payload = await sign_in(client)
    headers = csrf(payload)
    await client.put("/api/file?path=/a.txt", data=b"a", headers=headers)
    await client.put("/api/file?path=/b.txt", data=b"b", headers=headers)

    response = await client.post("/api/rename",
                                 json={"from": "/a.txt", "to": "/b.txt"},
                                 headers=headers)
    assert response.status == 409


async def test_removing_a_non_empty_directory_is_refused(client):
    payload = await sign_in(client)
    headers = csrf(payload)
    await client.post("/api/dir", json={"path": "/full"}, headers=headers)
    await client.put("/api/file?path=/full/x.txt", data=b"x", headers=headers)

    assert (await client.delete("/api/dir?path=/full",
                                headers=headers)).status == 409


async def test_a_path_cannot_climb_out_of_the_tree(client):
    """`..` is resolved, not rejected, and it cannot go above the root.

    The same `normalize_path` the SFTP layer uses, for the same reason: a
    client sending `..` is ordinary, and what matters is where it lands.
    """
    payload = await sign_in(client)
    headers = csrf(payload)
    await client.put("/api/file?path=/inside.txt", data=b"x", headers=headers)

    response = await client.get("/api/stat?path=/../../../inside.txt")
    assert response.status == 200
    assert (await response.json())["path"] == "/inside.txt"


async def test_an_empty_file_round_trips(client):
    payload = await sign_in(client)
    headers = csrf(payload)
    assert (await client.put("/api/file?path=/empty.bin", data=b"",
                             headers=headers)).status == 201

    response = await client.get("/api/file?path=/empty.bin")
    assert response.status == 200
    assert await response.read() == b""


async def test_uploading_over_an_existing_file_replaces_it(client):
    payload = await sign_in(client)
    headers = csrf(payload)
    await client.put("/api/file?path=/x.bin", data=b"first-version",
                     headers=headers)
    await client.put("/api/file?path=/x.bin", data=b"second", headers=headers)

    response = await client.get("/api/file?path=/x.bin")
    assert await response.read() == b"second"


async def test_a_download_names_the_file_even_when_the_name_is_not_ascii(client):
    """`filename*` carries the real name; the plain one is a safe fallback.

    A header is latin-1 on the wire, so a bare `filename="報告.bin"` is either
    mangled or an encoding error depending on the client. Sending both is what
    makes the name survive in browsers that read the extended form without
    breaking the ones that do not.
    """
    payload = await sign_in(client)
    headers = csrf(payload)
    name = "報告.bin"
    assert (await client.put("/api/file", params={"path": f"/{name}"},
                             data=b"x", headers=headers)).status == 201

    response = await client.get("/api/file", params={"path": f"/{name}"})
    assert response.status == 200
    disposition = response.headers["Content-Disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition
    assert "報告" not in disposition, "the raw name went into a latin-1 header"
