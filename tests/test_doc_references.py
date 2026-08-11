"""Every document that an instruction points at has to exist.

BUILD.md was deleted in 4ec83d3 and five files went on telling the reader to
see it: docker-compose.yml (as client/BUILD.md, where it never lived),
.env.example, requirements-dev.txt, discord-drive.spec, and
tests/test_db_indexes.py. Nothing failed, because nothing checks.

Repairing those five lines is not the repair. This project has met this shape
before with the compose variables -- twice, with the first fix's comment a few
lines above the second miss -- and what closed it was test_compose_coverage.py,
not the lines. So: the same again, for filenames.

Two rules, because a filename means different things in different files:

- In prose and config, any name ending in .md is a pointer. Those files have
  no reason to write one down except to send the reader there.
- In Python, only a backticked name counts. A name in a docstring is a
  pointer; the same name inside an assertion is usually the string some hook
  prints, and a rule that cannot tell those apart gets answered by deleting
  the docstring.

Narrative files are out of scope. ROADMAP.md, SOP.md, CLAUDE.md and
QUESTIONS.md name dead documents deliberately -- recording that a file was
deleted means writing its name -- so pointing this at them would either fail on
the history or teach people to stop keeping it. This docstring lives under the
same constraint, which is why the dead names above are bare.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Files that instruct rather than recount.
SCANNED = [
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "client/README.md",
    "docker-compose.yml",
    "Dockerfile",
    "discord-drive.spec",
    ".env.example",
    "requirements.txt",
    "requirements-dev.txt",
]
SCANNED_GLOBS = ["src/*.py", "tests/*.py", "scripts/*.py", "docs/*.md"]

# A bare name, optionally with directories in front of it. The leading guard
# keeps a path-qualified name in one piece instead of matching its tail.
BARE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.md)\b")
BACKTICKED = re.compile(r"`((?:[\w.-]+/)*[\w.-]+\.md)`")

# A .md inside a URL belongs to somebody else's repository.
URL = re.compile(r"https?://\S+")


def _scanned_files():
    found = [ROOT / name for name in SCANNED]
    for pattern in SCANNED_GLOBS:
        found.extend(sorted(ROOT.glob(pattern)))
    return [path for path in found if path.exists()]


def _references(path):
    text = URL.sub(" ", path.read_text(encoding="utf-8"))
    pattern = BACKTICKED if path.suffix == ".py" else BARE
    return {match.group(1) for match in pattern.finditer(text)}


@pytest.mark.skipif(
    not (ROOT / "docker-compose.yml").exists(),
    reason="not a source checkout -- the image ships src/ without the docs",
)
def test_every_referenced_document_exists():
    dangling = []
    for path in _scanned_files():
        for reference in _references(path):
            # As written from the repo root first, then relative to the file
            # that wrote it: client/README.md may say a bare name and mean one
            # of its own neighbours.
            if (ROOT / reference).exists() or (path.parent / reference).exists():
                continue
            dangling.append(f"{path.relative_to(ROOT).as_posix()} -> {reference}")

    assert not dangling, (
        "these files point at documents that do not exist:\n  "
        + "\n  ".join(sorted(dangling))
    )
