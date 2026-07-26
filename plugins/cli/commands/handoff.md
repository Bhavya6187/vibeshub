---
name: handoff
description: Upload this session to vibeshub and hand it off to Codex, printing the codex resume command.
argument-hint: "[<pr-number-or-url>]"
---

Upload the current Claude Code session to vibeshub (same target resolution
as /share-trace), then immediately place the converted Codex session on
this machine and print the exact resume command. After it runs, quit
Claude Code and run the printed `codex resume <id>` to continue this
conversation in Codex. Private traces authenticate with `gh auth token`.

Run the helper script:

!python3 "${CLAUDE_PLUGIN_ROOT}/commands/handoff.py" $ARGUMENTS
