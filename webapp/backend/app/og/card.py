"""Assemble the data for a trace's social card from its Trace row.

Pure, no I/O: a Trace in, a CardData out. The renderer
(`app.og.render`) turns CardData into a PNG; the route
(`app.api.og`) handles caching and serving.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.api.spa_seo import _agent_label
from app.storage.models import Trace


@dataclass(frozen=True)
class CardData:
    """Everything the renderer needs to draw one card.

    `ask`/`decisions`/`dead_ends` are None when the trace has no digest or
    the field is empty; `decisions`/`dead_ends` carry the first digest item.
    `repo_ref` is the header chip (`acme/site #482`, `acme/site`, or None
    for a standalone trace).
    """

    subject: str
    agent_label: str
    repo_ref: str | None
    owner_login: str | None
    ask: str | None
    decisions: str | None
    dead_ends: str | None
    message_count: int
    subagent_count: int


def _clean(value: object) -> str | None:
    """Return a non-empty stripped string, or None for blank/non-strings."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _first(value: object) -> str | None:
    """First item of a digest list field, or None. Non-list values (old
    string-shape digests awaiting the re-digest backfill) read as absent."""
    if isinstance(value, list) and value:
        return _clean(value[0])
    return None


def build_card_data(trace: Trace) -> CardData:
    repo = trace.repo_full_name
    pr = trace.pr_number

    if repo and pr is not None:
        repo_ref: str | None = f"{repo} #{pr}"
    elif repo:
        repo_ref = repo
    else:
        repo_ref = None

    # Subject mirrors spa_seo._render_trace_head: PR title, else repo #PR,
    # else the trace id.
    subject = trace.pr_title or (
        f"{repo} #{pr}"
        if repo and pr is not None
        else f"Trace {trace.short_id}"
    )

    digest = trace.digest_json or {}
    return CardData(
        subject=subject,
        agent_label=_agent_label(trace.platform),
        repo_ref=repo_ref,
        owner_login=trace.owner_login,
        ask=_clean(digest.get("ask")),
        decisions=_first(digest.get("decisions")),
        dead_ends=_first(digest.get("dead_ends")),
        message_count=trace.message_count or 0,
        subagent_count=trace.agent_count or 0,
    )
