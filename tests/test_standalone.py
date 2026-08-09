"""The entry point the packaged build starts.

What is worth pinning here is the part that has no equivalent in the compose
deployment: where this device's drive lives, what the first run does when
nothing has been configured yet, and the rule that an environment variable
beats the config file beats the built-in default. The server it eventually
starts is `main.start_server`, covered elsewhere except for the one thing
this module adds to it: an optional extra way to ask it to stop.

The password handling gets its own tests because the decision is the security
one: the config file and the database share a directory, so a password written
into that file would mean a copy of the folder is a copy of the drive. The
piped-password and stdin-shutdown paths exist for the desktop shell, which has
no console to hand this process and no reliable way on Windows to deliver an
actual signal to a console child it spawned -- measured directly, not assumed;
see the commentary in `standalone.py` and `main._wait_for_shutdown`.
"""

import asyncio
import io
import os

import pytest

from src import standalone


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A drive directory of our own, so nothing here touches a real profile."""
    directory = tmp_path / "drive-home"
    monkeypatch.setenv(standalone.HOME_VARIABLE, str(directory))
    return directory


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch):
    """None of the settings this module fills in may leak in from the shell."""
    for name in ("DB_BACKEND", "SQLITE_PATH", "SFTP_HOST_KEY_PATH",
                 "WEB_STATIC_DIR", "SFTP_PASSWORD", "SFTP_PASSWORD_FILE",
                 "DISCORD_BOT_TOKEN", "SFTP_USER", standalone.STDIN_LIFECYCLE_VARIABLE):
        monkeypatch.delenv(name, raising=False)


# ------------------------------------------------------------ the directory


def test_the_drive_directory_is_created(home):
    assert standalone.data_directory() == str(home)
    assert home.is_dir()


def test_the_drive_directory_is_per_user_not_beside_the_executable(
        tmp_path, monkeypatch):
    """A portable build runs from a USB stick or a downloads folder, and both
    are places where "the data is beside the program" means the data is
    deleted along with it."""
    profile = tmp_path / "profile"
    monkeypatch.delenv(standalone.HOME_VARIABLE, raising=False)
    monkeypatch.setenv("APPDATA", str(profile))
    monkeypatch.setenv("XDG_DATA_HOME", str(profile))
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(profile))

    directory = standalone.data_directory()

    assert str(profile) in directory
    assert os.path.dirname(os.path.abspath(standalone.__file__)) not in directory


# ------------------------------------------------------------- the first run


def test_the_first_run_writes_a_template_and_does_not_start(home, capsys):
    assert standalone.main() == 1

    config = home / standalone.CONFIG_FILENAME
    assert config.is_file()
    assert str(config) in capsys.readouterr().out


def test_the_first_run_prints_no_marker_for_a_plain_cli_user(home, capsys):
    """The AWAITING_PASSWORD marker is for the shell, which sets the lifecycle
    flag. A human running this from a terminal should see none of it -- only
    the settings-file message."""
    standalone.main()

    assert "AWAITING_PASSWORD" not in capsys.readouterr().out


def test_the_shell_gets_a_marker_before_the_blocking_read(home, monkeypatch, capsys):
    """What lets the shell tell "config missing, already exited" apart from
    "config present, now blocked waiting for the password" without racing a
    timeout against this process's own, variable startup time."""
    standalone.main()  # first run: writes the template
    (home / standalone.CONFIG_FILENAME).write_text("SFTP_USER=mine\n", encoding="utf-8")
    monkeypatch.setenv(standalone.STDIN_LIFECYCLE_VARIABLE, "1")
    monkeypatch.setattr(standalone.sys, "stdin", io.StringIO(""))  # EOF: refuse promptly

    standalone.main()

    assert "AWAITING_PASSWORD" in capsys.readouterr().out


def test_the_first_run_refuses_rather_than_defaulting_a_token(home):
    """A drive that started without a bot token would accept logins and then
    fail every upload, which reads as data loss rather than as a setting
    nobody filled in."""
    standalone.main()

    body = (home / standalone.CONFIG_FILENAME).read_text(encoding="utf-8")
    assert "DISCORD_BOT_TOKEN=\n" in body


def test_the_template_does_not_carry_a_password_line(home):
    """The decision this module exists to not get wrong.

    `SFTP_PASSWORD` wraps the key every stored file is encrypted with, and
    this file sits in the same directory as the database. A template with an
    `SFTP_PASSWORD=` line invites putting the lock and its key in one place,
    so there is no such line -- only an explanation of the three ways to
    supply it that do not write it down here.
    """
    standalone.main()

    body = (home / standalone.CONFIG_FILENAME).read_text(encoding="utf-8")
    assert "SFTP_PASSWORD=" not in body
    assert "SFTP_PASSWORD_FILE" in body


def test_a_second_run_does_not_overwrite_the_settings(home):
    standalone.main()
    config = home / standalone.CONFIG_FILENAME
    config.write_text("SFTP_USER=mine\n", encoding="utf-8")

    standalone.prepare_environment(str(home))

    assert config.read_text(encoding="utf-8") == "SFTP_USER=mine\n"


# ---------------------------------------------------------------- precedence


def test_the_standalone_defaults_are_filled_in(home):
    standalone.main()
    (home / standalone.CONFIG_FILENAME).write_text("SFTP_USER=mine\n",
                                                   encoding="utf-8")

    standalone.prepare_environment(str(home))

    assert os.environ["DB_BACKEND"] == "sqlite"
    assert os.environ["SQLITE_PATH"] == str(home / standalone.DATABASE_FILENAME)
    assert os.environ["SFTP_HOST_KEY_PATH"] == str(home / standalone.HOST_KEY_FILENAME)


def test_an_environment_variable_beats_the_config_file(home, monkeypatch):
    """What lets the desktop shell -- or a test -- drive this without writing
    into somebody's settings."""
    standalone.main()
    (home / standalone.CONFIG_FILENAME).write_text(
        "SFTP_USER=from-file\n", encoding="utf-8")
    monkeypatch.setenv("SFTP_USER", "from-environment")
    monkeypatch.setenv("SQLITE_PATH", "/somewhere/else.sqlite3")

    standalone.prepare_environment(str(home))

    assert os.environ["SFTP_USER"] == "from-environment"
    assert os.environ["SQLITE_PATH"] == "/somewhere/else.sqlite3"


# ------------------------------------------------------------- the password


def test_a_supplied_password_is_left_alone(monkeypatch):
    monkeypatch.setenv("SFTP_PASSWORD", "already-set-and-long-enough")

    assert standalone.resolve_password() is True
    assert os.environ["SFTP_PASSWORD"] == "already-set-and-long-enough"


def test_the_file_indirection_is_enough_on_its_own(monkeypatch):
    """An unattended start supplies it this way, and `config.py` resolves it.
    Prompting on top would break exactly the case the indirection exists for."""
    monkeypatch.setenv("SFTP_PASSWORD_FILE", "/run/secrets/whatever")

    assert standalone.resolve_password() is True
    assert "SFTP_PASSWORD" not in os.environ


def test_it_asks_on_a_console_rather_than_storing_anything(monkeypatch):
    monkeypatch.setattr(standalone.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(standalone.getpass, "getpass", lambda *a: "typed-in-password")

    assert standalone.resolve_password() is True
    assert os.environ["SFTP_PASSWORD"] == "typed-in-password"


def test_no_password_and_no_console_is_a_readable_refusal(monkeypatch, caplog):
    monkeypatch.setattr(standalone.sys.stdin, "isatty", lambda: False)

    assert standalone.resolve_password() is False
    assert "SFTP_PASSWORD" in caplog.text


def test_an_empty_password_is_not_accepted(monkeypatch):
    """`getpass` returning "" is someone pressing enter, not a password."""
    monkeypatch.setattr(standalone.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(standalone.getpass, "getpass", lambda *a: "")

    assert standalone.resolve_password() is False


# --------------------------------------------------- the desktop shell's path


def test_the_shell_pipes_the_password_as_one_line(monkeypatch):
    """No console, no getpass -- the shell already has its own dialog and
    just sends the password down as text. No echo to suppress, because
    nothing here is a terminal for anything to echo to."""
    monkeypatch.setenv(standalone.STDIN_LIFECYCLE_VARIABLE, "1")
    monkeypatch.setattr(standalone.sys, "stdin", io.StringIO("piped-password\n"))

    assert standalone.resolve_password() is True
    assert os.environ["SFTP_PASSWORD"] == "piped-password"


def test_a_piped_password_strips_only_the_line_ending(monkeypatch):
    """A trailing \\r\\n from a Windows-side writer must not survive into the
    password; nothing else about the line should be touched."""
    monkeypatch.setenv(standalone.STDIN_LIFECYCLE_VARIABLE, "1")
    monkeypatch.setattr(standalone.sys, "stdin", io.StringIO("pw with spaces \r\n"))

    standalone.resolve_password()

    assert os.environ["SFTP_PASSWORD"] == "pw with spaces "


def test_an_immediately_closed_pipe_is_not_a_password(monkeypatch):
    """The shell closing its end before ever writing is not "empty password",
    it is "never sent one" -- both refuse, but for a real deployment this is
    what a shell bug (or a user closing the dialog mid-flight in some other
    way) would look like."""
    monkeypatch.setenv(standalone.STDIN_LIFECYCLE_VARIABLE, "1")
    monkeypatch.setattr(standalone.sys, "stdin", io.StringIO(""))

    assert standalone.resolve_password() is False


def test_an_already_set_password_wins_over_the_pipe(monkeypatch):
    """SFTP_PASSWORD / SFTP_PASSWORD_FILE still take priority, exactly as they
    do for the console path -- the flag only changes how this asks, not
    whether it asks at all."""
    monkeypatch.setenv(standalone.STDIN_LIFECYCLE_VARIABLE, "1")
    monkeypatch.setenv("SFTP_PASSWORD", "already-set")
    monkeypatch.setattr(standalone.sys, "stdin", io.StringIO("should-not-be-read\n"))

    assert standalone.resolve_password() is True
    assert os.environ["SFTP_PASSWORD"] == "already-set"


async def test_the_stdin_watcher_resolves_on_eof(monkeypatch):
    """What `main._wait_for_shutdown` races against: the shell closing its
    end of the pipe, with nothing else written first."""
    monkeypatch.setattr(standalone.sys, "stdin", io.StringIO(""))

    await asyncio.wait_for(standalone._watch_stdin_for_shutdown(), timeout=2)


async def test_the_stdin_watcher_skips_the_password_line_already_consumed(monkeypatch):
    """`resolve_password` reads the first line; this reads *after* it, the
    same way the two calls are sequenced in `main()`. It must not treat
    leftover lines, or the line it already consumed, as the stop signal."""
    monkeypatch.setenv(standalone.STDIN_LIFECYCLE_VARIABLE, "1")
    monkeypatch.setattr(standalone.sys, "stdin", io.StringIO("the-password\n"))

    assert standalone.resolve_password() is True

    await asyncio.wait_for(standalone._watch_stdin_for_shutdown(), timeout=2)


async def test_the_stdin_watcher_does_not_resolve_while_the_pipe_stays_open(monkeypatch):
    """A shell that has not quit yet must not look like one that has. Modelled
    with a real pipe rather than StringIO, since StringIO has no notion of
    "more might still come" -- reading past its end is instant EOF, which
    would make this pass for the wrong reason."""
    read_fd, write_fd = os.pipe()
    try:
        monkeypatch.setattr(standalone.sys, "stdin", os.fdopen(read_fd))

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(standalone._watch_stdin_for_shutdown(), timeout=0.3)
    finally:
        os.close(write_fd)


# ------------------------------------------------------------ the bundle


def test_the_client_comes_from_the_bundle_when_frozen(monkeypatch):
    monkeypatch.setattr(standalone.sys, "_MEIPASS", r"C:\Temp\_MEI123",
                        raising=False)

    assert standalone.bundled_client() == os.path.join(r"C:\Temp\_MEI123", "web")


def test_the_client_comes_from_the_build_output_from_a_checkout(monkeypatch):
    monkeypatch.delattr(standalone.sys, "_MEIPASS", raising=False)

    assert standalone.bundled_client().endswith(os.path.join("client", "app", "dist"))


# ------------------------------------------------- the store it actually opens


async def test_the_settings_it_writes_open_a_working_sqlite_store(home, monkeypatch):
    """End to end through `Database.connect`, which is what startup calls.

    Everything above is about producing settings; this is the one that checks
    they produce a drive. The indexes are the part worth seeing: they are
    declared once in `db.py` for both backends, and this is where that
    declaration meets real DDL.
    """
    from src.db import Database

    standalone.main()
    (home / standalone.CONFIG_FILENAME).write_text("SFTP_USER=mine\n",
                                                   encoding="utf-8")
    standalone.prepare_environment(str(home))

    # `config.py` read the environment when it was imported, long before this
    # test set any of it, so the two values `db.py` holds are pointed at the
    # directory this test just prepared.
    monkeypatch.setattr("src.db.DB_BACKEND", "sqlite")
    monkeypatch.setattr("src.db.SQLITE_PATH", os.environ["SQLITE_PATH"])
    monkeypatch.setattr(Database, "db", None)
    monkeypatch.setattr(Database, "client", None)

    try:
        await Database.connect()
        assert (home / standalone.DATABASE_FILENAME).is_file()

        nodes = sorted((await Database.db.nodes.index_information()).keys())
        assert nodes == ["id_1", "parent_id_1_filename_1", "trashed_at_1"]
        assert "username_1" in await Database.db.users.index_information()
        assert "id_1" in await Database.db.keystore.index_information()
    finally:
        await Database.close()
