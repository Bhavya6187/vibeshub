from unittest.mock import MagicMock

import pytest

from app.agents.digest.schema import Digest
from app.storage.blob import LocalDirBlobStore
from app.storage.models import SearchDocument, Trace
from scripts.backfill_redigest import redigest_all
from sqlalchemy import select

SAMPLE_JSONL = (
    b'{"type":"user","uuid":"u1","message":{"content":"Test"}}\n'
    b'{"type":"assistant","uuid":"a1","message":'
    b'{"content":[{"type":"text","text":"Done."}]}}\n'
)

NEW_PAYLOAD = {
    "ask": "test ask",
    "decisions": ["chose test decisions over nothing because test"],
    "dead_ends": [],
    "learnings": ["the fixture trace has only two events"],
    "tests": "none",
    "chapters": [],
}

OLD_DIGEST = {
    "ask": "old ask", "decisions": "old prose", "files": "f",
    "tests": "t", "dead_ends": "old prose", "chapters": [],
}


def _mock_client():
    resp = MagicMock()
    resp.output_parsed = Digest.model_validate(NEW_PAYLOAD)
    resp.usage = MagicMock(input_tokens=5, output_tokens=3)
    client = MagicMock()
    client.responses.parse.return_value = resp
    return client


def _trace(short_id, **kw):
    defaults = dict(
        short_id=short_id, owner_login="alice", repo_full_name="alice/x",
        pr_number=1, pr_url=None, pr_title=None,
        platform="claude-code", session_id=f"s-{short_id}",
        byte_size=100, message_count=2,
        redaction_count_client=0, redaction_count_server=0,
        is_private=False, blob_path=None,
        blob_prefix=f"traces/{short_id}/",
        agents=[], agent_count=0,
        digest_json=dict(OLD_DIGEST), digest_input_hash="old-hash",
    )
    defaults.update(kw)
    return Trace(**defaults)


@pytest.fixture
def _store(tmp_path):
    store = LocalDirBlobStore(tmp_path)
    (tmp_path / "traces" / "abc12345").mkdir(parents=True)
    (tmp_path / "traces" / "abc12345" / "main.jsonl").write_bytes(SAMPLE_JSONL)
    return store


@pytest.fixture
def _env(monkeypatch):
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")


@pytest.mark.asyncio
async def test_redigests_old_traces_and_reindexes(
    db_session, _store, _env, monkeypatch,
):
    client = _mock_client()
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: client,
    )
    trace = _trace("abc12345")
    db_session.add(trace)
    await db_session.flush()

    counts = await redigest_all(db_session, _store)

    assert counts["redigested"] == 1
    assert counts["skipped_unchanged"] == 0
    assert client.responses.parse.call_count == 1
    assert trace.digest_json["decisions"] == NEW_PAYLOAD["decisions"]
    types = {d.source_type for d in (await db_session.execute(
        select(SearchDocument),
    )).scalars().all()}
    assert "decision" in types and "learning" in types


@pytest.mark.asyncio
async def test_second_run_skips_llm_call(
    db_session, _store, _env, monkeypatch,
):
    client = _mock_client()
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: client,
    )
    db_session.add(_trace("abc12345"))
    await db_session.flush()

    await redigest_all(db_session, _store)
    counts = await redigest_all(db_session, _store)

    # Resumable: the prompt-aware hash now matches, so no second LLM call.
    assert client.responses.parse.call_count == 1
    assert counts["skipped_unchanged"] == 1
    assert counts["redigested"] == 0


@pytest.mark.asyncio
async def test_v1_and_blobless_traces_are_counted_not_crashed(
    db_session, _store, _env, monkeypatch,
):
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: _mock_client(),
    )
    db_session.add(_trace("v1trace12", blob_prefix=None, blob_path="old.jsonl"))
    db_session.add(_trace("noblraw12"))  # prefix set but nothing in the store
    await db_session.flush()

    counts = await redigest_all(db_session, _store)

    assert counts["v1_skipped"] == 1
    assert counts["no_blob"] == 1
    assert counts["redigested"] == 0
