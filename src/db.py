from motor.motor_asyncio import AsyncIOMotorClient

from src.config import MONGO_DB_NAME, MONGO_URI


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
        await cls.db.nodes.create_index([("parent_id", 1), ("filename", 1)],
                                        unique=True)
        await cls.db.nodes.create_index("id", unique=True)
        await cls.db.keystore.create_index("id", unique=True)

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
