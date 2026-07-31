"""Everything `_request` and the download path do when Discord misbehaves.

`test_discord_retry.py` covers 429 and the form-data-reuse bug. This covers
the rest of the failure surface, all of it against a real local HTTP server
rather than a mock, because the behaviours here are aiohttp's as much as ours:

* 5xx, which used to propagate straight to the client as a failed upload;
* transport failures -- a dropped connection mid-upload did the same;
* signed attachment URLs, which expire (24h when measured against the real
  CDN) and previously had no path back to a fresh one;
* the bot token, which had no business being sent to the CDN host.
"""

import time

import pytest
from aiohttp import web

import src.discord_api as api_mod

PAYLOAD = b"chunk-payload" * 100
MAX_ATTEMPTS = api_mod._MAX_ATTEMPTS


class Stub:
    """Serves the three API routes the client uses, plus a stand-in CDN."""

    def __init__(self):
        self.upload_statuses = []      # popped per upload before succeeding
        self.cdn_statuses = []         # popped per download before succeeding
        self.abort_uploads = 0         # kill the connection this many times
        self.url_lifetime = 3600       # seconds until the signature expires
        self.reported_size = None      # None means "report what was received"

        self.uploads = 0
        self.url_lookups = 0
        self.downloads = 0
        self.cdn_auth_seen = []
        self.deleted = []
        self.attachments = {}
        self.base_url = None

    # -------------------------------------------------------------- routes

    def _url_for(self, message_id):
        expiry = int(time.time()) + self.url_lifetime
        return f"{self.base_url}/cdn/{message_id}?ex={expiry:x}&is=0&hm=stub"

    async def upload(self, request):
        self.uploads += 1
        if self.uploads <= self.abort_uploads:
            # A connection that dies mid-request, which aiohttp surfaces as a
            # ClientError rather than any HTTP status.
            request.transport.abort()
            raise ConnectionResetError("simulated disconnect")

        reader = await request.multipart()
        field = await reader.next()
        body = await field.read()

        if self.upload_statuses:
            return web.json_response({"message": "upstream"},
                                     status=self.upload_statuses.pop(0))

        message_id = f"msg-{self.uploads}"
        self.attachments[message_id] = body
        size = len(body) if self.reported_size is None else self.reported_size
        return web.json_response({
            "id": message_id,
            "attachments": [{"url": self._url_for(message_id), "size": size}],
        })

    async def message(self, request):
        self.url_lookups += 1
        message_id = request.match_info["mid"]
        if message_id not in self.attachments:
            return web.json_response({"message": "Unknown Message"}, status=404)
        return web.json_response({
            "id": message_id,
            "attachments": [{"url": self._url_for(message_id),
                             "size": len(self.attachments[message_id])}],
        })

    async def delete(self, request):
        self.deleted.append(request.match_info["mid"])
        self.attachments.pop(request.match_info["mid"], None)
        return web.Response(status=204)

    async def cdn(self, request):
        self.downloads += 1
        self.cdn_auth_seen.append(request.headers.get("Authorization"))

        if self.cdn_statuses:
            return web.Response(status=self.cdn_statuses.pop(0), text="nope")

        expiry = request.query.get("ex")
        if expiry and int(expiry, 16) < time.time():
            # What the real CDN does with a lapsed signature.
            return web.Response(status=403, text="expired")

        message_id = request.match_info["mid"]
        if message_id not in self.attachments:
            return web.Response(status=404, text="gone")
        return web.Response(body=self.attachments[message_id])


@pytest.fixture
async def stub():
    s = Stub()
    app = web.Application()
    app.router.add_post("/api/v10/channels/{cid}/messages", s.upload)
    app.router.add_get("/api/v10/channels/{cid}/messages/{mid}", s.message)
    app.router.add_delete("/api/v10/channels/{cid}/messages/{mid}", s.delete)
    app.router.add_get("/cdn/{mid}", s.cdn)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    s.base_url = f"http://127.0.0.1:{runner.addresses[0][1]}"

    try:
        yield s
    finally:
        await runner.cleanup()


@pytest.fixture
async def api(stub, monkeypatch):
    # No jitter sleeps: the backoff schedule is not what these tests are for,
    # and five real waits per test would dominate the suite.
    monkeypatch.setattr(api_mod, "_BACKOFF_BASE", 0.001)
    monkeypatch.setattr(api_mod, "_BACKOFF_CAP", 0.002)

    client = api_mod.DiscordAPI()
    client.base_url = f"{stub.base_url}/api/v10"
    client.dm_channel_id = "chan-1"
    try:
        yield client
    finally:
        await client.close()


# --------------------------------------------------------------------- 5xx


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_server_errors_are_retried(api, stub, status):
    stub.upload_statuses = [status, status]
    message_id, _url, size = await api.upload_chunk(PAYLOAD, "c.bin")
    assert size == len(PAYLOAD)
    assert stub.uploads == 3, "the 5xx responses did not cost an attempt each"
    assert stub.attachments[message_id] == PAYLOAD


async def test_persistent_server_errors_give_up_with_the_status(api, stub):
    stub.upload_statuses = [503] * MAX_ATTEMPTS
    with pytest.raises(api_mod.DiscordAPIError) as caught:
        await api.upload_chunk(PAYLOAD, "c.bin")
    assert caught.value.status == 503
    assert stub.uploads == MAX_ATTEMPTS


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_client_errors_are_not_retried(api, stub, status):
    # Retrying these burns the budget that exists for transient faults, and
    # none of them will ever succeed on a second try.
    stub.upload_statuses = [status] * MAX_ATTEMPTS
    with pytest.raises(api_mod.DiscordAPIError) as caught:
        await api.upload_chunk(PAYLOAD, "c.bin")
    assert caught.value.status == status
    assert stub.uploads == 1


# --------------------------------------------------------------- transport


async def test_a_dropped_connection_is_retried(api, stub):
    stub.abort_uploads = 2
    message_id, _url, size = await api.upload_chunk(PAYLOAD, "c.bin")
    assert size == len(PAYLOAD)
    # The retry has to rebuild the body, not resend a consumed one.
    assert stub.attachments[message_id] == PAYLOAD


async def test_persistent_transport_failure_gives_up(api, stub):
    stub.abort_uploads = MAX_ATTEMPTS
    with pytest.raises(api_mod.DiscordAPIError) as caught:
        await api.upload_chunk(PAYLOAD, "c.bin")
    # No HTTP status ever arrived, so there is none to report.
    assert caught.value.status is None


# ------------------------------------------------------------ upload check


async def test_a_short_stored_attachment_is_rejected(api, stub):
    stub.reported_size = len(PAYLOAD) - 1
    with pytest.raises(api_mod.DiscordAPIError, match="Discord stored"):
        await api.upload_chunk(PAYLOAD, "c.bin")


async def test_a_rejected_upload_does_not_leave_the_message_behind(api, stub):
    stub.reported_size = 1
    with pytest.raises(api_mod.DiscordAPIError):
        await api.upload_chunk(PAYLOAD, "c.bin")
    assert stub.deleted == ["msg-1"], "the unusable attachment was orphaned"


# -------------------------------------------------------------- URL expiry


async def test_the_attachment_url_is_cached(api, stub):
    message_id, _url, _size = await api.upload_chunk(PAYLOAD, "c.bin")
    for _ in range(3):
        assert await api.download_attachment(message_id) == PAYLOAD
    # The upload response already carried a URL, so no lookup is needed at all.
    assert stub.url_lookups == 0
    assert stub.downloads == 3


async def test_the_url_cache_is_bounded_and_evicts_the_oldest(api, stub, monkeypatch):
    """Bounded, unlike `vfs._node_versions`, and safely so.

    A miss here costs one extra lookup; a miss there would mean "nobody
    changed this", which is a wrong answer rather than a slow one. That
    difference is why only one of the two has a cap.
    """
    monkeypatch.setattr(api_mod, "_URL_CACHE_SIZE", 3)

    ids = [(await api.upload_chunk(PAYLOAD, f"c{i}.bin"))[0] for i in range(4)]

    assert len(api._url_cache) == 3
    assert ids[0] not in api._url_cache, "the oldest entry was not evicted"

    # And the evicted one still works -- it just costs a lookup.
    assert await api.download_attachment(ids[0]) == PAYLOAD
    assert stub.url_lookups == 1


async def test_using_a_cached_url_keeps_it_from_being_evicted(api, stub, monkeypatch):
    monkeypatch.setattr(api_mod, "_URL_CACHE_SIZE", 2)

    first, second = [(await api.upload_chunk(PAYLOAD, f"c{i}.bin"))[0]
                     for i in range(2)]
    await api.download_attachment(first)          # first is now the recent one
    third, = [(await api.upload_chunk(PAYLOAD, "c2.bin"))[0]]

    assert first in api._url_cache
    assert third in api._url_cache
    assert second not in api._url_cache


async def test_an_expired_url_is_re_resolved_before_use(api, stub):
    stub.url_lifetime = -60           # already expired when handed over
    message_id, _url, _size = await api.upload_chunk(PAYLOAD, "c.bin")

    stub.url_lifetime = 3600          # a fresh lookup gets a usable one
    assert await api.download_attachment(message_id) == PAYLOAD
    assert stub.url_lookups == 1, "the lapsed URL was used without re-resolving"
    # The load-bearing half. Re-resolving only after the CDN rejects it also
    # ends with one lookup, so the lookup count alone cannot tell the two
    # apart -- the number of CDN requests can.
    assert stub.downloads == 1, (
        "the lapsed URL was tried first and only re-resolved after the CDN "
        "refused it, which costs a round trip on every expiry")


async def test_a_url_rejected_by_the_cdn_is_re_resolved_and_retried(api, stub):
    # The expiry check is a prediction; this is the path for when it is wrong.
    message_id, _url, _size = await api.upload_chunk(PAYLOAD, "c.bin")
    stub.cdn_statuses = [403]

    assert await api.download_attachment(message_id) == PAYLOAD
    assert stub.url_lookups == 1
    assert stub.downloads == 2


async def test_a_download_that_stays_rejected_raises(api, stub):
    message_id, _url, _size = await api.upload_chunk(PAYLOAD, "c.bin")
    stub.cdn_statuses = [403, 403]
    with pytest.raises(api_mod.DiscordAPIError) as caught:
        await api.download_attachment(message_id)
    assert caught.value.status == 403


async def test_cdn_server_errors_are_retried(api, stub):
    message_id, _url, _size = await api.upload_chunk(PAYLOAD, "c.bin")
    stub.cdn_statuses = [500, 503]
    assert await api.download_attachment(message_id) == PAYLOAD


async def test_deleting_a_message_drops_its_cached_url(api, stub):
    message_id, _url, _size = await api.upload_chunk(PAYLOAD, "c.bin")
    await api.delete_message(message_id)
    # Re-resolving is what turns this into a clean 404 rather than a stale
    # URL that happens to still be signed.
    with pytest.raises(api_mod.DiscordAPIError):
        await api.download_attachment(message_id)


# ------------------------------------------------------------- credentials


async def test_the_bot_token_is_never_sent_to_the_cdn(api, stub):
    message_id, _url, _size = await api.upload_chunk(PAYLOAD, "c.bin")
    await api.download_attachment(message_id)

    assert stub.downloads == 1
    assert stub.cdn_auth_seen == [None], (
        "the bot token was sent to the attachment host, which is a different "
        "origin and needs no credentials -- the URL is already signed"
    )
