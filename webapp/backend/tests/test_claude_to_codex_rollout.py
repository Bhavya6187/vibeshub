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
