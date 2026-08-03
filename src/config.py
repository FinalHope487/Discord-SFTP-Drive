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

from src import crypto

load_dotenv()

# Anything true-ish an operator is likely to write. Everything else is false,
# including typos: an unrecognised value must not silently enable something
# this cautious.
_TRUTHY = {"1", "true", "yes", "on"}


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

# How the password becomes a key-encryption key. Changing any of this only
# affects keys wrapped from then on, because each stored record carries the
# function and parameters it was made with -- which is what lets the default
# move from PBKDF2 to Argon2id without stranding an existing deployment.
#
# Left as strings for the same reason as SFTP_PORT; see `kdf_settings()`.
KDF = os.getenv("KDF", crypto.DEFAULT_KDF)
PBKDF2_ITERATIONS = os.getenv("PBKDF2_ITERATIONS", str(crypto.DEFAULT_PBKDF2_ITERATIONS))
ARGON2_TIME_COST = os.getenv("ARGON2_TIME_COST", str(crypto.DEFAULT_ARGON2_TIME_COST))
ARGON2_MEMORY_KIB = os.getenv("ARGON2_MEMORY_KIB", str(crypto.DEFAULT_ARGON2_MEMORY_KIB))
ARGON2_PARALLELISM = os.getenv("ARGON2_PARALLELISM", str(crypto.DEFAULT_ARGON2_PARALLELISM))

# Whether startup may rewrite an existing wrapped-key record onto the
# configured KDF. Off by default and deliberately so: it is the only thing the
# server does to a record the deployment is already depending on, and getting
# it wrong makes every stored file unreadable rather than breaking one of them.
# The upgrade verifies the new record opens before storing it, but the
# operator still chooses when it happens.
KDF_UPGRADE = os.getenv("KDF_UPGRADE", "0")

# ------------------------------------------------------------------- web API
#
# The HTTP API runs in this process, beside the SFTP server, sharing one
# DiscordVFS-per-session and one `_node_versions`. That is not for convenience:
# a separate process against the same MongoDB is the second replica README.md
# forbids, and it would serve stale chunk layouts with no error and no log.

WEB_ENABLED = os.getenv("WEB_ENABLED", "1")

# Loopback by default, and the compose file does not publish the port. Reaching
# it from another machine means an SSH tunnel, which this deployment already
# has the credentials for. `.env.example` documents what to change for phones
# and what the server has to absorb in exchange.
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = os.getenv("WEB_PORT", "8080")

# How long a session may hold an unwrapped master key in memory.
#
# Both are *ceilings*. A client may ask for less at login and gets it; asking
# for more is clamped back to these. The distinction matters: a session
# lifetime is a security control, so letting the browser extend it would hand
# that control to whoever stole the cookie.
WEB_SESSION_IDLE_SECONDS = os.getenv("WEB_SESSION_IDLE_SECONDS", "600")
WEB_SESSION_ABSOLUTE_SECONDS = os.getenv("WEB_SESSION_ABSOLUTE_SECONDS", "7200")

# Argon2id runs twice per login at 64 MiB each. asyncssh caps its own
# concurrent connections; an HTTP endpoint has no such thing, so 100 parallel
# login attempts would be 6.4 GB of allocation from an attacker who never has
# to guess anything. This bounds it, and anything beyond the queue depth is
# refused with 503 rather than queued indefinitely.
WEB_LOGIN_CONCURRENCY = os.getenv("WEB_LOGIN_CONCURRENCY", "2")
WEB_LOGIN_QUEUE = os.getenv("WEB_LOGIN_QUEUE", "16")

# Cookies are marked Secure by default. Browsers treat http://localhost as a
# secure context, so this costs nothing on the default loopback binding; it
# only has to be turned off for a plaintext binding on a LAN, which is exactly
# the deployment that should have to make that choice explicitly.
WEB_COOKIE_SECURE = os.getenv("WEB_COOKIE_SECURE", "1")

# ----------------------------------------------------------------- the trash
#
# Deleting is two steps: `remove` marks a node, and a sweep destroys it once
# this long has passed. Until then the chunks are still on Discord and still
# counted against whatever storage the bot can see, which is the trade a trash
# bin is: recoverable mistakes in exchange for space you already gave up.

TRASH_RETENTION_DAYS = os.getenv("TRASH_RETENTION_DAYS", "30")

# How often the sweep runs, and how many trash items one pass may destroy.
#
# The batch cap is the important one. Purging means a Discord call per
# attachment, so a month of accumulated deletions arriving as one burst is the
# same rate-limit stampede that DISCORD_MAX_CONCURRENCY exists to prevent --
# except unattended, at whatever hour the retention happens to expire.
# Whatever the cap leaves behind is picked up by the next pass.
TRASH_SWEEP_SECONDS = os.getenv("TRASH_SWEEP_SECONDS", "900")
TRASH_SWEEP_BATCH = os.getenv("TRASH_SWEEP_BATCH", "25")

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

_SUPPORTED_KDFS = {crypto.KDF_ARGON2ID, crypto.KDF_PBKDF2_SHA256}

# Cost variables and the smallest value that is not simply nonsense. These are
# hard floors, not the recommended settings -- `keystore` warns separately when
# a valid-but-weak cost is configured, because "weaker than recommended" is the
# operator's call to make and "zero iterations" is not.
_KDF_COST_VARIABLES = {
    "PBKDF2_ITERATIONS": 1,
    "ARGON2_TIME_COST": 1,
    "ARGON2_MEMORY_KIB": 8,
    "ARGON2_PARALLELISM": 1,
}

# Same treatment for the web settings whose only sensible floor is 1. A zero
# session lifetime is a server nobody can log into; a zero login concurrency is
# a login endpoint that blocks for ever.
_WEB_INTEGER_FLOORS = {
    "WEB_SESSION_IDLE_SECONDS": 1,
    "WEB_SESSION_ABSOLUTE_SECONDS": 1,
    "WEB_LOGIN_CONCURRENCY": 1,
    "WEB_LOGIN_QUEUE": 1,
    # Retention alone may be zero -- "purge on the next sweep" is a coherent
    # setting. The other two may not: a zero interval is a sweep loop with no
    # sleep in it, and a zero batch is a sweep that never finishes anything
    # while still reporting work as pending on every pass.
    "TRASH_RETENTION_DAYS": 0,
    "TRASH_SWEEP_SECONDS": 1,
    "TRASH_SWEEP_BATCH": 1,
}


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

    kdf = env.get("KDF")
    if kdf and kdf not in _SUPPORTED_KDFS:
        problems.append(
            f"KDF is not a supported key derivation function: {kdf!r}. "
            f"Supported: {', '.join(sorted(_SUPPORTED_KDFS))}")

    for name, floor in _KDF_COST_VARIABLES.items():
        raw = env.get(name)
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            problems.append(f"{name} is not an integer: {raw!r}")
        else:
            if value < floor:
                problems.append(f"{name} must be at least {floor}: {value}")

    # Argon2 refuses a memory cost below 8 KiB per lane, and refusing it here
    # is a configuration error rather than a traceback out of the C library on
    # the first login attempt.
    try:
        lanes = int(env.get("ARGON2_PARALLELISM") or ARGON2_PARALLELISM)
        memory = int(env.get("ARGON2_MEMORY_KIB") or ARGON2_MEMORY_KIB)
    except ValueError:
        pass  # already reported above
    else:
        if memory < 8 * lanes:
            problems.append(
                f"ARGON2_MEMORY_KIB is {memory}, below the 8 KiB per lane that "
                f"Argon2 requires for ARGON2_PARALLELISM={lanes} "
                f"(at least {8 * lanes})")

    for name, floor in _WEB_INTEGER_FLOORS.items():
        raw = env.get(name)
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            problems.append(f"{name} is not an integer: {raw!r}")
        else:
            if value < floor:
                problems.append(f"{name} must be at least {floor}: {value}")

    raw_web_port = env.get("WEB_PORT")
    if raw_web_port:
        try:
            port = int(raw_web_port)
        except ValueError:
            problems.append(f"WEB_PORT is not an integer: {raw_web_port!r}")
        else:
            if not 1 <= port <= 65535:
                problems.append(f"WEB_PORT out of range: {port}")

    # An idle ceiling above the absolute one can never be reached, so it is a
    # setting that silently does nothing -- the shape of configuration error
    # this file exists to refuse.
    try:
        idle = int(env.get("WEB_SESSION_IDLE_SECONDS") or WEB_SESSION_IDLE_SECONDS)
        absolute = int(env.get("WEB_SESSION_ABSOLUTE_SECONDS")
                       or WEB_SESSION_ABSOLUTE_SECONDS)
    except ValueError:
        pass  # already reported above
    else:
        if idle > absolute:
            problems.append(
                f"WEB_SESSION_IDLE_SECONDS ({idle}) is above "
                f"WEB_SESSION_ABSOLUTE_SECONDS ({absolute}), so the idle "
                "timeout can never be reached and does nothing")

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


def kdf_settings():
    """How to derive a key-encryption key from now on. Safe after `validate()`.

    Both functions' costs are passed through whichever one is selected;
    `crypto.kdf_settings` keeps the ones that apply. That means an operator can
    leave both sets in `.env` and switch by changing `KDF` alone, rather than
    having to remember which variables become live.
    """
    return crypto.kdf_settings(
        KDF,
        iterations=int(PBKDF2_ITERATIONS),
        time_cost=int(ARGON2_TIME_COST),
        memory_kib=int(ARGON2_MEMORY_KIB),
        parallelism=int(ARGON2_PARALLELISM),
    )


def kdf_upgrade():
    """Whether startup may re-wrap the stored key onto the configured KDF."""
    return KDF_UPGRADE.strip().lower() in _TRUTHY


def web_enabled():
    return WEB_ENABLED.strip().lower() in _TRUTHY


def web_port():
    return int(WEB_PORT)


def web_cookie_secure():
    return WEB_COOKIE_SECURE.strip().lower() in _TRUTHY


def web_session_limits():
    """The ceilings on how long a session may hold a key. Ints, after validate()."""
    return {
        "idle": int(WEB_SESSION_IDLE_SECONDS),
        "absolute": int(WEB_SESSION_ABSOLUTE_SECONDS),
    }


def web_login_limits():
    return {
        "concurrency": int(WEB_LOGIN_CONCURRENCY),
        "queue": int(WEB_LOGIN_QUEUE),
    }


def trash_settings():
    """Retention in seconds, plus how the sweep paces itself.

    Retention is stored in days because that is how a person thinks about a
    trash bin, and handed out in seconds because that is what comparing
    against `trashed_at` needs. Zero days is allowed and means "purge on the
    next sweep" -- an odd thing to want, but a coherent one, and refusing it
    would only push somebody into setting it to a fraction and finding out it
    is parsed as an int.
    """
    return {
        "retention": int(TRASH_RETENTION_DAYS) * 24 * 3600,
        "interval": int(TRASH_SWEEP_SECONDS),
        "batch": int(TRASH_SWEEP_BATCH),
    }


def password_hash_settings():
    """Cost for the stored account password hash. Safe after `validate()`.

    Always Argon2id, whatever `KDF` says, and deliberately a separate function
    from `kdf_settings()` even though both read the same variables. They are
    different jobs: `derive_kek` produces a key, this produces a verifier, and
    the one thing they must not do is share a derivation. Reusing `derive_kek`
    for both would mean the value stored for checking a password and the key
    that decrypts everything came out of the same function on the same inputs.

    PBKDF2 is not offered here. The stored record's self-describing `kdf`
    field exists so an existing *wrapped key* keeps opening after the default
    moves; a password hash has no such legacy, so there is nothing to be
    compatible with. Argon2's own PHC string carries its parameters anyway,
    which is why raising these costs later needs no migration either.
    """
    return {
        "time_cost": int(ARGON2_TIME_COST),
        "memory_cost": int(ARGON2_MEMORY_KIB),
        "parallelism": int(ARGON2_PARALLELISM),
    }
