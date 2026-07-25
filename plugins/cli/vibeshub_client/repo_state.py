"""Advisory repo-state check for imported sessions. Never mutates the
working tree unless checkout=True AND the tree is clean AND the branch
exists; the recorded starting commit is provenance, never a checkout
target (see the trace-porting spec)."""
from __future__ import annotations

import re
import subprocess

# Header values are recorded by whoever uploaded the trace and the server
# stores the branch name verbatim, so treat both as untrusted input: they
# reach git argv below and land in copy-paste command suggestions.
# A leading "-" would be read as a git option, so ref names must start with
# an alphanumeric or underscore.
_BRANCH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]{0,254}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _safe_branch(name: str) -> bool:
    return bool(
        _BRANCH_RE.match(name)
        and ".." not in name
        and "@{" not in name
        and not name.endswith(("/", ".lock"))
    )


def _git(cwd: str, *args: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=30.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _repo_matches(cwd: str, repo_full_name: str) -> bool | None:
    url = _git(cwd, "remote", "get-url", "origin")
    if url is None:
        return None
    return repo_full_name.lower() in url.lower()


def repo_state_report(
    headers: dict, cwd: str, *, checkout: bool = False,
) -> list[str]:
    repo = headers.get("x-vibeshub-repo")
    branch = headers.get("x-vibeshub-git-branch")
    commit = headers.get("x-vibeshub-git-commit")
    if not (repo or branch or commit):
        return []
    lines: list[str] = []
    unusable: list[str] = []
    if branch and not _safe_branch(branch):
        branch = None
        unusable.append("branch")
    if commit and not _COMMIT_RE.match(commit):
        commit = None
        unusable.append("commit")
    if unusable:
        # Deliberately not echoing the value: it is the untrusted part.
        lines.append(
            "note: ignoring an unusable git "
            f"{' and '.join(unusable)} recorded on this trace"
        )
    if not (repo or branch or commit):
        return lines
    if _git(cwd, "rev-parse", "--git-dir") is None:
        lines.append(
            "note: current directory is not a git repo; "
            "skipped repo-state checks"
        )
        return lines
    if repo:
        match = _repo_matches(cwd, repo)
        if match is False:
            lines.append(
                f"warning: this trace belongs to {repo}, but the current "
                "repo has a different origin"
            )
        elif match is None:
            lines.append(
                f"note: could not read origin remote to compare with {repo}"
            )
    branch_exists = bool(branch) and (
        _git(cwd, "rev-parse", "--verify", "--quiet", branch) is not None
        or _git(
            cwd, "rev-parse", "--verify", "--quiet", f"origin/{branch}"
        ) is not None
    )
    if branch and not branch_exists:
        lines.append(
            f"note: session branch {branch} not found locally; try: "
            f"git fetch origin {branch} && git checkout {branch}"
        )
    commit_reachable = bool(commit) and _git(
        cwd, "cat-file", "-e", f"{commit}^{{commit}}"
    ) is not None
    if commit and not commit_reachable:
        lines.append(
            f"note: session start commit {commit[:12]} is not in this "
            "clone (unpushed or unfetched history)"
        )
    current = _git(cwd, "branch", "--show-current")
    if branch and branch_exists and current == branch and (
        not commit or commit_reachable
    ):
        lines.append(f"repo state matches the session (branch {branch})")

    if checkout and branch:
        if not branch_exists:
            lines.append(f"checkout skipped: branch {branch} not found")
            return lines
        status = _git(cwd, "status", "--porcelain")
        if status is None or status != "":
            lines.append(
                "checkout skipped: working tree is not clean, commit or "
                "stash first"
            )
            return lines
        if current == branch:
            lines.append(f"already on {branch}")
            return lines
        _git(cwd, "fetch", "origin", branch)
        if _git(cwd, "checkout", branch) is not None:
            lines.append(f"switched to branch {branch}")
        else:
            lines.append(
                f"checkout failed: run git checkout {branch} manually"
            )
    return lines
