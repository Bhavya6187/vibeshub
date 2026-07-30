from sqlalchemy import select

from tests._auth_helpers import authed_cookies
from tests.test_traces import make_bundle, _ingest_headers
from tests.search.test_index import DIGEST


def _mock_github(respx_mock, *, private: bool = False):
    respx_mock.get("https://api.github.test/user").respond(
        200, json={"login": "alice", "id": 7}
    )
    respx_mock.get(
        "https://api.github.test/repos/alice/repo/pulls/3"
    ).respond(
        200,
        json={
            "number": 3,
            "title": "Hello",
            "user": {"login": "alice"},
            "html_url": "https://github.com/alice/repo/pull/3",
            "head": {"repo": {"private": private, "full_name": "alice/repo"}},
            "base": {"repo": {"private": private, "full_name": "alice/repo"}},
        },
    )


def _patch_digest(monkeypatch):
    """Fake the digest agent: stamp digest_json so indexing has input."""
    async def fake_digest(session, trace, *, blob, subagent_blobs):
        trace.digest_json = DIGEST
        return None

    monkeypatch.setattr("app.agents.digest.compute_digest", fake_digest)


def _ingest(client, respx_mock) -> str:
    body = make_bundle({"main.jsonl": b'{"type":"user"}\n'})
    resp = client.post(
        "/api/ingest",
        content=body,
        headers=_ingest_headers("https://github.com/alice/repo/pull/3"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["short_id"]


async def test_upload_with_digest_indexes_documents(
    client, respx_mock, monkeypatch,
):
    from app.storage.models import SearchDocument

    _mock_github(respx_mock)
    _patch_digest(monkeypatch)
    _ingest(client, respx_mock)

    SessionLocal = client.app.state.session_maker
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(SearchDocument)
        )).scalars().all()
        assert len(rows) == 7
        assert {r.source_type for r in rows} == {
            "summary", "decision", "dead_end", "learning", "chapter", "files",
        }
        assert all(r.repo_full_name == "alice/repo" for r in rows)


async def test_delete_trace_removes_documents(
    client, respx_mock, monkeypatch,
):
    from app.storage.models import SearchDocument

    _mock_github(respx_mock)
    _patch_digest(monkeypatch)
    short_id = _ingest(client, respx_mock)

    resp = client.delete(
        f"/api/traces/{short_id}",
        headers={"Authorization": "Bearer gho_token"},
    )
    assert resp.status_code == 204

    SessionLocal = client.app.state.session_maker
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(SearchDocument)
        )).scalars().all()
        assert rows == []


async def test_patch_clearing_association_removes_documents(
    client, respx_mock, monkeypatch,
):
    from app.storage.models import SearchDocument

    _mock_github(respx_mock)
    _patch_digest(monkeypatch)
    short_id = _ingest(client, respx_mock)

    # Docs exist after a public ingest.
    SessionLocal = client.app.state.session_maker
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(SearchDocument)
        )).scalars().all()
        assert rows, "expected search documents after public ingest"

    # The ingest mock authenticates the uploader as "alice".
    cookies, _ = await authed_cookies(client, login="alice")
    resp = client.patch(
        f"/api/traces/{short_id}",
        json={"repo_full_name": None, "pr_url": None},
        cookies=cookies,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["repo_full_name"] is None

    # Clearing the association back to standalone drops the stale docs.
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(SearchDocument)
        )).scalars().all()
        assert rows == []
