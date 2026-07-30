"""End-to-end-ish test: upload a trace and verify the digest pipeline ran.

The OpenAI client is patched at the seam so this never hits the network.
The rest (redaction, blob store, DB persistence, response serialization)
is real.
"""
import io
import json
import tarfile
from unittest.mock import MagicMock

import pytest


SAMPLE_JSONL = (
    b'{"type":"user","uuid":"u1","message":{"content":"Test"}}\n'
    b'{"type":"assistant","uuid":"a1","message":'
    b'{"content":[{"type":"text","text":"Done."}]}}\n'
)


def _make_bundle(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _ingest_headers(pr_url: str) -> dict[str, str]:
    return {
        "X-Vibeshub-Pr-Url": pr_url,
        "X-Vibeshub-Platform": "claude-code",
        "X-Vibeshub-Plugin-Version": "0.2.0",
        "X-Vibeshub-Client-Redactions": "0",
        "Content-Type": "application/x-tar",
        "Authorization": "Bearer ghp_test",
    }


def _mock_alice_pr(respx_mock, owner: str, repo: str, number: int) -> None:
    respx_mock.get("https://api.github.test/user").respond(
        200, json={"login": "alice", "id": 7},
    )
    respx_mock.get(
        f"https://api.github.test/repos/{owner}/{repo}/pulls/{number}"
    ).respond(
        200,
        json={
            "number": number, "title": "End-to-end", "user": {"login": "alice"},
            "html_url": f"https://github.com/{owner}/{repo}/pull/{number}",
            "head": {"repo": {"private": False, "full_name": f"{owner}/{repo}"}},
            "base": {"repo": {"private": False, "full_name": f"{owner}/{repo}"}},
        },
    )


@pytest.fixture
def _digest_env(monkeypatch):
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")


@pytest.fixture
def _patch_llm(monkeypatch):
    from app.agents.digest.schema import Digest

    mock = MagicMock()
    payload = {
        "ask": "test ask",
        "decisions": ["chose test decisions over nothing because test"],
        "dead_ends": ["tried a shortcut, abandoned because it failed"],
        "learnings": ["the fixture trace has only two events"],
        "tests": "test tests",
        "chapters": [],
    }
    resp = MagicMock()
    # The pipeline uses responses.parse (Structured Outputs), which
    # returns an already-validated Digest on output_parsed.
    resp.output_parsed = Digest.model_validate(payload)
    resp.output_text = json.dumps(payload)
    resp.usage = MagicMock(input_tokens=5, output_tokens=3)
    mock.responses.parse.return_value = resp
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock,
    )
    return mock


@pytest.mark.asyncio
async def test_upload_runs_digest_and_returns_it(
    client, respx_mock, _digest_env, _patch_llm,
):
    """POST a trace via /api/ingest. Assert the response contains a
    digest, the trace row stores it, and the LLM was called once.
    """
    _mock_alice_pr(respx_mock, "alice", "repo", 7)
    r = client.post(
        "/api/ingest",
        content=_make_bundle({"main.jsonl": SAMPLE_JSONL}),
        headers=_ingest_headers("https://github.com/alice/repo/pull/7"),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    short_id = body["short_id"]
    assert body.get("ai_digest") is not None
    assert body["ai_digest"]["ask"] == "test ask"

    summary = client.get(f"/api/traces/{short_id}")
    assert summary.status_code == 200
    sbody = summary.json()
    assert sbody["ai_digest"]["ask"] == "test ask"
    assert _patch_llm.responses.parse.call_count == 1


@pytest.mark.asyncio
async def test_upload_without_env_persists_no_digest(client, respx_mock):
    """No OpenAI env vars set → digest is None on the response.
    Upload still succeeds (digest is best-effort)."""
    _mock_alice_pr(respx_mock, "alice", "repo", 8)
    r = client.post(
        "/api/ingest",
        content=_make_bundle({"main.jsonl": SAMPLE_JSONL}),
        headers=_ingest_headers("https://github.com/alice/repo/pull/8"),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    short_id = body["short_id"]
    assert body.get("ai_digest") is None

    summary = client.get(f"/api/traces/{short_id}")
    assert summary.status_code == 200
    assert summary.json().get("ai_digest") is None


# --- Digest chapter anchors resolve against /session (codex + cursor) ---
#
# Ingest converts codex/cursor uploads server-side and feeds the converted
# bytes to the digest, so chapter anchor_uuids are the synthetic
# codex-rec-<n> / cursor-rec-<n> uuids. The viewer jumps to records by
# uuid in the jsonl /session serves (also the converted blob). These tests
# pin the full loop: ingest -> digest (mocked LLM) -> persisted chapters
# -> /session records carry those exact uuids.

from pathlib import Path  # noqa: E402

_FIXTURES = Path(__file__).parent.parent / "fixtures"


def _platform_headers(pr_url: str, platform: str) -> dict[str, str]:
    headers = dict(_ingest_headers(pr_url))
    headers["X-Vibeshub-Platform"] = platform
    return headers


def _install_digest_mock(monkeypatch, chapters: list[dict]) -> MagicMock:
    """Patch the OpenAI client like _patch_llm, but with given chapters."""
    from app.agents.digest.schema import Digest

    mock = MagicMock()
    payload = {
        "ask": "test ask",
        "decisions": ["chose test decisions over nothing because test"],
        "dead_ends": ["tried a shortcut, abandoned because it failed"],
        "learnings": ["the fixture trace has only two events"],
        "tests": "test tests",
        "chapters": chapters,
    }
    resp = MagicMock()
    resp.output_parsed = Digest.model_validate(payload)
    resp.output_text = json.dumps(payload)
    resp.usage = MagicMock(input_tokens=5, output_tokens=3)
    mock.responses.parse.return_value = resp
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock,
    )
    return mock


def _session_uuids(client, sid):
    text = client.get(f"/api/traces/{sid}/session").text
    return {
        json.loads(line)["uuid"]
        for line in text.splitlines() if line.strip()
    }


@pytest.mark.asyncio
async def test_codex_digest_anchors_resolve_against_session(
    client, respx_mock, _digest_env, monkeypatch,
):
    """A codex upload's digest chapter anchors (codex-rec-<n>) must point
    at records the /session endpoint actually serves."""
    _mock_alice_pr(respx_mock, "alice", "repo", 30)
    mock = _install_digest_mock(monkeypatch, [
        {"anchor_uuid": "codex-rec-1", "title": "Frame the change",
         "caption": "User asks for a helper."},
    ])
    rollout = (_FIXTURES / "codex" / "rollout.jsonl").read_bytes()
    r = client.post(
        "/api/ingest",
        content=_make_bundle({"main.jsonl": rollout}),
        headers=_platform_headers(
            "https://github.com/alice/repo/pull/30", "codex",
        ),
    )
    assert r.status_code == 201, r.text
    sid = r.json()["short_id"]

    head = client.get(f"/api/traces/{sid}").json()
    chapters = head["ai_digest"]["chapters"]
    anchors = [c["anchor_uuid"] for c in chapters]
    # Assert equality first: a chapter whose anchor falls outside the
    # distilled uuid surface is silently dropped, so an empty list here
    # would be a real failure, not a tolerated case.
    assert anchors == ["codex-rec-1"], (
        "anchor dropped; distilled input was:\n"
        + mock.responses.parse.call_args.kwargs["input"]
    )
    # The viewer resolves anchors against /session records.
    assert set(anchors) <= _session_uuids(client, sid)


@pytest.mark.asyncio
async def test_cursor_digest_anchors_resolve_against_session(
    client, respx_mock, _digest_env, monkeypatch,
):
    """A cursor upload gets a digest for the first time (ingest converts
    it server-side). Its chapter anchors (cursor-rec-<n>) must resolve
    against /session, and distill must have handled Cursor tool names."""
    _mock_alice_pr(respx_mock, "alice", "repo", 31)
    mock = _install_digest_mock(monkeypatch, [
        {"anchor_uuid": "cursor-rec-1", "title": "Frame the change",
         "caption": "User asks for a bug review."},
    ])
    transcript = (_FIXTURES / "cursor" / "transcript.jsonl").read_bytes()
    r = client.post(
        "/api/ingest",
        content=_make_bundle({"main.jsonl": transcript}),
        headers=_platform_headers(
            "https://github.com/alice/repo/pull/31", "cursor",
        ),
    )
    assert r.status_code == 201, r.text
    sid = r.json()["short_id"]

    head = client.get(f"/api/traces/{sid}").json()
    chapters = head["ai_digest"]["chapters"]
    anchors = [c["anchor_uuid"] for c in chapters]
    assert anchors == ["cursor-rec-1"], (
        "anchor dropped; distilled input was:\n"
        + mock.responses.parse.call_args.kwargs["input"]
    )
    assert set(anchors) <= _session_uuids(client, sid)

    # Distill handled Cursor's native shape end-to-end: the user line and
    # the Subagent dispatch (subagent_type "explore") both surface.
    sent = mock.responses.parse.call_args.kwargs["input"]
    assert "USER: Review the frontend for likely bugs." in sent
    assert "Subagent[explore]:" in sent


@pytest.mark.asyncio
async def test_file_notes_survive_api_serialization(
    client, respx_mock, _digest_env, monkeypatch,
):
    """Per-file captions (file_notes) must survive API serialization and
    reach the client. Guards the schemas.py TraceDigest seam."""
    from app.agents.digest.schema import Digest

    _mock_alice_pr(respx_mock, "alice", "repo", 32)
    payload = {
        "ask": "a",
        "decisions": ["chose test decisions over nothing because test"],
        "dead_ends": ["tried a shortcut, abandoned because it failed"],
        "learnings": ["the fixture trace has only two events"],
        "tests": "d",
        "chapters": [],
        "file_notes": [{"path": "src/x.ts", "caption": "Tighten the x path"}],
    }
    mock = MagicMock()
    resp = MagicMock()
    resp.output_parsed = Digest.model_validate(payload)
    resp.output_text = json.dumps(payload)
    resp.usage = MagicMock(input_tokens=5, output_tokens=3)
    mock.responses.parse.return_value = resp
    monkeypatch.setattr("app.agents.digest.pipeline.get_client", lambda: mock)

    edit_jsonl = (
        b'{"type":"user","uuid":"u1","message":{"content":"edit x"}}\n'
        b'{"type":"assistant","uuid":"a1","message":{"content":[{"type":"tool_use",'
        b'"name":"Edit","id":"tu1","input":{"file_path":"src/x.ts",'
        b'"old_string":"a","new_string":"b"}}]}}\n'
    )
    r = client.post(
        "/api/ingest",
        content=_make_bundle({"main.jsonl": edit_jsonl}),
        headers=_ingest_headers("https://github.com/alice/repo/pull/32"),
    )
    assert r.status_code == 201, r.text
    sid = r.json()["short_id"]

    assert r.json()["ai_digest"]["file_notes"] == [
        {"path": "src/x.ts", "caption": "Tighten the x path"}
    ]
    summary = client.get(f"/api/traces/{sid}").json()
    assert summary["ai_digest"]["file_notes"] == [
        {"path": "src/x.ts", "caption": "Tighten the x path"}
    ]


# --- ai_digest boundary validator -----------------------------------

from app.api.schemas import IngestResponse, TraceSummary  # noqa: E402

OLD_SHAPE = {
    "ask": "a", "decisions": "prose", "files": "f", "tests": "t",
    "dead_ends": "prose", "chapters": [],
}
NEW_SHAPE = {
    "ask": "a",
    "decisions": ["chose X over Y because Z"],
    "dead_ends": [],
    "learnings": ["gotcha found mid-task"],
    "tests": "none",
    "chapters": [],
    "file_notes": [],
}


def _summary(digest) -> TraceSummary:
    return TraceSummary(
        trace_id="t1", short_id="abc12345", owner_login=None,
        repo_full_name=None, pr_number=None, pr_url=None, pr_title=None,
        platform="claude-code", byte_size=1, message_count=1,
        created_at="2026-07-19T00:00:00Z", ai_digest=digest,
    )


def test_old_shape_digest_is_hidden_not_500():
    # Pre-backfill rows must serialize as "no digest", not crash the API.
    assert _summary(OLD_SHAPE).ai_digest is None


def test_new_shape_digest_survives_serialization():
    s = _summary(NEW_SHAPE)
    assert s.ai_digest is not None
    assert s.ai_digest.decisions == ["chose X over Y because Z"]
    assert s.ai_digest.learnings == ["gotcha found mid-task"]


def test_ingest_response_hides_old_shape_too():
    r = IngestResponse(
        trace_id="t1", short_id="abc12345",
        trace_url="https://x/t/abc12345", ai_digest=OLD_SHAPE,
    )
    assert r.ai_digest is None
