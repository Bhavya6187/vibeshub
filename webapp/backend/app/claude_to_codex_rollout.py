"""Convert Claude-shaped JSONL to a resume-grade Codex rollout.

Spike verdict (2026-07-25, codex-cli 0.137): fabricated rollouts are
accepted and resume cleanly; foreign tool names ride along verbatim and
their output reaches the model; turn_context is optional; and
base_instructions is not required. Full verdict below.

Amendment (2026-07-26, codex-cli 0.145): session_meta MUST carry
model_provider. The interactive TUI's thread/resume rejects a rollout
without it ("Model provider `` not found", code -32600); `codex exec
resume`, which the spike used, still tolerates the omission, which is
how the gap escaped the spike.

Pure: bytes in, bytes out, no I/O. The inverse-direction sibling is
codex_to_claude_session.py; the view-grade codex_convert.py is frozen
and unrelated to this module.
"""
# ---------------------------------------------------------------------------
# Feasibility verdict, spiked against codex-cli 0.137.0 / gpt-5.5 on 2026-07-25.
# Three real `codex exec resume` runs against hand-fabricated rollout files.
#
# (a) Does `codex resume` accept a fabricated file?  YES.
#     A file written to ~/.codex/sessions/YYYY/MM/DD/rollout-<stamp>-<uuid>.jsonl
#     with a hand-made session_meta resumes cleanly (exit 0), and the model
#     answers in the context of the fabricated history. Codex appends its new
#     turn to that same file rather than starting a new one.
#
# (b) Are foreign function names (e.g. "Read") tolerated?  YES, and preserved.
#     A `function_call` named "Read" (a name absent from Codex's own tool set)
#     plus its `function_call_output` round-trips without error, AND its output
#     text reaches the model: a unique marker planted in that output was quoted
#     back verbatim on resume. So Claude tool names can be passed through as-is.
#     The `exec_command` "# vibeshub: <name>" comment-smuggling fallback is NOT
#     required.
#
# (c) Is `turn_context` required?  NO.
#     Removing the turn_context line entirely still resumes (exit 0). Codex
#     writes its own full turn_context for each new turn. Emitting a minimal one
#     is harmless but optional.
#
# (d) Is `base_instructions` required?  NO.
#     Every run omitted base_instructions from session_meta (real Codex rollouts
#     carry a large base_instructions object there) and all resumed fine. Codex
#     injects its own developer/permissions and <environment_context> messages
#     at resume time.
#
# Also observed, all tolerated: `source` as a plain string ("vibeshub") where
# real rollouts use an object; a non-semver `cli_version`; zero `reasoning`
# items; and timestamps formatted as local time with a "Z" suffix.
#
# The spike never varied the filename independently of session_meta.id, so keep
# rollout_filename's <uuid> equal to the session_meta id it is paired with.
# ---------------------------------------------------------------------------
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

_EPOCH_TS = "1970-01-01T00:00:00.000Z"
# Stand-in output for a tool call whose result never reached the transcript
# (interrupted session). The Responses API rejects an unpaired call on resume.
_MISSING_OUTPUT = "(tool result not recorded)"


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


def _first_session_meta(blob: bytes) -> tuple[dict, dict] | None:
    """`(record, payload)` when the blob's first line is a session_meta
    carrying a string id, None otherwise. Shared by the two public
    accessors below so they can never disagree about what a rollout is."""
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
    return rec, payload


def rollout_session_id_from_blob(blob: bytes) -> str | None:
    """The rollout's own session id, verbatim, or None when the blob does
    not open with a usable session_meta. Callers that need the id must read
    it from here rather than slicing it back out of the filename."""
    parsed = _first_session_meta(blob)
    return None if parsed is None else parsed[1]["id"]


def rollout_filename_from_blob(blob: bytes) -> str | None:
    parsed = _first_session_meta(blob)
    if parsed is None:
        return None
    rec, payload = parsed
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
        # Required by Codex >= 0.145.0: the TUI's thread/resume looks the
        # provider up by this name and rejects the rollout when it is absent
        # ("Model provider `` not found", code -32600). "openai" is Codex's
        # built-in default provider id.
        "model_provider": "openai",
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

    call_ids: list[str] = []  # function_call ids, in emission order
    output_ids: set[str] = set()  # ids a function_call_output was emitted for

    for rec in convo:
        ts = rec.get("timestamp") or first_ts
        msg = rec.get("message")
        msg = msg if isinstance(msg, dict) else {}
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
                    call_id = str(block.get("tool_use_id") or "")
                    output_ids.add(call_id)
                    push(ts, "response_item", {
                        "type": "function_call_output",
                        "call_id": call_id,
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
                    call_id = str(block.get("id") or "")
                    call_ids.append(call_id)
                    push(ts, "response_item", {
                        "type": "function_call", "name": name,
                        "arguments": json.dumps(args, ensure_ascii=False),
                        "call_id": call_id,
                    })
                # thinking blocks are dropped by design (see spec).

    # Interrupted sessions end on a tool_use whose tool_result was never
    # written. Codex resume feeds history to the Responses API, which rejects
    # a function_call with no matching output, so pair the leftovers up.
    last_ts = out[-1]["timestamp"] if out else first_ts
    for call_id in call_ids:
        if call_id in output_ids:
            continue
        output_ids.add(call_id)
        push(last_ts, "response_item", {
            "type": "function_call_output", "call_id": call_id,
            "output": _MISSING_OUTPUT,
        })

    body = "\n".join(
        json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in out
    )
    return (body + "\n").encode("utf-8")
