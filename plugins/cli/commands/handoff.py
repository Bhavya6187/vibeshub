#!/usr/bin/env python3
"""
Hand the current Claude Code session off to Codex.

Uploads the session to vibeshub (same target resolution as share-trace),
places the converted Codex session on this machine, and prints the exact
`codex resume` command as the last line of output.

Usage:
  handoff                       # auto-detect: PR, else repo, else standalone
  handoff <pr-url-or-number>    # attach the trace to a specific PR
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

# The plugin root must be importable before the imports below.
# CLAUDE_PLUGIN_ROOT is set by Claude Code; fall back to this file's
# grandparent when the module is imported directly (e.g. by tests).
_PLUGIN_ROOT = Path(
    os.environ.get(
        "CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parent.parent
    )
)
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from platform_adapter import select_adapter  # noqa: E402
from vibeshub_client.gh_token import get_gh_token  # noqa: E402
from vibeshub_client.import_trace import (  # noqa: E402
    ImportTraceError,
    run_import,
)
from vibeshub_client.pipeline import (  # noqa: E402
    RunOptions,
    RunResult,
    run_share_pipeline,
)
from vibeshub_client.pr_resolve import resolve_pr_url  # noqa: E402
from vibeshub_client.repo_resolve import resolve_repo_full_name  # noqa: E402


def _session_id() -> str | None:
    """The current Claude Code session id. Claude Code exports
    CLAUDE_CODE_SESSION_ID; CLAUDE_SESSION_ID is accepted as a legacy/manual
    fallback."""
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get(
        "CLAUDE_SESSION_ID"
    )


def _resolve_target(*, arg: str | None) -> tuple[str | None, str | None]:
    """Resolve the upload target as a (pr_url, repo_full_name) pair.

    Resolution order:
      1. An open PR (the explicit `arg`, or the current branch's PR) ->
         (pr_url, None).
      2. No PR but a GitHub repo for the current dir -> (None, repo).
      3. Neither -> (None, None), a standalone upload.
    """
    try:
        pr_url = resolve_pr_url(arg)
    except (subprocess.SubprocessError, OSError):
        pr_url = None
    if pr_url:
        return pr_url, None
    return None, resolve_repo_full_name()


async def _upload(
    args: list[str], server_url: str, session_id: str | None
) -> RunResult | None:
    """Share the current session. Returns the RunResult on success, or None
    after reporting why nothing was uploaded."""
    # handoff always ports *this* Claude Code session, so unlike share-trace
    # it cannot accept another platform's reader. select_adapter falls back to
    # CODEX_HOME, and CodexTranscriptReader then picks the newest rollout for
    # cwd: a different conversation entirely.
    reader = select_adapter(
        {"cwd": os.getcwd(), "plugin_root": str(_PLUGIN_ROOT)}
    )

    if reader.platform_id() != "claude-code":
        sys.stderr.write(
            "[vibeshub] handoff ports the current Claude Code session, but a "
            "non-Claude transcript reader was selected (this happens when "
            "CODEX_HOME is set in the shell). Refusing to upload a different "
            "conversation. Use /share-trace and then import-trace to pick the "
            "trace you want.\n"
        )
        return None

    if not session_id:
        sys.stderr.write(
            "[vibeshub] no session_id available; this command must be run "
            "inside a Claude Code session\n"
        )
        return None

    pr_url, repo_full_name = _resolve_target(arg=args[0] if args else None)

    options = RunOptions(
        server_url=server_url,
        token=get_gh_token(),
        pr_url=pr_url,
        repo_full_name=repo_full_name,
        session_id=session_id,
    )
    hook_input = {"session_id": session_id, "cwd": os.getcwd()}

    result = await run_share_pipeline(
        reader=reader, hook_input=hook_input, options=options
    )
    if not result.uploaded:
        print(f"skipped: {result.skip_reason}", file=sys.stderr)
        return None
    return result


def main() -> None:
    args = sys.argv[1:]
    server_url = os.environ.get("VIBESHUB_SERVER_URL", "https://vibeshub.ai")

    result = asyncio.run(_upload(args, server_url, _session_id()))
    if result is None:
        sys.exit(1)

    print(f"trace uploaded: {result.trace_url}")
    # The upload can succeed with a partial failure attached (e.g. the PR
    # comment). stderr keeps the stdout last line the resume command.
    if result.skip_reason:
        print(f"note: {result.skip_reason}", file=sys.stderr)
    try:
        code = run_import(
            result.trace_url, "codex",
            server=server_url, cwd=os.getcwd(), checkout=False,
        )
    # FileExistsError from place() is an OSError, and so is every other way
    # writing the session can fail (unwritable home, ENOSPC). The upload
    # already succeeded, so every failure here still names the trace URL.
    except (ImportTraceError, OSError) as e:
        print(
            "upload succeeded but placing the Codex session failed: "
            f"{e}\nYour trace is safe at {result.trace_url}; retry with: "
            f'python3 "{_PLUGIN_ROOT}/commands/import-trace.py" '
            f"{result.trace_url} --to codex",
            file=sys.stderr,
        )
        sys.exit(1)
    sys.exit(code)


if __name__ == "__main__":
    main()
