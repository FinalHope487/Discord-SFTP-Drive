"""Every setting `config.py` reads has to reach the container.

The bug this exists to prevent has now happened twice, in the same shape both
times. The image only `COPY`s `src/`, so there is no `.env` inside the
container for `load_dotenv()` to find, and any variable missing from the
`environment:` block in `docker-compose.yml` silently stays at its code
default. `DISCORD_MAX_CONCURRENCY` was the first (fixed 2026-08-01) and the
three `TRASH_*` settings were the second (fixed 2026-08-06) -- with the five
lines of commentary from the first fix still sitting a few lines above them in
the same file.

Neither was caught by anything, and neither could have been: the symptom is a
setting that has no effect and says nothing about it. Adding the missing lines
is a minute's work and does not stop it happening a third time, so this is the
part that does. It reads both files rather than trusting either.

Presence is what is asserted, not tunability. `SFTP_PORT=2222` and
`WEB_PORT=8080` are hardcoded on purpose -- the container's own ports are
fixed and the host mapping is what moves -- but they are *there*, which is the
property that keeps a code default from quietly winning.
"""

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "src" / "config.py"
COMPOSE = ROOT / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"

# This file compares the repository against itself, and the image contains only
# `src/`. Running the suite inside the built image is standard practice here --
# it is how the Python version that serves traffic gets exercised -- so without
# this every test below fails there on a missing file and buries the run.
#
# Skipped rather than made tolerant of a missing file: "the deployment
# descriptor is absent" must never read as "every setting is covered". A skip
# says the question was not asked; a pass would answer it wrongly. On the host,
# where both files exist, nothing is skipped.
pytestmark = pytest.mark.skipif(
    not (COMPOSE.exists() and ENV_EXAMPLE.exists()),
    reason="docker-compose.yml / .env.example are not in the image; this "
           "check belongs to the repository and runs on the host",
)

# Reading a setting goes through one of these. `_setting` is `os.getenv` plus
# the `_FILE` indirection for secrets; both take the variable name first.
_READERS = ("getenv", "_setting")

def _settings_config_reads():
    """Every environment variable name `src/config.py` looks up.

    Parsed rather than grepped: a regex over the source would also match the
    name inside an error message or a comment, and the point of this test is
    that it fails for exactly one reason.
    """
    tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        called = (function.attr if isinstance(function, ast.Attribute)
                  else getattr(function, "id", None))
        if called not in _READERS:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            names.add(first.value)
    return names


def _compose_environment():
    """The names assigned in the app service's `environment:` list.

    Deliberately not a YAML parse. The point is what a reader of this file
    sees, and a name appearing anywhere in that block as `NAME=` is exactly
    that -- while pulling in a YAML dependency to answer it would be a new
    package for one assertion.
    """
    text = COMPOSE.read_text(encoding="utf-8")
    block = text.split("environment:")[-1].split("\n    volumes:")[0]
    return set(re.findall(r"^\s*-\s*([A-Z][A-Z0-9_]*)=", block, re.MULTILINE))


def test_every_setting_config_reads_is_passed_into_the_container():
    wanted = _settings_config_reads()
    present = _compose_environment()

    # A secret arrives as `NAME_FILE` instead of `NAME`; either satisfies it.
    missing = sorted(name for name in wanted
                     if name not in present and f"{name}_FILE" not in present)

    assert not missing, (
        "docker-compose.yml does not pass these into the container, so "
        f"changing them in .env does nothing and reports nothing: {missing}"
    )


def test_the_scan_actually_found_the_settings():
    """Scaffolding check.

    If the parse above ever stops matching -- `config.py` renames `_setting`,
    or moves to a settings class -- it returns an empty set and the test above
    passes for the wrong reason, which is worse than not having it.
    """
    found = _settings_config_reads()

    assert len(found) > 20, found
    for name in ("TRASH_SWEEP_BATCH", "DISCORD_MAX_CONCURRENCY",
                 "SFTP_PASSWORD", "INCOMING_MAX_AGE_HOURS"):
        assert name in found, f"{name} is read by config.py but was not found"


def test_the_compose_scan_actually_found_the_block():
    """Same, for the other side of the comparison."""
    present = _compose_environment()

    assert len(present) > 15, present
    assert "SFTP_PASSWORD_FILE" in present, (
        "the secret indirection is what makes the _FILE fallback above real")


@pytest.mark.parametrize("name", sorted(_settings_config_reads()))
def test_every_setting_is_documented_in_env_example(name):
    """`.env.example` is where an operator finds out a setting exists.

    One missing line there has the same effect as a missing line in compose,
    reached from the other direction: the setting works, and nobody knows it is
    there. `WEB_HOST` and the container-fixed ports are the exception and say
    so in place.
    """
    assert re.search(rf"^#?\s*{re.escape(name)}=", ENV_EXAMPLE.read_text(
        encoding="utf-8"), re.MULTILINE), (
        f"{name} is read by config.py but never mentioned in .env.example")
