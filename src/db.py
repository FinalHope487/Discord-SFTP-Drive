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
        # error on the most ordinary operation there is. `$exists` rather than
        # an equality on null, because a partial index cannot be built on
        # "field is missing" any other way, which is also why a live node
        # never carries the field.
        await cls._unique_index(cls.db.nodes, [("parent_id", 1), ("filename", 1)],
                                partial={"trashed_at": {"$exists": False}})
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
