"""One-shot backfill: re-digest every trace under the current prompt and
re-index its search documents.

The digest cache hash includes SYSTEM_PROMPT, so traces digested under an
older prompt hash-miss and get a real LLM call; already-migrated traces
skip with SKIP_UNCHANGED, which makes the script resumable (safe to stop
and re-run). A failed LLM call keeps the old digest_json; the API hides
old-shape digests until a later run succeeds.

Run from webapp/backend:  ../../env/bin/python -m scripts.backfill_redigest
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.digest import compute_digest
from app.search.index import index_trace_documents
from app.settings import get_settings
from app.storage.blob import (
    BlobStore,
    LocalDirBlobStore,
    make_azure_blob_store,
)
from app.storage.db import engine_for
from app.storage.models import Trace


async def _first_present(store: BlobStore, *keys: str) -> bytes | None:
    for key in keys:
        try:
            return await store.get(key)
        except FileNotFoundError:
            continue
    return None


async def _subagent_blobs(store: BlobStore, trace: Trace) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for a in trace.agents or []:
        agent_id = a.get("agent_id")
        if not agent_id:
            continue
        blob = await _first_present(
            store,
            f"{trace.blob_prefix}agents/{agent_id}.converted.jsonl",
            f"{trace.blob_prefix}agents/{agent_id}.jsonl",
        )
        if blob is not None:
            out[a.get("tool_use_id") or agent_id] = blob
    return out


async def redigest_all(
    session: AsyncSession, store: BlobStore,
) -> dict[str, int]:
    counts = {
        "redigested": 0,
        "skipped_unchanged": 0,
        "no_digest": 0,
        "no_blob": 0,
        "v1_skipped": 0,
    }
    trace_ids = (await session.execute(
        select(Trace.id).where(Trace.deleted_at.is_(None))
        .order_by(Trace.created_at)
    )).scalars().all()
    for trace_id in trace_ids:
        trace = await session.get(Trace, trace_id)
        if trace is None:
            continue
        if trace.blob_prefix is None:
            counts["v1_skipped"] += 1
            continue
        blob = await _first_present(
            store,
            f"{trace.blob_prefix}converted.jsonl",
            f"{trace.blob_prefix}main.jsonl",
        )
        if blob is None:
            counts["no_blob"] += 1
            continue
        before = trace.digest_input_hash
        digest = await compute_digest(
            session, trace, blob=blob,
            subagent_blobs=await _subagent_blobs(store, trace),
        )
        if digest is None:
            counts["no_digest"] += 1
        elif trace.digest_input_hash == before:
            # Cache hit: prompt-aware hash already matched, no fresh LLM call.
            counts["skipped_unchanged"] += 1
        else:
            counts["redigested"] += 1
        # Re-index only on a real digest; re-indexing a failed re-digest would
        # delete the old summary-body coverage and reinsert strictly less.
        if digest is not None:
            await index_trace_documents(session, trace)
        # Commit per trace so a stopped run keeps its progress.
        await session.commit()
        print(f"{trace.short_id}: {'ok' if digest is not None else 'no digest'}")
    return counts


async def main() -> None:
    settings = get_settings()
    engine = engine_for(settings.database_url)
    store: BlobStore
    if settings.azure_blob_container:
        store = make_azure_blob_store(settings)
    else:
        store = LocalDirBlobStore(settings.blob_dir)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        counts = await redigest_all(session, store)
    print(" ".join(f"{k}={v}" for k, v in counts.items()))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
