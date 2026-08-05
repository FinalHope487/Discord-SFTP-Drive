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


# ------------------------------------------------------- secrets from files
#
# `FOO_FILE` exists so a docker secret can carry the password instead of an
# environment variable that `docker inspect` and `/proc/<pid>/environ` both
# hand out. The password wraps the master key, so that difference is the
# difference between reading the container's configuration and decrypting
# every stored file.


def secret(tmp_path, name, contents):
    """Write `contents` to a file and return its path, bytes written verbatim."""
    path = tmp_path / name
    path.write_bytes(contents)
    return str(path)


def test_a_secret_file_supplies_the_value(tmp_path):
    path = secret(tmp_path, "pw", b"x" * MIN_PASSWORD_BYTES)
    assert check(env(SFTP_PASSWORD=None, SFTP_PASSWORD_FILE=path)) == []


def test_one_trailing_newline_is_stripped(tmp_path):
    # `echo hunter2 > secret.txt` appends one, as does every editor that ends
    # files with a newline. Keeping it would make the ordinary way of writing
    # a secret produce a password nobody typed -- and since this password
    # derives the KEK, the failure would look like a wrong password.
    path = secret(tmp_path, "pw", b"x" * MIN_PASSWORD_BYTES + b"\n")
    assert check(env(SFTP_PASSWORD=None, SFTP_PASSWORD_FILE=path)) == []


def test_a_crlf_line_ending_is_stripped_whole(tmp_path):
    # A file written on Windows would otherwise keep a stray \r on the end.
    path = secret(tmp_path, "pw", b"x" * MIN_PASSWORD_BYTES + b"\r\n")
    assert check(env(SFTP_PASSWORD=None, SFTP_PASSWORD_FILE=path)) == []


def test_only_one_trailing_newline_is_stripped(tmp_path):
    # Two newlines mean the second one is part of the password. Stripping
    # greedily here would be a guess, and the file is the authority.
    path = secret(tmp_path, "pw", b"x" * (MIN_PASSWORD_BYTES - 1) + b"\n\n")
    # 11 x's + one surviving newline = 12 bytes, so the floor is met exactly.
    assert check(env(SFTP_PASSWORD=None, SFTP_PASSWORD_FILE=path)) == []


def test_trailing_spaces_are_not_stripped(tmp_path):
    # A password may legitimately end in a space. Stripping all whitespace
    # would silently mangle it into one that opens nothing.
    path = secret(tmp_path, "pw", b"x" * (MIN_PASSWORD_BYTES - 2) + b"  \n")
    assert check(env(SFTP_PASSWORD=None, SFTP_PASSWORD_FILE=path)) == []


def test_a_short_password_in_a_file_is_still_measured(tmp_path):
    # The length floor has to apply to the resolved value, or routing the
    # password through a file would quietly bypass it.
    path = secret(tmp_path, "pw", b"short\n")
    problems = check(env(SFTP_PASSWORD=None, SFTP_PASSWORD_FILE=path))
    assert any("SFTP_PASSWORD" in p and "5 bytes" in p for p in problems)


def test_a_missing_secret_file_is_reported_not_raised(tmp_path):
    path = str(tmp_path / "absent")
    problems = check(env(SFTP_PASSWORD=None, SFTP_PASSWORD_FILE=path))
    assert any("SFTP_PASSWORD_FILE" in p and "cannot be read" in p
               for p in problems)


def test_an_unreadable_secret_file_is_not_also_called_unset(tmp_path):
    # Two messages for one cause, and the second would point at the wrong fix.
    path = str(tmp_path / "absent")
    problems = check(env(SFTP_PASSWORD=None, SFTP_PASSWORD_FILE=path))
    assert not any(p == "SFTP_PASSWORD is not set" for p in problems)


def test_a_non_utf8_secret_file_is_reported(tmp_path):
    path = secret(tmp_path, "pw", b"\xff\xfe" * 8)
    problems = check(env(SFTP_PASSWORD=None, SFTP_PASSWORD_FILE=path))
    assert any("SFTP_PASSWORD_FILE" in p and "UTF-8" in p for p in problems)


def test_setting_both_the_variable_and_the_file_is_refused(tmp_path):
    # Resolving this by precedence either way would be a guess about which the
    # operator meant, and guessing wrong starts the server under the wrong
    # password.
    path = secret(tmp_path, "pw", b"y" * MIN_PASSWORD_BYTES)
    problems = check(env(SFTP_PASSWORD_FILE=path))
    assert any("both SFTP_PASSWORD and SFTP_PASSWORD_FILE" in p
               for p in problems)


def test_an_empty_secret_file_counts_as_unset(tmp_path):
    # Same rule as `FOO=` in a .env file: it is a typo, not a deliberate empty
    # password.
    path = secret(tmp_path, "pw", b"\n")
    problems = check(env(SFTP_PASSWORD=None, SFTP_PASSWORD_FILE=path))
    assert any("SFTP_PASSWORD is not set" in p for p in problems)


@pytest.mark.parametrize(
    "name", ["DISCORD_BOT_TOKEN", "MONGO_URI", "SFTP_PASSWORD_OLD"]
)
def test_the_other_secrets_are_file_backed_too(tmp_path, name):
    path = secret(tmp_path, name, b"value-from-a-file")
    overrides = {name: None, name + "_FILE": path}
    assert check(env(**overrides)) == []


def test_a_settings_variable_is_not_file_backed(tmp_path):
    # `_FILE` is for secrets. WEB_PORT_FILE is not a thing, and must not
    # silently become one -- it would be ignored, which is the shape of
    # configuration error this module exists to refuse.
    path = secret(tmp_path, "port", b"8080")
    assert any("WEB_PORT" in p
               for p in check(env(WEB_PORT="nonsense", WEB_PORT_FILE=path)))
