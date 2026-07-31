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
    KDF_ARGON2ID,
    KDF_PBKDF2_SHA256,
    KeyUnwrapError,
    generate_master_key,
    kdf_settings,
    record_matches_settings,
    unwrap_master_key,
    wrap_master_key,
)
from src.db import db

logger = logging.getLogger(__name__)

KEYSTORE_ID = "master"

# Below these, the derivation is fast enough to be worth brute-forcing offline
# by anyone who obtains the database. Not enforced -- the test suite runs far
# lower on purpose -- but said out loud at startup. The Argon2id numbers are
# OWASP's floor; the PBKDF2 one is well under OWASP's 600k, because a
# deployment still on PBKDF2 has already been told to move.
RECOMMENDED_MIN_ITERATIONS = 100_000
RECOMMENDED_MIN_ARGON2_MEMORY_KIB = 19 * 1024
RECOMMENDED_MIN_ARGON2_TIME_COST = 2


class KeystoreError(RuntimeError):
    """The master key could not be established for this configuration."""


async def load_record():
    return await db.get_db().keystore.find_one({"id": KEYSTORE_ID})


async def _store(record: dict):
    record = dict(record, id=KEYSTORE_ID)
    await db.get_db().keystore.replace_one(
        {"id": KEYSTORE_ID}, record, upsert=True)
    return record


def _describe(settings: dict) -> str:
    """The cost parameters, for a log line. Never the key or the password."""
    costs = ", ".join(f"{name}={value}" for name, value in settings.items()
                      if name != "kdf")
    return f"{settings['kdf']} ({costs})"


def _warn_if_weak(settings: dict):
    """Say so at startup when the configured cost is below the recommendation.

    A warning rather than a refusal: the suite deliberately runs far below
    these, and an operator who has decided to trade cost for login latency on
    a machine that cannot spare the memory is entitled to that decision. What
    they are not entitled to is not being told.
    """
    if settings["kdf"] == KDF_PBKDF2_SHA256:
        if settings["iterations"] < RECOMMENDED_MIN_ITERATIONS:
            logger.warning(
                "PBKDF2_ITERATIONS is %d, below the recommended %d. Anyone who "
                "obtains the database can try passwords this much faster.",
                settings["iterations"], RECOMMENDED_MIN_ITERATIONS)
        return

    if settings["kdf"] == KDF_ARGON2ID:
        if settings["memory_kib"] < RECOMMENDED_MIN_ARGON2_MEMORY_KIB:
            logger.warning(
                "ARGON2_MEMORY_KIB is %d, below the recommended %d. Memory is "
                "the parameter that makes Argon2id expensive to attack in "
                "parallel, so lowering it gives up most of the benefit.",
                settings["memory_kib"], RECOMMENDED_MIN_ARGON2_MEMORY_KIB)
        if settings["time_cost"] < RECOMMENDED_MIN_ARGON2_TIME_COST:
            logger.warning(
                "ARGON2_TIME_COST is %d, below the recommended %d.",
                settings["time_cost"], RECOMMENDED_MIN_ARGON2_TIME_COST)


async def bootstrap(password: str, *, settings: dict = None):
    """Create a fresh random master key wrapped under `password`.

    The key is generated here and only here. Nothing derives it from the
    password, so changing the password later is a re-wrap rather than a
    re-encryption of every chunk on Discord.
    """
    settings = kdf_settings() if settings is None else settings
    await _store(wrap_master_key(password, generate_master_key(),
                                 settings=settings))
    logger.info("Created a new wrapped master key: %s", _describe(settings))


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
                 settings: dict = None):
    """Move the master key to a new password. Stored data is untouched."""
    settings = kdf_settings() if settings is None else settings
    master_key = unwrap_master_key(old_password, await load_record())
    await _replace_wrapping(master_key, new_password, settings)
    logger.info("Master key re-wrapped under the new password")


async def _replace_wrapping(master_key: bytes, password: str, settings: dict):
    """Write a new wrapping of `master_key`, but only once it is known to open.

    The check is the point. This is the single most dangerous write in the
    system -- a bad record here does not corrupt one file, it makes every
    stored byte unreadable -- and a wrap that cannot be unwrapped costs
    nothing to detect here and everything to discover later.
    """
    record = wrap_master_key(password, master_key, settings=settings)

    try:
        reopened = unwrap_master_key(password, record)
    except KeyUnwrapError as exc:
        raise KeystoreError(
            f"the re-wrapped master key does not open again ({exc}); the "
            "existing record has been left untouched") from exc

    if reopened != master_key:
        raise KeystoreError(
            "the re-wrapped master key opens to different bytes; the existing "
            "record has been left untouched")

    await _store(record)


async def upgrade_kdf(password: str, settings: dict) -> bool:
    """Rewrite the stored record under `settings` if it is not already there.

    Returns whether anything was written. The master key itself is unchanged,
    so this touches no stored file: it is the wrapping around the key that
    moves, which is exactly why the KDF can be replaced without a migration.
    """
    record = await load_record()
    if record is None or record_matches_settings(record, settings):
        return False

    master_key = unwrap_master_key(password, record)
    await _replace_wrapping(master_key, password, settings)
    logger.warning("Master key re-wrapped under %s (was %s). No stored file "
                   "was touched -- only the wrapping around the key changed.",
                   _describe(settings), record.get("kdf"))
    return True


async def ensure_usable(password: str, *, old_password: str = None,
                        settings: dict = None, upgrade: bool = False):
    """Startup check: make sure the configured password opens the master key.

    Deliberately fatal when it cannot. A server that accepts logins and then
    fails every read looks like data loss from the client side, which is a far
    worse failure than refusing to start.

    `upgrade` moves an existing record onto `settings` -- raising the cost, or
    moving off PBKDF2 onto Argon2id. Off by default: it is the one operation
    here that rewrites a record the deployment is currently depending on, and
    an operator should be the one deciding when that happens.
    """
    settings = kdf_settings() if settings is None else settings
    _warn_if_weak(settings)

    record = await load_record()
    if record is None:
        await bootstrap(password, settings=settings)
        return

    try:
        unwrap_master_key(password, record)
    except KeyUnwrapError:
        pass
    else:
        if upgrade:
            await upgrade_kdf(password, settings)
        elif record.get("kdf") != settings["kdf"]:
            logger.warning(
                "The stored master key is still wrapped with %s while this "
                "server is configured for %s. Existing records keep working, "
                "but the upgrade only happens when KDF_UPGRADE is enabled.",
                record.get("kdf"), settings["kdf"])
        return

    if not old_password:
        raise KeystoreError(
            "SFTP_PASSWORD does not open the stored master key. Either it was "
            "changed without re-wrapping -- in which case set "
            "SFTP_PASSWORD_OLD to the previous one and restart, and this "
            "server will re-wrap and carry on -- or this database belongs to "
            "a different deployment."
        )

    try:
        await rewrap(old_password, password, settings=settings)
    except KeyUnwrapError as exc:
        raise KeystoreError(
            "neither SFTP_PASSWORD nor SFTP_PASSWORD_OLD opens the stored "
            "master key, so it cannot be re-wrapped."
        ) from exc

    logger.warning(
        "The master key was re-wrapped under SFTP_PASSWORD. Remove "
        "SFTP_PASSWORD_OLD from the environment now that it is no longer "
        "needed.")
