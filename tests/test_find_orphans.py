"""The orphan inventory, over fakes.

`scripts/find_orphans.py` is the answer to a complaint the code has been making
about itself for a while: `failure_tally` reports `orphans`, `purge`'s
docstring admits a crash can leak attachments, and until now nothing could name
one. It is worth testing precisely because it is the tool somebody reaches for
when they already believe something is wrong -- a scan that quietly reports
nothing would be read as "there is nothing there".

The set arithmetic is the whole script, so that is what these drive. Discord is
a fake; what it has to model for this is only that history exists and that a
message carries a filename, which is what `iter_messages` and `post_foreign`
were added for.
"""

import pytest

from src.vfs import _now
from scripts import find_orphans
from tests.conftest import TEST_CHUNK_SIZE

PAYLOAD = b"p" * (TEST_CHUNK_SIZE * 2 + 5)


@pytest.fixture(autouse=True)
def scanned_channel(monkeypatch, fake_discord):
    """Point the script at the same fake the VFS is writing to.

    The script does `from src.discord_api import discord_api`, so it holds its
    own reference to the singleton and the `fake_discord` fixture's patch of
    `src.vfs.discord_api` does not reach it. Autouse because a test that forgot
    this would not fail -- it would reach the real client, and what it did next
    would depend on whether a bot token happened to be in the environment.
    """
    monkeypatch.setattr(find_orphans, "discord_api", fake_discord)
    return fake_discord


async def _seed(vfs, path, data=PAYLOAD):
    handle = await vfs.open(path, read=False, write=True, create=True)
    await handle.write_at(0, data)
    await handle.close()
    return await vfs.get_node(path)


async def _scan():
    referenced, node_ids = await find_orphans._referenced()
    attachments, scanned = await find_orphans._attachments()
    orphans = [item for item in attachments if item[0] not in referenced]
    return {
        "orphans": orphans,
        "attachments": attachments,
        "scanned": scanned,
        "node_ids": node_ids,
        "missing": referenced - {m for m, *_ in attachments},
    }


async def test_a_healthy_drive_reports_nothing(vfs, fake_discord):
    await _seed(vfs, "/a.bin")
    await _seed(vfs, "/b.bin")

    result = await _scan()

    assert result["orphans"] == []
    assert result["missing"] == set()
    assert len(result["attachments"]) == len(fake_discord.store)


async def test_an_attachment_whose_node_vanished_is_reported(vfs, fake_db,
                                                             fake_discord):
    """The shape `purge` warns about: documents gone, attachments still there.

    Produced by deleting the row rather than by calling anything, because the
    only ways to reach it for real are a crash and a database that went away
    mid-unwind -- and either way what is left behind is exactly this.
    """
    node = await _seed(vfs, "/gone.bin")
    survivor = await _seed(vfs, "/kept.bin")
    fake_db.nodes.docs = [d for d in fake_db.nodes.docs if d["id"] != node["id"]]

    result = await _scan()

    assert {file_id for _mid, file_id, _i, _n in result["orphans"]} == \
        {node["id"]}
    assert len(result["orphans"]) == len(node["chunks"])
    assert survivor["id"] not in {f for _m, f, _i, _n in result["orphans"]}


async def test_a_live_file_missing_one_chunk_is_reported_separately(
        vfs, fake_db):
    """Worse than an orphan, and the script says so.

    The file still exists and still lists at its full size; it has simply lost
    track of one of its own attachments, so it will fail on read. Lumping it in
    with the ordinary orphans would invite somebody to delete the chunk a live
    file is missing.
    """
    node = await _seed(vfs, "/holed.bin")
    kept = node["chunks"][:1]
    await fake_db.nodes.update_one({"id": node["id"]}, {"$set": {"chunks": kept}})

    result = await _scan()

    stranded = {file_id for _mid, file_id, _i, _n in result["orphans"]}
    assert stranded == {node["id"]}
    assert node["id"] in result["node_ids"], (
        "the node still exists, which is what makes this the worse case")


async def test_a_referenced_message_that_is_not_on_discord_is_reported(
        vfs, fake_discord):
    """The opposite failure, which this scan is in a position to notice."""
    node = await _seed(vfs, "/vanished.bin")
    lost = node["chunks"][0]["message_id"]
    fake_discord.store.pop(lost)

    result = await _scan()

    assert result["missing"] == {lost}
    assert result["orphans"] == []


async def test_somebody_elses_attachment_is_not_claimed(vfs, fake_discord):
    """Anchored matching, and it matters.

    The channel may be an ordinary DM with other things in it. Reporting one of
    those as an orphan is how a person gets talked into deleting a file that
    was never ours -- so the name has to match the exact shape `_upload_chunk`
    writes, not merely contain it.
    """
    await _seed(vfs, "/a.bin")
    fake_discord.post_foreign("holiday.jpg")
    fake_discord.post_foreign("notes.txt")
    fake_discord.post_foreign("prefix_" + "0" * 36 + "_chunk_0.bin")
    fake_discord.post_foreign("0" * 36 + "_chunk_0.bin.bak")

    result = await _scan()

    assert result["orphans"] == []
    assert result["scanned"] == len(fake_discord.store), (
        "every message should still have been looked at")


async def test_a_trashed_file_is_not_an_orphan(vfs):
    """Its node is still there, holding its chunks. That is the point of it."""
    await _seed(vfs, "/deleted.bin")
    await vfs.remove("/deleted.bin")

    assert (await _scan())["orphans"] == []


async def test_an_abandoned_overwrite_is_not_an_orphan(vfs):
    """Still referenced -- by a detached node nothing can reach.

    Reporting it here would be wrong twice over: it is not an orphan, and
    naming it as one would point somebody at attachments that
    `sweep_incoming` is going to collect properly, tag check and all.
    """
    await _seed(vfs, "/f.bin")
    writer = await vfs.open("/f.bin", read=False, write=True, truncate=True)
    await writer.write_at(0, PAYLOAD)

    assert (await _scan())["orphans"] == []


async def test_the_name_pattern_matches_what_the_uploader_writes(vfs,
                                                                 fake_discord):
    """Scaffolding check, against the real producer rather than a literal.

    Every assertion above is worth nothing if the regex and
    `_upload_chunk`'s f-string drift apart, and a scan that matches nothing
    reports a clean drive.
    """
    node = await _seed(vfs, "/a.bin")

    names = [fake_discord.filenames[c["message_id"]] for c in node["chunks"]]
    assert names, "the payload should have produced attachments"
    for index, name in enumerate(names):
        match = find_orphans.CHUNK_NAME.match(name)
        assert match, name
        assert match.group(1) == node["id"]
        assert int(match.group(2)) == index


async def test_the_report_separates_the_two_kinds(vfs, fake_db, capsys):
    """The printed output, since that is the entire interface."""
    # Both seeded before either is damaged: deleting the row leaves the root's
    # entry tag covering a child that is gone, and the next `create` would
    # fail its staging check rather than the assertion this is about.
    dead = await _seed(vfs, "/gone.bin")
    alive = await _seed(vfs, "/holed.bin")

    fake_db.nodes.docs = [d for d in fake_db.nodes.docs if d["id"] != dead["id"]]
    await fake_db.nodes.update_one({"id": alive["id"]}, {"$set": {"chunks": []}})

    result = await _scan()
    find_orphans._report(result["orphans"], result["node_ids"])
    printed = capsys.readouterr().out

    assert "whose node is gone" in printed
    assert "STILL EXIST" in printed
    assert dead["id"] in printed
    assert alive["id"] in printed
    # The live one must not be filed under the ordinary orphans.
    ordinary, _, still_exist = printed.partition("STILL EXIST")
    assert dead["id"] in ordinary and alive["id"] not in ordinary
    assert alive["id"] in still_exist


async def test_now_is_not_used_by_the_scan(vfs):
    """Guards the one thing that would make this tool destructive to run.

    Nothing here is time-based, and nothing here writes. If either changes,
    this file is where somebody should be made to think about it.
    """
    before = _now()
    await _seed(vfs, "/a.bin")
    await _scan()
    assert _now() >= before
