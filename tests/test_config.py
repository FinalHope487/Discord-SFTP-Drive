"""Startup validation.

`check()` is pure over the mapping it is given, so these probe combinations
directly rather than reimporting the module under a mutated environment.
"""

import pytest

from src.config import MIN_PASSWORD_BYTES, ConfigError, check, validate

VALID = {
    "DISCORD_BOT_TOKEN": "token",
    "DISCORD_USER_ID": "100000000000000000",
    "SFTP_USER": "user",
    "SFTP_PASSWORD": "x" * MIN_PASSWORD_BYTES,
    "SFTP_PORT": "2222",
}


def env(**overrides):
    """A valid environment with `overrides` applied; `None` removes a key."""
    merged = dict(VALID, **overrides)
    return {k: v for k, v in merged.items() if v is not None}


def test_a_complete_environment_has_no_problems():
    assert check(VALID) == []


def test_validate_accepts_a_complete_environment():
    validate(VALID)  # must not raise


@pytest.mark.parametrize(
    "name", ["DISCORD_BOT_TOKEN", "SFTP_USER", "SFTP_PASSWORD"]
)
def test_each_secret_is_required(name):
    assert any(name in p for p in check(env(**{name: None})))


@pytest.mark.parametrize(
    "name", ["DISCORD_BOT_TOKEN", "SFTP_USER", "SFTP_PASSWORD"]
)
def test_empty_counts_as_unset(name):
    # `FOO=` in a .env file is a typo, not a deliberate empty password.
    assert any(name in p for p in check(env(**{name: ""})))


def test_missing_password_is_a_hard_failure():
    # It is the only secret left that can open stored data, so a server that
    # started without one would be a server with no key at all.
    with pytest.raises(ConfigError, match="SFTP_PASSWORD"):
        validate(env(SFTP_PASSWORD=None))


def test_a_short_password_is_rejected():
    # It stopped being only a login credential when it started wrapping the
    # master key: whoever gets the database gets to guess against it offline.
    problems = check(env(SFTP_PASSWORD="short"))
    assert any("SFTP_PASSWORD" in p and "5 bytes" in p for p in problems)


def test_a_multibyte_password_is_measured_in_bytes():
    # Four characters, twelve bytes once encoded. Counting characters would
    # have wrongly rejected it.
    assert check(env(SFTP_PASSWORD="密碼密碼")) == []


def test_no_aes_key_is_required_any_more():
    # The key is random and lives wrapped in the database; nothing in the
    # environment needs to carry it.
    assert check(env()) == []
    assert not any("AES" in p for p in check(env(SFTP_PASSWORD=None)))


def test_non_numeric_iteration_count_is_reported():
    assert any("PBKDF2_ITERATIONS" in p
               for p in check(env(PBKDF2_ITERATIONS="lots")))


def test_zero_iterations_is_rejected():
    assert any("PBKDF2_ITERATIONS" in p
               for p in check(env(PBKDF2_ITERATIONS="0")))


@pytest.mark.parametrize("name", ["ARGON2_TIME_COST", "ARGON2_MEMORY_KIB",
                                  "ARGON2_PARALLELISM"])
def test_non_numeric_argon2_costs_are_reported(name):
    assert any(name in p for p in check(env(**{name: "lots"})))


@pytest.mark.parametrize("name", ["ARGON2_TIME_COST", "ARGON2_PARALLELISM"])
def test_zero_argon2_costs_are_rejected(name):
    assert any(name in p for p in check(env(**{name: "0"})))


def test_an_unknown_kdf_is_rejected():
    problems = check(env(KDF="rot13"))
    assert any("KDF" in p and "rot13" in p for p in problems)


@pytest.mark.parametrize("kdf", ["argon2id", "pbkdf2-sha256"])
def test_both_supported_kdfs_are_accepted(kdf):
    assert check(env(KDF=kdf)) == []


def test_a_memory_cost_argon2_itself_would_refuse_is_reported():
    # Argon2 requires 8 KiB per lane. Catching it here makes it a startup
    # configuration error rather than a traceback out of the C library on the
    # first login attempt.
    assert any("ARGON2_MEMORY_KIB" in p
               for p in check(env(ARGON2_MEMORY_KIB="16", ARGON2_PARALLELISM="4")))


def test_a_memory_cost_that_clears_the_per_lane_floor_is_accepted():
    assert check(env(ARGON2_MEMORY_KIB="32", ARGON2_PARALLELISM="4")) == []


def test_a_destination_channel_is_required():
    problems = check(env(DISCORD_USER_ID=None))
    assert any("DISCORD_CHANNEL_ID" in p for p in problems)


def test_channel_id_alone_is_enough():
    assert check(env(DISCORD_USER_ID=None, DISCORD_CHANNEL_ID="123")) == []


def test_non_numeric_port_is_reported_not_raised():
    # Previously an int() at import time, so a typo produced a bare traceback
    # before any of the other problems could be reported.
    assert any("SFTP_PORT" in p for p in check(env(SFTP_PORT="not-a-port")))


def test_out_of_range_port_is_rejected():
    assert any("SFTP_PORT" in p for p in check(env(SFTP_PORT="70000")))


def test_all_problems_are_reported_at_once():
    # One problem per restart would be a miserable way to configure a server.
    problems = check(env(DISCORD_BOT_TOKEN=None, SFTP_PASSWORD=None,
                         SFTP_PORT="70000"))
    assert len(problems) == 3


def test_the_error_message_lists_every_problem():
    with pytest.raises(ConfigError) as excinfo:
        validate(env(DISCORD_BOT_TOKEN=None, SFTP_USER=None))
    message = str(excinfo.value)
    assert "DISCORD_BOT_TOKEN" in message and "SFTP_USER" in message
