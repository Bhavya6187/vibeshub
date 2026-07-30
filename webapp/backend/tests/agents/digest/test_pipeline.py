from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.agents._usage import Outcome
from app.agents.digest.pipeline import compute_digest
from app.agents.digest.schema import Digest
from app.codex_convert import codex_to_claude_jsonl
from app.storage.models import AgentRun, Trace


def _ok_response(payload: dict):
    """Mock the shape of an OpenAI responses.parse result.

    The SDK returns an already-validated model on .output_parsed when the
    request uses Structured Outputs (text_format=Digest).
    """
    resp = MagicMock()
    resp.output_parsed = Digest.model_validate(payload)
    resp.usage = MagicMock(input_tokens=42, output_tokens=10)
    return resp


VALID_PAYLOAD = {
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
        {"anchor_uuid": "bogus", "title": "Drop me",
         "caption": "Anchor not in trace."},
    ],
}


@pytest.fixture
def _trace_blob():
    # Reuse the short fixture from the distill tests
    from pathlib import Path
    return (
        Path(__file__).parent / "fixtures" / "short.jsonl"
    ).read_bytes()


@pytest.fixture
async def _seeded_trace(db_session):
    trace = Trace(
        short_id="abc12345",
        owner_login="alice",
        repo_full_name="alice/x",
        pr_number=1, pr_url=None, pr_title=None,
        platform="claude-code", session_id="s1",
        byte_size=100, message_count=5,
        redaction_count_client=0, redaction_count_server=0,
        is_private=False,
        blob_path=None, blob_prefix="traces/abc12345/",
        agents=[], agent_count=0,
    )
    db_session.add(trace)
    await db_session.flush()
    return trace


@pytest.mark.asyncio
async def test_happy_path_persists_digest_and_records_run(
    monkeypatch, db_session, _trace_blob, _seeded_trace,
):
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")
    mock_client = MagicMock()
    mock_client.responses.parse.return_value = _ok_response(VALID_PAYLOAD)
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock_client,
    )

    digest = await compute_digest(
        db_session, _seeded_trace,
        blob=_trace_blob, subagent_blobs={},
    )

    assert isinstance(digest, Digest)
    assert digest.ask == "Add a /healthcheck route"
    # The bogus chapter was filtered out; only the valid one survives
    assert [c.anchor_uuid for c in digest.chapters] == ["u1"]

    # Trace row updated
    assert _seeded_trace.digest_json is not None
    assert _seeded_trace.digest_json["ask"] == "Add a /healthcheck route"
    assert _seeded_trace.digest_input_hash is not None
    assert len(_seeded_trace.digest_input_hash) == 64  # sha256 hex

    # AgentRun row written
    rows = (await db_session.execute(
        select(AgentRun).where(AgentRun.agent_name == "digest"),
    )).scalars().all()
    assert len(rows) == 1
    r = rows[0]
    assert r.outcome == Outcome.OK.value
    assert r.input_tokens == 42
    assert r.output_tokens == 10
    assert r.extra == {"chapters_kept": 1, "chapters_total": 2,
                       "distill_truncated": False,
                       "file_notes_kept": 0, "file_notes_total": 0}


@pytest.mark.asyncio
async def test_call_uses_structured_output_format(
    monkeypatch, db_session, _trace_blob, _seeded_trace,
):
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")
    mock_client = MagicMock()
    mock_client.responses.parse.return_value = _ok_response(VALID_PAYLOAD)
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock_client,
    )
    await compute_digest(
        db_session, _seeded_trace,
        blob=_trace_blob, subagent_blobs={},
    )
    kwargs = mock_client.responses.parse.call_args.kwargs
    # Structured Outputs: the Digest model drives a strict json_schema,
    # so no manual text=json_object format and no "json" reminder needed.
    assert kwargs["text_format"] is Digest
    assert "text" not in kwargs
    assert kwargs["model"] == "gpt-5.5"
    assert "instructions" in kwargs
    # Input is the raw distilled trace, no appended json-keyword reminder.
    assert isinstance(kwargs["input"], str) and kwargs["input"]
    assert "matching the schema in the instructions" not in kwargs["input"]


@pytest.mark.asyncio
async def test_no_config_skips_call_and_returns_none(
    monkeypatch, db_session, _trace_blob, _seeded_trace,
):
    monkeypatch.delenv("VIBESHUB_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VIBESHUB_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("VIBESHUB_OPENAI_MODEL", raising=False)
    result = await compute_digest(
        db_session, _seeded_trace,
        blob=_trace_blob, subagent_blobs={},
    )
    assert result is None
    rows = (await db_session.execute(select(AgentRun))).scalars().all()
    assert len(rows) == 1
    assert rows[0].outcome == Outcome.SKIP_NO_CONFIG.value
    assert _seeded_trace.digest_json is None


@pytest.mark.asyncio
async def test_call_failure_records_fail_call(
    monkeypatch, db_session, _trace_blob, _seeded_trace,
):
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")
    mock_client = MagicMock()
    mock_client.responses.parse.side_effect = RuntimeError("502")
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock_client,
    )
    result = await compute_digest(
        db_session, _seeded_trace,
        blob=_trace_blob, subagent_blobs={},
    )
    assert result is None
    rows = (await db_session.execute(select(AgentRun))).scalars().all()
    assert rows[0].outcome == Outcome.FAIL_CALL.value
    assert "502" in (rows[0].error_detail or "")


@pytest.mark.asyncio
async def test_no_parsed_output_records_fail_schema(
    monkeypatch, db_session, _trace_blob, _seeded_trace,
):
    # With Structured Outputs the shape is guaranteed, so the only way to
    # get no usable Digest is a refusal / empty completion: output_parsed
    # is None. The pipeline must record FAIL_SCHEMA, not crash.
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")
    mock_client = MagicMock()
    bad = MagicMock()
    bad.output_parsed = None
    bad.output_text = "I can't help with that."
    bad.usage = MagicMock(input_tokens=10, output_tokens=2)
    mock_client.responses.parse.return_value = bad
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock_client,
    )
    result = await compute_digest(
        db_session, _seeded_trace,
        blob=_trace_blob, subagent_blobs={},
    )
    assert result is None
    rows = (await db_session.execute(select(AgentRun))).scalars().all()
    assert rows[0].outcome == Outcome.FAIL_SCHEMA.value
    assert "I can't help with that." in (rows[0].error_detail or "")


@pytest.mark.asyncio
async def test_em_dash_is_stripped_before_persist(
    monkeypatch, db_session, _trace_blob, _seeded_trace,
):
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")
    payload = dict(VALID_PAYLOAD)
    payload["ask"] = "fix oauth — clean up scopes"
    mock_client = MagicMock()
    mock_client.responses.parse.return_value = _ok_response(payload)
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock_client,
    )
    digest = await compute_digest(
        db_session, _seeded_trace,
        blob=_trace_blob, subagent_blobs={},
    )
    assert "—" not in digest.ask
    assert "fix oauth, clean up scopes" == digest.ask


@pytest.mark.asyncio
async def test_idempotency_skips_call_on_unchanged_input(
    monkeypatch, db_session, _trace_blob, _seeded_trace,
):
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")
    mock_client = MagicMock()
    mock_client.responses.parse.return_value = _ok_response(VALID_PAYLOAD)
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock_client,
    )
    # First call: live LLM
    first = await compute_digest(
        db_session, _seeded_trace,
        blob=_trace_blob, subagent_blobs={},
    )
    assert mock_client.responses.parse.call_count == 1
    assert first is not None
    # Second call with same blob: skipped
    second = await compute_digest(
        db_session, _seeded_trace,
        blob=_trace_blob, subagent_blobs={},
    )
    assert mock_client.responses.parse.call_count == 1  # no extra call
    assert second is not None
    assert second.ask == first.ask
    rows = (await db_session.execute(
        select(AgentRun).order_by(AgentRun.created_at),
    )).scalars().all()
    assert [r.outcome for r in rows] == [
        Outcome.OK.value, Outcome.SKIP_UNCHANGED.value,
    ]


@pytest.fixture
def _codex_blob():
    from pathlib import Path
    return (
        Path(__file__).parent.parent.parent / "fixtures" / "codex"
        / "rollout.jsonl"
    ).read_bytes()


@pytest.mark.asyncio
async def test_converted_codex_blob_digests_with_codex_rec_anchors(
    monkeypatch, db_session, _codex_blob, _seeded_trace,
):
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")
    payload = dict(VALID_PAYLOAD)
    payload["chapters"] = [
        {"anchor_uuid": "codex-rec-1", "title": "Frame the change",
         "caption": "User asks for a greet helper."},
    ]
    mock_client = MagicMock()
    mock_client.responses.parse.return_value = _ok_response(payload)
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock_client,
    )

    digest = await compute_digest(
        db_session, _seeded_trace,
        blob=codex_to_claude_jsonl(_codex_blob), subagent_blobs={},
    )

    assert digest is not None
    sent = mock_client.responses.parse.call_args.kwargs["input"]
    # trace_service converts at ingest; the anchor surface uses the
    # synthetic codex-rec uuids the viewer resolves against the served
    # converted blob.
    assert "USER: Add a greet function" in sent
    assert "[codex-rec-1]" in sent
    assert [c.anchor_uuid for c in digest.chapters] == ["codex-rec-1"]
    assert _seeded_trace.digest_json is not None


@pytest.mark.asyncio
async def test_converted_codex_subagent_blob_is_summarized(
    monkeypatch, db_session, _codex_blob, _seeded_trace,
):
    from pathlib import Path
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")
    mock_client = MagicMock()
    mock_client.responses.parse.return_value = _ok_response(VALID_PAYLOAD)
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock_client,
    )
    child = (
        Path(__file__).parent.parent.parent / "fixtures" / "codex"
        / "rollout_subagent.jsonl"
    ).read_bytes()

    await compute_digest(
        db_session, _seeded_trace,
        blob=codex_to_claude_jsonl(_codex_blob),
        subagent_blobs={"c_spawn": codex_to_claude_jsonl(child)},
    )

    sent = mock_client.responses.parse.call_args.kwargs["input"]
    # The child rollout (also codex-shaped) was converted too, so the
    # Tier-3 heuristic finds its final assistant text.
    assert "Subagent[default]: Review src/util.ts" in sent
    assert "The greet helper handles the common case" in sent


@pytest.mark.asyncio
async def test_raw_codex_blob_is_no_longer_converted_by_pipeline(
    monkeypatch, db_session, _codex_blob, _seeded_trace,
):
    # Conversion moved to ingest (trace_service); a raw codex blob fed
    # straight to the pipeline distills to nothing and records skip_empty.
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")
    mock_client = MagicMock()
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock_client,
    )

    result = await compute_digest(
        db_session, _seeded_trace, blob=_codex_blob, subagent_blobs={},
    )

    assert result is None
    assert mock_client.responses.parse.call_count == 0


@pytest.mark.asyncio
async def test_empty_distillate_records_skip_empty(
    monkeypatch, db_session, _seeded_trace,
):
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")
    mock_client = MagicMock()
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock_client,
    )
    # Tier-4-only content distills to nothing; the run must still be
    # visible in agent_run rather than silently returning None.
    blob = b'{"type":"ai-title","aiTitle":"x"}\n'

    result = await compute_digest(
        db_session, _seeded_trace, blob=blob, subagent_blobs={},
    )

    assert result is None
    assert mock_client.responses.parse.call_count == 0
    rows = (await db_session.execute(select(AgentRun))).scalars().all()
    assert len(rows) == 1
    assert rows[0].outcome == Outcome.SKIP_EMPTY.value


@pytest.mark.asyncio
async def test_file_notes_unknown_path_dropped_and_em_dash_swept(
    monkeypatch, db_session, _trace_blob, _seeded_trace,
):
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")
    payload = dict(VALID_PAYLOAD)
    payload["file_notes"] = [
        {"path": "webapp/backend/app/main.py",
         "caption": "Add the route — wire it in"},
        {"path": "not/edited.py", "caption": "Phantom file"},
    ]
    mock_client = MagicMock()
    mock_client.responses.parse.return_value = _ok_response(payload)
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock_client,
    )

    digest = await compute_digest(
        db_session, _seeded_trace, blob=_trace_blob, subagent_blobs={},
    )

    assert [n.path for n in digest.file_notes] == ["webapp/backend/app/main.py"]
    assert "—" not in digest.file_notes[0].caption
    assert digest.file_notes[0].caption == "Add the route, wire it in"
    rows = (await db_session.execute(
        select(AgentRun).where(AgentRun.agent_name == "digest"),
    )).scalars().all()
    assert rows[0].extra["file_notes_kept"] == 1
    assert rows[0].extra["file_notes_total"] == 2


@pytest.mark.asyncio
async def test_prompt_change_invalidates_cached_digest(
    monkeypatch, db_session, _trace_blob, _seeded_trace,
):
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")
    mock_client = MagicMock()
    mock_client.responses.parse.return_value = _ok_response(VALID_PAYLOAD)
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock_client,
    )
    await compute_digest(
        db_session, _seeded_trace, blob=_trace_blob, subagent_blobs={},
    )
    assert mock_client.responses.parse.call_count == 1

    # Same input, edited prompt: the cache must miss and re-call the LLM.
    monkeypatch.setattr(
        "app.agents.digest.pipeline.SYSTEM_PROMPT", "a different prompt",
    )
    await compute_digest(
        db_session, _seeded_trace, blob=_trace_blob, subagent_blobs={},
    )
    assert mock_client.responses.parse.call_count == 2


@pytest.mark.asyncio
async def test_em_dash_swept_from_list_items(
    monkeypatch, db_session, _trace_blob, _seeded_trace,
):
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")
    payload = dict(VALID_PAYLOAD)
    payload["decisions"] = ["chose A — over B"]
    payload["learnings"] = ["hook cwd — always ~/.cursor"]
    mock_client = MagicMock()
    mock_client.responses.parse.return_value = _ok_response(payload)
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock_client,
    )
    digest = await compute_digest(
        db_session, _seeded_trace, blob=_trace_blob, subagent_blobs={},
    )
    assert digest.decisions == ["chose A, over B"]
    assert digest.learnings == ["hook cwd, always ~/.cursor"]
