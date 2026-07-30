"""Single-turn tool loop for repo ask.

run_ask is an async generator of AskEvents; the API layer turns them
into SSE frames. Budget: 8 tool calls / 60s, then a forced final call
with no tools (answer flagged best-effort). The blocking OpenAI SDK
call runs in a thread so the event loop keeps streaming.

Every run records one agent_run row (agent_name="repo_ask"). Never
raises: all failures become a terminal `error` event.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from functools import partial
from typing import AsyncIterator

from sqlalchemy import select

from app.agents._client import get_client, get_model
from app.agents._usage import Outcome, record_run
from app.agents.ask.prompt import SYSTEM_PROMPT, user_prompt
from app.agents.ask.schema import AskAnswer, validate_citations
from app.agents.ask.tools import (
    AskGitHubError,
    ToolContext,
    execute_tool,
    tool_schemas,
)
from app.agents.digest.schema import strip_em_dashes
from app.storage.models import Trace

_MAX_TOOL_CALLS = 8
_WALL_CLOCK_BUDGET_S = 60.0
_MAX_OUTPUT_TOKENS = 2500
_MAX_TOOL_OUTPUT_CHARS = 8000

_NO_GITHUB_NOTICE = (
    "Sign in with GitHub to include PRs and code in answers."
)
_SIGNIN_MESSAGE = (
    "GitHub could not be reached for this ask. Sign in with GitHub to "
    "ask about PRs and code."
)
_GITHUB_DOWN_MESSAGE = (
    "GitHub could not be reached for this ask. Try again in a bit, or "
    "reconnect your GitHub account."
)
_LLM_DOWN_MESSAGE = "The ask agent is unavailable right now. Try again soon."


@dataclass
class AskEvent:
    event: str
    data: dict


def _status_text(name: str, args: dict) -> str:
    if name == "search_sessions":
        q = str(args.get("query", ""))[:60]
        return f'searching sessions for "{q}"'
    if name == "get_session":
        return "reading a session digest"
    if name == "list_sessions":
        return "listing recent sessions"
    if name == "search_prs":
        return "searching pull requests"
    if name == "get_pr":
        return f"reading PR #{args.get('number', '?')}"
    if name == "list_commits":
        return "listing commits"
    if name == "get_file":
        return f"reading {args.get('path', 'a file')}"
    return name


async def run_ask(
    ctx: ToolContext, question: str,
) -> AsyncIterator[AskEvent]:
    client = get_client()
    model = get_model()
    started = time.monotonic()
    if client is None:
        await _record(ctx, None, 0, 0, started, Outcome.SKIP_NO_CONFIG,
                      tool_calls=0)
        yield AskEvent("error", {
            "code": "llm_unavailable", "message": _LLM_DOWN_MESSAGE,
        })
        return

    if not ctx.github_enabled:
        yield AskEvent("notice", {"message": _NO_GITHUB_NOTICE})

    tools = tool_schemas(ctx.github_enabled)
    input_items: list = [{
        "role": "user",
        "content": user_prompt(ctx.repo_full_name, question),
    }]
    tool_calls = 0
    in_tok = out_tok = 0
    best_effort = False

    while True:
        force_final = (
            tool_calls >= _MAX_TOOL_CALLS
            or (time.monotonic() - started) > _WALL_CLOCK_BUDGET_S
        )
        try:
            response = await asyncio.to_thread(partial(
                client.responses.parse,
                model=model,
                instructions=SYSTEM_PROMPT,
                input=input_items,
                tools=[] if force_final else tools,
                text_format=AskAnswer,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
                reasoning={"effort": "low"},
            ))
        except Exception as exc:  # noqa: BLE001
            await _record(ctx, model, in_tok, out_tok, started,
                          Outcome.FAIL_CALL, error=str(exc)[:500],
                          tool_calls=tool_calls)
            yield AskEvent("error", {
                "code": "llm_unavailable", "message": _LLM_DOWN_MESSAGE,
            })
            return

        usage = getattr(response, "usage", None)
        in_tok += _safe_int(usage, "input_tokens")
        out_tok += _safe_int(usage, "output_tokens")

        calls = [
            item for item in (response.output or [])
            if getattr(item, "type", None) == "function_call"
        ]
        if calls and not force_final:
            # Echo the model's own output items (function_call +
            # reasoning) back verbatim so the follow-up turn stays valid
            # under the Responses API; then answer every call_id in order.
            input_items.extend(response.output)
            for call in calls:
                if tool_calls >= _MAX_TOOL_CALLS:
                    # Budget spent mid-response: answer the call_id so the
                    # API contract holds, but run nothing further.
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(
                            {"error": "tool budget exhausted"}
                        ),
                    })
                    continue
                tool_calls += 1
                try:
                    args = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield AskEvent(
                    "status", {"text": _status_text(call.name, args)},
                )
                try:
                    result = await execute_tool(ctx, call.name, args)
                except AskGitHubError as exc:
                    await _record(ctx, model, in_tok, out_tok, started,
                                  Outcome.FAIL_CALL,
                                  error=f"github: {exc}"[:500],
                                  tool_calls=tool_calls)
                    if ctx.viewer_token is None:
                        yield AskEvent("error", {
                            "code": "github_auth_required",
                            "message": _SIGNIN_MESSAGE,
                        })
                    else:
                        yield AskEvent("error", {
                            "code": "github_unavailable",
                            "message": _GITHUB_DOWN_MESSAGE,
                        })
                    return
                input_items.append({
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(result)[:_MAX_TOOL_OUTPUT_CHARS],
                })
            continue

        answer = response.output_parsed
        if answer is None:
            await _record(ctx, model, in_tok, out_tok, started,
                          Outcome.FAIL_SCHEMA,
                          error="output_parsed is None",
                          tool_calls=tool_calls)
            yield AskEvent("error", {
                "code": "llm_unavailable", "message": _LLM_DOWN_MESSAGE,
            })
            return
        best_effort = force_final
        break

    answer.answer_markdown = strip_em_dashes(answer.answer_markdown)
    for c in answer.citations:
        c.title = strip_em_dashes(c.title)

    visible = (await ctx.session.execute(
        select(Trace.short_id).where(
            Trace.repo_full_name == ctx.repo_full_name,
            Trace.deleted_at.is_(None),
            *( [Trace.is_private.is_(False)] if not ctx.include_private else [] ),
        )
    )).scalars().all()
    citations = validate_citations(
        answer,
        valid_short_ids=set(visible),
        repo_full_name=ctx.repo_full_name,
    )

    yield AskEvent("delta", {"text": answer.answer_markdown})
    yield AskEvent("citations", {
        "citations": [c.model_dump() for c in citations],
    })
    await _record(ctx, model, in_tok, out_tok, started, Outcome.OK,
                  tool_calls=tool_calls, best_effort=best_effort)
    yield AskEvent("done", {"best_effort": best_effort})


async def _record(
    ctx, model, in_tok, out_tok, started, outcome,
    *, tool_calls: int, best_effort: bool = False, error: str | None = None,
) -> None:
    await record_run(
        ctx.session, agent_name="repo_ask", trace_id=None, model=model,
        input_tokens=in_tok, output_tokens=out_tok,
        latency_ms=int((time.monotonic() - started) * 1000),
        outcome=outcome, error_detail=error,
        extra={
            "repo": ctx.repo_full_name,
            "tool_calls": tool_calls,
            "best_effort": best_effort,
        },
    )


def _safe_int(usage_obj, attr: str) -> int:
    if usage_obj is None:
        return 0
    try:
        return int(getattr(usage_obj, attr, 0) or 0)
    except (TypeError, ValueError):
        return 0
