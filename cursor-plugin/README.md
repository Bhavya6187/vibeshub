# vibeshub for Cursor

Upload your **Cursor agent** conversation traces to
[vibeshub](https://vibeshub.ai/) when you open or update a pull request, so your
team can see how a change was actually built.

> **Generated (do not hand-edit).** This plugin tree is generated from
> `plugins/cli` in [`vibeshub/vibeshub`](https://github.com/vibeshub/vibeshub)
> by `scripts/sync-cursor-plugin.py` and mirrored to
> [`vibeshub/vibeshub-cursor`](https://github.com/vibeshub/vibeshub-cursor).
> Send changes to `plugins/cli`.

Version: 0.6.1

## What it does

After a `gh pr create`, `gh pr edit`, or `git push`, an `afterShellExecution`
hook redacts the current Cursor agent transcript (plus any subagent
transcripts), uploads it to vibeshub, and (for a brand-new trace) comments the
trace link on the PR.

Cursor transcripts record the conversation and tool calls but not tool
outputs, token counts, or the model name, so those fields are blank in the
viewer. Cursor traces are for viewing and sharing: they can be exported to
Codex, but cannot come back as a resumable Claude Code session.

## Privacy

Traces attached to a **public** GitHub repo default to public; traces attached
to a **private** repo are private and gated on the viewer's GitHub repo
access. Two redaction passes (client + server) catch known secret patterns,
but neither is a guarantee. Installing the plugin is consent for upload; to
stop, uninstall the plugin. This package is hook-only (no slash commands), so
to change a trace's visibility or delete it, use the manage menu on its trace
page at vibeshub.ai.

## Install

Once the marketplace listing is live, install **vibeshub** from the Cursor
marketplace, then reload the window. Until then, install from a checkout of
this repo (see "Local development / testing" below).

### Requirements

- [`gh`](https://cli.github.com/) installed and authenticated (`gh auth login`)
- Python 3.9 or newer available as `python3`
- A GitHub remote and, for `git push` or `gh pr edit`, an open pull request for
  the current branch

## Local development / testing (no marketplace needed)

Cursor loads plugins straight from a directory:

```sh
ln -s "$(pwd)" ~/.cursor/plugins/local/vibeshub-cursor
```

Enable **Settings → Features → "Include third-party Plugins, Skills, and other
configs"**, then **Reload Window**. The Plugins panel should show this plugin's
Cursor description and an `afterShellExecution` hook. Trigger it with a real
`git push` to an open PR and watch the Hooks output channel.

You can also exercise the share script without Cursor at all. This performs a
real upload (and, for a brand-new trace, a real PR comment), so point it at a
throwaway server unless you mean it:

```sh
echo '{"command":"git push","workspace_roots":["'"$(pwd)"'"]}' \
  | VIBESHUB_SERVER_URL=http://127.0.0.1:8000 VIBESHUB_PLATFORM=cursor \
    python3 hooks/on-pr-share.py
```

Real Cursor payloads carry no `cwd`; the hook resolves the repo from
`workspace_roots[0]`, so test payloads should use that shape too.

## Troubleshooting hooks

If the Hooks output channel reports that `./hooks/on-pr-share.sh` cannot be
found, Cursor did not resolve the command relative to the plugin root. Edit
`hooks/hooks.json` to use an absolute path to `on-pr-share.sh`, or install the
plugin locally by symlinking this directory into
`~/.cursor/plugins/local/vibeshub-cursor` (see "Local development / testing"
above) so Cursor resolves the hook from a fixed path.

When editing `hooks/hooks.json`, keep the top-level `"version": 1` key:
Cursor's hooks-config validator silently rejects the entire file without it,
and none of the plugin's hooks run.

Everything the hook skips or fails on (no open PR, not a git repo, upload
errors) is logged to `~/.vibeshub/hook.log`; override the location with
`VIBESHUB_HOOK_LOG`, and the upload target with `VIBESHUB_SERVER_URL`.

## License

MIT, see [LICENSE](./LICENSE).
