"""In-memory stand-ins for MongoDB and the Discord API.

These cover the call contracts the VFS depends on -- nothing more. They do not
model Discord rate limits, attachment URL expiry, or MongoDB concurrency, so a
green suite is not a substitute for one run against real credentials.
"""

import copy
import uuid


def _matches(doc, flt):
    return all(doc.get(k) == v for k, v in flt.items())


class FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        return [copy.deepcopy(d) for d in self._docs]


class FakeCollection:
    def __init__(self):
        self.docs = []

    async def create_index(self, *a, **k):
        return "idx"

    async def find_one(self, flt):
        for d in self.docs:
            if _matches(d, flt):
                return copy.deepcopy(d)
        return None

    async def insert_one(self, doc):
        doc.setdefault("_id", str(uuid.uuid4()))
        self.docs.append(copy.deepcopy(doc))
        return type("R", (), {"inserted_id": doc["_id"]})()

    async def update_one(self, flt, update):
        for d in self.docs:
            if _matches(d, flt):
                d.update(copy.deepcopy(update["$set"]))
                return
        raise AssertionError(f"update_one matched nothing: {flt}")

    async def delete_one(self, flt):
        for i, d in enumerate(self.docs):
            if _matches(d, flt):
                del self.docs[i]
                return

    def find(self, flt):
        return FakeCursor([d for d in self.docs if _matches(d, flt)])


class FakeDB:
    def __init__(self):
        self.nodes = FakeCollection()

    async def command(self, *a, **k):
        return {"ok": 1}


class FakeDiscord:
    """Attachments live in `store`, keyed by message id.

    `store` doubles as the leak detector: anything left in it after a delete
    or an overwrite is an orphaned attachment that no longer has a metadata
    row pointing at it.
    """

    def __init__(self):
        self.store = {}
        self.uploads = 0
        self.deleted = []

    async def upload_chunk(self, data, filename):
        self.uploads += 1
        mid = str(uuid.uuid4())
        self.store[mid] = bytes(data)
        return mid, f"https://cdn.test/{mid}", len(data)

    async def get_attachment_url(self, message_id):
        if message_id not in self.store:
            raise RuntimeError("no such message")
        return f"https://cdn.test/{message_id}"

    async def download_chunk(self, url):
        return self.store[url.rsplit("/", 1)[1]]

    async def delete_message(self, message_id):
        self.deleted.append(message_id)
        self.store.pop(message_id, None)

    async def close(self):
        pass
