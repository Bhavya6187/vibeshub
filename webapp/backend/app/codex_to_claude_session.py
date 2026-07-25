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
            parts = payload.get("content")
            for part in parts if isinstance(parts, list) else []:
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
