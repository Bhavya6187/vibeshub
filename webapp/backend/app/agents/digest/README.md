# Trace digest agent

Generates a 5-line digest, 3-8 semantic chapter anchors, and up to 20
per-file captions (`file_notes`) for an uploaded trace (Claude Code, Codex,
or Cursor — always Claude-shaped after conversion). The bullets and chapters
surface in the trace viewer's Hero panel and in the PR comment body posted by
the plugin; the file captions surface in the Provenance (Changes) view.

## Flow

1. Backend calls `compute_digest(session, trace, blob, subagent_blobs)`
   from `app/api/trace_service.py::create_or_update_trace`, after the
   blob is written, before the transaction is committed.
2. Codex rollouts and Cursor transcripts are converted to Claude-shaped
   records at ingest by `app/codex_convert.py` / `app/cursor_convert.py`
   (`app/api/trace_service.py` stores the result as
   `{blob_prefix}converted.jsonl` and passes it here), so the distiller
   always sees Claude-shaped records. The synthetic uuids
   (`codex-rec-<n>`, `cursor-rec-<n>`) are the digest's chapter anchor
   surface, and the viewer resolves anchors against the same converted
   blob served by `GET /api/traces/{id}/session`; converter determinism
   is pinned by `tests/test_codex_convert.py` and
   `tests/test_cursor_convert.py`. Subagent blobs are converted the same
   way.
3. `distill_with_uuids` (in `distill.py`) walks the JSONL once and
   classifies every event into a tier (see spec §5). Before tiering, it
   strips the harness's own injected noise, which was previously billed
   and read as user content: `isMeta` user records (replayed Skill
   bodies) are dropped, `<system-reminder>` spans are removed,
   task-notification / slash-command / local-command-output wrappers are
   compacted to one-liners, and user text is capped at 2k chars with
   head+tail retention. Output is a single string with each retained
   event prefixed by `[uuid]`.

   Any change to the distiller changes `sha256(distilled)`, which
   invalidates every persisted `digest_input_hash`: existing traces
   re-digest on their next upload, and a backfill is needed to refresh
   traces that are never re-uploaded.
4. `pipeline.compute_digest` computes `sha256(distilled)` and compares
   to `trace.digest_input_hash`. Match → reuse persisted digest,
   `outcome=skip_unchanged`, no LLM call. Otherwise the config check
   runs next (`outcome=skip_no_config` when any of the three env vars
   is unset), and only then the empty-distillate check
   (`outcome=skip_empty`); `skip_empty` therefore never appears on an
   instance without OpenAI config.
5. Otherwise: calls OpenAI `responses.parse` with `text_format=Digest`
   (Structured Outputs, so the schema is enforced server-side) and
   `reasoning.effort=low`.
6. Reads the already-validated `Digest` from `response.output_parsed`
   (None → `outcome=fail_schema`). Drops chapters whose `anchor_uuid`
   isn't in the distilled UUID surface, and drops file_notes whose
   `path` isn't in `distill.edited_paths()` (the set of paths touched
   by Write/Edit/MultiEdit across the main and subagent streams); both
   kept/total counts land in `extra`. Strips em-dashes from every
   string field.
7. Persists `digest_json` and `digest_input_hash` on the Trace row.
8. Records the run in `agent_run` via `record_run`.

## Env vars

- `VIBESHUB_OPENAI_API_KEY`
- `VIBESHUB_OPENAI_ENDPOINT`
- `VIBESHUB_OPENAI_MODEL`

All three must be set. Missing any → `outcome=skip_no_config`, upload
still succeeds, viewer hides the DigestPanel.

## Known degradation modes

- **Trace exceeds 200k-token hard cap after the adaptive pass** — the
  distiller head/tail-truncates with a `[… elided N events …]` marker.
  `extra.distill_truncated=true` on the agent_run row. Digest may miss
  middle-of-trace decisions.
- **All chapter anchors invalid** — digest persists with `chapters=[]`,
  `outcome=ok`, `extra.chapters_kept=0`. The DigestPanel still renders
  the 5 bullets; just no "Jump to" rail.
- **All file_notes paths invalid** — digest persists with
  `file_notes=[]`, `outcome=ok`, `extra.file_notes_kept=0`. The
  Provenance view falls back to uncaptioned file diffs. Most common
  when the model invents a path, or when the trace's edits used a tool
  outside `_EDIT_TOOLS` (Write / Edit / MultiEdit).
- **LLM call fails / output is malformed** — `outcome=fail_call` /
  `fail_schema`. The viewer shows the existing Outcome card without a
  DigestPanel; the PR comment falls back to the one-line trace link.

## Operations

Daily cost rollup:
```sql
SELECT date_trunc('day', created_at) AS day,
       sum(input_tokens) AS in_tok,
       sum(output_tokens) AS out_tok
FROM agent_run
WHERE agent_name = 'digest'
GROUP BY 1 ORDER BY 1 DESC;
```

Failure-mode snapshot (last 7 days):
```sql
SELECT outcome, count(*) FROM agent_run
WHERE agent_name = 'digest' AND created_at > now() - interval '7 days'
GROUP BY 1 ORDER BY 2 DESC;
```

Per-trace history (debug a specific upload):
```sql
SELECT created_at, outcome, input_tokens, output_tokens, extra
FROM agent_run
WHERE trace_id = '<short_id>' ORDER BY created_at;
```

## Adding a new agent

1. Create `webapp/backend/app/agents/<name>/` with the same file layout
   (`__init__.py`, `schema.py`, `pipeline.py`, `prompt.py`, README,
   plus a pure input-shaping module like digest's `distill.py` if the
   agent needs one).
2. Reuse `app.agents._client.get_client/get_model` and
   `app.agents._usage.record_run`. The `Outcome` enum is shared.
3. Add keys to the `agent_run.extra` JSON payload for any per-agent
   metadata; no schema change required.
