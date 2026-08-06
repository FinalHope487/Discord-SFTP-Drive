"""List Discord attachments that no node points at any more. Reads only.

The gap this closes: `failure_tally` reports `orphans`, and `purge`'s docstring
admits a crash between deleting attachments and deleting documents can leave
some behind -- but nothing could tell you *which*, or even how many were really
out there. Somebody who saw `orphans=2` had no move available except retrying
the upload, which uploads a third copy.

An orphan is invisible from the database side. There is no row to notice the
absence of; the only way to see one is to ask Discord what it is holding and
subtract everything still referenced. That is all this does.

    docker compose exec -T sftp-discord-server python - < scripts/find_orphans.py

**It never deletes anything, and there is no flag that makes it.** That is not
timidity, it is what can currently be made safe. Deleting would mean trusting
the set of referenced message ids to be complete, and that set is read straight
out of `nodes.chunks` without checking any node's tag -- so anybody who could
edit the database could drop one chunk reference and have this tool destroy the
attachment for them. Verifying every tag first would make a delete mode safe,
and that needs the master key, which means the password, which is a different
tool with a different blast radius. Until then: this names them, and you decide.

What it cannot see, by design:

  * Attachments belonging to an overwrite that was abandoned mid-upload. Those
    are still referenced -- by a detached node no directory can reach -- so
    they are not orphans and would be wrong to report here.
    `DiscordVFS.sweep_incoming` is what collects them.
  * Anything in a channel other than the configured one.

Output is grouped by the file each orphan came from, which the attachment name
gives away: `{file_id}_chunk_{index}.bin`. A file id that still exists as a node
means the file is alive and missing a chunk -- worse than an orphan and worth
knowing separately, since that file will fail to read.
"""

import asyncio
import re
import sys
from collections import defaultdict

from src.db import Database, db
from src.discord_api import discord_api

# The name `_upload_chunk` builds. Anchored, so a message somebody else posted
# in the channel is never mistaken for ours -- reporting an unrelated
# attachment as an orphan is how a person gets talked into deleting it.
CHUNK_NAME = re.compile(r"^([0-9a-f-]{36})_chunk_(\d+)\.bin$")


async def _referenced():
    """Every message id the database still points at, and every node id."""
    nodes = await db.get_db().nodes.find({}).to_list(None)

    messages = set()
    for node in nodes:
        for chunk in node.get("chunks") or []:
            message_id = chunk.get("message_id")
            if message_id:
                messages.add(str(message_id))
    return messages, {node["id"] for node in nodes}


async def _attachments():
    """Our attachments on Discord, as `(message_id, file_id, index, name)`."""
    found = []
    scanned = 0
    async for message in discord_api.iter_messages():
        scanned += 1
        for attachment in message.get("attachments") or []:
            match = CHUNK_NAME.match(attachment.get("filename") or "")
            if match:
                found.append((str(message["id"]), match.group(1),
                              int(match.group(2)), attachment.get("filename")))
    return found, scanned


def _report(orphans, node_ids):
    by_file = defaultdict(list)
    for message_id, file_id, index, name in orphans:
        by_file[file_id].append((index, message_id, name))

    live = sorted(f for f in by_file if f in node_ids)
    dead = sorted(f for f in by_file if f not in node_ids)

    if dead:
        print(f"\n{len(dead)} file(s) whose node is gone -- ordinary orphans:")
        for file_id in dead:
            chunks = sorted(by_file[file_id])
            print(f"  {file_id}  ({len(chunks)} attachment(s))")
            for index, message_id, name in chunks:
                print(f"    chunk {index:<4} message {message_id}  {name}")

    if live:
        # A different and worse thing, so it is reported separately rather
        # than counted in with the rest.
        print(f"\n{len(live)} file(s) STILL EXIST but do not reference these "
              "attachments:")
        print("  Each of these is a live file with a chunk it has lost track "
              "of. It will fail to read.")
        for file_id in live:
            chunks = sorted(by_file[file_id])
            print(f"  {file_id}  ({len(chunks)} unreferenced attachment(s))")
            for index, message_id, name in chunks:
                print(f"    chunk {index:<4} message {message_id}  {name}")


async def main():
    await Database.connect()
    try:
        referenced, node_ids = await _referenced()
        attachments, scanned = await _attachments()
    finally:
        await discord_api.close()
        await Database.close()

    orphans = [item for item in attachments if item[0] not in referenced]

    print(f"Scanned {scanned} message(s) in the channel.")
    print(f"  {len(attachments)} attachment(s) look like ours.")
    print(f"  {len(referenced)} message id(s) still referenced by a node.")
    print(f"  {len(orphans)} orphan(s).")

    # Worth saying out loud rather than leaving as an empty section: a
    # referenced id with no attachment behind it is the opposite failure, and
    # this scan is in a position to notice it.
    missing = referenced - {message_id for message_id, *_ in attachments}
    if missing:
        print(f"\n{len(missing)} referenced message(s) are NOT on Discord. "
              "Those files cannot be read:")
        for message_id in sorted(missing):
            print(f"  {message_id}")

    _report(orphans, node_ids)

    if not orphans and not missing:
        print("\nNothing to do.")
    else:
        print("\nNothing was deleted. See this script's docstring for why.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
