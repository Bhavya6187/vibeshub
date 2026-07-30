import json

import pytest
from sqlalchemy import select

from app.agents.ask.pipeline import AskEvent

from tests._auth_helpers import authed_cookies
from tests.search.test_index import DIGEST
from tests.search.test_model import _trace


@pytest.fixture(autouse=True)
def _reset_limiters():
    import app.api.ask as ask_module
    ask_module._anon_limiter._events.clear()
    ask_module._user_limiter._events.clear()


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for frame in body.strip().split("\n\n"):
        lines = frame.split("\n")
        name = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        events.append((name, data))
    return events


async def _seed_trace(client, **kw):
    SessionLocal = client.app.state.session_maker
    async with SessionLocal() as session:
        trace = _trace(digest_json=DIGEST, **kw)
        session.add(trace)
        await session.commit()


def _fake_run_ask(events):
    async def fake(ctx, question):
        for ev in events:
            yield ev
    return fake


async def test_ask_streams_events(client, monkeypatch):
    await _seed_trace(client)
    monkeypatch.setattr(
        "app.api.ask.run_ask",
        _fake_run_ask([
            AskEvent("status", {"text": "searching sessions"}),
            AskEvent("delta", {"text": "Because."}),
            AskEvent("citations", {"citations": []}),
            AskEvent("done", {"best_effort": False}),
        ]),
    )
    resp = client.post(
        "/api/repos/alice/x/ask", json={"question": "why?"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.text)
    assert [e[0] for e in events] == [
        "status", "delta", "citations", "done",
    ]


async def test_agent_run_rows_are_committed(client, monkeypatch):
    from app.agents._usage import Outcome, record_run

    await _seed_trace(client)

    async def fake(ctx, question):
        await record_run(
            ctx.session, agent_name="repo_ask", trace_id=None, model="m",
            input_tokens=1, output_tokens=1, latency_ms=1,
            outcome=Outcome.OK,
        )
        yield AskEvent("done", {"best_effort": False})

    monkeypatch.setattr("app.api.ask.run_ask", fake)
    resp = client.post(
        "/api/repos/alice/x/ask", json={"question": "why?"},
    )
    assert resp.status_code == 200

    from app.storage.models import AgentRun
    SessionLocal = client.app.state.session_maker
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(AgentRun).where(AgentRun.agent_name == "repo_ask")
        )).scalars().all()
        assert len(rows) == 1


def test_repo_without_traces_404(client):
    resp = client.post(
        "/api/repos/ghost/none/ask", json={"question": "why?"},
    )
    assert resp.status_code == 404


async def test_question_validation(client):
    await _seed_trace(client)
    assert client.post(
        "/api/repos/alice/x/ask", json={"question": "   "},
    ).status_code == 400
    assert client.post(
        "/api/repos/alice/x/ask", json={"question": "x" * 501},
    ).status_code == 400


async def test_private_only_repo_anonymous_401(client):
    await _seed_trace(client, is_private=True)
    resp = client.post(
        "/api/repos/alice/x/ask", json={"question": "why?"},
    )
    assert resp.status_code == 401


async def test_private_only_repo_no_access_404(client, respx_mock):
    await _seed_trace(client, is_private=True)
    cookies, _user = await authed_cookies(
        client, token_scopes="read:user,repo",
    )
    # RepoAccessChecker: GitHub 404 means no read access.
    respx_mock.get("https://api.github.test/repos/alice/x").respond(
        404, json={},
    )
    resp = client.post(
        "/api/repos/alice/x/ask", json={"question": "why?"},
        cookies=cookies,
    )
    assert resp.status_code == 404


async def test_private_only_repo_with_access_streams(
    client, respx_mock, monkeypatch,
):
    await _seed_trace(client, is_private=True)
    cookies, _user = await authed_cookies(
        client, token_scopes="read:user,repo",
    )
    respx_mock.get("https://api.github.test/repos/alice/x").respond(
        200, json={"full_name": "alice/x"},
    )
    seen = {}

    async def fake(ctx, question):
        seen["include_private"] = ctx.include_private
        seen["viewer_token"] = ctx.viewer_token
        yield AskEvent("done", {"best_effort": False})

    monkeypatch.setattr("app.api.ask.run_ask", fake)
    resp = client.post(
        "/api/repos/alice/x/ask", json={"question": "why?"},
        cookies=cookies,
    )
    assert resp.status_code == 200
    assert seen["include_private"] is True
    assert seen["viewer_token"] == "gho_user"


async def test_anonymous_rate_limited_429(client, monkeypatch):
    await _seed_trace(client)
    monkeypatch.setattr(
        "app.api.ask.run_ask",
        _fake_run_ask([AskEvent("done", {"best_effort": False})]),
    )
    for _ in range(5):
        assert client.post(
            "/api/repos/alice/x/ask", json={"question": "why?"},
        ).status_code == 200
    resp = client.post(
        "/api/repos/alice/x/ask", json={"question": "why?"},
    )
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == "3600"


async def test_spoofed_forwarded_for_left_does_not_reset_limit(
    client, monkeypatch,
):
    """A client-controlled left-most XFF entry must not create a fresh rate
    bucket; the trusted right-most (proxy-appended) entry keys the limit."""
    await _seed_trace(client)
    monkeypatch.setattr(
        "app.api.ask.run_ask",
        _fake_run_ask([AskEvent("done", {"best_effort": False})]),
    )
    for i in range(5):
        assert client.post(
            "/api/repos/alice/x/ask", json={"question": "why?"},
            headers={"X-Forwarded-For": f"spoof{i}, 10.0.0.1"},
        ).status_code == 200
    resp = client.post(
        "/api/repos/alice/x/ask", json={"question": "why?"},
        headers={"X-Forwarded-For": "spoof-final, 10.0.0.1"},
    )
    assert resp.status_code == 429
