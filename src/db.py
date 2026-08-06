import logging

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure

from src.config import MONGO_DB_NAME, MONGO_URI

logger = logging.getLogger(__name__)

# MongoDB's way of saying "an index with this shape already exists and does not
# match what you asked for". It will not change one in place.
_INDEX_CONFLICT_CODES = (85, 86)   # IndexOptionsConflict, IndexKeySpecsConflict
_DUPLICATE_KEY = 11000


class Database:
    """Motor client bound to the process-wide event loop.

    `connect()` is a coroutine so the client is created and first used on the
    same loop — Motor attaches its futures to whichever loop is running, and
    mixing loops produces "attached to a different loop" errors at query time.
    """

    client: AsyncIOMotorClient = None
    db = None

    @classmethod
    async def connect(cls):
        cls.client = AsyncIOMotorClient(MONGO_URI)
        cls.db = cls.client[MONGO_DB_NAME]
        # Surface an unreachable MongoDB at startup rather than on first upload.
        await cls.db.command("ping")
        await cls._ensure_indexes()

    @classmethod
    async def _ensure_indexes(cls):
        # Path resolution walks one segment at a time; without these every
        # segment is a collection scan.
        #
        # Unique, not merely indexed: two nodes sharing a name under the same
        # parent make lookups depend on insertion order, and the loser becomes
        # a file that exists on Discord but can never be addressed again.
        #
        # Partial, over live nodes only. A trashed node keeps its name and its
        # place -- that is what makes restoring it free and what keeps its
        # membership in the parent's entry tag intact -- so deleting
        # `notes.txt` and writing a new one would otherwise be a duplicate key
        # error on the most ordinary operation there is.
        #
        # The filter is an equality on null, and it has to be. A
        # partialFilterExpression accepts only a small grammar -- equality,
        # `$exists: true`, the range operators, `$type`, `$and`/`$or`/`$in` --
        # and `$exists: false` is not in it: MongoDB rejects the whole index
        # with "Expression not supported in partial index: $not". This was
        # written as `$exists: false` and never ran, because the fake database
        # the suite uses does not validate index specifications and the
        # deployment predates the trash. The first real start after it refused
        # to boot at all.
        #
        # `{"trashed_at": None}` is the same set of documents by MongoDB's own
        # matching rules: an equality against null matches a null *and* a
        # missing field. So a live node still carries no field, `$unset` is
        # still what a restore does, and the index still covers exactly the
        # live nodes -- verified against 6.0 for all four cases that matter:
        # two live siblings collide, a new file may take a trashed one's name,
        # two trashed nodes may share a name, and restoring onto a taken name
        # is still refused.
        await cls._unique_index(cls.db.nodes, [("parent_id", 1), ("filename", 1)],
                                partial={"trashed_at": None})
        await cls._unique_index(cls.db.nodes, "id")
        await cls._unique_index(cls.db.keystore, "id")

        # Accounts. `username` is what a login resolves, and two rows sharing
        # one would make which account a password opens depend on insertion
        # order -- with a master key each, that is not an ambiguity, it is one
        # user reaching another's tree.
        #
        # The (parent_id, filename) index above needs nothing added for
        # per-account trees: each root is its own parent_id, so the same
        # filename under two of them is already two distinct keys.
        await cls._unique_index(cls.db.users, "username")
        await cls._unique_index(cls.db.users, "id")

        # The trash sweep. `purge_expired` runs every 15 minutes for each live
        # session, and without this it read every node in every tree to find
        # the handful that were due.
        #
        # Partial on `{"$gt": 0}` rather than plain, so the index holds only
        # the trashed nodes -- a live node carries no `trashed_at` at all, and
        # indexing the whole collection to find the small part of it that is
        # deleted is the cost this is meant to avoid. `$gt` is inside the
        # partial-filter grammar; `$ne` is not, which is the other reason the
        # queries had to change.
        #
        # Both queries must carry `{"$gt": 0}` themselves or the planner will
        # not use this index -- it can only use a partial index for a query it
        # can prove is a subset of the filter. Measured against MongoDB 6.0 on
        # four documents: `{"$ne": None}` is a COLLSCAN examining all four even
        # with this index present, while `{"$gt": 0, "$lte": cutoff}` is an
        # IXSCAN examining exactly the one that matched.
        await cls._plain_index(cls.db.nodes, "trashed_at",
                               partial={"trashed_at": {"$gt": 0}})

    @classmethod
    async def _index_named_by_key(cls, collection, keys):
        """The name of the existing index with this key spec, if there is one."""
        wanted = [(keys, 1)] if isinstance(keys, str) else [tuple(k) for k in keys]
        info = await collection.index_information()
        for name, spec in info.items():
            if [tuple(k) for k in spec.get("key") or []] == wanted:
                return name
        return None

    @classmethod
    async def _unique_index(cls, collection, keys, partial=None):
        """Create a unique index, replacing one of the same shape but other options.

        MongoDB refuses to change an existing index in place: asking for
        `unique=True`, or for a different partialFilterExpression, where an
        index of the same shape already exists fails with a conflict rather
        than upgrading it. Any deployment predating either constraint
        therefore needs migrating, and doing it here rather than in a runbook
        is what stops the server from simply refusing to start after an
        upgrade.

        The conflicting index is found by its key spec. An earlier version
        learned the name by asking MongoDB to create the plain index and
        reading back what it returned, which worked only while uniqueness was
        the sole difference -- once the *options* differ in any other way that
        second request conflicts too, and the recovery raised instead of
        recovering.
        """
        options = {"unique": True}
        if partial is not None:
            options["partialFilterExpression"] = partial

        try:
            await collection.create_index(keys, **options)
            return
        except OperationFailure as exc:
            if exc.code not in _INDEX_CONFLICT_CODES:
                raise
            conflict = exc

        name = await cls._index_named_by_key(collection, keys)
        if name is None:
            raise conflict

        logger.info("Replacing index %s on %s; its options no longer match "
                    "what this version requires", name, collection.name)
        await collection.drop_index(name)

        try:
            await collection.create_index(keys, **options)
        except OperationFailure as exc:
            # Put the old index back rather than leaving the collection
            # unindexed, then say plainly what has to be resolved by hand:
            # duplicates already in the data cannot be resolved from here,
            # because which of them to keep is not ours to decide.
            await collection.create_index(keys)
            if exc.code == _DUPLICATE_KEY:
                raise OperationFailure(
                    f"cannot make {keys} unique on {collection.name}: the "
                    "collection already contains duplicates. Remove them, "
                    "then restart. The previous non-unique index has been "
                    "restored in the meantime."
                ) from exc
            raise

    @classmethod
    async def _plain_index(cls, collection, keys, partial=None):
        """Create a non-unique index, replacing one of the same shape but other options.

        Same rule as `_unique_index`: MongoDB will not change an index in
        place, so a deployment carrying an earlier version of this index --
        plain where this one is partial, say -- would make the server refuse to
        start rather than upgrade.

        There is no restore path here, unlike the unique case. A plain index
        cannot be rejected by the data it is being built over: there are no
        duplicates for it to refuse, so the second create only fails for
        reasons the first one would have failed for too.
        """
        options = {}
        if partial is not None:
            options["partialFilterExpression"] = partial

        try:
            await collection.create_index(keys, **options)
            return
        except OperationFailure as exc:
            if exc.code not in _INDEX_CONFLICT_CODES:
                raise
            conflict = exc

        name = await cls._index_named_by_key(collection, keys)
        if name is None:
            raise conflict

        logger.info("Replacing index %s on %s; its options no longer match "
                    "what this version requires", name, collection.name)
        await collection.drop_index(name)
        await collection.create_index(keys, **options)

    @classmethod
    def get_db(cls):
        if cls.db is None:
            raise RuntimeError("Database.connect() has not been awaited yet")
        return cls.db

    @classmethod
    async def close(cls):
        if cls.client is not None:
            cls.client.close()
            cls.client = None
            cls.db = None


db = Database()
