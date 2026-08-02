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
from urllib.parse import quote

from aiohttp import web

from src import keystore, users
from src.config import web_cookie_secure, web_login_limits, web_session_limits
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


def _error(status: int, message: str) -> web.HTTPException:
    """An aiohttp exception whose body is JSON, like every other response."""
    cls = _ERROR_CLASSES.get(status, web.HTTPBadRequest)
    return cls(text=json.dumps({"error": message}),
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
        raise _error(500, "integrity check failed") from exc
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

    if request.path in _PUBLIC_PATHS:
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


def _session_body(session) -> dict:
    return {
        "signed_in": True,
        "username": session.username,
        "csrf_token": session.csrf_token,
        "expires_in": session.expires_in(time.monotonic()),
        "idle_seconds": session.idle_seconds,
        "absolute_seconds": session.absolute_seconds,
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
    return web.json_response(_session_body(session))


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

    response = web.json_response(_session_body(session))
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
    await session.vfs.removedir(path)
    return web.json_response({"removed": path})


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


# ----------------------------------------------------------------- assembly


def create_app(*, sessions=None, guard=None) -> web.Application:
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

    app.router.add_get("/api/files", list_dir)
    app.router.add_get("/api/stat", stat_path)
    app.router.add_get("/api/file", download)
    app.router.add_put("/api/file", upload)
    app.router.add_delete("/api/file", remove_file)
    app.router.add_post("/api/dir", make_dir)
    app.router.add_delete("/api/dir", remove_dir)
    app.router.add_post("/api/rename", rename)

    app.on_startup.append(_start_sweeper)
    app.on_cleanup.append(_stop_sweeper)
    return app


async def _start_sweeper(app):
    app[STATE]["sweeper"] = asyncio.create_task(sweeper(app[SESSIONS]))


async def _stop_sweeper(app):
    task = app[STATE].get("sweeper")
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    # Every session's key goes with the process, and dropping them explicitly
    # beats relying on the interpreter to get around to it.
    app[SESSIONS].drop_all()
