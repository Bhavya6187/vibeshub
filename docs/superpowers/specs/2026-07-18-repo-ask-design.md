# Repo ask: agent-based Q&A over session history and GitHub

Date: 2026-07-18
Status: approved
Supersedes: 2026-07-11-repo-search-design.md (embeddings retrieval design)

## Problem

Teams accumulate agent sessions attached to PRs, but there is no way to ask
"why is this code like this?" after the fact. vibeshub has the raw material
(digests with ask/decisions/dead_ends/chapters, linked to PRs) and no way to
query it.

The superseded spec answered this with hybrid lexical + semantic retrieval
(Postgres FTS + pgvector) returning ranked snippets, with RAG answer
synthesis deferred to phase 2. This spec replaces that: a ranked snippet
list does not actually answer "why" questions, and the retrieval layer
(embeddings, pgvector, RRF, re-embed bookkeeping, PR pre-indexing) is
infrastructure built to approximate what an agent with simple tools does
directly. We skip the retrieval layer and ship the answer experience as v1.

Positioning holds from the old spec: the moat is reasoning that was never
committed anywhere (dead ends, rejected alternatives, constraints discovered
mid-session). Cold start (repos with no traces yet) is deliberately out of
scope here and will be resolved in a separate spec; v1 targets repos that
already have traces.

## Decisions

- Consumer: humans on vibeshub.ai. An MCP/agent surface is a possible later
  layer, not in scope.
- Scope: repo-scoped. The ask box lives on the repo page and answers about
  one repo. Global and profile scopes are out.
- Shape: agent-only. One question in, a bounded single-turn tool loop, one
  streamed cited answer out. No keyword-search UI, no conversation history,
  no follow-ups in v1.
- Corpus: exploded digest documents (FTS only, no embeddings) plus the live
  GitHub API. Merged-PR pre-indexing ("Index PR history", the
  `repo_search_index` table) is dropped entirely; the agent queries GitHub
  at ask time.
- Access: public repos are open to everyone including anonymous visitors,
  behind tight rate limits. Private repos require the existing
  repo-read-access dependency.
- Failure honesty: a GitHub tool failure mid-ask is surfaced to the user
  with a sign-in prompt, not silently degraded to sessions-only.
- Cold start: out of scope, resolved separately. v1 only serves repos that
  already have traces; the ask box does not render on a repo with zero
  traces.

## Data model

New table `search_documents`; one row is one searchable digest snippet.
This is the superseded spec's table minus everything embedding-related: no
vector column, no `embedding_model`, no `content_hash`, no pgvector
extension, and no `pr` source type (PR data is live, not indexed).

| column | type | notes |
|---|---|---|
| id | UUID pk | |
| repo_full_name | String(255), indexed, NOT NULL | standalone traces (no repo) are excluded |
| trace_id | UUID FK traces.id, NOT NULL, ON DELETE CASCADE | |
| source_type | String(16) | `summary` \| `chapter` \| `files` |
| title | Text | chapter title or trace title |
| body | Text | the searchable text |
| anchor_uuid | String(64), nullable | chapter deep link into the trace viewer |
| pr_number | Integer, nullable | for linking |
| pr_url | String(512), nullable | |
| is_private | Boolean, NOT NULL | copied from trace / repo visibility at index time |
| created_at, updated_at | DateTime(tz) | |

Postgres-only, in the alembic migration behind a dialect guard: a generated
tsvector column `search_tsv` over `title || ' ' || body`, GIN indexed.
SQLite dev fallback is LIKE matching in the search tool; only the retrieval
primitive differs between environments.

## Trace ingest

At the end of the digest pipeline, after `digest_json` is persisted, explode
the digest into up to 12 docs (1 summary + up to 10 chapters + 1 files doc;
a digest with no chapters or file notes still yields the summary doc):

- one `summary` doc: body = ask + decisions + dead_ends + tests
- one `chapter` doc per chapter: title + caption, with `anchor_uuid`
- one `files` doc: all file_note paths + captions concatenated (this is
  what makes path queries like `auth/sessions.py` hit)

Indexing is delete-then-insert keyed on `trace_id`, so digest regeneration
and PR-update re-uploads stay consistent and refresh `is_private`. With no
embeddings there is no carry-over logic; re-indexing is free.

Failure handling mirrors the digest agent: failures are recorded in
`agent_run` (`agent_name="search_index"`) and the upload still succeeds.
Indexing is idempotent, so failures are backfillable by re-running it.

A one-shot script (`scripts/backfill_search_documents.py` pattern) indexes
existing traces that already have digests.

Consistency cascades: trace soft-delete also deletes its search documents.
The FK cascade covers hard deletes.

## Ask agent

New package `app/agents/ask/` mirroring the digest agent layout
(`pipeline.py`, `prompt.py`, `schema.py`), reusing `_client.py` and the
`VIBESHUB_OPENAI_*` configuration. A plain tool-calling loop:

- max 8 tool calls, then the model must answer with what it has
- 60s wall-clock budget; on expiry the model is forced to a final answer
  flagged as best-effort
- single turn: no conversation state is stored

Every run is logged to `agent_run` (`agent_name="repo_ask"`) with token
usage, matching the digest pattern.

### Tools

Seven thin functions, all repo-scoped. The first three read vibeshub's own
corpus; the rest call the GitHub API live.

| tool | backing | returns |
|---|---|---|
| `search_sessions(query)` | FTS over `search_documents` (LIKE on SQLite), `websearch_to_tsquery` ranked by `ts_rank`, top 10 | type, title, snippet, trace short id, anchor_uuid, pr_number, date |
| `get_session(trace_short_id)` | traces table | full digest: ask, decisions, dead ends, chapters, file notes, tests, PR link |
| `list_sessions()` | traces table, newest first, top 20 | title, short id, PR number, date |
| `search_prs(query)` | GitHub search API (`type:pr repo:X`) | number, title, state, merged date |
| `get_pr(number)` | GitHub PR API | title, body (truncated 4,000 chars), merge info, changed-file paths |
| `list_commits(path?)` / `get_file(path)` | GitHub commits / contents API | recent commits optionally filtered by path; file content (first 400 lines) at default branch |

`search_sessions` filters `is_private` docs with the same repo-read-access
rule as the private-trace endpoints, so an anonymous ask on a public repo
never surfaces a private trace.

### GitHub auth for tools

- Signed-in viewer: their stored OAuth token (own rate-limit bucket, and
  grants whatever private access they have).
- Anonymous viewer on a public repo: the existing server-level token
  setting (`VIBESHUB_GITHUB_FALLBACK_TOKEN`, already consumed by
  `PublicGitHubClient`), because unauthenticated API calls would share
  one 60/hr bucket on the server's IP.
- Anonymous with no server token configured (dev edge): GitHub tools are
  omitted from the tool list up front; the run proceeds sessions-only and
  the UI shows a non-blocking notice: "Sign in with GitHub to include PRs
  and code in answers."

### GitHub failure mid-ask

If a GitHub tool call fails during a run (rate limit, revoked token, API
error), the run aborts and the stream emits a user-visible `error` event.
Anonymous viewers get a sign-in prompt ("Sign in with GitHub to ask about
PRs and code"); signed-in viewers get a GitHub-unreachable message with a
reconnect hint. No silent degradation to sessions-only: a partial answer
that pretends to be complete is worse than an honest failure, and the
prompt doubles as the sign-up nudge.

## API

`POST /api/repos/{owner}/{repo}/ask`, body `{"question": "..."}` (capped at
500 chars), streaming SSE response:

- `status`: short activity text ("searching sessions", "reading PR #142"),
  one per tool call
- `delta`: answer markdown chunks
- `citations`: final list of `{type, title, trace_short_id?, anchor_uuid?,
  pr_number?, url?}`
- `error`: `{code, message}`; codes are `github_auth_required`,
  `github_unavailable`, `llm_unavailable`
- `done`

Access: public repos allow anonymous; private repos use the existing
repo-read-access dependency. Rate limits are in-memory: 5 asks/hour per IP
for anonymous, 20 asks/hour per user for signed-in. Rate-limit rejection is
a plain HTTP 429 before the stream opens, not an SSE event.

## Frontend

One input on the repo page above the existing tabs, styled to match the
page (subtle, no restructure), placeholder "Ask about this repo". Submitting
with Enter swaps the tab content area for the answer panel; Esc or clearing
restores the tabs.

Answer panel states:

- streaming: a small activity line from `status` events, then markdown
  accumulating from `delta` events
- done: rendered answer with citation links; chapter citations deep-link
  into the trace viewer at the anchor, PR and commit citations link to
  GitHub
- error: the `error` message; `github_auth_required` renders a sign-in
  button

The ask box renders only when the repo has at least one trace; the
zero-trace experience belongs to the separate cold-start spec. No em-dashes
in any user-facing copy.

## Error handling

- LLM unavailable at ask time: `error` event with `llm_unavailable`,
  recorded in `agent_run`, no retry loop.
- GitHub tool failure mid-ask: abort with `github_auth_required` /
  `github_unavailable` as above.
- Step or time budget exhausted: forced final answer, flagged best-effort
  in the UI.
- Indexing failure at upload: upload unaffected, recorded in `agent_run`.

## Testing

- Backend pytest (`env/bin/pytest`), OpenAI mocked per the digest test
  pattern: digest-to-docs explosion, FTS search and the SQLite LIKE
  fallback, private-doc gating, the tool loop driven by scripted fake
  tool-call sequences (including the abort-on-GitHub-failure path and the
  step cap), SSE event framing, rate limits, and token selection (viewer vs
  server vs none).
- Frontend vitest: ask box behavior (including not rendering on a
  zero-trace repo), streaming render, error and sign-in states.
- Playwright e2e: excluded (broken on main pre-existing).

## Build order

One shippable milestone: `search_documents` table + ingest + backfill
script, the seven tools, the agent loop, the SSE endpoint, and the repo
page UI. Cold start (local history import or any other corpus acquisition)
gets its own spec and plan later; nothing here blocks on it.

## Out of scope / phase 2

- Embeddings, pgvector, or any vector retrieval (revisit only if FTS
  recall provably limits answer quality at real corpus sizes).
- Cold start: local session history import, PR backfill, and the
  zero-trace repo experience (separate spec).
- A keyword-search results UI.
- Conversation history / follow-up questions on an answer.
- MCP/agent-facing surface.
- Global and profile-scoped asks.
- Transcript message-level indexing.
- PR review threads and commit-message search.
