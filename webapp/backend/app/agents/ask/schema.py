"""Structured output schema for the repo ask agent.

AskAnswer is what the final responses.parse call must return. Citations
are post-validated against the repo's actually-visible traces so the
model cannot invent session links (mirrors the digest agent's anchor
validation).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class AskCitation(BaseModel):
    type: Literal["session", "chapter", "pr", "commit", "file"]
    title: str = Field(max_length=200)
    trace_short_id: Optional[str] = None
    anchor_uuid: Optional[str] = None
    pr_number: Optional[int] = None
    url: Optional[str] = None


class AskAnswer(BaseModel):
    answer_markdown: str
    citations: list[AskCitation] = Field(default_factory=list, max_length=10)


def validate_citations(
    answer: AskAnswer,
    *,
    valid_short_ids: set[str],
    repo_full_name: str,
) -> list[AskCitation]:
    """Drop citations the viewer could not follow.

    session/chapter: must name a visible trace. pr: needs a number or a
    url (number alone gets the canonical GitHub url). commit/file: need
    a url. Order is preserved.
    """
    kept: list[AskCitation] = []
    for c in answer.citations:
        if c.type in ("session", "chapter"):
            if c.trace_short_id in valid_short_ids:
                kept.append(c)
        elif c.type == "pr":
            if c.url:
                kept.append(c)
            elif c.pr_number is not None:
                c.url = (
                    f"https://github.com/{repo_full_name}/pull/{c.pr_number}"
                )
                kept.append(c)
        else:  # commit / file
            if c.url:
                kept.append(c)
    return kept
