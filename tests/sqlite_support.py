"""Enough of `FakeCollection`'s inspection surface to run the suite on SQLite.

`pytest --db=sqlite` swaps the in-memory fake for the real SQLite backend and
runs the entire suite against it. That is the strongest check available on
`src/sqlitedb.py`: instead of testing the adapter against my idea of how a
collection behaves, it tests it against every path the drive actually takes --
multi-chunk uploads, overwrites, the trash, tag verification, concurrent
writes -- with the assertions that were written for MongoDB's semantics.

To do that the fixtures need the four hooks `tests/fakes.py` grew, and they
live here rather than in `src/` so that nothing shipped to users carries a test
hook:

  * `docs`         -- direct access to stored documents, which is how the
                      integrity tests forge a tampered database
  * `find_one_calls` -- traffic counting, for the cross-handle sync tests
  * `find_filters`   -- the filters `find` was given, so a test can prove
                      narrowing happened in the query and not in Python after
  * `fail_deletes`   -- an injected database outage mid-unwind

`docs` writes through. A test that reaches into it is simulating an attacker
with database access, so the write has to reach the same place a real attacker
would reach; a snapshot that quietly discarded the tampering would leave every
integrity test passing for the wrong reason.
"""

import copy
import json

from src.sqlitedb import SqliteCollection, SqliteDB
from tests.fakes import DatabaseFailure


def _wrap(value, root):
    if isinstance(value, dict):
        return _ProxyDict(value, root)
    if isinstance(value, list):
        return _ProxyList(value, root)
    return value


class _ProxyDict(dict):
    """A dict inside a stored document, writing the whole document back.

    The nesting is the part that matters. The fake keeps documents as live
    Python objects, so `node["chunks"][0].pop("hmac")` reaches the stored
    document through two containers without anything special happening. Here
    the document is text in a column, and a proxy that only watched the top
    level would let that tampering change nothing -- leaving
    `test_stripping_the_tag_fails_the_read` and two others passing because the
    attack silently did not happen.
    """

    def __init__(self, data, root):
        super().__init__()
        self._root = self if root is None else root
        for key, value in data.items():
            dict.__setitem__(self, key, _wrap(value, self._root))

    def __setitem__(self, key, value):
        dict.__setitem__(self, key, _wrap(value, self._root))
        self._root._flush()

    def __delitem__(self, key):
        dict.__delitem__(self, key)
        self._root._flush()

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            dict.__setitem__(self, key, _wrap(value, self._root))
        self._root._flush()

    def pop(self, *args):
        value = dict.pop(self, *args)
        self._root._flush()
        return value

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def clear(self):
        dict.clear(self)
        self._root._flush()


class _ProxyList(list):
    """A list inside a stored document. Same rule as `_ProxyDict`."""

    def __init__(self, data, root):
        super().__init__(_wrap(item, root) for item in data)
        self._root = root

    def __setitem__(self, index, value):
        list.__setitem__(self, index, _wrap(value, self._root))
        self._root._flush()

    def __delitem__(self, index):
        list.__delitem__(self, index)
        self._root._flush()

    def append(self, value):
        list.append(self, _wrap(value, self._root))
        self._root._flush()

    def extend(self, values):
        for value in values:
            list.append(self, _wrap(value, self._root))
        self._root._flush()

    def insert(self, index, value):
        list.insert(self, index, _wrap(value, self._root))
        self._root._flush()

    def pop(self, *args):
        value = list.pop(self, *args)
        self._root._flush()
        return value

    def remove(self, value):
        list.remove(self, value)
        self._root._flush()

    def clear(self):
        list.clear(self)
        self._root._flush()

    def sort(self, **kwargs):
        list.sort(self, **kwargs)
        self._root._flush()

    def reverse(self):
        list.reverse(self)
        self._root._flush()


class _DocProxy(_ProxyDict):
    """A stored document that writes itself back when anything in it changes.

    `doc["tag_version"] = 1`, `del doc["mac"]` and
    `doc["chunks"][0].pop("hmac")` are all how the tampering tests are
    written, and against plain containers they would change a copy and prove
    nothing.
    """

    def __init__(self, collection, _id, data):
        self._collection = collection
        self._id = _id
        super().__init__(data, None)

    def _flush(self):
        self._collection.write_back(self._id, self)


class _DocsList(list):
    """A snapshot of a table that turns list mutations into SQL.

    Only the operations the suite actually performs are overridden. Anything
    else -- `insert`, `sort`, slice assignment -- would silently change the
    snapshot and not the table, so they raise rather than lie.
    """

    def __init__(self, collection, items):
        super().__init__(items)
        self._collection = collection

    def remove(self, doc):
        self._collection.delete_by_id(doc["_id"])
        super().remove(doc)

    def clear(self):
        self._collection.delete_all()
        super().clear()

    def append(self, doc):
        self._collection.insert_raw(doc)
        super().append(doc)

    def _unsupported(self, *args, **kwargs):
        raise NotImplementedError(
            "this operation would change the snapshot and not the database; "
            "add it to tests/sqlite_support.py rather than letting a test "
            "pass against a mutation that never landed")

    insert = extend = pop = sort = reverse = _unsupported
    __setitem__ = __delitem__ = __iadd__ = _unsupported


class SqliteTestCollection(SqliteCollection):
    def __init__(self, name, conn):
        super().__init__(name, conn)
        self.find_one_calls = 0
        self.find_filters = []
        self.fail_deletes = False

    # ----------------------------------------------------- counted overrides

    async def find_one(self, flt):
        self.find_one_calls += 1
        return await super().find_one(flt)

    def find(self, flt):
        self.find_filters.append(copy.deepcopy(flt))
        return super().find(flt)

    async def delete_one(self, flt):
        # A delete that raises is what a database going away mid-unwind looks
        # like, and it is the one way a rollback can leave a node behind that
        # points at attachments it has already deleted.
        if self.fail_deletes:
            raise DatabaseFailure(f"injected delete failure on {self.name}")
        return await super().delete_one(flt)

    # -------------------------------------------------------- raw doc access

    @property
    def docs(self):
        rows = self._conn.execute(
            f'SELECT _id, doc FROM "{self.name}" ORDER BY rowid').fetchall()
        return _DocsList(self, [_DocProxy(self, _id, json.loads(doc))
                                for _id, doc in rows])

    @docs.setter
    def docs(self, replacement):
        self.delete_all()
        for doc in replacement:
            self.insert_raw(doc)

    def write_back(self, _id, doc):
        self._replace_row(_id, doc)

    def delete_by_id(self, _id):
        self._conn.execute(f'DELETE FROM "{self.name}" WHERE _id = ?', [_id])

    def delete_all(self):
        self._conn.execute(f'DELETE FROM "{self.name}"')

    def insert_raw(self, doc):
        """Store `doc` without going through `insert_one`.

        Bypassing the collection API is the point: these callers are standing
        in for something writing to the database directly, which is exactly
        the threat model the integrity tags exist for.
        """
        from src.sqlitedb import _dumps, _new_id

        doc.setdefault("_id", _new_id())
        self._conn.execute(
            f'INSERT INTO "{self.name}" (_id, doc) VALUES (?, ?)',
            [doc["_id"], _dumps(doc)])


class SqliteTestDB(SqliteDB):
    collection_class = SqliteTestCollection
