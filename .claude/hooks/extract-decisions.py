"""PreCompact hook: save the decisions before compaction throws away the reasons.

Compaction keeps the shape of the work and drops the *why*. CLAUDE.md only asks
for a write-up at the end of a round, so on a long round every trade-off made
before the first compaction is gone by the time anyone writes it down. This
harvests them into `DECISIONS.jsonl` while the transcript is still intact.

Opposite failure posture to `block-push-main.py`. That one fails closed. This
one fails open, always exit 0: a crash here stalls compaction on a session that
has already run out of context, and one lost decision line is far cheaper than
that.

jsonl, not a JSON array: appending is one write with no read-modify-write, so a
process killed mid-run costs the last line instead of the whole ledger.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

LEDGER = Path("DECISIONS.jsonl")

# Per compaction. A transcript long enough to compact holds far more matches
# than this, and the tail of that list is noise -- the cap keeps the ledger
# readable at session start, which is the only time it gets read.
MAX_PER_RUN = 20

# Chinese only, on purpose: this project's transcripts are Chinese prose, and
# English decision verbs ("chose", "dropped") mostly appear inside quoted code
# and library names here, where they mean nothing.
DECISION = re.compile(r"決定|拍板|改用|改成|改為|換成|沿用|放棄|捨棄|不採用|試過")

# Structure, not prose: table rows, headings, bullets, quotes, numbered items.
# A bare keyword grep over this project's own transcripts returns more of these
# than it does decisions -- see the parametrised cases in tests/test_decision_log.py.
STRUCTURE = re.compile(r"^([|#\->]|\d+\.)")

# Addressed to the user, so not a record of anything. Measured against 33 MB of
# this project's real transcripts: without this, two thirds of the harvest was
# 「需要你決定」/「要你拍板」/「等你決定」-- pending questions, which are the
# exact opposite of a decision. A decision says what was done and has no second
# person in it; the cost is dropping 「照你的決定改成 X」, and CLAUDE.md already
# says to add by hand what the hook misses.
ADDRESSED = re.compile(r"你")

MIN_LEN = 8
MAX_LEN = 200


def sentences(text):
    for chunk in re.split(r"[\n。]", text):
        # Strip markdown emphasis before judging: the question mark that
        # disqualifies a line is often inside `**...？**`.
        yield chunk.strip().strip("*").strip()


def is_decision(line):
    if not (MIN_LEN < len(line) < MAX_LEN):
        return False
    if STRUCTURE.search(line):
        return False
    if ADDRESSED.search(line):
        return False
    # A question about a choice is the opposite of a decision. A colon means the
    # sentence is introducing a list, so the content is in the next lines, not
    # this one.
    if line.endswith(("？", "?", "：", ":")):
        return False
    return bool(DECISION.search(line))


def harvest(transcript):
    """Yield (timestamp, decision) from assistant prose, oldest first.

    Only `text` blocks. Thinking blocks are where positions get taken and
    abandoned three sentences later; harvesting them fills the ledger with
    things that were never decided.
    """
    with transcript.open(encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                # A truncated line means a crashed write, not a broken file.
                continue
            if not isinstance(entry, dict) or entry.get("type") != "assistant":
                continue
            content = entry.get("message", {}).get("content")
            if not isinstance(content, list):
                continue
            # The message's own timestamp, not now(): the decision happened when
            # it was said, and compaction can fire hours later.
            ts = entry.get("timestamp") or datetime.now(timezone.utc).isoformat()
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                for line in sentences(block.get("text", "")):
                    if is_decision(line):
                        yield ts, line


def already_recorded():
    if not LEDGER.exists():
        return set()
    seen = set()
    for raw in LEDGER.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            seen.add(json.loads(raw)["decision"])
        except (ValueError, KeyError, TypeError):
            continue
    return seen


def main():
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        transcript_path = json.loads(raw).get("transcript_path")
    except (ValueError, AttributeError):
        return
    if not transcript_path:
        return

    transcript = Path(transcript_path)
    if not transcript.is_file():
        return

    # Compaction fires repeatedly against a transcript that keeps growing, so
    # every run re-reads what the previous run already took.
    seen = already_recorded()
    fresh = []
    for ts, decision in harvest(transcript):
        if decision in seen:
            continue
        seen.add(decision)
        fresh.append({"ts": ts, "decision": decision})
        if len(fresh) == MAX_PER_RUN:
            break

    if not fresh:
        return

    # Bytes, and ensure_ascii=False: the console here is cp950, and a ledger of
    # mojibake is a ledger the next session cannot read.
    with LEDGER.open("ab") as handle:
        for entry in fresh:
            handle.write((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # fail open, see module docstring
        sys.stderr.buffer.write(f"extract-decisions 略過本次：{exc!r}\n".encode("utf-8"))
    sys.exit(0)
