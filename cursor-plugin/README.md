# vibeshub for Cursor

Upload your **Cursor agent** conversation traces to
[vibeshub](https://vibeshub.ai/) when you open or update a pull request, so your
team can see how a change was actually built.

> **Generated (do not hand-edit).** This repository is generated from
> [`vibeshub/vibeshub`](https://github.com/vibeshub/vibeshub) by
> `scripts/sync-cursor-plugin.py`. Send changes there.

Version: 0.6.1

## What it does

After a `gh pr create`, `gh pr edit`, or `git push`, an `afterShellExecution`
hook redacts the current Cursor agent transcript, uploads it to vibeshub, and
(for a brand-new trace) comments the trace link on the PR.

## Install (Cursor marketplace)

Install **vibeshub** from the Cursor marketplace, then reload the window.

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

You can also exercise the share script without Cursor at all:

```sh
echo '{"command":"git push","cwd":"'"$(pwd)"'"}' \
  | VIBESHUB_PLATFORM=cursor python3 hooks/on-pr-share.py
```

## Troubleshooting hooks

If the Hooks output channel reports that `./hooks/on-pr-share.sh` cannot be
found, Cursor did not resolve the command relative to the plugin root. Edit
`hooks/hooks.json` to use an absolute path to `on-pr-share.sh`, or install the
plugin locally by symlinking this directory into
`~/.cursor/plugins/local/vibeshub-cursor` (see "Local development / testing"
above) so Cursor resolves the hook from a fixed path.

## License

MIT, see [LICENSE](./LICENSE).
