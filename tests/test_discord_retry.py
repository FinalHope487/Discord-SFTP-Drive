"""429 retry behaviour, against a local stand-in for the Discord API.

The bug this guards: `_request` used to retry with the same
`aiohttp.FormData` instance, which aiohttp refuses to send twice ("Form data
has been processed already"). A 1GB upload is roughly 114 messages, so hitting
at least one rate limit is close to certain -- and every one of them failed
outright instead of retrying. The fix passes a `data_factory` that builds a
fresh body per attempt.

Asserting on the *bodies the server received* rather than on the return value
is the point: a retry that sends an empty or truncated body would still look
like a success from the client side.
"""

import pytest
from aiohttp import web

import src.discord_api as api_mod

PAYLOAD = b"\x00\x01\x02\x03" + b"chunk-payload" * 100

# The API's own retry budget. Kept in sync deliberately -- the exhaustion test
# below is meaningless if it does not match.
MAX_ATTEMPTS = 5


class DiscordStub:
    """Fails the first `fail_count` uploads with a 429, then succeeds.

    `fail_count = None` means never succeed, for the exhaustion case.
    """

    def __init__(self):
        self.fail_count = 0
        self.bodies = []

    @property
    def calls(self):
        return len(self.bodies)

    async def handle(self, request):
        reader = await request.multipart()
        field = await reader.next()
        self.bodies.append(await field.read())

        if self.fail_count is None or self.calls <= self.fail_count:
            # Discord's real shape: retry_after in the JSON body and a header.
            return web.json_response(
                {"retry_after": 0.02, "global": False},
                status=429,
                headers={"Retry-After": "0.02"},
            )

        return web.json_response(
            {
                "id": "msg-1",
                "attachments": [
                    {"url": "https://cdn.test/msg-1", "size": len(self.bodies[-1])}
                ],
            }
        )


@pytest.fixture
async def discord_stub():
    stub = DiscordStub()
    app = web.Application()
    app.router.add_post("/api/v10/channels/{cid}/messages", stub.handle)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    stub.base_url = f"http://127.0.0.1:{runner.addresses[0][1]}/api/v10"

    try:
        yield stub
    finally:
        await runner.cleanup()


@pytest.fixture
async def api(discord_stub):
    client = api_mod.DiscordAPI()
    client.base_url = discord_stub.base_url
    # Pre-set so the test never tries to open a real DM channel.
    client.dm_channel_id = "chan-1"
    try:
        yield client
    finally:
        await client.close()


async def test_upload_succeeds_after_one_429(api, discord_stub):
    discord_stub.fail_count = 1
    message_id, _url, size = await api.upload_chunk(PAYLOAD, "c0.bin")
    assert (message_id, size) == ("msg-1", len(PAYLOAD))


async def test_a_429_costs_exactly_one_extra_attempt(api, discord_stub):
    discord_stub.fail_count = 1
    await api.upload_chunk(PAYLOAD, "c0.bin")
    assert discord_stub.calls == 2


async def test_every_attempt_carries_the_full_body(api, discord_stub):
    discord_stub.fail_count = 1
    await api.upload_chunk(PAYLOAD, "c0.bin")
    assert all(body == PAYLOAD for body in discord_stub.bodies)


async def test_survives_three_consecutive_429s(api, discord_stub):
    discord_stub.fail_count = 3
    message_id, _url, size = await api.upload_chunk(PAYLOAD, "c0.bin")
    assert (message_id, size) == ("msg-1", len(PAYLOAD))
    assert all(body == PAYLOAD for body in discord_stub.bodies)


async def test_exhausted_retries_raise_a_retry_error(api, discord_stub):
    # Specifically not a RuntimeError about consumed form data, which is how
    # the original bug surfaced.
    discord_stub.fail_count = None
    with pytest.raises(Exception, match="Max retries exceeded"):
        await api.upload_chunk(PAYLOAD, "c0.bin")


async def test_exhaustion_uses_the_whole_retry_budget(api, discord_stub):
    discord_stub.fail_count = None
    with pytest.raises(Exception, match="Max retries exceeded"):
        await api.upload_chunk(PAYLOAD, "c0.bin")
    assert discord_stub.calls == MAX_ATTEMPTS
