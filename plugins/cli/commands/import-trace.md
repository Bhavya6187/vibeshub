---
name: import-trace
description: Download a vibeshub trace as a resumable session for Codex or Claude Code.
argument-hint: "<trace-url-or-short-id> --to codex|claude [--checkout]"
---

Download a trace from vibeshub and place it where the target CLI keeps its
sessions, then print the exact resume command.

- `--to codex` writes a rollout under ~/.codex/sessions; resume with
  `codex resume <session-id>`.
- `--to claude` writes a session for the current project directory; resume
  with `claude --resume <session-id>`.
- `--checkout` additionally fetches and switches to the session's branch
  when that branch exists and the working tree is clean. Without it, the
  command only prints repo-state guidance and never touches your working
  tree. The commit the session started from is provenance only, it is
  never checked out.

Private traces authenticate with your `gh auth token`.

Run the helper script:

!python3 "${CLAUDE_PLUGIN_ROOT}/commands/import-trace.py" $ARGUMENTS
