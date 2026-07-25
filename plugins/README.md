# Platform plugins

Claude Code, Codex, and Cursor all share one upload pipeline. The source of
truth is [cli/](cli/):

- **Claude Code + Codex** install from this repo's marketplace package
  (`plugins/cli`). Runtime detection picks the right transcript reader.
- **Cursor** is a separate marketplace package generated from `plugins/cli` by
  [`scripts/sync-cursor-plugin.py`](../scripts/sync-cursor-plugin.py) and
  published at [vibeshub/vibeshub-cursor](https://github.com/vibeshub/vibeshub-cursor).
  Do not hand-edit the generated tree; change `plugins/cli` and re-run the sync
  script.

`vibeshub_client/` lives bundled inside `plugins/cli/` (and is copied into the
Cursor package) so each marketplace install is self-contained. Install and
config details: [cli/README.md](cli/README.md).

## How platforms are selected

[`cli/platform_adapter.py`](cli/platform_adapter.py) chooses a
`TranscriptReader` from `VIBESHUB_PLATFORM` (Cursor hooks set this explicitly),
transcript path (`~/.claude`, `~/.codex/sessions`, `~/.cursor/projects`), or
Codex/Claude env signals. Each reader returns a stable `platform_id` that
becomes the `platform` field on uploaded traces.

Triggers (`gh pr create`, `gh pr edit`, `git push`) are classified in
[`cli/vibeshub_client/share_trigger.py`](cli/vibeshub_client/share_trigger.py);
all platforms call the shared `run_share_pipeline()`.

## Ingest headers

Every platform posts the trace bundle as raw tar bytes to `POST /api/ingest`,
authenticated with `Authorization: Bearer <gh auth token>`. All metadata travels
in headers:

| Header | Required | Value |
|---|---|---|
| `X-Vibeshub-Platform` | yes | the adapter's `platform_id` (`claude-code`, `codex`, `cursor`, ...) |
| `X-Vibeshub-Plugin-Version` | yes | `vibeshub_client.version.PLUGIN_VERSION` |
| `X-Vibeshub-Client-Redactions` | no | count of client-side redactions; defaults to `0`, a non-integer is a 400 |
| `X-Vibeshub-Pr-Url` | no | PR the trace attaches to |
| `X-Vibeshub-Repo` | no | `owner/repo` when the trace attaches to a repo with no PR |
| `X-Vibeshub-Session-Id` | no | session id; a re-upload with the same value and the same association refreshes that trace instead of creating a new one |
| `X-Vibeshub-Git-Branch` | no | current branch at share time (truncated to 255 chars) |
| `X-Vibeshub-Git-Commit` | no | 40-hex `HEAD` sha at share time |

Git metadata is best effort: `vibeshub_client/git_info.py` returns `None` for
both values outside a git repo, and the server drops a commit that is not a full
lowercase-hex sha instead of failing the upload. The stored branch and commit are
what `import-trace` reports on, so they are provenance, never a requirement.

## Importing traces

`import-trace <trace-url-or-short-id> --to codex|claude [--checkout]`
(`/import-trace` in Claude Code, or run
[`cli/commands/import-trace.py`](cli/commands/import-trace.py) directly)
downloads a trace as a resumable session for the other CLI.

It calls `GET /api/traces/{short_id}/export/{target}`, which serves a trace that
is already native to the target verbatim and converts everything else on the
fly. The client reads `X-Vibeshub-Filename` and `X-Vibeshub-Session-Uuid` to name
and place the file (`~/.codex/sessions/<yyyy>/<mm>/<dd>/` for Codex, or
`~/.claude/projects/<encoded-cwd>/` for Claude Code), then prints the exact
resume command. Importing the same trace twice re-ids the copy rather than
overwriting the first one. Cursor traces have no Claude target; that pairing
answers `422 cursor_to_claude_unsupported` because the result would not resume.

The response also echoes `X-Vibeshub-Repo`, `X-Vibeshub-Git-Branch`, and
`X-Vibeshub-Git-Commit` when the trace has them, and
[`cli/vibeshub_client/repo_state.py`](cli/vibeshub_client/repo_state.py) turns
those into advisory notes (wrong origin, branch missing locally, start commit not
in this clone). `--checkout` additionally switches to the session's branch when
that branch exists and the working tree is clean. Without it the command never
touches your working tree, and the recorded start commit is provenance only: it
is never checked out.

Private traces authenticate with your `gh auth token`. The token is only ever
sent to the configured `VIBESHUB_SERVER_URL` host, so a pasted URL pointing at a
look-alike server is refused rather than handed your credentials.

## Adding another platform

1. Add a `TranscriptReader` subclass next to `reader.py` / `codex_reader.py` /
   `cursor_reader.py` that:
   - returns the transcript JSONL path for the active session
   - returns a stable `platform_id` string
2. Wire it into `platform_adapter.select_adapter()`.
3. Hook the platform's event surface so PR create/update/push invokes
   `run_share_pipeline()` (Claude/Codex: `PostToolUse` on `Bash`; Cursor:
   `afterShellExecution`).
4. Add a slash command (or platform equivalent) for manual share + delete.
5. If the platform needs its own marketplace package (like Cursor), extend
   `scripts/sync-cursor-plugin.py` or add a sibling generator, keeping
   `plugins/cli` as the shared source.
6. Document install + config in [cli/README.md](cli/README.md).

The server accepts any free-form `platform` on `/api/ingest`. Non-Claude
transcript shapes are converted to Claude-shaped JSONL at ingest (see
`webapp/backend/app/codex_convert.py` and `cursor_convert.py`); the viewer and
digest agent always read that converted stream.
