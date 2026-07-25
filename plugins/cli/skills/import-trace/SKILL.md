---
name: import-trace
description: Download a vibeshub trace as a resumable session for Codex or Claude Code, then print the resume command.
---

# vibeshub import-trace

Use this skill when the user invokes `/vibeshub:import-trace`,
`/import-trace`, or asks to import, download, or resume a vibeshub trace from
Codex. Codex surfaces plugin skills as namespaced slash entries, so the
`/vibeshub:import-trace` entry should run these instructions rather than the
Claude Code command template.

Resolve the plugin root as the directory two levels above this `SKILL.md`,
then run the plugin's import helper by its absolute path:

```bash
python3 "<plugin-root>/commands/import-trace.py" <trace-url-or-short-id> --to codex
```

Run it from the user's current working directory. Do not `cd` into the plugin
root first: the helper records the directory it runs in as the session's
project path, and the repo-state check runs there too.

Pass through any user-supplied arguments. Common forms:

```bash
python3 "<plugin-root>/commands/import-trace.py" https://vibeshub.ai/t/<id> --to codex
python3 "<plugin-root>/commands/import-trace.py" <short-id> --to claude
python3 "<plugin-root>/commands/import-trace.py" <short-id> --to codex --checkout
```

`--to` is required. Use `--to codex` unless the user asks for Claude Code,
since this skill runs inside Codex.

- `--to codex` writes a rollout under the user's Codex home (`~/.codex` by
  default, `CODEX_HOME` is honored) and prints `codex resume <session-id>`.
- `--to claude` writes a session for the current directory and prints
  `claude --resume <session-id>`.
- `--checkout` also fetches and switches to the session's branch when that
  branch exists and the working tree is clean. Without it the helper only
  prints repo-state guidance and never touches the working tree.

Private traces authenticate with the user's `gh auth token`, and the token is
only ever sent to the configured vibeshub server.

Do not use commands/import-trace.md; that file is the Claude Code slash
command wrapper and depends on Claude-specific runtime expansion.

Report the helper's stdout and stderr back to the user, including the resume
command it prints.
