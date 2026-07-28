"""Startup validation.

`check()` is pure over the mapping it is given, so these probe combinations
directly rather than reimporting the module under a mutated environment.
"""

import pytest

from src.config import AES_KEY_BYTES, ConfigError, check, validate

VALID = {
    "DISCORD_BOT_TOKEN": "token",
    "DISCORD_USER_ID": "100000000000000000",
    "SFTP_USER": "user",
    "SFTP_PASSWORD": "password",
    "AES_SECRET_KEY": "x" * AES_KEY_BYTES,
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
    "name", ["DISCORD_BOT_TOKEN", "SFTP_USER", "SFTP_PASSWORD", "AES_SECRET_KEY"]
)
def test_each_secret_is_required(name):
    assert any(name in p for p in check(env(**{name: None})))


@pytest.mark.parametrize(
    "name", ["DISCORD_BOT_TOKEN", "SFTP_USER", "SFTP_PASSWORD", "AES_SECRET_KEY"]
)
def test_empty_counts_as_unset(name):
    # `FOO=` in a .env file is a typo, not a deliberate empty password.
    assert any(name in p for p in check(env(**{name: ""})))


def test_missing_aes_key_is_a_hard_failure():
    # The regression that matters most: this used to fall back to a public
    # constant, so the server ran and encrypted nothing.
    with pytest.raises(ConfigError, match="AES_SECRET_KEY"):
        validate(env(AES_SECRET_KEY=None))


def test_short_aes_key_is_rejected_not_padded():
    problems = check(env(AES_SECRET_KEY="tooshort"))
    assert any("AES_SECRET_KEY" in p and "8 bytes" in p for p in problems)


def test_multibyte_aes_key_is_measured_in_bytes():
    # 16 characters, 48 bytes once encoded -- long enough. Measuring
    # characters instead would have wrongly rejected it.
    assert check(env(AES_SECRET_KEY="金鑰" * 8)) == []


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
    problems = check(env(DISCORD_BOT_TOKEN=None, SFTP_PASSWORD=None, AES_SECRET_KEY=None))
    assert len(problems) == 3


def test_the_error_message_lists_every_problem():
    with pytest.raises(ConfigError) as excinfo:
        validate(env(DISCORD_BOT_TOKEN=None, SFTP_USER=None))
    message = str(excinfo.value)
    assert "DISCORD_BOT_TOKEN" in message and "SFTP_USER" in message
