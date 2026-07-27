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
    rollout_session_id_from_blob,
    uuid7_from,
)

FIXTURES = Path(__file__).parent / "fixtures"
SESSION_UUID = "01912345-0000-7000-8000-000000000abc"


def _records(blob: bytes) -> list[dict]:
    return [json.loads(l) for l in blob.splitlines() if l.strip()]


def _out() -> list[dict]:
    raw = (FIXTURES / "claude_export" / "sample.jsonl").read_bytes()
    return _records(claude_to_codex_rollout(raw, session_uuid=SESSION_UUID))


def _claude_lines(*records: dict) -> bytes:
    """A hand-written Claude-shaped JSONL blob, for the edge cases the
    sample fixture does not contain."""
    return ("\n".join(json.dumps(r) for r in records) + "\n").encode("utf-8")


def test_first_line_is_session_meta_with_given_uuid():
    first = _out()[0]
    assert first["type"] == "session_meta"
    assert first["payload"]["id"] == SESSION_UUID
    assert first["payload"]["originator"] == "vibeshub"
    # Codex 0.145.0's TUI thread/resume rejects a rollout whose session_meta
    # lacks model_provider: "Model provider `` not found" (code -32600).
    assert first["payload"]["model_provider"] == "openai"
    assert "git" not in first["payload"]


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


def test_interrupted_tool_use_gets_a_synthesized_output():
    # An interrupted session ends on a tool_use whose tool_result was never
    # written. The Responses API rejects unpaired calls on resume.
    raw = _claude_lines(
        {"type": "user", "timestamp": "2026-07-24T18:00:00.000Z",
         "cwd": "/repo", "message": {"role": "user", "content": "read it"}},
        {"type": "assistant", "timestamp": "2026-07-24T18:00:01.000Z",
         "cwd": "/repo", "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "toolu_dangling", "name": "Read",
              "input": {"file_path": "/repo/x.py"}}]}},
    )
    recs = _records(claude_to_codex_rollout(raw, session_uuid=SESSION_UUID))
    outputs = [r for r in recs
               if r["payload"].get("type") == "function_call_output"]
    assert [o["payload"]["call_id"] for o in outputs] == ["toolu_dangling"]
    assert outputs[0]["payload"]["output"] == "(tool result not recorded)"
    assert outputs[0]["timestamp"] == "2026-07-24T18:00:01.000Z"


def test_paired_tool_use_gets_no_synthesized_output():
    raw = _claude_lines(
        {"type": "assistant", "timestamp": "2026-07-24T18:00:01.000Z",
         "cwd": "/repo", "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "toolu_paired", "name": "Read",
              "input": {"file_path": "/repo/x.py"}}]}},
        {"type": "user", "timestamp": "2026-07-24T18:00:02.000Z",
         "cwd": "/repo", "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_paired",
              "content": "file body"}]}},
    )
    recs = _records(claude_to_codex_rollout(raw, session_uuid=SESSION_UUID))
    outputs = [r["payload"] for r in recs
               if r["payload"].get("type") == "function_call_output"]
    assert [o["output"] for o in outputs] == ["file body"]


def test_non_dict_message_is_skipped_without_raising():
    raw = _claude_lines(
        {"type": "user", "timestamp": "2026-07-24T18:00:00.000Z",
         "cwd": "/repo", "message": "hello"},
    )
    recs = _records(claude_to_codex_rollout(raw, session_uuid=SESSION_UUID))
    assert [r["type"] for r in recs] == ["session_meta"]


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


def test_rollout_session_id_from_blob():
    raw = (FIXTURES / "claude_export" / "sample.jsonl").read_bytes()
    out = claude_to_codex_rollout(raw, session_uuid=SESSION_UUID)
    assert rollout_session_id_from_blob(out) == SESSION_UUID
    assert rollout_session_id_from_blob(b"not json\n") is None


def test_rollout_session_id_is_verbatim_not_derived_from_the_filename():
    """Ids of any shape round-trip whole. The export endpoint puts this in
    a response header, so a fixed-width slice of the filename would garble
    anything that is not a 36-char uuid."""
    native = (FIXTURES / "codex" / "rollout.jsonl").read_bytes()
    assert rollout_session_id_from_blob(native) == json.loads(
        native.splitlines()[0]
    )["payload"]["id"]
    short = b'{"type":"session_meta","payload":{"id":"tiny"}}\n{"x":1}\n'
    assert rollout_session_id_from_blob(short) == "tiny"
    # A session_meta with no usable id is not a rollout for either accessor.
    no_id = b'{"type":"session_meta","payload":{"id":7}}\n'
    assert rollout_session_id_from_blob(no_id) is None
    assert rollout_filename_from_blob(no_id) is None


def test_conversion_matches_golden():
    raw = (FIXTURES / "claude_export" / "sample.jsonl").read_bytes()
    golden = (FIXTURES / "claude_export" / "sample.golden.jsonl").read_bytes()
    out = claude_to_codex_rollout(raw, session_uuid=SESSION_UUID)
    assert _records(out) == _records(golden)
    assert out == golden
