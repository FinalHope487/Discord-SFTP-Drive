"""The PreCompact hook that salvages decisions before compaction discards them.

Driven as a subprocess with JSON on stdin, same as `test_push_guard.py` and for
the same reason: the process boundary is the thing under test.

The mirror image of the push guard on failure, though. That hook fails closed --
a push it cannot classify gets blocked. This one must fail *open*: a crash here
would stall compaction, and losing one decision line is cheaper than wedging a
session that has run out of context. Every malformed-input case below asserts
exit 0.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "extract-decisions.py"

LEDGER = "DECISIONS.jsonl"


def transcript(tmp_path, entries):
    """Write a .jsonl transcript in the shape Claude Code actually records."""
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    return path


def assistant(*blocks, ts="2026-08-09T12:00:00.000Z"):
    return {"type": "assistant", "timestamp": ts, "message": {"content": list(blocks)}}


def text(body):
    return {"type": "text", "text": body}


def thinking(body):
    return {"type": "thinking", "thinking": body}


def mislabelled(body):
    """A `thinking` block carrying its body where prose blocks carry theirs.

    Synthetic, and deliberately so. Real thinking blocks store text under
    `thinking`, which `harvest` never reads -- meaning the type check and the
    key lookup each exclude thinking on their own, and a test using the real
    shape stays green even after the type check is deleted. This shape is the
    only one that fails when the gate is actually removed.
    """
    return {"type": "thinking", "text": body}


def run_hook(tmp_path, transcript_path):
    return run_raw(tmp_path, json.dumps({"transcript_path": str(transcript_path)}))


def run_raw(tmp_path, payload):
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(tmp_path),
    )


def ledger(tmp_path):
    path = tmp_path / LEDGER
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def decisions(tmp_path):
    return [entry["decision"] for entry in ledger(tmp_path)]


def test_extracts_a_decision_from_assistant_text(tmp_path):
    t = transcript(tmp_path, [assistant(text("改用 asyncssh 的 gss_host=None，因為反向 DNS 每次要一秒"))])
    assert run_hook(tmp_path, t).returncode == 0
    assert decisions(tmp_path) == ["改用 asyncssh 的 gss_host=None，因為反向 DNS 每次要一秒"]


def test_records_the_message_timestamp_not_the_hook_run_time(tmp_path):
    """The decision happened when it was said, not when compaction fired."""
    t = transcript(tmp_path, [assistant(text("決定用 SQLite 當測試後端"), ts="2026-08-03T04:05:06.000Z")])
    run_hook(tmp_path, t)
    assert ledger(tmp_path)[0]["ts"] == "2026-08-03T04:05:06.000Z"


@pytest.mark.parametrize("block", [thinking, mislabelled])
def test_ignores_thinking_blocks(tmp_path, block):
    """Deliberation is not a decision.

    Thinking is where "改用 X ... 不對，還是 Y" lives. Harvesting it fills the
    ledger with positions that were abandoned three sentences later.
    """
    t = transcript(tmp_path, [assistant(block("也許改用 Mongo 的 changestream"))])
    run_hook(tmp_path, t)
    assert decisions(tmp_path) == []


@pytest.mark.parametrize(
    "line",
    [
        # Every one of these is a real false positive from this project's own
        # transcripts -- a bare keyword grep pulls in structure, not decisions.
        "| [README.md](README.md) | 把「沒有備份」改成像 GitHub |",
        "## 三個我需要你決定的問題",
        "**選項 3（由需求決定）：UI 要不要支援多個帳號？**",
    ],
)
def test_ignores_markdown_structure(tmp_path, line):
    t = transcript(tmp_path, [assistant(text(line))])
    run_hook(tmp_path, t)
    assert decisions(tmp_path) == []


@pytest.mark.parametrize(
    "line",
    [
        "要改用 Postgres 嗎？",
        # Colon means the sentence introduces a list; the content is below it.
        "先量清楚是 listen 還是 connect 慢，再決定怎麼改：",
    ],
)
def test_ignores_questions_and_lead_ins(tmp_path, line):
    """A question about a choice is the opposite of a decision."""
    t = transcript(tmp_path, [assistant(text(line))])
    run_hook(tmp_path, t)
    assert decisions(tmp_path) == []


@pytest.mark.parametrize(
    "line",
    [
        # Real harvest noise from this project's transcripts. Every one of these
        # is a decision *not yet made* -- the ledger's worst possible content,
        # because next session reads it as settled.
        "這是產品判斷，我沒有替你決定",
        "第三項的決策你決定好了告訴我，我再繼續 Electron 整合那段",
        "這個不是驗證能不能做，是三個方案要選哪個，動到金鑰處理所以我沒有替你決定",
        "分支 feat/standalone-app，未 commit（照專案慣例，提交由你決定）",
    ],
)
def test_ignores_decisions_handed_back_to_the_user(tmp_path, line):
    t = transcript(tmp_path, [assistant(text(line))])
    run_hook(tmp_path, t)
    assert decisions(tmp_path) == []


@pytest.mark.parametrize(
    "line",
    [
        # Also real, and these must survive: a decision states what was done.
        "改成隨機主金鑰＋密碼包裝，換密碼只重寫 32 bytes",
        "ROADMAP 上「把 server 改成 module-scoped」那個計畫不需要了——根因不在那裡",
        "已改成指向 `src/main.py` 的 `ensure_host_key()`，因為錯誤訊息本身就寫了遷移步驟",
    ],
)
def test_keeps_real_decisions(tmp_path, line):
    t = transcript(tmp_path, [assistant(text(line))])
    run_hook(tmp_path, t)
    assert decisions(tmp_path) == [line]


def test_dedupes_within_one_run(tmp_path):
    said = "放棄 polling，改成 changestream"
    t = transcript(tmp_path, [assistant(text(said)), assistant(text(said))])
    run_hook(tmp_path, t)
    assert decisions(tmp_path) == [said]


def test_dedupes_against_what_is_already_on_disk(tmp_path):
    """Compaction fires repeatedly on a growing transcript.

    The second run re-reads every line the first run already harvested. Without
    a check against the ledger, one long session writes the same decision five
    times and the file stops being readable.
    """
    t = transcript(tmp_path, [assistant(text("決定把 keystore 改成 per-user"))])
    run_hook(tmp_path, t)
    run_hook(tmp_path, t)
    assert decisions(tmp_path) == ["決定把 keystore 改成 per-user"]


def test_appends_rather_than_overwrites(tmp_path):
    first = transcript(tmp_path, [assistant(text("決定把 keystore 改成 per-user"))])
    run_hook(tmp_path, first)

    second = tmp_path / "later.jsonl"
    second.write_text(json.dumps(assistant(text("放棄 daemon-per-project 的做法"))), encoding="utf-8")
    run_hook(tmp_path, second)

    assert decisions(tmp_path) == [
        "決定把 keystore 改成 per-user",
        "放棄 daemon-per-project 的做法",
    ]


def test_caps_the_harvest_per_compaction(tmp_path):
    t = transcript(tmp_path, [assistant(text(f"決定採用方案 {i}")) for i in range(40)])
    run_hook(tmp_path, t)
    assert len(decisions(tmp_path)) == 20


def test_survives_non_ascii_round_trip(tmp_path):
    """cp950 is the console codepage here; the ledger has to stay UTF-8.

    Same trap `block-push-main.py` documents -- a ledger full of mojibake is a
    ledger nobody can read next session.
    """
    said = "改用《部分唯一索引》表達「欄位不存在」，因為 $exists: false 不被 partialFilterExpression 接受"
    t = transcript(tmp_path, [assistant(text(said))])
    run_hook(tmp_path, t)
    assert decisions(tmp_path) == [said]


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        "",
        json.dumps({}),
        json.dumps({"transcript_path": "no/such/file.jsonl"}),
        json.dumps({"transcript_path": None}),
    ],
)
def test_never_blocks_compaction_on_bad_input(tmp_path, payload):
    assert run_raw(tmp_path, payload).returncode == 0


def test_skips_malformed_transcript_lines_without_dropping_the_rest(tmp_path):
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        "\n".join(
            [
                "{ this line is truncated",
                json.dumps(assistant(text("決定用 jsonl 而不是 JSON array"))),
                "",
            ]
        ),
        encoding="utf-8",
    )
    result = run_hook(tmp_path, path)
    assert result.returncode == 0
    assert decisions(tmp_path) == ["決定用 jsonl 而不是 JSON array"]
