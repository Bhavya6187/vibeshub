# vibeshub — Claude Code + Codex + Cursor plugin

Uploads your Claude Code, Codex, or Cursor conversation trace to vibeshub
whenever you create or update a PR (or push the branch), and posts a comment on
the PR linking to the trace. Trace visibility mirrors the repository on GitHub:
public repos stay public, private repos stay private and are gated on the
viewer's GitHub access.

| Platform | Package | Install |
|---|---|---|
| [Claude Code](#claude-code) | this repo (`plugins/cli`) | Marketplace plugin below |
| [Codex](#codex) | same package as Claude Code | Marketplace plugin; runtime auto-detects Codex |
| [Cursor](#cursor) | [vibeshub/vibeshub-cursor](https://github.com/vibeshub/vibeshub-cursor) | Cursor marketplace (generated from this tree) |

## Requirements

All platforms need:

- `gh` CLI, installed and authenticated (`gh auth login`) — your GitHub login
  is your vibeshub identity.
- `python3` 3.9+ on your `PATH` — hooks run with `python3`. The client uses
  only the Python standard library (plus a vendored
  [`truststore`](vibeshub_client/_vendor/README.md) on Python 3.10+ for OS-CA
  TLS verification), so there is nothing extra to `pip install`.

## Install

### Claude Code

Inside Claude Code, add the vibeshub marketplace and install the plugin:

```
/plugin marketplace add vibeshub/vibeshub
/plugin install vibeshub@vibeshub
```

Claude Code resolves the `<owner>/<repo>` shorthand against GitHub and reads
[.claude-plugin/marketplace.json](../../.claude-plugin/marketplace.json) from
the repo — no clone required.

### Codex

Install the same marketplace package as Claude Code (commands above). Codex
loads the `.codex-plugin/` metadata from this tree; at runtime
`platform_adapter` selects the Codex transcript reader from path/env signals
(`~/.codex/sessions/`, `CODEX_HOME`, etc.).

Manual share uses the namespaced skill — see [Manual share command](#manual-share-command).

### Cursor

Cursor runs the same share logic through its own hook system, packaged as a
separate plugin generated from this one by `scripts/sync-cursor-plugin.py` and
published at [vibeshub/vibeshub-cursor](https://github.com/vibeshub/vibeshub-cursor).

Install **vibeshub** from the Cursor marketplace, then Reload Window.

To install without the marketplace (local development or air-gapped machines),
symlink the generated plugin tree into Cursor's local plugins directory:

```
ln -s /path/to/vibeshub-cursor ~/.cursor/plugins/local/vibeshub-cursor
```

Enable Settings → Features → "Include third-party Plugins, Skills, and other
configs", then Reload Window.

An `afterShellExecution` hook runs the plugin's share script after a
`git push`, tagged with `VIBESHUB_PLATFORM=cursor`. It reads the Cursor agent
transcript from `~/.cursor/projects/<project>/agent-transcripts/<id>/`
(including any subagents) and uploads it the same way. Cursor transcripts record
the conversation and tool calls but not tool outputs, token counts, or the model
name, so those fields are blank in the viewer.

## Configure

| Env var | Default | Notes |
|---|---|---|
| `VIBESHUB_SERVER_URL` | `https://vibeshub.ai` | Override for self-hosting |
| `VIBESHUB_HOOK_LOG` | `~/.vibeshub/hook.log` | Where the hook appends its per-invocation log |
| `VIBESHUB_PLATFORM` | _(unset)_ | Set to `cursor` by Cursor hooks; otherwise inferred from transcript path / env |

## How it works

When a hook sees `gh pr create`, `gh pr edit`, or `git push`, the shared
pipeline:

1. Locates this session's transcript (plus any subagent transcripts):
   - Claude Code: `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`
   - Codex: `~/.codex/sessions/...`
   - Cursor: `~/.cursor/projects/<project>/agent-transcripts/<id>/`
2. Runs client-side redaction over the JSONL (AWS keys, GitHub tokens, OpenAI
   keys, Anthropic keys, JWTs, env-style assignments, and high-entropy tokens).
3. Resolves the target PR — from `gh pr create`'s stdout, or by looking up the
   current branch's open PR for `git push` / `gh pr edit`.
4. Uploads to vibeshub using your `gh auth token` for identity. TLS is verified
   against the OS trust store (Python 3.10+) so the upload works on networks
   behind a TLS-intercepting proxy whose root CA the OS already trusts.
5. On the first upload for a PR, posts a `gh pr comment` linking to the
   trace. Subsequent updates refresh the same trace in place.

Hook surfaces differ by platform: Claude Code and Codex use a `PostToolUse`
hook on `Bash`; Cursor uses `afterShellExecution`.

Installing the plugin is consent for upload. To stop uploading, uninstall the
plugin or remove the hook entry from your settings. After-the-fact deletion of
any trace is available via the manual share command below.

## Manual share command

Upload manually (e.g., the hook didn't run, or you want to re-share after
fixing something) or delete an existing trace. Without arguments it picks the
best target automatically:

1. An open PR you authored on the current branch — the trace is attached to that
   PR and a PR comment is posted.
2. Otherwise, if you are inside a git repo with a GitHub remote, the trace is
   attached to that repo (no PR).
3. Otherwise, a standalone public trace; you can switch it to private from the
   trace page in the vibeshub UI.

### Claude Code

- `/share-trace` — auto-detect per the order above
- `/share-trace <pr-url-or-number>` — share a specific PR
- `/share-trace delete <pr-url | pr-number | /t/<id> url | short-id>` — delete a
  trace. A bare number is always treated as a PR number.

### Codex

Plugin skills are surfaced as namespaced slash entries:

- `/vibeshub:share-trace` — auto-detect per the order above
- `/vibeshub:share-trace <pr-url-or-number>` — share a specific PR
- `/vibeshub:share-trace delete <pr-url | pr-number | /t/<id> url | short-id>` —
  delete a trace. A bare number is always treated as a PR number.

If you type `/share-trace` in Codex, ask Codex to run the
`vibeshub:share-trace` skill; the un-namespaced Claude command wrapper is not
used by Codex.

## Manual import command

Pull a trace back down as a session your CLI can resume. The trace is placed
where that CLI keeps its sessions and the exact resume command is printed:

- `/import-trace <trace-url-or-short-id> --to codex` (Claude Code) or
  `/vibeshub:import-trace <trace-url-or-short-id> --to codex` (Codex) writes a
  rollout under your Codex home (`~/.codex` by default, `CODEX_HOME` is
  honored), resumable with `codex resume <session-id>`.
- `--to claude` writes a session for the current directory, resumable with
  `claude --resume <session-id>`.
- Add `--checkout` to also fetch and switch to the session's branch when that
  branch exists and your working tree is clean. Without it the command only
  prints repo-state guidance and never touches your working tree. The commit
  the session started from is provenance only, it is never checked out.

Importing the same trace twice never overwrites the first copy; the second
import is re-identified so both sessions resume independently. Private traces
authenticate with your `gh auth token`, which is only sent to the vibeshub
server you configured.

Codex traces imported into Claude Code (and the reverse) are converted, so
some detail is dropped by design: reasoning is encrypted per provider and
cannot cross over. Cursor traces are view-grade only and have no Claude
target.

## Privacy

Traces attached to a **public** GitHub repo (and standalone traces) default to
public; traces attached to a **private** repo are private and gated on the
viewer's GitHub repo-read access. Two redaction passes (client + server) catch
known secret patterns, but neither is a guarantee. You can delete any trace
after the fact via
`/share-trace delete <pr-url | /t/<id> url | short-id>` (Claude Code) or
`/vibeshub:share-trace delete …` (Codex).
