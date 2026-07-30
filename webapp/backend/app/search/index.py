"""Explode trace digests into search_documents rows.

Indexing is delete-then-insert keyed on trace_id, so digest regeneration
and re-uploads stay consistent and refresh is_private. Never raises out
of index_trace_documents: failures are recorded in agent_run
(agent_name="search_index") and the upload continues, mirroring the
digest agent's failure handling.
"""
from __future__ import annotations

import logging

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._usage import Outcome, record_run
from app.storage.models import SearchDocument, Trace

log = logging.getLogger("vibeshub.search.index")


def explode_digest(trace: Trace) -> list[SearchDocument]:
    """Digest -> unsaved rows: 1 summary + per-item decision/dead_end/
    learning docs + <=10 chapters + 1 files."""
    digest = trace.digest_json or {}
    title = (
        trace.title or trace.pr_title or digest.get("ask") or "Untitled session"
    )
    common = dict(
        repo_full_name=trace.repo_full_name,
        trace_id=trace.id,
        pr_number=trace.pr_number,
        pr_url=trace.pr_url,
        is_private=trace.is_private,
    )
    summary_body = " ".join(
        s for s in (digest.get("ask"), digest.get("tests")) if s
    )
    docs = [SearchDocument(
        source_type="summary", title=title, body=summary_body, **common,
    )]
    for source_type, key in (
        ("decision", "decisions"),
        ("dead_end", "dead_ends"),
        ("learning", "learnings"),
    ):
        items = digest.get(key)
        # Pre-backfill rows hold prose strings here; don't iterate those.
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, str) and item.strip():
                docs.append(SearchDocument(
                    source_type=source_type, title=title, body=item, **common,
                ))
    for ch in digest.get("chapters") or []:
        docs.append(SearchDocument(
            source_type="chapter",
            title=ch.get("title", ""),
            body=ch.get("caption", ""),
            anchor_uuid=ch.get("anchor_uuid"),
            **common,
        ))
    notes = digest.get("file_notes") or []
    if notes:
        body = " ".join(
            f"{n.get('path', '')}: {n.get('caption', '')}" for n in notes
        )
        docs.append(SearchDocument(
            source_type="files", title="Files touched", body=body, **common,
        ))
    return docs


async def index_trace_documents(session: AsyncSession, trace: Trace) -> None:
    """Replace this trace's search docs. Never raises."""
    if trace.repo_full_name is None or trace.digest_json is None:
        return
    try:
        # New traces have no id until flushed; docs need trace.id for the FK.
        await session.flush()
        await session.execute(
            delete(SearchDocument).where(SearchDocument.trace_id == trace.id)
        )
        for doc in explode_digest(trace):
            session.add(doc)
    except Exception as exc:  # noqa: BLE001
        log.warning("search indexing failed for %s: %s", trace.short_id, exc)
        await record_run(
            session, agent_name="search_index", trace_id=trace.short_id,
            model=None, input_tokens=0, output_tokens=0, latency_ms=0,
            outcome=Outcome.FAIL_CALL, error_detail=str(exc)[:500],
        )


async def delete_trace_documents(session: AsyncSession, trace_id) -> None:
    """Remove a trace's docs (used by the soft-delete path)."""
    await session.execute(
        delete(SearchDocument).where(SearchDocument.trace_id == trace_id)
    )
