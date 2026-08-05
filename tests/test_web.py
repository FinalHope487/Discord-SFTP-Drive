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


# ------------------------------------------------------------------- trash


async def _trash_of(client):
    response = await client.get("/api/trash")
    assert response.status == 200, await response.text()
    return await response.json()


async def test_deleting_a_file_puts_it_in_the_trash(client):
    payload = await sign_in(client)
    headers = csrf(payload)

    await client.put("/api/file?path=/notes.txt", data=b"hello", headers=headers)
    assert (await client.delete("/api/file?path=/notes.txt",
                                headers=headers)).status == 200

    listing = await (await client.get("/api/files?path=/")).json()
    assert listing["entries"] == []

    body = await _trash_of(client)
    [entry] = body["entries"]
    assert entry["original_path"] == "/notes.txt"
    assert entry["trashed_at"] > 0
    assert body["retention_seconds"] > 0


async def test_restoring_from_the_trash_brings_the_bytes_back(client):
    payload = await sign_in(client)
    headers = csrf(payload)

    await client.put("/api/file?path=/notes.txt", data=b"hello", headers=headers)
    await client.delete("/api/file?path=/notes.txt", headers=headers)
    [entry] = (await _trash_of(client))["entries"]

    response = await client.post("/api/trash/restore", json={"id": entry["id"]},
                                 headers=headers)
    assert response.status == 200, await response.text()
    assert (await response.json())["path"] == "/notes.txt"

    assert await (await client.get("/api/file?path=/notes.txt")).read() == b"hello"
    assert (await _trash_of(client))["entries"] == []


async def test_a_restore_onto_a_taken_name_is_a_conflict_the_client_resolves(client):
    """409 first, then whichever of the three answers the dialog got."""
    payload = await sign_in(client)
    headers = csrf(payload)

    await client.put("/api/file?path=/notes.txt", data=b"old", headers=headers)
    await client.delete("/api/file?path=/notes.txt", headers=headers)
    await client.put("/api/file?path=/notes.txt", data=b"new", headers=headers)
    [entry] = (await _trash_of(client))["entries"]

    refused = await client.post("/api/trash/restore", json={"id": entry["id"]},
                                headers=headers)
    assert refused.status == 409

    kept = await client.post("/api/trash/restore",
                             json={"id": entry["id"], "on_conflict": "keep_both"},
                             headers=headers)
    assert kept.status == 200
    assert (await kept.json())["path"] == "/notes (2).txt"

    listing = await (await client.get("/api/files?path=/")).json()
    assert sorted(e["name"] for e in listing["entries"]) \
        == ["notes (2).txt", "notes.txt"]


async def test_an_unknown_conflict_choice_is_refused(client):
    payload = await sign_in(client)
    response = await client.post("/api/trash/restore",
                                 json={"id": "whatever", "on_conflict": "clobber"},
                                 headers=csrf(payload))
    assert response.status == 400


async def test_purging_releases_the_attachments(client, fake_discord):
    payload = await sign_in(client)
    headers = csrf(payload)

    await client.put("/api/file?path=/big.bin", data=os.urandom(TEST_CHUNK_SIZE + 9),
                     headers=headers)
    await client.delete("/api/file?path=/big.bin", headers=headers)
    assert fake_discord.store != {}, "the trash must keep the chunks"

    [entry] = (await _trash_of(client))["entries"]
    response = await client.delete(f"/api/trash?id={entry['id']}", headers=headers)
    assert response.status == 200
    assert (await response.json())["attachments"] >= 1
    assert fake_discord.store == {}


async def test_purging_without_an_id_is_refused_rather_than_emptying_the_bin(client):
    payload = await sign_in(client)
    headers = csrf(payload)

    await client.put("/api/file?path=/notes.txt", data=b"x", headers=headers)
    await client.delete("/api/file?path=/notes.txt", headers=headers)

    assert (await client.delete("/api/trash", headers=headers)).status == 400
    assert len((await _trash_of(client))["entries"]) == 1


async def test_emptying_the_trash_takes_everything(client):
    payload = await sign_in(client)
    headers = csrf(payload)

    for name in ("a.txt", "b.txt"):
        await client.put(f"/api/file?path=/{name}", data=b"x", headers=headers)
        await client.delete(f"/api/file?path=/{name}", headers=headers)

    response = await client.post("/api/trash/empty", headers=headers)
    assert response.status == 200
    assert (await response.json())["purged"] == 2
    assert (await _trash_of(client))["entries"] == []


async def test_a_directory_with_things_in_it_needs_the_recursive_flag(client):
    payload = await sign_in(client)
    headers = csrf(payload)

    await client.post("/api/dir", json={"path": "/project"}, headers=headers)
    await client.put("/api/file?path=/project/a.txt", data=b"x", headers=headers)

    assert (await client.delete("/api/dir?path=/project",
                                headers=headers)).status == 409

    assert (await client.delete("/api/dir?path=/project&recursive=true",
                                headers=headers)).status == 200
    listing = await (await client.get("/api/files?path=/")).json()
    assert listing["entries"] == []


async def test_the_trash_needs_a_session_and_a_csrf_token(client):
    payload = await sign_in(client)
    headers = csrf(payload)
    await client.put("/api/file?path=/notes.txt", data=b"x", headers=headers)
    await client.delete("/api/file?path=/notes.txt", headers=headers)
    [entry] = (await _trash_of(client))["entries"]

    # Mutating the trash without the token is refused, exactly like every
    # other state change. Emptying somebody's bin from another origin would be
    # the most destructive thing this API could be tricked into doing.
    assert (await client.post("/api/trash/restore",
                              json={"id": entry["id"]})).status == 403
    assert (await client.delete(f"/api/trash?id={entry['id']}")).status == 403
    assert (await client.post("/api/trash/empty")).status == 403

    await client.post("/api/logout", headers=headers)
    assert (await client.get("/api/trash")).status == 401


# ------------------------------------------------------------------ search


async def _tree_for_search(client, headers):
    """A small tree with matches at three depths and one decoy."""
    await client.post("/api/dir", json={"path": "/docs"}, headers=headers)
    await client.post("/api/dir", json={"path": "/docs/2026"}, headers=headers)
    await client.post("/api/dir", json={"path": "/other"}, headers=headers)
    await client.put("/api/file?path=/report.txt", data=b"a", headers=headers)
    await client.put("/api/file?path=/docs/report-draft.txt", data=b"bb",
                     headers=headers)
    await client.put("/api/file?path=/docs/2026/REPORT-final.txt", data=b"ccc",
                     headers=headers)
    await client.put("/api/file?path=/other/unrelated.txt", data=b"d",
                     headers=headers)


async def test_search_walks_the_whole_tree_and_returns_full_paths(client):
    payload = await sign_in(client)
    await _tree_for_search(client, csrf(payload))

    body = await (await client.get("/api/search?q=report")).json()
    paths = {r["path"] for r in body["results"]}

    # Every depth, and the path is absolute -- the UI shows where a hit lives,
    # so a bare filename would make two files called the same thing
    # indistinguishable in the one view where telling them apart is the point.
    assert paths == {"/report.txt", "/docs/report-draft.txt",
                     "/docs/2026/REPORT-final.txt"}
    assert body["truncated"] is False


async def test_search_ignores_case(client):
    payload = await sign_in(client)
    await _tree_for_search(client, csrf(payload))

    upper = await (await client.get("/api/search?q=REPORT")).json()
    lower = await (await client.get("/api/search?q=report")).json()
    assert {r["path"] for r in upper["results"]} == \
           {r["path"] for r in lower["results"]}


async def test_search_reports_directories_as_well_as_files(client):
    payload = await sign_in(client)
    await _tree_for_search(client, csrf(payload))

    body = await (await client.get("/api/search?q=docs")).json()
    [hit] = body["results"]
    assert hit["path"] == "/docs"
    assert hit["is_dir"] is True


async def test_search_says_when_it_stopped_rather_than_looking_complete(client):
    payload = await sign_in(client)
    await _tree_for_search(client, csrf(payload))

    body = await (await client.get("/api/search?q=report&limit=2")).json()
    # Two results and an honest flag. A short list with no flag is the failure
    # mode that matters: it reads as "there are only two", and a search
    # trusted for absence is a search that hides a file.
    assert len(body["results"]) == 2
    assert body["truncated"] is True


async def test_search_clamps_a_limit_above_the_server_ceiling(client):
    payload = await sign_in(client)
    await _tree_for_search(client, csrf(payload))

    body = await (await client.get("/api/search?q=report&limit=999999")).json()
    # Honoured downward, clamped upward -- the same asymmetry the session
    # lifetimes have, and for the same reason: the cost is not the caller's.
    assert len(body["results"]) <= web_mod._SEARCH_LIMIT
    assert body["truncated"] is False


async def test_search_does_not_find_trashed_files(client):
    payload = await sign_in(client)
    headers = csrf(payload)
    await _tree_for_search(client, headers)
    await client.delete("/api/file?path=/report.txt", headers=headers)

    body = await (await client.get("/api/search?q=report")).json()
    paths = {r["path"] for r in body["results"]}
    # `entries_of` filters the trash out, and search goes through it rather
    # than around it. A hit that cannot be opened would be worse than no hit.
    assert "/report.txt" not in paths
    assert "/docs/report-draft.txt" in paths


async def test_search_without_a_query_is_refused(client):
    await sign_in(client)
    assert (await client.get("/api/search")).status == 400
    assert (await client.get("/api/search?q=   ")).status == 400


async def test_search_needs_a_session(client):
    assert (await client.get("/api/search?q=x")).status == 401


# ------------------------------------------------- sessions and connections


async def test_the_session_body_carries_both_deadlines(client):
    payload = await sign_in(client)

    # Both, separately. A client shown only the nearer one cannot tell "go and
    # make coffee, you will have to sign in again" from "this ends at 4pm
    # whatever you do", and those call for different behaviour.
    assert payload["idle_expires_in"] > 0
    assert payload["absolute_expires_in"] >= payload["idle_expires_in"]
    assert payload["expires_in"] == min(payload["idle_expires_in"],
                                        payload["absolute_expires_in"])


async def test_a_second_sign_in_on_one_account_is_visible_to_the_first(app):
    first, second = _client_for(app), _client_for(app)
    await first.start_server()
    await second.start_server()
    try:
        payload = await sign_in(first)
        assert payload["connections"] == 1

        await sign_in(second)
        # Nothing stopped two people sharing this account before; what is new
        # is that the owner can see it rather than infer it from a file
        # appearing.
        refreshed = await (await first.get("/api/session")).json()
        assert refreshed["connections"] == 2
    finally:
        await first.close()
        await second.close()


async def test_revoking_other_sessions_keeps_the_caller_signed_in(app):
    mine, theirs = _client_for(app), _client_for(app)
    await mine.start_server()
    await theirs.start_server()
    try:
        payload = await sign_in(mine)
        await sign_in(theirs)

        body = await (await mine.post("/api/sessions/revoke-others",
                                      headers=csrf(payload))).json()
        assert body["signed_out"] == 1
        assert body["connections"] == 1

        # The other one is over; mine is not. A control that signs the caller
        # out too is a control nobody dares press with an intruder on the line.
        assert (await theirs.get("/api/files?path=/")).status == 401
        assert (await mine.get("/api/files?path=/")).status == 200
    finally:
        await mine.close()
        await theirs.close()


async def test_revoking_other_sessions_needs_a_csrf_token(client):
    await sign_in(client)
    assert (await client.post("/api/sessions/revoke-others")).status == 403


# ----------------------------------------------------------- static client


@pytest.fixture
def built_client(tmp_path):
    bundle = tmp_path / "bundle"
    (bundle / "assets").mkdir(parents=True)
    (bundle / "index.html").write_text("<title>app</title>", encoding="utf-8")
    (bundle / "assets" / "app-abc123.js").write_text("//js", encoding="utf-8")
    return bundle


@pytest.fixture
async def static_client(fake_db, fake_discord, account, built_client):
    c = _client_for(web_mod.create_app(static_dir=str(built_client)))
    await c.start_server()
    try:
        yield c
    finally:
        await c.close()


async def test_the_client_is_served_without_a_session(static_client):
    # The sign-in screen is part of the bundle, so requiring a session to
    # fetch it would be a door that can only be opened from inside.
    response = await static_client.get("/")
    assert response.status == 200
    assert "<title>app</title>" in await response.text()


async def test_a_client_side_route_returns_the_app_not_a_404(static_client):
    response = await static_client.get("/trash")
    assert response.status == 200
    assert "<title>app</title>" in await response.text()


async def test_an_unknown_api_path_is_json_not_the_html_shell(static_client):
    # The catch-all is registered last and would otherwise swallow these. A
    # client that asked for JSON and got 200 plus HTML reports a parse error,
    # which is a far worse diagnostic than the status it was owed.
    anonymous = await static_client.get("/api/nope")
    # 401 rather than 404 while signed out, because auth runs first -- which
    # also means an unauthenticated caller cannot map the API by watching
    # which paths come back as missing.
    assert anonymous.status == 401
    assert anonymous.content_type == "application/json"

    await sign_in(static_client)
    signed_in = await static_client.get("/api/nope")
    assert signed_in.status == 404
    assert signed_in.content_type == "application/json"


async def test_hashed_assets_are_immutable_and_the_shell_is_not(static_client):
    asset = await static_client.get("/assets/app-abc123.js")
    assert "immutable" in asset.headers["Cache-Control"]
    # index.html is the one file whose bytes change while its name does not,
    # so a cached copy pins the browser to assets the next build deleted.
    shell = await static_client.get("/")
    assert shell.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("attempt", [
    "/../../../etc/passwd",
    "/assets/../../secret.txt",
    "/%2e%2e%2f%2e%2e%2fsecret.txt",
])
async def test_a_static_path_cannot_climb_out_of_the_bundle(static_client,
                                                            attempt,
                                                            built_client):
    (built_client.parent / "secret.txt").write_text("leaked", encoding="utf-8")
    response = await static_client.get(attempt)
    # Either refused outright or answered with the shell; never the file.
    assert "leaked" not in await response.text()


async def test_an_unbuilt_client_says_so_instead_of_crashing(
        fake_db, fake_discord, account, tmp_path):
    empty = tmp_path / "not-built"
    empty.mkdir()
    c = _client_for(web_mod.create_app(static_dir=str(empty)))
    await c.start_server()
    try:
        response = await c.get("/")
        assert response.status == 503
        assert "npm run build" in await response.text()
        # The API is untouched by a missing frontend. Refusing to serve it
        # would turn a cosmetic problem into an outage for the SFTP side too.
        assert (await c.get("/api/health")).status == 200
    finally:
        await c.close()
