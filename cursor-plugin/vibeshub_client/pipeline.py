from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from vibeshub_client.bundle import build_bundle
from vibeshub_client.git_info import git_info
from vibeshub_client.post_comment import build_comment_body, post_pr_comment
from vibeshub_client.redact import redact_jsonl
from vibeshub_client.upload import UploadError, upload_bundle
from vibeshub_client.version import PLUGIN_VERSION

log = logging.getLogger(__name__)


def _platform_label(platform_id: str) -> str:
    if platform_id == "codex":
        return "Codex CLI"
    if platform_id == "cursor":
        return "Cursor"
    return "Claude Code"


def _header_safe(value: str | None) -> str | None:
    """None unless `value` can ride an HTTP header, which is latin-1 on the
    wire. An exotic branch name is never worth failing a share over."""
    if value is None:
        return None
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        return None
    return value


def _git_dir(hook_input: dict) -> str:
    """The directory git metadata describes. Claude Code and Codex payloads
    carry `cwd`; Cursor's afterShellExecution payload has none (and the hook
    process runs from `~/.cursor`, outside any repo), so fall back to the
    first workspace root before the process cwd. Mirrors `_repo_dir` in
    hooks/on-pr-share.py."""
    if hook_input.get("cwd"):
        return hook_input["cwd"]
    roots = hook_input.get("workspace_roots")
    if isinstance(roots, list) and roots:
        return roots[0]
    return os.getcwd()


def _capture_git_info(hook_input: dict) -> tuple[str | None, str | None]:
    """Best-effort (branch, commit) for the session's working directory.
    Never raises: git is an optional detail, the trace is the payload."""
    try:
        branch, commit = git_info(_git_dir(hook_input))
    except Exception:
        log.debug("git info capture failed", exc_info=True)
        return None, None
    return _header_safe(branch), _header_safe(commit)


@dataclass
class RunOptions:
    server_url: str
    token: str
    pr_url: Optional[str] = None
    repo_full_name: Optional[str] = None
    session_id: Optional[str] = None


@dataclass
class RunResult:
    uploaded: bool
    short_id: str | None = None
    trace_url: str | None = None
    skip_reason: str | None = None
    payload_bytes: int | None = None
    upload_elapsed_seconds: float | None = None
    created: bool = True


async def run_share_pipeline(
    *,
    reader,
    hook_input: dict,
    options: RunOptions,
) -> RunResult:
    paths = reader.find_session_paths(hook_input)
    if not paths.main_jsonl.is_file():
        return RunResult(uploaded=False, skip_reason="transcript not found")

    agents = reader.link_subagents(paths, hook_input)
    log.info("found %d subagent(s) for session", len(agents))

    tar_bytes, report = build_bundle(paths.main_jsonl, agents, redact=redact_jsonl)
    payload_bytes = len(tar_bytes)

    git_branch, git_commit = _capture_git_info(hook_input)

    started = time.monotonic()
    try:
        result = await upload_bundle(
            server_url=options.server_url,
            token=options.token,
            tar_bytes=tar_bytes,
            pr_url=options.pr_url,
            repo_full_name=options.repo_full_name,
            plugin_version=PLUGIN_VERSION,
            session_id=options.session_id,
            redaction_count_client=report.total(),
            platform=reader.platform_id(),
            git_branch=git_branch,
            git_commit=git_commit,
        )
    except UploadError as e:
        return RunResult(
            uploaded=False,
            skip_reason=f"upload failed: {e}",
            payload_bytes=payload_bytes,
            upload_elapsed_seconds=time.monotonic() - started,
        )
    elapsed = time.monotonic() - started

    if result.created and options.pr_url:
        try:
            post_pr_comment(
                pr_url=options.pr_url,
                body=build_comment_body(
                    result.trace_url, options.pr_url,
                    platform_label=_platform_label(reader.platform_id()),
                    digest=result.digest,
                ),
            )
        except RuntimeError as e:
            return RunResult(
                uploaded=True,
                short_id=result.short_id,
                trace_url=result.trace_url,
                skip_reason=f"comment failed: {e}",
                payload_bytes=payload_bytes,
                upload_elapsed_seconds=elapsed,
                created=result.created,
            )

    return RunResult(
        uploaded=True,
        short_id=result.short_id,
        trace_url=result.trace_url,
        payload_bytes=payload_bytes,
        upload_elapsed_seconds=elapsed,
        created=result.created,
    )
