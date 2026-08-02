"""Where the wrapped master keys live, and how they get opened.

One document per account, in its own collection, holding that account's master
key encrypted under a key derived from its password. Keeping it in the
database rather than in `.env` is what lets the password change without
re-encrypting anything: only this record is rewritten.

Three rules the rest of the code depends on:

* **the master key is never written anywhere in the clear**, including logs;
* **it is opened per connection and not held process-wide.** `ensure_usable`
  runs at startup so a bad password is a startup failure rather than a
  mystery at first upload, but it drops the key immediately afterwards. The
  copy that does the work belongs to one connection and goes away with it;
* **one key per account, never a shared one.** Every function here takes the
  record id it operates on, so there is no ambient "the" master key to reach
  for by accident. That is what makes one account's password cryptographically
  unable to open another's data, which the alternative -- one key wrapped
  once per password -- would not have done.
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

# What the single-account deployments before this change called their one
# record. Nothing writes it any more; `adopt_legacy_record` moves it onto its
# owner's id at startup.
LEGACY_ID = "master"

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


async def load_record(record_id: str):
    return await db.get_db().keystore.find_one({"id": record_id})


async def _store(record_id: str, record: dict):
    record = dict(record, id=record_id)
    await db.get_db().keystore.replace_one(
        {"id": record_id}, record, upsert=True)
    return record


async def adopt_legacy_record(record_id: str) -> bool:
    """Move the pre-multi-user record onto its owner's id. Returns whether it did.

    **This rewrites one field and touches no ciphertext.** The wrapped key, its
    salt, its nonce, its MAC and the KDF parameters are all carried across
    untouched, so the record that opened before opens after and no password is
    needed to perform the move. That is the entire safety argument for doing it
    automatically: the dangerous operation in this module is re-wrapping a key,
    and this is not one -- it is a rename.

    Deliberately not conditional on the password. If `SFTP_PASSWORD` has also
    changed, the rename still happens and `ensure_usable` then finds the record
    where it expects it and takes the ordinary `SFTP_PASSWORD_OLD` path. Making
    the move depend on the password would mean a password change and this
    upgrade in the same restart left the record stranded under its old id, with
    a "no master key has been created yet" error naming neither cause.
    """
    if record_id == LEGACY_ID:
        return False
    if await load_record(record_id) is not None:
        return False

    legacy = await db.get_db().keystore.find_one({"id": LEGACY_ID})
    if legacy is None:
        return False

    await db.get_db().keystore.update_one(
        {"id": LEGACY_ID}, {"$set": {"id": record_id}})
    logger.warning(
        "Moved the shared master key record from %r to %r. The wrapped key "
        "itself is unchanged, so no stored file was touched and no password "
        "was needed. This runs once.", LEGACY_ID, record_id)
    return True


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


async def bootstrap(record_id: str, password: str, *, settings: dict = None):
    """Create a fresh random master key wrapped under `password`.

    The key is generated here and only here, and a new one is generated for
    every account rather than an existing one being wrapped a second time.
    Nothing derives it from the password, so changing the password later is a
    re-wrap rather than a re-encryption of every chunk on Discord.
    """
    settings = kdf_settings() if settings is None else settings
    await _store(record_id, wrap_master_key(password, generate_master_key(),
                                            settings=settings))
    logger.info("Created a new wrapped master key for %r: %s", record_id,
                _describe(settings))


async def open_master_key(record_id: str, password: str) -> bytes:
    """The master key for this account and password, or `KeyUnwrapError`.

    This is the authentication that matters: a password that does not open
    the key cannot read anything, whatever else it satisfies.
    """
    record = await load_record(record_id)
    if record is None:
        raise KeystoreError(f"no master key has been created for {record_id!r}")
    return unwrap_master_key(password, record)


async def rewrap(record_id: str, old_password: str, new_password: str, *,
                 settings: dict = None):
    """Move the master key to a new password. Stored data is untouched."""
    settings = kdf_settings() if settings is None else settings
    master_key = unwrap_master_key(old_password, await load_record(record_id))
    await _replace_wrapping(record_id, master_key, new_password, settings)
    logger.info("Master key for %r re-wrapped under the new password", record_id)


async def _replace_wrapping(record_id: str, master_key: bytes, password: str,
                            settings: dict):
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

    await _store(record_id, record)


async def upgrade_kdf(record_id: str, password: str, settings: dict) -> bool:
    """Rewrite the stored record under `settings` if it is not already there.

    Returns whether anything was written. The master key itself is unchanged,
    so this touches no stored file: it is the wrapping around the key that
    moves, which is exactly why the KDF can be replaced without a migration.
    """
    record = await load_record(record_id)
    if record is None or record_matches_settings(record, settings):
        return False

    master_key = unwrap_master_key(password, record)
    await _replace_wrapping(record_id, master_key, password, settings)
    logger.warning("Master key re-wrapped under %s (was %s). No stored file "
                   "was touched -- only the wrapping around the key changed.",
                   _describe(settings), record.get("kdf"))
    return True


async def ensure_usable(record_id: str, password: str, *,
                        old_password: str = None, settings: dict = None,
                        upgrade: bool = False):
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

    record = await load_record(record_id)
    if record is None:
        # A fresh database is the only place a new key may be created from
        # here. Once accounts became rows, the link from this deployment to
        # its wrapped key runs through `users` -- so a `users` collection that
        # is lost or rebuilt while `keystore` survives produces a new account
        # id, no record under it, and, without this, a brand-new master key
        # bootstrapped cheerfully on top of data that only the old one opens.
        # Every byte would still be there and nothing would ever read it
        # again.
        #
        # `bootstrap` itself is deliberately left unguarded: adding a second
        # account is exactly "a new record beside existing ones", and that is
        # its job. This is the startup path, where it never is.
        orphan = await db.get_db().keystore.find_one({})
        if orphan is not None:
            raise KeystoreError(
                f"there is no wrapped master key for {record_id!r}, but the "
                f"keystore is not empty (it holds {orphan.get('id')!r}). "
                "Creating one here would encrypt new data under a key that "
                "cannot read any of the existing data, so this refuses "
                "instead. The usual cause is a `users` collection that was "
                "dropped or restored separately from `keystore`: the account "
                "row and its key record have to travel together. Restore the "
                "account row, or point this deployment at the database its "
                "keystore belongs to."
            )
        await bootstrap(record_id, password, settings=settings)
        return

    try:
        unwrap_master_key(password, record)
    except KeyUnwrapError:
        pass
    else:
        if upgrade:
            await upgrade_kdf(record_id, password, settings)
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
        await rewrap(record_id, old_password, password, settings=settings)
    except KeyUnwrapError as exc:
        raise KeystoreError(
            "neither SFTP_PASSWORD nor SFTP_PASSWORD_OLD opens the stored "
            "master key, so it cannot be re-wrapped."
        ) from exc

    logger.warning(
        "The master key was re-wrapped under SFTP_PASSWORD. Remove "
        "SFTP_PASSWORD_OLD from the environment now that it is no longer "
        "needed.")
