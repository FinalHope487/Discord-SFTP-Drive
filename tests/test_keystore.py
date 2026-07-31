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
    KEY_SIZE,
    KeyUnwrapError,
    generate_master_key,
    unwrap_master_key,
    wrap_master_key,
)

PASSWORD = "correct horse battery staple"
OTHER = "Tr0ub4dor&3-and-then-some"

# The suite runs PBKDF2 far below production cost on purpose; see conftest.
FAST = 1000


# ------------------------------------------------------------- the primitive


def test_wrap_then_unwrap_returns_the_same_key():
    key = generate_master_key()
    assert unwrap_master_key(PASSWORD, wrap_master_key(PASSWORD, key,
                                                       iterations=FAST)) == key


def test_a_wrong_password_is_rejected_not_silently_wrong():
    # Without the MAC this would return 32 bytes of garbage that look exactly
    # like a key, and every later read would fail as a corrupt file instead of
    # as a bad password.
    record = wrap_master_key(PASSWORD, generate_master_key(), iterations=FAST)
    with pytest.raises(KeyUnwrapError):
        unwrap_master_key(OTHER, record)


def test_the_wrapped_key_does_not_contain_the_key():
    key = generate_master_key()
    record = wrap_master_key(PASSWORD, key, iterations=FAST)
    assert key.hex() not in repr(record)
    assert bytes.fromhex(record["ciphertext"]) != key


def test_wrapping_twice_reuses_nothing():
    key = generate_master_key()
    a = wrap_master_key(PASSWORD, key, iterations=FAST)
    b = wrap_master_key(PASSWORD, key, iterations=FAST)
    assert a["kdf_salt"] != b["kdf_salt"]
    assert a["nonce"] != b["nonce"]
    assert a["ciphertext"] != b["ciphertext"]


def test_the_record_carries_its_own_parameters():
    # So that raising the cost later does not strand the keys wrapped before.
    record = wrap_master_key(PASSWORD, generate_master_key(), iterations=FAST)
    assert record["kdf"] == "pbkdf2-sha256"
    assert record["kdf_iterations"] == FAST
    assert unwrap_master_key(PASSWORD, record)  # opens without being told how


def test_a_record_claiming_an_unknown_kdf_is_refused():
    record = wrap_master_key(PASSWORD, generate_master_key(), iterations=FAST)
    record["kdf"] = "rot13"
    with pytest.raises(KeyUnwrapError):
        unwrap_master_key(PASSWORD, record)


@pytest.mark.parametrize("field", ["kdf_salt", "nonce", "ciphertext", "hmac"])
def test_a_tampered_record_is_refused(field):
    record = wrap_master_key(PASSWORD, generate_master_key(), iterations=FAST)
    record[field] = "00" * (len(bytes.fromhex(record[field])))
    with pytest.raises(KeyUnwrapError):
        unwrap_master_key(PASSWORD, record)


def test_a_malformed_record_raises_cleanly():
    # Not a KeyError or a ValueError escaping from bytes.fromhex.
    with pytest.raises(KeyUnwrapError):
        unwrap_master_key(PASSWORD, {"kdf": "pbkdf2-sha256"})


def test_a_key_of_the_wrong_length_is_refused():
    with pytest.raises(ValueError):
        wrap_master_key(PASSWORD, b"short", iterations=FAST)


# ------------------------------------------------------------- the keystore


async def test_bootstrap_creates_a_usable_key(fake_db):
    await keystore.bootstrap(PASSWORD, iterations=FAST)
    key = await keystore.open_master_key(PASSWORD)
    assert len(key) == KEY_SIZE


async def test_bootstrap_creates_a_different_key_every_time(fake_db):
    await keystore.bootstrap(PASSWORD, iterations=FAST)
    first = await keystore.open_master_key(PASSWORD)

    fake_db.keystore.docs.clear()
    await keystore.bootstrap(PASSWORD, iterations=FAST)
    assert await keystore.open_master_key(PASSWORD) != first


async def test_opening_with_the_wrong_password_fails(fake_db):
    await keystore.bootstrap(PASSWORD, iterations=FAST)
    with pytest.raises(KeyUnwrapError):
        await keystore.open_master_key(OTHER)


async def test_opening_before_bootstrap_is_an_error(fake_db):
    with pytest.raises(keystore.KeystoreError):
        await keystore.open_master_key(PASSWORD)


async def test_ensure_usable_bootstraps_on_a_fresh_database(fake_db):
    await keystore.ensure_usable(PASSWORD, iterations=FAST)
    assert await keystore.load_record() is not None


async def test_ensure_usable_is_idempotent(fake_db):
    await keystore.ensure_usable(PASSWORD, iterations=FAST)
    key = await keystore.open_master_key(PASSWORD)
    await keystore.ensure_usable(PASSWORD, iterations=FAST)
    assert await keystore.open_master_key(PASSWORD) == key, (
        "a restart re-wrapped the key instead of leaving it alone")


async def test_ensure_usable_refuses_a_password_that_does_not_open_the_key(fake_db):
    # Fatal on purpose: a server that starts here accepts logins and then
    # fails every read, which looks like data loss from the client side.
    await keystore.bootstrap(PASSWORD, iterations=FAST)
    with pytest.raises(keystore.KeystoreError, match="SFTP_PASSWORD_OLD"):
        await keystore.ensure_usable(OTHER, iterations=FAST)


# -------------------------------------------------------- changing password


async def test_changing_the_password_keeps_the_key(fake_db):
    """The whole reason the key is wrapped rather than derived.

    If the data key came from the password, this key would necessarily change
    and every stored chunk would become unreadable.
    """
    await keystore.bootstrap(PASSWORD, iterations=FAST)
    before = await keystore.open_master_key(PASSWORD)

    await keystore.rewrap(PASSWORD, OTHER, iterations=FAST)

    assert await keystore.open_master_key(OTHER) == before


async def test_the_old_password_stops_working_after_a_rewrap(fake_db):
    await keystore.bootstrap(PASSWORD, iterations=FAST)
    await keystore.rewrap(PASSWORD, OTHER, iterations=FAST)
    with pytest.raises(KeyUnwrapError):
        await keystore.open_master_key(PASSWORD)


async def test_ensure_usable_rewraps_when_given_the_old_password(fake_db):
    await keystore.bootstrap(PASSWORD, iterations=FAST)
    before = await keystore.open_master_key(PASSWORD)

    await keystore.ensure_usable(OTHER, old_password=PASSWORD, iterations=FAST)

    assert await keystore.open_master_key(OTHER) == before


async def test_ensure_usable_refuses_when_neither_password_fits(fake_db):
    await keystore.bootstrap(PASSWORD, iterations=FAST)
    with pytest.raises(keystore.KeystoreError):
        await keystore.ensure_usable(OTHER, old_password="also wrong",
                                     iterations=FAST)


async def test_a_failed_rewrap_leaves_the_original_intact(fake_db):
    await keystore.bootstrap(PASSWORD, iterations=FAST)
    before = await keystore.open_master_key(PASSWORD)

    with pytest.raises(keystore.KeystoreError):
        await keystore.ensure_usable(OTHER, old_password="also wrong",
                                     iterations=FAST)

    assert await keystore.open_master_key(PASSWORD) == before


async def test_a_low_iteration_count_is_warned_about(fake_db, caplog):
    await keystore.ensure_usable(PASSWORD, iterations=FAST)
    assert any("PBKDF2_ITERATIONS" in r.message for r in caplog.records)
