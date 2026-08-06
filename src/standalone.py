"""Run the drive without Docker: SQLite, one data directory, one process.

This is the entry point the packaged build starts. It does three things the
compose deployment gets from elsewhere, and then hands over to `main.py`
unchanged:

  * decides where this device's drive lives, since there is no volume mount to
    say so;
  * fills in the settings that are structural to this shape -- the metadata
    store is SQLite, the host key and database sit in that directory, the
    client is the copy inside the bundle -- before `src.config` reads the
    environment at import;
  * resolves the password without writing it anywhere.

Everything else still comes from the same `config.py`, is still validated by
the same `validate()`, and still reports every problem at once.

**The drive here is this device's.** There is no sync, no shared backend and
no migration from a compose deployment: the chunks would be readable from
either, but what says which chunks make up which file lives in the metadata
store, and these two have nothing in common. Pointing this at a Discord
channel an existing deployment uses does not import that drive -- it starts an
empty one alongside it.
"""

import getpass
import logging
import os
import sys

logger = logging.getLogger(__name__)

APP_DIRECTORY = "Discord Drive"
CONFIG_FILENAME = "drive.env"
DATABASE_FILENAME = "drive.sqlite3"
HOST_KEY_FILENAME = "host_key"

# Overrides where everything below is rooted. Exists for two reasons: running
# two drives on one machine (which is coherent -- they are separate drives,
# not two views of one), and letting a test drive this module without writing
# into the real profile directory.
HOME_VARIABLE = "DISCORD_DRIVE_HOME"


def data_directory():
    """Where this device keeps its drive, created if it is not there yet.

    Per-user rather than next to the executable. A portable build lives on a
    USB stick or in a downloads folder, and both are places where "the data is
    beside the program" means the data is deleted with it.
    """
    override = os.environ.get(HOME_VARIABLE)
    if override:
        directory = override
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        directory = os.path.join(base, APP_DIRECTORY)
    elif sys.platform == "darwin":
        directory = os.path.expanduser(
            f"~/Library/Application Support/{APP_DIRECTORY}")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        directory = os.path.join(base, "discord-drive")

    os.makedirs(directory, exist_ok=True)
    return directory


def bundled_client():
    """The file manager's built files, wherever this is running from.

    PyInstaller unpacks the bundle to a temporary directory and names it in
    `sys._MEIPASS`; from a source checkout the same files are the Vite build
    output. Returning a path that does not exist is fine and deliberate --
    `web.py` already serves a "the client is not built" page for exactly that,
    and the API and SFTP are unaffected by it.
    """
    root = getattr(sys, "_MEIPASS", None)
    if root:
        return os.path.join(root, "web")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "client", "app", "dist")


CONFIG_TEMPLATE = """\
# Discord Drive -- this device's settings.
#
# Every REQUIRED value has no default and the drive will not start without it.
# `.env.example` in the source repository documents all of them, and what
# getting each one wrong costs.

# --- REQUIRED --------------------------------------------------------------

# Discord Developer Portal -> your application -> Bot -> Reset Token.
DISCORD_BOT_TOKEN=

# Where chunks are stored. Set at least one; if both are set the DM wins.
DISCORD_USER_ID=
DISCORD_CHANNEL_ID=

# The account name you sign in with.
SFTP_USER=

# --- THE PASSWORD ----------------------------------------------------------
#
# NOT set here, on purpose. It is not only a login: it wraps the key every
# stored file is encrypted with, and this file sits in the same directory as
# the database. Writing it here would put the lock and its key in one place,
# so that a copy of this folder is a copy of the drive.
#
# Left unset, the drive asks for it on the console at startup and it never
# reaches the disk. To run it unattended instead, supply it as the
# SFTP_PASSWORD environment variable, or point SFTP_PASSWORD_FILE at a file
# you have restricted yourself.
#
# BACK IT UP SOMEWHERE ELSE. Losing it loses the files, not just the login --
# and so does losing {database}, which is the only record of which chunks
# make up which file.

# --- OPTIONAL --------------------------------------------------------------

# SFTP_PORT=2222
# WEB_PORT=8080
# WEB_ENABLED=1
"""


def write_config_template(path, database):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(CONFIG_TEMPLATE.format(database=database))


def prepare_environment(directory):
    """Load this device's settings, then fill in the standalone ones.

    Order matters twice. The user's file is read first so that anything in it
    wins over the defaults below, and every one of these is a `setdefault` so
    that a real environment variable wins over both -- which is what lets the
    desktop shell, or a test, drive this without editing anyone's config file.

    All of it has to happen before `src.config` is imported, because that
    module reads the environment once, at import.
    """
    from dotenv import load_dotenv

    config_path = os.path.join(directory, CONFIG_FILENAME)
    database = os.path.join(directory, DATABASE_FILENAME)

    if not os.path.exists(config_path):
        write_config_template(config_path, database)
        return config_path, False

    load_dotenv(config_path)

    os.environ.setdefault("DB_BACKEND", "sqlite")
    os.environ.setdefault("SQLITE_PATH", database)
    os.environ.setdefault("SFTP_HOST_KEY_PATH",
                          os.path.join(directory, HOST_KEY_FILENAME))
    os.environ.setdefault("WEB_STATIC_DIR", bundled_client())
    return config_path, True


def resolve_password():
    """Get the password into the environment without putting it on disk.

    Asked for on the console when it is not already supplied. That keeps the
    default path free of a stored credential: this build's database and its
    config file share a directory, and a password written beside them would
    mean copying the folder copies the drive.

    Supplying it through `SFTP_PASSWORD` or `SFTP_PASSWORD_FILE` still works
    and is what an unattended start uses -- `config.py` resolves both, and
    this does not interfere with either.
    """
    if os.environ.get("SFTP_PASSWORD") or os.environ.get("SFTP_PASSWORD_FILE"):
        return True

    if not sys.stdin or not sys.stdin.isatty():
        logger.error(
            "No password available and no console to ask on. Supply it as the "
            "SFTP_PASSWORD environment variable, or point SFTP_PASSWORD_FILE "
            "at a file containing it.")
        return False

    try:
        password = getpass.getpass("Drive password: ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if not password:
        logger.error("No password entered.")
        return False

    os.environ["SFTP_PASSWORD"] = password
    return True


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    directory = data_directory()
    config_path, existed = prepare_environment(directory)

    if not existed:
        # First run. Refusing to start with a freshly written template is the
        # point: there is nothing sensible to default a bot token to, and a
        # drive that started without one would accept logins and then fail
        # every upload, which reads as data loss rather than as a setting
        # nobody filled in.
        print(f"Wrote a settings file to:\n  {config_path}\n\n"
              "Fill in the REQUIRED values and start the drive again.")
        return 1

    if not resolve_password():
        return 1

    from src.config import ConfigError, validate

    try:
        validate()
    except ConfigError as exc:
        logger.error("%s", exc)
        print(f"\nThese are set in:\n  {config_path}")
        return 1

    import asyncio

    import asyncssh

    from src.main import start_server

    logger.info("Drive directory: %s", directory)
    try:
        asyncio.run(start_server())
    except KeyboardInterrupt:
        logger.info("Shutting down")
    except ConfigError as exc:
        logger.error("%s", exc)
        return 1
    except (OSError, asyncssh.Error) as exc:
        logger.error("Error starting server: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
