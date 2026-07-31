"""The wrapped master key: deriving it, opening it, and changing the password.

The property that shapes all of this is that the data key is *random*, not
derived from the password. Deriving it directly would be simpler and is what
the original plan said, but it makes a password change equivalent to
destroying every stored file -- `test_changing_the_password_keeps_the_key` is
the one that pins the difference.
"""

import pytest

from src import keystore
from src.crypto import (
    KDF_ARGON2ID,
    KDF_PBKDF2_SHA256,
    KEY_SIZE,
    KeyUnwrapError,
    generate_master_key,
    kdf_settings,
    unwrap_master_key,
    wrap_master_key,
)

PASSWORD = "correct horse battery staple"
OTHER = "Tr0ub4dor&3-and-then-some"

# The suite runs either KDF far below production cost on purpose; see conftest.
# `FAST` is the production *algorithm* at a trivial cost, so the default path
# is the one most of these exercise.
FAST = kdf_settings(KDF_ARGON2ID, time_cost=1, memory_kib=64, parallelism=1)
FAST_PBKDF2 = kdf_settings(KDF_PBKDF2_SHA256, iterations=1000)


# ------------------------------------------------------------- the primitive


def test_wrap_then_unwrap_returns_the_same_key():
    key = generate_master_key()
    assert unwrap_master_key(PASSWORD, wrap_master_key(PASSWORD, key,
                                                       settings=FAST)) == key


def test_a_wrong_password_is_rejected_not_silently_wrong():
    # Without the MAC this would return 32 bytes of garbage that look exactly
    # like a key, and every later read would fail as a corrupt file instead of
    # as a bad password.
    record = wrap_master_key(PASSWORD, generate_master_key(), settings=FAST)
    with pytest.raises(KeyUnwrapError):
        unwrap_master_key(OTHER, record)


def test_the_wrapped_key_does_not_contain_the_key():
    key = generate_master_key()
    record = wrap_master_key(PASSWORD, key, settings=FAST)
    assert key.hex() not in repr(record)
    assert bytes.fromhex(record["ciphertext"]) != key


def test_wrapping_twice_reuses_nothing():
    key = generate_master_key()
    a = wrap_master_key(PASSWORD, key, settings=FAST)
    b = wrap_master_key(PASSWORD, key, settings=FAST)
    assert a["kdf_salt"] != b["kdf_salt"]
    assert a["nonce"] != b["nonce"]
    assert a["ciphertext"] != b["ciphertext"]


def test_the_record_carries_its_own_parameters():
    # So that raising the cost later does not strand the keys wrapped before.
    record = wrap_master_key(PASSWORD, generate_master_key(), settings=FAST)
    assert record["kdf"] == "argon2id"
    assert record["kdf_time_cost"] == FAST["time_cost"]
    assert record["kdf_memory_kib"] == FAST["memory_kib"]
    assert record["kdf_parallelism"] == FAST["parallelism"]
    assert unwrap_master_key(PASSWORD, record)  # opens without being told how


def test_a_new_wrap_uses_argon2id():
    # The default, not just an option: a deployment that configures nothing
    # gets the memory-hard function rather than the one it replaced.
    record = wrap_master_key(PASSWORD, generate_master_key())
    assert record["kdf"] == KDF_ARGON2ID


def test_a_pbkdf2_record_still_opens_after_the_default_moved():
    """The claim that switching KDF needs no migration, pinned.

    Every key wrapped before Argon2id existed here is a PBKDF2 record, and the
    only thing that makes it still readable is that the record names its own
    function instead of the code assuming the current default.
    """
    key = generate_master_key()
    record = wrap_master_key(PASSWORD, key, settings=FAST_PBKDF2)

    assert record["kdf"] == KDF_PBKDF2_SHA256
    assert record["kdf_iterations"] == FAST_PBKDF2["iterations"]
    assert unwrap_master_key(PASSWORD, record) == key


def test_a_record_claiming_an_unknown_kdf_is_refused():
    record = wrap_master_key(PASSWORD, generate_master_key(), settings=FAST)
    record["kdf"] = "rot13"
    with pytest.raises(KeyUnwrapError):
        unwrap_master_key(PASSWORD, record)


def test_wrapping_with_an_unknown_kdf_is_refused():
    with pytest.raises(KeyUnwrapError):
        kdf_settings("rot13")


@pytest.mark.parametrize("field", ["kdf_time_cost", "kdf_memory_kib",
                                   "kdf_parallelism"])
def test_a_missing_cost_parameter_is_refused_not_defaulted(field):
    # Filling one in from the current default would derive a different key and
    # surface as "wrong password", which is the worst possible way to say
    # "this record is incomplete".
    record = wrap_master_key(PASSWORD, generate_master_key(), settings=FAST)
    del record[field]
    with pytest.raises(KeyUnwrapError, match=field):
        unwrap_master_key(PASSWORD, record)


@pytest.mark.parametrize("field", ["kdf_time_cost", "kdf_memory_kib"])
def test_altering_a_cost_parameter_breaks_the_unwrap(field):
    # Not a security claim -- the record is not authenticated as a whole --
    # but it does confirm the parameters are genuinely read from the record
    # rather than being decorative alongside a hard-coded cost.
    record = wrap_master_key(PASSWORD, generate_master_key(), settings=FAST)
    record[field] = record[field] + 8
    with pytest.raises(KeyUnwrapError):
        unwrap_master_key(PASSWORD, record)


@pytest.mark.parametrize("field", ["kdf_salt", "nonce", "ciphertext", "hmac"])
def test_a_tampered_record_is_refused(field):
    record = wrap_master_key(PASSWORD, generate_master_key(), settings=FAST)
    record[field] = "00" * (len(bytes.fromhex(record[field])))
    with pytest.raises(KeyUnwrapError):
        unwrap_master_key(PASSWORD, record)


def test_a_malformed_record_raises_cleanly():
    # Not a KeyError or a ValueError escaping from bytes.fromhex.
    with pytest.raises(KeyUnwrapError):
        unwrap_master_key(PASSWORD, {"kdf": "pbkdf2-sha256"})


def test_a_key_of_the_wrong_length_is_refused():
    with pytest.raises(ValueError):
        wrap_master_key(PASSWORD, b"short", settings=FAST)


# ------------------------------------------------------------- the keystore


async def test_bootstrap_creates_a_usable_key(fake_db):
    await keystore.bootstrap(PASSWORD, settings=FAST)
    key = await keystore.open_master_key(PASSWORD)
    assert len(key) == KEY_SIZE


async def test_bootstrap_creates_a_different_key_every_time(fake_db):
    await keystore.bootstrap(PASSWORD, settings=FAST)
    first = await keystore.open_master_key(PASSWORD)

    fake_db.keystore.docs.clear()
    await keystore.bootstrap(PASSWORD, settings=FAST)
    assert await keystore.open_master_key(PASSWORD) != first


async def test_opening_with_the_wrong_password_fails(fake_db):
    await keystore.bootstrap(PASSWORD, settings=FAST)
    with pytest.raises(KeyUnwrapError):
        await keystore.open_master_key(OTHER)


async def test_opening_before_bootstrap_is_an_error(fake_db):
    with pytest.raises(keystore.KeystoreError):
        await keystore.open_master_key(PASSWORD)


async def test_ensure_usable_bootstraps_on_a_fresh_database(fake_db):
    await keystore.ensure_usable(PASSWORD, settings=FAST)
    assert await keystore.load_record() is not None


async def test_ensure_usable_is_idempotent(fake_db):
    await keystore.ensure_usable(PASSWORD, settings=FAST)
    key = await keystore.open_master_key(PASSWORD)
    await keystore.ensure_usable(PASSWORD, settings=FAST)
    assert await keystore.open_master_key(PASSWORD) == key, (
        "a restart re-wrapped the key instead of leaving it alone")


async def test_ensure_usable_refuses_a_password_that_does_not_open_the_key(fake_db):
    # Fatal on purpose: a server that starts here accepts logins and then
    # fails every read, which looks like data loss from the client side.
    await keystore.bootstrap(PASSWORD, settings=FAST)
    with pytest.raises(keystore.KeystoreError, match="SFTP_PASSWORD_OLD"):
        await keystore.ensure_usable(OTHER, settings=FAST)


# -------------------------------------------------------- changing password


async def test_changing_the_password_keeps_the_key(fake_db):
    """The whole reason the key is wrapped rather than derived.

    If the data key came from the password, this key would necessarily change
    and every stored chunk would become unreadable.
    """
    await keystore.bootstrap(PASSWORD, settings=FAST)
    before = await keystore.open_master_key(PASSWORD)

    await keystore.rewrap(PASSWORD, OTHER, settings=FAST)

    assert await keystore.open_master_key(OTHER) == before


async def test_the_old_password_stops_working_after_a_rewrap(fake_db):
    await keystore.bootstrap(PASSWORD, settings=FAST)
    await keystore.rewrap(PASSWORD, OTHER, settings=FAST)
    with pytest.raises(KeyUnwrapError):
        await keystore.open_master_key(PASSWORD)


async def test_ensure_usable_rewraps_when_given_the_old_password(fake_db):
    await keystore.bootstrap(PASSWORD, settings=FAST)
    before = await keystore.open_master_key(PASSWORD)

    await keystore.ensure_usable(OTHER, old_password=PASSWORD, settings=FAST)

    assert await keystore.open_master_key(OTHER) == before


async def test_ensure_usable_refuses_when_neither_password_fits(fake_db):
    await keystore.bootstrap(PASSWORD, settings=FAST)
    with pytest.raises(keystore.KeystoreError):
        await keystore.ensure_usable(OTHER, old_password="also wrong",
                                     settings=FAST)


async def test_a_failed_rewrap_leaves_the_original_intact(fake_db):
    await keystore.bootstrap(PASSWORD, settings=FAST)
    before = await keystore.open_master_key(PASSWORD)

    with pytest.raises(keystore.KeystoreError):
        await keystore.ensure_usable(OTHER, old_password="also wrong",
                                     settings=FAST)

    assert await keystore.open_master_key(PASSWORD) == before


async def test_a_low_iteration_count_is_warned_about(fake_db, caplog):
    await keystore.ensure_usable(PASSWORD, settings=FAST_PBKDF2)
    assert any("PBKDF2_ITERATIONS" in r.message for r in caplog.records)


async def test_a_low_argon2_memory_cost_is_warned_about(fake_db, caplog):
    # Memory is the parameter Argon2id's whole advantage rests on, so a low
    # one is worth as much noise as a low iteration count used to be.
    await keystore.ensure_usable(PASSWORD, settings=FAST)
    assert any("ARGON2_MEMORY_KIB" in r.message for r in caplog.records)


async def test_a_production_cost_is_not_warned_about(fake_db, caplog):
    # Cheap insurance against a warning that fires always and therefore says
    # nothing. Uses the real defaults, so it is the slowest test here.
    await keystore.ensure_usable(PASSWORD, settings=kdf_settings())
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


# ------------------------------------------------------------ changing KDF


async def test_upgrade_moves_a_pbkdf2_record_to_argon2id(fake_db):
    """The migration-free upgrade path, end to end.

    The master key has to come back identical: if it changed, every chunk on
    Discord would have just become unreadable.
    """
    await keystore.bootstrap(PASSWORD, settings=FAST_PBKDF2)
    before = await keystore.open_master_key(PASSWORD)

    assert await keystore.upgrade_kdf(PASSWORD, FAST) is True

    record = await keystore.load_record()
    assert record["kdf"] == KDF_ARGON2ID
    assert await keystore.open_master_key(PASSWORD) == before


async def test_upgrade_is_a_no_op_when_already_on_the_configured_settings(fake_db):
    await keystore.bootstrap(PASSWORD, settings=FAST)
    before = await keystore.load_record()

    assert await keystore.upgrade_kdf(PASSWORD, FAST) is False
    assert await keystore.load_record() == before, (
        "an unnecessary re-wrap still rewrote the record")


async def test_upgrade_notices_a_cost_change_within_the_same_kdf(fake_db):
    await keystore.bootstrap(PASSWORD, settings=FAST)
    stronger = kdf_settings(KDF_ARGON2ID, time_cost=2, memory_kib=64,
                            parallelism=1)

    assert await keystore.upgrade_kdf(PASSWORD, stronger) is True
    assert (await keystore.load_record())["kdf_time_cost"] == 2


async def test_ensure_usable_does_not_upgrade_unless_asked(fake_db, caplog):
    # The default has to be "leave it alone". Rewriting the one record that
    # every stored byte depends on is not something a restart should decide.
    await keystore.bootstrap(PASSWORD, settings=FAST_PBKDF2)

    await keystore.ensure_usable(PASSWORD, settings=FAST)

    assert (await keystore.load_record())["kdf"] == KDF_PBKDF2_SHA256
    assert any("KDF_UPGRADE" in r.message for r in caplog.records), (
        "the mismatch was left in place silently")


async def test_ensure_usable_upgrades_when_asked(fake_db):
    await keystore.bootstrap(PASSWORD, settings=FAST_PBKDF2)
    before = await keystore.open_master_key(PASSWORD)

    await keystore.ensure_usable(PASSWORD, settings=FAST, upgrade=True)

    assert (await keystore.load_record())["kdf"] == KDF_ARGON2ID
    assert await keystore.open_master_key(PASSWORD) == before


async def test_a_rewrap_that_does_not_open_again_is_not_stored(fake_db, monkeypatch):
    """The guard on the most dangerous write in the system.

    A stored record that cannot be unwrapped does not corrupt one file, it
    makes every stored byte unreadable -- so the new wrapping is checked
    before the old one is replaced, not after.
    """
    await keystore.bootstrap(PASSWORD, settings=FAST_PBKDF2)
    before = await keystore.load_record()

    def broken_wrap(password, master_key, *, settings=None):
        record = wrap_master_key(password, master_key, settings=settings)
        record["ciphertext"] = "00" * KEY_SIZE
        return record

    monkeypatch.setattr(keystore, "wrap_master_key", broken_wrap)

    with pytest.raises(keystore.KeystoreError):
        await keystore.upgrade_kdf(PASSWORD, FAST)

    assert await keystore.load_record() == before
