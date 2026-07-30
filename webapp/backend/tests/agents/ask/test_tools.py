import base64

import pytest

from app.agents.ask.tools import (
    AskGitHubError,
    ToolContext,
    execute_tool,
    tool_schemas,
)
from app.github.public_client import PublicGitHubClient
from app.search.index import index_trace_documents

from tests.search.test_index import DIGEST
from tests.search.test_model import _trace


def _ctx(db_session, gh=None, *, include_private=False, viewer_token=None):
    return ToolContext(
        session=db_session,
        repo_full_name="alice/x",
        include_private=include_private,
        gh=gh or PublicGitHubClient(
            "https://api.github.test", fallback_token="ghp_fallback",
        ),
        viewer_token=viewer_token,
        github_enabled=True,
    )


def test_tool_schemas_gated_on_github():
    names = [t["name"] for t in tool_schemas(True)]
    assert names == [
        "search_sessions", "get_session", "list_sessions",
        "search_prs", "get_pr", "list_commits", "get_file",
    ]
    assert [t["name"] for t in tool_schemas(False)] == [
        "search_sessions", "get_session", "list_sessions",
    ]
    assert all(t["type"] == "function" for t in tool_schemas(True))


async def _seed(db_session, **kw):
    trace = _trace(digest_json=DIGEST, **kw)
    db_session.add(trace)
    await index_trace_documents(db_session, trace)
    return trace


async def test_search_sessions(db_session):
    await _seed(db_session)
    out = await execute_tool(
        _ctx(db_session), "search_sessions", {"query": "healthcheck"},
    )
    assert out["hits"]
    assert out["hits"][0]["trace_short_id"] == "abc12345"


async def test_get_session_returns_digest(db_session):
    await _seed(db_session)
    out = await execute_tool(
        _ctx(db_session), "get_session", {"trace_short_id": "abc12345"},
    )
    assert out["ask"] == "Add a /healthcheck route"
    assert out["dead_ends"] == DIGEST["dead_ends"]
    assert out["learnings"] == DIGEST["learnings"]
    assert "files" not in out
    assert len(out["chapters"]) == 2


async def test_get_session_private_hidden_without_access(db_session):
    await _seed(db_session, is_private=True)
    out = await execute_tool(
        _ctx(db_session), "get_session", {"trace_short_id": "abc12345"},
    )
    assert out == {"error": "session not found"}
    ok = await execute_tool(
        _ctx(db_session, include_private=True),
        "get_session", {"trace_short_id": "abc12345"},
    )
    assert ok["ask"] == "Add a /healthcheck route"


async def test_list_sessions(db_session):
    await _seed(db_session)
    out = await execute_tool(_ctx(db_session), "list_sessions", {})
    assert out["sessions"][0]["trace_short_id"] == "abc12345"


async def test_search_prs_happy_path(db_session, respx_mock):
    respx_mock.get("https://api.github.test/search/issues").respond(
        200,
        json={"items": [{
            "number": 7, "title": "Fix auth", "state": "closed",
            "updated_at": "2026-07-01T00:00:00Z",
        }]},
    )
    out = await execute_tool(
        _ctx(db_session), "search_prs", {"query": "auth"},
    )
    assert out["prs"] == [{
        "number": 7, "title": "Fix auth", "state": "closed",
        "updated_at": "2026-07-01T00:00:00Z",
    }]


async def test_get_pr_includes_body_and_files(db_session, respx_mock):
    respx_mock.get("https://api.github.test/repos/alice/x/pulls/7").respond(
        200,
        json={
            "number": 7, "title": "Fix auth", "body": "Long body",
            "merged_at": "2026-07-01T00:00:00Z",
            "user": {"login": "alice"},
            "html_url": "https://github.com/alice/x/pull/7",
        },
    )
    respx_mock.get(
        "https://api.github.test/repos/alice/x/pulls/7/files"
    ).respond(200, json=[{"filename": "app/auth.py"}])
    out = await execute_tool(_ctx(db_session), "get_pr", {"number": 7})
    assert out["title"] == "Fix auth"
    assert out["files"] == ["app/auth.py"]
    assert out["url"] == "https://github.com/alice/x/pull/7"


async def test_list_commits_parses_and_truncates(db_session, respx_mock):
    respx_mock.get("https://api.github.test/repos/alice/x/commits").respond(
        200,
        json=[
            {
                "sha": "abcdef0123456789abcdef0123456789abcdef01",
                "commit": {
                    "message": "Fix auth bug\n\nLonger body explaining why",
                    "author": {"date": "2026-07-02T00:00:00Z"},
                },
                "author": {"login": "alice"},
                "html_url": "https://github.com/alice/x/commit/abcdef0",
            },
            {
                "sha": "1234567890123456789012345678901234567890",
                "commit": {
                    "message": "Tidy up",
                    "author": {"date": "2026-07-01T00:00:00Z"},
                },
                "author": None,
                "html_url": "https://github.com/alice/x/commit/1234567",
            },
        ],
    )
    out = await execute_tool(
        _ctx(db_session), "list_commits", {"path": "app/auth.py"},
    )
    assert out["commits"] == [
        {
            "sha": "abcdef0",
            "message": "Fix auth bug",
            "date": "2026-07-02T00:00:00Z",
            "author": "alice",
            "url": "https://github.com/alice/x/commit/abcdef0",
        },
        {
            "sha": "1234567",
            "message": "Tidy up",
            "date": "2026-07-01T00:00:00Z",
            "author": None,
            "url": "https://github.com/alice/x/commit/1234567",
        },
    ]


async def test_get_file_decodes_and_truncates(db_session, respx_mock):
    content = base64.b64encode(
        ("\n".join(f"line{i}" for i in range(500))).encode()
    ).decode()
    respx_mock.get(
        "https://api.github.test/repos/alice/x/contents/app/auth.py"
    ).respond(200, json={"content": content, "encoding": "base64"})
    out = await execute_tool(
        _ctx(db_session), "get_file", {"path": "app/auth.py"},
    )
    assert out["content"].startswith("line0")
    assert "line399" in out["content"]
    assert "line400" not in out["content"]
    assert out["truncated"] is True


async def test_github_404_is_a_normal_tool_result(db_session, respx_mock):
    respx_mock.get("https://api.github.test/repos/alice/x/pulls/99").respond(
        404, json={"message": "Not Found"},
    )
    out = await execute_tool(_ctx(db_session), "get_pr", {"number": 99})
    assert out == {"error": "not found"}


async def test_github_rate_limit_raises_ask_error(db_session, respx_mock):
    respx_mock.get("https://api.github.test/search/issues").respond(
        403,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9"},
    )
    with pytest.raises(AskGitHubError):
        await execute_tool(
            _ctx(db_session), "search_prs", {"query": "auth"},
        )


async def test_unknown_tool(db_session):
    out = await execute_tool(_ctx(db_session), "bogus", {})
    assert out == {"error": "unknown tool"}
