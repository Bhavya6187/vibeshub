# Digest restructure: item-granularity fields, dual-audience prompt

**Date:** 2026-07-19
**Status:** Approved

## Problem

The trace digest currently packs decisions, dead ends, and file activity
into single ≤200-char sentences. That shape serves neither consumer well:

- **Search** (`app/search/index.py`) mashes `ask + decisions + dead_ends
  + tests` into one summary document, so FTS ranks and snippets at
  whole-digest granularity and the literal filler string "none." gets
  indexed.
- **Frontend** (`DigestPanel`) renders three prose rows and needs a
  regex hack (`EMPTY_VALUE`) to drop "none." filler.
- The `files` field is dead weight: its only consumer is the ask agent's
  `get_session` tool, where it is redundant with `file_notes`. The
  panel, OG card, and search index never use it.
- The digest never captures mid-task discoveries (constraints, gotchas)
  that are invisible in the final diff, even though the repo-ask feature
  promises exactly that "reasoning that never reached git".
- The digest cache (`digest_input_hash`) hashes only the distilled
  trace, so prompt changes never invalidate cached digests.

## Decision

Restructure the shared digest fields so one structure serves both
consumers, rewrite the prompt to demand concrete identifiers, and
re-digest all existing traces.

Explicitly out of scope (deferred): decoupling `search/index.py` from
the digest shape behind a neutral interface, and unifying the duplicated
`TraceDigest` schema with the canonical `Digest` model.

## 1. Schema (`app/agents/digest/schema.py`)

```python
class Digest(BaseModel):
    ask: str                      # unchanged, ≤200
    decisions: list[str]          # NEW SHAPE: 1-6 items, each ≤200
    dead_ends: list[str]          # NEW SHAPE: 0-4 items, each ≤200
    learnings: list[str]          # NEW FIELD: 0-5 items, each ≤200
    tests: str                    # unchanged, ≤200
    chapters: list[Chapter]       # unchanged
    file_notes: list[FileNote]    # unchanged
    # files: REMOVED
```

Item count ceilings are schema-enforced (`max_length` 6 / 4 / 5 on the
list fields, like `chapters` today); minimums and item templates are
prompt-enforced only, so a thin digest degrades rather than failing
validation. Item shapes:

- `decisions[]`: "chose X over Y because Z", naming symbols, files, and
  libraries verbatim.
- `dead_ends[]`: "tried X, abandoned because Y".
- `learnings[]`: constraints and gotchas discovered mid-task that are
  not visible in the final diff (e.g. "Cursor's afterShellExecution
  payload has no cwd").

`dead_ends` and `learnings` may be empty; an empty list means "nothing
to report", never filler items. `decisions` should always carry at
least one item (prompt asks for 1-6). `tests` keeps the string shape
and writes exactly `none` when empty.

## 2. Prompt (`app/agents/digest/prompt.py`)

Full rewrite of the output block to match the schema, plus rules:

- **Concrete nouns everywhere**: name exact functions, files, error
  strings, flags, and libraries ("moved retry from fetch_pr into
  gh_client.get_json", not "refactored the retry logic"). State in the
  prompt that digests are full-text searched, so exact names are what
  teammates find.
- `ask` reads as a title stating the goal in the model's own words,
  never a verbatim quote of the user prompt (it is the hero-title and
  search-title fallback for standalone traces).
- Per-item templates for decisions / dead_ends / learnings as above,
  with item count caps (1-6 / 0-4 / 0-5).
- Chapter titles lead with the specific action or subsystem ("Fix
  abort-stream race"), never generic ("More fixes"); captions state the
  segment's outcome, not just its activity.
- Existing rules retained: anchor_uuid and file_notes.path must appear
  in the input, drop rather than guess; no em-dashes; no URLs, markdown,
  or emoji.

## 3. Pipeline (`app/agents/digest/pipeline.py`)

- `digest_input_hash` becomes `sha256(SYSTEM_PROMPT + "\0" + distilled)`
  so any prompt edit auto-invalidates cached digests. This is what makes
  the backfill (and all future prompt iteration) work.
- Em-dash sweep updated: strings `ask`, `tests`; each item of
  `decisions`, `dead_ends`, `learnings`; chapters and file_notes as
  today.
- Anchor validation and file_notes path validation unchanged.

## 4. Indexing (`app/search/index.py`)

`explode_digest` changes:

- One `SearchDocument` per decisions/dead_ends/learnings item, with new
  `source_type` values `"decision"`, `"dead_end"`, `"learning"` (all fit
  the `String(16)` column; update the comment in `storage/models.py`).
  Body is the item text; title is the trace display title (same fallback
  chain as the summary doc).
- The summary doc body shrinks to `ask` + `tests` so items are not
  double-weighted in `ts_rank`.
- Chapter docs and the joined `files` doc (from `file_notes`) are
  unchanged.

Result: FTS ranks and snippets at item granularity, and the ask agent's
`search_sessions` tool returns typed hits (it already passes
`source_type` through as `type`). There is no human-facing search UI, so
no frontend search work.

## 5. API and frontend ripple

- `api/schemas.py` `TraceDigest` and frontend `types.ts` `TraceDigest`
  updated together (both copies, or Pydantic silently strips the new
  fields): `decisions: list[str]`, `dead_ends: list[str]`,
  `learnings: list[str]`, `files` removed.
- **Old-shape window**: until the backfill reaches a trace, its stored
  `digest_json` has string `decisions`/`dead_ends`, which would fail
  response validation. Add a `mode="before"` field validator on
  `ai_digest` (in `TraceSummary` / `IngestResponse`) that coerces any
  non-conforming digest to `None`. One boundary guard, no dual-shape
  rendering logic; old traces briefly show no digest and self-heal as
  the backfill lands.
- `og/card.py`: `CardData.decisions` / `dead_ends` take the first item
  of each list (cards only have room for one line each).
- `agents/ask/tools.py` `_get_session`: drop `files`, add `learnings`
  (lists serialize as-is).
- `DigestPanel.tsx`: keep the Ask row; render Key decisions / Dead ends
  / Learnings as compact bullet rows. Empty lists omit the group, which
  retires the `EMPTY_VALUE` regex for those fields. Styling stays subtle
  and consistent with the existing panel; no restructure.

## 6. Backfill (`scripts/backfill_redigest.py`)

One-shot script modeled on `scripts/backfill_search_documents.py`:

1. Select non-deleted traces with `blob_prefix` set. Skip and count v1
   `blob_path`-only rows.
2. Per trace: load `{prefix}converted.jsonl` if present, else
   `{prefix}main.jsonl`; load subagent blobs (converted variant
   preferred) keyed the same way the upload path keys them
   (`toolUseId` from agent meta, falling back to agent_id).
3. `compute_digest(...)` — the prompt-aware hash guarantees a real LLM
   call for old digests — then `index_trace_documents(...)`, then commit
   per trace.
4. Print a summary line (re-digested / skipped-unchanged / failed /
   v1-skipped counts).

Properties: resumable and idempotent (already-redigested traces
hash-match and record `SKIP_UNCHANGED`); a failed LLM call keeps the old
digest_json (hidden by the boundary validator rather than 500ing);
sequential, one low-reasoning-effort OpenAI call per trace.

## 7. Testing and rollout

- Unit tests: schema validation, em-dash sweep over list fields,
  prompt-aware hash (old hash + same input → recompute; same prompt +
  input → skip), `explode_digest` per-item docs and shrunken summary
  body, `ai_digest` boundary validator (old shape → `None`), DigestPanel
  bullet groups, backfill script against SQLite fixtures with a stubbed
  LLM client.
- Prompt is checked against the three digest fixtures in
  `tests/agents/digest/fixtures/` per the existing convention.
- Backend tests run with `env/bin/pytest` (the `.venv` lacks pytest).
- Rollout: deploy, run the backfill against production, eyeball a
  handful of re-digested traces early in the run (the script is
  resumable, so stopping mid-run is safe).

## Noted, not in scope

- `search/index.py` still indexes the literal `tests` string "none" in
  the summary doc; skipping it is a one-line follow-up.
- The digest→search decoupling and TraceDigest schema unification
  remain deferred.
