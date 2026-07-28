"""Startup reachability checks against a stand-in Discord API.

`config.validate()` proves the settings are *present*; these checks prove they
are *usable*. The distinction matters because every failure modelled here --
a reset token, a bot with no shared server, a channel missing Attach Files --
otherwise stays invisible until the first upload, by which point the client
has already been told the write succeeded.

The permission cases are the fiddly ones: Discord's effective permissions are
a fold over the @everyone role, the member's other roles, and three tiers of
channel overwrite, and getting the order wrong yields a check that passes for
a bot that cannot actually post.
"""

import pytest
from aiohttp import web

import src.discord_api as api_mod
from src.discord_api import (
    PERM_ADMINISTRATOR,
    PERM_ATTACH_FILES,
    PERM_READ_MESSAGE_HISTORY,
    PERM_SEND_MESSAGES,
    PERM_VIEW_CHANNEL,
    ReachabilityError,
)

GUILD_ID = "guild-1"
CHANNEL_ID = "chan-1"
BOT_ID = "bot-1"
USER_ID = "user-1"

ALL_REQUIRED = (
    PERM_VIEW_CHANNEL | PERM_SEND_MESSAGES
    | PERM_ATTACH_FILES | PERM_READ_MESSAGE_HISTORY
)


class DiscordStub:
    """Serves just enough of the API surface for the checks to run.

    Every response is overridable per test so a single unhappy path can be
    injected without rebuilding the whole fixture.
    """

    def __init__(self):
        self.me_status = 200
        self.dm_status = 200
        self.channel_status = 200
        self.member_status = 200
        self.roles_status = 200

        self.guild_id = GUILD_ID
        self.overwrites = []
        self.everyone_perms = ALL_REQUIRED
        self.member_role_perms = {}   # role id -> permission bits
        self.member_roles = []

    async def me(self, request):
        if self.me_status != 200:
            return web.json_response({"message": "401: Unauthorized"},
                                     status=self.me_status)
        return web.json_response(
            {"id": BOT_ID, "username": "testbot", "discriminator": "0"})

    async def dm(self, request):
        if self.dm_status not in (200, 201):
            return web.json_response({"message": "nope"}, status=self.dm_status)
        return web.json_response({"id": "dm-chan-1"}, status=self.dm_status)

    async def channel(self, request):
        if self.channel_status != 200:
            return web.json_response({"message": "nope"},
                                     status=self.channel_status)
        body = {"id": CHANNEL_ID, "permission_overwrites": self.overwrites}
        if self.guild_id is not None:
            body["guild_id"] = self.guild_id
        return web.json_response(body)

    async def member(self, request):
        if self.member_status != 200:
            return web.json_response({"message": "nope"},
                                     status=self.member_status)
        return web.json_response(
            {"user": {"id": BOT_ID}, "roles": self.member_roles})

    async def roles(self, request):
        if self.roles_status != 200:
            return web.json_response({"message": "nope"},
                                     status=self.roles_status)
        # @everyone always carries the guild id as its own role id.
        out = [{"id": GUILD_ID, "permissions": str(self.everyone_perms)}]
        out += [{"id": rid, "permissions": str(bits)}
                for rid, bits in self.member_role_perms.items()]
        return web.json_response(out)


@pytest.fixture
async def stub():
    s = DiscordStub()
    app = web.Application()
    app.router.add_get("/api/v10/users/@me", s.me)
    app.router.add_post("/api/v10/users/@me/channels", s.dm)
    app.router.add_get("/api/v10/channels/{cid}", s.channel)
    app.router.add_get("/api/v10/guilds/{gid}/members/@me", s.member)
    app.router.add_get("/api/v10/guilds/{gid}/roles", s.roles)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    s.base_url = f"http://127.0.0.1:{runner.addresses[0][1]}/api/v10"
    try:
        yield s
    finally:
        await runner.cleanup()


@pytest.fixture
async def api(stub):
    client = api_mod.DiscordAPI()
    client.base_url = stub.base_url
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
def dm_mode(monkeypatch):
    monkeypatch.setattr(api_mod, "DISCORD_USER_ID", USER_ID)
    monkeypatch.setattr(api_mod, "DISCORD_CHANNEL_ID", None)


@pytest.fixture
def channel_mode(monkeypatch):
    monkeypatch.setattr(api_mod, "DISCORD_USER_ID", None)
    monkeypatch.setattr(api_mod, "DISCORD_CHANNEL_ID", CHANNEL_ID)


# ------------------------------------------------------------------ the token

async def test_valid_token_and_dm_reports_no_problems(api, dm_mode):
    assert await api.check_reachability() == []


async def test_rejected_token_is_reported(api, stub, dm_mode):
    stub.me_status = 401
    problems = await api.check_reachability()
    assert len(problems) == 1
    assert "DISCORD_BOT_TOKEN" in problems[0]


async def test_a_bad_token_short_circuits_the_rest(api, stub, dm_mode):
    # Nothing downstream can be trusted once auth failed, and reporting a
    # cascade of consequences would bury the one problem worth fixing.
    stub.me_status = 401
    stub.dm_status = 403
    assert len(await api.check_reachability()) == 1


async def test_unreachable_host_raises_rather_than_reporting(api, dm_mode):
    # Discord being down is not a configuration error; the caller starts the
    # server anyway rather than burning its restart budget.
    api.base_url = "http://127.0.0.1:1/api/v10"
    with pytest.raises(ReachabilityError):
        await api.check_reachability()


# --------------------------------------------------------------------- DM mode

async def test_dm_success_caches_the_channel_id(api, dm_mode):
    await api.check_reachability()
    # The startup probe already paid for this lookup; the first upload should
    # not repeat it.
    assert api.dm_channel_id == "dm-chan-1"


async def test_dm_forbidden_explains_the_shared_server_rule(api, stub, dm_mode):
    stub.dm_status = 403
    problems = await api.check_reachability()
    assert len(problems) == 1
    assert "shares a server" in problems[0]


async def test_dm_bad_request_points_at_the_user_id(api, stub, dm_mode):
    stub.dm_status = 400
    problems = await api.check_reachability()
    assert len(problems) == 1
    assert "DISCORD_USER_ID" in problems[0]


async def test_channel_is_not_probed_in_dm_mode(api, stub, dm_mode):
    # A stale DISCORD_CHANNEL_ID left in .env must not fail startup when DM
    # mode is the one actually in use.
    stub.channel_status = 404
    assert await api.check_reachability() == []


# ---------------------------------------------------------------- channel mode

async def test_channel_with_all_permissions_passes(api, channel_mode):
    assert await api.check_reachability() == []


async def test_invisible_channel_is_reported(api, stub, channel_mode):
    stub.channel_status = 404
    problems = await api.check_reachability()
    assert len(problems) == 1
    assert "DISCORD_CHANNEL_ID" in problems[0]


async def test_missing_attach_files_is_named(api, stub, channel_mode):
    stub.everyone_perms = ALL_REQUIRED & ~PERM_ATTACH_FILES
    problems = await api.check_reachability()
    assert len(problems) == 1
    assert "Attach Files" in problems[0]


async def test_missing_read_history_is_named(api, stub, channel_mode):
    # Needed to resolve a chunk's attachment URL on download; a bot that can
    # upload but not re-read produces write-only storage.
    stub.everyone_perms = ALL_REQUIRED & ~PERM_READ_MESSAGE_HISTORY
    problems = await api.check_reachability()
    assert "Read Message History" in problems[0]


async def test_every_missing_permission_is_listed_at_once(api, stub, channel_mode):
    stub.everyone_perms = PERM_VIEW_CHANNEL
    problems = await api.check_reachability()
    assert len(problems) == 1
    for name in ("Send Messages", "Attach Files", "Read Message History"):
        assert name in problems[0]


async def test_administrator_bypasses_the_individual_checks(api, stub, channel_mode):
    stub.everyone_perms = PERM_ADMINISTRATOR
    assert await api.check_reachability() == []


async def test_permission_from_a_role_counts(api, stub, channel_mode):
    stub.everyone_perms = PERM_VIEW_CHANNEL
    stub.member_roles = ["role-1"]
    stub.member_role_perms = {"role-1": ALL_REQUIRED}
    assert await api.check_reachability() == []


async def test_channel_overwrite_denying_a_role_is_honoured(api, stub, channel_mode):
    # Guild-wide grant, revoked on this one channel: the common way a bot
    # looks configured and still cannot post.
    stub.everyone_perms = ALL_REQUIRED
    stub.overwrites = [
        {"id": GUILD_ID, "type": 0, "allow": "0", "deny": str(PERM_ATTACH_FILES)},
    ]
    problems = await api.check_reachability()
    assert "Attach Files" in problems[0]


async def test_a_member_overwrite_restores_a_denied_permission(api, stub, channel_mode):
    stub.everyone_perms = ALL_REQUIRED
    stub.overwrites = [
        {"id": GUILD_ID, "type": 0, "allow": "0", "deny": str(PERM_ATTACH_FILES)},
        {"id": BOT_ID, "type": 1, "allow": str(PERM_ATTACH_FILES), "deny": "0"},
    ]
    assert await api.check_reachability() == []


async def test_a_role_allow_beats_another_roles_deny(api, stub, channel_mode):
    # Discord accumulates role overwrites before applying them, so a deny on
    # one role must not cancel an allow on another. Applying them one role at
    # a time would get this backwards depending on iteration order.
    stub.everyone_perms = ALL_REQUIRED
    stub.member_roles = ["role-deny", "role-allow"]
    stub.member_role_perms = {"role-deny": 0, "role-allow": 0}
    stub.overwrites = [
        {"id": "role-deny", "type": 0, "allow": "0", "deny": str(PERM_ATTACH_FILES)},
        {"id": "role-allow", "type": 0, "allow": str(PERM_ATTACH_FILES), "deny": "0"},
    ]
    assert await api.check_reachability() == []


async def test_dm_style_channel_skips_permission_checks(api, stub, channel_mode):
    # A channel with no guild_id is a DM; guild permissions do not apply and
    # asking for roles would 404.
    stub.guild_id = None
    assert await api.check_reachability() == []


async def test_unreadable_permissions_do_not_block_startup(api, stub, channel_mode):
    # The bot can see the channel but the guild lookup failed. Refusing to
    # start here would punish a working setup for an inconclusive probe.
    stub.member_status = 403
    assert await api.check_reachability() == []
