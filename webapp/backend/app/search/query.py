"""Repo-scoped retrieval over search_documents.

Postgres: FTS over the generated search_tsv column (websearch_to_tsquery,
ts_rank ordering, ts_headline snippets). SQLite (dev/tests): AND-of-LIKE
terms with a naive snippet. Only the retrieval primitive differs; callers
see the same SearchHit shape.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.models import SearchDocument, Trace


@dataclass
class SearchHit:
    source_type: str
    title: str
    snippet: str
    trace_short_id: str
    anchor_uuid: str | None
    pr_number: int | None
    created_at: str


_PG_SQL = """
SELECT sd.source_type, sd.title, sd.anchor_uuid, sd.pr_number,
       t.short_id, t.created_at,
       ts_headline('english', sd.body,
                   websearch_to_tsquery('english', :q),
                   'MaxFragments=1, MaxWords=30, MinWords=10') AS snippet
FROM search_documents sd
JOIN traces t ON t.id = sd.trace_id
WHERE sd.repo_full_name = :repo
  AND t.deleted_at IS NULL
  AND (:include_private OR NOT sd.is_private)
  AND sd.search_tsv @@ websearch_to_tsquery('english', :q)
ORDER BY ts_rank(sd.search_tsv, websearch_to_tsquery('english', :q)) DESC
LIMIT :limit
"""


def _snippet(body: str, term: str, width: int = 240) -> str:
    idx = body.lower().find(term.lower())
    if idx < 0:
        return body[:width]
    start = max(0, idx - width // 2)
    return body[start:start + width]


async def search_documents(
    session: AsyncSession,
    *,
    repo_full_name: str,
    query: str,
    include_private: bool,
    limit: int = 10,
) -> list[SearchHit]:
    query = query.strip()
    if not query:
        return []
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        return await _search_pg(
            session, repo_full_name=repo_full_name, query=query,
            include_private=include_private, limit=limit,
        )
    return await _search_like(
        session, repo_full_name=repo_full_name, query=query,
        include_private=include_private, limit=limit,
    )


async def _search_pg(
    session, *, repo_full_name, query, include_private, limit,
) -> list[SearchHit]:
    rows = (await session.execute(
        text(_PG_SQL),
        {
            "q": query, "repo": repo_full_name,
            "include_private": include_private, "limit": limit,
        },
    )).all()
    return [
        SearchHit(
            source_type=r.source_type, title=r.title, snippet=r.snippet or "",
            trace_short_id=r.short_id, anchor_uuid=r.anchor_uuid,
            pr_number=r.pr_number, created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


async def _search_like(
    session, *, repo_full_name, query, include_private, limit,
) -> list[SearchHit]:
    terms = [t for t in query.lower().split() if t]
    stmt = (
        select(SearchDocument, Trace)
        .join(Trace, SearchDocument.trace_id == Trace.id)
        .where(
            SearchDocument.repo_full_name == repo_full_name,
            Trace.deleted_at.is_(None),
        )
    )
    if not include_private:
        stmt = stmt.where(SearchDocument.is_private.is_(False))
    haystack = func.lower(
        SearchDocument.title + " " + SearchDocument.body
    )
    for term in terms:
        stmt = stmt.where(haystack.like(f"%{term}%"))
    stmt = stmt.order_by(Trace.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).all()
    return [
        SearchHit(
            source_type=doc.source_type,
            title=doc.title,
            snippet=_snippet(f"{doc.title} {doc.body}", terms[0]),
            trace_short_id=trace.short_id,
            anchor_uuid=doc.anchor_uuid,
            pr_number=doc.pr_number,
            created_at=trace.created_at.isoformat(),
        )
        for doc, trace in rows
    ]
