"""Shared trace-creation service.

`create_or_update_trace` is the single place that writes trace blobs and
performs the session-id upsert. Both ingest paths — `/api/ingest` (CLI tar
uploads) and the future `/api/uploads` (web multipart uploads) — call it so
the storage layout, the redaction-count bookkeeping, and the upsert rule
stay identical across paths.

Upsert rule: a re-upload carrying the same `session_id` refreshes that
session's existing trace (stable short_id / URL) instead of inserting a new
row. For a repo-associated upload the match is scoped to
`(repo_full_name, pr_number, session_id)`; for a standalone upload (repo and
PR both None) the match is `session_id` alone among the uploader's own
standalone, non-deleted traces. A null `session_id` always creates a fresh
trace. A soft-deleted trace (`deleted_at` set) is never resurrected.

This is a best-effort select-then-update — there is no unique constraint —
but `session_id` is unique per Claude Code session and its upload hook is
synchronous, so concurrent same-session uploads do not occur in practice.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.pr_url import parse_pr_url
from app.auth.github import GitHubAPIError, GitHubClient
from app.convert import IMPORTED_FORMATS, convert_imported, sniff_import_format
from app.message_count import count_messages
from app.redact.bundle import UnpackedBundle
from app.short_id import generate
from app.storage.blob import BlobStore
from app.storage.models import Trace


@dataclass
class TraceWriteResult:
    trace: Trace
    created: bool


async def _find_existing(
    session: AsyncSession,
    *,
    owner_login: str,
    repo_full_name: str | None,
    pr_number: int | None,
    session_id: str | None,
) -> Trace | None:
    """Return the trace this upload should refresh, or None to create one."""
    if not session_id:
        return None
    stmt = select(Trace).where(
        Trace.session_id == session_id,
        Trace.deleted_at.is_(None),
    )
    # Invariant: ingest always sets repo_full_name and pr_number together
    # (both present for a repo-associated upload, both None for a standalone
    # one). Checking both keeps this consistent with traces.py, which treats
    # repo_full_name is None as the sole standalone marker.
    if repo_full_name is not None and pr_number is not None:
        # Repo-associated: scope the match to this exact PR (today's rule).
        stmt = stmt.where(
            Trace.repo_full_name == repo_full_name,
            Trace.pr_number == pr_number,
        )
    else:
        # Standalone: match this uploader's own standalone traces only.
        stmt = stmt.where(
            Trace.owner_login == owner_login,
            Trace.repo_full_name.is_(None),
        )
    stmt = stmt.order_by(Trace.created_at.desc())
    return (await session.execute(stmt)).scalars().first()


async def create_or_update_trace(
    *,
    session: AsyncSession,
    blob_store: BlobStore,
    unpacked: UnpackedBundle,
    owner_login: str | None,
    platform: str,
    plugin_version: str | None,
    session_id: str | None,
    redaction_count_client: int,
    repo_full_name: str | None,
    pr_number: int | None,
    pr_url: str | None,
    pr_title: str | None,
    is_private: bool,
    source_export_bytes: bytes | None = None,
    source_format: str | None = None,
    claim_token_hash: str | None = None,
    git_branch: str | None = None,
    git_commit: str | None = None,
) -> TraceWriteResult:
    """Write the bundle's blobs and create or refresh the matching Trace row.

    The caller owns the transaction — this function adds/mutates the row and
    writes blobs but does NOT commit.
    """
    # An anonymous upload (owner_login None) has no uploader to scope the
    # session-id upsert to, and a null session_id never matches anyway, so
    # distinct anonymous uploads must always create distinct rows. Skip the
    # lookup entirely; only signed-in uploads can refresh an existing trace.
    if owner_login is None:
        existing = None
    else:
        existing = await _find_existing(
            session,
            owner_login=owner_login,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            session_id=session_id,
        )

    created = existing is None
    sid = existing.short_id if existing is not None else generate()
    blob_prefix = f"traces/{sid}/"
    await blob_store.put(f"{blob_prefix}main.jsonl", unpacked.main_bytes)
    if source_export_bytes is not None:
        # Archived (already redacted by the caller) so an improved converter
        # can re-parse the original export later.
        await blob_store.put(
            f"{blob_prefix}source_export.txt", source_export_bytes
        )

    # Imported formats (Codex rollouts, Cursor transcripts) additionally get
    # a Claude-shaped converted copy stored next to the raw original. The
    # viewer and the digest read the converted copy; the raw stays the
    # canonical original. Bytes here are already redacted (bundle unpack
    # redacts before this function runs), so the converted copy is too.
    if source_format is None:
        source_format = sniff_import_format(unpacked.main_bytes)
    converted_main: bytes | None = None
    if source_format in IMPORTED_FORMATS:
        converted_main = convert_imported(unpacked.main_bytes)
    if converted_main is not None:
        await blob_store.put(f"{blob_prefix}converted.jsonl", converted_main)

    agent_summaries: list[dict] = []
    converted_agents: dict[str, bytes] = {}
    for agent in unpacked.agents:
        await blob_store.put(
            f"{blob_prefix}agents/{agent.agent_id}.jsonl",
            agent.jsonl_bytes,
        )
        # Sniffed per blob: a codex/cursor trace's subagent threads are
        # stored in the same native format as its main transcript.
        converted = convert_imported(agent.jsonl_bytes)
        if converted is not None:
            converted_agents[agent.agent_id] = converted
            await blob_store.put(
                f"{blob_prefix}agents/{agent.agent_id}.converted.jsonl",
                converted,
            )
        await blob_store.put(
            f"{blob_prefix}agents/{agent.agent_id}.meta.json",
            json.dumps(agent.meta, ensure_ascii=False).encode("utf-8"),
        )
        agent_summaries.append({
            "agent_id": agent.agent_id,
            "tool_use_id": agent.meta.get("toolUseId"),
            "agent_type": agent.meta["agentType"],
            "description": agent.meta["description"],
            "message_count": count_messages(agent.jsonl_bytes),
        })

    message_count_main = count_messages(unpacked.main_bytes)
    byte_size = len(unpacked.main_bytes) + sum(
        len(a.jsonl_bytes) for a in unpacked.agents
    )

    if existing is not None:
        trace = existing
        trace.repo_full_name = repo_full_name
        trace.pr_number = pr_number
        trace.pr_url = pr_url
        trace.pr_title = pr_title
        trace.platform = platform
        trace.plugin_version = plugin_version
        trace.byte_size = byte_size
        trace.message_count = message_count_main
        trace.redaction_count_client = redaction_count_client
        trace.redaction_count_server = unpacked.total_redactions
        trace.is_private = is_private
        trace.blob_path = None
        trace.blob_prefix = blob_prefix
        trace.agents = agent_summaries
        trace.agent_count = len(agent_summaries)
        trace.source_format = source_format
        trace.git_branch = git_branch
        trace.git_commit = git_commit
    else:
        trace = Trace(
            short_id=sid,
            owner_login=owner_login,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            pr_url=pr_url,
            pr_title=pr_title,
            platform=platform,
            plugin_version=plugin_version,
            session_id=session_id,
            byte_size=byte_size,
            message_count=message_count_main,
            redaction_count_client=redaction_count_client,
            redaction_count_server=unpacked.total_redactions,
            is_private=is_private,
            blob_path=None,
            blob_prefix=blob_prefix,
            agents=agent_summaries,
            agent_count=len(agent_summaries),
            source_format=source_format,
            claim_token_hash=claim_token_hash,
            git_branch=git_branch,
            git_commit=git_commit,
        )
        session.add(trace)

    # Trace digest agent — best-effort, never blocks the upload.
    # compute_digest already catches its own LLM/Pydantic failures; the
    # outer try guards against unexpected exceptions in distillation
    # (e.g. malformed event shapes) so the upload always succeeds.
    from app.agents.digest import compute_digest
    try:
        await compute_digest(
            session,
            trace,
            blob=(
                converted_main
                if converted_main is not None else unpacked.main_bytes
            ),
            subagent_blobs={
                a.meta.get("toolUseId", a.agent_id):
                    converted_agents.get(a.agent_id, a.jsonl_bytes)
                for a in unpacked.agents
            },
        )
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("vibeshub.agents.digest").exception(
            "compute_digest raised unexpectedly; upload continues",
        )

    return TraceWriteResult(trace=trace, created=created)


@dataclass(frozen=True)
class ResolvedAssociation:
    repo_full_name: str | None
    pr_number: int | None
    pr_url: str | None
    pr_title: str | None
    is_private: bool


_STANDALONE = ResolvedAssociation(
    repo_full_name=None, pr_number=None, pr_url=None,
    pr_title=None, is_private=False,
)


def _parse_repo_full_name(value: str) -> tuple[str, str]:
    parts = value.strip().split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise HTTPException(
            status_code=400, detail=f"invalid repo: {value}"
        )
    return parts[0], parts[1]


async def resolve_association(
    *,
    github: GitHubClient,
    token: str,
    uploader_login: str,
    pr_url: str | None,
    repo_full_name: str | None,
) -> ResolvedAssociation:
    """Resolve an optional PR/repo association for an upload.

    PR wins over repo. Verifies the uploader is the PR author (PR path) or
    a repo collaborator (repo-only path), snapshotting repo visibility.
    Raises HTTPException (400/403/404/502) on failure; returns a standalone
    association when neither is given.
    """
    if pr_url:
        try:
            parsed = parse_pr_url(pr_url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        try:
            pr = await github.get_pull(
                token, parsed.owner, parsed.repo, parsed.number
            )
        except GitHubAPIError as e:
            msg = str(e)
            if "not found" in msg.lower():
                raise HTTPException(
                    status_code=404, detail=f"PR not found: {pr_url}"
                )
            raise HTTPException(
                status_code=502, detail=f"github upstream error: {msg}"
            )
        if pr.author_login != uploader_login:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"PR author ({pr.author_login}) does not match "
                    f"uploader ({uploader_login})"
                ),
            )
        return ResolvedAssociation(
            repo_full_name=pr.repo_full_name,
            pr_number=pr.number,
            pr_url=pr.html_url,
            pr_title=pr.title,
            is_private=pr.repo_is_private,
        )

    if repo_full_name:
        owner, repo = _parse_repo_full_name(repo_full_name)
        try:
            perm = await github.get_repo_permission(
                token, owner, repo, uploader_login
            )
        except GitHubAPIError as e:
            msg = str(e)
            if "not found" in msg.lower():
                raise HTTPException(
                    status_code=404,
                    detail=f"repo not found: {repo_full_name}",
                )
            raise HTTPException(
                status_code=502, detail=f"github upstream error: {msg}"
            )
        if not perm.is_collaborator:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"{uploader_login} is not a collaborator on "
                    f"{repo_full_name}"
                ),
            )
        try:
            info = await github.get_repo(token, owner, repo)
        except GitHubAPIError as e:
            raise HTTPException(
                status_code=502, detail=f"github upstream error: {e}"
            )
        return ResolvedAssociation(
            repo_full_name=info.full_name,
            pr_number=None,
            pr_url=None,
            pr_title=None,
            is_private=info.is_private,
        )

    return _STANDALONE
