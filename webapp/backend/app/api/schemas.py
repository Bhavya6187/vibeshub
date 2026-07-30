from __future__ import annotations

import logging

from pydantic import BaseModel, Field, ValidationError, field_validator

log = logging.getLogger("vibeshub.api.schemas")


# Note: IngestRequest is gone — /api/ingest now takes raw tar bytes via the
# request body, with PR metadata in X-Vibeshub-* headers.


class AgentSummary(BaseModel):
    agent_id: str
    tool_use_id: str | None
    agent_type: str
    description: str
    message_count: int


class DigestChapter(BaseModel):
    anchor_uuid: str
    title: str
    caption: str


class FileNote(BaseModel):
    path: str
    caption: str


class TraceDigest(BaseModel):
    ask: str
    decisions: list[str] = Field(default_factory=list)
    dead_ends: list[str] = Field(default_factory=list)
    learnings: list[str] = Field(default_factory=list)
    tests: str
    chapters: list[DigestChapter] = Field(default_factory=list)
    file_notes: list[FileNote] = Field(default_factory=list)


def _digest_or_none(cls, value):
    """Old-shape digests (string decisions/dead_ends) linger until the
    re-digest backfill lands; serialize them as "no digest" instead of
    failing the whole response."""
    if value is None or isinstance(value, TraceDigest):
        return value
    try:
        return TraceDigest.model_validate(value)
    except ValidationError as exc:
        log.warning(
            "ai_digest failed TraceDigest validation; hiding digest: %s",
            str(exc)[:200],
        )
        return None


class TraceSummary(BaseModel):
    trace_id: str
    short_id: str
    # None for an anonymous (no-login) upload that has not been claimed.
    owner_login: str | None
    # None for a standalone trace (no PR/repo association).
    repo_full_name: str | None
    pr_number: int | None
    pr_url: str | None
    pr_title: str | None
    title: str | None = None
    platform: str
    byte_size: int
    message_count: int
    created_at: str
    is_private: bool = False

    ai_digest: TraceDigest | None = None
    agent_count: int = 0
    agents: list[AgentSummary] = Field(default_factory=list)

    _coerce_digest = field_validator("ai_digest", mode="before")(_digest_or_none)


class IngestResponse(BaseModel):
    trace_id: str
    short_id: str
    trace_url: str
    created: bool = True
    # One-time token to later claim an anonymous (no-login) upload. None for
    # the CLI ingest path and for signed-in web uploads.
    claim_token: str | None = None
    ai_digest: TraceDigest | None = None

    _coerce_digest = field_validator("ai_digest", mode="before")(_digest_or_none)


class ClaimRequest(BaseModel):
    claim_token: str
