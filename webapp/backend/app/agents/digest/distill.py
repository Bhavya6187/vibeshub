"""Distill a Claude Code JSONL trace into a compact text form for the LLM.

The four-tier classification is documented in
docs/superpowers/specs/2026-06-06-trace-digest-agent-design.md §5.

This module is pure: no I/O, no env-var reads. It operates on bytes in,
returns str out. Easy to unit-test against fixture jsonls.
"""
from __future__ import annotations

import json
import re
from typing import Any

_DROPPED_TYPES = {
    "permission-mode",
    "file-history-snapshot",
    "attachment",
    "last-prompt",
    "ai-title",
}

# Tools whose presence we record but whose inputs are scratchpad noise.
# update_plan is Codex's TodoWrite (emitted by codex_convert).
_SCRATCH_TOOLS = {"TodoWrite", "update_plan"}

# Shell-style tools: render as "<name>: <command>". Bash is Claude Code's;
# shell is what codex_convert emits, Shell is Cursor's. All carry the same
# input keys.
_SHELL_TOOLS = {"Bash", "shell", "Shell"}

# Subagent dispatch tools: Claude Code's Task, the spawn_agent calls that
# codex_convert emits, and Cursor's Subagent. All carry the same input
# keys (subagent_type, description).
_SUBAGENT_TOOLS = {"Task", "spawn_agent", "Subagent"}

_TOOL_RESULT_PREFIX = 80
_TOOL_RESULT_ERROR_PREFIX = 400
_BASH_COMMAND_MAX = 120
_DEFAULT_TARGET_TOKENS = 60_000
_DEFAULT_HARDCAP_TOKENS = 200_000
_EXPLORATION_RUN_MIN = 6
# Measured with o200k_base on real traces: distilled output runs
# 0.26-0.32 tokens/char. 0.4 overshot by 25-50%, tripping collapse and
# the hard cap well before the intended budgets.
_TOKENS_PER_CHAR = 0.3
# Genuine user prose averages ~300 chars; anything much larger is a
# pasted log or stack trace whose signal is in the head.
_USER_TEXT_MAX = 2000
_EDIT_TOOLS = {"Write", "Edit", "MultiEdit"}
_EDIT_PREVIEW_LINES = 3
_EDIT_PREVIEW_LINE_MAX = 80


def distill(
    blob: bytes, *, subagent_blobs: dict[str, bytes],
    target_tokens: int = _DEFAULT_TARGET_TOKENS,
    hard_cap_tokens: int = _DEFAULT_HARDCAP_TOKENS,
) -> str:
    """Return a compact text form of `blob` ready for the LLM call."""
    text, _ = distill_with_uuids(
        blob, subagent_blobs=subagent_blobs,
        target_tokens=target_tokens, hard_cap_tokens=hard_cap_tokens,
    )
    return text


def distill_with_uuids(
    blob: bytes, *, subagent_blobs: dict[str, bytes],
    target_tokens: int = _DEFAULT_TARGET_TOKENS,
    hard_cap_tokens: int = _DEFAULT_HARDCAP_TOKENS,
) -> tuple[str, set[str]]:
    """Like `distill`, but also returns the set of event UUIDs that appear
    in the output. Callers use this to validate chapter anchor_uuids
    against the actual anchorable surface.
    """
    lines, uuids = _classify(blob, subagent_blobs)
    if _est_tokens(lines) > target_tokens:
        lines = _collapse_exploration_runs(lines)
    if _est_tokens(lines) > hard_cap_tokens:
        lines = _truncate_middle(lines, hard_cap_tokens)
    return "\n".join(lines), uuids


def edited_paths(blob: bytes, *, subagent_blobs: dict[str, bytes]) -> set[str]:
    """Set of file paths touched by edit tools in the main and subagent
    streams. The digest pipeline validates file_notes paths against this."""
    paths: set[str] = set()
    for b in (blob, *subagent_blobs.values()):
        for raw in b.splitlines():
            if not raw.strip():
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if ev.get("type") != "assistant":
                continue
            content = (ev.get("message") or {}).get("content") or []
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") in _EDIT_TOOLS
                ):
                    fp = (block.get("input") or {}).get("file_path")
                    if isinstance(fp, str) and fp:
                        paths.add(fp)
    return paths


def _classify(
    blob: bytes, subagent_blobs: dict[str, bytes],
) -> tuple[list[str], set[str]]:
    """Walk the JSONL once, applying the four-tier classifier, and return
    the rendered lines plus the set of anchorable event UUIDs."""
    lines: list[str] = []
    uuids: set[str] = set()
    prev: str | None = None
    for raw in blob.splitlines():
        if not raw.strip():
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        rendered = _render(ev, subagent_blobs)
        if rendered is None:
            continue
        prefix, body = rendered
        if body == prev:
            continue  # adjacent duplicate (compare bodies, not uuid-prefixed lines)
        prev = body
        uuid = ev.get("uuid")
        if uuid:
            uuids.add(uuid)
        lines.append(prefix + body)
    return lines, uuids


def _render(
    ev: dict, subagent_blobs: dict[str, bytes],
) -> tuple[str, str] | None:
    et = ev.get("type")
    if et in _DROPPED_TYPES:
        return None
    uuid = ev.get("uuid", "")
    prefix = f"[{uuid}] " if uuid else ""
    if et == "user":
        # Synthetic user records injected by Claude Code itself (Skill
        # bodies replayed with isMeta: true). Not user-authored; often
        # 5-20k chars each of boilerplate the digest must not mistake
        # for the user's ask.
        if ev.get("isMeta") is True:
            return None
        text = _user_text(ev)
        if text is None:
            return None
        return prefix, f"USER: {text}"
    if et == "assistant":
        body = _render_assistant(ev, subagent_blobs)
        if body is None:
            return None
        return prefix, body
    return None


_CMD_TAG_RE = re.compile(r"<command-(name|message|args)>([\s\S]*?)</command-\1>")
_LOCAL_STDOUT_RE = re.compile(
    r"<local-command-std(?:out|err)>([\s\S]*?)</local-command-std(?:out|err)>"
)
_TASK_NOTIF_FIELD_RE = re.compile(r"<(status|summary|result)>([\s\S]*?)</\1>")
_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>[\s\S]*?</system-reminder>")
_ANSI_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")


def _compact_wrapper_text(text: str) -> str | None:
    """Rewrite Claude Code's injected user-message wrappers into compact
    one-liners; ordinary user prose passes through unchanged."""
    stripped = text.strip()
    if stripped.startswith("<task-notification>"):
        fields = dict(_TASK_NOTIF_FIELD_RE.findall(stripped))
        summary = " ".join((fields.get("summary") or "").split())
        result = " ".join((fields.get("result") or "").split())[:160]
        status = fields.get("status") or "done"
        body = ": ".join(p for p in (summary, result) if p)
        return f"[background task {status}] {body}".strip()
    if "<command-name>" in stripped:
        tags = _CMD_TAG_RE.findall(stripped)
        if tags and not _CMD_TAG_RE.sub("", stripped).strip():
            parts = {k: v.strip() for k, v in tags}
            name = parts.get("name", "")
            if not name:
                return None
            if not name.startswith("/"):
                name = f"/{name}"
            args = parts.get("args", "")
            return f"{name} {args}".strip()
    if stripped.startswith(("<local-command-stdout>", "<local-command-stderr>")):
        inner = " ".join(
            m.strip() for m in _LOCAL_STDOUT_RE.findall(stripped) if m.strip()
        )
        inner = " ".join(_ANSI_SGR_RE.sub("", inner).split())
        if inner == "(no content)":
            inner = ""
        inner = inner[:_TOOL_RESULT_PREFIX]
        return f"[command output] {inner}".strip()
    return text


def _user_text(ev: dict) -> str | None:
    msg = ev.get("message") or {}
    content = msg.get("content")
    parts: list[str] = []
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_result":
                parts.append(_tool_result_to_line(block))
    else:
        return None
    cleaned: list[str] = []
    for p in parts:
        if not p:
            continue
        p = _SYSTEM_REMINDER_RE.sub("", p).strip()
        if not p:
            continue
        compacted = _compact_wrapper_text(p)
        if compacted:
            cleaned.append(compacted)
    joined = " | ".join(cleaned)
    if len(joined) > _USER_TEXT_MAX:
        # Keep head AND tail: in a giant paste the ask is often the
        # sentence after the log, not before it.
        over = len(joined) - _USER_TEXT_MAX
        head = joined[: _USER_TEXT_MAX // 2]
        tail = joined[len(joined) - (_USER_TEXT_MAX - _USER_TEXT_MAX // 2):]
        joined = f"{head}… [{over} chars elided] …{tail}"
    return joined or None


def _render_assistant(
    ev: dict, subagent_blobs: dict[str, bytes],
) -> str | None:
    msg = ev.get("message") or {}
    content = msg.get("content") or []
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = (block.get("text") or "").strip()
            if text:
                parts.append(f"ASSISTANT: {text}")
        elif btype == "tool_use":
            line = _tool_use_to_line(block, subagent_blobs)
            if line is not None:
                parts.append(line)
        # Thinking blocks are intentionally dropped
    if not parts:
        return None
    return " ".join(parts)


def _new_lines(inp: dict) -> list[str]:
    """Non-blank lines an edit introduces (Write content / Edit & MultiEdit
    new_string), used for the grounding preview and the +count."""
    texts: list[str] = []
    if isinstance(inp.get("content"), str):
        texts.append(inp["content"])
    if isinstance(inp.get("new_string"), str):
        texts.append(inp["new_string"])
    if isinstance(inp.get("edits"), list):
        for e in inp["edits"]:
            if isinstance(e, dict) and isinstance(e.get("new_string"), str):
                texts.append(e["new_string"])
    out: list[str] = []
    for t in texts:
        for ln in t.split("\n"):
            s = ln.strip()
            if s:
                out.append(s)
    return out


def _removed_count(inp: dict) -> int:
    n = 0
    if isinstance(inp.get("old_string"), str):
        n += sum(1 for ln in inp["old_string"].split("\n") if ln.strip())
    if isinstance(inp.get("edits"), list):
        for e in inp["edits"]:
            if isinstance(e, dict) and isinstance(e.get("old_string"), str):
                n += sum(1 for ln in e["old_string"].split("\n") if ln.strip())
    return n


def _edit_preview(name: str, inp: dict, path: str) -> str:
    # Rough non-blank add/remove line counts for the LLM preview, not a true diff.
    added = _new_lines(inp)
    head = f"{name} {path} (+{len(added)} -{_removed_count(inp)})"
    if not added:
        return head
    shown = [ln[:_EDIT_PREVIEW_LINE_MAX] for ln in added[:_EDIT_PREVIEW_LINES]]
    return head + ": " + " / ".join(shown)


def _tool_use_to_line(
    block: dict, subagent_blobs: dict[str, bytes],
) -> str | None:
    name = block.get("name") or ""
    inp = block.get("input") or {}
    if name in _SCRATCH_TOOLS:
        return None
    if name in _SUBAGENT_TOOLS:
        agent_id = (inp.get("subagent_type") or "agent")
        desc = (inp.get("description") or "").strip()
        sub_summary = _summarize_subagent(block, subagent_blobs)
        return f"Subagent[{agent_id}]: {desc} → {sub_summary}".rstrip(" →")
    if name in _SHELL_TOOLS:
        cmd = (inp.get("command") or "").strip()
        if len(cmd) > _BASH_COMMAND_MAX:
            cmd = cmd[:_BASH_COMMAND_MAX] + "…"
        return f"{name}: {cmd}"
    fp = inp.get("file_path")
    if isinstance(fp, str) and fp:
        if name in _EDIT_TOOLS:
            return _edit_preview(name, inp, fp)
        return f"{name} {fp}"
    # Grep/Glob carry pattern + path; other tools fall through to a label.
    pattern = inp.get("pattern") or inp.get("query")
    path = inp.get("path") or ""
    if pattern:
        return f'{name} "{pattern}"' + (f" in {path}" if path else "")
    if isinstance(path, str) and path:
        # Cursor's ReadFile/Read carry path instead of file_path.
        return f"{name} {path}"
    return name


def _tool_result_to_line(block: dict) -> str:
    content = block.get("content")
    is_error = bool(block.get("is_error"))
    if isinstance(content, list):
        text_parts = [
            str(b.get("text") or "") for b in content if isinstance(b, dict)
        ]
        body = " ".join(text_parts).strip()
    else:
        body = str(content or "").strip()
    limit = _TOOL_RESULT_ERROR_PREFIX if is_error else _TOOL_RESULT_PREFIX
    body = body.replace("\n", " ")
    if len(body) > limit:
        body = body[:limit] + "…"
    status = "ERR" if is_error else "OK"
    return f"RESULT[{status}]: {body}" if body else f"RESULT[{status}]"


def _summarize_subagent(
    block: dict, subagent_blobs: dict[str, bytes],
) -> str:
    """Heuristic one-line outcome for a subagent dispatch.

    Strategy:
    1. If we have the child's blob, take the LAST assistant_text and use
       the first sentence (~120 chars).
    2. Otherwise, fall back to "(N tool calls)" so the LLM at least sees
       that something happened.
    """
    tool_use_id = block.get("id") or ""
    child = subagent_blobs.get(tool_use_id)
    if child is None:
        return ""
    last_text: str | None = None
    tool_call_count = 0
    for raw in child.splitlines():
        if not raw.strip():
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "assistant":
            continue
        msg = ev.get("message") or {}
        content = msg.get("content") or []
        if not isinstance(content, list):
            continue
        for sub_block in content:
            if not isinstance(sub_block, dict):
                continue
            btype = sub_block.get("type")
            if btype == "text":
                t = (sub_block.get("text") or "").strip()
                if t:
                    last_text = t
            elif btype == "tool_use":
                tool_call_count += 1
    if last_text:
        # First sentence, capped at 120 chars
        sentence = last_text.split(". ")[0].strip()
        if len(sentence) > 120:
            sentence = sentence[:120] + "…"
        return sentence
    if tool_call_count:
        return f"({tool_call_count} tool calls)"
    return ""


def _est_tokens(lines: list[str]) -> float:
    return sum(len(ln) for ln in lines) * _TOKENS_PER_CHAR


def _is_tool_line(line: str) -> bool:
    """A 'tool action' line — a [uuid]-prefixed line whose payload is a
    bare tool call (no ASSISTANT: text, no USER: text)."""
    if not line.startswith("["):
        return False
    after = line.split("] ", 1)
    if len(after) != 2:
        return False
    body = after[1]
    if body.startswith("ASSISTANT:") or body.startswith("USER:"):
        return False
    return True


def _collapse_exploration_runs(lines: list[str]) -> list[str]:
    """Replace runs of >= _EXPLORATION_RUN_MIN consecutive tool-action
    lines with a single '[exploration: N tools]' summary.

    Counts the tools that get collapsed for a more useful summary."""
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if _is_tool_line(lines[i]):
            j = i
            while j < n and _is_tool_line(lines[j]):
                j += 1
            run = lines[i:j]
            if len(run) >= _EXPLORATION_RUN_MIN:
                out.append(_summarize_run(run))
            else:
                out.extend(run)
            i = j
        else:
            out.append(lines[i])
            i += 1
    return out


def _summarize_run(run: list[str]) -> str:
    """One-line summary of a collapsed exploration run."""
    from collections import Counter
    counter: Counter[str] = Counter()
    for line in run:
        body = line.split("] ", 1)[1] if "] " in line else line
        tool = body.split(" ", 1)[0].rstrip(":")
        counter[tool] += 1
    parts = ", ".join(f"{n} {t.lower()}{'s' if n != 1 else ''}"
                      for t, n in counter.most_common())
    return f"[exploration: {parts}]"


def _truncate_middle(lines: list[str], hard_cap_tokens: int) -> list[str]:
    """Head/tail truncation. Always keep first and last events; fit as many
    additional head/tail lines as the budget allows."""
    if len(lines) <= 2:
        return lines
    target_chars = int(hard_cap_tokens / _TOKENS_PER_CHAR)
    head_budget = max(int(target_chars * 0.5), len(lines[0]) + 1)
    tail_budget = max(int(target_chars * 0.5), len(lines[-1]) + 1)
    head: list[str] = [lines[0]]
    head_chars = len(lines[0]) + 1
    for line in lines[1:-1]:
        head_chars += len(line) + 1
        if head_chars > head_budget:
            break
        head.append(line)
    tail: list[str] = [lines[-1]]
    tail_chars = len(lines[-1]) + 1
    for line in reversed(lines[len(head):-1]):
        tail_chars += len(line) + 1
        if tail_chars > tail_budget:
            break
        tail.append(line)
    tail.reverse()
    elided = len(lines) - len(head) - len(tail)
    if elided <= 0:
        return head + tail
    marker = f"[… elided {elided} events …]"
    return head + [marker] + tail
