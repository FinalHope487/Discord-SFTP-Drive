"""Signed attachment URLs that really do lapse, over real HTTP.

The gap this closes, recorded in ROADMAP.md: `FakeDiscord` hands out
`https://cdn.test/<id>` and it works for ever, so the whole of `_url_cache`'s
invalidation and every branch of the re-resolve had never run against a URL
that stopped working. A green suite said nothing about them.

So the stub here behaves the way the CDN does rather than the way a mock does.
It signs each URL with an `ex` expiry in hex epoch seconds, and it **checks
that parameter on the way in**: past its time, the download is a 404, exactly
as Discord answers a lapsed signature. Both mechanisms in `discord_api` then
have something real to be right or wrong about --

* the *predictive* one, where `_url_expiry` reads `ex` and `get_attachment_url`
  re-resolves before the margin runs out;
* the *reactive* one, where a URL that lapsed anyway comes back 403/404 and
  `download_attachment` resolves it a second time.

What this still is not: Discord. The stub agrees with the format Discord
documents and this client parses, and a change at Discord's end would not show
up here. It is real expiry over a real socket against a stand-in, which is a
long way from a fake that never expires and short of the live CDN.
"""

import time

import pytest
from aiohttp import web

import src.discord_api as api_mod

PAYLOAD = b"attachment-bytes" * 64


class CdnStub:
    """Discord's message API and its CDN, both enforcing the `ex` parameter.

    `issued` counts resolutions rather than downloads. It is the number the
    tests actually care about: a cache that silently re-resolved on every read
    would pass a bytes-equality assertion and still be the bug.
    """

    def __init__(self):
        self.lifetime = 3600
        self.issued = 0
        self.resolves = 0
        self.rejected = 0
        self.store = {"msg-1": PAYLOAD}
        # Set to a status to make the *next* download fail with it, whatever
        # the URL says. This is the race the reactive path exists for: a
        # signature that lapsed between resolving it and using it.
        self.reject_next_with = None

    def _sign(self, message_id: str) -> str:
        self.issued += 1
        expiry = int(time.time()) + self.lifetime
        # The serial is not cosmetic. Discord's `hm` differs per signature, and
        # without something varying here two URLs minted in the same second are
        # byte-identical -- which would make "the cached URL was replaced"
        # untestable for the reason that it looks true either way.
        # Hex epoch seconds in `ex`, which is the format `_url_expiry` parses.
        return (f"{self.base_url}/attachments/{message_id}"
                f"?ex={expiry:x}&is={int(time.time()):x}"
                f"&hm=deadbeef{self.issued:04x}")

    async def message(self, request):
        # Counted before the lookup, so a resolve that 404s still registers as
        # an attempt. `issued` only moves when a URL is actually handed out.
        self.resolves += 1
        message_id = request.match_info["mid"]
        if message_id not in self.store:
            return web.json_response({"message": "Unknown Message"}, status=404)
        return web.json_response({
            "id": message_id,
            "attachments": [{"url": self._sign(message_id),
                             "size": len(self.store[message_id])}],
        })

    async def attachment(self, request):
        message_id = request.match_info["mid"]
        if self.reject_next_with is not None:
            status, self.reject_next_with = self.reject_next_with, None
            self.rejected += 1
            return web.Response(status=status, text="signature rejected")

        raw = request.query.get("ex")
        if raw is not None and int(raw, 16) <= time.time():
            # What the CDN does with a lapsed signature, and the reason the
            # reactive branch exists at all.
            self.rejected += 1
            return web.Response(status=404, text="This content is no longer available.")

        return web.Response(body=self.store[message_id])


@pytest.fixture
async def cdn():
    stub = CdnStub()
    app = web.Application()
    app.router.add_get("/api/v10/channels/{cid}/messages/{mid}", stub.message)
    app.router.add_get("/attachments/{mid}", stub.attachment)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    stub.base_url = f"http://127.0.0.1:{port}"
    stub.api_url = f"http://127.0.0.1:{port}/api/v10"

    try:
        yield stub
    finally:
        await runner.cleanup()


@pytest.fixture
async def api(cdn):
    client = api_mod.DiscordAPI()
    client.base_url = cdn.api_url
    client.dm_channel_id = "chan-1"
    try:
        yield client
    finally:
        await client.close()


# ------------------------------------------------------------ the cache works


async def test_a_live_url_is_resolved_once_and_then_reused(api, cdn):
    # The reason the cache exists. Without this assertion the expiry tests
    # below would all pass against a client that never cached anything.
    assert await api.download_attachment("msg-1") == PAYLOAD
    assert await api.download_attachment("msg-1") == PAYLOAD
    assert cdn.issued == 1


async def test_the_expiry_is_parsed_off_the_url(api, cdn):
    await api.download_attachment("msg-1")
    _url, expiry = api._url_cache["msg-1"]
    assert expiry is not None
    assert abs(expiry - (time.time() + cdn.lifetime)) < 5


# ------------------------------------------------------- the predictive path


async def test_a_url_inside_the_margin_is_re_resolved_before_it_is_used(api, cdn):
    # The margin is the whole point: a URL that expires in one second is not
    # worth using, because the download will outlive it. Resolving early
    # costs one API call; getting it wrong costs a failed read.
    cdn.lifetime = api_mod._URL_EXPIRY_MARGIN // 2
    assert await api.download_attachment("msg-1") == PAYLOAD
    first = cdn.issued

    assert await api.download_attachment("msg-1") == PAYLOAD
    assert cdn.issued == first + 1, "a URL inside the margin must not be reused"
    assert cdn.rejected == 0, "and it must be replaced before it is rejected"


async def test_an_outright_expired_cache_entry_is_replaced(api, cdn):
    await api.download_attachment("msg-1")
    url, _ = api._url_cache["msg-1"]
    # Put the cached entry in the past rather than sleeping: the branch under
    # test is "the cached expiry has passed", and waiting an hour to reach it
    # would be the same assertion at a much worse price.
    api._url_cache["msg-1"] = (url, time.time() - 1)

    assert await api.download_attachment("msg-1") == PAYLOAD
    assert cdn.issued == 2
    assert cdn.rejected == 0


async def test_a_urls_without_an_expiry_is_kept(api, cdn):
    # `_url_expiry` returns None when the URL says nothing about expiry, and
    # None must mean "no reason to re-resolve" rather than "expired".
    api._url_cache["msg-1"] = ("http://example.invalid/x", None)
    url = await api.get_attachment_url("msg-1")
    assert url == "http://example.invalid/x"
    assert cdn.issued == 0


# -------------------------------------------------------- the reactive path


async def test_a_url_rejected_by_the_cdn_is_resolved_again_and_the_read_succeeds(
        api, cdn):
    # The predictive check is a prediction, and this is what happens when it is
    # wrong: the signature lapsed between resolving the URL and using it.
    await api.download_attachment("msg-1")
    assert cdn.issued == 1

    cdn.reject_next_with = 404
    assert await api.download_attachment("msg-1") == PAYLOAD
    assert cdn.rejected == 1
    assert cdn.issued == 2, "the retry must re-resolve rather than reuse"


async def test_a_403_is_treated_the_same_as_a_404(api, cdn):
    # Discord answers a bad signature with either, depending on which check
    # rejects it first. Handling only one leaves half the cases failing reads
    # that a second resolve would have fixed.
    await api.download_attachment("msg-1")
    cdn.reject_next_with = 403
    assert await api.download_attachment("msg-1") == PAYLOAD
    assert cdn.issued == 2


async def test_the_retry_happens_once_and_not_in_a_loop(api, cdn):
    # A URL that keeps being rejected is not a stale URL, and treating it as
    # one would be an unbounded loop against the CDN.
    await api.download_attachment("msg-1")
    cdn.store.pop("msg-1")
    cdn.reject_next_with = 404

    with pytest.raises(api_mod.DiscordAPIError):
        await api.download_attachment("msg-1")
    # Exactly one re-resolve, which then 404s at the message endpoint. Counted
    # as an attempt rather than as a URL, because no URL comes back from it.
    assert cdn.resolves == 2
    assert cdn.issued == 1


async def test_a_stale_cache_entry_is_not_left_behind_after_a_refresh(api, cdn):
    await api.download_attachment("msg-1")
    stale, _ = api._url_cache["msg-1"]
    cdn.reject_next_with = 404
    await api.download_attachment("msg-1")

    fresh, _ = api._url_cache["msg-1"]
    assert fresh != stale, "the rejected URL must not still be the cached one"


# ------------------------------------------------------------- the parser


@pytest.mark.parametrize("url,expected", [
    ("https://cdn.discordapp.com/attachments/1/2/f.bin?ex=68a1b2c3", 0x68a1b2c3),
    ("https://cdn.discordapp.com/attachments/1/2/f.bin?ex=68a1b2c3&is=1&hm=aa",
     0x68a1b2c3),
    ("https://cdn.discordapp.com/attachments/1/2/f.bin", None),
    ("https://cdn.discordapp.com/attachments/1/2/f.bin?ex=", None),
    ("https://cdn.discordapp.com/attachments/1/2/f.bin?ex=nothex", None),
])
def test_url_expiry_parsing(url, expected):
    # Hex, not decimal. Reading it as decimal would put every expiry in the
    # past and re-resolve on every single read -- a performance bug that no
    # correctness assertion would ever catch.
    assert api_mod._url_expiry(url) == expected
