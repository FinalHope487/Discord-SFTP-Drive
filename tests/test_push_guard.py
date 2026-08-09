"""The PreToolUse hook that stops `git push` from landing on a protected branch.

Driven the way Claude Code actually drives it -- a subprocess, JSON on stdin,
the exit code as the whole answer -- because the process boundary *is* what is
being tested. Importing the module and calling `main()` would assert nothing
about whether the hook reads stdin correctly or exits with the code that makes
the harness refuse the tool call.

The branch cases each get a throwaway repo rather than this one: asserting
"blocks while on main" is impossible from a feature branch, and checking out
main in the working repo to test a guard against main is an obviously bad
trade.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "block-push-main.py"

ALLOW = 0
BLOCK = 2


def run_hook(command, cwd):
    return run_raw(json.dumps({"tool_input": {"command": command}}), cwd)


def run_raw(payload, cwd):
    # encoding= on both ends: the hook is fed UTF-8 by the harness regardless of
    # what the console codepage is, and the tests have to reproduce that.
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(cwd),
    )


@pytest.fixture
def repo_on(tmp_path):
    """A real git repo sitting on `branch`, with one commit so HEAD resolves."""

    def make(branch):
        git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", "-b", branch], cwd=tmp_path, check=True)
        (tmp_path / "f").write_text("x")
        subprocess.run([*git, "add", "f"], cwd=tmp_path, check=True)
        subprocess.run([*git, "commit", "-qm", "c"], cwd=tmp_path, check=True)
        return tmp_path

    return make


@pytest.mark.parametrize(
    "branch, command, expected",
    [
        # The refspec names a protected branch, whoever we happen to be on.
        ("feature", "git push origin main", BLOCK),
        ("feature", "git push origin HEAD:master", BLOCK),
        ("feature", "git push --force origin main", BLOCK),
        ("feature", "cd somewhere && git push origin master", BLOCK),
        # No refspec: the current branch is where the commits land.
        ("main", "git push", BLOCK),
        ("master", "git push", BLOCK),
        ("main", "git push --force-with-lease", BLOCK),
        # Everything else is ordinary work and must not be slowed down.
        ("feature", "git push", ALLOW),
        ("feature", "git push -u origin feature", ALLOW),
        ("feature", "git status", ALLOW),
        ("main", "git log --oneline -5", ALLOW),
        # `main` as a path, not a destination -- the refspec is still the branch.
        ("feature", "git push origin feature:feature", ALLOW),
    ],
)
def test_decides_by_destination_branch(repo_on, branch, command, expected):
    assert run_hook(command, repo_on(branch)).returncode == expected


def test_blocks_when_the_branch_cannot_be_read(tmp_path):
    """Fails closed: no repo means no way to know where a bare push would go."""
    assert run_hook("git push", tmp_path).returncode == BLOCK


def test_unparseable_payload_does_not_wedge_unrelated_commands(tmp_path):
    """This hook matches every Bash call, so it must not block what it cannot read.

    Regression: the first version called `json.load(sys.stdin)`, which decodes
    through the locale codec. One non-ASCII byte on a cp950 console raised
    JSONDecodeError, and failing closed on that refused every command in the
    session, not just the pushes.
    """
    assert run_raw("not json at all", tmp_path).returncode == ALLOW


def test_unparseable_payload_still_blocks_a_visible_push(tmp_path):
    assert run_raw("garbage git push origin main garbage", tmp_path).returncode == BLOCK


@pytest.mark.parametrize(
    "command, expected",
    [
        ("git commit -m '修好了索引' && git push origin main", BLOCK),
        ("echo '中文註解' && git status", ALLOW),
    ],
)
def test_handles_non_ascii_commands(repo_on, command, expected):
    """The payload is UTF-8; reading it as cp950 is what broke this before."""
    assert run_hook(command, repo_on("feature")).returncode == expected


def test_explains_itself_when_blocking(repo_on):
    """The message goes to the model, so it has to say what to do instead."""
    result = run_hook("git push origin main", repo_on("feature"))
    assert "BLOCKED" in result.stderr
    assert "QUESTIONS.md" in result.stderr
