"""What `_ensure_indexes` actually asks MongoDB for.

This file exists because of a bug the rest of the suite could not see. The
partial unique index on `(parent_id, filename)` was written with

    partialFilterExpression={"trashed_at": {"$exists": False}}

which MongoDB rejects outright -- `$exists: false` is not in the small grammar a
partialFilterExpression accepts, and the server refuses to create the index at
all: "Expression not supported in partial index: $not". `FakeDB` does not
validate index specifications, so 511 tests passed against an index that no real
deployment could ever build. It surfaced the first time the trash code met a
real MongoDB, as a server that would not start.

These tests cannot catch a rejection either -- only a real server can do that,
and `BUILD.md` says so. What they can do is pin the expression, so that changing
it back to something MongoDB refuses is a failing test rather than a discovery
made in production.
"""

import pytest

from src.db import Database


class _RecordingCollection:
    """Stands in for a Motor collection, remembering what was asked for."""

    def __init__(self, name, calls):
        self.name = name
        self._calls = calls

    async def create_index(self, keys, **options):
        self._calls.append({"collection": self.name, "keys": keys, **options})
        return "index_name"

    async def index_information(self):
        return {}


class _RecordingDB:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return _RecordingCollection(name, self.calls)


@pytest.fixture
async def index_calls(monkeypatch):
    recorder = _RecordingDB()
    monkeypatch.setattr(Database, "db", recorder)
    await Database._ensure_indexes()
    return recorder.calls


def _nodes_name_index(calls):
    for call in calls:
        if call["collection"] == "nodes" and call["keys"] == [("parent_id", 1),
                                                              ("filename", 1)]:
            return call
    raise AssertionError(f"no (parent_id, filename) index was requested: {calls}")


async def test_the_name_index_is_unique_and_partial(index_calls):
    index = _nodes_name_index(index_calls)
    assert index["unique"] is True
    assert "partialFilterExpression" in index


async def test_the_partial_filter_is_an_equality_on_null(index_calls):
    # Not `$exists: false`. MongoDB rejects that in a partial index; an equality
    # against null matches a null *and* a missing field, which is the same set
    # of documents and is inside the grammar. Verified against MongoDB 6.0:
    # two live siblings collide, a new file may take a trashed one's name, two
    # trashed nodes may share a name, and restoring onto a taken name is still
    # refused.
    assert _nodes_name_index(index_calls)["partialFilterExpression"] == {
        "trashed_at": None
    }


async def test_no_index_asks_for_an_operator_a_partial_filter_rejects(index_calls):
    # The grammar allows equality, `$exists: true`, the range operators,
    # `$type`, and `$and`/`$or`/`$in`. Everything below is outside it, and
    # asking for any of them makes the whole index fail to build -- which the
    # fake database will happily accept and a real one will not.
    forbidden = ("$exists", "$ne", "$not", "$nin", "$expr", "$regex")

    def walk(node, path="partialFilterExpression"):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$exists" and value is False:
                    raise AssertionError(f"{path}.$exists: false is rejected by MongoDB")
                if key in forbidden and key != "$exists":
                    raise AssertionError(f"{path}.{key} is not allowed in a partial filter")
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    for call in index_calls:
        walk(call.get("partialFilterExpression") or {})


def _trashed_at_index(calls):
    for call in calls:
        if call["collection"] == "nodes" and call["keys"] == "trashed_at":
            return call
    raise AssertionError(f"no trashed_at index was requested: {calls}")


async def test_the_trash_sweep_index_is_partial_and_not_unique(index_calls):
    # Partial so it holds only the trashed nodes: a live node has no
    # `trashed_at` at all, and indexing every node in every tree to find the
    # deleted few is the cost this index exists to remove. Not unique --
    # any number of things may be deleted in the same second.
    index = _trashed_at_index(index_calls)
    assert not index.get("unique")
    assert index["partialFilterExpression"] == {"trashed_at": {"$gt": 0}}


async def test_the_trash_queries_match_the_partial_filter(index_calls):
    # A partial index only serves a query the planner can prove is a subset of
    # the index's own filter, so these two have to agree. They live in
    # different files, and the failure when they drift is silent: the queries
    # keep returning the right documents by scanning the collection, which is
    # exactly the state this index was added to leave.
    from src.vfs import _TRASHED

    assert _trashed_at_index(index_calls)["partialFilterExpression"] == {
        "trashed_at": _TRASHED
    }


async def test_the_accounts_and_id_indexes_are_unique(index_calls):
    # Two rows sharing a username would make which account a password opens
    # depend on insertion order -- with a master key each, that is one user
    # reaching another user's tree.
    wanted = {("users", "username"), ("users", "id"),
              ("nodes", "id"), ("keystore", "id")}
    # `keys` is a string for a single-field index and a list of pairs for a
    # compound one, so it is normalised before going into a set.
    got = {
        (c["collection"],
         c["keys"] if isinstance(c["keys"], str) else tuple(map(tuple, c["keys"])))
        for c in index_calls if c.get("unique")
    }
    assert wanted <= got, f"missing unique indexes: {wanted - got}"
