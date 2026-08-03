"""In-memory stand-ins for MongoDB and the Discord API.

These cover the call contracts the VFS depends on -- nothing more. They do not
model Discord rate limits, attachment URL expiry, or MongoDB concurrency, so a
green suite is not a substitute for one run against real credentials.
"""

import copy
import uuid


def _matches(doc, flt):
    return all(_matches_field(doc.get(k), v) for k, v in flt.items())


def _matches_field(actual, expected):
    """Equality, plus the query operators this project actually issues.

    Unmodelled operators raise rather than quietly matching nothing. A fake
    that treats `{"$gt": 5}` as a literal value to compare against would make
    every query using it return an empty result, and an empty result is what
    most callers here read as "there is nothing there" -- so the divergence
    would surface as a passing test for behaviour that does not work.
    """
    if isinstance(expected, dict) and expected and \
            all(k.startswith("$") for k in expected):
        for operator, operand in expected.items():
            if operator != "$ne":
                raise AssertionError(
                    f"the fake collection does not model {operator}; add it "
                    "rather than letting the query silently match nothing")
            # Mongo treats a missing field as null here, and so does `.get`.
            if actual == operand:
                return False
        return True
    return actual == expected


class FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        return [copy.deepcopy(d) for d in self._docs]


class FakeCollection:
    def __init__(self, name="fake"):
        self.docs = []
        self.indexes = []
        self.dropped_indexes = []
        self.name = name
        # Set to a list of exceptions to raise from successive unique-index
        # creations. Lets a test drive the upgrade path MongoDB takes when an
        # index of the same shape already exists without `unique`.
        self.create_index_errors = []
        # Lets a test assert on database traffic directly -- e.g. that a
        # handle's cross-handle sync check skips the round trip when nothing
        # actually changed underneath it.
        self.find_one_calls = 0

    @staticmethod
    def _index_name(keys):
        """MongoDB's own naming rule, because the code drops indexes by name."""
        pairs = [(keys, 1)] if isinstance(keys, str) else list(keys)
        return "_".join(f"{field}_{direction}" for field, direction in pairs)

    async def create_index(self, keys, **options):
        # Recorded rather than enforced. Uniqueness is MongoDB's job, and a
        # fake that pretended to implement it would prove nothing about the
        # real one -- but that we *asked* for it is our code's job, and a test
        # can check that here.
        if options.get("unique") and self.create_index_errors:
            raise self.create_index_errors.pop(0)
        self.indexes.append((keys, options))
        return self._index_name(keys)

    async def index_information(self):
        """What MongoDB reports about the indexes that exist.

        Shaped like the real return value -- names mapped to specs carrying a
        `key` list -- because that is how the upgrade path finds the index it
        has to drop.
        """
        info = {}
        for keys, options in self.indexes:
            pairs = [(keys, 1)] if isinstance(keys, str) else list(keys)
            info[self._index_name(keys)] = {"key": pairs, **options}
        return info

    async def drop_index(self, name):
        self.dropped_indexes.append(name)
        self.indexes = [(keys, options) for keys, options in self.indexes
                        if self._index_name(keys) != name]

    async def find_one(self, flt):
        self.find_one_calls += 1
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
                d.update(copy.deepcopy(update.get("$set") or {}))
                # $unset is modelled because a field that lingers here but
                # not in MongoDB is a divergence that hides bugs rather than
                # causing them: the staged directory tag is meant to be
                # removed once promoted, and a test against a fake that kept
                # it would pass while the real thing behaved differently.
                for field in update.get("$unset") or {}:
                    d.pop(field, None)
                return
        raise AssertionError(f"update_one matched nothing: {flt}")

    async def replace_one(self, flt, doc, upsert=False):
        for i, d in enumerate(self.docs):
            if _matches(d, flt):
                self.docs[i] = copy.deepcopy(doc)
                return
        if upsert:
            await self.insert_one(dict(doc))
            return
        raise AssertionError(f"replace_one matched nothing: {flt}")

    async def delete_one(self, flt):
        for i, d in enumerate(self.docs):
            if _matches(d, flt):
                del self.docs[i]
                return

    def find(self, flt):
        return FakeCursor([d for d in self.docs if _matches(d, flt)])


class FakeDB:
    def __init__(self):
        self.nodes = FakeCollection("nodes")
        self.keystore = FakeCollection("keystore")
        self.users = FakeCollection("users")

    async def command(self, *a, **k):
        return {"ok": 1}


class DiscordFailure(RuntimeError):
    """What an injected Discord outage raises.

    Its own type so a test can assert that the failure it arranged is the one
    that surfaced, rather than quietly passing on an unrelated exception.
    """


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
        # Attempt number, counted from 1, at which uploads start raising and
        # keep raising. `fail_uploads_from = 3` is a Discord outage that
        # begins part way through a multi-chunk write, which is the shape of
        # the failure that matters: the bytes already up there are committed
        # and the ones still coming cannot land.
        #
        # One raise here stands for a whole exhausted retry budget. The real
        # client retries 5xx and transport errors internally and only raises
        # once it has given up, so this is what the VFS actually sees.
        #
        # `uploads` counts attempts, failed ones included, so it and this
        # threshold are on the same scale.
        self.fail_uploads_from = None

    async def upload_chunk(self, data, filename):
        self.uploads += 1
        if self.fail_uploads_from is not None and self.uploads >= self.fail_uploads_from:
            raise DiscordFailure(
                f"injected upload failure on attempt {self.uploads} ({filename})")
        mid = str(uuid.uuid4())
        self.store[mid] = bytes(data)
        return mid, f"https://cdn.test/{mid}", len(data)

    async def get_attachment_url(self, message_id, *, refresh=False):
        if message_id not in self.store:
            raise RuntimeError("no such message")
        return f"https://cdn.test/{message_id}"

    async def download_chunk(self, url):
        return self.store[url.rsplit("/", 1)[1]]

    async def download_attachment(self, message_id):
        return await self.download_chunk(await self.get_attachment_url(message_id))

    async def delete_message(self, message_id):
        self.deleted.append(message_id)
        self.store.pop(message_id, None)

    async def close(self):
        pass
