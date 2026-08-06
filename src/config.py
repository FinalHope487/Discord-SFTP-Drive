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


# --------------------------------------------------------- secrets in files
#
# `FOO_FILE=/run/secrets/foo` supplies the value of `FOO` from a file. This is
# what lets a docker secret carry the password.
#
# An environment variable is readable by anything that can run `docker
# inspect` or open `/proc/<pid>/environ`, and for SFTP_PASSWORD that turns
# "can read the container's configuration" into "can decrypt every stored
# file" -- the password is not a login credential, it wraps the master key. A
# secret arrives instead as a file mounted at 0400 and never enters the
# process environment at all.
#
# Only secrets are listed. A file indirection for WEB_PORT would be a setting
# nobody wants and one more way for startup to fail.
_FILE_BACKED = (
    "DISCORD_BOT_TOKEN",
    "MONGO_URI",
    "SFTP_PASSWORD",
    "SFTP_PASSWORD_OLD",
)

_FILE_SUFFIX = "_FILE"


def _read_secret_file(path):
    """The file's contents as text, minus at most one trailing newline.

    Stripping exactly one newline -- and nothing else -- is the deliberate
    part. `echo hunter2 > secret.txt` appends one, as does every editor that
    ends files with a newline, so leaving it on would make the ordinary way of
    writing a secret produce a password nobody typed. Stripping all trailing
    whitespace instead would silently mangle a password that genuinely ends in
    a space.

    That asymmetry matters more here than it would for a login credential:
    this password derives the key-encryption key, so a value differing by one
    byte from the one the master key was wrapped under is indistinguishable at
    startup from an outright wrong password. The trade is that a password
    ending in a newline cannot be expressed this way; `.env.example` says so.
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    return raw.decode("utf-8")


def _resolve(env, name):
    """`(value, problem)` for `name`, following its `_FILE` variant if set.

    Having both set is refused rather than resolved by precedence. Either
    order would be a guess about which one the operator meant, and the cost of
    guessing wrong is a server that starts up under the wrong password.
    """
    direct = env.get(name)
    path = env.get(name + _FILE_SUFFIX)
    if not path:
        return direct, None
    if direct:
        return None, (
            f"both {name} and {name}{_FILE_SUFFIX} are set; use one or the "
            "other, since which of them wins would otherwise be a guess"
        )
    try:
        return _read_secret_file(path), None
    except OSError as exc:
        reason = exc.strerror or exc
        return None, f"{name}{_FILE_SUFFIX} is {path!r} but it cannot be read: {reason}"
    except UnicodeDecodeError:
        return None, (
            f"{name}{_FILE_SUFFIX} is {path!r} but its contents are not valid "
            "UTF-8"
        )


def _setting(name):
    """The import-time value of `name`, with any problem left for `check()`.

    Import stays non-fatal on purpose: an unreadable secret file must surface
    as one line in the startup validation report alongside every other
    problem, not as a traceback raised before `validate()` gets to run.
    """
    value, problem = _resolve(os.environ, name)
    return None if problem else value


DISCORD_BOT_TOKEN = _setting("DISCORD_BOT_TOKEN")
DISCORD_USER_ID = os.getenv("DISCORD_USER_ID")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")

# Which metadata store to use. `mongo` is the compose deployment and stays the
# default, so an existing `.env` keeps behaving exactly as it did.
#
# `sqlite` is the standalone build: one file, no server, no container. The two
# are not interchangeable for a given deployment -- there is no migration
# either way and the file formats have nothing in common -- so this chooses
# which drive you are opening, not merely how it is stored.
DB_BACKEND = os.getenv("DB_BACKEND", "mongo")

MONGO_URI = _setting("MONGO_URI") or "mongodb://127.0.0.1:27017"
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "discord_sftp_vfs")

# Where the standalone build keeps its metadata. Ignored under `mongo`.
#
# Losing this file loses the drive: the chunks on Discord are still there, but
# what says which chunks belong to which file, in what order, under what name
# -- and every integrity tag proving none of that was tampered with -- is only
# here. `SFTP_PASSWORD` and this file are both required, and neither one
# substitutes for the other.
SQLITE_PATH = os.getenv("SQLITE_PATH", "drive.sqlite3")

SFTP_USER = os.getenv("SFTP_USER")
SFTP_PASSWORD = _setting("SFTP_PASSWORD")

# Only needed while changing the password: it lets the server open the
# existing wrapped master key once and re-wrap it under the new one. There is
# no AES_SECRET_KEY any more -- the data key is random, lives in the database
# wrapped under the password, and never appears in the environment.
SFTP_PASSWORD_OLD = _setting("SFTP_PASSWORD_OLD")

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

# Where the built client lives. It is served by this same process, for the same
# reason the API is: a second service in front of the same MongoDB is the
# replica README.md forbids.
#
# Mounted rather than baked into the image, so rebuilding the frontend does not
# mean rebuilding the image and dropping every live session with it. A missing
# or empty directory is deliberately not a startup error -- the API and the
# SFTP surface do not depend on it, and refusing to boot over an unbuilt
# frontend would turn a cosmetic problem into an outage.
WEB_STATIC_DIR = os.getenv("WEB_STATIC_DIR", "web")

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

# How long an unfinished overwrite may sit before the same sweep collects it.
#
# Overwriting a file writes the new bytes into a node of its own and swaps it
# in at the end, so the old contents survive an upload that never finishes.
# What that leaves behind, when the process dies before it can unwind, is a
# node no directory points at, holding attachments nothing will reference
# again.
#
# Measured against `modified_at`, which every committed chunk moves, so this
# bounds the gap between two chunks rather than the length of a whole upload --
# a slow transfer of something enormous is never collected while it is making
# progress. Hours rather than minutes because being early destroys somebody's
# live upload and being late costs some Discord space.
INCOMING_MAX_AGE_HOURS = os.getenv("INCOMING_MAX_AGE_HOURS", "24")

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

BACKEND_MONGO = "mongo"
BACKEND_SQLITE = "sqlite"
_SUPPORTED_BACKENDS = {BACKEND_MONGO, BACKEND_SQLITE}

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
    # Zero would collect an overwrite that started this second, which is a
    # live upload destroyed under its own client.
    "INCOMING_MAX_AGE_HOURS": 1,
}


def check(env=None):
    """Return every problem with `env` as a list of human-readable strings.

    Pure over the mapping it is handed, so tests can probe combinations
    without reimporting this module or mutating the real environment.
    """
    env = os.environ if env is None else env
    problems = []

    # Resolve the file-backed secrets first: everything below wants their
    # effective values, not the raw variables. A mapping with no `_FILE` keys
    # -- which is every existing caller -- comes back through this unchanged.
    resolved = {}
    unresolved = set()
    for name in _FILE_BACKED:
        value, problem = _resolve(env, name)
        if problem:
            problems.append(problem)
            unresolved.add(name)
        resolved[name] = value

    def setting(name):
        return resolved[name] if name in resolved else env.get(name)

    for name in _REQUIRED:
        # A secret whose file could not be read has already been reported, and
        # saying "is not set" about it as well would point at the wrong fix.
        if name not in unresolved and not setting(name):
            problems.append(f"{name} is not set")

    password = setting("SFTP_PASSWORD")
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

    backend = env.get("DB_BACKEND")
    if backend and backend not in _SUPPORTED_BACKENDS:
        # Refused rather than defaulted. Falling back to `mongo` on a typo
        # would start the standalone build against a MongoDB that is not
        # running, and the error it eventually gave would name the connection,
        # not the misspelling that caused it.
        problems.append(
            f"DB_BACKEND is not a supported metadata store: {backend!r}. "
            f"Supported: {', '.join(sorted(_SUPPORTED_BACKENDS))}"
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


def web_static_dir():
    return WEB_STATIC_DIR.strip()


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
        # Carried here rather than in a settings function of its own because
        # the same sweep pass does both jobs on the same timer.
        "incoming_max_age": int(INCOMING_MAX_AGE_HOURS) * 3600,
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
