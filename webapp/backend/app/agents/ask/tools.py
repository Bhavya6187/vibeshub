"""The seven ask tools: three over vibeshub's own corpus, four over the
live GitHub API via PublicGitHubClient (which picks viewer token, then
the server fallback token, and raises GitHubAuthError when neither is
set).

GitHub 404s are normal tool results (the model keeps going). Auth, rate
limit, and upstream errors raise AskGitHubError: the spec requires the
run to abort with a user-visible error, never silently degrade.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.github.public_client import (
    GitHubAuthError,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubUpstreamError,
    PublicGitHubClient,
)
from app.search.query import search_documents
from app.storage.models import Trace

_MAX_FILE_LINES = 400
_MAX_PR_BODY = 4000


class AskGitHubError(Exception):
    """GitHub failed mid-ask; the run must abort with a visible error."""


@dataclass
class ToolContext:
    session: AsyncSession
    repo_full_name: str
    include_private: bool
    gh: PublicGitHubClient
    viewer_token: str | None
    github_enabled: bool


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


_SESSION_TOOLS = [
    _fn(
        "search_sessions",
        "Full-text search this repo's uploaded agent-session digests "
        "(summaries, chapters, touched files). Returns matching snippets "
        "with trace_short_id and anchor_uuid for citations.",
        {"query": {"type": "string", "description": "search terms"}},
        ["query"],
    ),
    _fn(
        "get_session",
        "Read one session's full digest: ask, decisions, dead ends, "
        "tests, chapters, file notes, and its PR link.",
        {"trace_short_id": {"type": "string"}},
        ["trace_short_id"],
    ),
    _fn(
        "list_sessions",
        "List this repo's most recent sessions (newest first, top 20).",
        {},
        [],
    ),
]

_GITHUB_TOOLS = [
    _fn(
        "search_prs",
        "Search this repo's pull requests on GitHub by keywords.",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _fn(
        "get_pr",
        "Read one pull request: title, description, merge info, changed "
        "file paths.",
        {"number": {"type": "integer"}},
        ["number"],
    ),
    _fn(
        "list_commits",
        "List recent commits, optionally only those touching a path.",
        {"path": {"type": "string", "description": "optional file path"}},
        [],
    ),
    _fn(
        "get_file",
        "Read a file's current content from the default branch "
        "(first 400 lines).",
        {"path": {"type": "string"}},
        ["path"],
    ),
]


def tool_schemas(github_enabled: bool) -> list[dict]:
    return _SESSION_TOOLS + (_GITHUB_TOOLS if github_enabled else [])


async def execute_tool(ctx: ToolContext, name: str, args: dict):
    handlers = {
        "search_sessions": _search_sessions,
        "get_session": _get_session,
        "list_sessions": _list_sessions,
        "search_prs": _search_prs,
        "get_pr": _get_pr,
        "list_commits": _list_commits,
        "get_file": _get_file,
    }
    handler = handlers.get(name)
    if handler is None:
        return {"error": "unknown tool"}
    return await handler(ctx, args)


# --- session tools ----------------------------------------------------


async def _search_sessions(ctx: ToolContext, args: dict):
    hits = await search_documents(
        ctx.session,
        repo_full_name=ctx.repo_full_name,
        query=str(args.get("query", "")),
        include_private=ctx.include_private,
    )
    return {"hits": [
        {
            "type": h.source_type, "title": h.title, "snippet": h.snippet,
            "trace_short_id": h.trace_short_id, "anchor_uuid": h.anchor_uuid,
            "pr_number": h.pr_number, "date": h.created_at,
        }
        for h in hits
    ]}


def _visible_traces_stmt(ctx: ToolContext):
    stmt = select(Trace).where(
        Trace.repo_full_name == ctx.repo_full_name,
        Trace.deleted_at.is_(None),
    )
    if not ctx.include_private:
        stmt = stmt.where(Trace.is_private.is_(False))
    return stmt


async def _get_session(ctx: ToolContext, args: dict):
    stmt = _visible_traces_stmt(ctx).where(
        Trace.short_id == str(args.get("trace_short_id", ""))
    )
    trace = (await ctx.session.execute(stmt)).scalar_one_or_none()
    if trace is None:
        return {"error": "session not found"}
    digest = trace.digest_json or {}
    return {
        "trace_short_id": trace.short_id,
        "title": trace.title or trace.pr_title,
        "date": trace.created_at.isoformat(),
        "pr_number": trace.pr_number,
        "pr_url": trace.pr_url,
        "ask": digest.get("ask"),
        "decisions": digest.get("decisions"),
        "dead_ends": digest.get("dead_ends"),
        "tests": digest.get("tests"),
        "learnings": digest.get("learnings") or [],
        "chapters": digest.get("chapters") or [],
        "file_notes": digest.get("file_notes") or [],
    }


async def _list_sessions(ctx: ToolContext, args: dict):
    stmt = _visible_traces_stmt(ctx).order_by(
        Trace.created_at.desc()
    ).limit(20)
    rows = (await ctx.session.execute(stmt)).scalars().all()
    return {"sessions": [
        {
            "trace_short_id": t.short_id,
            "title": t.title or t.pr_title
                or (t.digest_json or {}).get("ask"),
            "pr_number": t.pr_number,
            "date": t.created_at.isoformat(),
        }
        for t in rows
    ]}


# --- GitHub tools -----------------------------------------------------


async def _gh_json(ctx: ToolContext, path: str, params: dict | None = None):
    try:
        return await ctx.gh.get_json(
            path, viewer_token=ctx.viewer_token, params=params,
        )
    except GitHubNotFound:
        return None
    except (GitHubAuthError, GitHubRateLimited, GitHubUpstreamError) as exc:
        raise AskGitHubError(str(exc)) from exc


async def _search_prs(ctx: ToolContext, args: dict):
    q = f"{args.get('query', '')} repo:{ctx.repo_full_name} type:pr"
    data = await _gh_json(ctx, "/search/issues", {"q": q, "per_page": 10})
    if data is None:
        return {"error": "not found"}
    return {"prs": [
        {
            "number": it.get("number"), "title": it.get("title"),
            "state": it.get("state"), "updated_at": it.get("updated_at"),
        }
        for it in data.get("items", [])
    ]}


async def _get_pr(ctx: ToolContext, args: dict):
    number = args.get("number")
    pr = await _gh_json(
        ctx, f"/repos/{ctx.repo_full_name}/pulls/{number}",
    )
    if pr is None:
        return {"error": "not found"}
    files = await _gh_json(
        ctx,
        f"/repos/{ctx.repo_full_name}/pulls/{number}/files",
        {"per_page": 30},
    )
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "body": (pr.get("body") or "")[:_MAX_PR_BODY],
        "merged_at": pr.get("merged_at"),
        "author": (pr.get("user") or {}).get("login"),
        "url": pr.get("html_url"),
        "files": [f.get("filename") for f in (files or [])],
    }


async def _list_commits(ctx: ToolContext, args: dict):
    params: dict = {"per_page": 15}
    if args.get("path"):
        params["path"] = args["path"]
    data = await _gh_json(
        ctx, f"/repos/{ctx.repo_full_name}/commits", params,
    )
    if data is None:
        return {"error": "not found"}
    return {"commits": [
        {
            "sha": (c.get("sha") or "")[:7],
            "message": (c.get("commit", {}).get("message") or "")
                .split("\n", 1)[0],
            "date": c.get("commit", {}).get("author", {}).get("date"),
            "author": (c.get("author") or {}).get("login"),
            "url": c.get("html_url"),
        }
        for c in data
    ]}


async def _get_file(ctx: ToolContext, args: dict):
    path = str(args.get("path", "")).lstrip("/")
    data = await _gh_json(
        ctx, f"/repos/{ctx.repo_full_name}/contents/{path}",
    )
    if data is None:
        return {"error": "not found"}
    if isinstance(data, list):
        return {"error": "path is a directory"}
    raw = data.get("content")
    if not raw:
        return {"error": "file too large to read"}
    text = base64.b64decode(raw).decode("utf-8", errors="replace")
    lines = text.split("\n")
    truncated = len(lines) > _MAX_FILE_LINES
    return {
        "path": path,
        "content": "\n".join(lines[:_MAX_FILE_LINES]),
        "truncated": truncated,
    }
