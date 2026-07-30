"""One-shot backfill: index search documents for traces that already
have digests. Idempotent (indexing is delete-then-insert per trace).

Run from webapp/backend:  ../../env/bin/python -m scripts.backfill_search_documents
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.search.index import index_trace_documents
from app.settings import get_settings
from app.storage.db import engine_for
from app.storage.models import Trace


async def main() -> None:
    engine = engine_for(get_settings().database_url)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Trace).where(
                Trace.deleted_at.is_(None),
                Trace.repo_full_name.is_not(None),
                Trace.digest_json.is_not(None),
            )
        )).scalars().all()
        for trace in rows:
            await index_trace_documents(session, trace)
        await session.commit()
    print(f"indexed {len(rows)} traces")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
