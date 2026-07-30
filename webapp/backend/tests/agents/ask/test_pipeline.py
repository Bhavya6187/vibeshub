import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.agents.ask.pipeline import run_ask
from app.agents.ask.schema import AskAnswer, AskCitation
from app.agents.ask.tools import ToolContext
from app.github.public_client import PublicGitHubClient
from app.search.index import index_trace_documents
from app.storage.models import AgentRun

from tests.search.test_index import DIGEST
from tests.search.test_model import _trace


def _fn_call(name, arguments, call_id="c1"):
    return SimpleNamespace(
        type="function_call", name=name,
        arguments=json.dumps(arguments), call_id=call_id,
    )


def _tool_response(*calls):
    resp = MagicMock()
    resp.output = list(calls)
    resp.output_parsed = None
    resp.usage = MagicMock(input_tokens=100, output_tokens=20)
    return resp


def _final_response(answer: AskAnswer):
    resp = MagicMock()
    resp.output = []
    resp.output_parsed = answer
    resp.usage = MagicMock(input_tokens=200, output_tokens=80)
    return resp


@pytest.fixture
def _env(monkeypatch):
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")


async def _ctx(db_session, **kw):
    trace = _trace(digest_json=DIGEST)
    db_session.add(trace)
    await index_trace_documents(db_session, trace)
    defaults = dict(
        session=db_session, repo_full_name="alice/x",
        include_private=False,
        gh=PublicGitHubClient(
            "https://api.github.test", fallback_token="ghp_fallback",
        ),
        viewer_token=None, github_enabled=True,
    )
    defaults.update(kw)
    return ToolContext(**defaults)


def _mock_client(monkeypatch, responses):
    client = MagicMock()
    client.responses.parse.side_effect = responses
    monkeypatch.setattr(
        "app.agents.ask.pipeline.get_client", lambda: client,
    )
    return client


async def _collect(gen):
    return [ev async for ev in gen]


async def test_happy_path_tool_then_answer(_env, db_session, monkeypatch):
    answer = AskAnswer(
        answer_markdown="Because the session decided so.",
        citations=[AskCitation(
            type="session", title="the session", trace_short_id="abc12345",
        )],
    )
    client = _mock_client(monkeypatch, [
        _tool_response(_fn_call(
            "search_sessions", {"query": "healthcheck"},
        )),
        _final_response(answer),
    ])
    events = await _collect(run_ask(await _ctx(db_session), "why?"))

    kinds = [e.event for e in events]
    assert kinds == ["status", "delta", "citations", "done"]
    assert "searching sessions" in events[0].data["text"]
    assert events[1].data["text"] == "Because the session decided so."
    assert events[2].data["citations"][0]["trace_short_id"] == "abc12345"
    assert events[3].data == {"best_effort": False}
    assert client.responses.parse.call_count == 2

    runs = (await db_session.execute(
        select(AgentRun).where(AgentRun.agent_name == "repo_ask")
    )).scalars().all()
    assert len(runs) == 1
    assert runs[0].outcome == "ok"
    assert runs[0].input_tokens == 300
    assert runs[0].extra["tool_calls"] == 1


async def test_invalid_citations_dropped(_env, db_session, monkeypatch):
    answer = AskAnswer(
        answer_markdown="ok",
        citations=[AskCitation(
            type="session", title="ghost", trace_short_id="ghost999",
        )],
    )
    _mock_client(monkeypatch, [_final_response(answer)])
    events = await _collect(run_ask(await _ctx(db_session), "why?"))
    cit = [e for e in events if e.event == "citations"][0]
    assert cit.data["citations"] == []


async def test_em_dashes_stripped_from_answer(_env, db_session, monkeypatch):
    answer = AskAnswer(answer_markdown="a — b", citations=[])
    _mock_client(monkeypatch, [_final_response(answer)])
    events = await _collect(run_ask(await _ctx(db_session), "why?"))
    delta = [e for e in events if e.event == "delta"][0]
    assert "—" not in delta.data["text"]


async def test_step_cap_forces_final_answer(_env, db_session, monkeypatch):
    tool_resps = [
        _tool_response(_fn_call(
            "search_sessions", {"query": f"q{i}"}, call_id=f"c{i}",
        ))
        for i in range(8)
    ]
    final = _final_response(AskAnswer(answer_markdown="best effort"))
    client = _mock_client(monkeypatch, tool_resps + [final])
    events = await _collect(run_ask(await _ctx(db_session), "why?"))
    assert events[-1].event == "done"
    assert events[-1].data == {"best_effort": True}
    # 8 tool rounds + 1 forced final call
    assert client.responses.parse.call_count == 9
    # The forced final call must not offer tools.
    assert client.responses.parse.call_args.kwargs["tools"] == []


async def test_github_failure_aborts_with_signin_for_anonymous(
    _env, db_session, monkeypatch, respx_mock,
):
    respx_mock.get("https://api.github.test/search/issues").respond(
        403,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9"},
    )
    _mock_client(monkeypatch, [
        _tool_response(_fn_call("search_prs", {"query": "auth"})),
    ])
    ctx = await _ctx(db_session, viewer_token=None)
    events = await _collect(run_ask(ctx, "why?"))
    assert events[-1].event == "error"
    assert events[-1].data["code"] == "github_auth_required"
    assert "sign in" in events[-1].data["message"].lower()
    assert not any(e.event == "done" for e in events)


async def test_github_failure_signed_in_is_unavailable(
    _env, db_session, monkeypatch, respx_mock,
):
    respx_mock.get("https://api.github.test/search/issues").respond(
        502, json={},
    )
    _mock_client(monkeypatch, [
        _tool_response(_fn_call("search_prs", {"query": "auth"})),
    ])
    ctx = await _ctx(db_session, viewer_token="gho_viewer")
    events = await _collect(run_ask(ctx, "why?"))
    assert events[-1].event == "error"
    assert events[-1].data["code"] == "github_unavailable"


async def test_no_llm_config_yields_error(db_session, monkeypatch):
    for var in (
        "VIBESHUB_OPENAI_API_KEY", "VIBESHUB_OPENAI_ENDPOINT",
        "VIBESHUB_OPENAI_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    events = await _collect(run_ask(await _ctx(db_session), "why?"))
    assert [e.event for e in events] == ["error"]
    assert events[0].data["code"] == "llm_unavailable"


async def test_llm_exception_yields_error(_env, db_session, monkeypatch):
    client = MagicMock()
    client.responses.parse.side_effect = RuntimeError("boom")
    monkeypatch.setattr(
        "app.agents.ask.pipeline.get_client", lambda: client,
    )
    events = await _collect(run_ask(await _ctx(db_session), "why?"))
    assert events[-1].event == "error"
    assert events[-1].data["code"] == "llm_unavailable"


async def test_github_disabled_emits_notice_and_omits_tools(
    _env, db_session, monkeypatch,
):
    client = _mock_client(monkeypatch, [
        _final_response(AskAnswer(answer_markdown="sessions only")),
    ])
    ctx = await _ctx(db_session, github_enabled=False)
    events = await _collect(run_ask(ctx, "why?"))
    assert events[0].event == "notice"
    assert "sign in" in events[0].data["message"].lower()
    names = [
        t["name"]
        for t in client.responses.parse.call_args.kwargs["tools"]
    ]
    assert "search_prs" not in names


async def test_multi_call_response_respects_budget(
    _env, db_session, monkeypatch,
):
    # One response asks for 10 tools at once; the budget is 8.
    calls = [
        _fn_call("search_sessions", {"query": f"q{i}"}, call_id=f"c{i}")
        for i in range(10)
    ]
    final = _final_response(AskAnswer(answer_markdown="best effort"))
    client = _mock_client(monkeypatch, [_tool_response(*calls), final])
    events = await _collect(run_ask(await _ctx(db_session), "why?"))

    # Only the first 8 tools run (and emit status); the last 2 do not.
    status_events = [e for e in events if e.event == "status"]
    assert len(status_events) == 8

    # Every one of the 10 call_ids is still answered on the next turn.
    second_input = client.responses.parse.call_args_list[1].kwargs["input"]
    outputs = [
        i for i in second_input
        if isinstance(i, dict) and i.get("type") == "function_call_output"
    ]
    assert len(outputs) == 10
    exhausted = json.dumps({"error": "tool budget exhausted"})
    assert [o["output"] for o in outputs[-2:]] == [exhausted, exhausted]
    assert all(o["output"] != exhausted for o in outputs[:8])

    # Budget spent -> the follow-up is a forced final call with no tools.
    assert client.responses.parse.call_args_list[1].kwargs["tools"] == []
    assert events[-1].event == "done"
    assert events[-1].data == {"best_effort": True}


async def test_response_output_echoed_into_next_input(
    _env, db_session, monkeypatch,
):
    fn = _fn_call("search_sessions", {"query": "healthcheck"}, call_id="cX")
    final = _final_response(AskAnswer(answer_markdown="done"))
    client = _mock_client(monkeypatch, [_tool_response(fn), final])
    await _collect(run_ask(await _ctx(db_session), "why?"))

    second_input = client.responses.parse.call_args_list[1].kwargs["input"]
    # The model's own output item is echoed back verbatim (same object)...
    assert any(item is fn for item in second_input)
    idx = next(k for k, item in enumerate(second_input) if item is fn)
    # ...immediately followed by the matching function_call_output.
    output = second_input[idx + 1]
    assert output["type"] == "function_call_output"
    assert output["call_id"] == "cX"
