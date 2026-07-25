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
    return [json.loads(line) for line in blob.splitlines() if line.strip()]


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


def test_malformed_records_are_skipped_not_raised():
    """Uploaded bytes are untrusted: bad shapes drop records, never raise."""
    blob = b"\n".join([
        b"not json at all",
        b'"a bare string record"',
        b'{"type":"response_item","payload":"not a dict"}',
        b'{"type":"response_item","payload":{"type":"message",'
        b'"role":"assistant","content":5}}',
        b'{"type":"response_item","payload":{"type":"function_call",'
        b'"name":"exec_command","call_id":"c1","arguments":"[1,2]"}}',
        b'{"type":"event_msg","payload":{"type":"user_message",'
        b'"message":{"not":"a string"}}}',
        b'{"timestamp":"t","type":"event_msg","payload":'
        b'{"type":"user_message","message":"survivor"}}',
    ])
    recs = [json.loads(line)
            for line in codex_to_claude_session(blob, session_id=SID)
            .splitlines() if line.strip()]
    # The malformed exec_command still converts (non-dict args -> {}); only
    # the unusable records drop out.
    assert [r["message"].get("content") for r in recs if r["type"] == "user"] \
        == ["survivor"]
    assert len(recs) == 2


def test_user_prompts_come_from_event_msg_not_env_context():
    recs = _records()
    texts = [r["message"]["content"] for r in recs
             if r["type"] == "user"
             and isinstance(r["message"]["content"], str)]
    assert texts, "expected at least one typed user prompt"
    assert not any("environment_context" in t for t in texts)
