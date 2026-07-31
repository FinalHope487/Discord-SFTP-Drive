import aiohttp
import asyncio
import logging
import random
import time
import urllib.parse
from src.config import (
    DISCORD_BOT_TOKEN,
    DISCORD_USER_ID,
    DISCORD_CHANNEL_ID,
    discord_max_concurrency,
)
from src.ratelimit import RateLimiter, route_key

logger = logging.getLogger(__name__)

# Server-side faults and transport failures are retried; a 4xx other than 429
# is the caller's problem and retrying it just wastes the budget.
_RETRYABLE_STATUS = (500, 502, 503, 504)
_MAX_ATTEMPTS = 5

# Equal jitter: half the delay fixed, half random. Without the random half,
# every chunk of a multi-part upload that fails together also retries together,
# reproducing the burst that caused the failure.
_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 8.0

# Discord signs attachment URLs with an expiry (24h when this was measured).
# Treat one as spent slightly early rather than discovering mid-download that
# it lapsed between the check and the request.
_URL_EXPIRY_MARGIN = 300

_TIMEOUT = aiohttp.ClientTimeout(total=600, connect=15, sock_read=120)


class DiscordAPIError(RuntimeError):
    """A Discord request failed. `status` is None for transport failures."""

    def __init__(self, status, message):
        super().__init__(f"Discord API error {status}: {message}"
                         if status else f"Discord API error: {message}")
        self.status = status


def _backoff(attempt: int) -> float:
    ceiling = min(_BACKOFF_CAP, _BACKOFF_BASE * (2 ** attempt))
    return random.uniform(ceiling / 2, ceiling)


def _url_expiry(url: str):
    """When a signed attachment URL stops working, or None if it says nothing.

    Discord puts the expiry in the `ex` query parameter as hex epoch seconds.
    """
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    raw = query.get("ex", [None])[0]
    if not raw:
        return None
    try:
        return int(raw, 16)
    except ValueError:
        return None

# Discord permission bits we actually depend on. Named here rather than
# inlined because a bare `1 << 15` at the call site says nothing about why
# that bit matters.
PERM_ADMINISTRATOR = 1 << 3
PERM_VIEW_CHANNEL = 1 << 10
PERM_SEND_MESSAGES = 1 << 11
PERM_ATTACH_FILES = 1 << 15
PERM_READ_MESSAGE_HISTORY = 1 << 16

# Why each one is required, for the operator reading the error message.
_REQUIRED_PERMS = (
    (PERM_VIEW_CHANNEL, "View Channel", "to address the channel at all"),
    (PERM_SEND_MESSAGES, "Send Messages", "to create the message holding a chunk"),
    (PERM_ATTACH_FILES, "Attach Files", "to upload the chunk itself"),
    (PERM_READ_MESSAGE_HISTORY, "Read Message History",
     "to fetch a chunk's attachment URL when reading it back"),
)


class ReachabilityError(RuntimeError):
    """Raised when credentials are well-formed but cannot actually be used."""


class DiscordAPI:
    def __init__(self, limiter=None):
        self.session = None
        self.cdn_session = None
        self.base_url = "https://discord.com/api/v10"
        self.headers = {
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}"
        }
        self.dm_channel_id = None
        self._limiter = limiter
        self._url_cache = {}     # message id -> (url, expiry epoch or None)

    @property
    def limiter(self):
        """Built on first use, not in `__init__`.

        The module-level instance below is constructed at import time, which
        happens before `validate()` has had a chance to report a malformed
        DISCORD_MAX_CONCURRENCY as a readable configuration error.
        """
        if self._limiter is None:
            self._limiter = RateLimiter(discord_max_concurrency())
        return self._limiter

    async def get_target_channel_id(self):
        if self.dm_channel_id:
            return self.dm_channel_id
        
        if DISCORD_USER_ID:
            endpoint = "/users/@me/channels"
            data = {"recipient_id": DISCORD_USER_ID}
            result = await self._request("POST", endpoint, json=data)
            self.dm_channel_id = result["id"]
            return self.dm_channel_id
            
        return DISCORD_CHANNEL_ID

    async def get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers=self.headers,
                                                 timeout=_TIMEOUT)
        return self.session

    async def get_cdn_session(self):
        """Separate session for attachment downloads, with no credentials.

        The API session carries the bot token on every request. Attachment
        URLs point at a different host and are already signed, so sending the
        token there buys nothing and widens where it can end up.
        """
        if self.cdn_session is None or self.cdn_session.closed:
            self.cdn_session = aiohttp.ClientSession(timeout=_TIMEOUT)
        return self.cdn_session

    async def close(self):
        for session in (self.session, self.cdn_session):
            if session and not session.closed:
                await session.close()

    async def _request(self, method, endpoint, *, data_factory=None, **kwargs):
        """`data_factory`, if given, is called to build a fresh `data=` value
        for every attempt. aiohttp.FormData is single-use — retrying a 429
        with the same instance raises "Form data has been processed already"
        instead of resending, which is why a plain `data=` kwarg won't do for
        multipart bodies. `json=`/`params=` etc. are plain values aiohttp
        re-serializes per call, so they pass straight through in `kwargs`.
        """
        session = await self.get_session()
        url = f"{self.base_url}{endpoint}"
        route = route_key(method, endpoint)

        last = None
        for attempt in range(_MAX_ATTEMPTS):
            request_kwargs = dict(kwargs)
            if data_factory is not None:
                request_kwargs["data"] = data_factory()

            try:
                # The slot is taken per attempt, not around the whole loop: a
                # 429 means waiting, and holding a concurrency slot while
                # waiting would block requests to unrelated routes that are
                # still within budget.
                async with self.limiter.slot(route):
                    async with session.request(method, url, **request_kwargs) as response:
                        self.limiter.update(route, response.headers)

                        if response.status == 429:
                            data = await response.json()
                            retry_after = float(data.get("retry_after", 1.0))
                            self.limiter.note_429(response.headers, retry_after)
                            logger.warning(
                                "Rate limited by Discord on %s. Retrying after %ss.",
                                route, retry_after)
                            last = DiscordAPIError(429, "rate limited")
                            sleep_for = retry_after
                        elif response.status in _RETRYABLE_STATUS:
                            # Discord's own fault, and usually brief. Left
                            # unretried these surfaced to the client as a
                            # failed upload.
                            text = await response.text()
                            last = DiscordAPIError(response.status, text)
                            sleep_for = _backoff(attempt)
                            logger.warning(
                                "Discord returned %s on %s (attempt %d/%d); "
                                "retrying in %.1fs",
                                response.status, route, attempt + 1,
                                _MAX_ATTEMPTS, sleep_for)
                        elif not response.ok:
                            text = await response.text()
                            logger.error("Discord API Error %s: %s",
                                         response.status, text)
                            raise DiscordAPIError(response.status, text)
                        elif response.status == 204:   # No content
                            return None
                        else:
                            return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                # A dropped connection or a stalled socket mid-upload used to
                # fail the whole write. DiscordAPIError deliberately is not an
                # aiohttp.ClientError, so genuine 4xx still escape here.
                last = exc
                sleep_for = _backoff(attempt)
                logger.warning(
                    "Transport failure on %s (attempt %d/%d): %s; retrying in %.1fs",
                    route, attempt + 1, _MAX_ATTEMPTS, exc, sleep_for)

            if attempt + 1 < _MAX_ATTEMPTS:
                await asyncio.sleep(sleep_for)

        raise DiscordAPIError(
            getattr(last, "status", None),
            f"gave up on {route} after {_MAX_ATTEMPTS} attempts: {last}",
        ) from last

    async def upload_chunk(self, file_bytes: bytes, filename: str):
        def build_form_data():
            data = aiohttp.FormData()
            data.add_field("file", file_bytes, filename=filename, content_type="application/octet-stream")
            return data

        channel_id = await self.get_target_channel_id()
        endpoint = f"/channels/{channel_id}/messages"
        result = await self._request("POST", endpoint, data_factory=build_form_data)

        message_id = result["id"]
        attachment = result["attachments"][0]
        url = attachment["url"]
        size = attachment["size"]

        if size != len(file_bytes):
            # Caught here rather than left to the HMAC on the next read: the
            # chunk is still referenced by nothing, so it can be dropped
            # instead of becoming a file that fails to open much later.
            await self._safe_delete(message_id)
            raise DiscordAPIError(
                None,
                f"Discord stored {size} bytes for {filename} but "
                f"{len(file_bytes)} were sent",
            )

        self._url_cache[message_id] = (url, _url_expiry(url))
        return message_id, url, size

    async def _safe_delete(self, message_id: str):
        try:
            await self.delete_message(message_id)
        except Exception:
            logger.warning("Could not delete Discord message %s", message_id,
                           exc_info=True)

    async def get_attachment_url(self, message_id: str, *, refresh: bool = False):
        """A usable download URL, re-resolved when the cached one has lapsed.

        Caching matters: without it every chunk read costs an extra API call
        purely to learn a URL that is valid for a day.
        """
        if not refresh:
            cached = self._url_cache.get(message_id)
            if cached is not None:
                url, expiry = cached
                if expiry is None or time.time() < expiry - _URL_EXPIRY_MARGIN:
                    return url

        channel_id = await self.get_target_channel_id()
        endpoint = f"/channels/{channel_id}/messages/{message_id}"
        result = await self._request("GET", endpoint)
        if not result.get("attachments"):
            raise DiscordAPIError(
                None, f"message {message_id} has no attachment")

        url = result["attachments"][0]["url"]
        self._url_cache[message_id] = (url, _url_expiry(url))
        return url

    async def delete_message(self, message_id: str):
        channel_id = await self.get_target_channel_id()
        endpoint = f"/channels/{channel_id}/messages/{message_id}"
        await self._request("DELETE", endpoint)
        self._url_cache.pop(message_id, None)

    async def download_attachment(self, message_id: str) -> bytes:
        """A chunk's stored bytes, re-signing the URL if it has expired.

        The expiry check above is a prediction; this is what happens when it
        is wrong. A signature that lapsed between resolving the URL and using
        it comes back as a 403/404 from the CDN, which is a stale URL rather
        than missing data, so it is resolved again instead of failing the read.
        """
        for attempt in range(2):
            url = await self.get_attachment_url(message_id, refresh=attempt > 0)
            try:
                return await self.download_chunk(url)
            except DiscordAPIError as exc:
                if attempt == 0 and exc.status in (403, 404):
                    logger.info(
                        "Attachment URL for %s was rejected (%s); re-resolving",
                        message_id, exc.status)
                    continue
                raise
        raise AssertionError("unreachable")

    async def download_chunk(self, url: str) -> bytes:
        session = await self.get_cdn_session()

        last = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                async with session.get(url) as response:
                    if response.ok:
                        return await response.read()
                    text = await response.text()
                    error = DiscordAPIError(response.status, text)
                    if response.status not in _RETRYABLE_STATUS:
                        raise error
                    last = error
                    logger.warning(
                        "CDN returned %s (attempt %d/%d)",
                        response.status, attempt + 1, _MAX_ATTEMPTS)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last = exc
                logger.warning("Transport failure downloading a chunk "
                               "(attempt %d/%d): %s", attempt + 1,
                               _MAX_ATTEMPTS, exc)

            if attempt + 1 < _MAX_ATTEMPTS:
                await asyncio.sleep(_backoff(attempt))

        raise DiscordAPIError(
            getattr(last, "status", None),
            f"gave up downloading after {_MAX_ATTEMPTS} attempts: {last}",
        ) from last

    # ------------------------------------------------------------ reachability

    async def _probe(self, method, endpoint, **kwargs):
        """Like `_request` but hands back the status instead of raising.

        Reachability checks care *which* way a call failed -- a 401 and a 403
        need different advice -- so they cannot use the raising path.
        """
        session = await self.get_session()
        url = f"{self.base_url}{endpoint}"
        route = route_key(method, endpoint)
        async with self.limiter.slot(route):
            async with session.request(method, url, **kwargs) as response:
                self.limiter.update(route, response.headers)
                if response.status == 204:
                    return response.status, None
                try:
                    return response.status, await response.json()
                except (aiohttp.ContentTypeError, ValueError):
                    return response.status, await response.text()

    async def check_reachability(self):
        """Verify the configured credentials can actually move bytes.

        `config.validate()` only proves the settings are present and
        well-formed. Everything here needs the network to answer, and the
        failures it catches -- a revoked token, a bot with no shared server,
        a channel missing Attach Files -- otherwise stay hidden until the
        first upload, which is both much later and much harder to read.

        Returns a list of human-readable problems. Transport failures raise
        `ReachabilityError` instead, because "Discord did not answer" is not
        the same kind of fact as "this token is wrong" and the caller treats
        them differently.
        """
        problems = []

        try:
            status, me = await self._probe("GET", "/users/@me")
        except aiohttp.ClientError as exc:
            raise ReachabilityError(f"could not reach discord.com: {exc}") from exc

        if status == 401:
            return ["DISCORD_BOT_TOKEN was rejected by Discord (401). It is "
                    "either mistyped or has been reset -- issue a new one at "
                    "https://discord.com/developers/applications -> Bot -> "
                    "Reset Token."]
        if status != 200:
            return [f"unexpected {status} from GET /users/@me: {me!r}"]

        logger.info("Discord bot authenticated as %s#%s (id %s)",
                    me.get("username"), me.get("discriminator"), me.get("id"))

        # Only the configured target is checked. Validating the unused one
        # would turn a harmless leftover line in .env into a startup failure.
        if DISCORD_USER_ID:
            problems += await self._check_dm()
        else:
            problems += await self._check_channel()

        return problems

    async def _check_dm(self):
        status, body = await self._probe(
            "POST", "/users/@me/channels", json={"recipient_id": DISCORD_USER_ID},
        )
        if status in (200, 201):
            self.dm_channel_id = body["id"]
            logger.info("DM channel with user %s is open", DISCORD_USER_ID)
            return []

        if status == 403:
            return [f"cannot open a DM with DISCORD_USER_ID={DISCORD_USER_ID} "
                    "(403). A bot may only DM a user it shares a server with, "
                    "and the user must allow DMs from server members."]
        if status == 400:
            return [f"Discord rejected DISCORD_USER_ID={DISCORD_USER_ID} as "
                    "malformed (400). It should be the numeric user id from "
                    "right-click -> Copy User ID with Developer Mode on, not "
                    "a username."]
        return [f"unexpected {status} opening a DM channel: {body!r}"]

    async def _check_channel(self):
        status, channel = await self._probe("GET", f"/channels/{DISCORD_CHANNEL_ID}")
        if status in (403, 404):
            # 404 is what Discord returns for a channel the bot cannot see, so
            # it does not distinguish "wrong id" from "not invited".
            return [f"cannot see DISCORD_CHANNEL_ID={DISCORD_CHANNEL_ID} "
                    f"({status}). Either the id is wrong or the bot has not "
                    "been added to that server / given View Channel."]
        if status != 200:
            return [f"unexpected {status} reading the channel: {channel!r}"]

        guild_id = channel.get("guild_id")
        if guild_id is None:
            # An existing DM/group channel: permissions do not apply.
            logger.info("Channel %s is a DM channel", DISCORD_CHANNEL_ID)
            return []

        perms = await self._channel_permissions(guild_id, channel)
        if perms is None:
            logger.warning(
                "Could not compute permissions for channel %s; skipping the "
                "permission check. Upload failures will surface at first use.",
                DISCORD_CHANNEL_ID,
            )
            return []

        if perms & PERM_ADMINISTRATOR:
            return []

        missing = [f"{name} ({why})" for bit, name, why in _REQUIRED_PERMS
                   if not perms & bit]
        if missing:
            return [f"bot is missing permissions on channel {DISCORD_CHANNEL_ID}: "
                    + "; ".join(missing)]
        return []

    async def _channel_permissions(self, guild_id, channel):
        """Effective permission bits for this bot in `channel`.

        Follows Discord's documented order: @everyone role, then the bot's
        other roles, then channel overwrites (role denies before allows, and
        member overwrites last). Returns None if any lookup fails -- a
        permission check that cannot run should not masquerade as a failure.
        """
        status, member = await self._probe(
            "GET", f"/guilds/{guild_id}/members/@me")
        if status != 200:
            return None
        status, roles = await self._probe("GET", f"/guilds/{guild_id}/roles")
        if status != 200:
            return None

        by_id = {r["id"]: r for r in roles}
        # The @everyone role always carries the guild id as its own id.
        everyone = by_id.get(guild_id)
        if everyone is None:
            return None

        perms = int(everyone["permissions"])
        member_roles = set(member.get("roles", []))
        for role_id in member_roles:
            role = by_id.get(role_id)
            if role:
                perms |= int(role["permissions"])

        if perms & PERM_ADMINISTRATOR:
            return perms

        overwrites = {o["id"]: o for o in channel.get("permission_overwrites", [])}

        everyone_ow = overwrites.get(guild_id)
        if everyone_ow:
            perms &= ~int(everyone_ow["deny"])
            perms |= int(everyone_ow["allow"])

        # Role overwrites are accumulated first and applied together, so that
        # an allow on one role is not cancelled by a deny on another.
        role_deny = role_allow = 0
        for role_id in member_roles:
            ow = overwrites.get(role_id)
            if ow:
                role_deny |= int(ow["deny"])
                role_allow |= int(ow["allow"])
        perms &= ~role_deny
        perms |= role_allow

        member_ow = overwrites.get(member.get("user", {}).get("id"))
        if member_ow:
            perms &= ~int(member_ow["deny"])
            perms |= int(member_ow["allow"])

        return perms


discord_api = DiscordAPI()
