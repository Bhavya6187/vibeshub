# Trace Porting Between Claude Code and Codex

Date: 2026-07-24
Status: Approved design, pending implementation plan

## Summary

Let users port a session across agent CLIs through vibeshub: upload a trace
from one tool, download it as a **resumable** session for the other. V1
supports both directions (Claude Code to Codex, Codex to Claude Code) with
resume-grade fidelity, delivered by a CLI import verb.

## Background

vibeshub already stores every trace's native bytes verbatim (`main.jsonl`)
and converts imported formats (Codex, Cursor) into a Claude-shaped copy
(`converted.jsonl`) for viewing. The view-grade converter
`webapp/backend/app/codex_convert.py` is frozen: its synthetic
`codex-rec-<n>` uuids anchor digest chapters, so this feature adds new
modules and does not touch it.

Format ground truth (verified against 77 local Codex rollouts and real
Claude Code sessions):

| | Claude Code | Codex |
|---|---|---|
| Location | `~/.claude/projects/<cwd-slug>/<uuid>.jsonl` | `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` |
| Envelope | flat records, `uuid`/`parentUuid`/`sessionId` per line | `session_meta`, `turn_context`, `response_item`, `event_msg` |
| Messages | Anthropic content blocks (`text`, `thinking`, `tool_use`, `tool_result`) | OpenAI Responses items (`message`, `function_call`, `function_call_output`) |
| Reasoning | plaintext `thinking` (signatures killed by upload redaction) | `reasoning` with `encrypted_content` (opaque) |
| Tools | `Bash`, `Read`, `Edit`, `Write`, `Grep`, `Task`, ... | `exec_command`, `write_stdin`, `apply_patch`, `update_plan`, ... |

Feasibility notes:

- Foreign transcripts resume fine in Claude Code when the filename matches
  the session id (verified 2026-07-24).
- Codex accepting a fabricated rollout is the open question; settled by the
  step-0 spike below.

## Fidelity contract

Porting moves the conversation history, not the model's private state.

Ported: user turns, assistant text, tool calls and outputs, timestamps,
cwd/branch metadata.

Lost by design:

- Chain-of-thought, both directions. Codex reasoning is encrypted; Claude
  thinking signatures are destroyed by upload redaction.
- Claude-only records: file-history snapshots, attachments, permission
  modes, pr-link/ai-title markers.
- Subagent transcripts. V1 ports the main thread only; subagent work still
  appears as the `Task`/`spawn_agent` call and its result.
- Anything server redaction removed (secrets, image base64, long hex).

Round-trips are therefore mildly degraded. This is accepted, consistent
with the project's migration-simplicity stance.

## Architecture

### Converters (backend, `webapp/backend/app/`)

Two new modules, pure bytes-in/bytes-out, deterministic, golden-pinned:

**`claude_to_codex_rollout.py`** takes the Claude-shaped copy, which exists
for every trace (native for Claude uploads, `converted.jsonl` for imported
ones, so Cursor-to-Codex export falls out for free). Emits a valid rollout:

- `session_meta` with a deterministic UUIDv7 derived from the trace, cwd
  and timestamps from the source.
- Minimal `turn_context` per user turn.
- `response_item`s: `message` (user `input_text` / assistant
  `output_text`), `function_call` (Claude `tool_use.input` JSON-stringified
  into `arguments`, `tool_use_id` mapped to `call_id`),
  `function_call_output`.
- `event_msg` `user_message`/`agent_message` pairs so the Codex TUI
  replays the transcript.

Tool names: `Bash` maps to `exec_command` with translated arguments; all
other Claude tools stay verbatim as foreign function calls, subject to
revision after the spike.

**`codex_to_claude_session.py`** is the resume-grade sibling of the
view-grade converter: real uuid/parentUuid chains, `sessionId` matching the
output filename, full per-line envelope (cwd, timestamp, version),
Anthropic-shaped `message` bodies. Encrypted reasoning is dropped;
`exec_command` maps back to `Bash`; other Codex tools stay verbatim and
render generically in Claude Code.

### Export endpoints (`webapp/backend/app/api/traces.py`)

- `GET /api/traces/{short_id}/export/codex`
- `GET /api/traces/{short_id}/export/claude`

Behavior:

- Behind `_require_trace_access` (private-trace gating unchanged).
- Auth: browser session cookie, or a GitHub bearer token verified the same
  way `/api/ingest` verifies uploads. The bearer path is what lets the CLI
  download private traces.
- Same-format short-circuit: exporting a trace to its own native format
  serves the stored `main.jsonl` bytes unchanged.
- Dispatch by `source_format`:

  | Trace source | `export/codex` | `export/claude` |
  |---|---|---|
  | Claude (native) | `claude_to_codex_rollout(main.jsonl)` | `main.jsonl` verbatim |
  | Codex | `main.jsonl` verbatim | `codex_to_claude_session(main.jsonl)` |
  | Cursor | `claude_to_codex_rollout(converted.jsonl)` | 422 in v1 (see below) |

  Cursor-to-Claude is excluded from v1: the stored `converted.jsonl` is
  view-grade (synthetic `cursor-rec-<n>` uuids, no resume envelope), so
  resume-grade output would need an additional envelope-upgrading pass.
  The endpoint returns 422 with a clear message. Cursor-to-Codex works
  because `claude_to_codex_rollout` only reads message content, not the
  resume envelope.
- Response: JSONL body, `Content-Disposition: attachment`, suggested
  filename in a response header so clients never reimplement naming rules.
- Malformed or unconvertible input returns 422, never a broken file.

No frontend work in v1. An "open in Codex" hint on the trace page is a
follow-up.

### CLI import verb (`plugins/cli/`)

`vibeshub import <trace-url-or-short-id> --to codex|claude`, beside the
existing share command, shipped in the Claude Code plugin and regenerated
into the Cursor plugin per the existing sync flow.

Steps: resolve the trace reference, call the export endpoint using the
same `gh auth token` flow the uploader uses, write the file where the
target CLI looks for it, print the exact resume command.

Placement:

- Codex: `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` (date
  dirs from the session timestamp).
- Claude Code: the current project's `~/.claude/projects/<cwd-slug>/`
  directory so `claude --resume` lists it.

Collision handling: if the target file exists (re-importing your own
session on the same machine), re-id the session rather than overwrite.

Failure behavior: verify the write landed; never partially overwrite;
unknown targets or missing converted copies fail with instructions, not
tracebacks.

## Build order

1. **Spike (step 0):** hand-convert one real local Claude session to a
   rollout, place it, run `codex resume`. Settles foreign tool names and
   absent reasoning before production code. Output becomes the first
   golden fixture.
2. Converters plus golden tests (backend pytest via `env/bin/pytest`).
3. Export endpoints plus auth plus tests.
4. CLI import verb plus plugin packaging.
5. End-to-end both directions on a real machine: upload, import, resume.

## Risks and open questions

- **Codex resume tolerance** (foreign tool names, missing reasoning items,
  fabricated `session_meta`): resolved by the spike; if Codex rejects
  foreign names, fall back to mapping more tools onto `exec_command`
  equivalents or a generic passthrough shape the spike identifies.
- **Codex format drift:** rollout schema is unversioned and has changed
  across releases (session_meta grew past 32 KB at 0.135). Golden tests
  pin our output; a periodic re-verification against the installed Codex
  is part of e2e.
- **Anthropic API tolerance on resumed foreign history** is already
  de-risked by the 2026-07-24 verification.
