# /handoff and /import Slash Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the Claude-to-Codex porting flow to one slash command: `/vibeshub:handoff` uploads the current session, places the converted rollout in `~/.codex/sessions`, and prints `codex resume <uuid>`; `/vibeshub:import <ref>` brings any vibeshub trace into Claude Code the same way.

**Architecture:** Two thin command wrappers over machinery that already shipped in 0.6.0: `run_share_pipeline` (upload, returns trace_url) and `import_trace.run_import` (fetch export, place, re-id on collision, print resume command). No backend changes. Version bumps to 0.6.1.

**Tech Stack:** stdlib-only Python in `plugins/cli/` (urllib/subprocess/asyncio conventions of that tree), pytest.

## Context for a fresh session

The trace-porting feature is live (PR #170, deployed): `GET /api/traces/{short_id}/export/{codex|claude}` serves resume-grade sessions; `plugins/cli/vibeshub_client/import_trace.py` downloads and places them; `plugins/cli/commands/import-trace.py` is the existing generic CLI (`<ref> --to codex|claude [--checkout]`); `plugins/cli/commands/share-trace.py` uploads the current session. This plan only adds ergonomic wrappers. The user's target demo: (1) Claude Code session makes edits, (2) `/vibeshub:handoff` prints the trace URL and `codex resume <uuid>`, (3) user quits Claude, runs the printed command, (4) continues in Codex and makes a PR.

Key interfaces (verify signatures against the code, they are current as of 3563f59 + this branch):
- `run_share_pipeline(reader=..., hook_input={"session_id":..., "cwd":...}, options=RunOptions(server_url=, token=, pr_url=, repo_full_name=, session_id=)) -> result` with `result.uploaded: bool`, `result.trace_url: str`, `result.skip_reason` (see `commands/share-trace.py:_share` for the full call pattern including `select_adapter` and `_resolve_target`).
- `run_import(ref, target, *, server, cwd, checkout) -> int` from `vibeshub_client.import_trace`; prints "placed <path>", repo-state lines, and "resume with: <cmd>" itself; raises `ImportTraceError`/`OSError`.
- `get_gh_token()` from `vibeshub_client.gh_token`, raises `GhTokenError`.

## Global Constraints

- Branch: create `handoff-commands` from `main` and commit there. Other sessions switch branches in this checkout: every commit command MUST verify the branch inline: `[ "$(git branch --show-current)" = "handoff-commands" ] && git add ... && git commit ...`.
- Plugin tests: `env/bin/pytest plugins/cli/tests -q` from the repo root (`env/`, NOT `.venv`). Backend suite for the lockstep test: `env/bin/pytest webapp/backend/tests -q`.
- No em-dashes in any user-facing string (CLI output and .md command docs).
- Plugin stays stdlib-only.
- `cursor-plugin/` is GENERATED; never hand-edit; regenerate with `python3 scripts/sync-cursor-plugin.py --out cursor-plugin` and verify with `--check --out cursor-plugin` (CI enforces).
- `webapp/backend/app/codex_convert.py` is FROZEN (not that this plan should touch the backend at all).
- Version lockstep: `plugins/cli/tests/test_version_lockstep.py` names every file that must agree; bump to `0.6.1` in Task 3.

---

### Task 1: `/vibeshub:handoff` command

**Files:**
- Create: `plugins/cli/commands/handoff.py`
- Create: `plugins/cli/commands/handoff.md`
- Test: `plugins/cli/tests/test_handoff.py`

**Interfaces:**
- Consumes: `run_share_pipeline`, `RunOptions`, `select_adapter`, `_resolve_target`-equivalent logic (copy the small helpers from `commands/share-trace.py`, or import them from it the way `tests/test_share_trace.py` imports that module), `run_import`, `get_gh_token`.
- Produces: a command whose LAST stdout line on success is exactly `resume with: codex resume <uuid>` (emitted by `run_import`), preceded by `trace uploaded: <url>`.

- [ ] **Step 1: Write the failing tests**

Model the scaffolding on `plugins/cli/tests/test_share_trace.py` (module import via the same sys.path bootstrap) and monkeypatch the seams, not the network:

```python
import types

import pytest

# import the command module the same way test_share_trace.py imports
# share-trace (path bootstrap + importlib for the hyphen-free name).


def test_handoff_uploads_then_imports(monkeypatch, capsys, tmp_path):
    calls = {}

    async def fake_pipeline(*, reader, hook_input, options):
        calls["upload"] = {"session_id": options.session_id}
        return types.SimpleNamespace(
            uploaded=True,
            trace_url="https://vibeshub.ai/t/abc123",
            skip_reason=None,
        )

    def fake_run_import(ref, target, *, server, cwd, checkout):
        calls["import"] = {
            "ref": ref, "target": target, "server": server,
            "cwd": cwd, "checkout": checkout,
        }
        print("placed /tmp/x.jsonl")
        print("resume with: codex resume 0191-abc")
        return 0

    # monkeypatch the module's run_share_pipeline, run_import, get_gh_token
    # and set CLAUDE_CODE_SESSION_ID; then call its main()/asyncio entry.
    ...
    out = capsys.readouterr().out
    assert "trace uploaded: https://vibeshub.ai/t/abc123" in out
    assert out.rstrip().endswith("resume with: codex resume 0191-abc")
    assert calls["import"]["ref"] == "https://vibeshub.ai/t/abc123"
    assert calls["import"]["target"] == "codex"
    assert calls["import"]["checkout"] is False


def test_handoff_aborts_on_skipped_upload(monkeypatch, capsys):
    # fake pipeline returns uploaded=False, skip_reason="no transcript found";
    # assert exit code 1, stderr contains the reason, and fake_run_import
    # was never called.
    ...


def test_handoff_reports_placement_failure_with_trace_url(monkeypatch, capsys):
    # upload succeeds, fake_run_import raises ImportTraceError("boom");
    # assert exit 1, stderr mentions both the failure and the still-usable
    # trace URL plus the fallback command name (import-trace).
    ...


def test_handoff_requires_session_id(monkeypatch, capsys):
    # no CLAUDE_CODE_SESSION_ID/CLAUDE_SESSION_ID in env -> exit 1 with the
    # "must be run inside a Claude Code session" message; nothing uploaded.
    ...
```

Fill the `...` bodies following the first test's monkeypatch pattern; every test asserts observable behavior (stdout/stderr/exit code/call record), none may pass vacuously.

- [ ] **Step 2: Run to verify failure**

Run: `env/bin/pytest plugins/cli/tests/test_handoff.py -q`
Expected: FAIL (module does not exist).

- [ ] **Step 3: Implement `handoff.py`**

Mirror `share-trace.py`'s structure exactly (bootstrap, `_session_id`, target resolution). Core flow after a successful upload, where `server_url` is the same `VIBESHUB_SERVER_URL` default used everywhere:

```python
    print(f"trace uploaded: {result.trace_url}")
    try:
        code = run_import(
            result.trace_url, "codex",
            server=server_url, cwd=os.getcwd(), checkout=False,
        )
    except (ImportTraceError, OSError) as e:
        print(
            "upload succeeded but placing the Codex session failed: "
            f"{e}\nYour trace is safe at {result.trace_url}; retry with: "
            f"python3 <plugin>/commands/import-trace.py {result.trace_url} "
            "--to codex",
            file=sys.stderr,
        )
        sys.exit(1)
    sys.exit(code)
```

Requirements the tests pin: session-id gate identical to share-trace's (claude-code platform only), optional `[<pr-number-or-url>]` argument forwarded to target resolution exactly like share-trace, skip reasons abort before any import, and the failure path always names the trace URL.

`handoff.md` (no em-dashes):

```markdown
---
name: handoff
description: Upload this session to vibeshub and hand it off to Codex, printing the codex resume command.
argument-hint: "[<pr-number-or-url>]"
---

Upload the current Claude Code session to vibeshub (same target resolution
as /share-trace), then immediately place the converted Codex session on
this machine and print the exact resume command. After it runs, quit
Claude Code and run the printed `codex resume <id>` to continue this
conversation in Codex. Private traces authenticate with `gh auth token`.

Run the helper script:

!python3 "${CLAUDE_PLUGIN_ROOT}/commands/handoff.py" $ARGUMENTS
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env/bin/pytest plugins/cli/tests/test_handoff.py plugins/cli/tests/test_share_trace.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
[ "$(git branch --show-current)" = "handoff-commands" ] && \
git add plugins/cli/commands/handoff.py plugins/cli/commands/handoff.md \
  plugins/cli/tests/test_handoff.py && \
git commit -m "feat: /handoff uploads the session and stages the codex resume"
```

---

### Task 2: `/vibeshub:import` command + docs

**Files:**
- Create: `plugins/cli/commands/import.md`
- Modify: `plugins/cli/README.md` (usage section: add /handoff and /import beside /share-trace and /import-trace)
- Modify: `plugins/README.md` ("Importing traces" section: mention both new slash forms)
- Test: `plugins/cli/tests/test_import_command_md.py`

**Interfaces:**
- Consumes: the existing `commands/import-trace.py` argparse surface (`ref` positional, `--to {codex,claude}` required, `--checkout` flag).
- Produces: `/vibeshub:import <trace-url-or-short-id> [--checkout]` importing INTO Claude Code (the `--to claude` is appended by the .md wrapper, after `$ARGUMENTS`).

- [ ] **Step 1: Write the failing test**

argparse's last-occurrence-wins makes the trailing `--to claude` safe even if a user passes their own `--to`; pin that assumption so an argparse refactor cannot silently break the wrapper:

```python
"""Pins the contract import.md relies on: a trailing --to claude wins."""
import importlib.util
import sys
from pathlib import Path

_COMMANDS = Path(__file__).resolve().parents[1] / "commands"


def _load_import_trace():
    spec = importlib.util.spec_from_file_location(
        "import_trace_cmd", _COMMANDS / "import-trace.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_trailing_to_claude_wins(monkeypatch, capsys):
    mod = _load_import_trace()
    seen = {}

    def fake_run_import(ref, target, *, server, cwd, checkout):
        seen.update(ref=ref, target=target, checkout=checkout)
        return 0

    monkeypatch.setattr(mod, "run_import", fake_run_import)
    monkeypatch.setattr(
        sys, "argv",
        ["import-trace", "https://vibeshub.ai/t/abc", "--checkout",
         "--to", "claude"],
    )
    with __import__("pytest").raises(SystemExit) as e:
        mod.main()
    assert e.value.code == 0
    assert seen == {
        "ref": "https://vibeshub.ai/t/abc", "target": "claude",
        "checkout": True,
    }


def test_import_md_appends_to_claude():
    body = (_COMMANDS / "import.md").read_text(encoding="utf-8")
    assert body.rstrip().endswith('$ARGUMENTS --to claude')
    assert "—" not in body
```

(If `import-trace.py`'s `main()` calls `run_import` via a different reference than a module attribute, adapt the monkeypatch to whatever seam exists; do not weaken the assertions.)

- [ ] **Step 2: Run to verify failure**

Run: `env/bin/pytest plugins/cli/tests/test_import_command_md.py -q`
Expected: FAIL (import.md missing).

- [ ] **Step 3: Write `import.md` and the doc updates**

```markdown
---
name: import
description: Import a vibeshub trace as a resumable Claude Code session in this project.
argument-hint: "<trace-url-or-short-id> [--checkout]"
---

Download a trace from vibeshub, convert it if needed, and place it as a
session for the current project directory. The command prints the exact
`claude --resume <id>` to open it. Works for traces uploaded from Codex
too. `--checkout` switches to the session's branch when the working tree
is clean; without it the command only prints repo-state guidance and never
touches your files. Private traces authenticate with `gh auth token`.

Run the helper script:

!python3 "${CLAUDE_PLUGIN_ROOT}/commands/import-trace.py" $ARGUMENTS --to claude
```

README updates: one usage line each for `/handoff` and `/import` in `plugins/cli/README.md`'s command section, and a sentence in `plugins/README.md`'s "Importing traces" section naming the two slash forms. No em-dashes.

- [ ] **Step 4: Run tests**

Run: `env/bin/pytest plugins/cli/tests/test_import_command_md.py plugins/cli/tests/test_import_trace.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
[ "$(git branch --show-current)" = "handoff-commands" ] && \
git add plugins/cli/commands/import.md plugins/cli/README.md \
  plugins/README.md plugins/cli/tests/test_import_command_md.py && \
git commit -m "feat: /import slash command brings vibeshub traces into Claude Code"
```

---

### Task 3: version 0.6.1, cursor regen, both suites

**Files:**
- Modify: `plugins/cli/vibeshub_client/version.py` (PLUGIN_VERSION = "0.6.1") plus every file `test_version_lockstep.py` asserts (run it; fix all mismatches it names), plus the root README version badge and `webapp/frontend/package-lock.json` project fields if they carry the version (they did at 0.6.0).
- Regenerate: `cursor-plugin/` via the sync script.

- [ ] **Step 1: Bump and satisfy lockstep**

Run: `env/bin/pytest plugins/cli/tests/test_version_lockstep.py -q` and fix every named mismatch until PASS.

- [ ] **Step 2: Regenerate cursor plugin**

Run: `python3 scripts/sync-cursor-plugin.py --out cursor-plugin && python3 scripts/sync-cursor-plugin.py --check --out cursor-plugin`
Expected: "in sync".

- [ ] **Step 3: Run both full suites**

Run: `env/bin/pytest plugins/cli/tests -q && env/bin/pytest webapp/backend/tests -q`
Expected: PASS (plugin suite grows by the new tests; backend unchanged).

- [ ] **Step 4: Commit**

```bash
[ "$(git branch --show-current)" = "handoff-commands" ] && \
git add -A plugins cursor-plugin webapp README.md && \
git commit -m "chore: bump to 0.6.1 with handoff/import commands, regenerate cursor plugin"
```

---

## After the tasks

1. Final review, then PR to main (squash-merge convention). Merging deploys the server automatically (no backend changes here, so the deploy is a no-op rebuild) and makes 0.6.1 installable.
2. Post-merge: regenerate `~/git/vibeshub-cursor` from the merged commit and push (both repos ship together).
3. Acceptance = the user's live demo: Claude session with edits, `/vibeshub:handoff`, quit, run the printed `codex resume <uuid>`, continue in Codex, make a PR. This doubles as the pending live verification for the Claude-to-Codex direction; record the outcome.
