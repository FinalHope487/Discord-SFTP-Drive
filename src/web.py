"""The HTTP API, served from the SFTP server's own process.

Same process on purpose, and not for convenience. Handles coordinate through
`vfs._node_versions`, a dict that lives in the process; a separate service
against the same MongoDB is the second replica `README.md` forbids, and it
would serve a stale chunk layout with no error and no log line. Sharing the
process makes that whole class of bug impossible rather than unlikely.

Authentication is a cookie holding an opaque session id -- see `websession`
for why the key itself is never in it. The cookie is HttpOnly, so a script
that gets into the page cannot read it; it is SameSite=Strict, so another
origin cannot cause the browser to send it; and every state-changing request
must also carry a CSRF token that only this origin's own JavaScript can see.
Those three together are what make "the cookie is present" mean "this request
came from the person who signed in".
"""

import asyncio
import hmac
import json
import logging
import secrets
import time
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from src import keystore, users
from src.config import (trash_settings, web_cookie_secure, web_login_limits,
                        web_session_limits, web_static_dir)
from src.crypto import IntegrityError, KeyUnwrapError
from src.vfs import (
    AlreadyExists,
    DiscordVFS,
    IsADirectory,
    NotADirectory,
    NotEmpty,
    NotFound,
    Unsupported,
    VFSError,
    normalize_path,
)
from src.webauth import LoginBusy, LoginGuard, LoginLocked
from src.websession import SessionStore, sweeper

logger = logging.getLogger(__name__)

SESSION_COOKIE = "dd_session"
DEVICE_COOKIE = "dd_device"
CSRF_HEADER = "X-CSRF-Token"

# A year. The device id is not a credential -- it only makes a lockout precise
# enough not to punish a whole household for one bad browser -- so it lives
# long enough to be useful and is never treated as proof of anything.
DEVICE_COOKIE_MAX_AGE = 365 * 24 * 3600

# Bodies move in pieces this size, so a 9MB chunk is assembled from many of
# them rather than a whole upload being held in memory at once.
STREAM_CHUNK = 256 * 1024

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# Typed keys rather than bare strings: aiohttp deprecated the latter, and this
# process turns `src.*` DeprecationWarnings into errors precisely so they get
# dealt with rather than accumulated.
SESSIONS = web.AppKey("sessions", SessionStore)
GUARD = web.AppKey("guard", LoginGuard)

# Anything the app needs to record *after* startup lives in here. The
# Application itself is frozen once it starts, so the sweeper task cannot be
# stored on it directly -- mutating a dict it already holds is the supported
# way to keep per-run state.
STATE = web.AppKey("state", dict)

_ERROR_CLASSES = {
    400: web.HTTPBadRequest,
    401: web.HTTPUnauthorized,
    403: web.HTTPForbidden,
    404: web.HTTPNotFound,
    409: web.HTTPConflict,
    429: web.HTTPTooManyRequests,
    500: web.HTTPInternalServerError,
    501: web.HTTPNotImplemented,
    503: web.HTTPServiceUnavailable,
}


def _error(status: int, message: str, **extra) -> web.HTTPException:
    """An aiohttp exception whose body is JSON, like every other response.

    `extra` carries a machine-readable `code` where the client has to act
    differently rather than just say something differently -- an integrity
    failure is not a 500 to retry past, and telling them apart by matching on
    prose is how a retry loop ends up hammering a tampered file.
    """
    cls = _ERROR_CLASSES.get(status, web.HTTPBadRequest)
    return cls(text=json.dumps({"error": message, **extra}),
               content_type="application/json")


def _status_for(exc: VFSError) -> int:
    if isinstance(exc, NotFound):
        return 404
    if isinstance(exc, (AlreadyExists, NotEmpty)):
        return 409
    if isinstance(exc, (IsADirectory, NotADirectory)):
        return 400
    if isinstance(exc, Unsupported):
        return 501
    return 400


# -------------------------------------------------------------- middlewares


@web.middleware
async def error_middleware(request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except IntegrityError as exc:
        # Logged as the security event it is rather than as an unhandled bug,
        # and no bytes are served: returning them anyway would defeat the
        # check entirely. Same reasoning as the SFTP layer's `_translate`.
        logger.error("Integrity check failed in %s %s: %s",
                     request.method, request.path, exc)
        raise _error(500, "integrity check failed",
                     code="integrity_failure",
                     path=request.query.get("path") or "",
                     detail=str(exc)) from exc
    except VFSError as exc:
        raise _error(_status_for(exc),
                     str(exc) or exc.__class__.__name__) from exc
    except Exception as exc:
        logger.exception("Unhandled error in %s %s", request.method, request.path)
        raise _error(500, "internal error") from exc


def _cookie_kwargs(max_age=None):
    return {
        "httponly": True,
        "samesite": "Strict",
        "secure": web_cookie_secure(),
        "path": "/",
        "max_age": max_age,
    }


@web.middleware
async def device_middleware(request, handler):
    """Make sure every browser carries a stable device id.

    Not a credential and never treated as one -- anybody can clear it and
    arrive as somebody new. It exists so a lockout can be aimed at one
    misbehaving browser instead of at every browser sharing an address. The
    address-level counter in `webauth` is what a cookie-clearing attacker runs
    into, and clearing it makes that arrive sooner rather than later.

    The cookie is set on failures too. Issuing it only with a successful
    response would mean a browser that keeps failing never gets one, so every
    attempt would look like a brand-new device and the precise half of the
    lockout would never engage against the one source it is meant for.
    """
    device_id = request.cookies.get(DEVICE_COOKIE)
    issued = None
    if not device_id:
        device_id = secrets.token_urlsafe(16)
        issued = device_id
    request["device_id"] = device_id

    try:
        response = await handler(request)
    except web.HTTPException as exc:
        if issued is not None:
            exc.set_cookie(DEVICE_COOKIE, issued,
                           **_cookie_kwargs(DEVICE_COOKIE_MAX_AGE))
        raise

    if issued is not None:
        response.set_cookie(DEVICE_COOKIE, issued,
                            **_cookie_kwargs(DEVICE_COOKIE_MAX_AGE))
    return response


_PUBLIC_PATHS = {"/api/login", "/api/session", "/api/health"}


@web.middleware
async def auth_middleware(request, handler):
    store = request.app[SESSIONS]
    session = store.get(request.cookies.get(SESSION_COOKIE))
    request["session"] = session

    # Everything outside `/api/` is the static client, and the sign-in screen
    # has to load before there is a session to check it against. The entire
    # protected surface lives under one prefix precisely so this can be a
    # prefix test rather than a list somebody forgets to extend.
    if request.path in _PUBLIC_PATHS or not request.path.startswith("/api/"):
        return await handler(request)

    if session is None:
        raise _error(401, "not signed in")

    if request.method in _MUTATING:
        supplied = request.headers.get(CSRF_HEADER, "")
        # Constant time: a byte-at-a-time comparison leaks how much of a
        # guessed token was right, which is enough to finish guessing it.
        if not hmac.compare_digest(supplied.encode(),
                                   session.csrf_token.encode()):
            raise _error(403, "missing or wrong CSRF token")

    return await handler(request)


# ------------------------------------------------------------ session shape


def _session_body(session, store) -> dict:
    now = time.monotonic()
    return {
        "signed_in": True,
        "username": session.username,
        "csrf_token": session.csrf_token,
        "expires_in": session.expires_in(now),
        # Both deadlines, separately. The client displays the countdown from
        # these and never from a timer of its own: a local timer drifts, and
        # it drifts towards "you still have time" -- which is the direction
        # that loses an upload rather than the direction that annoys somebody.
        "idle_expires_in": session.idle_expires_in(now),
        "absolute_expires_in": session.absolute_expires_in(now),
        "idle_seconds": session.idle_seconds,
        "absolute_seconds": session.absolute_seconds,
        # Everybody signed into this same tree, this session included.
        "connections": store.count_for_tree(session.root_id, now=now),
    }


async def health(request):
    return web.json_response({"ok": True})


async def session_info(request):
    session = request["session"]
    if session is None:
        limits = request.app[SESSIONS]
        return web.json_response({
            "signed_in": False,
            # The ceilings, so the sign-in form can offer a shorter session
            # without offering one the server will silently clamp.
            "max_idle_seconds": limits.idle_ceiling,
            "max_absolute_seconds": limits.absolute_ceiling,
        })
    return web.json_response(_session_body(session, request.app[SESSIONS]))


async def login(request):
    store = request.app[SESSIONS]
    guard = request.app[GUARD]
    address = request.remote or "unknown"
    device_id = request["device_id"]

    payload = await _json_body(request)
    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")
    if not username or not password:
        raise _error(400, "username and password are required")

    try:
        guard.check(address, device_id)
    except LoginLocked as exc:
        refusal = _error(429, "too many failed attempts from here")
        refusal.headers["Retry-After"] = str(exc.retry_after)
        raise refusal from None

    try:
        async with guard.slot():
            user = await users.authenticate(username, password)
            key = None
            if user is not None:
                try:
                    key = await keystore.open_master_key(
                        users.keystore_id(user), password)
                except (KeyUnwrapError, keystore.KeystoreError) as exc:
                    # Startup proved this works for the environment's account,
                    # so reaching here means the keystore moved underneath a
                    # running server.
                    logger.error("Could not open the master key for %s: %s",
                                 username, exc)
    except LoginBusy:
        refusal = _error(503, "too many sign-ins in progress")
        refusal.headers["Retry-After"] = "5"
        raise refusal from None

    if user is None or key is None:
        guard.record_failure(address, device_id)
        # No detail about which half failed. Telling them apart is exactly
        # what the dummy verification in `users.authenticate` exists to
        # withhold, and saying it here would hand it straight back.
        logger.info("Web sign-in refused for %r from %s", username, address)
        raise _error(401, "wrong username or password")

    try:
        await DiscordVFS(key, user["root_id"]).ensure_root()
    except VFSError as exc:
        logger.error("Cannot serve this tree: %s", exc)
        raise _error(500, "this account's tree cannot be served") from exc

    guard.record_success(address, device_id)

    session = store.create(
        username=user["username"], root_id=user["root_id"], key=key,
        idle=_positive_int(payload.get("idle_seconds")),
        absolute=_positive_int(payload.get("absolute_seconds")),
    )

    response = web.json_response(_session_body(session, store))
    # No max_age, so closing the browser drops it. The server-side deadlines
    # are what actually bound the session; this only stops the id outliving
    # its window in a cookie jar.
    response.set_cookie(SESSION_COOKIE, session.id, **_cookie_kwargs())
    logger.info("Web sign-in for %r from %s (idle %ds, absolute %ds)",
                session.username, address, session.idle_seconds,
                session.absolute_seconds)
    return response


def _positive_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


async def logout(request):
    request.app[SESSIONS].drop(request.cookies.get(SESSION_COOKIE) or "")
    response = web.json_response({"signed_in": False})
    response.del_cookie(SESSION_COOKIE, path="/")
    return response


# -------------------------------------------------------------------- files


def _path_of(request) -> str:
    path = request.query.get("path")
    if path is None:
        raise _error(400, "a path query parameter is required")
    return normalize_path(path)


def _entry(node: dict) -> dict:
    return {
        "name": node.get("filename") or "",
        "is_dir": bool(node.get("is_dir")),
        "size": int(node.get("size") or 0),
        "modified_at": int(node.get("modified_at")
                           or node.get("created_at") or 0),
        "permissions": int(node.get("permissions") or 0),
    }


async def list_dir(request):
    session = request["session"]
    path = _path_of(request)
    entries = await session.vfs.list_dir(path)
    return web.json_response({
        "path": path,
        "entries": sorted((_entry(e) for e in entries),
                          key=lambda e: (not e["is_dir"], e["name"].lower())),
    })


async def stat_path(request):
    session = request["session"]
    path = _path_of(request)
    node = await session.vfs.require_node(path)
    return web.json_response({"path": path, **_entry(node)})


# The ceiling a client cannot raise. `limit` below it is honoured; above it is
# clamped, because the walk holds its results in this process's memory and
# "give me a million" is a request to spend the server's memory, not the
# caller's.
_SEARCH_LIMIT = 200


async def search(request):
    session = request["session"]
    needle = request.query.get("q") or ""
    if not needle.strip():
        raise _error(400, "a q query parameter is required")
    asked = _positive_int(request.query.get("limit"))
    return web.json_response(await session.vfs.search(
        needle, limit=min(asked or _SEARCH_LIMIT, _SEARCH_LIMIT)))


async def revoke_other_sessions(request):
    """Sign out every other session on this tree, keeping this one.

    One account can be signed in from several places at once, which is what
    sharing this drive looks like today. This is the control for the moment
    that stops being intentional -- and it keeps the caller signed in, because
    a button that logs you out as well is a button nobody presses while an
    intruder is the one they are trying to remove.
    """
    session = request["session"]
    store = request.app[SESSIONS]
    dropped = store.drop_others(session.root_id,
                                keep=request.cookies.get(SESSION_COOKIE) or "")
    logger.info("Signed out %d other session(s) for %r",
                dropped, session.username)
    return web.json_response({
        "signed_out": dropped,
        "connections": store.count_for_tree(session.root_id),
    })


def _disposition(filename: str) -> str:
    """`filename*` so a non-ASCII name survives, with an ASCII fallback."""
    fallback = filename.encode("ascii", "replace").decode("ascii")
    fallback = fallback.replace('"', "_")
    return (f'attachment; filename="{fallback}"; '
            f"filename*=UTF-8''{quote(filename)}")


async def download(request):
    session = request["session"]
    path = _path_of(request)

    node = await session.vfs.require_node(path)
    if node["is_dir"]:
        raise _error(400, "that is a directory")

    handle = await session.vfs.open(path, read=True, write=False)
    try:
        size = handle.size
        response = web.StreamResponse(status=200, headers={
            "Content-Length": str(size),
            "Content-Type": "application/octet-stream",
            "Content-Disposition": _disposition(node.get("filename") or "file"),
        })
        await response.prepare(request)

        offset = 0
        while offset < size:
            data = await handle.read_at(offset, min(STREAM_CHUNK, size - offset))
            if not data:
                break
            await response.write(data)
            offset += len(data)

        await response.write_eof()
        return response
    finally:
        await handle.close()


async def upload(request):
    session = request["session"]
    path = _path_of(request)

    handle = await session.vfs.open(path, read=False, write=True, create=True,
                                    truncate=True)
    offset = 0
    try:
        while True:
            data = await request.content.read(STREAM_CHUNK)
            if not data:
                break
            await handle.write_at(offset, data)
            offset += len(data)
    finally:
        # Always. Buffered bytes only reach Discord on close, so skipping this
        # on the error path would drop the tail of every interrupted upload --
        # the same bug the SFTP shutdown drain exists to avoid.
        await handle.close()

    node = await session.vfs.require_node(path)
    return web.json_response({"path": path, **_entry(node)}, status=201)


async def remove_file(request):
    session = request["session"]
    path = _path_of(request)
    await session.vfs.remove(path)
    return web.json_response({"removed": path})


async def make_dir(request):
    session = request["session"]
    payload = await _json_body(request)
    path = normalize_path(str(payload.get("path") or ""))
    if path == "/":
        raise _error(400, "a path is required")
    await session.vfs.makedir(path)
    return web.json_response({"path": path}, status=201)


async def remove_dir(request):
    session = request["session"]
    path = _path_of(request)
    # `recursive` is the one way to trash a directory with things still in it,
    # and it is opt-in for the same reason `rename` refuses to clobber: the
    # difference between "this folder is empty, tidy it away" and "this folder
    # and everything in it" is the difference a mis-click lands on.
    if _flag(request, "recursive"):
        await session.vfs.trash(path)
    else:
        await session.vfs.removedir(path)
    return web.json_response({"removed": path})


def _flag(request, name: str) -> bool:
    return (request.query.get(name) or "").strip().lower() in ("1", "true", "yes")


# -------------------------------------------------------------------- trash


def _trash_entry(item: dict) -> dict:
    node = item["node"]
    return {
        **_entry(node),
        # The id, not the path: two things deleted from the same place under
        # the same name can both sit in the trash, so a path does not identify
        # one of them. Everything in here is addressed by id for that reason.
        "id": node["id"],
        "original_path": item["path"],
        "trashed_at": int(node.get("trashed_at") or 0),
    }


async def list_trash(request):
    session = request["session"]
    items = await session.vfs.list_trash()
    return web.json_response({
        "entries": [_trash_entry(item) for item in items],
        # So the UI can say "deleted for ever in 12 days" instead of making
        # the reader work out what the server's retention happens to be.
        "retention_seconds": trash_settings()["retention"],
    })


_CONFLICT_CHOICES = ("fail", "replace", "skip", "keep_both")


async def restore_trash(request):
    session = request["session"]
    payload = await _json_body(request)
    node_id = str(payload.get("id") or "")
    if not node_id:
        raise _error(400, "an id is required")

    on_conflict = str(payload.get("on_conflict") or "fail")
    if on_conflict not in _CONFLICT_CHOICES:
        raise _error(400, "on_conflict must be one of "
                          + ", ".join(_CONFLICT_CHOICES))

    return web.json_response(
        await session.vfs.restore(node_id, on_conflict=on_conflict))


async def purge_trash(request):
    session = request["session"]
    node_id = request.query.get("id")
    if not node_id:
        raise _error(400, "an id query parameter is required")
    return web.json_response(await session.vfs.purge(node_id))


async def empty_trash(request):
    session = request["session"]
    nodes = attachments = 0
    items = await session.vfs.list_trash()
    for item in items:
        result = await session.vfs.purge(item["node"]["id"])
        nodes += result["nodes"]
        attachments += result["attachments"]
    return web.json_response({"purged": len(items), "nodes": nodes,
                              "attachments": attachments})


async def rename(request):
    session = request["session"]
    payload = await _json_body(request)
    source = normalize_path(str(payload.get("from") or ""))
    target = normalize_path(str(payload.get("to") or ""))
    if source == "/" or target == "/":
        raise _error(400, "both from and to are required")
    # SFTP v3 `rename` semantics rather than `posix_rename`: refuse to
    # clobber. A file manager that silently replaces the target on a drag is
    # a file manager that loses data on a mis-drop.
    await session.vfs.rename(source, target, overwrite=False)
    return web.json_response({"from": source, "to": target})


async def _json_body(request):
    try:
        payload = await request.json()
    except Exception:
        raise _error(400, "expected a JSON body") from None
    if not isinstance(payload, dict):
        raise _error(400, "expected a JSON object")
    return payload


# ----------------------------------------------------------- the static client


_NOT_BUILT = """<!doctype html><meta charset="utf-8">
<title>Discord Drive</title>
<style>body{margin:0;height:100vh;display:grid;place-items:center;
background:#161826;color:#e9e9ed;font:14px/1.7 system-ui,"Noto Sans TC",sans-serif}
div{max-width:52ch;padding:24px}code{font-family:ui-monospace,monospace;color:#9184d9}
h1{font-size:17px;font-weight:500;margin:0 0 8px}p{color:#9a9aa8;margin:0 0 10px}</style>
<div><h1>前端還沒有建置</h1>
<p>API 是好的，SFTP 也是。缺的只有這個畫面。在 repo 目錄跑：</p>
<p><code>cd client/app &amp;&amp; npm install &amp;&amp; npm run build</code></p>
<p>然後重新整理這一頁。不需要重建 image：<code>client/app/dist</code>
是挂進容器的，不是烤進去的。</p>
<p>The API and the SFTP surface are fine; only this page is missing.
Build the client with the command above and reload.</p></div>"""


def _asset_headers(rel: str) -> dict:
    """Cache policy, decided by whether the URL can ever mean different bytes.

    Vite content-hashes everything under `assets/`, so those URLs are
    immutable by construction. `index.html` is the opposite: it is the one
    file whose contents change while its name does not, and a cached copy of
    it pins a browser to asset names that the next build deleted.
    """
    if rel.startswith("assets/"):
        return {"Cache-Control": "public, max-age=31536000, immutable"}
    return {"Cache-Control": "no-store"}


def _add_client_routes(app, directory: str):
    """Serve the built SPA, and let it own its own URLs.

    Registered last on purpose. aiohttp resolves in registration order, so the
    catch-all below can only be reached by a path no API route claimed -- and
    a stray `/api/...` is still answered as a missing endpoint rather than
    handed the HTML shell, because a client that gets a 200 and an HTML body
    where it expected JSON reports a parse error instead of a 404.
    """
    root = Path(directory).resolve()

    async def client(request):
        rel = request.match_info.get("tail", "")
        if request.path.startswith("/api/"):
            raise _error(404, "no such endpoint")

        index = root / "index.html"
        if not index.is_file():
            return web.Response(text=_NOT_BUILT, content_type="text/html",
                                charset="utf-8", status=503)

        if rel:
            candidate = (root / rel).resolve()
            # Resolve first, then check containment. The path arrives from the
            # network and `../../` is the first thing anybody tries; comparing
            # the resolved result is what makes symlinks and encoded traversal
            # land inside the check rather than around it.
            if (candidate == root or root in candidate.parents) \
                    and candidate.is_file():
                return web.FileResponse(candidate, headers=_asset_headers(rel))

        # Not a file, so it is a client-side route. A reload on /trash has to
        # return the app, not a 404 -- the SPA owns its history.
        return web.FileResponse(index, headers={"Cache-Control": "no-store"})

    app.router.add_get("/", client)
    app.router.add_get("/{tail:.*}", client)


# ----------------------------------------------------------------- assembly


def create_app(*, sessions=None, guard=None, static_dir=None) -> web.Application:
    limits = web_session_limits()
    login_limits = web_login_limits()

    app = web.Application(middlewares=[error_middleware, device_middleware,
                                       auth_middleware])
    app[SESSIONS] = sessions or SessionStore(
        idle_ceiling=limits["idle"], absolute_ceiling=limits["absolute"])
    app[GUARD] = guard or LoginGuard(concurrency=login_limits["concurrency"],
                                     queue=login_limits["queue"])
    app[STATE] = {}

    app.router.add_get("/api/health", health)
    app.router.add_get("/api/session", session_info)
    app.router.add_post("/api/login", login)
    app.router.add_post("/api/logout", logout)

    app.router.add_post("/api/sessions/revoke-others", revoke_other_sessions)

    app.router.add_get("/api/files", list_dir)
    app.router.add_get("/api/stat", stat_path)
    app.router.add_get("/api/search", search)
    app.router.add_get("/api/file", download)
    app.router.add_put("/api/file", upload)
    app.router.add_delete("/api/file", remove_file)
    app.router.add_post("/api/dir", make_dir)
    app.router.add_delete("/api/dir", remove_dir)
    app.router.add_post("/api/rename", rename)

    app.router.add_get("/api/trash", list_trash)
    app.router.add_post("/api/trash/restore", restore_trash)
    app.router.add_delete("/api/trash", purge_trash)
    # A POST rather than a DELETE with no target: "delete everything" is worth
    # a route of its own, so a client that forgets the `id` on the line above
    # gets a 400 instead of emptying the bin.
    app.router.add_post("/api/trash/empty", empty_trash)

    # Last, so every API route above wins the match. See `_add_client_routes`.
    _add_client_routes(app, web_static_dir() if static_dir is None
                       else static_dir)

    app.on_startup.append(_start_sweeper)
    app.on_cleanup.append(_stop_sweeper)
    return app


async def trash_sweeper(store, *, retention: int, interval: int, batch: int):
    """Destroy trash past its retention, borrowing a key from whoever is on.

    The awkward part, and the reason this is not a plain timer over the
    database: purging verifies node tags and rewrites a directory's entry tag,
    and both need the master key. This process has no master key. That is a
    deliberate decision recorded in `main.py` -- the key belongs to a
    connection, not to the process -- and keeping one here for a background
    task's convenience would mean the key sat in memory whether or not anybody
    was signed in, which is the exact property that design avoids.

    So this borrows a key rather than holding one. It sweeps the trees that
    have a live session, one pass each, and does nothing at all while nobody
    is signed in. Retention therefore means "at least this long", never
    "exactly this long": something deleted forty days ago on an account nobody
    has opened since is still there, and goes on the first sweep after the
    next sign-in. For a drive its owner logs into, that is the honest trade.
    """
    while True:
        await asyncio.sleep(interval)
        for session in store.live_by_tree():
            try:
                result = await session.vfs.purge_expired(
                    retention=retention, limit=batch)
            except Exception:
                # One tree failing -- a Discord outage, a tampered node --
                # must not take the loop down and stop every other tree from
                # ever being swept again.
                logger.exception("Trash sweep failed for %r", session.username)
                continue
            if result["purged"]:
                logger.info(
                    "Trash sweep for %r destroyed %d item(s) and %d "
                    "attachment(s); %d still due",
                    session.username, result["purged"], result["attachments"],
                    result["remaining"])


async def _start_sweeper(app):
    limits = trash_settings()
    app[STATE]["sweeper"] = asyncio.create_task(sweeper(app[SESSIONS]))
    app[STATE]["trash_sweeper"] = asyncio.create_task(trash_sweeper(
        app[SESSIONS], retention=limits["retention"],
        interval=limits["interval"], batch=limits["batch"]))


async def _stop_sweeper(app):
    for name in ("sweeper", "trash_sweeper"):
        task = app[STATE].get(name)
        if task is None:
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    # Every session's key goes with the process, and dropping them explicitly
    # beats relying on the interpreter to get around to it.
    app[SESSIONS].drop_all()
