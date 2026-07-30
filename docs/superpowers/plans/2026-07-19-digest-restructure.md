# Digest Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the trace digest into item-granularity fields (`decisions`/`dead_ends` become lists, new `learnings` list, `files` dropped), rewrite the prompt to demand concrete identifiers, index one search doc per item, render bullet groups in DigestPanel, and re-digest every existing trace.

**Architecture:** The digest shape is defined in `app/agents/digest/schema.py` and mirrored (deliberately, per project convention) in `app/api/schemas.py` and frontend `types.ts`. The shape change must land atomically across the backend (schema, prompt, pipeline, search indexer, API, ask tools, OG card) because each layer consumes the raw `digest_json` dict; Task 1 does that in one commit. Frontend and the backfill script follow as separate commits. A `mode="before"` validator on `ai_digest` hides old-shape digests from API responses until the backfill re-digests them.

**Tech Stack:** FastAPI + SQLAlchemy async + Pydantic v2 (backend), OpenAI `responses.parse` Structured Outputs, React + vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-07-19-digest-restructure-design.md`

## Global Constraints

- Never use em-dashes ("—") in any user-facing string or digest content; the prompt bans them and `strip_em_dashes` sweeps on persist.
- Any digest field change must land in BOTH `app/agents/digest/schema.py` and `app/api/schemas.py` `TraceDigest`, and in frontend `src/types.ts`, or Pydantic silently strips it.
- Backend tests run from `webapp/backend` with `../../env/bin/pytest` (the `.venv` lacks pytest). Frontend tests: `cd webapp/frontend && npm test`; type-check via `npm run build`.
- Other sessions switch branches in this checkout. Every commit command must verify the branch inline: `[ "$(git branch --show-current)" = "repo-search" ] && git add ... && git commit ...`.
- Item count ceilings are schema-enforced (max 6 decisions / 4 dead_ends / 5 learnings, 200 chars per item); minimums and item templates ("chose X over Y because Z") are prompt-enforced only, so a thin digest degrades instead of failing validation.
- New `SearchDocument.source_type` values are exactly `"decision"`, `"dead_end"`, `"learning"` (all fit the `String(16)` column).

---

### Task 1: Backend digest contract flip (schema, prompt, pipeline, index, API, ask tools, OG card)

Every backend layer reads the raw `digest_json` dict, so the shape change is atomic: one task, component-by-component TDD inside it, one commit at the end when the whole backend suite is green.

**Files:**
- Modify: `webapp/backend/app/agents/digest/schema.py`
- Modify: `webapp/backend/app/agents/digest/prompt.py`
- Modify: `webapp/backend/app/agents/digest/pipeline.py`
- Modify: `webapp/backend/app/search/index.py`
- Modify: `webapp/backend/app/storage/models.py:193` (comment only)
- Modify: `webapp/backend/app/api/schemas.py`
- Modify: `webapp/backend/app/agents/ask/tools.py:170-184`
- Modify: `webapp/backend/app/og/card.py`
- Create: `webapp/backend/tests/agents/digest/test_prompt.py`
- Test: `webapp/backend/tests/agents/digest/test_schema.py`, `tests/agents/digest/test_pipeline.py`, `tests/search/test_index.py`, `tests/search/test_ingest_hook.py`, `tests/api/test_traces_digest.py`, `tests/agents/ask/test_tools.py`, `tests/test_og_card.py`, `tests/test_og_render.py`, `tests/test_og_route.py`, `tests/test_uploads.py`

**Interfaces:**
- Consumes: existing `Digest`, `compute_digest(session, trace, *, blob, subagent_blobs)`, `explode_digest(trace)`, `index_trace_documents(session, trace)`.
- Produces (later tasks rely on these): `Digest` with fields `ask: str`, `decisions: list[str]`, `dead_ends: list[str]`, `learnings: list[str]`, `tests: str`, `chapters`, `file_notes` (no `files`); `digest_input_hash = sha256(SYSTEM_PROMPT + "\0" + distilled)`; search docs with source_types `summary | decision | dead_end | learning | chapter | files`; API `TraceDigest` matching the new shape with old-shape dicts coerced to `None` on `ai_digest`.

**Canonical new-shape payload** (used, with small variations, in every test below):

```python
{
    "ask": "Add a /healthcheck route",
    "decisions": [
        "Chose an inline route in app/main.py over a separate router because YAGNI",
    ],
    "dead_ends": [
        "Tried a separate APIRouter, abandoned because one route does not justify it",
    ],
    "learnings": [
        "TestClient needs raise_server_exceptions=False to assert 500 responses",
    ],
    "tests": "test_health.py adds /healthcheck assertion",
    "chapters": [
        {"anchor_uuid": "u1", "title": "Frame the change",
         "caption": "User asks for /healthcheck."},
    ],
}
```

- [ ] **Step 1: Rewrite `tests/agents/digest/test_schema.py` for the new shape**

Replace the whole file with:

```python
import pytest
from pydantic import ValidationError

from app.agents.digest.schema import Chapter, Digest, FileNote, strip_em_dashes


def _digest(**over) -> Digest:
    base = dict(
        ask="Add a /healthcheck route",
        decisions=["Chose inline route in app/main.py over a router because YAGNI"],
        dead_ends=[],
        learnings=[],
        tests="none",
        chapters=[],
    )
    base.update(over)
    return Digest(**base)


def test_digest_accepts_new_shape():
    d = _digest(
        dead_ends=["Tried a separate APIRouter, abandoned as overkill"],
        learnings=["TestClient needs raise_server_exceptions=False"],
    )
    assert d.decisions[0].startswith("Chose inline route")
    assert d.learnings == ["TestClient needs raise_server_exceptions=False"]


def test_digest_rejects_missing_ask():
    with pytest.raises(ValidationError):
        Digest(  # type: ignore[call-arg]
            decisions=["d"], dead_ends=[], learnings=[],
            tests="t", chapters=[],
        )


def test_digest_rejects_old_string_shape():
    # Pre-restructure digests stored decisions/dead_ends as prose strings.
    # They must NOT validate; the API boundary hides them until the
    # re-digest backfill replaces them.
    with pytest.raises(ValidationError):
        Digest.model_validate({
            "ask": "a", "decisions": "prose", "files": "f",
            "tests": "t", "dead_ends": "prose", "chapters": [],
        })


def test_digest_has_no_files_field():
    assert "files" not in Digest.model_fields


def test_list_item_counts_are_capped():
    with pytest.raises(ValidationError):
        _digest(decisions=[f"d{i}" for i in range(7)])
    with pytest.raises(ValidationError):
        _digest(dead_ends=[f"e{i}" for i in range(5)])
    with pytest.raises(ValidationError):
        _digest(learnings=[f"l{i}" for i in range(6)])


def test_list_item_length_is_capped():
    with pytest.raises(ValidationError):
        _digest(decisions=["x" * 201])


def test_digest_caps_chapters_at_10():
    chapters = [
        Chapter(anchor_uuid=f"uuid-{i}", title=f"t{i}", caption=f"c{i}")
        for i in range(11)
    ]
    with pytest.raises(ValidationError):
        _digest(chapters=chapters)


def test_digest_caps_field_lengths():
    with pytest.raises(ValidationError):
        _digest(ask="x" * 201)


def test_chapter_caps_title_and_caption():
    with pytest.raises(ValidationError):
        Chapter(anchor_uuid="u", title="x" * 81, caption="c")
    with pytest.raises(ValidationError):
        Chapter(anchor_uuid="u", title="t", caption="x" * 161)


def test_strip_em_dashes_replaces_with_comma_between_words():
    assert strip_em_dashes("a — b") == "a, b"


def test_strip_em_dashes_handles_sentence_breaks():
    assert strip_em_dashes("one — two — three") == "one, two, three"


def test_strip_em_dashes_strips_unicode_em_dash_only():
    assert strip_em_dashes("file-name foo-bar") == "file-name foo-bar"


def test_digest_defaults_lists_to_empty():
    d = Digest.model_validate({"ask": "a", "tests": "d"})
    assert d.decisions == []
    assert d.dead_ends == []
    assert d.learnings == []
    assert d.file_notes == []


def test_file_note_round_trips():
    d = Digest.model_validate({
        "ask": "a", "tests": "d",
        "file_notes": [{"path": "src/x.ts", "caption": "Tighten the loop"}],
    })
    assert d.file_notes == [FileNote(path="src/x.ts", caption="Tighten the loop")]
```

- [ ] **Step 2: Run schema tests to verify they fail**

Run (from `webapp/backend`): `../../env/bin/pytest tests/agents/digest/test_schema.py -v`
Expected: FAIL (old `Digest` still requires `files` and string `decisions`).

- [ ] **Step 3: Implement the new `Digest` schema**

In `webapp/backend/app/agents/digest/schema.py`, replace the `Digest` class (keep `FileNote`, `Chapter`, `strip_em_dashes` as-is) and add the `Annotated` import:

```python
from typing import Annotated
```

```python
_Item = Annotated[str, Field(max_length=200)]


class Digest(BaseModel):
    ask: str = Field(max_length=200)
    decisions: list[_Item] = Field(default_factory=list, max_length=6)
    dead_ends: list[_Item] = Field(default_factory=list, max_length=4)
    learnings: list[_Item] = Field(default_factory=list, max_length=5)
    tests: str = Field(max_length=200)
    chapters: list[Chapter] = Field(default_factory=list, max_length=10)
    file_notes: list[FileNote] = Field(default_factory=list, max_length=20)
```

- [ ] **Step 4: Run schema tests to verify they pass**

Run: `../../env/bin/pytest tests/agents/digest/test_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing prompt-consistency test**

Create `webapp/backend/tests/agents/digest/test_prompt.py`:

```python
from app.agents.digest.prompt import SYSTEM_PROMPT
from app.agents.digest.schema import Digest


def test_prompt_names_every_digest_field():
    for field in Digest.model_fields:
        assert f'"{field}"' in SYSTEM_PROMPT, f"prompt missing {field}"


def test_prompt_does_not_mention_dropped_files_field():
    # '"files"' would not match '"file_notes"'; this pins the removal.
    assert '"files"' not in SYSTEM_PROMPT


def test_prompt_teaches_item_templates_and_search_voice():
    assert "chose X over Y because Z" in SYSTEM_PROMPT
    assert "tried X, abandoned because Y" in SYSTEM_PROMPT
    assert "full-text searched" in SYSTEM_PROMPT
```

Run: `../../env/bin/pytest tests/agents/digest/test_prompt.py -v`
Expected: FAIL (old prompt has `"files"`, no `"learnings"`, no templates).

- [ ] **Step 6: Rewrite the system prompt**

Replace `SYSTEM_PROMPT` in `webapp/backend/app/agents/digest/prompt.py` with (keep the module docstring, updating "5-line digest" wording if you wish):

```python
SYSTEM_PROMPT = """You read a distilled Claude Code session trace and \
return a structured digest: the ask, decisions, dead ends, learnings, \
tests, 3-8 semantic chapter anchors, and per-file notes. The reader is a \
teammate reviewing a PR; voice is "what changed and why", plain English.

These digests are full-text searched by teammates. Name the exact \
functions, files, error strings, flags, and libraries involved ("moved \
retry from fetch_pr into gh_client.get_json", not "refactored the retry \
logic"). Concrete names are what searches find.

The trace is presented as a sequence of lines, each prefixed with the \
source event's UUID in square brackets, e.g. [a1f8…] ASSISTANT: text.

## Output (strict JSON only)

{
  "ask": "<the goal of the session, 1 sentence>",
  "decisions": ["<1-6 one-sentence items>"],
  "dead_ends": ["<0-4 one-sentence items>"],
  "learnings": ["<0-5 one-sentence items>"],
  "tests": "<tests added/changed, or exactly 'none'. 1 sentence>",
  "chapters": [
    {
      "anchor_uuid": "<a UUID that appears in [brackets] in the input>",
      "title": "<2-6 word chapter heading>",
      "caption": "<1 sentence: what happens in this segment>"
    },
    ...
  ],
  "file_notes": [
    {
      "path": "<a file path that appears in the input>",
      "caption": "<1 sentence: what changed in this file and why>"
    },
    ...
  ]
}

## Rules

- ask: state the session's goal as a title in your own words; never \
  quote the user's prompt verbatim. At most 200 characters.
- decisions: 1-6 items, each shaped "chose X over Y because Z", naming \
  the symbols, files, or libraries verbatim. At most 200 characters each.
- dead_ends: 0-4 items, each shaped "tried X, abandoned because Y". \
  Use an empty list when nothing was abandoned; never write filler items.
- learnings: 0-5 items: constraints or gotchas discovered mid-task that \
  are not visible in the final diff (environment quirks, API surprises, \
  payload shapes, flaky-test causes). Empty list if none.
- tests: 1 sentence, or exactly "none" if no tests were added or changed.
- 3-8 chapters total. Pick natural semantic breaks (new sub-goal, wrong \
  fix discarded, course-correction, polish phase). Do NOT use every user \
  prompt as a chapter; aim coarser than that.
- chapter.title is at most 80 chars: lead with the specific action or \
  subsystem ("Fix abort-stream race"), never generic ("More fixes"). \
  chapter.caption is at most 160 chars and states the segment's outcome, \
  not just its activity.
- anchor_uuid MUST be one of the UUIDs in square brackets in the input. \
  If unsure, drop the chapter rather than guess.
- file_notes: one caption per significant changed file, PR-review voice \
  ("what changed here and why"). caption is at most 140 chars. Up to 20 \
  files; skip trivial/unchanged ones.
- file_notes[].path MUST be a file path that appears in the input (the \
  "Edit <path>" / "Write <path>" lines). If unsure, drop it rather than guess.
- Never use em-dashes ("—"). Use commas, periods, or parentheses instead.
- No URLs, no markdown formatting, no emoji.
"""
```

Run: `../../env/bin/pytest tests/agents/digest/test_prompt.py -v`
Expected: PASS.

- [ ] **Step 7: Update pipeline tests (new payload + hash + list sweep)**

In `webapp/backend/tests/agents/digest/test_pipeline.py`:

1. Replace `VALID_PAYLOAD` with the canonical new-shape payload from the top of this task, keeping the second `"bogus"` chapter entry exactly as it is today:

```python
VALID_PAYLOAD = {
    "ask": "Add a /healthcheck route",
    "decisions": [
        "Chose an inline route in app/main.py over a separate router because YAGNI",
    ],
    "dead_ends": [
        "Tried a separate APIRouter, abandoned because one route does not justify it",
    ],
    "learnings": [
        "TestClient needs raise_server_exceptions=False to assert 500 responses",
    ],
    "tests": "test_health.py adds /healthcheck assertion",
    "chapters": [
        {"anchor_uuid": "u1", "title": "Frame the change",
         "caption": "User asks for /healthcheck."},
        {"anchor_uuid": "bogus", "title": "Drop me",
         "caption": "Anchor not in trace."},
    ],
}
```

2. Append two new tests:

```python
@pytest.mark.asyncio
async def test_prompt_change_invalidates_cached_digest(
    monkeypatch, db_session, _trace_blob, _seeded_trace,
):
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")
    mock_client = MagicMock()
    mock_client.responses.parse.return_value = _ok_response(VALID_PAYLOAD)
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock_client,
    )
    await compute_digest(
        db_session, _seeded_trace, blob=_trace_blob, subagent_blobs={},
    )
    assert mock_client.responses.parse.call_count == 1

    # Same input, edited prompt: the cache must miss and re-call the LLM.
    monkeypatch.setattr(
        "app.agents.digest.pipeline.SYSTEM_PROMPT", "a different prompt",
    )
    await compute_digest(
        db_session, _seeded_trace, blob=_trace_blob, subagent_blobs={},
    )
    assert mock_client.responses.parse.call_count == 2


@pytest.mark.asyncio
async def test_em_dash_swept_from_list_items(
    monkeypatch, db_session, _trace_blob, _seeded_trace,
):
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")
    payload = dict(VALID_PAYLOAD)
    payload["decisions"] = ["chose A — over B"]
    payload["learnings"] = ["hook cwd — always ~/.cursor"]
    mock_client = MagicMock()
    mock_client.responses.parse.return_value = _ok_response(payload)
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: mock_client,
    )
    digest = await compute_digest(
        db_session, _seeded_trace, blob=_trace_blob, subagent_blobs={},
    )
    assert digest.decisions == ["chose A, over B"]
    assert digest.learnings == ["hook cwd, always ~/.cursor"]
```

Run: `../../env/bin/pytest tests/agents/digest/test_pipeline.py -v`
Expected: the two new tests FAIL (`test_em_dash_swept_from_list_items` crashes in the old sweep loop that does `strip_em_dashes(getattr(candidate, "files"))`, and the prompt-change test gets 1 call, not 2). Several existing tests also fail until Step 8.

- [ ] **Step 8: Implement pipeline changes**

In `webapp/backend/app/agents/digest/pipeline.py`:

1. Make the hash prompt-aware. Replace:

```python
    input_hash = hashlib.sha256(distilled.encode("utf-8")).hexdigest()
```

with:

```python
    # Prompt-aware: editing SYSTEM_PROMPT invalidates every cached digest,
    # which is what lets the re-digest backfill (and future prompt
    # iteration) actually re-call the LLM.
    input_hash = hashlib.sha256(
        (SYSTEM_PROMPT + "\0" + distilled).encode("utf-8")
    ).hexdigest()
```

2. Replace the string-field sweep:

```python
    for field in ("ask", "decisions", "files", "tests", "dead_ends"):
        setattr(candidate, field, strip_em_dashes(getattr(candidate, field)))
```

with:

```python
    candidate.ask = strip_em_dashes(candidate.ask)
    candidate.tests = strip_em_dashes(candidate.tests)
    for field in ("decisions", "dead_ends", "learnings"):
        setattr(candidate, field, [
            strip_em_dashes(item) for item in getattr(candidate, field)
        ])
```

3. Update the module docstring's step 2 to "Hashes the prompt + distilled string; if it matches trace.digest_input_hash, skip the LLM call entirely."

Run: `../../env/bin/pytest tests/agents/digest/ -v`
Expected: PASS (all pipeline, schema, prompt, distill tests).

- [ ] **Step 9: Update search index tests for per-item docs**

In `webapp/backend/tests/search/test_index.py`:

1. Replace `DIGEST` (note: `tests/agents/ask/test_tools.py` imports this constant, so updating it feeds the ask-tool tests too):

```python
DIGEST = {
    "ask": "Add a /healthcheck route",
    "decisions": [
        "Chose an inline route in app/main.py over a separate router because YAGNI",
    ],
    "dead_ends": [
        "Tried a separate APIRouter, abandoned because one route does not justify it",
    ],
    "learnings": [
        "TestClient needs raise_server_exceptions=False to assert 500 responses",
    ],
    "tests": "test_health.py adds /healthcheck assertion",
    "chapters": [
        {"anchor_uuid": "u1", "title": "Frame the change",
         "caption": "User asks for /healthcheck."},
        {"anchor_uuid": "u2", "title": "Implement",
         "caption": "Route added inline."},
    ],
    "file_notes": [
        {"path": "webapp/backend/app/main.py", "caption": "route added"},
    ],
}
```

2. Replace `test_explode_digest_yields_summary_chapters_files` with:

```python
def test_explode_digest_yields_item_docs_between_summary_and_chapters():
    trace = _trace(digest_json=DIGEST)
    docs = explode_digest(trace)
    types = [d.source_type for d in docs]
    assert types == [
        "summary", "decision", "dead_end", "learning",
        "chapter", "chapter", "files",
    ]
    summary = docs[0]
    # Summary now carries ask + tests only; items get their own docs so
    # ts_rank does not double-weight them.
    assert "Add a /healthcheck route" in summary.body
    assert "test_health.py adds /healthcheck assertion" in summary.body
    assert "YAGNI" not in summary.body
    assert summary.repo_full_name == "alice/x"
    assert summary.pr_number == 1
    decision = docs[1]
    assert decision.body == DIGEST["decisions"][0]
    assert decision.title == summary.title
    assert docs[2].body == DIGEST["dead_ends"][0]
    assert docs[3].body == DIGEST["learnings"][0]
    chapter = docs[4]
    assert chapter.anchor_uuid == "u1"
    assert chapter.title == "Frame the change"
    files_doc = docs[6]
    assert "webapp/backend/app/main.py" in files_doc.body


def test_explode_digest_old_string_shape_yields_no_item_docs():
    # Pre-backfill rows still hold prose strings; re-index paths (e.g. the
    # trace PATCH resync) must not iterate a string into per-char docs.
    old = {**DIGEST, "decisions": "a prose sentence",
           "dead_ends": "another", "learnings": None}
    docs = explode_digest(_trace(digest_json=old))
    assert [d.source_type for d in docs] == [
        "summary", "chapter", "chapter", "files",
    ]
```

3. Update `test_explode_digest_no_chapters_still_yields_summary`:

```python
def test_explode_digest_no_chapters_still_yields_summary():
    digest = {**DIGEST, "chapters": [], "file_notes": [],
              "decisions": [], "dead_ends": [], "learnings": []}
    docs = explode_digest(_trace(digest_json=digest))
    assert [d.source_type for d in docs] == ["summary"]
```

4. In `test_index_is_delete_then_insert`, the expected row count changes from 4 to 7 (both `assert len(...) == 4` lines become `== 7`).

Run: `../../env/bin/pytest tests/search/test_index.py -v`
Expected: new/updated tests FAIL against old `explode_digest`.

- [ ] **Step 10: Implement `explode_digest` per-item docs**

In `webapp/backend/app/search/index.py`, replace `explode_digest` with:

```python
def explode_digest(trace: Trace) -> list[SearchDocument]:
    """Digest -> unsaved rows: 1 summary + per-item decision/dead_end/
    learning docs + <=10 chapters + 1 files."""
    digest = trace.digest_json or {}
    title = (
        trace.title or trace.pr_title or digest.get("ask") or "Untitled session"
    )
    common = dict(
        repo_full_name=trace.repo_full_name,
        trace_id=trace.id,
        pr_number=trace.pr_number,
        pr_url=trace.pr_url,
        is_private=trace.is_private,
    )
    summary_body = " ".join(
        s for s in (digest.get("ask"), digest.get("tests")) if s
    )
    docs = [SearchDocument(
        source_type="summary", title=title, body=summary_body, **common,
    )]
    for source_type, key in (
        ("decision", "decisions"),
        ("dead_end", "dead_ends"),
        ("learning", "learnings"),
    ):
        items = digest.get(key)
        # Pre-backfill rows hold prose strings here; don't iterate those.
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, str) and item.strip():
                docs.append(SearchDocument(
                    source_type=source_type, title=title, body=item, **common,
                ))
    for ch in digest.get("chapters") or []:
        docs.append(SearchDocument(
            source_type="chapter",
            title=ch.get("title", ""),
            body=ch.get("caption", ""),
            anchor_uuid=ch.get("anchor_uuid"),
            **common,
        ))
    notes = digest.get("file_notes") or []
    if notes:
        body = " ".join(
            f"{n.get('path', '')}: {n.get('caption', '')}" for n in notes
        )
        docs.append(SearchDocument(
            source_type="files", title="Files touched", body=body, **common,
        ))
    return docs
```

Also update the docstring count in the module header ("up to 12 rows" is stale) and the `source_type` comment in `webapp/backend/app/storage/models.py:193` to:

```python
    # "summary" | "decision" | "dead_end" | "learning" | "chapter" | "files"
```

Run: `../../env/bin/pytest tests/search/ -v`
Expected: `test_index.py` PASSES. `test_ingest_hook.py` may still fail if it builds a `Digest` from an old-shape payload; Step 11 sweeps those.

- [ ] **Step 11: Sweep remaining old-shape LLM payloads in backend tests**

Find every remaining old-shape payload:

Run: `grep -rn '"decisions":' tests/ | grep -v '\['`

For each hit (expected: `tests/search/test_ingest_hook.py`, `tests/api/test_traces_digest.py` in `_patch_llm`, `_install_digest_mock`, and the file_notes test payload; possibly `tests/test_uploads.py`), rewrite the payload dict to the new shape. Use this exact minimal form, keeping whatever `chapters`/`file_notes` entries the payload already has:

```python
    payload = {
        "ask": "test ask",
        "decisions": ["chose test decisions over nothing because test"],
        "dead_ends": ["tried a shortcut, abandoned because it failed"],
        "learnings": ["the fixture trace has only two events"],
        "tests": "test tests",
        "chapters": [],
    }
```

Drop every `"files": ...` key. Where a test asserts on the old string (e.g. `assert body["ai_digest"]["decisions"] == "test decisions"`), assert the list instead (`== ["chose test decisions over nothing because test"]`). Where a test asserts search-doc counts or an ordered `source_type` list, update it to the new explode order: `summary`, then one doc per decision/dead_end/learning item, then chapters, then `files`.

Run: `../../env/bin/pytest tests/search/ tests/api/test_traces_digest.py -v`
Expected: `test_ingest_hook.py` PASSES. `test_traces_digest.py` still FAILS on response serialization (API `TraceDigest` still expects strings), fixed next.

- [ ] **Step 12: Write failing API boundary-validator tests**

Append to `webapp/backend/tests/api/test_traces_digest.py`:

```python
# --- ai_digest boundary validator -----------------------------------

from app.api.schemas import IngestResponse, TraceSummary  # noqa: E402

OLD_SHAPE = {
    "ask": "a", "decisions": "prose", "files": "f", "tests": "t",
    "dead_ends": "prose", "chapters": [],
}
NEW_SHAPE = {
    "ask": "a",
    "decisions": ["chose X over Y because Z"],
    "dead_ends": [],
    "learnings": ["gotcha found mid-task"],
    "tests": "none",
    "chapters": [],
    "file_notes": [],
}


def _summary(digest) -> TraceSummary:
    return TraceSummary(
        trace_id="t1", short_id="abc12345", owner_login=None,
        repo_full_name=None, pr_number=None, pr_url=None, pr_title=None,
        platform="claude-code", byte_size=1, message_count=1,
        created_at="2026-07-19T00:00:00Z", ai_digest=digest,
    )


def test_old_shape_digest_is_hidden_not_500():
    # Pre-backfill rows must serialize as "no digest", not crash the API.
    assert _summary(OLD_SHAPE).ai_digest is None


def test_new_shape_digest_survives_serialization():
    s = _summary(NEW_SHAPE)
    assert s.ai_digest is not None
    assert s.ai_digest.decisions == ["chose X over Y because Z"]
    assert s.ai_digest.learnings == ["gotcha found mid-task"]


def test_ingest_response_hides_old_shape_too():
    r = IngestResponse(
        trace_id="t1", short_id="abc12345",
        trace_url="https://x/t/abc12345", ai_digest=OLD_SHAPE,
    )
    assert r.ai_digest is None
```

Run: `../../env/bin/pytest tests/api/test_traces_digest.py -v`
Expected: FAIL (old API `TraceDigest` shape; no validator).

- [ ] **Step 13: Implement API `TraceDigest` + boundary validator**

In `webapp/backend/app/api/schemas.py`:

1. Extend the pydantic import line with `ValidationError` and `field_validator`.
2. Replace the `TraceDigest` class (keep `DigestChapter` and `FileNote` as-is) and add the shared validator function directly below it:

```python
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
    except ValidationError:
        return None
```

3. In `TraceSummary` and in `IngestResponse` (both declare `ai_digest: TraceDigest | None = None`), add:

```python
    _coerce_digest = field_validator("ai_digest", mode="before")(_digest_or_none)
```

Run: `../../env/bin/pytest tests/api/test_traces_digest.py -v`
Expected: PASS.

- [ ] **Step 14: Update ask-tool tests, then `_get_session`**

In `webapp/backend/tests/agents/ask/test_tools.py`, replace `test_get_session_returns_digest`:

```python
async def test_get_session_returns_digest(db_session):
    await _seed(db_session)
    out = await execute_tool(
        _ctx(db_session), "get_session", {"trace_short_id": "abc12345"},
    )
    assert out["ask"] == "Add a /healthcheck route"
    assert out["dead_ends"] == DIGEST["dead_ends"]
    assert out["learnings"] == DIGEST["learnings"]
    assert "files" not in out
    assert len(out["chapters"]) == 2
```

Run: `../../env/bin/pytest tests/agents/ask/test_tools.py -v` — the updated test FAILS.

Then in `webapp/backend/app/agents/ask/tools.py` `_get_session`'s return dict: delete the `"files": digest.get("files"),` line and add after `"tests"`:

```python
        "learnings": digest.get("learnings") or [],
```

Run: `../../env/bin/pytest tests/agents/ask/ -v`
Expected: PASS.

- [ ] **Step 15: Update OG card tests, then `build_card_data`**

In `webapp/backend/tests/test_og_card.py`:

1. Replace the `_digest` helper's base dict:

```python
def _digest(**over) -> dict:
    base = {
        "ask": "Stop the navbar overflowing on small screens",
        "decisions": [
            "Chose flex-wrap over fixed widths because it survives narrow viewports",
        ],
        "dead_ends": [
            "Tried overflow-x first, abandoned because it broke the sticky header",
        ],
        "learnings": [],
        "tests": "added a viewport snapshot",
        "chapters": [],
        "file_notes": [],
    }
    base.update(over)
    return base
```

2. Update the two assertions in `test_full_repo_trace_with_digest`:

```python
    assert card.decisions == (
        "Chose flex-wrap over fixed widths because it survives narrow viewports"
    )
    assert card.dead_ends == (
        "Tried overflow-x first, abandoned because it broke the sticky header"
    )
```

3. Update `test_empty_digest_strings_treated_as_missing` to the list world (empty lists read as missing) and add an old-shape test:

```python
def test_empty_digest_lists_treated_as_missing():
    trace = _make_trace(digest_json=_digest(
        ask="  ", decisions=[], dead_ends=[],
    ))
    card = build_card_data(trace)
    assert card.ask is None
    assert card.decisions is None
    assert card.dead_ends is None


def test_old_string_shape_reads_as_absent():
    # Pre-backfill rows: og reads digest_json raw, so non-list values
    # must degrade to omitted rows, not crash the card.
    trace = _make_trace(digest_json={
        "ask": "old ask", "decisions": "old prose", "dead_ends": "old prose",
    })
    card = build_card_data(trace)
    assert card.ask == "old ask"
    assert card.decisions is None
    assert card.dead_ends is None
```

(If the old `test_empty_digest_strings_treated_as_missing` passed string kwargs, delete it in favor of the two tests above. Keep its `_make_trace` usage pattern.)

Run: `../../env/bin/pytest tests/test_og_card.py -v` — FAILS.

Then in `webapp/backend/app/og/card.py`, add below `_clean`:

```python
def _first(value: object) -> str | None:
    """First item of a digest list field, or None. Non-list values (old
    string-shape digests awaiting the re-digest backfill) read as absent."""
    if isinstance(value, list) and value:
        return _clean(value[0])
    return None
```

and in `build_card_data` change:

```python
        decisions=_first(digest.get("decisions")),
        dead_ends=_first(digest.get("dead_ends")),
```

Also update the `CardData` docstring line to "`ask`/`decisions`/`dead_ends` are None when the trace has no digest or the field is empty; `decisions`/`dead_ends` carry the first digest item."

Run: `../../env/bin/pytest tests/test_og_card.py tests/test_og_render.py tests/test_og_route.py -v`
Expected: PASS. If `test_og_render.py`/`test_og_route.py` fail on digest fixtures, update their payload dicts to the same list shape as `_digest` above.

- [ ] **Step 16: Full backend suite**

Run: `../../env/bin/pytest`
Expected: PASS. Fix any straggler old-shape fixture the greps missed (the failure output names the file; apply the same list-shape rewrite).

- [ ] **Step 17: Commit**

```bash
cd /Users/bhavya/git/vibeshub && [ "$(git branch --show-current)" = "repo-search" ] && \
git add webapp/backend && \
git commit -m "feat: item-granularity digest fields with per-item search docs

decisions/dead_ends become lists, new learnings field, files dropped.
Prompt rewritten for concrete identifiers; cache hash is prompt-aware.
Old-shape digests are hidden at the API boundary until the backfill.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Frontend types + DigestPanel bullet groups

**Files:**
- Modify: `webapp/frontend/src/types.ts:20-28`
- Modify: `webapp/frontend/src/components/trace/DigestPanel.tsx`
- Modify: `webapp/frontend/src/components/trace/DigestPanel.module.css`
- Test: `webapp/frontend/src/tests/trace/DigestPanel.test.tsx`
- Modify (fixture shape only): `webapp/frontend/src/tests/trace/ChapterRail.test.tsx:32`, `webapp/frontend/src/tests/trace/Thread.test.tsx:30,67`, `webapp/frontend/src/tests/trace/HeroTitle.test.tsx:150-153`, `webapp/frontend/src/tests/trace/ProvenanceView.test.tsx:103,263`, `webapp/frontend/src/tests/routes/TraceView.test.tsx:568,632,670,693`

**Interfaces:**
- Consumes: API `TraceDigest` JSON from Task 1 (`decisions`/`dead_ends`/`learnings` always arrays, `files` gone, old-shape traces arrive as `ai_digest: null`).
- Produces: `TraceDigest` TS interface `{ ask: string; decisions: string[]; dead_ends: string[]; learnings: string[]; tests: string; chapters: DigestChapter[]; file_notes?: FileNote[] }`.

- [ ] **Step 1: Update the `TraceDigest` type**

In `webapp/frontend/src/types.ts` replace the `TraceDigest` interface:

```ts
export interface TraceDigest {
  ask: string;
  decisions: string[];
  dead_ends: string[];
  learnings: string[];
  tests: string;
  chapters: DigestChapter[];
  file_notes?: FileNote[];
}
```

- [ ] **Step 2: Rewrite DigestPanel tests for bullet groups**

Replace `webapp/frontend/src/tests/trace/DigestPanel.test.tsx` with:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { DigestPanel } from "../../components/trace/DigestPanel";
import type { TraceDigest } from "../../types";

const sampleDigest: TraceDigest = {
  ask: "Add /healthcheck",
  decisions: [
    "Chose an inline route in main.py over a new router because YAGNI",
    "Chose starlette TestClient over httpx.AsyncClient because sync tests",
  ],
  dead_ends: ["Tried overflow-x first, abandoned because it broke the header"],
  learnings: ["TestClient needs raise_server_exceptions=False"],
  tests: "test_health.py",
  chapters: [
    { anchor_uuid: "u1", title: "Frame", caption: "User asks." },
    { anchor_uuid: "u2", title: "Land", caption: "Patch shipped." },
  ],
};

describe("DigestPanel", () => {
  it("renders the ask row and all three item groups", () => {
    render(<DigestPanel digest={sampleDigest} />);
    expect(screen.getByText(/^Ask$/i)).toBeInTheDocument();
    expect(screen.getByText("Add /healthcheck")).toBeInTheDocument();
    expect(screen.getByText(/Key decisions/i)).toBeInTheDocument();
    expect(screen.getByText(/Dead ends/i)).toBeInTheDocument();
    expect(screen.getByText(/Learnings/i)).toBeInTheDocument();
    expect(
      screen.getByText("TestClient needs raise_server_exceptions=False"),
    ).toBeInTheDocument();
  });

  it("renders multi-item groups as bullets and never shows tests", () => {
    render(<DigestPanel digest={sampleDigest} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByText("test_health.py")).not.toBeInTheDocument();
  });

  it("omits empty groups", () => {
    render(
      <DigestPanel digest={{ ...sampleDigest, dead_ends: [], learnings: [] }} />,
    );
    expect(screen.queryByText(/dead ends/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/learnings/i)).not.toBeInTheDocument();
    expect(screen.getByText("Add /healthcheck")).toBeInTheDocument();
  });

  it("does not render chapter jump chips (owned by the rail)", () => {
    render(<DigestPanel digest={sampleDigest} />);
    expect(screen.queryByText("Frame")).not.toBeInTheDocument();
    expect(screen.queryByText("Land")).not.toBeInTheDocument();
  });

  it("renders nothing when the ask is blank and every list is empty", () => {
    const { container } = render(
      <DigestPanel
        digest={{
          ...sampleDigest,
          ask: " ",
          decisions: [],
          dead_ends: [],
          learnings: [],
        }}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});
```

Run: `cd webapp/frontend && npm test -- DigestPanel`
Expected: FAIL.

- [ ] **Step 3: Rewrite `DigestPanel.tsx`**

Replace the component body (imports and `Props` stay):

```tsx
import type { TraceDigest } from "../../types";
import styles from "./DigestPanel.module.css";

interface Props {
  digest: TraceDigest;
}

const GROUPS: Array<{
  key: "decisions" | "dead_ends" | "learnings";
  label: string;
}> = [
  { key: "decisions", label: "Key decisions" },
  { key: "dead_ends", label: "Dead ends" },
  { key: "learnings", label: "Learnings" },
];

export function DigestPanel({ digest }: Props) {
  const ask = (digest.ask ?? "").trim();
  const groups = GROUPS.map(({ key, label }) => ({
    key,
    label,
    items: (digest[key] ?? []).filter((s) => s.trim() !== ""),
  })).filter((g) => g.items.length > 0);
  if (!ask && groups.length === 0) return null;
  return (
    <div className={styles.wrap}>
      <section className={styles.panel}>
        <div className={styles.eyebrow}>
          <span className={styles.badge}>ai digest</span>
          <span className={styles.note}>generated on upload</span>
        </div>
        <div className={styles.bullets}>
          {ask && (
            <div className={styles.row}>
              <div className={styles.label}>Ask</div>
              <div className={styles.value}>{ask}</div>
            </div>
          )}
          {groups.map(({ key, label, items }) => (
            <div className={styles.row} key={key}>
              <div className={styles.label}>{label}</div>
              {items.length === 1 ? (
                <div className={styles.value}>{items[0]}</div>
              ) : (
                <ul className={styles.itemList}>
                  {items.map((item, i) => (
                    <li key={i}>{item}</li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
```

(Single-item groups render as a plain value row, keeping the panel as quiet as today; only genuinely multi-item groups become bullet lists.)

- [ ] **Step 4: Add the list style**

Append to `webapp/frontend/src/components/trace/DigestPanel.module.css`:

```css
.itemList {
  margin: 0;
  padding-left: 18px;
  color: var(--txt);
  line-height: 1.55;
}
.itemList li {
  overflow-wrap: anywhere;
  padding: 2px 0;
}
.itemList li::marker {
  color: var(--faint);
}
```

Run: `npm test -- DigestPanel`
Expected: PASS.

- [ ] **Step 5: Sweep remaining frontend digest fixtures**

Update every test fixture constructing a `TraceDigest` (vitest does not type-check, so these only surface via `tsc`): at each location below, change `decisions: "d"`-style strings to arrays, delete `files`, and add `learnings: []`. The canonical minimal fixture is:

```ts
  { ask: "a", decisions: ["d"], dead_ends: ["e"], learnings: [], tests: "t", chapters }
```

Locations: `src/tests/trace/ChapterRail.test.tsx:32`, `src/tests/trace/Thread.test.tsx:30` and `:67`, `src/tests/trace/HeroTitle.test.tsx:150-153` (its `decisions: ""`/`files: ""` become `decisions: []`, drop `files`, add `learnings: []`), `src/tests/trace/ProvenanceView.test.tsx:103` and `:263`, `src/tests/routes/TraceView.test.tsx:568`, `:632`, `:670`, `:693`.

- [ ] **Step 6: Full frontend verification**

Run: `npm test` — Expected: PASS.
Run: `npm run build` — Expected: PASS (`tsc -b` catches any fixture the sweep missed; fix with the same pattern).

- [ ] **Step 7: Commit**

```bash
cd /Users/bhavya/git/vibeshub && [ "$(git branch --show-current)" = "repo-search" ] && \
git add webapp/frontend && \
git commit -m "feat: DigestPanel bullet groups for decisions, dead ends, learnings

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Re-digest backfill script

**Files:**
- Create: `webapp/backend/scripts/backfill_redigest.py`
- Create: `webapp/backend/tests/scripts/__init__.py` (empty)
- Create: `webapp/backend/tests/scripts/test_backfill_redigest.py`

**Interfaces:**
- Consumes: `compute_digest(session, trace, *, blob, subagent_blobs)` (prompt-aware hash from Task 1 makes old digests hash-miss), `index_trace_documents(session, trace)`, `BlobStore.get(key)` raising `FileNotFoundError` on missing keys, blob layout `traces/<sid>/main.jsonl`, `traces/<sid>/converted.jsonl`, `traces/<sid>/agents/<agent_id>.jsonl` / `.converted.jsonl`, `Trace.agents` rows shaped `{"agent_id", "tool_use_id", ...}`.
- Produces: `redigest_all(session, store) -> dict[str, int]` with keys `redigested | no_digest | no_blob | v1_skipped`, and a `python -m scripts.backfill_redigest` entry point.

- [ ] **Step 1: Write the failing script test**

Create `webapp/backend/tests/scripts/__init__.py` (empty) and `webapp/backend/tests/scripts/test_backfill_redigest.py`:

```python
from unittest.mock import MagicMock

import pytest

from app.agents.digest.schema import Digest
from app.storage.blob import LocalDirBlobStore
from app.storage.models import SearchDocument, Trace
from scripts.backfill_redigest import redigest_all
from sqlalchemy import select

SAMPLE_JSONL = (
    b'{"type":"user","uuid":"u1","message":{"content":"Test"}}\n'
    b'{"type":"assistant","uuid":"a1","message":'
    b'{"content":[{"type":"text","text":"Done."}]}}\n'
)

NEW_PAYLOAD = {
    "ask": "test ask",
    "decisions": ["chose test decisions over nothing because test"],
    "dead_ends": [],
    "learnings": ["the fixture trace has only two events"],
    "tests": "none",
    "chapters": [],
}

OLD_DIGEST = {
    "ask": "old ask", "decisions": "old prose", "files": "f",
    "tests": "t", "dead_ends": "old prose", "chapters": [],
}


def _mock_client():
    resp = MagicMock()
    resp.output_parsed = Digest.model_validate(NEW_PAYLOAD)
    resp.usage = MagicMock(input_tokens=5, output_tokens=3)
    client = MagicMock()
    client.responses.parse.return_value = resp
    return client


def _trace(short_id, **kw):
    defaults = dict(
        short_id=short_id, owner_login="alice", repo_full_name="alice/x",
        pr_number=1, pr_url=None, pr_title=None,
        platform="claude-code", session_id=f"s-{short_id}",
        byte_size=100, message_count=2,
        redaction_count_client=0, redaction_count_server=0,
        is_private=False, blob_path=None,
        blob_prefix=f"traces/{short_id}/",
        agents=[], agent_count=0,
        digest_json=dict(OLD_DIGEST), digest_input_hash="old-hash",
    )
    defaults.update(kw)
    return Trace(**defaults)


@pytest.fixture
def _store(tmp_path):
    store = LocalDirBlobStore(tmp_path)
    (tmp_path / "traces" / "abc12345").mkdir(parents=True)
    (tmp_path / "traces" / "abc12345" / "main.jsonl").write_bytes(SAMPLE_JSONL)
    return store


@pytest.fixture
def _env(monkeypatch):
    monkeypatch.setenv("VIBESHUB_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("VIBESHUB_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("VIBESHUB_OPENAI_MODEL", "gpt-5.5")


@pytest.mark.asyncio
async def test_redigests_old_traces_and_reindexes(
    db_session, _store, _env, monkeypatch,
):
    client = _mock_client()
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: client,
    )
    trace = _trace("abc12345")
    db_session.add(trace)
    await db_session.flush()

    counts = await redigest_all(db_session, _store)

    assert counts["redigested"] == 1
    assert client.responses.parse.call_count == 1
    assert trace.digest_json["decisions"] == NEW_PAYLOAD["decisions"]
    types = {d.source_type for d in (await db_session.execute(
        select(SearchDocument),
    )).scalars().all()}
    assert "decision" in types and "learning" in types


@pytest.mark.asyncio
async def test_second_run_skips_llm_call(
    db_session, _store, _env, monkeypatch,
):
    client = _mock_client()
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: client,
    )
    db_session.add(_trace("abc12345"))
    await db_session.flush()

    await redigest_all(db_session, _store)
    counts = await redigest_all(db_session, _store)

    # Resumable: the prompt-aware hash now matches, so no second LLM call.
    assert client.responses.parse.call_count == 1
    assert counts["redigested"] == 1


@pytest.mark.asyncio
async def test_v1_and_blobless_traces_are_counted_not_crashed(
    db_session, _store, _env, monkeypatch,
):
    monkeypatch.setattr(
        "app.agents.digest.pipeline.get_client", lambda: _mock_client(),
    )
    db_session.add(_trace("v1trace12", blob_prefix=None, blob_path="old.jsonl"))
    db_session.add(_trace("noblraw12"))  # prefix set but nothing in the store
    await db_session.flush()

    counts = await redigest_all(db_session, _store)

    assert counts["v1_skipped"] == 1
    assert counts["no_blob"] == 1
    assert counts["redigested"] == 0
```

Run: `../../env/bin/pytest tests/scripts/ -v`
Expected: FAIL with `ModuleNotFoundError: scripts.backfill_redigest`.

- [ ] **Step 2: Implement the script**

Create `webapp/backend/scripts/backfill_redigest.py`:

```python
"""One-shot backfill: re-digest every trace under the current prompt and
re-index its search documents.

The digest cache hash includes SYSTEM_PROMPT, so traces digested under an
older prompt hash-miss and get a real LLM call; already-migrated traces
skip with SKIP_UNCHANGED, which makes the script resumable (safe to stop
and re-run). A failed LLM call keeps the old digest_json; the API hides
old-shape digests until a later run succeeds.

Run from webapp/backend:  ../../env/bin/python -m scripts.backfill_redigest
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.digest import compute_digest
from app.search.index import index_trace_documents
from app.settings import get_settings
from app.storage.blob import (
    BlobStore,
    LocalDirBlobStore,
    make_azure_blob_store,
)
from app.storage.db import engine_for
from app.storage.models import Trace


async def _first_present(store: BlobStore, *keys: str) -> bytes | None:
    for key in keys:
        try:
            return await store.get(key)
        except FileNotFoundError:
            continue
    return None


async def _subagent_blobs(store: BlobStore, trace: Trace) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for a in trace.agents or []:
        agent_id = a.get("agent_id")
        if not agent_id:
            continue
        blob = await _first_present(
            store,
            f"{trace.blob_prefix}agents/{agent_id}.converted.jsonl",
            f"{trace.blob_prefix}agents/{agent_id}.jsonl",
        )
        if blob is not None:
            out[a.get("tool_use_id") or agent_id] = blob
    return out


async def redigest_all(
    session: AsyncSession, store: BlobStore,
) -> dict[str, int]:
    counts = {"redigested": 0, "no_digest": 0, "no_blob": 0, "v1_skipped": 0}
    trace_ids = (await session.execute(
        select(Trace.id).where(Trace.deleted_at.is_(None))
        .order_by(Trace.created_at)
    )).scalars().all()
    for trace_id in trace_ids:
        trace = await session.get(Trace, trace_id)
        if trace is None:
            continue
        if trace.blob_prefix is None:
            counts["v1_skipped"] += 1
            continue
        blob = await _first_present(
            store,
            f"{trace.blob_prefix}converted.jsonl",
            f"{trace.blob_prefix}main.jsonl",
        )
        if blob is None:
            counts["no_blob"] += 1
            continue
        digest = await compute_digest(
            session, trace, blob=blob,
            subagent_blobs=await _subagent_blobs(store, trace),
        )
        counts["redigested" if digest is not None else "no_digest"] += 1
        await index_trace_documents(session, trace)
        # Commit per trace so a stopped run keeps its progress.
        await session.commit()
        print(f"{trace.short_id}: {'ok' if digest is not None else 'no digest'}")
    return counts


async def main() -> None:
    settings = get_settings()
    engine = engine_for(settings.database_url)
    store: BlobStore
    if settings.azure_blob_container:
        store = make_azure_blob_store(settings)
    else:
        store = LocalDirBlobStore(settings.blob_dir)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        counts = await redigest_all(session, store)
    print(" ".join(f"{k}={v}" for k, v in counts.items()))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Run the script tests**

Run: `../../env/bin/pytest tests/scripts/ -v`
Expected: PASS.

- [ ] **Step 4: Full backend suite**

Run: `../../env/bin/pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/bhavya/git/vibeshub && [ "$(git branch --show-current)" = "repo-search" ] && \
git add webapp/backend/scripts/backfill_redigest.py webapp/backend/tests/scripts && \
git commit -m "feat: re-digest backfill script for the restructured digest

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Final verification and rollout notes

**Files:** none created; verification only.

- [ ] **Step 1: Full backend suite** — from `webapp/backend`: `../../env/bin/pytest` → PASS.
- [ ] **Step 2: Full frontend suite + type-check** — from `webapp/frontend`: `npm test` → PASS; `npm run build` → PASS.
- [ ] **Step 3: Do NOT gate on Playwright e2e** — it fails on main pre-existing; only investigate an e2e failure if it is new relative to the branch base.
- [ ] **Step 4: Report rollout steps to the user** (do not execute them): deploy the backend, then run `../../env/bin/python -m scripts.backfill_redigest` from `webapp/backend` against production; eyeball the first few re-digested traces in the UI before letting it run out (it is resumable, so Ctrl-C is safe). Until it completes, old traces show no digest panel by design.
