"""Accounts, and what changed when the credential stopped being a constant.

The old `validate_password` compared two fixed-length secrets with
`compare_digest` and opened the one master key. Three things about that were
free and are not any more, and each has a test here:

* **"no such user" used to cost the same as "wrong password".** A collection
  lookup returns immediately when nothing matches, so the response time became
  an oracle for which usernames exist. `authenticate` verifies against a
  throwaway hash to put the time back.
* **the password hash and the key-encryption key must stay unrelated.** One is
  stored for comparison, the other decrypts every byte in the system.
* **there is no longer "the" master key.** Model B: a key per account, so one
  account's password cannot open another's data even with the whole database
  in hand. That is the guarantee the last group of tests exists to pin down,
  because it is the one that is expensive to restore if it is ever quietly
  lost -- switching back means re-encrypting everyone's files.
"""

import pytest

from src import keystore, users
from src.config import kdf_settings, password_hash_settings
from src.crypto import KeyUnwrapError, derive_kek
from src.vfs import ROOT_ID, DiscordVFS
from tests.conftest import TEST_PASSWORD, TEST_USER, connect

OTHER_PASSWORD = "a-different-password-entirely"


# ------------------------------------------------------ syncing from the env


async def test_the_environment_account_becomes_a_row(fake_db):
    user = await users.sync_env_user(TEST_USER, TEST_PASSWORD, root_id=ROOT_ID)

    assert user["username"] == TEST_USER
    assert user["root_id"] == ROOT_ID
    assert user["disabled"] is False
    assert len(fake_db.users.docs) == 1


async def test_the_existing_root_stays_with_the_environment_account(fake_db):
    """The reason making trees per-account needed no migration.

    A directory's tag covers its id. Had the pre-existing root been given a
    fresh id, every tag in the tree beneath it would have had to be recomputed
    -- which is the one operation this project refuses to perform on data it
    cannot independently verify.
    """
    user = await users.sync_env_user(TEST_USER, TEST_PASSWORD, root_id=ROOT_ID)
    assert user["root_id"] == ROOT_ID


async def test_syncing_twice_does_not_rewrite_the_hash(fake_db):
    first = await users.sync_env_user(TEST_USER, TEST_PASSWORD, root_id=ROOT_ID)
    second = await users.sync_env_user(TEST_USER, TEST_PASSWORD, root_id=ROOT_ID)

    assert second["id"] == first["id"]
    assert second["password_hash"] == first["password_hash"], (
        "an ordinary restart re-hashed a password that had not changed")
    assert len(fake_db.users.docs) == 1


async def test_a_changed_environment_password_updates_the_hash(fake_db):
    await users.sync_env_user(TEST_USER, TEST_PASSWORD, root_id=ROOT_ID)
    await users.sync_env_user(TEST_USER, OTHER_PASSWORD, root_id=ROOT_ID)

    assert await users.authenticate(TEST_USER, OTHER_PASSWORD) is not None
    assert await users.authenticate(TEST_USER, TEST_PASSWORD) is None


# -------------------------------------------------------------- the password


async def test_the_stored_hash_is_argon2id_and_not_the_password(fake_db):
    user = await users.sync_env_user(TEST_USER, TEST_PASSWORD, root_id=ROOT_ID)

    assert user["password_hash"].startswith("$argon2id$")
    assert TEST_PASSWORD not in user["password_hash"]


async def test_the_stored_hash_is_not_the_key_encryption_key(fake_db):
    """The two derivations must not share an output.

    `derive_kek` produces the key that unwraps the master key; the hash here
    is produced for comparison. If one were reused as the other, the value
    kept in the database for checking a password would be -- or would reveal
    -- the key that decrypts everything.
    """
    user = await users.sync_env_user(TEST_USER, TEST_PASSWORD, root_id=ROOT_ID)
    stored = user["password_hash"]

    settings = kdf_settings()
    for salt in (b"\x00" * 16, b"\x11" * 16):
        assert derive_kek(TEST_PASSWORD, salt, settings).hex() not in stored


async def test_two_accounts_with_the_same_password_hash_differently(fake_db):
    # A per-record salt, which is what stops the hashes themselves from
    # revealing that two accounts share a password.
    first = await users.sync_env_user(TEST_USER, TEST_PASSWORD, root_id=ROOT_ID)
    await db_insert_user(fake_db, "second", TEST_PASSWORD)
    second = await users.find("second")

    assert first["password_hash"] != second["password_hash"]


async def db_insert_user(fake_db, username, password):
    from argon2 import PasswordHasher

    await fake_db.users.insert_one({
        "id": f"id-{username}",
        "username": username,
        "password_hash": PasswordHasher(**password_hash_settings()).hash(password),
        "root_id": f"root:{username}",
        "created_at": 0,
        "disabled": False,
    })


# ---------------------------------------------------------- authenticating


async def test_the_right_credentials_return_the_account(fake_db):
    await users.sync_env_user(TEST_USER, TEST_PASSWORD, root_id=ROOT_ID)
    user = await users.authenticate(TEST_USER, TEST_PASSWORD)
    assert user is not None and user["username"] == TEST_USER


async def test_the_wrong_password_is_refused(fake_db):
    await users.sync_env_user(TEST_USER, TEST_PASSWORD, root_id=ROOT_ID)
    assert await users.authenticate(TEST_USER, OTHER_PASSWORD) is None


async def test_an_unknown_username_is_refused(fake_db):
    await users.sync_env_user(TEST_USER, TEST_PASSWORD, root_id=ROOT_ID)
    assert await users.authenticate("nobody", TEST_PASSWORD) is None


async def test_an_unknown_username_still_costs_a_verification(fake_db, monkeypatch):
    """The user-enumeration guard, asserted on behaviour rather than timing.

    A timing assertion would be flaky; what actually matters is that the
    early return does not exist. Counting the verifications catches the
    refactor that reintroduces it, which a wall-clock test would only catch
    on an unloaded machine.
    """
    await users.sync_env_user(TEST_USER, TEST_PASSWORD, root_id=ROOT_ID)

    calls = []
    original = users._verify
    monkeypatch.setattr(users, "_verify",
                        lambda *a, **k: (calls.append(1), original(*a, **k))[1])

    await users.authenticate("nobody-at-all", TEST_PASSWORD)
    unknown = len(calls)

    calls.clear()
    await users.authenticate(TEST_USER, OTHER_PASSWORD)
    wrong_password = len(calls)

    assert unknown == wrong_password == 1, (
        "an unknown username took a different number of password "
        "verifications than a wrong password, which is how it becomes "
        "possible to enumerate accounts by timing them")


async def test_a_disabled_account_is_refused(fake_db):
    user = await users.sync_env_user(TEST_USER, TEST_PASSWORD, root_id=ROOT_ID)
    await fake_db.users.update_one({"id": user["id"]},
                                   {"$set": {"disabled": True}})

    assert await users.authenticate(TEST_USER, TEST_PASSWORD) is None


async def test_a_disabled_account_still_costs_a_verification(fake_db, monkeypatch):
    # Same reasoning as the unknown username: a disabled account that failed
    # instantly would be distinguishable from a wrong password.
    user = await users.sync_env_user(TEST_USER, TEST_PASSWORD, root_id=ROOT_ID)
    await fake_db.users.update_one({"id": user["id"]},
                                   {"$set": {"disabled": True}})

    calls = []
    original = users._verify
    monkeypatch.setattr(users, "_verify",
                        lambda *a, **k: (calls.append(1), original(*a, **k))[1])

    await users.authenticate(TEST_USER, TEST_PASSWORD)
    assert len(calls) == 1


async def test_a_disabled_account_cannot_log_in_over_the_protocol(sftp_port,
                                                                  fake_db,
                                                                  account):
    await fake_db.users.update_one({"id": account["id"]},
                                   {"$set": {"disabled": True}})

    with pytest.raises(asyncssh_permission_denied()):
        async with connect(sftp_port):
            pass


def asyncssh_permission_denied():
    import asyncssh
    return asyncssh.PermissionDenied


# ------------------------------------------------- one key per account (B)


async def test_each_account_gets_its_own_master_key(fake_db):
    """Model B, stated as an assertion.

    Two accounts, two keys. Under model A -- one key wrapped once per
    password -- both of these would come back equal, and every test above
    would still pass. This is the one that would notice.
    """
    fast = kdf_settings_fast()
    await keystore.bootstrap("user:a", TEST_PASSWORD, settings=fast)
    await keystore.bootstrap("user:b", TEST_PASSWORD, settings=fast)

    key_a = await keystore.open_master_key("user:a", TEST_PASSWORD)
    key_b = await keystore.open_master_key("user:b", TEST_PASSWORD)

    assert key_a != key_b, (
        "two accounts share a master key, so either one's password decrypts "
        "the other's files")


async def test_one_accounts_password_does_not_open_anothers_key(fake_db):
    fast = kdf_settings_fast()
    await keystore.bootstrap("user:a", TEST_PASSWORD, settings=fast)
    await keystore.bootstrap("user:b", OTHER_PASSWORD, settings=fast)

    with pytest.raises(KeyUnwrapError):
        await keystore.open_master_key("user:b", TEST_PASSWORD)


def kdf_settings_fast():
    # `crypto.kdf_settings` builds an arbitrary one; `config.kdf_settings`
    # imported above reads the environment and takes no arguments. Same name,
    # different jobs, and both are wanted in this file.
    from src.crypto import KDF_ARGON2ID
    from src.crypto import kdf_settings as build_settings
    return build_settings(KDF_ARGON2ID, time_cost=1, memory_kib=64,
                          parallelism=1)


async def test_the_keystore_id_follows_the_account_id_not_the_username(fake_db):
    """Renaming an account must never be what strands its master key."""
    user = await users.sync_env_user(TEST_USER, TEST_PASSWORD, root_id=ROOT_ID)
    before = users.keystore_id(user)

    renamed = dict(user, username="something-else")
    assert users.keystore_id(renamed) == before


# ------------------------------------------------------- one tree per account


async def test_two_accounts_do_not_see_each_others_files(fake_db, fake_discord,
                                                         master_key):
    """Separate roots, so the same path in each resolves to a different file.

    The uniqueness constraint needed nothing added for this: each root is its
    own `parent_id`, so `/notes.txt` under two of them is already two distinct
    keys.
    """
    mine = DiscordVFS(master_key, ROOT_ID)
    await mine.ensure_root()

    theirs = DiscordVFS(master_key, "root:someone-else")
    await theirs.ensure_root()

    handle = await mine.open("/notes.txt", read=False, write=True, create=True)
    await handle.write_at(0, b"mine")
    await handle.close()

    assert await mine.get_node("/notes.txt") is not None
    assert await theirs.get_node("/notes.txt") is None
    assert [e["filename"] for e in await theirs.list_dir("/")] == []


async def test_a_vfs_without_a_root_is_refused(master_key):
    # No default. A caller who forgot which tree to serve would otherwise get
    # somebody else's, and with a key per account the mismatch surfaces as a
    # failed integrity check rather than as the programming error it is.
    with pytest.raises(ValueError, match="tree"):
        DiscordVFS(master_key, "")
