"""Export endpoint tests. Traces are seeded through /api/ingest with
respx-mocked GitHub, mirroring test_e2e.py's helpers."""
import json
from pathlib import Path

from sqlalchemy import select

from app.storage.models import Trace
from tests.test_e2e import make_bundle
from tests.test_private_traces import REPO_URL, _ingest as _ingest_private

FIXTURES = Path(__file__).parent / "fixtures"
CODEX_FIXTURE = FIXTURES / "codex" / "rollout.jsonl"
CLAUDE_FIXTURE = FIXTURES / "claude_export" / "sample.jsonl"
CURSOR_FIXTURE = FIXTURES / "cursor" / "transcript.jsonl"

# Valid Codex, but nothing in it survives the Claude conversion: session_meta
# carries no conversation and reasoning items are dropped by design.
REASONING_ONLY_ROLLOUT = (
    b'{"timestamp":"2026-05-31T16:20:17.129Z","type":"session_meta",'
    b'"payload":{"id":"019e7ed6-2a11-7c00-9f31-000000000001",'
    b'"timestamp":"2026-05-31T16:20:17.129Z","cwd":"/repo"}}\n'
    b'{"timestamp":"2026-05-31T16:20:18.129Z","type":"response_item",'
    b'"payload":{"type":"reasoning","encrypted_content":"gAAAAscrubbed"}}\n'
)

BEARER = {"Authorization": "Bearer ghp_test"}


def _upload(client, respx_mock, blob: bytes, platform: str) -> str:
    respx_mock.get("https://api.github.test/user").respond(
        200, json={"login": "alice", "id": 7}
    )
    resp = client.post(
        "/api/ingest",
        content=make_bundle({"main.jsonl": blob}),
        headers={
            "X-Vibeshub-Platform": platform,
            "X-Vibeshub-Plugin-Version": "0.6.0",
            "X-Vibeshub-Client-Redactions": "0",
            "X-Vibeshub-Git-Branch": "main",
            "X-Vibeshub-Git-Commit": "c" * 40,
            "Content-Type": "application/x-tar",
            "Authorization": "Bearer ghp_test",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["short_id"]


async def _make_private(client, short_id: str) -> None:
    """Flip a standalone trace to private without going through PATCH."""
    SessionLocal = client.app.state.session_maker
    async with SessionLocal() as session:
        trace = (await session.execute(
            select(Trace).where(Trace.short_id == short_id)
        )).scalar_one()
        trace.is_private = True
        await session.commit()


def test_claude_native_to_codex_converts(client, respx_mock):
    sid = _upload(client, respx_mock, CLAUDE_FIXTURE.read_bytes(), "claude-code")
    r = client.get(f"/api/traces/{sid}/export/codex")
    assert r.status_code == 200
    first = json.loads(r.content.splitlines()[0])
    assert first["type"] == "session_meta"
    assert first["payload"]["git"]["commit_hash"] == "c" * 40
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["x-vibeshub-filename"].startswith("rollout-")
    assert r.headers["x-vibeshub-git-branch"] == "main"


def test_claude_native_to_claude_is_verbatim(client, respx_mock):
    blob = CLAUDE_FIXTURE.read_bytes()
    sid = _upload(client, respx_mock, blob, "claude-code")
    r = client.get(f"/api/traces/{sid}/export/claude")
    assert r.status_code == 200
    # Verbatim modulo server redaction: same line count, same first uuid.
    assert len(r.content.splitlines()) == len(blob.splitlines())
    assert r.headers["x-vibeshub-filename"].endswith(".jsonl")


def test_codex_native_to_codex_is_verbatim_with_original_filename(
    client, respx_mock,
):
    blob = CODEX_FIXTURE.read_bytes()
    sid = _upload(client, respx_mock, blob, "codex")
    r = client.get(f"/api/traces/{sid}/export/codex")
    assert r.status_code == 200
    orig_id = json.loads(blob.splitlines()[0])["payload"]["id"]
    assert orig_id in r.headers["x-vibeshub-filename"]
    assert r.headers["x-vibeshub-session-uuid"] == orig_id
    # Byte-identical to the stored native original.
    assert r.content == client.get(f"/api/traces/{sid}/raw").content


def test_codex_native_to_claude_is_resume_grade(client, respx_mock):
    sid = _upload(client, respx_mock, CODEX_FIXTURE.read_bytes(), "codex")
    r = client.get(f"/api/traces/{sid}/export/claude")
    assert r.status_code == 200
    recs = [json.loads(line) for line in r.content.splitlines()]
    session_uuid = r.headers["x-vibeshub-session-uuid"]
    assert all(rec["sessionId"] == session_uuid for rec in recs)
    assert recs[0]["parentUuid"] is None
    assert r.headers["x-vibeshub-filename"] == f"{session_uuid}.jsonl"


def test_cursor_native_to_claude_is_unsupported(client, respx_mock):
    sid = _upload(client, respx_mock, CURSOR_FIXTURE.read_bytes(), "cursor")
    r = client.get(f"/api/traces/{sid}/export/claude")
    assert r.status_code == 422
    assert r.json()["detail"] == "cursor_to_claude_unsupported"


def test_cursor_native_to_codex_converts(client, respx_mock):
    sid = _upload(client, respx_mock, CURSOR_FIXTURE.read_bytes(), "cursor")
    r = client.get(f"/api/traces/{sid}/export/codex")
    assert r.status_code == 200
    first = json.loads(r.content.splitlines()[0])
    assert first["type"] == "session_meta"
    # Converted from the stored Claude-shaped copy, so the conversation
    # survives rather than emitting a lone session_meta.
    assert len(r.content.splitlines()) > 1


def test_reasoning_only_rollout_to_claude_is_422(client, respx_mock):
    sid = _upload(client, respx_mock, REASONING_ONLY_ROLLOUT, "codex")
    r = client.get(f"/api/traces/{sid}/export/claude")
    assert r.status_code == 422
    assert r.json()["detail"] == "unconvertible_trace"


def test_hostile_rollout_id_cannot_inject_headers(client, respx_mock):
    """The codex filename is built from the uploaded rollout's own
    session_meta id, so it must never reach a header unsanitized."""
    hostile = (
        b'{"timestamp":"2026-05-31T16:20:17.129Z","type":"session_meta",'
        b'"payload":{"id":"aa\\r\\nX-Evil: 1",'
        b'"timestamp":"2026-05-31T16:20:17.129Z","cwd":"/repo"}}\n'
        b'{"timestamp":"2026-05-31T16:20:18.129Z","type":"event_msg",'
        b'"payload":{"type":"user_message","message":"hi"}}\n'
    )
    sid = _upload(client, respx_mock, hostile, "codex")
    r = client.get(f"/api/traces/{sid}/export/codex")
    assert r.status_code == 200
    assert "x-evil" not in r.headers
    for value in (
        r.headers["content-disposition"],
        r.headers["x-vibeshub-filename"],
        r.headers["x-vibeshub-session-uuid"],
    ):
        assert "\r" not in value and "\n" not in value


def test_unknown_target_404(client, respx_mock):
    sid = _upload(client, respx_mock, CLAUDE_FIXTURE.read_bytes(), "claude-code")
    assert client.get(f"/api/traces/{sid}/export/cursor").status_code == 404


def test_unknown_trace_404(client):
    assert client.get("/api/traces/abcdefgh/export/codex").status_code == 404


def test_export_is_deterministic(client, respx_mock):
    sid = _upload(client, respx_mock, CLAUDE_FIXTURE.read_bytes(), "claude-code")
    a = client.get(f"/api/traces/{sid}/export/codex")
    b = client.get(f"/api/traces/{sid}/export/codex")
    assert a.content == b.content
    assert a.headers["x-vibeshub-filename"] == b.headers["x-vibeshub-filename"]


def test_private_repo_export_401_for_anonymous(client, respx_mock):
    sid = _ingest_private(client, respx_mock, private=True)
    r = client.get(f"/api/traces/{sid}/export/claude")
    assert r.status_code == 401
    assert r.json()["detail"] == "auth_required"
    assert r.headers["Cache-Control"] == "no-store"


def test_private_repo_export_200_for_bearer_token(client, respx_mock):
    sid = _ingest_private(client, respx_mock, private=True)
    respx_mock.get(REPO_URL).respond(200, json={"id": 1})
    r = client.get(f"/api/traces/{sid}/export/claude", headers=BEARER)
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "private, no-store"


def test_private_repo_export_404_when_github_denies_bearer(
    client, respx_mock,
):
    sid = _ingest_private(client, respx_mock, private=True)
    respx_mock.get(REPO_URL).respond(404, json={})
    r = client.get(f"/api/traces/{sid}/export/claude", headers=BEARER)
    assert r.status_code == 404


def test_private_repo_export_401_for_invalid_bearer(client, respx_mock):
    sid = _ingest_private(client, respx_mock, private=True)
    respx_mock.get("https://api.github.test/user").respond(401, json={})
    r = client.get(f"/api/traces/{sid}/export/claude", headers=BEARER)
    assert r.status_code == 401
    assert r.json()["detail"] == "auth_required"


async def test_standalone_private_export_200_for_owner_bearer(
    client, respx_mock,
):
    sid = _upload(client, respx_mock, CLAUDE_FIXTURE.read_bytes(), "claude-code")
    await _make_private(client, sid)
    r = client.get(f"/api/traces/{sid}/export/codex", headers=BEARER)
    assert r.status_code == 200


async def test_standalone_private_export_404_for_other_bearer(
    client, respx_mock,
):
    sid = _upload(client, respx_mock, CLAUDE_FIXTURE.read_bytes(), "claude-code")
    await _make_private(client, sid)
    # The bearer now resolves to a different GitHub user than the owner.
    respx_mock.get("https://api.github.test/user").respond(
        200, json={"login": "bob", "id": 9}
    )
    r = client.get(f"/api/traces/{sid}/export/codex", headers=BEARER)
    assert r.status_code == 404
