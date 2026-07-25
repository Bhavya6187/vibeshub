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


def test_dangling_tool_call_gets_a_placeholder_result():
    """An interrupted rollout ends on a function_call whose output was never
    written. The Messages API rejects a trailing tool_use with no tool_result,
    so resume would fail unless the export pairs the leftovers up."""
    blob = b"\n".join([
        b'{"timestamp":"t1","type":"event_msg","payload":'
        b'{"type":"user_message","message":"run it"}}',
        b'{"timestamp":"t2","type":"response_item","payload":'
        b'{"type":"function_call","name":"exec_command","call_id":"call-1",'
        b'"arguments":"{\\"cmd\\":\\"ls\\"}"}}',
    ])
    recs = [json.loads(line)
            for line in codex_to_claude_session(blob, session_id=SID)
            .splitlines() if line.strip()]
    assert len(recs) == 3
    last = recs[-1]
    assert last["type"] == "user"
    assert last["message"]["content"] == [{
        "type": "tool_result", "tool_use_id": "call-1",
        "content": "(tool result not recorded)", "is_error": False,
    }]
    assert last["toolUseResult"] == {"stdout": "(tool result not recorded)"}
    # Timestamp borrowed from the last real record; chain still intact.
    assert last["timestamp"] == "t2"
    assert last["parentUuid"] == recs[-2]["uuid"]
    assert last["sessionId"] == SID


def test_paired_tool_calls_are_not_padded_twice():
    """Every tool_use in a complete rollout already has its result, so the
    dangling-call pass must add nothing."""
    recs = _records()
    uses = [b for r in recs if r["type"] == "assistant"
            for b in r["message"]["content"] if b["type"] == "tool_use"]
    results = [b for r in recs if r["type"] == "user"
               for b in r["message"]["content"]
               if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert len(results) == len(uses)
    assert not any(
        b["content"] == "(tool result not recorded)" for b in results
    )


def test_contiguous_assistant_records_share_one_message_id():
    """Real transcripts carry one message.id per API response, so a reader
    regrouping by id must not split back-to-back tool calls into separate
    messages: that would leave a tool_use with no adjacent tool_result."""
    blob = b"\n".join([
        b'{"timestamp":"t1","type":"event_msg","payload":'
        b'{"type":"user_message","message":"go"}}',
        b'{"timestamp":"t2","type":"response_item","payload":'
        b'{"type":"function_call","name":"exec_command","call_id":"c1",'
        b'"arguments":"{\\"cmd\\":\\"ls\\"}"}}',
        b'{"timestamp":"t3","type":"response_item","payload":'
        b'{"type":"function_call","name":"exec_command","call_id":"c2",'
        b'"arguments":"{\\"cmd\\":\\"pwd\\"}"}}',
        b'{"timestamp":"t4","type":"response_item","payload":'
        b'{"type":"function_call_output","call_id":"c1","output":"a"}}',
        b'{"timestamp":"t5","type":"response_item","payload":'
        b'{"type":"function_call_output","call_id":"c2","output":"b"}}',
        b'{"timestamp":"t6","type":"response_item","payload":'
        b'{"type":"message","role":"assistant","content":'
        b'[{"type":"output_text","text":"done"}]}}',
    ])
    recs = [json.loads(line)
            for line in codex_to_claude_session(blob, session_id=SID)
            .splitlines() if line.strip()]
    ids = [r["message"]["id"] for r in recs if r["type"] == "assistant"]
    assert len(ids) == 3
    # The two consecutive tool calls are one assistant turn.
    assert ids[0] == ids[1]
    # The text after the tool results is a new turn, so a new id.
    assert ids[2] != ids[0]


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
        b'{"type":"response_item","payload":{"type":"message",'
        b'"role":"assistant","content":[{"type":"output_text",'
        b'"text":{"x":1}}]}}',
        b'{"timestamp":"t","type":"event_msg","payload":'
        b'{"type":"user_message","message":"survivor"}}',
    ])
    recs = [json.loads(line)
            for line in codex_to_claude_session(blob, session_id=SID)
            .splitlines() if line.strip()]
    # The malformed exec_command still converts (non-dict args -> {}); only
    # the unusable records drop out. A non-str output_text is one of them:
    # emitting it raw would produce a non-Anthropic-shaped text block.
    assert [r["message"]["content"] for r in recs
            if isinstance(r["message"]["content"], str)] == ["survivor"]
    # tool_use, prompt, and the placeholder result the unpaired call needs.
    assert len(recs) == 3


def test_unconvertible_input_returns_empty_bytes():
    """An export must never serve a syntactically-valid but empty file."""
    assert codex_to_claude_session(b"", session_id=SID) == b""
    assert codex_to_claude_session(b"not json\n", session_id=SID) == b""


def test_envelope_fields_are_resume_grade():
    """Pins the envelope contract Claude Code needs to resume a session."""
    recs = _records()
    for r in recs:
        assert isinstance(r["cwd"], str) and r["cwd"]
        assert r["userType"] == "external"
        assert "timestamp" in r
        assert "version" not in r
        # kitchen_sink's session_meta carries git.branch == "main".
        assert r["gitBranch"] == "main"


def test_user_prompts_come_from_event_msg_not_env_context():
    recs = _records()
    texts = [r["message"]["content"] for r in recs
             if r["type"] == "user"
             and isinstance(r["message"]["content"], str)]
    assert texts, "expected at least one typed user prompt"
    assert not any("environment_context" in t for t in texts)
