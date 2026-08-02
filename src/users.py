"""Accounts: who may log in, whose tree they get, and whose key opens for them.

This is the first of the three structural steps that turn a server with one
hard-coded account into one whose account count is data. It is deliberately
the step that changes no behaviour: there is still exactly one user, still
supplied by `SFTP_USER` / `SFTP_PASSWORD`, and it is still synchronised from
the environment at startup. What moved is *where the credential lives* --
from two `compare_digest` calls against module constants into a row that a
second row could sit beside.

Two things here are not obvious.

**The password hash is not the key-encryption key, and must never become it.**
`crypto.derive_kek` turns the password into the key that unwraps the master
key; this module turns the same password into a value stored for checking.
Deriving both from one function on one input would mean the thing kept for
comparison and the thing that decrypts every stored byte shared an ancestor.
They use different functions on purpose -- `PasswordHasher` here, `derive_kek`
there -- and the salt in each is independent.

**A username that does not exist still costs a full password verification.**
The old code compared two fixed-length secrets in constant time, so "no such
user" and "wrong password" were indistinguishable. A database lookup is not:
returning early on a miss makes the response time a reliable oracle for which
usernames exist. `authenticate` therefore verifies against a throwaway hash
built with the same cost parameters before reporting the failure. This is a
new attack surface that arrived with the collection, not a pre-existing one.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from src.config import password_hash_settings
from src.db import db

logger = logging.getLogger(__name__)

# Verified against when the username is unknown or the account is disabled, so
# that either failure costs the same as a wrong password. The plaintext is
# fixed and public; it is never compared with anything a caller supplies, and
# no account can be reached through it.
_DUMMY_SECRET = "not-a-password-just-something-to-spend-the-same-time-on"

# Keyed by cost parameters: one Argon2 run to build, then reused. Computing it
# lazily rather than at import means the suite -- which imports this module
# constantly and authenticates in almost every test -- pays for it once.
_dummy_hashes: dict = {}


@dataclass(frozen=True)
class Session:
    """What one authenticated connection carries.

    The master key used to be handed to the connection on its own. It cannot
    be any more: which tree the key belongs to is now a property of the
    account, so the two travel together or a session could open one user's
    key against another user's root.
    """

    key: bytes
    root_id: str
    username: str


def keystore_id(user: dict) -> str:
    """Which wrapped-key record belongs to this account.

    Keyed by the account's id rather than its username so that renaming an
    account -- which nothing does yet -- could never be the operation that
    strands its master key.
    """
    return f"user:{user['id']}"


def _now() -> int:
    return int(time.time())


def _hasher(settings: dict = None) -> PasswordHasher:
    # Constructed per call rather than cached: it only stores parameters, so
    # this costs nothing, and a cached one would quietly outlive a change to
    # the configured cost.
    return PasswordHasher(**(password_hash_settings() if settings is None
                             else settings))


def _dummy(settings: dict) -> str:
    key = tuple(sorted(settings.items()))
    if key not in _dummy_hashes:
        _dummy_hashes[key] = _hasher(settings).hash(_DUMMY_SECRET)
    return _dummy_hashes[key]


def _verify(stored: str, password: str, settings: dict) -> bool:
    """Whether `password` matches `stored`. Never raises.

    A malformed or absent hash is a failure, not an exception: this runs on
    the authentication path, where an unhandled error would be an outage and
    a caught one is simply a login that does not succeed.
    """
    if not stored:
        return False
    try:
        return _hasher(settings).verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


async def find(username: str):
    return await db.get_db().users.find_one({"username": username})


async def authenticate(username: str, password: str):
    """The account for these credentials, or `None`.

    Never says *why* it failed. The caller logs at most that a login was
    refused; distinguishing "no such user" from "wrong password" in a message
    that reaches a client would hand back exactly what the dummy verification
    below exists to withhold.
    """
    settings = password_hash_settings()
    record = await find(username)

    # Off the event loop. Argon2 is memory-hard C that does not yield, so
    # verifying in place blocks every other connection for its whole duration
    # -- about 125ms at production cost. That was survivable while the only
    # way in was SSH, whose own connection limits bounded it; an HTTP endpoint
    # anyone can POST to makes it a way to stall the SFTP server from outside.
    if record is None or record.get("disabled"):
        # Spend the same time as a real check. See the module docstring: the
        # collection lookup is what made this necessary.
        await asyncio.to_thread(_verify, _dummy(settings), password, settings)
        return None

    if not await asyncio.to_thread(_verify, record.get("password_hash"),
                                   password, settings):
        return None

    return record


async def sync_env_user(username: str, password: str, *, root_id: str) -> dict:
    """Make `SFTP_USER` / `SFTP_PASSWORD` into a row, and keep it current.

    The environment stays authoritative for this one account, which is what
    keeps this step behaviour-preserving: the credentials that worked before
    are the credentials that work after, and an operator who changes them in
    `.env` still gets what they asked for.

    Called before `keystore.ensure_usable`, so a changed password is written
    here and only then proved against the wrapped key. If that proof fails the
    process exits and never serves a connection, which is why the ordering is
    safe: the row can briefly describe a password that cannot open anything,
    but nothing is listening while it does.
    """
    settings = password_hash_settings()
    record = await find(username)

    if record is None:
        record = {
            "id": str(uuid.uuid4()),
            "username": username,
            "password_hash": _hasher(settings).hash(password),
            "root_id": root_id,
            "created_at": _now(),
            "disabled": False,
        }
        await db.get_db().users.insert_one(record)
        logger.info("Created the account for %r from the environment", username)
        return record

    if not _verify(record.get("password_hash"), password, settings):
        updated = _hasher(settings).hash(password)
        await db.get_db().users.update_one(
            {"id": record["id"]}, {"$set": {"password_hash": updated}})
        record["password_hash"] = updated
        logger.warning(
            "SFTP_PASSWORD no longer matches the stored hash for %r; the hash "
            "has been updated. If the master key was wrapped under the old "
            "password, SFTP_PASSWORD_OLD must be set or startup will refuse "
            "to continue.", username)

    return record
