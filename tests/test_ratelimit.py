"""Proactive rate-limit accounting.

The behaviour that matters is *waiting before being told to*. Asserting on
recorded header values alone would pass for a limiter that stores the numbers
and then ignores them, so the tests here assert on elapsed time and on the
order requests actually reach a stub server.

Timings are deliberately coarse (tens of milliseconds) -- this is checking
that a wait happened at all, not measuring its precision.
"""

import asyncio
import time

import pytest
from aiohttp import web

import src.discord_api as api_mod
from src.ratelimit import RateLimiter, route_key

PAYLOAD = b"chunk-payload" * 10


# --------------------------------------------------------------- route keys


def test_minor_ids_collapse_to_one_route():
    # Otherwise every message read would be tracked as its own bucket and the
    # accounting would never accumulate anything useful.
    a = route_key("GET", "/channels/111/messages/222")
    b = route_key("GET", "/channels/111/messages/333")
    assert a == b


def test_major_id_stays_in_the_route():
    # Discord buckets per channel, so two channels must not share accounting.
    a = route_key("POST", "/channels/111/messages")
    b = route_key("POST", "/channels/999/messages")
    assert a != b


def test_method_is_part_of_the_route():
    assert route_key("GET", "/channels/1/messages/2") != \
        route_key("DELETE", "/channels/1/messages/2")


# ------------------------------------------------------------ the limiter


def _headers(bucket="b1", remaining=5, reset_after=0.05):
    return {
        "X-RateLimit-Bucket": bucket,
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset-After": str(reset_after),
    }


async def test_an_unknown_route_does_not_wait():
    limiter = RateLimiter()
    start = time.monotonic()
    await limiter.acquire("GET /whatever")
    assert time.monotonic() - start < 0.02


async def test_exhausted_bucket_waits_for_the_reset():
    limiter = RateLimiter()
    route = "POST /channels/1/messages"
    limiter.update(route, _headers(remaining=0, reset_after=0.15))

    start = time.monotonic()
    await limiter.acquire(route)
    elapsed = time.monotonic() - start
    assert elapsed >= 0.1, f"did not wait for the bucket reset (waited {elapsed:.3f}s)"


async def test_a_bucket_with_allowance_left_does_not_wait():
    limiter = RateLimiter()
    route = "POST /channels/1/messages"
    limiter.update(route, _headers(remaining=3, reset_after=10))

    start = time.monotonic()
    await limiter.acquire(route)
    assert time.monotonic() - start < 0.02


async def test_an_elapsed_window_does_not_wait():
    # remaining=0 but the window is already over: the allowance has refreshed
    # even though no response has said so yet. Waiting here would stall for a
    # limit that no longer applies.
    limiter = RateLimiter()
    route = "POST /channels/1/messages"
    limiter.update(route, _headers(remaining=0, reset_after=0.01))
    await asyncio.sleep(0.05)

    start = time.monotonic()
    await limiter.acquire(route)
    assert time.monotonic() - start < 0.02


async def test_allowance_is_spent_across_successive_acquires():
    # Without the optimistic decrement, N concurrent callers would all read
    # the same "remaining" and overshoot together.
    limiter = RateLimiter()
    route = "POST /channels/1/messages"
    limiter.update(route, _headers(remaining=2, reset_after=0.15))

    await limiter.acquire(route)
    await limiter.acquire(route)

    start = time.monotonic()
    await limiter.acquire(route)   # allowance is now spent
    assert time.monotonic() - start >= 0.1


async def test_a_global_429_pauses_an_unrelated_route():
    limiter = RateLimiter()
    limiter.note_429({"X-RateLimit-Global": "true"}, 0.15)

    start = time.monotonic()
    await limiter.acquire("GET /users/@me")
    assert time.monotonic() - start >= 0.1


async def test_the_global_scope_header_is_also_honoured():
    limiter = RateLimiter()
    limiter.note_429({"X-RateLimit-Scope": "global"}, 0.15)

    start = time.monotonic()
    await limiter.acquire("GET /users/@me")
    assert time.monotonic() - start >= 0.1


async def test_a_route_scoped_429_does_not_pause_other_routes():
    # Blocking everything on a per-route limit would stall calls that are
    # still well within their own budget.
    limiter = RateLimiter()
    limiter.note_429({"X-RateLimit-Scope": "user"}, 5.0)

    start = time.monotonic()
    await limiter.acquire("GET /users/@me")
    assert time.monotonic() - start < 0.02


async def test_missing_bucket_header_is_ignored():
    # Not every Discord response carries the headers; the limiter must not
    # invent a bucket from a partial one.
    limiter = RateLimiter()
    limiter.update("POST /x", {"X-RateLimit-Remaining": "0"})
    start = time.monotonic()
    await limiter.acquire("POST /x")
    assert time.monotonic() - start < 0.02


async def test_unparseable_headers_do_not_raise():
    limiter = RateLimiter()
    limiter.update("POST /x", {
        "X-RateLimit-Bucket": "b1",
        "X-RateLimit-Remaining": "not-a-number",
        "X-RateLimit-Reset-After": "also-bad",
    })
    await limiter.acquire("POST /x")


async def test_concurrency_cap_limits_requests_in_flight():
    limiter = RateLimiter(max_concurrency=2)
    live = 0
    peak = 0

    async def one():
        nonlocal live, peak
        async with limiter.slot("POST /channels/1/messages"):
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.05)
            live -= 1

    await asyncio.gather(*(one() for _ in range(8)))
    assert peak <= 2, f"{peak} requests were in flight at once"


# --------------------------------------------------- wired into the client


class BucketStub:
    """Serves one message endpoint on an allowance of one request per window.

    Every response reports the allowance as already spent, so a client that
    reads the headers must wait out the window before sending again -- and one
    that ignores them sends immediately. The stub never returns a 429, so the
    difference is attributable to the headers alone.
    """

    RESET_AFTER = 0.1

    def __init__(self):
        self.times = []

    async def handle(self, request):
        reader = await request.multipart()
        field = await reader.next()
        body = await field.read()
        self.times.append(time.monotonic())

        # Report the size actually received. It used to be a hard-coded 1,
        # which no longer passes: the client now checks that Discord stored as
        # many bytes as it sent.
        return web.json_response(
            {"id": f"msg-{len(self.times)}",
             "attachments": [{"url": "https://cdn.test/x", "size": len(body)}]},
            headers={
                "X-RateLimit-Bucket": "bucket-a",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset-After": str(self.RESET_AFTER),
            },
        )


@pytest.fixture
async def bucket_stub():
    stub = BucketStub()
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


async def test_client_paces_itself_without_being_429ed(bucket_stub):
    client = api_mod.DiscordAPI(limiter=RateLimiter(max_concurrency=4))
    client.base_url = bucket_stub.base_url
    client.dm_channel_id = "chan-1"
    try:
        await client.upload_chunk(PAYLOAD, "c0.bin")
        await client.upload_chunk(PAYLOAD, "c1.bin")
    finally:
        await client.close()

    assert len(bucket_stub.times) == 2
    gap = bucket_stub.times[1] - bucket_stub.times[0]
    # The stub never returns a 429, so any delay here came from the client
    # reading the headers rather than from reacting to a rejection.
    assert gap >= BucketStub.RESET_AFTER * 0.8, \
        f"second request was not paced (gap {gap:.3f}s)"


async def test_pacing_does_not_consume_the_retry_budget(bucket_stub):
    # The point of the whole exercise: retries stay available for real
    # failures instead of being spent on predictable rate limits.
    client = api_mod.DiscordAPI(limiter=RateLimiter(max_concurrency=4))
    client.base_url = bucket_stub.base_url
    client.dm_channel_id = "chan-1"
    try:
        for i in range(4):
            await client.upload_chunk(PAYLOAD, f"c{i}.bin")
    finally:
        await client.close()

    # One request per upload means nothing was retried.
    assert len(bucket_stub.times) == 4
