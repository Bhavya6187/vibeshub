"""POST /api/repos/{owner}/{repo}/ask — the repo ask SSE endpoint.

Pre-stream failures are plain HTTP (400/401/404/429); once the stream
opens, failures arrive as SSE `error` events from the pipeline. Rate
limits are in-memory per spec: 5/hour per anonymous IP, 20/hour per
signed-in user.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.ask import ToolContext, run_ask
from app.api.traces import _can_view_repo, _viewer_token
from app.auth.scopes import has_repo_scope
from app.auth.sessions import get_current_user
from app.deps import (
    get_app_settings,
    get_public_github,
    get_repo_access,
    get_session,
)
from app.github.public_client import PublicGitHubClient
from app.github.repo_access import RepoAccessChecker
from app.ratelimit import SlidingWindowLimiter
from app.settings import Settings
from app.storage.models import Trace, User

router = APIRouter()

_QUESTION_MAX = 500
_anon_limiter = SlidingWindowLimiter(5, 3600)
_user_limiter = SlidingWindowLimiter(20, 3600)


class AskRequest(BaseModel):
    question: str


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Right-most entry is appended by our own proxy; left entries are
        # client-supplied and spoofable.
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


async def _trace_count(
    session: AsyncSession, full_name: str, *, public_only: bool,
) -> int:
    stmt = select(func.count(Trace.id)).where(
        Trace.repo_full_name == full_name,
        Trace.deleted_at.is_(None),
    )
    if public_only:
        stmt = stmt.where(Trace.is_private.is_(False))
    return (await session.execute(stmt)).scalar_one()


@router.post("/api/repos/{owner}/{repo}/ask")
async def ask_repo(
    owner: str,
    repo: str,
    body: AskRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    access: RepoAccessChecker = Depends(get_repo_access),
    gh: PublicGitHubClient = Depends(get_public_github),
):
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question_required")
    if len(question) > _QUESTION_MAX:
        raise HTTPException(status_code=400, detail="question_too_long")

    full_name = f"{owner}/{repo}"
    no_store = {"Cache-Control": "no-store"}

    include_private = False
    if user is not None:
        include_private = await _can_view_repo(
            full_name, user, settings, access,
        )

    public_count = await _trace_count(session, full_name, public_only=True)
    if public_count == 0:
        total = await _trace_count(session, full_name, public_only=False)
        if total == 0:
            raise HTTPException(
                status_code=404, detail="not_found", headers=no_store,
            )
        if user is None:
            raise HTTPException(
                status_code=401, detail="auth_required", headers=no_store,
            )
        if not include_private:
            raise HTTPException(
                status_code=404, detail="not_found", headers=no_store,
            )

    if user is not None:
        allowed = _user_limiter.allow(user.github_login)
    else:
        allowed = _anon_limiter.allow(_client_ip(request))
    if not allowed:
        raise HTTPException(
            status_code=429, detail="rate_limited",
            headers={"Retry-After": "3600", **no_store},
        )

    viewer_token = None
    if user is not None and has_repo_scope(user):
        viewer_token = _viewer_token(user, settings)
    github_enabled = bool(viewer_token or settings.github_fallback_token)

    ctx = ToolContext(
        session=session,
        repo_full_name=full_name,
        include_private=include_private,
        gh=gh,
        viewer_token=viewer_token,
        github_enabled=github_enabled,
    )

    async def stream():
        try:
            async for ev in run_ask(ctx, question):
                yield _sse(ev.event, ev.data)
        finally:
            # run_ask adds agent_run rows to this session; make them
            # durable even when the client disconnects mid-stream.
            await session.commit()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
