"""PreToolUse hook: refuse `git push` when it would land on a protected branch.

CLAUDE.md lists "push main" as high risk, but that was only a behavioural rule
-- nothing stopped it. This is the enforcement layer. Exit 2 blocks the call
and hands stderr back to the model.

Two ways a push reaches a protected branch, both checked here:
  - the refspec names it            git push origin main / origin HEAD:master
  - the current branch is it        git push (with no refspec)

Deliberately fails closed: any error below exits 2 rather than letting the
push through, because the failure mode of guessing wrong is unrecoverable.
"""

import json
import re
import subprocess
import sys

PROTECTED = {"main", "master"}


PUSH = re.compile(r"\bgit\s+push\b")


def block(reason):
    # Explicit UTF-8: this runs on a cp950 console, and a mojibake refusal is a
    # refusal nobody can act on.
    sys.stderr.buffer.write(
        (
            f"BLOCKED: {reason}\n"
            "CLAUDE.md 決策分級把 push main 列為高風險，必須由使用者逐項確認。\n"
            "改推功能分支，或把這件事寫進 QUESTIONS.md 的「待你執行／待你批准的動作」。\n"
        ).encode("utf-8")
    )
    sys.exit(2)


def main():
    # Decode the payload ourselves. `json.load(sys.stdin)` reads through the
    # locale codec, so on a Chinese Windows console any non-ASCII byte in the
    # command dies as a JSONDecodeError -- and this hook matches every Bash
    # call, so failing closed on that would wedge the whole session.
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")

    try:
        command = json.loads(raw).get("tool_input", {}).get("command", "")
    except (ValueError, AttributeError):
        # Unparseable, so we cannot know the destination. Only the pushes are
        # worth blocking on a guess; everything else goes through.
        if PUSH.search(raw):
            block("讀不懂 hook 收到的 payload，但裡面有 git push，保守起見擋下。")
        sys.exit(0)

    if not PUSH.search(command):
        sys.exit(0)

    # A refspec may be written as `src:dst`, `+src:dst`, or a bare branch name;
    # only the destination half decides where the commits actually land.
    for token in command.split():
        if token.startswith("-"):
            continue
        if token.split(":")[-1].lstrip("+") in PROTECTED:
            block(f"這個指令的目標分支是受保護的：{command}")

    # cwd, not a hardcoded path: hooks run from the project root, and reading
    # the branch from wherever we actually are keeps this testable against a
    # throwaway repo. If cwd is not a repo at all, git fails and we block.
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        block(f"讀不到當前分支，無法確認這次 push 的目標：{result.stderr.strip()}")

    branch = result.stdout.strip()
    if branch in PROTECTED:
        block(f"當前分支是 {branch}，這次 push 會直接推上它。")

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail closed, see module docstring
        block(f"hook 本身出錯：{exc!r}")
