from sqlalchemy import select

from app.search.index import (
    delete_trace_documents,
    explode_digest,
    index_trace_documents,
)
from app.storage.models import AgentRun, SearchDocument, Trace

from tests.search.test_model import _trace

DIGEST = {
    "ask": "Add a /healthcheck route",
    "decisions": [
        "Chose an inline route in app/main.py over a separate router because YAGNI",
    ],
    "dead_ends": [
        "Tried a separate APIRouter, abandoned because one route does not justify it",
    ],
    "learnings": [
        "TestClient needs raise_server_exceptions=False to assert 500 responses",
    ],
    "tests": "test_health.py adds /healthcheck assertion",
    "chapters": [
        {"anchor_uuid": "u1", "title": "Frame the change",
         "caption": "User asks for /healthcheck."},
        {"anchor_uuid": "u2", "title": "Implement",
         "caption": "Route added inline."},
    ],
    "file_notes": [
        {"path": "webapp/backend/app/main.py", "caption": "route added"},
    ],
}


def test_explode_digest_yields_item_docs_between_summary_and_chapters():
    trace = _trace(digest_json=DIGEST)
    docs = explode_digest(trace)
    types = [d.source_type for d in docs]
    assert types == [
        "summary", "decision", "dead_end", "learning",
        "chapter", "chapter", "files",
    ]
    summary = docs[0]
    # Summary now carries ask + tests only; items get their own docs so
    # ts_rank does not double-weight them.
    assert "Add a /healthcheck route" in summary.body
    assert "test_health.py adds /healthcheck assertion" in summary.body
    assert "YAGNI" not in summary.body
    assert summary.repo_full_name == "alice/x"
    assert summary.pr_number == 1
    decision = docs[1]
    assert decision.body == DIGEST["decisions"][0]
    assert decision.title == summary.title
    assert docs[2].body == DIGEST["dead_ends"][0]
    assert docs[3].body == DIGEST["learnings"][0]
    chapter = docs[4]
    assert chapter.anchor_uuid == "u1"
    assert chapter.title == "Frame the change"
    files_doc = docs[6]
    assert "webapp/backend/app/main.py" in files_doc.body


def test_explode_digest_old_string_shape_yields_no_item_docs():
    # Pre-backfill rows still hold prose strings; re-index paths (e.g. the
    # trace PATCH resync) must not iterate a string into per-char docs.
    old = {**DIGEST, "decisions": "a prose sentence",
           "dead_ends": "another", "learnings": None}
    docs = explode_digest(_trace(digest_json=old))
    assert [d.source_type for d in docs] == [
        "summary", "chapter", "chapter", "files",
    ]


def test_explode_digest_no_chapters_still_yields_summary():
    digest = {**DIGEST, "chapters": [], "file_notes": [],
              "decisions": [], "dead_ends": [], "learnings": []}
    docs = explode_digest(_trace(digest_json=digest))
    assert [d.source_type for d in docs] == ["summary"]


async def test_index_is_delete_then_insert(db_session):
    trace = _trace(digest_json=DIGEST)
    db_session.add(trace)
    await index_trace_documents(db_session, trace)
    first = (await db_session.execute(select(SearchDocument))).scalars().all()
    assert len(first) == 7

    trace.is_private = True
    await index_trace_documents(db_session, trace)
    rows = (await db_session.execute(select(SearchDocument))).scalars().all()
    assert len(rows) == 7
    assert all(r.is_private for r in rows)


async def test_index_skips_without_digest_or_repo(db_session):
    t1 = _trace(short_id="nodig123", session_id="s2", digest_json=None)
    t2 = _trace(short_id="norepo12", session_id="s3",
                repo_full_name=None, pr_number=None, digest_json=DIGEST)
    db_session.add_all([t1, t2])
    await index_trace_documents(db_session, t1)
    await index_trace_documents(db_session, t2)
    rows = (await db_session.execute(select(SearchDocument))).scalars().all()
    assert rows == []


async def test_index_failure_recorded_never_raises(db_session, monkeypatch):
    trace = _trace(digest_json=DIGEST)
    db_session.add(trace)

    def boom(_trace):
        raise RuntimeError("explode failed")

    monkeypatch.setattr("app.search.index.explode_digest", boom)
    await index_trace_documents(db_session, trace)  # must not raise
    runs = (await db_session.execute(
        select(AgentRun).where(AgentRun.agent_name == "search_index")
    )).scalars().all()
    assert len(runs) == 1
    assert runs[0].outcome == "fail_call"


async def test_delete_trace_documents(db_session):
    trace = _trace(digest_json=DIGEST)
    db_session.add(trace)
    await index_trace_documents(db_session, trace)
    await delete_trace_documents(db_session, trace.id)
    rows = (await db_session.execute(select(SearchDocument))).scalars().all()
    assert rows == []
