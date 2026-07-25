import io
import tarfile

import pytest
import respx
from sqlalchemy import select

from app.short_id import looks_like_short_id
from app.storage.models import Trace, utcnow


def make_bundle(members: dict[str, bytes]) -> bytes:
    """Build a gzipped tar bundle from {name: bytes} members, in the same
    shape produced by the plugin and consumed by `unpack_and_redact`."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


COMMON_HEADERS = {
    "X-Vibeshub-Pr-Url": "https://github.com/alice/repo/pull/1",
    "X-Vibeshub-Platform": "claude-code",
    "X-Vibeshub-Plugin-Version": "0.2.0",
    "X-Vibeshub-Client-Redactions": "0",
    "Content-Type": "application/x-tar",
    "Authorization": "Bearer ghp_test",
}


def _mock_alice_pr1(respx_mock: respx.MockRouter, *, private: bool = False, author: str = "alice") -> None:
    """Stand up the two GitHub responses the ingest handler needs."""
    respx_mock.get("https://api.github.test/user").respond(
        200, json={"login": "alice", "id": 7}
    )
    respx_mock.get(
        "https://api.github.test/repos/alice/repo/pulls/1"
    ).respond(
        200,
        json={
            "number": 1,
            "title": "Add a feature",
            "user": {"login": author},
            "html_url": "https://github.com/alice/repo/pull/1",
            "head": {"repo": {"private": private, "full_name": "alice/repo"}},
            "base": {"repo": {"private": private, "full_name": "alice/repo"}},
        },
    )


def _mock_alice_collab_repo(
    respx_mock: respx.MockRouter, *, permission: str = "write",
    private: bool = False,
) -> None:
    """Stand up the GitHub responses the repo-only ingest path needs."""
    respx_mock.get("https://api.github.test/user").respond(
        200, json={"login": "alice", "id": 7}
    )
    respx_mock.get(
        "https://api.github.test/repos/alice/repo/collaborators/alice/permission"
    ).respond(200, json={"permission": permission})
    respx_mock.get("https://api.github.test/repos/alice/repo").respond(
        200, json={"full_name": "alice/repo", "private": private}
    )


@pytest.mark.asyncio
async def test_ingest_accepts_tar_bundle(client, respx_mock):
    _mock_alice_pr1(respx_mock)

    body = make_bundle({"main.jsonl": b'{"type":"user","message":{}}\n'})
    r = client.post("/api/ingest", content=body, headers=COMMON_HEADERS)
    assert r.status_code == 201, r.text
    data = r.json()
    assert "trace_id" in data and "short_id" in data and "trace_url" in data
    assert looks_like_short_id(data["short_id"])
    assert data["trace_url"].endswith(f"/t/{data['short_id']}")


@pytest.mark.asyncio
async def test_ingest_standalone_when_no_pr_or_repo(client, respx_mock):
    respx_mock.get("https://api.github.test/user").respond(
        200, json={"login": "alice", "id": 7}
    )
    headers = {k: v for k, v in COMMON_HEADERS.items()
               if k != "X-Vibeshub-Pr-Url"}
    body = make_bundle({"main.jsonl": b'{"type":"user"}\n'})
    r = client.post("/api/ingest", content=body, headers=headers)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["trace_url"].endswith(f"/t/{data['short_id']}")

    SessionLocal = client.app.state.session_maker
    async with SessionLocal() as session:
        trace = (await session.execute(
            select(Trace).where(Trace.short_id == data["short_id"])
        )).scalar_one()
    assert trace.repo_full_name is None
    assert trace.pr_number is None
    assert trace.pr_url is None
    assert trace.is_private is False
    assert trace.owner_login == "alice"


@pytest.mark.asyncio
async def test_ingest_repo_only_for_collaborator(client, respx_mock):
    _mock_alice_collab_repo(respx_mock, permission="write", private=True)
    headers = {k: v for k, v in COMMON_HEADERS.items()
               if k != "X-Vibeshub-Pr-Url"}
    headers["X-Vibeshub-Repo"] = "alice/repo"
    body = make_bundle({"main.jsonl": b'{"type":"user"}\n'})
    r = client.post("/api/ingest", content=body, headers=headers)
    assert r.status_code == 201, r.text
    short_id = r.json()["short_id"]

    SessionLocal = client.app.state.session_maker
    async with SessionLocal() as session:
        trace = (await session.execute(
            select(Trace).where(Trace.short_id == short_id)
        )).scalar_one()
    assert trace.repo_full_name == "alice/repo"
    assert trace.pr_number is None
    assert trace.is_private is True


@pytest.mark.asyncio
async def test_ingest_repo_only_rejects_non_collaborator(
    client, respx_mock
):
    respx_mock.get("https://api.github.test/user").respond(
        200, json={"login": "alice", "id": 7}
    )
    respx_mock.get(
        "https://api.github.test/repos/alice/repo/collaborators/alice/permission"
    ).respond(200, json={"permission": "none"})
    headers = {k: v for k, v in COMMON_HEADERS.items()
               if k != "X-Vibeshub-Pr-Url"}
    headers["X-Vibeshub-Repo"] = "alice/repo"
    body = make_bundle({"main.jsonl": b'{"type":"user"}\n'})
    r = client.post("/api/ingest", content=body, headers=headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_ingest_persists_agents(client, respx_mock):
    _mock_alice_pr1(respx_mock)

    aid = "a0123456789abcdef"
    # Streamed assistant message (one text block, then text + tool_use) plus
    # a tool_result user line: 3 JSONL lines, but only 2 rendered messages.
    agent_jsonl = (
        b'{"type":"assistant","message":{"id":"m1","content":'
        b'[{"type":"text","text":"hi"}]}}\n'
        b'{"type":"assistant","message":{"id":"m1","content":'
        b'[{"type":"text","text":"hi"},'
        b'{"type":"tool_use","id":"t1","name":"Bash","input":{}}]}}\n'
        b'{"type":"user","message":{"content":'
        b'[{"type":"tool_result","tool_use_id":"t1"}]}}\n'
    )
    body = make_bundle({
        "main.jsonl": b'{"type":"user"}\n',
        f"agents/{aid}.jsonl": agent_jsonl,
        f"agents/{aid}.meta.json": (
            b'{"agentType":"Explore","description":"d","toolUseId":"toolu_01x"}'
        ),
    })
    r = client.post("/api/ingest", content=body, headers=COMMON_HEADERS)
    assert r.status_code == 201, r.text
    short_id = r.json()["short_id"]

    # Query the trace row via the app's session_maker (same engine the
    # handler used, since this is an in-memory SQLite shared across the app).
    SessionLocal = client.app.state.session_maker
    async with SessionLocal() as session:
        trace = (
            await session.execute(select(Trace).where(Trace.short_id == short_id))
        ).scalar_one()

    assert trace.blob_prefix == f"traces/{trace.short_id}/"
    assert trace.blob_path is None
    assert trace.agent_count == 1
    assert trace.agents == [{
        "agent_id": aid,
        "tool_use_id": "toolu_01x",
        "agent_type": "Explore",
        "description": "d",
        "message_count": 2,
    }]


@pytest.mark.asyncio
async def test_ingest_rejects_oversize_bundle(client, respx_mock):
    _mock_alice_pr1(respx_mock)
    # Cap the in-process settings so we don't have to actually produce a
    # 50 MiB tar to trigger the limit.
    client.app.state.settings.max_trace_bytes = 100

    body = make_bundle({"main.jsonl": b"x" * 5000})
    r = client.post("/api/ingest", content=body, headers=COMMON_HEADERS)
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_ingest_rejects_malformed_tar(client, respx_mock):
    _mock_alice_pr1(respx_mock)
    r = client.post(
        "/api/ingest", content=b"not a tar", headers=COMMON_HEADERS,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_ingest_without_pr_url_header_is_standalone(client, respx_mock):
    respx_mock.get("https://api.github.test/user").respond(
        200, json={"login": "alice", "id": 7}
    )
    body = make_bundle({"main.jsonl": b"{}\n"})
    headers = {k: v for k, v in COMMON_HEADERS.items()
               if k != "X-Vibeshub-Pr-Url"}
    r = client.post("/api/ingest", content=body, headers=headers)
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_ingest_missing_bearer(client):
    body = make_bundle({"main.jsonl": b"{}\n"})
    headers = {k: v for k, v in COMMON_HEADERS.items() if k != "Authorization"}
    r = client.post("/api/ingest", content=body, headers=headers)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_ingest_invalid_token(client, respx_mock):
    respx_mock.get("https://api.github.test/user").respond(401)
    respx_mock.get(
        "https://api.github.test/repos/alice/repo/pulls/1"
    ).respond(
        200,
        json={
            "number": 1, "title": "x", "user": {"login": "alice"},
            "html_url": "https://github.com/alice/repo/pull/1",
            "head": {"repo": {"private": False, "full_name": "alice/repo"}},
            "base": {"repo": {"private": False, "full_name": "alice/repo"}},
        },
    )
    body = make_bundle({"main.jsonl": b"{}\n"})
    r = client.post(
        "/api/ingest",
        content=body,
        headers={**COMMON_HEADERS, "Authorization": "Bearer bad"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_ingest_rejects_pr_author_mismatch(client, respx_mock):
    _mock_alice_pr1(respx_mock, author="bob")
    body = make_bundle({"main.jsonl": b"{}\n"})
    r = client.post("/api/ingest", content=body, headers=COMMON_HEADERS)
    assert r.status_code == 403
    assert "author" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_ingest_upserts_trace_for_same_session(client, respx_mock):
    _mock_alice_pr1(respx_mock)
    headers = {**COMMON_HEADERS, "X-Vibeshub-Session-Id": "sess-A"}
    SessionLocal = client.app.state.session_maker

    r1 = client.post(
        "/api/ingest",
        content=make_bundle({"main.jsonl": b'{"type":"user"}\n'}),
        headers=headers,
    )
    assert r1.status_code == 201, r1.text
    assert r1.json()["created"] is True
    sid1 = r1.json()["short_id"]

    async with SessionLocal() as session:
        row1 = (
            await session.execute(
                select(Trace).where(Trace.session_id == "sess-A")
            )
        ).scalar_one()
        byte_size_1 = row1.byte_size

    r2 = client.post(
        "/api/ingest",
        content=make_bundle(
            {"main.jsonl": b'{"type":"user"}\n{"type":"assistant"}\n'}
        ),
        headers=headers,
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["created"] is False
    assert r2.json()["short_id"] == sid1

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Trace).where(Trace.session_id == "sess-A")
            )
        ).scalars().all()

    assert len(rows) == 1                    # upserted, not duplicated
    assert rows[0].byte_size > byte_size_1  # content refreshed in place


@pytest.mark.asyncio
async def test_ingest_without_session_always_creates(client, respx_mock):
    _mock_alice_pr1(respx_mock)
    # COMMON_HEADERS carries no X-Vibeshub-Session-Id.
    body = make_bundle({"main.jsonl": b"{}\n"})
    r1 = client.post("/api/ingest", content=body, headers=COMMON_HEADERS)
    r2 = client.post("/api/ingest", content=body, headers=COMMON_HEADERS)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["created"] is True
    assert r2.json()["created"] is True
    assert r1.json()["short_id"] != r2.json()["short_id"]


@pytest.mark.asyncio
async def test_ingest_does_not_resurrect_a_deleted_trace(client, respx_mock):
    _mock_alice_pr1(respx_mock)
    headers = {**COMMON_HEADERS, "X-Vibeshub-Session-Id": "sess-D"}
    SessionLocal = client.app.state.session_maker

    r1 = client.post(
        "/api/ingest",
        content=make_bundle({"main.jsonl": b'{"type":"user"}\n'}),
        headers=headers,
    )
    assert r1.status_code == 201, r1.text
    sid1 = r1.json()["short_id"]

    # Soft-delete that trace.
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(Trace).where(Trace.session_id == "sess-D")
            )
        ).scalar_one()
        row.deleted_at = utcnow()
        await session.commit()

    # A re-upload from the same session creates a fresh trace, not a revival.
    r2 = client.post(
        "/api/ingest",
        content=make_bundle({"main.jsonl": b'{"type":"user"}\n'}),
        headers=headers,
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["created"] is True
    assert r2.json()["short_id"] != sid1


@pytest.mark.asyncio
async def test_ingest_codex_platform_and_uuid_agent(client, respx_mock):
    _mock_alice_pr1(respx_mock)

    uuid = "019e7f09-bca2-7150-ac2b-54f7b075a2ea"
    main = (
        b'{"type":"session_meta","payload":{"id":"019e7ed1","cwd":"/x"}}\n'
        b'{"type":"response_item","payload":{"type":"message","role":"assistant",'
        b'"content":[{"type":"output_text","text":"on it"}]}}\n'
        b'{"type":"response_item","payload":{"type":"function_call",'
        b'"name":"exec_command","arguments":"{\\"cmd\\":\\"ls\\"}","call_id":"c1"}}\n'
    )
    body = make_bundle({
        "main.jsonl": main,
        f"agents/{uuid}.jsonl": b'{"type":"session_meta","payload":{"id":"019e7f09"}}\n',
        f"agents/{uuid}.meta.json": (
            b'{"agentType":"default","description":"Godel","toolUseId":"call_spawn"}'
        ),
    })
    headers = {**COMMON_HEADERS, "X-Vibeshub-Platform": "codex"}
    r = client.post("/api/ingest", content=body, headers=headers)
    assert r.status_code == 201, r.text
    short_id = r.json()["short_id"]

    SessionLocal = client.app.state.session_maker
    async with SessionLocal() as session:
        trace = (
            await session.execute(select(Trace).where(Trace.short_id == short_id))
        ).scalar_one()

    assert trace.platform == "codex"
    assert trace.message_count == 2  # 1 assistant msg + 1 function_call
    assert trace.agent_count == 1
    assert trace.agents[0]["agent_id"] == uuid
    assert trace.agents[0]["tool_use_id"] == "call_spawn"


@pytest.mark.asyncio
async def test_ingest_cursor_platform_and_uuid_agent(client, respx_mock):
    _mock_alice_pr1(respx_mock)

    uuid = "09fbacda-2df4-47a7-a12e-2534c6d55047"
    main = (
        b'{"role":"user","message":{"content":[{"type":"text",'
        b'"text":"<user_query>do a sweep</user_query>"}]}}\n'
        b'{"role":"assistant","message":{"content":['
        b'{"type":"text","text":"on it"},'
        b'{"type":"tool_use","name":"Subagent","input":{"subagent_type":"explore",'
        b'"description":"Bug sweep","prompt":"Find bugs"}}]}}\n'
    )
    body = make_bundle({
        "main.jsonl": main,
        f"agents/{uuid}.jsonl": (
            b'{"role":"user","message":{"content":[{"type":"text","text":"Find bugs"}]}}\n'
        ),
        f"agents/{uuid}.meta.json": (
            b'{"agentType":"explore","description":"Bug sweep","toolUseId":"cursor-agent-0"}'
        ),
    })
    headers = {**COMMON_HEADERS, "X-Vibeshub-Platform": "cursor"}
    r = client.post("/api/ingest", content=body, headers=headers)
    assert r.status_code == 201, r.text
    short_id = r.json()["short_id"]

    SessionLocal = client.app.state.session_maker
    async with SessionLocal() as session:
        trace = (
            await session.execute(select(Trace).where(Trace.short_id == short_id))
        ).scalar_one()

    assert trace.platform == "cursor"
    assert trace.message_count == 2  # 1 assistant text block + 1 tool_use block
    assert trace.agent_count == 1
    assert trace.agents[0]["agent_id"] == uuid
    assert trace.agents[0]["tool_use_id"] == "cursor-agent-0"


@pytest.mark.asyncio
async def test_ingest_stores_git_headers(client, respx_mock):
    _mock_alice_pr1(respx_mock)

    headers = {
        **COMMON_HEADERS,
        "X-Vibeshub-Git-Branch": "feature/x",
        "X-Vibeshub-Git-Commit": "b" * 40,
    }
    body = make_bundle({"main.jsonl": b'{"type":"user"}\n'})
    r = client.post("/api/ingest", content=body, headers=headers)
    assert r.status_code == 201, r.text
    short_id = r.json()["short_id"]

    SessionLocal = client.app.state.session_maker
    async with SessionLocal() as session:
        trace = (
            await session.execute(select(Trace).where(Trace.short_id == short_id))
        ).scalar_one()

    assert trace.git_branch == "feature/x"
    assert trace.git_commit == "b" * 40


@pytest.mark.asyncio
async def test_ingest_rejects_malformed_git_commit_silently(
    client, respx_mock
):
    _mock_alice_pr1(respx_mock)

    headers = {
        **COMMON_HEADERS,
        "X-Vibeshub-Git-Branch": "feature/x",
        "X-Vibeshub-Git-Commit": "not-a-sha",
    }
    body = make_bundle({"main.jsonl": b'{"type":"user"}\n'})
    r = client.post("/api/ingest", content=body, headers=headers)
    # Bad git metadata never fails an upload: it is silently dropped.
    assert r.status_code == 201, r.text
    short_id = r.json()["short_id"]

    SessionLocal = client.app.state.session_maker
    async with SessionLocal() as session:
        trace = (
            await session.execute(select(Trace).where(Trace.short_id == short_id))
        ).scalar_one()

    assert trace.git_commit is None
    assert trace.git_branch == "feature/x"
