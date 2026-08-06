"""The parts of the SQLite backend the rest of the suite cannot reach.

Running the whole suite with `--db=sqlite` is the main check on
`src/sqlitedb.py`, and it is a much stronger one than anything here: it drives
the adapter through every path the drive actually takes, with assertions
written against MongoDB's behaviour. What it cannot do is test the places
where SQLite and MongoDB would *disagree*, because the code above the adapter
never asks a question whose answer differs -- that is the whole point of it.

So this file covers three things the suite structurally cannot:

  * the three semantics in `sqlitedb`'s docstring, two of which agree with
    MongoDB by luck rather than design and would break silently if the SQL
    were rewritten;
  * uniqueness, which `tests/fakes.py` says outright that it does not enforce,
    so nothing else in this suite has ever proved a duplicate is refused;
  * the failure modes that exist only here -- an index whose definition
    changed between releases, and a document that will not survive JSON.
"""

import pytest

from src.sqlitedb import DuplicateKey, SqliteDB, UnsupportedQuery


@pytest.fixture
def store(tmp_path):
    db = SqliteDB(str(tmp_path / "t.sqlite3"))
    yield db
    db.close()


@pytest.fixture
async def indexed(store):
    """The same indexes `Database._ensure_indexes` asks for, on `nodes`."""
    await store.nodes.create_index([("parent_id", 1), ("filename", 1)],
                                   unique=True,
                                   partialFilterExpression={"trashed_at": None})
    await store.nodes.create_index("id", unique=True)
    await store.nodes.create_index("trashed_at",
                                   partialFilterExpression={"trashed_at": {"$gt": 0}})
    return store


# --------------------------------------------------------- null and missing


async def test_a_query_for_null_matches_a_missing_field(store):
    """MongoDB's rule, and what the partial index over live nodes relies on.

    `json_extract` returns SQL NULL both for a key that is absent and for one
    explicitly set to null, so `IS NULL` covers the same set MongoDB's
    equality-against-null does. That is an alignment, not a decision, which is
    exactly why it is pinned: a rewrite that reached for `json_type(...) =
    'null'` instead would still pass every other test in this suite while
    making every live node invisible to the trash queries.
    """
    await store.nodes.insert_one({"id": "absent"})
    await store.nodes.insert_one({"id": "explicit", "trashed_at": None})
    await store.nodes.insert_one({"id": "set", "trashed_at": 5})

    found = await store.nodes.find({"trashed_at": None}).to_list(None)
    assert sorted(d["id"] for d in found) == ["absent", "explicit"]


async def test_a_range_bound_does_not_match_a_missing_field(store):
    """The one whose cost is destroyed data.

    `purge_expired` asks for `{"$gt": 0, "$lte": cutoff}` and hands what comes
    back to `purge()`. A backend that let a live node through that filter
    would be deleting files nobody deleted, so this is checked directly rather
    than trusted to SQL's three-valued logic continuing to behave.
    """
    await store.nodes.insert_one({"id": "live"})
    await store.nodes.insert_one({"id": "null", "trashed_at": None})
    await store.nodes.insert_one({"id": "due", "trashed_at": 10})
    await store.nodes.insert_one({"id": "later", "trashed_at": 9999})

    due = await store.nodes.find(
        {"trashed_at": {"$gt": 0, "$lte": 100}}).to_list(None)
    assert [d["id"] for d in due] == ["due"]


# ------------------------------------------------------------ type brackets


async def test_a_range_bound_does_not_compare_across_types(store):
    """The one that does *not* align, and needs the explicit guard.

    SQLite orders NULL < numbers < text < blobs and compares happily across
    them, so `'abc' > 0` is true there and false in MongoDB. Without the
    `json_type` guard in `_operator_clause` a node whose `trashed_at` had been
    tampered into a string would be swept.
    """
    await store.nodes.insert_one({"id": "text", "trashed_at": "abc"})
    await store.nodes.insert_one({"id": "number", "trashed_at": 5})

    found = await store.nodes.find({"trashed_at": {"$gt": 0}}).to_list(None)
    assert [d["id"] for d in found] == ["number"]


async def test_a_boolean_does_not_match_the_number_one(store):
    """`json_extract` turns JSON `true` into 1, and MongoDB keeps them apart."""
    await store.nodes.insert_one({"id": "bool", "is_dir": True})
    await store.nodes.insert_one({"id": "number", "is_dir": 1})

    found = await store.nodes.find({"is_dir": True}).to_list(None)
    assert [d["id"] for d in found] == ["bool"]


async def test_ne_matches_a_missing_field(store):
    """SQL would answer NULL for `col <> x` on a missing field, and `WHERE`
    would drop it. MongoDB matches it, so the null case is written out."""
    await store.nodes.insert_one({"id": "absent"})
    await store.nodes.insert_one({"id": "same", "state": "x"})
    await store.nodes.insert_one({"id": "other", "state": "y"})

    found = await store.nodes.find({"state": {"$ne": "x"}}).to_list(None)
    assert sorted(d["id"] for d in found) == ["absent", "other"]


# --------------------------------------------------------------- uniqueness


async def test_two_live_siblings_cannot_share_a_name(indexed):
    await indexed.nodes.insert_one(
        {"id": "a", "parent_id": "root", "filename": "notes.txt"})

    with pytest.raises(DuplicateKey):
        await indexed.nodes.insert_one(
            {"id": "b", "parent_id": "root", "filename": "notes.txt"})


async def test_a_trashed_node_frees_its_name(indexed):
    """Why the index is partial. Deleting `notes.txt` and writing a new one is
    the most ordinary operation there is, and a plain unique index would
    refuse it -- while a trashed node has to keep its name and its place, or
    restoring it could not work and the parent's entry tag would not match."""
    await indexed.nodes.insert_one(
        {"id": "a", "parent_id": "root", "filename": "notes.txt"})
    await indexed.nodes.update_one({"id": "a"}, {"$set": {"trashed_at": 1}})

    await indexed.nodes.insert_one(
        {"id": "b", "parent_id": "root", "filename": "notes.txt"})

    # And two trashed nodes may share one too.
    await indexed.nodes.update_one({"id": "b"}, {"$set": {"trashed_at": 2}})
    await indexed.nodes.insert_one(
        {"id": "c", "parent_id": "root", "filename": "notes.txt"})

    assert len(await indexed.nodes.find({"filename": "notes.txt"}).to_list(None)) == 3


async def test_restoring_onto_a_taken_name_is_refused(indexed):
    await indexed.nodes.insert_one({"id": "a", "parent_id": "root",
                                    "filename": "notes.txt", "trashed_at": 1})
    await indexed.nodes.insert_one({"id": "b", "parent_id": "root",
                                    "filename": "notes.txt"})

    with pytest.raises(DuplicateKey):
        await indexed.nodes.update_one({"id": "a"}, {"$unset": {"trashed_at": ""}})


# ------------------------------------------------------------- refusing to guess


@pytest.mark.parametrize("query", [
    {"id": {"$in": ["a"]}},
    {"id": {"$exists": True}},
    {"id": {"$regex": "a"}},
])
async def test_an_unmodelled_operator_raises(store, query):
    """Rather than matching nothing.

    Almost every caller in this project reads an empty result as "there is
    nothing there", so an unknown operator that quietly matched nothing would
    surface as a passing test for a feature that does not work. Same stance as
    `tests/fakes.py`.
    """
    with pytest.raises(UnsupportedQuery):
        store.nodes.find(query)


async def test_an_unmodelled_update_operator_raises(store):
    await store.nodes.insert_one({"id": "a", "n": 1})

    with pytest.raises(UnsupportedQuery):
        await store.nodes.update_one({"id": "a"}, {"$inc": {"n": 1}})


async def test_a_range_bound_against_a_string_raises(store):
    """Not silently compared. The guard above brackets a *stored* value by
    type; a bound that is itself a string is a caller doing something this
    backend has not been shown to get right."""
    with pytest.raises(UnsupportedQuery):
        store.nodes.find({"filename": {"$gt": "m"}})


async def test_a_document_that_will_not_survive_json_raises(store):
    """Every field this project stores is hex-encoded text, deliberately.
    Bytes reaching here would mean that stopped being true, and a backend that
    coerced them would corrupt a key record into one no password opens."""
    with pytest.raises(UnsupportedQuery):
        await store.nodes.insert_one({"id": "a", "raw": b"\x00\x01"})


# ------------------------------------------------------------------- indexes


async def test_an_index_whose_definition_changed_is_rebuilt(store):
    """`CREATE INDEX IF NOT EXISTS` would keep the old one and report success.

    That is the same silent divergence `_unique_index` handles for MongoDB,
    reached from the other side: an index whose shape changed between releases
    would go on serving the previous definition for ever, and nothing would
    say so.
    """
    await store.nodes.create_index("trashed_at")
    assert (await store.nodes.index_information())["trashed_at_1"]["partial"] is False

    await store.nodes.create_index("trashed_at",
                                   partialFilterExpression={"trashed_at": {"$gt": 0}})
    assert (await store.nodes.index_information())["trashed_at_1"]["partial"] is True


async def test_the_same_index_name_on_two_collections_does_not_collide(store):
    """Index names are per-collection in MongoDB and per-database in SQLite.

    `nodes`, `keystore` and `users` all ask for an index called `id_1`.
    Unqualified, the second request found the name taken, concluded the
    definition had changed, and replaced it -- so startup finished with one
    `id_1` on whichever collection asked last and none on the other two.
    Nothing raised and nothing was logged; the uniqueness that keeps one node
    id from being reachable as two was just absent.
    """
    await store.nodes.create_index("id", unique=True)
    await store.keystore.create_index("id", unique=True)
    await store.users.create_index("id", unique=True)

    for collection in (store.nodes, store.keystore, store.users):
        assert "id_1" in await collection.index_information()

    # And each one actually enforces, rather than merely being listed.
    for collection in (store.nodes, store.keystore, store.users):
        await collection.insert_one({"id": "same"})
        with pytest.raises(DuplicateKey):
            await collection.insert_one({"id": "same"})


async def test_dropping_an_index_only_touches_this_collection(store):
    await store.nodes.create_index("id", unique=True)
    await store.users.create_index("id", unique=True)

    await store.nodes.drop_index("id_1")

    assert "id_1" not in await store.nodes.index_information()
    assert "id_1" in await store.users.index_information()


async def test_reopening_an_existing_database_finds_its_generated_columns(tmp_path):
    """Generated columns do not appear in `PRAGMA table_info`.

    Reading the column list with that instead of `table_xinfo` made a reopened
    database look like it had none, and the first `create_index` then tried to
    add a column that was already there. Every start after the first would
    have failed.
    """
    path = str(tmp_path / "reopen.sqlite3")

    first = SqliteDB(path)
    await first.nodes.create_index("id", unique=True)
    await first.nodes.insert_one({"id": "a"})
    first.close()

    second = SqliteDB(path)
    try:
        await second.nodes.create_index("id", unique=True)
        assert (await second.nodes.find_one({"id": "a"}))["id"] == "a"
    finally:
        second.close()


async def test_the_indexes_are_actually_used(indexed):
    """The SQLite half of the IXSCAN check `ROADMAP.md` records for MongoDB.

    A partial index only serves a query the planner can prove is a subset of
    the index's own filter, so the query and the index have to agree. When
    they drift the result stays correct and the scan comes back -- which is
    the state the index was added to leave, and it is invisible without this.
    """
    plans = {}
    for label, flt in [
        ("id", {"id": "x"}),
        ("live name", {"parent_id": "p", "filename": "f", "trashed_at": None}),
        ("sweep", {"trashed_at": {"$gt": 0, "$lte": 100}}),
    ]:
        from src.sqlitedb import _Sql, _where

        collection = indexed.nodes
        where, params = _where(_Sql("nodes", collection._columns), flt)
        rows = collection._conn.execute(
            f"EXPLAIN QUERY PLAN SELECT doc FROM nodes WHERE {where}",
            params).fetchall()
        plans[label] = " ".join(r[3] for r in rows)

    # The SQL names carry the table prefix; see `_sql_index_name`.
    assert "USING INDEX nodes_id_1" in plans["id"]
    assert "USING INDEX nodes_parent_id_1_filename_1" in plans["live name"]
    assert "USING INDEX nodes_trashed_at_1" in plans["sweep"]


# --------------------------------------------------------------- update semantics


async def test_update_one_matching_nothing_is_not_an_error(store):
    """Unlike the fake, which raises to catch a bug.

    This stands in for a real database, and a real `update_one` that matches
    nothing reports that in its result. A backend that raised where production
    does not would turn a harmless no-op into a crash only the standalone
    build ever sees.
    """
    result = await store.nodes.update_one({"id": "nobody"}, {"$set": {"x": 1}})
    assert result.matched_count == 0


async def test_replace_one_keeps_the_stored_id(store):
    await store.nodes.insert_one({"id": "a", "v": 1})
    stored = await store.nodes.find_one({"id": "a"})

    await store.nodes.replace_one({"id": "a"}, {"id": "a", "v": 2})

    assert (await store.nodes.find_one({"id": "a"}))["_id"] == stored["_id"]


async def test_nested_values_survive_the_round_trip(store):
    """A file's chunk list is the reason documents are stored whole."""
    chunks = [{"index": 0, "offset": 0, "message_id": "m", "hmac": "ab"},
              {"index": 1, "offset": 9, "message_id": "n", "hmac": "cd"}]
    await store.nodes.insert_one({"id": "a", "chunks": chunks, "size": 18})

    assert (await store.nodes.find_one({"id": "a"}))["chunks"] == chunks
