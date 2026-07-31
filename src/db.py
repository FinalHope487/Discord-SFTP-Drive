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
        await cls._unique_index(cls.db.nodes, [("parent_id", 1), ("filename", 1)])
        await cls._unique_index(cls.db.nodes, "id")
        await cls._unique_index(cls.db.keystore, "id")

    @classmethod
    async def _unique_index(cls, collection, keys):
        """Create a unique index, replacing a non-unique one of the same shape.

        MongoDB refuses to change an existing index in place: asking for
        `unique=True` where a plain index of the same shape already exists
        fails with IndexKeySpecsConflict rather than upgrading it. Any
        deployment predating the constraint therefore needs migrating, and
        doing it here rather than in a runbook is what stops the server from
        simply refusing to start after an upgrade.
        """
        try:
            await collection.create_index(keys, unique=True)
            return
        except OperationFailure as exc:
            if exc.code not in _INDEX_CONFLICT_CODES:
                raise

        # Asking for the index that already exists hands back its generated
        # name, which is what `drop_index` needs.
        name = await collection.create_index(keys)
        logger.info("Replacing non-unique index %s on %s with a unique one",
                    name, collection.name)
        await collection.drop_index(name)

        try:
            await collection.create_index(keys, unique=True)
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
