"""One failed upload used to make the containing directory unlistable.

Fixed in `DiscordFile._rollback` on 2026-08-06; these are what hold it down.
The defect was not introduced by that change -- it reproduced through
`FakeDiscord.fail_uploads_from`, the same switch the existing failure tests
already used, so a Discord outage part way through writing a *new* file was
enough to trigger it. In the root directory that was the whole drive.

The mechanism, in three steps that are each individually reasonable:

1. Creating a node stages the parent's next entry tag with the new child added,
   inserts the node, and promotes the tag (`DiscordVFS.open`, via
   `_stage_entries`). So the parent's `entries_mac` now covers the child.
2. The upload fails part way. `DiscordFile._rollback()` releases the
   attachments and deletes the node document, which is correct and careful --
   its docstring is mostly about the thing it must *not* delete.
3. Nothing puts the parent's entry tag back. It still covers a child that no
   longer exists, and `verify_dir_entries` compares that tag against the
   children actually present.

From then on the directory fails verification on every read, and on every write
that has to restage it. There is no way back through the API: listing is the
operation that breaks.

Why the suite never caught it: the existing tests assert on the shape of the
500 that the failed upload returns -- `chunks_uploaded`, `orphans`,
`stale_node`, all correct -- and then stop. Nothing ever listed the directory
afterwards. The claim under test was "the upload reports what it left behind",
and it does; "the drive still works" was never asked.

The repair follows the three-step `purge()` already used: stage the parent's
next entry tag, make the change, promote the tag last, so a crash in between
leaves the parent holding a tag for one of the two possible child sets and
`verify_dir_entries` accepts either. If the staging itself fails -- which only
happens when the parent's tag already did not match its children -- the node is
left in place rather than deleted out of a directory whose tag still covers it,
and reported as `stale_node`.
"""

import pytest

from tests.conftest import TEST_CHUNK_SIZE
from tests.test_web import _client_for, csrf, sign_in


@pytest.fixture
async def app(fake_db, fake_discord, account):
    from src import web as web_mod
    return web_mod.create_app()


@pytest.fixture
async def client(app):
    c = _client_for(app)
    await c.start_server()
    try:
        yield c
    finally:
        await c.close()


async def _fail_an_upload(client, fake_discord, *, path="/boom.bin"):
    """Write a multi-chunk file whose second chunk cannot reach Discord."""
    headers = csrf(await sign_in(client))
    fake_discord.fail_uploads_from = 2
    response = await client.put(f"/api/file?path={path}",
                                data=b"x" * (TEST_CHUNK_SIZE * 3),
                                headers=headers)
    assert response.status == 500
    body = await response.json()
    assert body["code"] == "upload_failed"
    # The unwind did its own job correctly -- this is not a test about orphans.
    assert body["orphans"] == 0
    assert body["stale_node"] is False

    fake_discord.fail_uploads_from = None
    return headers


async def test_the_failed_upload_itself_reports_correctly(client, fake_discord):
    # Here so the tests below cannot be mistaken for the unwind being broken
    # in general. It releases what it uploaded, and says so.
    await _fail_an_upload(client, fake_discord)
    assert fake_discord.store == {}


async def test_the_directory_still_lists_after_a_failed_upload(
        client, fake_discord):
    await _fail_an_upload(client, fake_discord)

    listing = await client.get("/api/files?path=/")
    assert listing.status == 200, await listing.text()


async def test_another_file_can_still_be_written_after_a_failed_upload(
        client, fake_discord):
    headers = await _fail_an_upload(client, fake_discord)

    written = await client.put("/api/file?path=/after.txt", data=b"ok",
                               headers=headers)
    assert written.status == 201, await written.text()


async def test_a_subdirectory_is_affected_the_same_way(client, fake_discord):
    headers = csrf(await sign_in(client))
    made = await client.post("/api/dir", json={"path": "/docs"}, headers=headers)
    assert made.status == 201

    fake_discord.fail_uploads_from = 2
    failed = await client.put("/api/file?path=/docs/boom.bin",
                              data=b"x" * (TEST_CHUNK_SIZE * 3), headers=headers)
    assert failed.status == 500
    fake_discord.fail_uploads_from = None

    listing = await client.get("/api/files?path=/docs")
    assert listing.status == 200, await listing.text()


async def test_the_root_is_untouched_when_no_upload_failed(client, fake_discord):
    # The control. Without it the tests above could just as well mean
    # "listing is broken", which is a different and much easier bug.
    headers = csrf(await sign_in(client))
    written = await client.put("/api/file?path=/fine.txt", data=b"ok",
                               headers=headers)
    assert written.status == 201

    listing = await client.get("/api/files?path=/")
    assert listing.status == 200
    assert [e["name"] for e in (await listing.json())["entries"]] == ["fine.txt"]
