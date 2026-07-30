from app.search.index import index_trace_documents
from app.search.query import SearchHit, search_documents

from tests.search.test_index import DIGEST
from tests.search.test_model import _trace


async def _seed(db_session, **trace_kw):
    trace = _trace(digest_json=DIGEST, **trace_kw)
    db_session.add(trace)
    await index_trace_documents(db_session, trace)
    return trace


async def test_like_search_finds_summary_terms(db_session):
    await _seed(db_session)
    hits = await search_documents(
        db_session, repo_full_name="alice/x",
        query="healthcheck route", include_private=False,
    )
    assert hits
    assert isinstance(hits[0], SearchHit)
    assert hits[0].trace_short_id == "abc12345"
    assert "healthcheck" in hits[0].snippet.lower()


async def test_search_is_repo_scoped(db_session):
    await _seed(db_session)
    hits = await search_documents(
        db_session, repo_full_name="bob/other",
        query="healthcheck", include_private=False,
    )
    assert hits == []


async def test_private_docs_hidden_unless_included(db_session):
    await _seed(db_session, is_private=True)
    public = await search_documents(
        db_session, repo_full_name="alice/x",
        query="healthcheck", include_private=False,
    )
    assert public == []
    private = await search_documents(
        db_session, repo_full_name="alice/x",
        query="healthcheck", include_private=True,
    )
    assert private


async def test_soft_deleted_trace_hits_excluded(db_session):
    from app.storage.models import utcnow

    trace = await _seed(db_session)
    trace.deleted_at = utcnow()
    hits = await search_documents(
        db_session, repo_full_name="alice/x",
        query="healthcheck", include_private=True,
    )
    assert hits == []


async def test_all_terms_must_match(db_session):
    await _seed(db_session)
    hits = await search_documents(
        db_session, repo_full_name="alice/x",
        query="healthcheck zebra", include_private=False,
    )
    assert hits == []


async def test_chapter_hit_carries_anchor(db_session):
    await _seed(db_session)
    hits = await search_documents(
        db_session, repo_full_name="alice/x",
        query="frame the change", include_private=False,
    )
    assert any(h.anchor_uuid == "u1" for h in hits)


def test_pg_sql_uses_websearch_tsquery():
    from app.search.query import _PG_SQL

    assert "websearch_to_tsquery" in _PG_SQL
    assert "ts_rank" in _PG_SQL
    assert "ts_headline" in _PG_SQL
    assert "deleted_at IS NULL" in _PG_SQL
