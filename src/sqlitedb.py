"""A SQLite store shaped like the Motor collections the rest of this project uses.

Nothing above this module knows which database it is talking to. `vfs.py`,
`keystore.py` and `users.py` issue `find_one` / `find` / `insert_one` /
`update_one` / `replace_one` / `delete_one` against three collections, using
equality, `$gt`, `$gte`, `$lt`, `$lte`, `$ne`, `$set` and `$unset` -- and
nothing else. That is a small enough contract to implement twice, which is why
the standalone build can drop MongoDB without touching the 2300-line file that
holds the filesystem.

Documents are stored whole, as JSON, in a `doc` column. The fields that are
indexed are pulled out into *generated* columns rather than written alongside
by hand: a column that disagreed with `doc` would make a node vanish from
queries while looking perfectly intact when read back, and generated columns
make that state unreachable rather than merely unlikely.

Three places where SQL and MongoDB do not agree, all of which would be silent:

  * A missing field and an explicit null both come out of `json_extract` as
    SQL NULL, which is what MongoDB means by `{"field": None}`. These agree by
    luck, not design, so `tests/test_sqlite_backend.py` pins it.

  * `{"$lte": n}` must not match a missing field. It does not here, because
    `NULL <= n` is NULL and `WHERE` drops it -- again agreeing by luck. The
    cost of getting it wrong is `purge_expired` handing live nodes to
    `purge()`, so it is pinned too.

  * `{"$gt": 0}` against a *string* is false in MongoDB and true in SQLite,
    whose comparisons run across storage classes. This one does not agree by
    luck and every range comparison below carries an explicit `json_type`
    guard because of it.

Operators that are not implemented raise. The reason is the one `tests/fakes.py`
gives: almost every caller here reads an empty result as "there is nothing
there", so an unknown operator that quietly matched nothing would surface as a
passing test for a feature that does not work.
"""

import asyncio
import json
import logging
import re
import sqlite3

logger = logging.getLogger(__name__)

# Field names reaching SQL as identifiers or JSON paths. Anything outside this
# is refused rather than escaped: every field this project stores is a plain
# identifier, so a name that needs quoting means a caller built a field name
# from data, and that is worth a loud stop.
_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_COMPARISONS = {"$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<="}

# What `json_type` reports for a JSON number. Both, because 0 is 'integer' and
# 0.5 is 'real', and a bound that only matched one of them would drop half the
# documents it was asked about.
_NUMERIC = "('integer','real')"


class UnsupportedQuery(RuntimeError):
    """A query or update this backend does not model.

    Its own type so the message survives: raising is the whole point, and a
    caller that caught a generic error and carried on would reintroduce the
    silent-empty-result failure this class exists to prevent.
    """


def _field_path(field):
    if not _FIELD.match(field):
        raise UnsupportedQuery(
            f"{field!r} is not a plain field name; this backend does not "
            "build SQL from arbitrary strings")
    return f"'$.{field}'"


class _Sql:
    """Renders one field of a filter into SQL against a table's columns.

    Held together as a class only so the column set travels with the field
    name; a generated column is referenced directly (so the index on it can be
    used) and anything else falls back to reading the JSON.
    """

    def __init__(self, table, columns):
        self.table = table
        self.columns = columns

    def value(self, field):
        if field in self.columns:
            return f'"{field}"'
        return f"json_extract(doc, {_field_path(field)})"

    def type(self, field):
        return f"json_type(doc, {_field_path(field)})"


def _clause(sql, field, expected):
    """`(sql_fragment, params)` for one `field: expected` pair of a filter."""
    if isinstance(expected, dict) and expected and \
            all(k.startswith("$") for k in expected):
        return _operator_clause(sql, field, expected)

    column = sql.value(field)

    if expected is None:
        # Matches a stored null and a missing field alike, which is what
        # MongoDB means by an equality against null -- and what makes the
        # partial index over live nodes cover the right set.
        return f"{column} IS NULL", []

    if isinstance(expected, bool):
        # Compared as a JSON type rather than as 0/1. `json_extract` turns
        # JSON `true` into the integer 1, so a plain `= 1` would also match a
        # document that stored the number 1 -- which MongoDB would not.
        return f"{sql.type(field)} = ?", ["true" if expected else "false"]

    if isinstance(expected, (str, int, float)):
        return f"{column} = ?", [expected]

    raise UnsupportedQuery(
        f"cannot match {field!r} against {type(expected).__name__}; this "
        "backend models equality on null, booleans, numbers and strings only")


def _operator_clause(sql, field, expected):
    fragments, params = [], []
    column = sql.value(field)

    for operator, operand in expected.items():
        if operator == "$ne":
            if operand is None:
                fragments.append(f"{column} IS NOT NULL")
            else:
                # A missing field is not equal to a value, and SQL would
                # answer NULL -- which `WHERE` discards -- so the null case is
                # spelled out rather than left to three-valued logic.
                fragments.append(f"({column} IS NULL OR {column} <> ?)")
                params.append(operand)
        elif operator in _COMPARISONS:
            if isinstance(operand, bool) or not isinstance(operand, (int, float)):
                raise UnsupportedQuery(
                    f"{operator} on {field!r} is only modelled against "
                    "numbers; add the type bracketing for this type rather "
                    "than letting SQLite compare across storage classes")
            # The guard is the whole point. Without it SQLite would answer
            # `'abc' > 0` with true, because it orders NULL < numbers < text
            # < blobs, while MongoDB brackets comparisons by type and answers
            # false.
            fragments.append(
                f"({sql.type(field)} IN {_NUMERIC} AND "
                f"{column} {_COMPARISONS[operator]} ?)")
            params.append(operand)
        else:
            raise UnsupportedQuery(
                f"this backend does not model {operator}; add it rather than "
                "letting the query silently match nothing")

    return " AND ".join(fragments), params


def _where(sql, flt):
    """`(where_clause, params)` for a whole filter. An empty filter matches all."""
    if not flt:
        return "1", []
    fragments, params = [], []
    for field, expected in flt.items():
        fragment, values = _clause(sql, field, expected)
        fragments.append(fragment)
        params.extend(values)
    return " AND ".join(fragments), params


def _literal(value):
    """`value` as a SQL literal, for the one place a parameter cannot go.

    Only the types a filter can already carry, all of them checked on the way
    in by `_clause`: a number that reached here has been through
    `isinstance(..., (int, float))`, and a string is either a field's expected
    value or one of this module's own `json_type` names. Anything else stops.
    """
    if isinstance(value, bool) or value is None:
        raise UnsupportedQuery(f"cannot render {value!r} as a SQL literal")
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    raise UnsupportedQuery(
        f"cannot render {type(value).__name__} as a SQL literal")


def _inline(clause, params):
    """Fold bound parameters into `clause` as literals.

    DDL has nowhere to bind, and the partial indexes this project needs are
    expressed as filters. Splitting on `?` is sound here because every
    fragment `_clause` builds is made of validated identifiers, fixed operator
    text and placeholders -- there is no other `?` for this to find.
    """
    parts = clause.split("?")
    if len(parts) - 1 != len(params):
        raise UnsupportedQuery(
            f"cannot inline {len(params)} parameters into {clause!r}")
    rendered = parts[0]
    for value, tail in zip(params, parts[1:]):
        rendered += _literal(value) + tail
    return rendered


def _dumps(doc):
    """`doc` as JSON, refusing anything that would not survive the round trip.

    Every document this project stores is already JSON-shaped -- tags, salts,
    nonces and ciphertexts are all stored hex-encoded, deliberately. Bytes
    reaching here would mean that stopped being true, and silently base64ing
    them or letting `str()` win would corrupt a key record in a way that only
    shows up as a password that no longer opens the drive.
    """
    try:
        return json.dumps(doc, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise UnsupportedQuery(
            f"document is not JSON-serialisable ({exc}); this backend stores "
            "documents as JSON, so a new field has to be stored in a form "
            "that survives that") from exc


class _Result:
    """What Motor hands back. Only the fields anything here reads."""

    def __init__(self, matched=0, modified=0, inserted_id=None, deleted=0):
        self.matched_count = matched
        self.modified_count = modified
        self.inserted_id = inserted_id
        self.deleted_count = deleted


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, length=None):
        await asyncio.sleep(0)
        rows = self._rows if length is None else self._rows[:length]
        return [json.loads(row) for row in rows]


class SqliteCollection:
    """One table, behaving like a Motor collection.

    Every method opens with `await asyncio.sleep(0)`. SQLite is a local file
    and does not need to suspend, but the coroutines above this one were
    written against a database that is a network round trip, and
    `tests/fakes.py` records what happened the last time a stand-in did not
    yield: the concurrent-write bug that stopped a directory listing was
    unreachable, and 578 green tests said nothing about it. Keeping the
    suspension point keeps the suite meaning the same thing on both backends.
    """

    def __init__(self, name, conn):
        if not _FIELD.match(name):
            raise UnsupportedQuery(f"{name!r} is not a usable collection name")
        self.name = name
        self._conn = conn
        self._columns = set()
        self._conn.execute(
            f'CREATE TABLE IF NOT EXISTS "{name}" ('
            "  _id TEXT PRIMARY KEY,"
            "  doc TEXT NOT NULL)")
        self._load_columns()

    # ------------------------------------------------------------- internals

    def _load_columns(self):
        # `table_xinfo`, not `table_info`. The latter omits generated columns
        # entirely, so reopening an existing database would report none of
        # them and the first `create_index` would try to add one that is
        # already there -- which is how this was found.
        rows = self._conn.execute(f'PRAGMA table_xinfo("{self.name}")').fetchall()
        self._columns = {r[1] for r in rows} - {"_id", "doc"}

    def _ensure_column(self, field):
        """Add the generated column for `field`, if it is not there yet.

        Declared with no type, so the column keeps whatever storage class
        `json_extract` produced. Giving it TEXT affinity would make SQLite
        coerce a stored number on comparison, and a numeric bound would then
        match values MongoDB would not.

        VIRTUAL rather than STORED for two reasons: SQLite can only ALTER a
        table to add a virtual generated column, and the indexes below are
        materialised anyway, so a query that goes through one pays nothing for
        recomputing the expression.
        """
        if field in self._columns:
            return
        path = _field_path(field)
        self._conn.execute(
            f'ALTER TABLE "{self.name}" ADD COLUMN "{field}" '
            f"GENERATED ALWAYS AS (json_extract(doc, {path})) VIRTUAL")
        self._columns.add(field)

    def _sql(self):
        return _Sql(self.name, self._columns)

    def _sql_index_name(self, mongo_name):
        """MongoDB's index name, qualified by the table it belongs to.

        Index names are per-collection in MongoDB and *per-database* in
        SQLite. Three collections here ask for an index called `id_1` --
        `nodes`, `keystore` and `users` -- and without this the second request
        found the first one's name already taken, decided its definition had
        changed, and replaced it. Startup ended with a single `id_1` on
        whichever table asked last, and the other two collections silently
        had none: no error, no log line, and the uniqueness that stops one
        node id from being reachable as two was simply absent.

        The prefix stays inside this class. `index_information` takes it back
        off, so `db.py` goes on seeing the MongoDB names it looks indexes up
        by.
        """
        return f"{self.name}_{mongo_name}"

    def _fetch_one(self, flt):
        where, params = _where(self._sql(), flt)
        return self._conn.execute(
            f'SELECT _id, doc FROM "{self.name}" WHERE {where} '
            "ORDER BY rowid LIMIT 1", params).fetchone()

    # --------------------------------------------------------------- reading

    async def find_one(self, flt):
        await asyncio.sleep(0)
        row = self._fetch_one(flt)
        return None if row is None else json.loads(row[1])

    def find(self, flt):
        """Synchronous, like Motor's: it returns a cursor, it does not run yet.

        Ordered by insertion. MongoDB promises no order without a sort and the
        callers here do not ask for one, but an *unstable* order would make a
        test that happens to depend on it flap rather than fail, so this picks
        the same order the in-memory fake has.
        """
        where, params = _where(self._sql(), flt)
        rows = self._conn.execute(
            f'SELECT doc FROM "{self.name}" WHERE {where} ORDER BY rowid',
            params).fetchall()
        return _Cursor([r[0] for r in rows])

    # --------------------------------------------------------------- writing

    async def insert_one(self, doc):
        await asyncio.sleep(0)
        doc.setdefault("_id", _new_id())
        payload = _dumps(doc)
        try:
            self._conn.execute(
                f'INSERT INTO "{self.name}" (_id, doc) VALUES (?, ?)',
                [doc["_id"], payload])
        except sqlite3.IntegrityError as exc:
            raise DuplicateKey(f"{self.name}: {exc}") from exc
        return _Result(inserted_id=doc["_id"])

    async def update_one(self, flt, update):
        """`$set` and `$unset`, applied in Python.

        Read-modify-write rather than SQL `json_set`: the values being set
        include lists (a file's chunk list) and the JSON-surgery version of
        this would have to decide, per value, whether to bind it as a scalar
        or splice it as JSON. Doing it in Python is one code path and it is
        the same one the in-memory fake takes, which is what makes the two
        backends comparable.

        There is no `await` between the read and the write, so no other
        coroutine can interleave inside it.

        Matching nothing is not an error here, though it is in the fake. This
        stands in for a real database, and a real `update_one` that matches
        nothing reports that in its result rather than raising; a backend that
        raised where production does not would turn a harmless no-op into a
        crash that only the standalone build ever sees.
        """
        await asyncio.sleep(0)
        for operator in update:
            if operator not in ("$set", "$unset"):
                raise UnsupportedQuery(
                    f"this backend does not model the update operator "
                    f"{operator}; add it rather than dropping it silently")

        row = self._fetch_one(flt)
        if row is None:
            return _Result(matched=0, modified=0)

        _id, doc = row[0], json.loads(row[1])
        before = doc.copy()
        doc.update(update.get("$set") or {})
        for field in update.get("$unset") or {}:
            doc.pop(field, None)

        if doc == before:
            return _Result(matched=1, modified=0)

        self._replace_row(_id, doc)
        return _Result(matched=1, modified=1)

    async def replace_one(self, flt, doc, upsert=False):
        await asyncio.sleep(0)
        row = self._fetch_one(flt)
        if row is None:
            if not upsert:
                return _Result(matched=0, modified=0)
            return await self.insert_one(dict(doc))

        # The stored `_id` survives the replacement. MongoDB will not let a
        # replace change it, and the documents handed to this method are built
        # fresh and carry none of their own.
        self._replace_row(row[0], dict(doc, _id=row[0]))
        return _Result(matched=1, modified=1)

    async def delete_one(self, flt):
        await asyncio.sleep(0)
        row = self._fetch_one(flt)
        if row is None:
            return _Result(deleted=0)
        self._conn.execute(f'DELETE FROM "{self.name}" WHERE _id = ?', [row[0]])
        return _Result(deleted=1)

    def _replace_row(self, _id, doc):
        try:
            self._conn.execute(
                f'UPDATE "{self.name}" SET doc = ? WHERE _id = ?',
                [_dumps(doc), _id])
        except sqlite3.IntegrityError as exc:
            raise DuplicateKey(f"{self.name}: {exc}") from exc

    # --------------------------------------------------------------- indexes

    async def create_index(self, keys, **options):
        """Translate a MongoDB index request into SQL DDL.

        Taking the request rather than declaring SQLite's indexes separately is
        deliberate: `Database._ensure_indexes` stays the single place that says
        which indexes this project needs, and the two backends cannot drift
        into disagreeing about it. `test_db_indexes.py` already pins that one
        declaration, so pinning it once covers both.
        """
        pairs = _key_pairs(keys)
        name = self._sql_index_name(_index_name(pairs))

        for field, _ in pairs:
            self._ensure_column(field)

        partial = options.get("partialFilterExpression")
        where = ""
        if partial:
            for field in partial:
                self._ensure_column(field)
            clause, params = _where(self._sql(), partial)
            where = f" WHERE {_inline(clause, params)}"

        unique = "UNIQUE " if options.get("unique") else ""
        columns = ", ".join(f'"{field}"' for field, _ in pairs)
        ddl = (f'CREATE {unique}INDEX "{name}" ON "{self.name}" '
               f"({columns}){where}")

        existing = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            [name]).fetchone()

        if existing is not None:
            if existing[0] == ddl:
                return name
            # `CREATE INDEX IF NOT EXISTS` would leave the old definition in
            # place and report success, so an index whose shape changed
            # between releases would keep serving the previous one for ever.
            # That is the same silent-divergence failure `_unique_index`
            # handles for MongoDB, reached from the other side.
            logger.info("Replacing index %s on %s; its definition no longer "
                        "matches what this version requires", name, self.name)
            self._conn.execute(f'DROP INDEX "{name}"')

        try:
            self._conn.execute(ddl)
        except sqlite3.IntegrityError as exc:
            raise DuplicateKey(
                f"cannot build {name} on {self.name}: the table already "
                f"contains rows that violate it ({exc}). Which of the "
                "duplicates to keep is not something startup can decide."
            ) from exc
        return name

    async def index_information(self):
        """Shaped like MongoDB's, for the recovery path in `db.py`.

        Only the `key` list is reconstructed, because that is the only part
        anything reads -- `_index_named_by_key` matches on it to find the
        index it has to drop.
        """
        info = {}
        prefix = f"{self.name}_"
        for row in self._conn.execute(
                f'PRAGMA index_list("{self.name}")').fetchall():
            name, unique, partial = row[1], row[2], row[4]
            if name.startswith("sqlite_autoindex"):
                continue
            fields = [r[2] for r in self._conn.execute(
                f'PRAGMA index_info("{name}")').fetchall()]
            reported = name[len(prefix):] if name.startswith(prefix) else name
            info[reported] = {"key": [(field, 1) for field in fields],
                              "unique": bool(unique), "partial": bool(partial)}
        return info

    async def drop_index(self, name):
        """Takes the MongoDB name, the way `db.py` learned it from
        `index_information`; the table prefix is added back here."""
        if not _FIELD.match(name):
            raise UnsupportedQuery(f"{name!r} is not a usable index name")
        self._conn.execute(
            f'DROP INDEX IF EXISTS "{self._sql_index_name(name)}"')


class DuplicateKey(RuntimeError):
    """A unique index refused a write.

    Named rather than left as `sqlite3.IntegrityError` so callers above this
    module never have to import sqlite3 to recognise it -- and so the
    standalone build fails the same *shape* of failure MongoDB produces, which
    is a duplicate key error and not a generic database fault.
    """


def _new_id():
    import uuid
    return str(uuid.uuid4())


def _key_pairs(keys):
    """MongoDB's two index-key spellings, normalised to one."""
    if isinstance(keys, str):
        return [(keys, 1)]
    return [tuple(pair) for pair in keys]


def _index_name(pairs):
    """MongoDB's own naming rule, so both backends name an index the same."""
    return "_".join(f"{field}_{direction}" for field, direction in pairs)


class SqliteDB:
    """The database handle. Collections appear on first use, as in MongoDB."""

    # The class each collection is built from. A seam for the test suite,
    # which runs this whole backend under the fixtures written for the
    # in-memory fake and needs the handful of inspection hooks those fixtures
    # expect. Kept as one overridable name so none of those hooks have to
    # exist in the code that serves traffic.
    collection_class = SqliteCollection

    def __init__(self, path):
        self.path = path
        # `check_same_thread` is left on: everything here runs on the one
        # event loop that `db.py` documents, and turning it off would hide a
        # thread creeping in rather than making one safe.
        #
        # `isolation_level=None` puts the connection in autocommit, so there
        # are no `commit()` calls below and none are missing. Nothing here
        # spans more than one statement that needs to be atomic against
        # another writer, because there is no other writer -- one process, one
        # connection, and no `await` between a read and the write that
        # follows it.
        self._conn = sqlite3.connect(path, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode = WAL")
        # FULL, not NORMAL. A metadata write that is lost after its chunks
        # reached Discord is a file that exists nowhere the drive can find it,
        # while the cost is one fsync per filesystem operation -- next to
        # nothing beside the network round trip that preceded it.
        self._conn.execute("PRAGMA synchronous = FULL")
        self._collections = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        collection = self._collections.get(name)
        if collection is None:
            collection = self.collection_class(name, self._conn)
            self._collections[name] = collection
        return collection

    async def command(self, *args, **kwargs):
        """`ping`, and nothing else. Startup uses it to fail early on a
        database it cannot reach; here it proves the file is openable."""
        await asyncio.sleep(0)
        self._conn.execute("SELECT 1").fetchone()
        return {"ok": 1}

    def close(self):
        self._conn.close()
