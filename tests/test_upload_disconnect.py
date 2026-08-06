"""An upload whose connection really is cut, at the socket.

The gap this closes, recorded in ROADMAP.md: every existing test of the failure
path injects the failure into the *fake* -- `FakeDiscord.fail_uploads_from`
raises, and the handler unwinds. That covers what happens once an exception
reaches `upload()`, and says nothing about whether cutting a client off
produces one. Those are different claims, and the second is the one a person
closing a laptop lid actually exercises.

It turned out not to, and the answer is worse than "the cleanup is wrong": there
is no cleanup, because nothing reports a failure. aiohttp **cancels the handler
task** when the client goes away, and `asyncio.CancelledError` is a
`BaseException` -- so `upload()`'s `except Exception` never sees it, the
`finally` runs `handle.close()`, and closing is what committing means. The
drive gains a file listed at its truncated size with nothing anywhere saying it
is short.

Fixed on 2026-08-06, and only in that order: routing a disconnect into
`DiscordFile._rollback()` exposes a second and worse defect -- see
`test_rollback_leaves_the_directory.py` -- where unwinding a newly created file
left the parent's entry tag covering a child that no longer existed and the
directory stopped listing entirely. Fixing this one alone would have turned
"one truncated file" into "the folder it was in is unusable", so the rollback
went first. `upload()` now aborts the handle instead of closing it, which is
the difference between unwinding and committing.

Discord is still a fake here. What is real is the disconnect.
"""

import asyncio

import pytest

from src import web as web_mod
from src.vfs import ROOT_ID, DiscordVFS
from tests.conftest import TEST_CHUNK_SIZE
from tests.test_web import _client_for, csrf, sign_in


@pytest.fixture
async def finished():
    """Set once the upload handler returns, however it returns."""
    return asyncio.Event()


@pytest.fixture
async def app(fake_db, fake_discord, account, finished):
    """The real app, with one wrapper around `upload` that records completion.

    Waiting on this rather than on a timeout is what keeps the assertions
    honest. The obvious alternative -- poll until the fake's store is empty --
    is the property under test, so waiting for it would turn a real failure
    into a timeout and a passing run into a tautology. This waits for the
    *handler* to be done, and asserts afterwards.
    """
    application = web_mod.create_app()

    async def watched(request):
        try:
            return await web_mod.upload(request)
        finally:
            finished.set()

    # The route, not the module attribute: `create_app` has already captured
    # the function object by the time this runs.
    for resource in application.router.resources():
        for route in resource:
            if route.method == "PUT" and resource.canonical == "/api/file":
                route._handler = watched
    return application


@pytest.fixture
async def client(app):
    c = _client_for(app)
    await c.start_server()
    try:
        yield c
    finally:
        await c.close()


async def _cut_mid_upload(client, path, *, headers, sent_bytes, declared):
    """Send `sent_bytes` of a `declared`-byte body, then destroy the socket.

    A Content-Length larger than what arrives, followed by the transport going
    away, is what a dropped connection looks like from the server's side: a
    body that stops early and never completes.
    """
    session = client.session

    async def body():
        yield b"\x00" * sent_bytes
        await asyncio.sleep(3600)   # never finishes; the abort below ends it

    request = session.put(
        client.make_url(f"/api/file?path={path}"),
        data=body(),
        headers={**headers, "Content-Length": str(declared)},
    )
    task = asyncio.create_task(request.__aenter__())
    await asyncio.sleep(0.2)        # let the server read and buffer the prefix
    task.cancel()
    try:
        await task
    except BaseException:           # noqa: BLE001 -- cancellation, by design
        pass
    # Closing the connector is what actually reaches the server as a truncated
    # request rather than merely abandoning it on this side.
    await session.connector.close()


async def _cut_and_wait(client, fake_discord, finished, *, path="/cut.bin"):
    headers = csrf(await sign_in(client))
    await _cut_mid_upload(client, path, headers=headers,
                          sent_bytes=TEST_CHUNK_SIZE * 2,
                          declared=TEST_CHUNK_SIZE * 8)

    # Crossing a chunk boundary is the precondition. Below it nothing reaches
    # Discord until close, and an empty store afterwards would prove nothing.
    assert fake_discord.uploads > 0, (
        "nothing reached Discord before the disconnect, so this would pass "
        "without exercising the unwind at all")

    await asyncio.wait_for(finished.wait(), timeout=30)


async def test_a_cut_upload_is_not_committed_as_a_whole_file(
        client, fake_discord, finished, fake_db):
    await _cut_and_wait(client, fake_discord, finished)

    live = [node for node in fake_db.nodes.docs
            if not node.get("is_dir") and not node.get("trashed_at")]
    assert live == [], (
        "a body that stopped early was committed as a complete file; it would "
        "list at its truncated size with nothing to say it is short")


async def test_a_cut_connection_leaves_no_attachment_behind(
        client, fake_discord, finished):
    await _cut_and_wait(client, fake_discord, finished)

    assert fake_discord.delete_attempts > 0, "the unwind must actually have run"
    assert fake_discord.store == {}, (
        "chunks written before the disconnect are referenced by nothing and "
        "must have been released")


async def test_the_drive_is_still_usable_after_a_cut_upload(
        client, app, fake_discord, finished):
    # The failure has to stay confined to the one upload.
    await _cut_and_wait(client, fake_discord, finished)

    fresh = _client_for(app)
    await fresh.start_server()
    try:
        headers = csrf(await sign_in(fresh))
        written = await fresh.put("/api/file?path=/after.txt",
                                  data=b"still works", headers=headers)
        assert written.status == 201, await written.text()

        listing = await (await fresh.get("/api/files?path=/")).json()
        names = sorted(entry["name"] for entry in listing["entries"])
        assert names == ["after.txt"], (
            f"the interrupted upload left something behind: {names}")
    finally:
        await fresh.close()


async def test_a_cut_overwrite_leaves_the_original_file_intact(
        client, fake_discord, finished, master_key):
    """The scenario the copy-on-write overwrite exists for, driven end to end.

    `PUT /api/file` always opened with `truncate=True`, and truncating
    committed the file empty and deleted its attachments before a single byte
    of the new body had been read. Dropping the connection part way through
    replacing a large file therefore destroyed the file being replaced, with
    nothing recoverable anywhere: the trash was never involved, and
    `_rollback` restores nothing for a file this handle did not create --
    correctly, since by then there was nothing left to restore.

    Everything below the disconnect is identical to the tests above. The only
    difference is that the path already holds a file, which is the difference
    that used to lose data.
    """
    headers = csrf(await sign_in(client))
    original = b"o" * (TEST_CHUNK_SIZE * 2 + 3)
    seeded = await client.put("/api/file?path=/keep.bin", data=original,
                              headers=headers)
    assert seeded.status == 201, await seeded.text()

    # The seeding PUT went through the same wrapped handler.
    finished.clear()
    before = fake_discord.uploads

    await _cut_mid_upload(client, "/keep.bin", headers=headers,
                          sent_bytes=TEST_CHUNK_SIZE * 2,
                          declared=TEST_CHUNK_SIZE * 8)
    assert fake_discord.uploads > before, (
        "nothing of the replacement reached Discord, so this would pass "
        "without exercising the overwrite at all")
    await asyncio.wait_for(finished.wait(), timeout=30)

    # Checked through a VFS that shares nothing with the request that died,
    # rather than through a second HTTP client: the disconnect has already
    # destroyed this client's connector, and standing another server up on the
    # same application object starts a second pair of sweeper tasks over the
    # first. What is under test here is what survived in the database, and
    # opening a file re-reads it and re-verifies its tag.
    survivor = DiscordVFS(master_key, ROOT_ID)
    handle = await survivor.open("/keep.bin", read=True, write=False)
    assert (handle.size, await handle.read_at(0, handle.size)) \
        == (len(original), original), (
        "the interrupted overwrite damaged the file it was replacing")
    await handle.close()

    assert [e["filename"] for e in await survivor.list_dir("/")] == ["keep.bin"]
    assert await survivor.list_trash() == [], (
        "an overwrite that never completed must not trash the original")


async def test_a_complete_upload_is_unaffected(client, fake_discord):
    # The guard must not fire on the ordinary path. A check that rejected
    # healthy uploads would be a worse bug than the one it fixes.
    headers = csrf(await sign_in(client))
    payload = b"z" * (TEST_CHUNK_SIZE * 2 + 17)
    written = await client.put("/api/file?path=/whole.bin", data=payload,
                               headers=headers)
    assert written.status == 201, await written.text()

    read_back = await client.get("/api/file?path=/whole.bin")
    assert read_back.status == 200
    assert await read_back.read() == payload


async def test_a_chunked_upload_without_a_length_still_completes(client):
    # `_incomplete_body` compares against Content-Length, which a chunked body
    # does not have. It has to fall through to "finished", not to "truncated".
    headers = csrf(await sign_in(client))
    payload = b"q" * (TEST_CHUNK_SIZE + 5)

    async def body():
        yield payload[:100]
        yield payload[100:]

    written = await client.put("/api/file?path=/chunked.bin", data=body(),
                               headers=headers)
    assert written.status == 201, await written.text()
    assert (await (await client.get("/api/file?path=/chunked.bin")).read()) == payload


def test_the_route_wrapper_actually_wrapped_something(app):
    # Without this, a change to the routing table would leave every test above
    # waiting on an Event nobody sets, and the timeout would read like a
    # product bug rather than like scaffolding that stopped matching.
    handlers = [route.handler for resource in app.router.resources()
                for route in resource
                if route.method == "PUT" and resource.canonical == "/api/file"]
    assert handlers and all(h is not web_mod.upload for h in handlers)
