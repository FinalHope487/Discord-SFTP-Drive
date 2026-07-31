"""Where the wrapped master key lives, and how it gets opened.

One document, in its own collection, holding the master key encrypted under a
key derived from the SFTP password. Keeping it in the database rather than in
`.env` is what lets the password change without re-encrypting anything: only
this record is rewritten.

Two rules the rest of the code depends on:

* **the master key is never written anywhere in the clear**, including logs;
* **it is opened per connection and not held process-wide.** `ensure_usable`
  runs at startup so a bad password is a startup failure rather than a
  mystery at first upload, but it drops the key immediately afterwards. The
  copy that does the work belongs to one connection and goes away with it.
"""

import logging

from src.crypto import (
    DEFAULT_PBKDF2_ITERATIONS,
    KeyUnwrapError,
    generate_master_key,
    unwrap_master_key,
    wrap_master_key,
)
from src.db import db

logger = logging.getLogger(__name__)

KEYSTORE_ID = "master"

# Below this, the derivation is fast enough to be worth brute-forcing offline
# by anyone who obtains the database. Not enforced -- the test suite runs far
# lower on purpose -- but said out loud at startup.
RECOMMENDED_MIN_ITERATIONS = 100_000


class KeystoreError(RuntimeError):
    """The master key could not be established for this configuration."""


async def load_record():
    return await db.get_db().keystore.find_one({"id": KEYSTORE_ID})


async def _store(record: dict):
    record = dict(record, id=KEYSTORE_ID)
    await db.get_db().keystore.replace_one(
        {"id": KEYSTORE_ID}, record, upsert=True)
    return record


async def bootstrap(password: str, *, iterations: int = DEFAULT_PBKDF2_ITERATIONS):
    """Create a fresh random master key wrapped under `password`.

    The key is generated here and only here. Nothing derives it from the
    password, so changing the password later is a re-wrap rather than a
    re-encryption of every chunk on Discord.
    """
    record = wrap_master_key(password, generate_master_key(), iterations=iterations)
    await _store(record)
    logger.info("Created a new wrapped master key (%s, %d iterations)",
                record["kdf"], record["kdf_iterations"])


async def open_master_key(password: str) -> bytes:
    """The master key for this password, or `KeyUnwrapError`.

    This is the authentication that matters: a password that does not open
    the key cannot read anything, whatever else it satisfies.
    """
    record = await load_record()
    if record is None:
        raise KeystoreError("no master key has been created yet")
    return unwrap_master_key(password, record)


async def rewrap(old_password: str, new_password: str, *,
                 iterations: int = DEFAULT_PBKDF2_ITERATIONS):
    """Move the master key to a new password. Stored data is untouched."""
    master_key = unwrap_master_key(old_password, await load_record())
    await _store(wrap_master_key(new_password, master_key, iterations=iterations))
    logger.info("Master key re-wrapped under the new password")


async def ensure_usable(password: str, *, old_password: str = None,
                        iterations: int = DEFAULT_PBKDF2_ITERATIONS):
    """Startup check: make sure the configured password opens the master key.

    Deliberately fatal when it cannot. A server that accepts logins and then
    fails every read looks like data loss from the client side, which is a far
    worse failure than refusing to start.
    """
    if iterations < RECOMMENDED_MIN_ITERATIONS:
        logger.warning(
            "PBKDF2_ITERATIONS is %d, below the recommended %d. Anyone who "
            "obtains the database can try passwords this much faster.",
            iterations, RECOMMENDED_MIN_ITERATIONS)

    record = await load_record()
    if record is None:
        await bootstrap(password, iterations=iterations)
        return

    try:
        unwrap_master_key(password, record)
        return
    except KeyUnwrapError:
        pass

    if not old_password:
        raise KeystoreError(
            "SFTP_PASSWORD does not open the stored master key. Either it was "
            "changed without re-wrapping -- in which case set "
            "SFTP_PASSWORD_OLD to the previous one and restart, and this "
            "server will re-wrap and carry on -- or this database belongs to "
            "a different deployment."
        )

    try:
        await rewrap(old_password, password, iterations=iterations)
    except KeyUnwrapError as exc:
        raise KeystoreError(
            "neither SFTP_PASSWORD nor SFTP_PASSWORD_OLD opens the stored "
            "master key, so it cannot be re-wrapped."
        ) from exc

    logger.warning(
        "The master key was re-wrapped under SFTP_PASSWORD. Remove "
        "SFTP_PASSWORD_OLD from the environment now that it is no longer "
        "needed.")
