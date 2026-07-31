"""Environment-backed settings, validated at startup.

Every secret is required and has no default. The previous version fell back to
a hard-coded public AES key when `AES_SECRET_KEY` was unset, which meant an
operator who forgot one line in `.env` got a server that ran perfectly and
encrypted nothing. Silent insecure defaults are worse than a crash, so the
process now refuses to start instead.

Module-level constants are still plain values read at import time -- an unset
variable simply lands as `None`. Nothing dereferences them before
`validate()` runs (called first thing in `main.py`), and keeping import
side-effect-free means tests and tooling can import this module without a
fully populated environment.
"""

import os

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    """Raised when the environment cannot support a working server."""


DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_USER_ID = os.getenv("DISCORD_USER_ID")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "discord_sftp_vfs")

SFTP_USER = os.getenv("SFTP_USER")
SFTP_PASSWORD = os.getenv("SFTP_PASSWORD")

# Only needed while changing the password: it lets the server open the
# existing wrapped master key once and re-wrap it under the new one. There is
# no AES_SECRET_KEY any more -- the data key is random, lives in the database
# wrapped under the password, and never appears in the environment.
SFTP_PASSWORD_OLD = os.getenv("SFTP_PASSWORD_OLD")

SFTP_PORT = os.getenv("SFTP_PORT", "2222")
SFTP_HOST_KEY_PATH = os.getenv("SFTP_HOST_KEY_PATH", "host_key")

# Cost of turning the password into a key-encryption key. Left as a string
# for the same reason as SFTP_PORT; see `pbkdf2_iterations()`. Lowering it
# only affects keys wrapped from then on, because each stored record carries
# the parameters it was made with.
PBKDF2_ITERATIONS = os.getenv("PBKDF2_ITERATIONS", "600000")

MAX_CHUNK_SIZE = 9 * 1024 * 1024  # 9MB, under Discord's 10MiB attachment cap

# How many Discord requests may be in flight at once. Firing every chunk of a
# large upload in parallel exhausts the rate-limit allowance in one burst and
# then stalls, which is slower end to end than a steady rate.
#
# Left as a string here for the same reason as SFTP_PORT: parsing at import
# time turns a typo into a bare traceback raised before validate() has a
# chance to report it alongside every other problem.
DISCORD_MAX_CONCURRENCY = os.getenv("DISCORD_MAX_CONCURRENCY", "4")

# Decrypted chunks held per open file. Sequential reads cross a boundary
# constantly, so keeping the previous chunk avoids re-downloading it.
CHUNK_CACHE_SIZE = 2

# Unset or empty are the same failure: `FOO=` in a .env file is a typo, not a
# deliberate empty password.
_REQUIRED = ("DISCORD_BOT_TOKEN", "SFTP_USER", "SFTP_PASSWORD")

# The password is now the only thing standing between an attacker with the
# database and every file in it, so it gets a length floor that a login
# credential alone would not need.
MIN_PASSWORD_BYTES = 12


def check(env=None):
    """Return every problem with `env` as a list of human-readable strings.

    Pure over the mapping it is handed, so tests can probe combinations
    without reimporting this module or mutating the real environment.
    """
    env = os.environ if env is None else env
    problems = []

    for name in _REQUIRED:
        if not env.get(name):
            problems.append(f"{name} is not set")

    password = env.get("SFTP_PASSWORD")
    if password:
        size = len(password.encode("utf-8"))
        if size < MIN_PASSWORD_BYTES:
            problems.append(
                f"SFTP_PASSWORD is {size} bytes; at least {MIN_PASSWORD_BYTES} "
                "are required. It is no longer only a login credential -- it "
                "wraps the key that every stored file is encrypted with. "
                'Generate one with: python -c "import secrets; '
                'print(secrets.token_urlsafe(24))"'
            )

    # DM mode and channel mode are alternatives, but with neither there is
    # nowhere to put a chunk.
    if not env.get("DISCORD_USER_ID") and not env.get("DISCORD_CHANNEL_ID"):
        problems.append(
            "neither DISCORD_USER_ID nor DISCORD_CHANNEL_ID is set; "
            "one is required to know where to store chunks"
        )

    raw_port = env.get("SFTP_PORT")
    if raw_port:
        try:
            port = int(raw_port)
        except ValueError:
            problems.append(f"SFTP_PORT is not an integer: {raw_port!r}")
        else:
            if not 1 <= port <= 65535:
                problems.append(f"SFTP_PORT out of range: {port}")

    raw_iterations = env.get("PBKDF2_ITERATIONS")
    if raw_iterations:
        try:
            iterations = int(raw_iterations)
        except ValueError:
            problems.append(
                f"PBKDF2_ITERATIONS is not an integer: {raw_iterations!r}")
        else:
            if iterations < 1:
                problems.append(
                    f"PBKDF2_ITERATIONS must be at least 1: {iterations}")

    raw_concurrency = env.get("DISCORD_MAX_CONCURRENCY")
    if raw_concurrency:
        try:
            concurrency = int(raw_concurrency)
        except ValueError:
            problems.append(
                f"DISCORD_MAX_CONCURRENCY is not an integer: {raw_concurrency!r}")
        else:
            if concurrency < 1:
                problems.append(
                    f"DISCORD_MAX_CONCURRENCY must be at least 1: {concurrency}")

    return problems


def validate(env=None):
    """Raise `ConfigError` listing *every* problem, not just the first.

    Reporting them one per restart would be a miserable way to configure a
    server, so the message carries the full set.
    """
    problems = check(env)
    if problems:
        raise ConfigError(
            "Invalid configuration; refusing to start:\n"
            + "\n".join(f"  - {p}" for p in problems)
            + "\nSee .env.example for the full list of settings."
        )


def sftp_port():
    """The listening port as an int. Only safe once `validate()` has passed."""
    return int(SFTP_PORT)


def discord_max_concurrency():
    """In-flight Discord request cap. Safe once `validate()` has passed."""
    return int(DISCORD_MAX_CONCURRENCY)


def pbkdf2_iterations():
    """Key-derivation cost for newly wrapped keys. Safe after `validate()`."""
    return int(PBKDF2_ITERATIONS)
