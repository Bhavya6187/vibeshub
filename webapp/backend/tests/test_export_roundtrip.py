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
