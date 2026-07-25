import subprocess

from vibeshub_client.repo_state import repo_state_report

SHA = "e" * 40


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "HOME": str(cwd)})


def _repo(tmp_path, branch="main"):
    _git(tmp_path, "init", "-b", branch)
    (tmp_path / "f").write_text("x")
    _git(tmp_path, "add", "f")
    _git(tmp_path, "commit", "-m", "c")
    return tmp_path


def test_no_git_headers_is_silent(tmp_path):
    assert repo_state_report({}, str(_repo(tmp_path))) == []


def test_wrong_repo_warns(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "remote", "add", "origin",
         "https://github.com/other/elsewhere.git")
    lines = repo_state_report(
        {"x-vibeshub-repo": "acme/widgets"}, str(repo))
    assert any("acme/widgets" in l for l in lines)


def test_missing_branch_suggests_fetch(tmp_path):
    lines = repo_state_report(
        {"x-vibeshub-git-branch": "feature/missing"}, str(_repo(tmp_path)))
    assert any("git fetch" in l for l in lines)


def test_matching_branch_and_commit_reports_ok(tmp_path):
    repo = _repo(tmp_path, branch="main")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
        text=True).stdout.strip()
    lines = repo_state_report(
        {"x-vibeshub-git-branch": "main", "x-vibeshub-git-commit": head},
        str(repo))
    assert any("matches" in l.lower() for l in lines)


def test_default_never_moves_the_working_tree(tmp_path):
    """The core invariant: without checkout=True nothing is mutated, even
    when the branch exists and the tree is clean."""
    repo = _repo(tmp_path)
    _git(repo, "branch", "feature/y")
    repo_state_report({"x-vibeshub-git-branch": "feature/y"}, str(repo))
    current = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo,
        capture_output=True, text=True).stdout.strip()
    assert current == "main"


def test_checkout_skips_missing_branch(tmp_path):
    repo = _repo(tmp_path)
    lines = repo_state_report(
        {"x-vibeshub-git-branch": "feature/gone"}, str(repo), checkout=True)
    assert any("checkout skipped: branch" in l for l in lines)
    current = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo,
        capture_output=True, text=True).stdout.strip()
    assert current == "main"


def test_checkout_when_already_on_branch(tmp_path):
    repo = _repo(tmp_path)
    lines = repo_state_report(
        {"x-vibeshub-git-branch": "main"}, str(repo), checkout=True)
    assert any("already on" in l for l in lines)
    assert not any("failed" in l or "skipped" in l for l in lines)


def test_checkout_refuses_dirty_tree(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "branch", "feature/y")
    (repo / "f").write_text("dirty")
    lines = repo_state_report(
        {"x-vibeshub-git-branch": "feature/y"}, str(repo), checkout=True)
    assert any("not clean" in l for l in lines)
    current = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo,
        capture_output=True, text=True).stdout.strip()
    assert current == "main"


def test_checkout_switches_when_clean(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "branch", "feature/y")
    lines = repo_state_report(
        {"x-vibeshub-git-branch": "feature/y"}, str(repo), checkout=True)
    current = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo,
        capture_output=True, text=True).stdout.strip()
    assert current == "feature/y"
    assert any("switched" in l.lower() for l in lines)


def test_unreachable_commit_is_noted(tmp_path):
    lines = repo_state_report(
        {"x-vibeshub-git-commit": SHA}, str(_repo(tmp_path)))
    assert any(SHA[:12] in l for l in lines)


def test_shell_ish_branch_is_ignored_not_echoed_as_a_command(tmp_path):
    """The branch header is uploader-controlled and echoed back verbatim by
    the server, so it must never land in a copy-paste command line."""
    lines = repo_state_report(
        {"x-vibeshub-git-branch": "main; rm -rf ~"}, str(_repo(tmp_path)))
    assert not any("rm -rf" in l for l in lines)
    assert any("ignoring" in l.lower() for l in lines)


def test_option_like_branch_never_reaches_git(tmp_path):
    repo = _repo(tmp_path)
    lines = repo_state_report(
        {"x-vibeshub-git-branch": "--all"}, str(repo), checkout=True)
    assert any("ignoring" in l.lower() for l in lines)
    current = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo,
        capture_output=True, text=True).stdout.strip()
    assert current == "main"


def test_non_sha_commit_is_ignored(tmp_path):
    lines = repo_state_report(
        {"x-vibeshub-git-commit": "'; rm -rf ~"}, str(_repo(tmp_path)))
    assert not any("rm -rf" in l for l in lines)
    assert any("ignoring" in l.lower() for l in lines)


def test_non_git_dir_is_noted_not_crashed(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    lines = repo_state_report(
        {"x-vibeshub-git-branch": "main"}, str(plain))
    assert any("not a git repo" in l for l in lines)
