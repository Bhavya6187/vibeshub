import uuid

from sqlalchemy import select

from app.storage.models import SearchDocument, Trace


def _trace(**kw) -> Trace:
    defaults = dict(
        short_id="abc12345", owner_login="alice",
        repo_full_name="alice/x", pr_number=1, pr_url=None, pr_title=None,
        platform="claude-code", session_id="s1",
        byte_size=100, message_count=5,
        redaction_count_client=0, redaction_count_server=0,
        is_private=False, blob_path=None, blob_prefix="traces/abc12345/",
        agents=[], agent_count=0,
    )
    defaults.update(kw)
    return Trace(**defaults)


async def test_search_document_round_trip(db_session):
    trace = _trace()
    db_session.add(trace)
    await db_session.flush()

    doc = SearchDocument(
        repo_full_name="alice/x",
        trace_id=trace.id,
        source_type="summary",
        title="Add healthcheck",
        body="ask decisions dead ends",
        anchor_uuid=None,
        pr_number=1,
        pr_url="https://github.com/alice/x/pull/1",
        is_private=False,
    )
    db_session.add(doc)
    await db_session.flush()

    row = (await db_session.execute(select(SearchDocument))).scalar_one()
    assert isinstance(row.id, uuid.UUID)
    assert row.trace_id == trace.id
    assert row.source_type == "summary"
    assert row.created_at is not None
