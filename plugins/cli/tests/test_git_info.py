import subprocess

from vibeshub_client.git_info import git_info


def _git(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin",
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
             "HOME": str(cwd)},
    )


def test_git_info_in_repo(tmp_path):
    _git(tmp_path, "init", "-b", "feature/x")
    (tmp_path / "f").write_text("x")
    _git(tmp_path, "add", "f")
    _git(tmp_path, "commit", "-m", "c")
    branch, commit = git_info(str(tmp_path))
    assert branch == "feature/x"
    assert commit and len(commit) == 40


def test_git_info_outside_repo(tmp_path):
    assert git_info(str(tmp_path)) == (None, None)
