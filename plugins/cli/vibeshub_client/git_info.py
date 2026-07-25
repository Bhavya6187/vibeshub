from __future__ import annotations

import re
import subprocess

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _run(cwd: str, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out or None


def git_info(cwd: str) -> tuple[str | None, str | None]:
    """(branch, commit) for `cwd`, both None when not a git repo. Captured
    client-side because server redaction destroys in-blob commit SHAs."""
    branch = _run(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    commit = _run(cwd, "rev-parse", "HEAD")
    if commit is not None and not _SHA_RE.match(commit):
        commit = None
    if branch == "HEAD":  # detached; branch name is meaningless
        branch = None
    return branch, commit
