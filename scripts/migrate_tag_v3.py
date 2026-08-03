"""Move every node from tag version 2 to version 3.

Version 3 folds `trashed_at` into the node and directory tags. Nothing else
changed: chunk tags are untouched, and `dir_entries_tag` is byte for byte the
same function it was, so directory membership is never re-signed by this.

The rule this follows, and the reason it is safe to run: **verify under the old
tag before writing the new one.** A migration that simply recomputed tags would
sign off on whatever happened to be in the database, including a change made by
somebody who could not have produced a valid tag themselves -- which is the one
thing a backfill must never do (`ensure_root` refuses a non-empty untagged root
for the same reason). Anything that fails its version 2 tag stops the run with
its id named, and nothing is written.

Run it against a stopped-or-idle server. It is idempotent: nodes already at
version 3 are skipped, so an interrupted run can simply be repeated.

    docker compose exec -T sftp-discord-server python - < scripts/migrate_tag_v3.py
"""

import asyncio
import hmac
import os
import sys

from src import keystore, users
from src.crypto import _length_prefixed, _MAC_INFO, _name, _subkey
from src.db import Database, db
from src.vfs import TAG_VERSION, _dir_mac, _file_mac, _chunk_tags


def _v2_file_tag(key, node):
    """`node_tag` exactly as version 2 computed it, domain separator and all."""
    mac = hmac.new(_subkey(key, _MAC_INFO), digestmod="sha256")
    mac.update(b"node2")
    mac.update(_length_prefixed(node["id"].encode("utf-8")))
    mac.update(_length_prefixed((node.get("parent_id") or "").encode("utf-8")))
    mac.update(_length_prefixed(_name(node.get("filename") or "")))
    mac.update(int(node.get("size") or 0).to_bytes(8, "big"))
    chunk_tags = _chunk_tags(node)
    mac.update(len(chunk_tags).to_bytes(8, "big"))
    for tag in chunk_tags:
        mac.update(_length_prefixed(bytes.fromhex(tag)))
    return mac.hexdigest()


def _v2_dir_tag(key, node):
    """`dir_tag` exactly as version 2 computed it."""
    mac = hmac.new(_subkey(key, _MAC_INFO), digestmod="sha256")
    mac.update(b"dir")
    mac.update(_length_prefixed(node["id"].encode("utf-8")))
    mac.update(_length_prefixed((node.get("parent_id") or "").encode("utf-8")))
    mac.update(_length_prefixed(_name(node.get("filename") or "")))
    return mac.hexdigest()


async def migrate(username: str, password: str, *, apply: bool) -> int:
    await Database.connect()
    user = await db.get_db().users.find_one({"username": username})
    if user is None:
        raise SystemExit(f"no such account: {username!r}")

    key = await keystore.open_master_key(users.keystore_id(user), password)

    nodes = await db.get_db().nodes.find({}).to_list(length=None)
    mine = [n for n in nodes if await _in_tree(n, user["root_id"])]

    stale = [n for n in mine if n.get("tag_version") != TAG_VERSION]
    print(f"{len(mine)} node(s) in this tree, {len(stale)} at an older version")

    bad = []
    for node in stale:
        expected = _v2_dir_tag(key, node) if node.get("is_dir") \
            else _v2_file_tag(key, node)
        if not hmac.compare_digest(expected, node.get("mac") or ""):
            bad.append(node)

    if bad:
        for node in bad[:20]:
            print(f"  FAILED version 2 check: {node.get('filename')!r} "
                  f"({node['id']})")
        raise SystemExit(
            f"\n{len(bad)} node(s) do not verify under the tag they already "
            "carry. Nothing has been written. These were changed by something "
            "without the key, and re-tagging them would certify that change "
            "as authentic -- resolve them by hand first.")

    if not apply:
        print("\nAll verified. Re-run with --apply to write version 3 tags.")
        return 0

    for node in stale:
        # `trashed_at` is absent on every version 2 node -- the field did not
        # exist -- so the new tag is computed over exactly the state that just
        # verified, plus "not trashed", which is what it was.
        fresh = _dir_mac(key, node) if node.get("is_dir") else _file_mac(key, node)
        await db.get_db().nodes.update_one(
            {"id": node["id"]},
            {"$set": {"mac": fresh, "tag_version": TAG_VERSION}})

    print(f"\nRe-tagged {len(stale)} node(s) at version {TAG_VERSION}.")
    return len(stale)


async def _in_tree(node, root_id) -> bool:
    """Whether this node hangs off the given root, following parent ids."""
    seen = set()
    current = node
    while current is not None and current["id"] not in seen:
        if current["id"] == root_id:
            return True
        seen.add(current["id"])
        parent_id = current.get("parent_id")
        if not parent_id:
            return False
        current = await db.get_db().nodes.find_one({"id": parent_id})
    return False


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    user = os.environ["SFTP_USER"]
    password = os.environ["SFTP_PASSWORD"]
    asyncio.run(migrate(user, password, apply=apply))
