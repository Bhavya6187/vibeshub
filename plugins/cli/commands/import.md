---
name: import
description: Import a vibeshub trace as a resumable Claude Code session in this project.
argument-hint: "<trace-url-or-short-id> [--checkout]"
---

Download a trace from vibeshub, convert it if needed, and place it as a
session for the current project directory. The command prints the exact
`claude --resume <id>` to open it. Works for traces uploaded from Codex
too. `--checkout` switches to the session's branch when the working tree
is clean; without it the command only prints repo-state guidance and never
touches your files. Private traces authenticate with `gh auth token`.

Run the helper script:

!python3 "${CLAUDE_PLUGIN_ROOT}/commands/import-trace.py" $ARGUMENTS --to claude
