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
