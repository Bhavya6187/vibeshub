from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import IngestResponse
from app.api.trace_service import create_or_update_trace, resolve_association
from app.auth.github import GitHubAuthError, GitHubClient
from app.deps import get_blob_store, get_github, get_app_settings, get_session
from app.redact.bundle import BundleError, BundleSizeError, unpack_and_redact
from app.settings import Settings
from app.storage.blob import BlobStore


router = APIRouter()


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return authorization.split(None, 1)[1].strip()


def _trace_url(settings: Settings, sid: str) -> str:
    base = settings.public_base_url.rstrip("/")
    return f"{base}/t/{sid}"


def _require_header(value: str | None, name: str) -> str:
    if not value:
        raise HTTPException(
            status_code=400, detail=f"missing required header: {name}"
        )
    return value


@router.post(
    "/api/ingest",
    status_code=status.HTTP_201_CREATED,
    response_model=IngestResponse,
)
async def ingest(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_vibeshub_pr_url: Annotated[str | None, Header()] = None,
    x_vibeshub_repo: Annotated[str | None, Header()] = None,
    x_vibeshub_platform: Annotated[str | None, Header()] = None,
    x_vibeshub_plugin_version: Annotated[str | None, Header()] = None,
    x_vibeshub_session_id: Annotated[str | None, Header()] = None,
    x_vibeshub_client_redactions: Annotated[str | None, Header()] = None,
    x_vibeshub_git_branch: Annotated[str | None, Header()] = None,
    x_vibeshub_git_commit: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
    blob_store: BlobStore = Depends(get_blob_store),
    github: GitHubClient = Depends(get_github),
    settings: Settings = Depends(get_app_settings),
) -> IngestResponse:
    token = _bearer_token(authorization)
    platform = _require_header(x_vibeshub_platform, "X-Vibeshub-Platform")
    plugin_version = _require_header(
        x_vibeshub_plugin_version, "X-Vibeshub-Plugin-Version"
    )
    try:
        redaction_count_client = int(x_vibeshub_client_redactions or "0")
    except ValueError:
        raise HTTPException(
            status_code=400, detail="invalid X-Vibeshub-Client-Redactions"
        )

    try:
        user = await github.verify_token(token)
    except GitHubAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))

    assoc = await resolve_association(
        github=github,
        token=token,
        uploader_login=user.login,
        pr_url=x_vibeshub_pr_url,
        repo_full_name=x_vibeshub_repo,
    )

    tar_bytes = await request.body()
    if len(tar_bytes) > settings.max_trace_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"upload exceeds {settings.max_trace_bytes} "
                f"compressed bytes"
            ),
        )

    try:
        unpacked = unpack_and_redact(
            tar_bytes, max_total_bytes=settings.max_trace_bytes
        )
    except BundleSizeError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except BundleError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Bad git metadata must never fail an upload: a commit that is not a full
    # lowercase-hex SHA is dropped, and an over-long branch name is truncated.
    git_commit = (
        x_vibeshub_git_commit
        if x_vibeshub_git_commit
        and re.fullmatch(r"[0-9a-f]{40}", x_vibeshub_git_commit)
        else None
    )
    git_branch = (x_vibeshub_git_branch or None)
    if git_branch is not None:
        git_branch = git_branch[:255]

    result = await create_or_update_trace(
        session=session,
        blob_store=blob_store,
        unpacked=unpacked,
        owner_login=user.login,
        platform=platform,
        plugin_version=plugin_version,
        session_id=x_vibeshub_session_id,
        redaction_count_client=redaction_count_client,
        repo_full_name=assoc.repo_full_name,
        pr_number=assoc.pr_number,
        pr_url=assoc.pr_url,
        pr_title=assoc.pr_title,
        is_private=assoc.is_private,
        git_branch=git_branch,
        git_commit=git_commit,
    )
    await session.commit()

    return IngestResponse(
        trace_id=str(result.trace.id),
        short_id=result.trace.short_id,
        trace_url=_trace_url(settings, result.trace.short_id),
        created=result.created,
        ai_digest=result.trace.digest_json,
    )
