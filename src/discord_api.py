import aiohttp
import asyncio
import logging
from src.config import (
    DISCORD_BOT_TOKEN,
    DISCORD_USER_ID,
    DISCORD_CHANNEL_ID,
    discord_max_concurrency,
)
from src.ratelimit import RateLimiter, route_key

logger = logging.getLogger(__name__)

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
        self.base_url = "https://discord.com/api/v10"
        self.headers = {
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}"
        }
        self.dm_channel_id = None
        self._limiter = limiter

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
            self.session = aiohttp.ClientSession(headers=self.headers)
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

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

        retries = 5
        for attempt in range(retries):
            request_kwargs = dict(kwargs)
            if data_factory is not None:
                request_kwargs["data"] = data_factory()

            # The slot is taken per attempt, not around the whole loop: a 429
            # means waiting, and holding a concurrency slot while waiting would
            # block requests to unrelated routes that are still within budget.
            async with self.limiter.slot(route):
                async with session.request(method, url, **request_kwargs) as response:
                    self.limiter.update(route, response.headers)

                    if response.status == 429:
                        data = await response.json()
                        retry_after = data.get("retry_after", 1.0)
                        self.limiter.note_429(response.headers, retry_after)
                        logger.warning(
                            "Rate limited by Discord on %s. Retrying after %ss.",
                            route, retry_after)
                        sleep_for = retry_after
                    else:
                        if not response.ok:
                            text = await response.text()
                            logger.error(f"Discord API Error {response.status}: {text}")
                            response.raise_for_status()

                        if response.status == 204: # No content
                            return None
                        return await response.json()

            await asyncio.sleep(sleep_for)
        raise Exception("Max retries exceeded for Discord API")

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
        
        return message_id, url, size

    async def get_attachment_url(self, message_id: str):
        channel_id = await self.get_target_channel_id()
        endpoint = f"/channels/{channel_id}/messages/{message_id}"
        result = await self._request("GET", endpoint)
        if not result.get("attachments"):
            raise Exception("No attachments found on the message")
        return result["attachments"][0]["url"]

    async def delete_message(self, message_id: str):
        channel_id = await self.get_target_channel_id()
        endpoint = f"/channels/{channel_id}/messages/{message_id}"
        await self._request("DELETE", endpoint)

    async def download_chunk(self, url: str) -> bytes:
        session = await self.get_session()
        async with session.get(url) as response:
            if not response.ok:
                response.raise_for_status()
            return await response.read()

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
