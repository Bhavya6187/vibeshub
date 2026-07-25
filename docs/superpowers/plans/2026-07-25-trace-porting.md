# Trace Porting (Claude Code <-> Codex) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upload a trace from Claude Code or Codex to vibeshub and download it as a resume-grade session for the other CLI, delivered by a `vibeshub import` command.

**Architecture:** Two new pure converter modules in the backend (Claude-shaped -> Codex rollout, Codex rollout -> resume-grade Claude session), a dual-auth `GET /api/traces/{short_id}/export/{target}` endpoint that short-circuits same-format exports to the stored native bytes, git metadata captured plugin-side at share time (redaction destroys in-blob SHAs), and a CLI import verb that downloads, places, re-ids on collision, and prints the resume command. Spec: `docs/superpowers/specs/2026-07-24-trace-porting-design.md`.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (backend), pytest, stdlib-only Python for the plugin CLI (urllib, no third-party deps), Codex CLI 0.137 and Claude Code for live verification.

## Global Constraints

- All commits go on branch `trace-porting-design`. Other sessions switch branches in this checkout; every commit command MUST verify the branch in the same shell command: `[ "$(git branch --show-current)" = "trace-porting-design" ] && git add ... && git commit ...`. If the guard fails, stop and re-run `git checkout trace-porting-design` first.
- Backend tests: `env/bin/pytest webapp/backend/tests/... -q` from the repo root (`env/`, not `.venv`). Plugin tests: `env/bin/pytest plugins/cli/tests/... -q`.
- `webapp/backend/app/codex_convert.py` is FROZEN (its `codex-rec-<n>` uuids anchor digest chapters). Never modify it. Importing its private helpers is allowed.
- No em-dashes ("—") in any user-facing string: CLI output, command .md docs, HTTP error details. Use commas, periods, or parentheses.
- Python 3.13: `uuid.uuid7` does not exist; use the `uuid7_from` helper defined in Task 2. Scripts must not depend on network access except where stated.
- Alembic: current head is `e1f8a2b9c073`; the new revision in Task 4 sets `down_revision` to it. If `cd webapp/backend && ../../env/bin/python -m alembic heads` prints a different head, use that instead.
- `cursor-plugin/` is GENERATED from `plugins/cli/` by `scripts/sync-cursor-plugin.py`; never edit `cursor-plugin/` by hand. Regenerate after plugin changes (Task 9).
- The plugin CLI must remain stdlib-only (match `plugins/cli/vibeshub_client/*` conventions: urllib, subprocess, asyncio.to_thread).

---

### Task 1: Spike, prove Codex resumes a fabricated rollout

**Files:**
- Create: `/private/tmp/claude-501/-Users-bhavya-git-vibeshub/a8c9c977-6e6b-4997-9e53-d6a77597dad2/scratchpad/spike_rollout.py` (throwaway, never committed)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: a written verdict recorded as a comment block at the top of Task 2's module (`claude_to_codex_rollout.py`), stating: (a) does `codex resume` accept the file, (b) are foreign function names (`Read`) tolerated, (c) is `turn_context` required, (d) is `base_instructions` required. Task 2's `_map_tool` fallback depends on (b).

- [ ] **Step 1: Write the spike script**

```python
#!/usr/bin/env python3
"""Fabricate a minimal Codex rollout and check `codex exec resume` accepts it.

Exercises exactly the risky properties: fabricated session_meta (no
base_instructions), minimal turn_context, a FOREIGN tool name (Read), an
exec_command pair, and zero reasoning items.
"""
import json
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

CODEX_HOME = Path.home() / ".codex"
SESSION_UUID = str(uuid.uuid4())
now = datetime.now()
ts = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
fname = f"rollout-{now.strftime('%Y-%m-%dT%H-%M-%S')}-{SESSION_UUID}.jsonl"
dest_dir = CODEX_HOME / "sessions" / now.strftime("%Y/%m/%d")
dest_dir.mkdir(parents=True, exist_ok=True)
dest = dest_dir / fname

def line(typ, payload):
    return json.dumps({"timestamp": ts, "type": typ, "payload": payload})

lines = [
    line("session_meta", {
        "id": SESSION_UUID, "timestamp": ts, "cwd": str(Path.cwd()),
        "originator": "vibeshub", "cli_version": "vibeshub-export",
        "source": "vibeshub",
    }),
    line("turn_context", {
        "cwd": str(Path.cwd()), "approval_policy": "on-request",
        "sandbox_policy": {"type": "workspace-write"},
    }),
    line("response_item", {"type": "message", "role": "user",
        "content": [{"type": "input_text", "text": "What is 2+2? Use the tools if needed."}]}),
    line("event_msg", {"type": "user_message",
        "message": "What is 2+2? Use the tools if needed."}),
    line("response_item", {"type": "function_call", "name": "Read",
        "arguments": json.dumps({"file_path": "/etc/hosts"}),
        "call_id": "call_spike_foreign_1"}),
    line("response_item", {"type": "function_call_output",
        "call_id": "call_spike_foreign_1", "output": "127.0.0.1 localhost"}),
    line("response_item", {"type": "function_call", "name": "exec_command",
        "arguments": json.dumps({"cmd": "echo 4"}),
        "call_id": "call_spike_exec_1"}),
    line("response_item", {"type": "function_call_output",
        "call_id": "call_spike_exec_1", "output": "4"}),
    line("response_item", {"type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": "2+2 is 4."}]}),
    line("event_msg", {"type": "agent_message", "message": "2+2 is 4."}),
]
dest.write_text("\n".join(lines) + "\n")
print(f"WROTE {dest}")
print(f"SESSION {SESSION_UUID}")

r = subprocess.run(
    ["codex", "exec", "resume", SESSION_UUID,
     "Reply with exactly the word: RESUMED"],
    capture_output=True, text=True, timeout=180,
)
print("--- stdout ---"); print(r.stdout[-3000:])
print("--- stderr ---"); print(r.stderr[-3000:])
print("EXIT", r.returncode)
print("VERDICT:", "ACCEPTED" if "RESUMED" in r.stdout else "CHECK OUTPUT")
```

- [ ] **Step 2: Run the spike**

Run: `python3 /private/tmp/claude-501/-Users-bhavya-git-vibeshub/a8c9c977-6e6b-4997-9e53-d6a77597dad2/scratchpad/spike_rollout.py`
Expected: `VERDICT: ACCEPTED` and an assistant reply containing "RESUMED". This proves resume works with foreign tool names, no reasoning, no base_instructions.

- [ ] **Step 3: If the verdict is not ACCEPTED, isolate the cause**

Re-run three variants by editing the script, one change at a time, and note which one flips the verdict:
1. Remove the `Read` function_call + its output (foreign-name tolerance).
2. Remove the `turn_context` line (turn_context required?).
3. Copy `base_instructions` from a real rollout's session_meta (`jq -c 'select(.type=="session_meta") | .payload.base_instructions' $(find ~/.codex/sessions -name "*.jsonl" | tail -1)`) into the fabricated session_meta.

Record the narrowest set of required properties. If foreign names are rejected, Task 2's `_map_tool` must instead render every non-Bash tool as `exec_command` with `{"cmd": "# vibeshub: <name> " + json.dumps(input)}` (a no-op shell comment carrying the history); note this in the verdict comment.

- [ ] **Step 4: Clean up and record the verdict**

Run: `rm <the WROTE path printed in step 2>`
Then write the four-point verdict into a comment; Task 2 embeds it at the top of `claude_to_codex_rollout.py`. Nothing is committed in this task.

---

### Task 2: `claude_to_codex_rollout.py` converter + golden tests

**Files:**
- Create: `webapp/backend/app/claude_to_codex_rollout.py`
- Create: `webapp/backend/tests/test_claude_to_codex_rollout.py`
- Create: `webapp/backend/tests/fixtures/claude_export/sample.jsonl` (copy of `tests/fixtures/sample-session.jsonl`)
- Create: `webapp/backend/tests/fixtures/claude_export/sample.golden.jsonl` (generated in step 5)

**Interfaces:**
- Consumes: Task 1's verdict (embedded as the module's header comment).
- Produces (Task 5 imports these):
  - `claude_to_codex_rollout(blob: bytes, *, session_uuid: str, git: dict | None = None) -> bytes`
  - `uuid7_from(ts_ms: int, seed: str) -> str`
  - `rollout_filename(session_uuid: str, first_ts: str) -> str` (returns `rollout-YYYY-MM-DDThh-mm-ss-<uuid>.jsonl` from a `2026-07-24T18:00:00.123Z`-style timestamp, used verbatim)
  - `rollout_filename_from_blob(blob: bytes) -> str | None` (for verbatim Codex exports: parses the first line's session_meta id + timestamp; None when unparseable)

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for app.claude_to_codex_rollout.

sample.golden.jsonl pins determinism the same way the codex_convert
goldens do: generated once from the converter, eyeballed, committed.
"""
import json
from pathlib import Path

from app.claude_to_codex_rollout import (
    claude_to_codex_rollout,
    rollout_filename,
    rollout_filename_from_blob,
    uuid7_from,
)

FIXTURES = Path(__file__).parent / "fixtures"
SESSION_UUID = "01912345-0000-7000-8000-000000000abc"


def _records(blob: bytes) -> list[dict]:
    return [json.loads(l) for l in blob.splitlines() if l.strip()]


def _out() -> list[dict]:
    raw = (FIXTURES / "claude_export" / "sample.jsonl").read_bytes()
    return _records(claude_to_codex_rollout(raw, session_uuid=SESSION_UUID))


def test_first_line_is_session_meta_with_given_uuid():
    first = _out()[0]
    assert first["type"] == "session_meta"
    assert first["payload"]["id"] == SESSION_UUID
    assert first["payload"]["originator"] == "vibeshub"


def test_user_text_appears_as_both_response_item_and_event_msg():
    recs = _out()
    users = [r for r in recs if r["type"] == "response_item"
             and r["payload"].get("type") == "message"
             and r["payload"].get("role") == "user"]
    echoes = [r for r in recs if r["type"] == "event_msg"
              and r["payload"].get("type") == "user_message"]
    assert users and len(users) == len(echoes)
    assert users[0]["payload"]["content"][0]["type"] == "input_text"


def test_bash_becomes_exec_command_and_others_stay_verbatim():
    calls = {r["payload"]["name"]: json.loads(r["payload"]["arguments"])
             for r in _out()
             if r["type"] == "response_item"
             and r["payload"].get("type") == "function_call"}
    # sample-session.jsonl contains a Bash tool_use and a Read tool_use.
    assert "cmd" in calls["exec_command"]
    assert "file_path" in calls["Read"]


def test_tool_results_pair_by_call_id():
    recs = _out()
    call_ids = {r["payload"]["call_id"] for r in recs
                if r["payload"].get("type") == "function_call"}
    out_ids = {r["payload"]["call_id"] for r in recs
               if r["payload"].get("type") == "function_call_output"}
    assert out_ids <= call_ids and out_ids


def test_no_thinking_or_reasoning_leaks():
    assert not any(r["payload"].get("type") == "reasoning" for r in _out())


def test_git_metadata_lands_in_session_meta():
    raw = (FIXTURES / "claude_export" / "sample.jsonl").read_bytes()
    git = {"branch": "main", "commit_hash": "a" * 40,
           "repository_url": "https://github.com/x/y.git"}
    recs = _records(claude_to_codex_rollout(
        raw, session_uuid=SESSION_UUID, git=git))
    assert recs[0]["payload"]["git"] == git


def test_uuid7_is_deterministic_and_v7_shaped():
    a = uuid7_from(1753380000000, "abc123")
    assert a == uuid7_from(1753380000000, "abc123")
    assert a != uuid7_from(1753380000000, "other")
    assert a[14] == "7"


def test_rollout_filename():
    assert rollout_filename(SESSION_UUID, "2026-07-24T18:00:00.123Z") == (
        f"rollout-2026-07-24T18-00-00-{SESSION_UUID}.jsonl"
    )


def test_rollout_filename_from_blob_roundtrip():
    raw = (FIXTURES / "claude_export" / "sample.jsonl").read_bytes()
    out = claude_to_codex_rollout(raw, session_uuid=SESSION_UUID)
    name = rollout_filename_from_blob(out)
    assert name is not None and SESSION_UUID in name
    assert rollout_filename_from_blob(b"not json\n") is None


def test_conversion_matches_golden():
    raw = (FIXTURES / "claude_export" / "sample.jsonl").read_bytes()
    golden = (FIXTURES / "claude_export" / "sample.golden.jsonl").read_bytes()
    assert _records(claude_to_codex_rollout(
        raw, session_uuid=SESSION_UUID)) == _records(golden)
```

Also copy the fixture: `mkdir -p webapp/backend/tests/fixtures/claude_export && cp webapp/backend/tests/fixtures/sample-session.jsonl webapp/backend/tests/fixtures/claude_export/sample.jsonl`. Inspect `sample.jsonl`; if it lacks a `Read` tool_use or a `Bash` tool_use, append minimal assistant/user record pairs to the fixture copy so both cases exist (keep the file tiny; hand-write records in the shape of the existing lines).

- [ ] **Step 2: Run tests to verify they fail**

Run: `env/bin/pytest webapp/backend/tests/test_claude_to_codex_rollout.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.claude_to_codex_rollout'`

- [ ] **Step 3: Write the implementation**

```python
"""Convert Claude-shaped JSONL to a resume-grade Codex rollout.

Spike verdict (2026-07-25, codex-cli 0.137): <PASTE TASK 1 VERDICT HERE:
accepted?, foreign names?, turn_context?, base_instructions?>

Pure: bytes in, bytes out, no I/O. The inverse-direction sibling is
codex_to_claude_session.py; the view-grade codex_convert.py is frozen
and unrelated to this module.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

_EPOCH_TS = "1970-01-01T00:00:00.000Z"


def uuid7_from(ts_ms: int, seed: str) -> str:
    """Deterministic UUIDv7: millisecond timestamp + sha256(seed) tail."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    b = bytearray(16)
    b[0:6] = int(ts_ms).to_bytes(6, "big")
    b[6:16] = digest[:10]
    b[6] = (b[6] & 0x0F) | 0x70
    b[8] = (b[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(b)))


def rollout_filename(session_uuid: str, first_ts: str) -> str:
    stamp = first_ts[:19].replace(":", "-") if len(first_ts) >= 19 else (
        "1970-01-01T00-00-00"
    )
    return f"rollout-{stamp}-{session_uuid}.jsonl"


def rollout_filename_from_blob(blob: bytes) -> str | None:
    nl = blob.find(b"\n")
    first = (blob if nl == -1 else blob[:nl]).strip()
    try:
        rec = json.loads(first)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(rec, dict) or rec.get("type") != "session_meta":
        return None
    payload = rec.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
        return None
    ts = payload.get("timestamp") or rec.get("timestamp") or _EPOCH_TS
    return rollout_filename(payload["id"], str(ts))


def _stringify(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def _map_tool(name: str, inp: Any) -> tuple[str, dict]:
    inp = inp if isinstance(inp, dict) else {}
    if name == "Bash":
        return "exec_command", {"cmd": str(inp.get("command") or "")}
    # Spike-verified: foreign names ride along as history verbatim.
    return name, inp


def claude_to_codex_rollout(
    blob: bytes, *, session_uuid: str, git: dict | None = None,
) -> bytes:
    convo: list[dict] = []
    for raw in blob.decode("utf-8", errors="replace").split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(rec, dict)
            and rec.get("type") in ("user", "assistant")
            and not rec.get("isSidechain")
            and not rec.get("isMeta")
        ):
            convo.append(rec)

    first_ts = next(
        (r["timestamp"] for r in convo if r.get("timestamp")), _EPOCH_TS
    )
    cwd = next((r["cwd"] for r in convo if r.get("cwd")), "/")

    out: list[dict] = []

    def push(ts: str, typ: str, payload: dict) -> None:
        out.append({"timestamp": ts, "type": typ, "payload": payload})

    meta: dict = {
        "id": session_uuid, "timestamp": first_ts, "cwd": cwd,
        "originator": "vibeshub", "cli_version": "vibeshub-export",
        "source": "vibeshub",
    }
    if git:
        meta["git"] = git
    push(first_ts, "session_meta", meta)

    def push_user_text(ts: str, text: str) -> None:
        push(ts, "turn_context", {
            "cwd": cwd, "approval_policy": "on-request",
            "sandbox_policy": {"type": "workspace-write"},
        })
        push(ts, "response_item", {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": text}],
        })
        push(ts, "event_msg", {"type": "user_message", "message": text})

    for rec in convo:
        ts = rec.get("timestamp") or first_ts
        msg = rec.get("message") or {}
        content = msg.get("content")
        if rec["type"] == "user":
            if isinstance(content, str):
                if content.strip():
                    push_user_text(ts, content)
                continue
            if not isinstance(content, list):
                continue
            text_parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    push(ts, "response_item", {
                        "type": "function_call_output",
                        "call_id": str(block.get("tool_use_id") or ""),
                        "output": _stringify(block.get("content")),
                    })
                elif block.get("type") == "text":
                    text_parts.append(block.get("text") or "")
            text = "\n".join(p for p in text_parts if p).strip()
            if text:
                push_user_text(ts, text)
        else:  # assistant
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type")
                if bt == "text" and (block.get("text") or "").strip():
                    push(ts, "response_item", {
                        "type": "message", "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": block["text"]}
                        ],
                    })
                    push(ts, "event_msg", {
                        "type": "agent_message", "message": block["text"],
                    })
                elif bt == "tool_use":
                    name, args = _map_tool(
                        str(block.get("name") or ""), block.get("input")
                    )
                    push(ts, "response_item", {
                        "type": "function_call", "name": name,
                        "arguments": json.dumps(args, ensure_ascii=False),
                        "call_id": str(block.get("id") or ""),
                    })
                # thinking blocks are dropped by design (see spec).

    body = "\n".join(
        json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in out
    )
    return (body + "\n").encode("utf-8")
```

Paste the Task 1 verdict into the module docstring where marked. If the spike required the exec_command fallback for foreign names, replace the last two lines of `_map_tool` with:

```python
    return "exec_command", {
        "cmd": "# vibeshub tool: " + name + " " + json.dumps(inp),
    }
```

and update `test_bash_becomes_exec_command_and_others_stay_verbatim` to assert the comment form instead.

- [ ] **Step 4: Run tests (golden still missing)**

Run: `env/bin/pytest webapp/backend/tests/test_claude_to_codex_rollout.py -q`
Expected: all pass except `test_conversion_matches_golden` (FileNotFoundError).

- [ ] **Step 5: Generate the golden, eyeball it, re-run**

Run:
```bash
env/bin/python - <<'EOF'
from pathlib import Path
import sys
sys.path.insert(0, "webapp/backend")
from app.claude_to_codex_rollout import claude_to_codex_rollout
fx = Path("webapp/backend/tests/fixtures/claude_export")
out = claude_to_codex_rollout(
    (fx / "sample.jsonl").read_bytes(),
    session_uuid="01912345-0000-7000-8000-000000000abc",
)
(fx / "sample.golden.jsonl").write_bytes(out)
print(out.decode()[:1500])
EOF
```
Eyeball the printed head: first line session_meta, user turns have turn_context + response_item + event_msg. Then run: `env/bin/pytest webapp/backend/tests/test_claude_to_codex_rollout.py -q`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
[ "$(git branch --show-current)" = "trace-porting-design" ] && \
git add webapp/backend/app/claude_to_codex_rollout.py \
  webapp/backend/tests/test_claude_to_codex_rollout.py \
  webapp/backend/tests/fixtures/claude_export && \
git commit -m "feat: claude-to-codex rollout converter with golden tests"
```

---

### Task 3: `codex_to_claude_session.py` converter + tests

**Files:**
- Create: `webapp/backend/app/codex_to_claude_session.py`
- Create: `webapp/backend/tests/test_codex_to_claude_session.py`

**Interfaces:**
- Consumes: `_parse_exec_output` and `_s` from the frozen `app.codex_convert` (import only, never modify), fixtures `tests/fixtures/codex/*.jsonl`.
- Produces (Task 5 imports this): `codex_to_claude_session(blob: bytes, *, session_id: str) -> bytes`. Output records carry: `type` (user/assistant), deterministic `uuid`, `parentUuid` chain, `sessionId == session_id`, `timestamp`, `cwd`, `isSidechain: false`, `userType: "external"`, `gitBranch` when the rollout had one, and Anthropic-shaped `message`. No `version` field is fabricated; Task 10's live check validates that omission.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for app.codex_to_claude_session (resume-grade, distinct from the
frozen view-grade codex_convert)."""
import json
from pathlib import Path

from app.codex_to_claude_session import codex_to_claude_session

FIXTURES = Path(__file__).parent / "fixtures" / "codex"
SID = "b6a7c8d9-1111-4222-8333-444455556666"


def _records() -> list[dict]:
    raw = (FIXTURES / "kitchen_sink.jsonl").read_bytes()
    blob = codex_to_claude_session(raw, session_id=SID)
    return [json.loads(l) for l in blob.splitlines() if l.strip()]


def test_envelope_chain():
    recs = _records()
    assert recs, "no records produced"
    assert recs[0]["parentUuid"] is None
    for prev, cur in zip(recs, recs[1:]):
        assert cur["parentUuid"] == prev["uuid"]
    assert all(r["sessionId"] == SID for r in recs)
    assert all(r["isSidechain"] is False for r in recs)
    assert len({r["uuid"] for r in recs}) == len(recs)


def test_deterministic():
    raw = (FIXTURES / "kitchen_sink.jsonl").read_bytes()
    assert codex_to_claude_session(raw, session_id=SID) == \
        codex_to_claude_session(raw, session_id=SID)


def test_only_user_and_assistant_types():
    assert {r["type"] for r in _records()} <= {"user", "assistant"}


def test_no_reasoning_and_no_encrypted_content():
    blob = codex_to_claude_session(
        (FIXTURES / "kitchen_sink.jsonl").read_bytes(), session_id=SID)
    assert b"encrypted_content" not in blob
    assert b"thinking" not in blob


def test_exec_command_maps_to_bash_with_paired_result():
    recs = _records()
    uses = [b for r in recs if r["type"] == "assistant"
            for b in r["message"]["content"] if b["type"] == "tool_use"]
    bash = [u for u in uses if u["name"] == "Bash"]
    assert bash and "command" in bash[0]["input"]
    results = [b for r in recs if r["type"] == "user"
               for b in r["message"]["content"]
               if isinstance(b, dict) and b.get("type") == "tool_result"]
    result_ids = {b["tool_use_id"] for b in results}
    assert {u["id"] for u in uses} >= result_ids and result_ids


def test_user_prompts_come_from_event_msg_not_env_context():
    recs = _records()
    texts = [r["message"]["content"] for r in recs
             if r["type"] == "user"
             and isinstance(r["message"]["content"], str)]
    assert texts, "expected at least one typed user prompt"
    assert not any("environment_context" in t for t in texts)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env/bin/pytest webapp/backend/tests/test_codex_to_claude_session.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.codex_to_claude_session'`

- [ ] **Step 3: Write the implementation**

```python
"""Convert a Codex rollout to a resume-grade Claude Code session.

Unlike the frozen view-grade codex_convert (synthetic codex-rec-<n>
uuids, display tool names), this emits records shaped like real
~/.claude/projects session lines: uuid/parentUuid chain, sessionId,
cwd/gitBranch envelope, Anthropic message bodies. Encrypted reasoning
is dropped by design. Pure: bytes in, bytes out.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from app.codex_convert import _parse_exec_output, _s


def codex_to_claude_session(blob: bytes, *, session_id: str) -> bytes:
    records: list[dict] = []
    n = 0
    prev_uuid: str | None = None
    model: str | None = None
    cwd: str = "/"
    branch: str | None = None

    def envelope(
        rec_type: str, ts: str, message: dict, extra: dict | None = None,
    ) -> None:
        nonlocal n, prev_uuid
        u = str(uuid.uuid5(
            uuid.NAMESPACE_URL, f"vibeshub-import:{session_id}:{n}"
        ))
        rec: dict = {
            "type": rec_type, "uuid": u, "parentUuid": prev_uuid,
            "sessionId": session_id, "timestamp": ts, "cwd": cwd,
            "isSidechain": False, "userType": "external",
            "message": message,
        }
        if branch:
            rec["gitBranch"] = branch
        if extra:
            rec.update(extra)
        records.append(rec)
        prev_uuid = u
        n += 1

    def assistant_msg(blocks: list[dict]) -> dict:
        return {
            "id": f"msg-vibeshub-{n}", "type": "message",
            "role": "assistant", "model": model or "codex",
            "content": blocks, "stop_reason": None,
        }

    for raw in blob.decode("utf-8", errors="replace").split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        ts = _s(rec.get("timestamp"))
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            continue

        if rec.get("type") == "session_meta":
            if isinstance(payload.get("cwd"), str) and payload["cwd"]:
                cwd = payload["cwd"]
            git = payload.get("git")
            if isinstance(git, dict) and isinstance(git.get("branch"), str):
                branch = git["branch"]
            continue
        if rec.get("type") == "turn_context":
            if isinstance(payload.get("model"), str):
                model = payload["model"]
            continue
        if rec.get("type") == "event_msg":
            if (
                payload.get("type") == "user_message"
                and isinstance(payload.get("message"), str)
                and payload["message"]
            ):
                envelope("user", ts, {
                    "role": "user", "content": payload["message"],
                })
            continue
        if rec.get("type") != "response_item":
            continue

        pt = payload.get("type")
        if pt == "message" and payload.get("role") == "assistant":
            for part in payload.get("content") or []:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "output_text"
                    and _s(part.get("text"))
                ):
                    envelope("assistant", ts, assistant_msg(
                        [{"type": "text", "text": part["text"]}]
                    ))
        elif pt in ("function_call", "custom_tool_call"):
            call_id = _s(payload.get("call_id"))
            raw_name = _s(payload.get("name"))
            if pt == "custom_tool_call":
                name, inp = raw_name, {"input": _s(payload.get("input"))}
            else:
                try:
                    args = json.loads(_s(payload.get("arguments"), "{}"))
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                if raw_name == "exec_command":
                    name, inp = "Bash", {"command": _s(args.get("cmd"))}
                else:
                    name, inp = raw_name, args
            envelope("assistant", ts, assistant_msg([{
                "type": "tool_use", "id": call_id, "name": name,
                "input": inp,
            }]))
        elif pt in ("function_call_output", "custom_tool_call_output"):
            call_id = _s(payload.get("call_id"))
            body, exit_code = _parse_exec_output(_s(payload.get("output")))
            tool_use_result: dict[str, Any] = {"stdout": body}
            if exit_code is not None:
                tool_use_result["exitCode"] = exit_code
            envelope("user", ts, {
                "role": "user",
                "content": [{
                    "type": "tool_result", "tool_use_id": call_id,
                    "content": body,
                    "is_error": exit_code is not None and exit_code != 0,
                }],
            }, extra={"toolUseResult": tool_use_result})
        # reasoning items are dropped by design (encrypted, see spec).

    body = "\n".join(
        json.dumps(r, ensure_ascii=False, separators=(",", ":"))
        for r in records
    )
    return (body + "\n").encode("utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env/bin/pytest webapp/backend/tests/test_codex_to_claude_session.py webapp/backend/tests/test_codex_convert.py -q`
Expected: PASS (both files; the second proves the frozen module is untouched).

- [ ] **Step 5: Commit**

```bash
[ "$(git branch --show-current)" = "trace-porting-design" ] && \
git add webapp/backend/app/codex_to_claude_session.py \
  webapp/backend/tests/test_codex_to_claude_session.py && \
git commit -m "feat: resume-grade codex-to-claude session converter"
```

---

### Task 4: git columns migration + ingest headers

**Files:**
- Create: `webapp/backend/alembic/versions/f7c2d9e14a58_add_git_columns_to_traces.py`
- Modify: `webapp/backend/app/storage/models.py` (after `source_format`, ~line 77)
- Modify: `webapp/backend/app/api/trace_service.py:81-99` (signature) and the row create/update blocks (~line 177 onward)
- Modify: `webapp/backend/app/api/ingest.py` (new headers, pass-through)
- Test: `webapp/backend/tests/test_ingest.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `Trace.git_branch: str | None`, `Trace.git_commit: str | None`; `create_or_update_trace(..., git_branch: str | None = None, git_commit: str | None = None)`; ingest headers `X-Vibeshub-Git-Branch` / `X-Vibeshub-Git-Commit` (optional; commit must match `^[0-9a-f]{40}$` or it is stored as None). Task 5 reads the columns; Task 8 sends the headers.

- [ ] **Step 1: Write the failing test (append to `tests/test_ingest.py`)**

```python
def test_ingest_stores_git_headers(client, respx_mock, db_session):
    # Reuse this file's existing bundle/mock helpers and header dict; add:
    #   "X-Vibeshub-Git-Branch": "feature/x",
    #   "X-Vibeshub-Git-Commit": "b" * 40,
    # to the headers of an otherwise-normal successful ingest, then load the
    # Trace row (same query pattern as this file's other row assertions) and:
    assert trace.git_branch == "feature/x"
    assert trace.git_commit == "b" * 40


def test_ingest_rejects_malformed_git_commit_silently(client, respx_mock, db_session):
    # Same as above with "X-Vibeshub-Git-Commit": "not-a-sha". Ingest must
    # still return 201 and store git_commit None (bad metadata never fails
    # an upload).
    assert trace.git_commit is None
    assert trace.git_branch == "feature/x"
```

Adapt the two tests to this file's local helper names (it already has successful-ingest tests to copy the scaffolding from; keep assertions exactly as above).

- [ ] **Step 2: Run to verify failure**

Run: `env/bin/pytest webapp/backend/tests/test_ingest.py -q -k git`
Expected: FAIL with `AttributeError: ... git_branch` (or TypeError on unexpected kwarg).

- [ ] **Step 3: Implement**

models.py, directly after the `source_format` column:

```python
    # Captured plugin-side at share time (redaction destroys in-blob SHAs;
    # see the trace-porting spec). NULL for pre-0.6.0 plugins/web uploads.
    git_branch: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    git_commit: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
```

Migration file (full content):

```python
"""add git branch/commit columns to traces

Revision ID: f7c2d9e14a58
Revises: e1f8a2b9c073
Create Date: 2026-07-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7c2d9e14a58"
down_revision: Union[str, Sequence[str], None] = "e1f8a2b9c073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "traces", sa.Column("git_branch", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "traces", sa.Column("git_commit", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("traces", "git_commit")
    op.drop_column("traces", "git_branch")
```

trace_service.py: add `git_branch: str | None = None, git_commit: str | None = None,` to the `create_or_update_trace` keyword args; in the update branch set `trace.git_branch = git_branch` / `trace.git_commit = git_commit` alongside the other refreshed fields, and pass both in the `Trace(...)` constructor in the create branch.

ingest.py: add header params after `x_vibeshub_client_redactions`:

```python
    x_vibeshub_git_branch: Annotated[str | None, Header()] = None,
    x_vibeshub_git_commit: Annotated[str | None, Header()] = None,
```

before `create_or_update_trace`, normalize:

```python
    git_commit = (
        x_vibeshub_git_commit
        if x_vibeshub_git_commit
        and re.fullmatch(r"[0-9a-f]{40}", x_vibeshub_git_commit)
        else None
    )
    git_branch = (x_vibeshub_git_branch or None)
    if git_branch is not None:
        git_branch = git_branch[:255]
```

(add `import re` at top) and pass `git_branch=git_branch, git_commit=git_commit` into the call.

- [ ] **Step 4: Run tests**

Run: `env/bin/pytest webapp/backend/tests/test_ingest.py webapp/backend/tests/test_models.py webapp/backend/tests/test_migration_repo_pr_nullable.py -q`
Expected: PASS. (conftest creates the schema from models; the migration file is exercised by alembic-based tests if present, and by deploy.)

- [ ] **Step 5: Commit**

```bash
[ "$(git branch --show-current)" = "trace-porting-design" ] && \
git add webapp/backend/app/storage/models.py \
  webapp/backend/alembic/versions/f7c2d9e14a58_add_git_columns_to_traces.py \
  webapp/backend/app/api/trace_service.py webapp/backend/app/api/ingest.py \
  webapp/backend/tests/test_ingest.py && \
git commit -m "feat: capture git branch/commit at ingest via headers"
```

---

### Task 5: export endpoints with dual auth

**Files:**
- Modify: `webapp/backend/app/api/traces.py` (new helpers + endpoint after `get_trace_session`, ~line 489)
- Test: `webapp/backend/tests/test_export_endpoints.py` (create)

**Interfaces:**
- Consumes: Task 2 (`claude_to_codex_rollout`, `uuid7_from`, `rollout_filename`, `rollout_filename_from_blob`), Task 3 (`codex_to_claude_session`), Task 4 columns, existing `_claude_shaped`, `_require_trace_access`, `get_github`.
- Produces: `GET /api/traces/{short_id}/export/{target}` with `target in {"codex","claude"}`. Response headers Task 7's CLI reads: `Content-Disposition: attachment; filename="<name>"`, `X-Vibeshub-Filename`, `X-Vibeshub-Session-Uuid`, and optional `X-Vibeshub-Git-Branch`, `X-Vibeshub-Git-Commit`, `X-Vibeshub-Repo`. Errors: 404 unknown target/trace, 422 `{"detail": "cursor_to_claude_unsupported"}`, 401/403/404/502 access as in `_require_trace_access`.

- [ ] **Step 1: Write the failing tests**

```python
"""Export endpoint tests. Traces are seeded through /api/ingest with
respx-mocked GitHub, mirroring test_e2e.py's helpers."""
import json
from pathlib import Path

CODEX_FIXTURE = (
    Path(__file__).parent / "fixtures" / "codex" / "rollout.jsonl"
)
CLAUDE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "claude_export" / "sample.jsonl"
)

# Copy make_bundle and the standalone-ingest header helper pattern from
# test_e2e.py (Authorization "Bearer ghp_test" + respx user mock, no PR
# headers, X-Vibeshub-Platform per test). Local helper:


def _upload(client, respx_mock, blob: bytes, platform: str) -> str:
    respx_mock.get("https://api.github.test/user").respond(
        200, json={"login": "alice", "id": 7}
    )
    resp = client.post(
        "/api/ingest",
        content=make_bundle({"main.jsonl": blob}),
        headers={
            "X-Vibeshub-Platform": platform,
            "X-Vibeshub-Plugin-Version": "0.6.0",
            "X-Vibeshub-Client-Redactions": "0",
            "X-Vibeshub-Git-Branch": "main",
            "X-Vibeshub-Git-Commit": "c" * 40,
            "Content-Type": "application/x-tar",
            "Authorization": "Bearer ghp_test",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["short_id"]


def test_claude_native_to_codex_converts(client, respx_mock):
    sid = _upload(client, respx_mock, CLAUDE_FIXTURE.read_bytes(), "claude-code")
    r = client.get(f"/api/traces/{sid}/export/codex")
    assert r.status_code == 200
    first = json.loads(r.content.splitlines()[0])
    assert first["type"] == "session_meta"
    assert first["payload"]["git"]["commit_hash"] == "c" * 40
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["x-vibeshub-filename"].startswith("rollout-")
    assert r.headers["x-vibeshub-git-branch"] == "main"


def test_claude_native_to_claude_is_verbatim(client, respx_mock):
    blob = CLAUDE_FIXTURE.read_bytes()
    sid = _upload(client, respx_mock, blob, "claude-code")
    r = client.get(f"/api/traces/{sid}/export/claude")
    assert r.status_code == 200
    # Verbatim modulo server redaction: same line count, same first uuid.
    assert len(r.content.splitlines()) == len(blob.splitlines())
    assert r.headers["x-vibeshub-filename"].endswith(".jsonl")


def test_codex_native_to_codex_is_verbatim_with_original_filename(
    client, respx_mock,
):
    blob = CODEX_FIXTURE.read_bytes()
    sid = _upload(client, respx_mock, blob, "codex")
    r = client.get(f"/api/traces/{sid}/export/codex")
    assert r.status_code == 200
    orig_id = json.loads(blob.splitlines()[0])["payload"]["id"]
    assert orig_id in r.headers["x-vibeshub-filename"]


def test_codex_native_to_claude_is_resume_grade(client, respx_mock):
    sid = _upload(client, respx_mock, CODEX_FIXTURE.read_bytes(), "codex")
    r = client.get(f"/api/traces/{sid}/export/claude")
    assert r.status_code == 200
    recs = [json.loads(l) for l in r.content.splitlines()]
    session_uuid = r.headers["x-vibeshub-session-uuid"]
    assert all(rec["sessionId"] == session_uuid for rec in recs)
    assert recs[0]["parentUuid"] is None
    assert r.headers["x-vibeshub-filename"] == f"{session_uuid}.jsonl"


def test_unknown_target_404(client, respx_mock):
    sid = _upload(client, respx_mock, CLAUDE_FIXTURE.read_bytes(), "claude-code")
    assert client.get(f"/api/traces/{sid}/export/cursor").status_code == 404


def test_export_is_deterministic(client, respx_mock):
    sid = _upload(client, respx_mock, CLAUDE_FIXTURE.read_bytes(), "claude-code")
    a = client.get(f"/api/traces/{sid}/export/codex")
    b = client.get(f"/api/traces/{sid}/export/codex")
    assert a.content == b.content
    assert a.headers["x-vibeshub-filename"] == b.headers["x-vibeshub-filename"]
```

Also add a cursor-to-claude 422 test using an existing cursor fixture (`tests/fixtures/cursor/`, platform "cursor"), asserting `r.status_code == 422` and `r.json()["detail"] == "cursor_to_claude_unsupported"`, and a private-trace bearer test: PATCH is not needed, instead ingest with a private repo PR association following test_private_traces.py's existing mocking pattern, then assert anonymous export gives 401 and bearer export (Authorization + mocked `/user` + mocked `/repos/{repo}` 200) gives 200. Copy the exact mock scaffolding from `test_private_traces.py`, keep the assertions.

- [ ] **Step 2: Run to verify failure**

Run: `env/bin/pytest webapp/backend/tests/test_export_endpoints.py -q`
Expected: FAIL, `404` on every export call (route does not exist).

- [ ] **Step 3: Implement the endpoint (append to traces.py)**

New imports at top of traces.py:

```python
import uuid as uuid_mod

from app.claude_to_codex_rollout import (
    claude_to_codex_rollout,
    rollout_filename,
    rollout_filename_from_blob,
    uuid7_from,
)
from app.codex_to_claude_session import codex_to_claude_session
```

Helpers + route:

```python
def _git_meta(trace: Trace) -> dict | None:
    git: dict = {}
    if trace.git_branch:
        git["branch"] = trace.git_branch
    if trace.git_commit:
        git["commit_hash"] = trace.git_commit
    if trace.repo_full_name:
        git["repository_url"] = (
            f"https://github.com/{trace.repo_full_name}.git"
        )
    return git or None


def _export_claude_session_id(trace: Trace) -> str:
    try:
        return str(uuid_mod.UUID(trace.session_id or ""))
    except ValueError:
        return str(uuid_mod.uuid5(
            uuid_mod.NAMESPACE_URL,
            f"vibeshub-claude-export:{trace.short_id}",
        ))


async def _require_export_access(
    trace: Trace,
    *,
    user: User | None,
    authorization: str | None,
    github: GitHubClient,
    settings: Settings,
    access: RepoAccessChecker,
) -> None:
    """Cookie viewers use the normal gate; a GitHub bearer token (the
    plugin's `gh auth token`) is accepted as an alternative identity."""
    if not trace.is_private or user is not None:
        await _require_trace_access(trace, user, settings, access)
        return
    no_store = {"Cache-Control": "no-store"}
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401, detail="auth_required", headers=no_store
        )
    token = authorization.split(None, 1)[1].strip()
    try:
        gh_user = await github.verify_token(token)
    except GitHubAuthError:
        raise HTTPException(
            status_code=401, detail="auth_required", headers=no_store
        )
    if trace.repo_full_name is None:
        if trace.owner_login != gh_user.login:
            raise HTTPException(
                status_code=404, detail="not_found", headers=no_store
            )
        return
    pseudo_id = uuid_mod.uuid5(
        uuid_mod.NAMESPACE_URL, f"vibeshub-gh-user:{gh_user.id}"
    )
    try:
        allowed = await access.can_read(
            pseudo_id, token, trace.repo_full_name
        )
    except RepoAccessError:
        raise HTTPException(
            status_code=502, detail="github_upstream_error", headers=no_store
        )
    if not allowed:
        raise HTTPException(
            status_code=404, detail="not_found", headers=no_store
        )


@router.get("/api/traces/{short_id}/export/{target}")
async def export_trace(
    short_id: str,
    target: str,
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
    blob_store: BlobStore = Depends(get_blob_store),
    user: User | None = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    access: RepoAccessChecker = Depends(get_repo_access),
    github: GitHubClient = Depends(get_github),
):
    if target not in ("codex", "claude"):
        raise HTTPException(status_code=404, detail="not found")
    if not looks_like_short_id(short_id):
        raise HTTPException(status_code=404, detail="not found")
    stmt = select(Trace).where(
        Trace.short_id == short_id, Trace.deleted_at.is_(None)
    )
    trace = (await session.execute(stmt)).scalar_one_or_none()
    if trace is None:
        raise HTTPException(status_code=404, detail="not found")
    await _require_export_access(
        trace, user=user, authorization=authorization,
        github=github, settings=settings, access=access,
    )
    if trace.blob_prefix is None:
        raise HTTPException(
            status_code=500, detail="trace not migrated to v2 layout"
        )

    raw = await blob_store.get(f"{trace.blob_prefix}main.jsonl")
    src = trace.source_format  # None means already Claude-shaped
    session_uuid: str
    if target == "codex":
        if src == "codex":
            data = raw
            filename = rollout_filename_from_blob(raw)
            if filename is None:
                raise HTTPException(
                    status_code=422, detail="unconvertible_trace"
                )
            # The stem ends with the original rollout's 36-char session id.
            session_uuid = filename[:-len(".jsonl")][-36:]
        else:
            session_uuid = uuid7_from(
                int(trace.created_at.timestamp() * 1000), trace.short_id
            )
            claude_shaped = await _claude_shaped(
                blob_store,
                raw_key=f"{trace.blob_prefix}main.jsonl",
                converted_key=f"{trace.blob_prefix}converted.jsonl",
                source_format=src,
            )
            data = claude_to_codex_rollout(
                claude_shaped,
                session_uuid=session_uuid,
                git=_git_meta(trace),
            )
            filename = rollout_filename_from_blob(data)
            if filename is None:
                raise HTTPException(
                    status_code=422, detail="unconvertible_trace"
                )
    else:  # target == "claude"
        if src == "cursor":
            raise HTTPException(
                status_code=422, detail="cursor_to_claude_unsupported"
            )
        session_uuid = _export_claude_session_id(trace)
        if src == "codex":
            data = codex_to_claude_session(raw, session_id=session_uuid)
        else:
            data = raw
        filename = f"{session_uuid}.jsonl"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Vibeshub-Filename": filename,
        "X-Vibeshub-Session-Uuid": session_uuid,
    }
    if trace.git_branch:
        headers["X-Vibeshub-Git-Branch"] = trace.git_branch
    if trace.git_commit:
        headers["X-Vibeshub-Git-Commit"] = trace.git_commit
    if trace.repo_full_name:
        headers["X-Vibeshub-Repo"] = trace.repo_full_name
    if trace.is_private:
        headers["Cache-Control"] = "private, no-store"
    return Response(
        content=data, media_type="application/x-ndjson", headers=headers
    )
```

- [ ] **Step 4: Run tests**

Run: `env/bin/pytest webapp/backend/tests/test_export_endpoints.py webapp/backend/tests/test_private_traces.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
[ "$(git branch --show-current)" = "trace-porting-design" ] && \
git add webapp/backend/app/api/traces.py \
  webapp/backend/tests/test_export_endpoints.py && \
git commit -m "feat: dual-auth export endpoints for codex/claude targets"
```

---

### Task 6: plugin captures git branch/commit at share time

**Files:**
- Create: `plugins/cli/vibeshub_client/git_info.py`
- Modify: `plugins/cli/vibeshub_client/pipeline.py` (RunOptions + upload call site)
- Modify: `plugins/cli/vibeshub_client/upload.py:175-205` (params + headers)
- Test: `plugins/cli/tests/test_git_info.py` (create), `plugins/cli/tests/test_upload.py` (append), `plugins/cli/tests/test_pipeline.py` (append)

**Interfaces:**
- Consumes: Task 4's ingest headers (server side already accepts them).
- Produces: `git_info(cwd: str) -> tuple[str | None, str | None]` (branch, commit; both None outside a repo); `upload_bundle(..., git_branch: str | None = None, git_commit: str | None = None)` emitting `X-Vibeshub-Git-Branch` / `X-Vibeshub-Git-Commit` when set.

- [ ] **Step 1: Write the failing tests**

`tests/test_git_info.py`:

```python
import subprocess

from vibeshub_client.git_info import git_info


def _git(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin",
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
             "HOME": str(cwd)},
    )


def test_git_info_in_repo(tmp_path):
    _git(tmp_path, "init", "-b", "feature/x")
    (tmp_path / "f").write_text("x")
    _git(tmp_path, "add", "f")
    _git(tmp_path, "commit", "-m", "c")
    branch, commit = git_info(str(tmp_path))
    assert branch == "feature/x"
    assert commit and len(commit) == 40


def test_git_info_outside_repo(tmp_path):
    assert git_info(str(tmp_path)) == (None, None)
```

`tests/test_upload.py`, append (mirror this file's existing header-capture test scaffolding, which monkeypatches the request layer and records headers):

```python
def test_upload_sends_git_headers(...existing fixture args...):
    # call upload_bundle with git_branch="main", git_commit="d" * 40 and
    # assert on the recorded request headers:
    assert headers["X-Vibeshub-Git-Branch"] == "main"
    assert headers["X-Vibeshub-Git-Commit"] == "d" * 40
    # and a second call with both None asserts the headers are absent.
```

`tests/test_pipeline.py`, append a test that runs the share pipeline against this file's existing fake-upload scaffolding from a tmp git repo cwd and asserts the recorded upload received non-None git kwargs (copy the file's existing pipeline invocation pattern; the new assertion is the only novelty).

- [ ] **Step 2: Run to verify failure**

Run: `env/bin/pytest plugins/cli/tests/test_git_info.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'vibeshub_client.git_info'`

- [ ] **Step 3: Implement**

`git_info.py`:

```python
from __future__ import annotations

import re
import subprocess

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _run(cwd: str, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out or None


def git_info(cwd: str) -> tuple[str | None, str | None]:
    """(branch, commit) for `cwd`, both None when not a git repo. Captured
    client-side because server redaction destroys in-blob commit SHAs."""
    branch = _run(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    commit = _run(cwd, "rev-parse", "HEAD")
    if commit is not None and not _SHA_RE.match(commit):
        commit = None
    if branch == "HEAD":  # detached; branch name is meaningless
        branch = None
    return branch, commit
```

upload.py: add `git_branch: str | None = None, git_commit: str | None = None,` params to `upload_bundle` and after the `session_id` header block:

```python
    if git_branch:
        headers["X-Vibeshub-Git-Branch"] = git_branch
    if git_commit:
        headers["X-Vibeshub-Git-Commit"] = git_commit
```

pipeline.py: in `run_share_pipeline`, after the reader resolves the session and before the upload call, compute `branch, commit = git_info(hook_input.get("cwd") or os.getcwd())` (import `git_info`, guard with try/except returning (None, None) on any surprise) and pass `git_branch=branch, git_commit=commit` through to `upload_bundle`. Follow the file's existing structure for where options flow into the upload call.

- [ ] **Step 4: Run tests**

Run: `env/bin/pytest plugins/cli/tests -q`
Expected: PASS (all 156+ tests).

- [ ] **Step 5: Commit**

```bash
[ "$(git branch --show-current)" = "trace-porting-design" ] && \
git add plugins/cli/vibeshub_client/git_info.py \
  plugins/cli/vibeshub_client/upload.py plugins/cli/vibeshub_client/pipeline.py \
  plugins/cli/tests/test_git_info.py plugins/cli/tests/test_upload.py \
  plugins/cli/tests/test_pipeline.py && \
git commit -m "feat: plugin captures git branch/commit at share time"
```

---

### Task 7: import module, download + place + re-id + resume hint

**Files:**
- Create: `plugins/cli/vibeshub_client/import_trace.py`
- Test: `plugins/cli/tests/test_import_trace.py`

**Interfaces:**
- Consumes: Task 5's endpoint contract (headers `X-Vibeshub-Filename`, `X-Vibeshub-Session-Uuid`, `X-Vibeshub-Git-*`, `X-Vibeshub-Repo`), `gh_token.get_gh_token`.
- Produces (Task 8's command + tests use these):
  - `parse_trace_ref(arg: str, default_server: str) -> tuple[str, str]` (server_url, short_id; accepts `https://host/t/<id>` or bare short id)
  - `fetch_export(server_url: str, short_id: str, target: str) -> tuple[bytes, dict]` (anonymous first, bearer retry on 401; raises `ImportTraceError` with actionable message otherwise)
  - `codex_dest(codex_home: Path, filename: str) -> Path`, `claude_dest(claude_home: Path, cwd: str, session_uuid: str) -> Path`
  - `re_id_codex(blob: bytes, new_uuid: str) -> bytes`, `re_id_claude(blob: bytes, new_uuid: str) -> bytes`
  - `place(dest: Path, blob: bytes) -> Path` (atomic tmp+rename, never overwrites)
  - `run_import(ref: str, target: str, *, server: str, cwd: str, checkout: bool) -> int` (orchestrator returning exit code, prints outcome + resume command)

- [ ] **Step 1: Write the failing tests**

```python
import json
import uuid
from pathlib import Path

import pytest

from vibeshub_client.import_trace import (
    ImportTraceError,
    claude_dest,
    codex_dest,
    parse_trace_ref,
    place,
    re_id_claude,
    re_id_codex,
)


def test_parse_trace_ref_url_and_bare():
    assert parse_trace_ref(
        "https://vibeshub.ai/t/abc123", "https://vibeshub.ai"
    ) == ("https://vibeshub.ai", "abc123")
    assert parse_trace_ref("abc123", "https://vibeshub.ai") == (
        "https://vibeshub.ai", "abc123"
    )
    with pytest.raises(ImportTraceError):
        parse_trace_ref("https://vibeshub.ai/nope", "https://vibeshub.ai")


def test_codex_dest_uses_date_dirs():
    p = codex_dest(
        Path("/x/.codex"),
        "rollout-2026-07-24T18-00-00-0191a-b.jsonl",
    )
    assert str(p) == (
        "/x/.codex/sessions/2026/07/24/"
        "rollout-2026-07-24T18-00-00-0191a-b.jsonl"
    )


def test_claude_dest_encodes_cwd():
    p = claude_dest(Path("/x/.claude"), "/Users/y/repo", "abc-def")
    assert str(p) == "/x/.claude/projects/-Users-y-repo/abc-def.jsonl"


def test_re_id_codex_rewrites_session_meta_only():
    old, new = str(uuid.uuid4()), str(uuid.uuid4())
    blob = (
        json.dumps({"timestamp": "t", "type": "session_meta",
                    "payload": {"id": old}}) + "\n" +
        json.dumps({"timestamp": "t", "type": "response_item",
                    "payload": {"type": "message"}}) + "\n"
    ).encode()
    out = re_id_codex(blob, new)
    recs = [json.loads(l) for l in out.splitlines()]
    assert recs[0]["payload"]["id"] == new
    assert recs[1] == json.loads(blob.splitlines()[1])


def test_re_id_claude_rewrites_every_session_id():
    old, new = str(uuid.uuid4()), str(uuid.uuid4())
    blob = b"".join(
        json.dumps({"type": t, "sessionId": old, "uuid": f"u{i}"}).encode()
        + b"\n"
        for i, t in enumerate(["user", "assistant"])
    )
    recs = [json.loads(l) for l in re_id_claude(blob, new).splitlines()]
    assert all(r["sessionId"] == new for r in recs)


def test_place_never_overwrites(tmp_path):
    dest = tmp_path / "a.jsonl"
    assert place(dest, b"one") == dest
    with pytest.raises(FileExistsError):
        place(dest, b"two")
    assert dest.read_bytes() == b"one"
```

- [ ] **Step 2: Run to verify failure**

Run: `env/bin/pytest plugins/cli/tests/test_import_trace.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Download a vibeshub trace as a resume-grade session and place it where
the target CLI looks. Stdlib-only, mirroring upload.py conventions."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request

_ROLLOUT_RE = re.compile(
    r"^rollout-(\d{4})-(\d{2})-(\d{2})T\d{2}-\d{2}-\d{2}-.+\.jsonl$"
)


class ImportTraceError(Exception):
    pass


def parse_trace_ref(arg: str, default_server: str) -> tuple[str, str]:
    value = arg.strip().rstrip("/")
    if "://" in value:
        if "/t/" not in value:
            raise ImportTraceError(
                f"not a trace URL (expected .../t/<id>): {arg}"
            )
        server, short_id = value.rsplit("/t/", 1)
        if not short_id:
            raise ImportTraceError(f"empty trace id in URL: {arg}")
        return server, short_id
    if "/" in value or not value:
        raise ImportTraceError(f"not a trace URL or short id: {arg}")
    return default_server.rstrip("/"), value


def _get(url: str, token: str | None) -> tuple[int, bytes, dict]:
    headers = {"User-Agent": "vibeshub-plugin-import"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib_request.Request(url, headers=headers)
    try:
        with urllib_request.urlopen(req, timeout=60.0) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib_error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)
    except (urllib_error.URLError, TimeoutError, OSError) as e:
        raise ImportTraceError(f"network error: {e}") from e


def fetch_export(
    server_url: str, short_id: str, target: str,
) -> tuple[bytes, dict]:
    url = f"{server_url.rstrip('/')}/api/traces/{short_id}/export/{target}"
    status, body, headers = _get(url, token=None)
    if status == 401:
        from vibeshub_client.gh_token import GhTokenError, get_gh_token
        try:
            token = get_gh_token()
        except GhTokenError as e:
            raise ImportTraceError(
                f"trace is private and GitHub auth failed: {e}"
            ) from e
        status, body, headers = _get(url, token=token)
    if status == 422:
        detail = ""
        try:
            detail = json.loads(body).get("detail", "")
        except (json.JSONDecodeError, AttributeError):
            pass
        raise ImportTraceError(
            f"this trace cannot be exported to {target}: {detail}"
        )
    if status != 200:
        raise ImportTraceError(
            f"export failed: HTTP {status} "
            f"{body[:200].decode('utf-8', errors='replace')}"
        )
    return body, {k.lower(): v for k, v in headers.items()}


def codex_dest(codex_home: Path, filename: str) -> Path:
    m = _ROLLOUT_RE.match(filename)
    if not m:
        raise ImportTraceError(f"unexpected rollout filename: {filename}")
    y, mo, d = m.group(1), m.group(2), m.group(3)
    return codex_home / "sessions" / y / mo / d / filename


def claude_dest(claude_home: Path, cwd: str, session_uuid: str) -> Path:
    encoded = cwd.replace("/", "-")
    return claude_home / "projects" / encoded / f"{session_uuid}.jsonl"


def re_id_codex(blob: bytes, new_uuid: str) -> bytes:
    lines = blob.split(b"\n")
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            break
        if isinstance(rec, dict) and rec.get("type") == "session_meta":
            rec["payload"]["id"] = new_uuid
            lines[i] = json.dumps(
                rec, ensure_ascii=False, separators=(",", ":")
            ).encode()
        break  # session_meta is the first parseable line
    return b"\n".join(lines)


def re_id_claude(blob: bytes, new_uuid: str) -> bytes:
    out = []
    for line in blob.split(b"\n"):
        if not line.strip():
            out.append(line)
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue
        if isinstance(rec, dict) and "sessionId" in rec:
            rec["sessionId"] = new_uuid
            out.append(json.dumps(
                rec, ensure_ascii=False, separators=(",", ":")
            ).encode())
        else:
            out.append(line)
    return b"\n".join(out)


def place(dest: Path, blob: bytes) -> Path:
    if dest.exists():
        raise FileExistsError(str(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".vibeshub-tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, dest)
    return dest


def run_import(
    ref: str, target: str, *, server: str, cwd: str, checkout: bool,
) -> int:
    from vibeshub_client.repo_state import repo_state_report
    server_url, short_id = parse_trace_ref(ref, server)
    blob, headers = fetch_export(server_url, short_id, target)
    filename = headers.get("x-vibeshub-filename", "")
    session_uuid = headers.get("x-vibeshub-session-uuid", "")
    if not filename or not session_uuid:
        raise ImportTraceError(
            "server response is missing export headers; "
            "is the server up to date?"
        )

    if target == "codex":
        codex_home = Path(
            os.environ.get("CODEX_HOME", Path.home() / ".codex")
        )
        dest = codex_dest(codex_home, filename)
        if dest.exists():
            new_uuid = str(uuid.uuid4())
            blob = re_id_codex(blob, new_uuid)
            new_name = filename.replace(session_uuid, new_uuid)
            session_uuid, filename = new_uuid, new_name
            dest = codex_dest(codex_home, filename)
            print(f"session already imported; re-id as {new_uuid}")
        place(dest, blob)
        resume = f"codex resume {session_uuid}"
    else:
        claude_home = Path(
            os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")
        )
        dest = claude_dest(claude_home, cwd, session_uuid)
        if dest.exists():
            new_uuid = str(uuid.uuid4())
            blob = re_id_claude(blob, new_uuid)
            session_uuid = new_uuid
            dest = claude_dest(claude_home, cwd, session_uuid)
            print(f"session already imported; re-id as {new_uuid}")
        place(dest, blob)
        resume = f"claude --resume {session_uuid}"

    print(f"placed {dest}")
    for line in repo_state_report(headers, cwd, checkout=checkout):
        print(line)
    print(f"resume with: {resume}")
    return 0
```

(`repo_state_report` is Task 8; for this task create a placeholder module `plugins/cli/vibeshub_client/repo_state.py` containing exactly:

```python
from __future__ import annotations


def repo_state_report(
    headers: dict, cwd: str, *, checkout: bool = False,
) -> list[str]:
    return []
```

Task 8 replaces its body; the placeholder keeps Task 7 independently shippable.)

- [ ] **Step 4: Run tests**

Run: `env/bin/pytest plugins/cli/tests/test_import_trace.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
[ "$(git branch --show-current)" = "trace-porting-design" ] && \
git add plugins/cli/vibeshub_client/import_trace.py \
  plugins/cli/vibeshub_client/repo_state.py \
  plugins/cli/tests/test_import_trace.py && \
git commit -m "feat: import module downloads, places, re-ids trace exports"
```

---

### Task 8: repo-state check, --checkout, and the command wiring

**Files:**
- Modify: `plugins/cli/vibeshub_client/repo_state.py` (replace placeholder)
- Create: `plugins/cli/commands/import-trace.py`, `plugins/cli/commands/import-trace.md`
- Test: `plugins/cli/tests/test_repo_state.py` (create)

**Interfaces:**
- Consumes: Task 7 (`run_import` calls `repo_state_report(headers, cwd, checkout=...)`), Task 5's `X-Vibeshub-Repo` / `X-Vibeshub-Git-Branch` / `X-Vibeshub-Git-Commit` headers.
- Produces: `repo_state_report(headers: dict, cwd: str, *, checkout: bool = False) -> list[str]` returning printable advisory lines; performs `git fetch` + `git checkout <branch>` ONLY when `checkout=True`, the tree is clean, and the repo matches. The slash command `/vibeshub:import-trace <ref> --to codex|claude [--checkout]`.

- [ ] **Step 1: Write the failing tests**

```python
import subprocess

from vibeshub_client.repo_state import repo_state_report

SHA = "e" * 40


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "HOME": str(cwd)})


def _repo(tmp_path, branch="main"):
    _git(tmp_path, "init", "-b", branch)
    (tmp_path / "f").write_text("x")
    _git(tmp_path, "add", "f")
    _git(tmp_path, "commit", "-m", "c")
    return tmp_path


def test_no_git_headers_is_silent(tmp_path):
    assert repo_state_report({}, str(_repo(tmp_path))) == []


def test_wrong_repo_warns(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "remote", "add", "origin",
         "https://github.com/other/elsewhere.git")
    lines = repo_state_report(
        {"x-vibeshub-repo": "acme/widgets"}, str(repo))
    assert any("acme/widgets" in l for l in lines)


def test_missing_branch_suggests_fetch(tmp_path):
    lines = repo_state_report(
        {"x-vibeshub-git-branch": "feature/missing"}, str(_repo(tmp_path)))
    assert any("git fetch" in l for l in lines)


def test_matching_branch_and_commit_reports_ok(tmp_path):
    repo = _repo(tmp_path, branch="main")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
        text=True).stdout.strip()
    lines = repo_state_report(
        {"x-vibeshub-git-branch": "main", "x-vibeshub-git-commit": head},
        str(repo))
    assert any("matches" in l.lower() for l in lines)


def test_checkout_refuses_dirty_tree(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "branch", "feature/y")
    (repo / "f").write_text("dirty")
    lines = repo_state_report(
        {"x-vibeshub-git-branch": "feature/y"}, str(repo), checkout=True)
    assert any("not clean" in l for l in lines)
    current = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo,
        capture_output=True, text=True).stdout.strip()
    assert current == "main"


def test_checkout_switches_when_clean(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "branch", "feature/y")
    lines = repo_state_report(
        {"x-vibeshub-git-branch": "feature/y"}, str(repo), checkout=True)
    current = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo,
        capture_output=True, text=True).stdout.strip()
    assert current == "feature/y"
    assert any("switched" in l.lower() for l in lines)
```

- [ ] **Step 2: Run to verify failure**

Run: `env/bin/pytest plugins/cli/tests/test_repo_state.py -q`
Expected: FAIL (placeholder returns []).

- [ ] **Step 3: Implement `repo_state.py`**

```python
"""Advisory repo-state check for imported sessions. Never mutates the
working tree unless checkout=True AND the tree is clean AND the branch
exists; the recorded starting commit is provenance, never a checkout
target (see the trace-porting spec)."""
from __future__ import annotations

import subprocess


def _git(cwd: str, *args: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=30.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _repo_matches(cwd: str, repo_full_name: str) -> bool | None:
    url = _git(cwd, "remote", "get-url", "origin")
    if url is None:
        return None
    return repo_full_name.lower() in url.lower()


def repo_state_report(
    headers: dict, cwd: str, *, checkout: bool = False,
) -> list[str]:
    repo = headers.get("x-vibeshub-repo")
    branch = headers.get("x-vibeshub-git-branch")
    commit = headers.get("x-vibeshub-git-commit")
    if not (repo or branch or commit):
        return []
    lines: list[str] = []
    if _git(cwd, "rev-parse", "--git-dir") is None:
        lines.append(
            "note: current directory is not a git repo; "
            "skipped repo-state checks"
        )
        return lines
    if repo:
        match = _repo_matches(cwd, repo)
        if match is False:
            lines.append(
                f"warning: this trace belongs to {repo}, but the current "
                "repo has a different origin"
            )
        elif match is None:
            lines.append(
                f"note: could not read origin remote to compare with {repo}"
            )
    branch_exists = bool(branch) and (
        _git(cwd, "rev-parse", "--verify", "--quiet", branch) is not None
        or _git(
            cwd, "rev-parse", "--verify", "--quiet", f"origin/{branch}"
        ) is not None
    )
    if branch and not branch_exists:
        lines.append(
            f"note: session branch {branch} not found locally; try: "
            f"git fetch origin {branch} && git checkout {branch}"
        )
    commit_reachable = bool(commit) and _git(
        cwd, "cat-file", "-e", f"{commit}^{{commit}}"
    ) is not None
    if commit and not commit_reachable:
        lines.append(
            f"note: session start commit {commit[:12]} is not in this "
            "clone (unpushed or unfetched history)"
        )
    current = _git(cwd, "branch", "--show-current")
    if branch and branch_exists and current == branch and (
        not commit or commit_reachable
    ):
        lines.append(f"repo state matches the session (branch {branch})")

    if checkout and branch:
        if not branch_exists:
            lines.append(f"checkout skipped: branch {branch} not found")
            return lines
        status = _git(cwd, "status", "--porcelain")
        if status is None or status != "":
            lines.append(
                "checkout skipped: working tree is not clean, commit or "
                "stash first"
            )
            return lines
        if current == branch:
            lines.append(f"already on {branch}")
            return lines
        _git(cwd, "fetch", "origin", branch)
        if _git(cwd, "checkout", branch) is not None:
            lines.append(f"switched to branch {branch}")
        else:
            lines.append(
                f"checkout failed: run git checkout {branch} manually"
            )
    return lines
```

- [ ] **Step 4: Write the command pair**

`commands/import-trace.py` (mirrors share-trace.py's bootstrap):

```python
#!/usr/bin/env python3
"""
Import a vibeshub trace as a resumable session for another CLI.

Usage:
  import-trace <trace-url-or-short-id> --to codex|claude [--checkout]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(
    os.environ.get(
        "CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parent.parent
    )
)
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from vibeshub_client.import_trace import (  # noqa: E402
    ImportTraceError,
    run_import,
)


def main() -> None:
    parser = argparse.ArgumentParser(prog="import-trace")
    parser.add_argument("ref", help="trace URL or short id")
    parser.add_argument(
        "--to", required=True, choices=("codex", "claude"), dest="target"
    )
    parser.add_argument("--checkout", action="store_true")
    args = parser.parse_args()
    server = os.environ.get("VIBESHUB_SERVER_URL", "https://vibeshub.ai")
    try:
        sys.exit(run_import(
            args.ref, args.target, server=server, cwd=os.getcwd(),
            checkout=args.checkout,
        ))
    except (ImportTraceError, FileExistsError) as e:
        print(f"import failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

`commands/import-trace.md`:

```markdown
---
name: import-trace
description: Download a vibeshub trace as a resumable session for Codex or Claude Code.
argument-hint: "<trace-url-or-short-id> --to codex|claude [--checkout]"
---

Download a trace from vibeshub and place it where the target CLI keeps its
sessions, then print the exact resume command.

- `--to codex` writes a rollout under ~/.codex/sessions; resume with
  `codex resume <session-id>`.
- `--to claude` writes a session for the current project directory; resume
  with `claude --resume <session-id>`.
- `--checkout` additionally switches to the session's branch (latest pushed
  state) when the working tree is clean. Without it, the command only
  prints repo-state guidance and never touches your working tree.

Private traces authenticate with your `gh auth token`.

Run the helper script:

!python3 "${CLAUDE_PLUGIN_ROOT}/commands/import-trace.py" $ARGUMENTS
```

- [ ] **Step 5: Run all plugin tests**

Run: `env/bin/pytest plugins/cli/tests -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
[ "$(git branch --show-current)" = "trace-porting-design" ] && \
git add plugins/cli/vibeshub_client/repo_state.py \
  plugins/cli/commands/import-trace.py plugins/cli/commands/import-trace.md \
  plugins/cli/tests/test_repo_state.py && \
git commit -m "feat: import-trace command with repo-state check and opt-in checkout"
```

---

### Task 9: version bump, cursor plugin regeneration, contract docs

**Files:**
- Modify: `plugins/cli/vibeshub_client/version.py` (PLUGIN_VERSION -> "0.6.0"), plus every file `plugins/cli/tests/test_version_lockstep.py` checks (run it to get the list: plugin.json manifests, webapp package versions, FastAPI metadata).
- Modify: `plugins/README.md` (document the new ingest headers and the import verb under the existing contract sections)
- Regenerate: `cursor-plugin/` via `scripts/sync-cursor-plugin.py`

**Interfaces:**
- Consumes: everything shipped in Tasks 6-8.
- Produces: a consistent 0.6.0 across manifests; regenerated cursor plugin.

- [ ] **Step 1: Bump version and satisfy lockstep**

Set `PLUGIN_VERSION = "0.6.0"` in `plugins/cli/vibeshub_client/version.py`. Run `env/bin/pytest plugins/cli/tests/test_version_lockstep.py -q`; fix every mismatch it names (it asserts exact paths). Repeat until PASS.

- [ ] **Step 2: Document the contract**

In `plugins/README.md`, add to the ingest-headers table: `X-Vibeshub-Git-Branch` (optional, current branch at share time) and `X-Vibeshub-Git-Commit` (optional, 40-hex HEAD sha; the server drops malformed values). Add a short "Importing traces" section describing `import-trace <ref> --to codex|claude [--checkout]`, the export endpoint it calls, and that private traces use `gh auth token`. No em-dashes in the prose.

- [ ] **Step 3: Regenerate the cursor plugin**

Run: `python3 scripts/sync-cursor-plugin.py && git status --short cursor-plugin`
Expected: regenerated files only; no hand edits.

- [ ] **Step 4: Run both suites**

Run: `env/bin/pytest plugins/cli/tests -q && env/bin/pytest webapp/backend/tests -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
[ "$(git branch --show-current)" = "trace-porting-design" ] && \
git add -A plugins cursor-plugin webapp plugins/README.md && \
git commit -m "chore: bump to 0.6.0, regenerate cursor plugin, document import contract"
```

Note for the finishing session (not this task): per project convention the
generated plugin is also pushed to the `~/git/vibeshub-cursor` checkout so
both repos ship together.

---

### Task 10: end-to-end round trip + live resume verification

**Files:**
- Test: `webapp/backend/tests/test_export_roundtrip.py` (create)

**Interfaces:**
- Consumes: everything above.
- Produces: automated structural round-trip coverage plus a recorded live-resume checklist; any live failure loops back as a fix to Tasks 2/3.

- [ ] **Step 1: Write the round-trip test**

```python
"""Structural round trip: claude -> codex -> claude survives with the
conversation intact (fidelity contract: text turns and tool pairs, not
byte equality)."""
import json
from pathlib import Path

from app.claude_to_codex_rollout import claude_to_codex_rollout
from app.codex_to_claude_session import codex_to_claude_session

FIXTURES = Path(__file__).parent / "fixtures"


def _user_texts(records):
    # Joined per record: the claude->codex converter joins a user record's
    # text blocks into one input_text, so compare at that granularity.
    out = []
    for r in records:
        if r.get("type") != "user":
            continue
        c = (r.get("message") or {}).get("content")
        if isinstance(c, str):
            text = c
        elif isinstance(c, list):
            text = "\n".join(
                b.get("text", "") for b in c
                if isinstance(b, dict) and b.get("type") == "text"
                and b.get("text")
            )
        else:
            continue
        if text.strip():
            out.append(text)
    return out


def _assistant_texts(records):
    # Per block: each text block becomes its own output_text both ways.
    out = []
    for r in records:
        if r.get("type") != "assistant":
            continue
        c = (r.get("message") or {}).get("content")
        if isinstance(c, list):
            out.extend(
                b.get("text", "") for b in c
                if isinstance(b, dict) and b.get("type") == "text"
            )
    return [t for t in out if t.strip()]


def test_claude_codex_claude_roundtrip_preserves_conversation():
    src = (FIXTURES / "claude_export" / "sample.jsonl").read_bytes()
    rollout = claude_to_codex_rollout(
        src, session_uuid="01912345-0000-7000-8000-00000000cafe"
    )
    back = codex_to_claude_session(
        rollout, session_id="99912345-0000-4000-8000-00000000beef"
    )
    src_recs = [
        json.loads(l) for l in src.splitlines() if l.strip()
    ]
    back_recs = [json.loads(l) for l in back.splitlines() if l.strip()]
    src_convo = [
        r for r in src_recs
        if r.get("type") in ("user", "assistant")
        and not r.get("isSidechain") and not r.get("isMeta")
    ]
    assert _user_texts(back_recs) == _user_texts(src_convo)
    assert _assistant_texts(back_recs) == _assistant_texts(src_convo)
    src_tools = sum(
        1 for r in src_convo if r["type"] == "assistant"
        for b in (r.get("message") or {}).get("content") or []
        if isinstance(b, dict) and b.get("type") == "tool_use"
    )
    back_tools = sum(
        1 for r in back_recs if r["type"] == "assistant"
        for b in r["message"]["content"] if b.get("type") == "tool_use"
    )
    assert back_tools == src_tools
```

- [ ] **Step 2: Run it**

Run: `env/bin/pytest webapp/backend/tests/test_export_roundtrip.py -q`
Expected: PASS. If text lists mismatch, the bug is in one of the converters; fix there, never by loosening the assertion.

- [ ] **Step 3: Commit**

```bash
[ "$(git branch --show-current)" = "trace-porting-design" ] && \
git add webapp/backend/tests/test_export_roundtrip.py && \
git commit -m "test: claude-codex-claude structural round trip"
```

- [ ] **Step 4: Live verification checklist (this machine, run with the user)**

Run the backend locally (its README/dev script), upload a real session from this repo, then:

1. Claude -> Codex: `python3 plugins/cli/commands/import-trace.py <trace-url> --to codex` with `VIBESHUB_SERVER_URL` pointed at the local server; run the printed `codex resume <id>`; ask "summarize this session so far"; expect an on-topic answer.
2. Codex -> Claude: upload a real rollout (share from a Codex session or POST a `~/.codex/sessions` file), `... --to claude`, run the printed `claude --resume <id>` from this repo; expect the history to render and continue. If Claude Code rejects the file, the first suspect is the omitted `version` field (Task 3 note): add `"version": "1.0.0"` to the envelope in `codex_to_claude_session.py`, regenerate, retest, and record the finding in that module's docstring.
3. Takeover-style: from a different branch than the trace's, re-run import and confirm the divergence guidance prints; re-run with `--checkout` on a clean tree and confirm the switch.

Record all three outcomes in the PR description. Cleanup: delete the spike/imported sessions from `~/.codex/sessions` and `~/.claude/projects` if unwanted.
```
