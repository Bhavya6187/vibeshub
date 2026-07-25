import io
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from reader import ClaudeCodeTranscriptReader
from vibeshub_client.pipeline import RunOptions, run_share_pipeline
from vibeshub_client.upload import UploadResult
from vibeshub_client.version import PLUGIN_VERSION

FIXTURES = Path(__file__).parent / "fixtures" / "sessions"


@pytest.mark.asyncio
async def test_pipeline_builds_bundle_with_agents(tmp_path):
    # Replicate Claude Code's on-disk layout from the single-agent fixture
    project_root = tmp_path / "projects" / "-fake-cwd"
    project_root.mkdir(parents=True)
    (project_root / "sess1.jsonl").write_bytes(
        (FIXTURES / "single-agent" / "session.jsonl").read_bytes()
    )
    session_dir = project_root / "sess1"
    session_dir.mkdir()
    (session_dir / "subagents").mkdir()
    for f in (FIXTURES / "single-agent" / "subagents").iterdir():
        (session_dir / "subagents" / f.name).write_bytes(f.read_bytes())

    reader = ClaudeCodeTranscriptReader()
    hook_input = {
        "session_id": "sess1",
        "cwd": "/fake/cwd",
        "transcript_path": str(project_root / "sess1.jsonl"),
    }

    captured: dict = {}

    async def fake_upload(
        *, server_url, token, tar_bytes, pr_url, repo_full_name,
        plugin_version, session_id, redaction_count_client,
        platform="claude-code", timeout=60.0,
        git_branch=None, git_commit=None,
    ):
        captured["tar_bytes"] = tar_bytes
        captured["plugin_version"] = plugin_version
        captured["pr_url"] = pr_url
        captured["repo_full_name"] = repo_full_name
        captured["platform"] = platform
        return UploadResult(trace_id="t1", short_id="abc", trace_url="https://x/abc")

    with patch("vibeshub_client.pipeline.upload_bundle", new=fake_upload), \
         patch("vibeshub_client.pipeline.post_pr_comment") as mock_comment:
        result = await run_share_pipeline(
            reader=reader,
            hook_input=hook_input,
            options=RunOptions(
                server_url="https://x",
                token="t",
                pr_url="https://github.com/a/r/pull/1",
                session_id="sess1",
            ),
        )

    assert result.uploaded is True
    assert result.short_id == "abc"
    # Verify captured tar contains main + the one agent
    with tarfile.open(fileobj=io.BytesIO(captured["tar_bytes"]), mode="r:gz") as tar:
        names = {m.name for m in tar.getmembers()}
    assert "main.jsonl" in names
    assert "agents/a1111111111111111.jsonl" in names
    assert "agents/a1111111111111111.meta.json" in names
    assert captured["plugin_version"] == PLUGIN_VERSION
    # Pipeline threads reader.platform_id() into the upload.
    assert captured["platform"] == "claude-code"
    # fake_upload returns a default UploadResult (created=True) -> comment posted.
    mock_comment.assert_called_once()


@pytest.mark.asyncio
async def test_pipeline_skips_when_main_missing(tmp_path):
    """aborted-parent: only subagents/, no main jsonl. Pipeline must not crash."""
    project_root = tmp_path / "projects" / "-fake-cwd"
    project_root.mkdir(parents=True)
    session_dir = project_root / "sess1"
    session_dir.mkdir()
    (session_dir / "subagents").mkdir()
    src = FIXTURES / "aborted-parent" / "subagents"
    for f in src.iterdir():
        (session_dir / "subagents" / f.name).write_bytes(f.read_bytes())

    reader = ClaudeCodeTranscriptReader()
    hook_input = {
        "session_id": "sess1",
        "cwd": "/fake/cwd",
        "transcript_path": str(project_root / "sess1.jsonl"),
    }

    result = await run_share_pipeline(
        reader=reader,
        hook_input=hook_input,
        options=RunOptions(
            server_url="https://x",
            token="t",
            pr_url="https://github.com/a/r/pull/1",
        ),
    )
    assert result.uploaded is False
    assert "transcript" in (result.skip_reason or "").lower()


@pytest.mark.asyncio
async def test_pipeline_skips_comment_when_trace_not_created(tmp_path):
    project_root = tmp_path / "projects" / "-fake-cwd"
    project_root.mkdir(parents=True)
    (project_root / "sess1.jsonl").write_bytes(
        (FIXTURES / "single-agent" / "session.jsonl").read_bytes()
    )
    session_dir = project_root / "sess1"
    session_dir.mkdir()
    (session_dir / "subagents").mkdir()
    for f in (FIXTURES / "single-agent" / "subagents").iterdir():
        (session_dir / "subagents" / f.name).write_bytes(f.read_bytes())

    reader = ClaudeCodeTranscriptReader()
    hook_input = {
        "session_id": "sess1",
        "cwd": "/fake/cwd",
        "transcript_path": str(project_root / "sess1.jsonl"),
    }

    async def fake_upload(
        *, server_url, token, tar_bytes, pr_url, repo_full_name,
        plugin_version, session_id, redaction_count_client,
        platform="claude-code", timeout=60.0,
        git_branch=None, git_commit=None,
    ):
        return UploadResult(
            trace_id="t1", short_id="abc",
            trace_url="https://x/abc", created=False,
        )

    with patch("vibeshub_client.pipeline.upload_bundle", new=fake_upload), \
         patch("vibeshub_client.pipeline.post_pr_comment") as mock_comment:
        result = await run_share_pipeline(
            reader=reader,
            hook_input=hook_input,
            options=RunOptions(
                server_url="https://x",
                token="t",
                pr_url="https://github.com/a/r/pull/1",
                session_id="sess1",
            ),
        )

    assert result.uploaded is True
    assert result.created is False
    mock_comment.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_standalone_uploads_without_comment(tmp_path):
    project_root = tmp_path / "projects" / "-fake-cwd"
    project_root.mkdir(parents=True)
    (project_root / "sess1.jsonl").write_bytes(
        (FIXTURES / "single-agent" / "session.jsonl").read_bytes()
    )
    session_dir = project_root / "sess1"
    session_dir.mkdir()
    (session_dir / "subagents").mkdir()
    for f in (FIXTURES / "single-agent" / "subagents").iterdir():
        (session_dir / "subagents" / f.name).write_bytes(f.read_bytes())

    reader = ClaudeCodeTranscriptReader()
    hook_input = {
        "session_id": "sess1",
        "cwd": "/fake/cwd",
        "transcript_path": str(project_root / "sess1.jsonl"),
    }

    captured: dict = {}

    async def fake_upload(
        *, server_url, token, tar_bytes, pr_url, repo_full_name,
        plugin_version, session_id, redaction_count_client,
        platform="claude-code", timeout=60.0,
        git_branch=None, git_commit=None,
    ):
        captured["pr_url"] = pr_url
        captured["repo_full_name"] = repo_full_name
        return UploadResult(trace_id="t1", short_id="abc", trace_url="https://x/t/abc")

    with patch("vibeshub_client.pipeline.upload_bundle", new=fake_upload), \
         patch("vibeshub_client.pipeline.post_pr_comment") as mock_comment:
        result = await run_share_pipeline(
            reader=reader,
            hook_input=hook_input,
            options=RunOptions(
                server_url="https://x",
                token="t",
                pr_url=None,
                repo_full_name=None,
                session_id="sess1",
            ),
        )

    assert result.uploaded is True
    assert result.trace_url == "https://x/t/abc"
    assert captured["pr_url"] is None
    assert captured["repo_full_name"] is None
    mock_comment.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_repo_only_uploads_without_comment(tmp_path):
    project_root = tmp_path / "projects" / "-fake-cwd"
    project_root.mkdir(parents=True)
    (project_root / "sess1.jsonl").write_bytes(
        (FIXTURES / "single-agent" / "session.jsonl").read_bytes()
    )
    session_dir = project_root / "sess1"
    session_dir.mkdir()
    (session_dir / "subagents").mkdir()
    for f in (FIXTURES / "single-agent" / "subagents").iterdir():
        (session_dir / "subagents" / f.name).write_bytes(f.read_bytes())

    reader = ClaudeCodeTranscriptReader()
    hook_input = {
        "session_id": "sess1",
        "cwd": "/fake/cwd",
        "transcript_path": str(project_root / "sess1.jsonl"),
    }

    captured: dict = {}

    async def fake_upload(
        *, server_url, token, tar_bytes, pr_url, repo_full_name,
        plugin_version, session_id, redaction_count_client,
        platform="claude-code", timeout=60.0,
        git_branch=None, git_commit=None,
    ):
        captured["pr_url"] = pr_url
        captured["repo_full_name"] = repo_full_name
        return UploadResult(trace_id="t1", short_id="abc", trace_url="https://x/t/abc")

    with patch("vibeshub_client.pipeline.upload_bundle", new=fake_upload), \
         patch("vibeshub_client.pipeline.post_pr_comment") as mock_comment:
        result = await run_share_pipeline(
            reader=reader,
            hook_input=hook_input,
            options=RunOptions(
                server_url="https://x",
                token="t",
                pr_url=None,
                repo_full_name="alice/repo",
                session_id="sess1",
            ),
        )

    assert result.uploaded is True
    assert captured["pr_url"] is None
    assert captured["repo_full_name"] == "alice/repo"
    mock_comment.assert_not_called()


def _init_git_repo(path: Path, branch: str) -> None:
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "HOME": str(path),
    }
    for args in (
        ["init", "-b", branch],
        ["add", "-A"],
        ["commit", "-m", "c", "--allow-empty"],
    ):
        subprocess.run(
            ["git", *args], cwd=path, check=True, capture_output=True, env=env,
        )


@pytest.mark.asyncio
async def test_pipeline_sends_git_branch_and_commit(tmp_path):
    """The share pipeline captures branch/commit from the hook cwd and threads
    them into the upload."""
    project_root = tmp_path / "projects" / "-fake-cwd"
    project_root.mkdir(parents=True)
    (project_root / "sess1.jsonl").write_bytes(
        (FIXTURES / "single-agent" / "session.jsonl").read_bytes()
    )
    session_dir = project_root / "sess1"
    session_dir.mkdir()
    (session_dir / "subagents").mkdir()
    for f in (FIXTURES / "single-agent" / "subagents").iterdir():
        (session_dir / "subagents" / f.name).write_bytes(f.read_bytes())

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo, "feature/port")

    reader = ClaudeCodeTranscriptReader()
    hook_input = {
        "session_id": "sess1",
        "cwd": str(repo),
        "transcript_path": str(project_root / "sess1.jsonl"),
    }

    captured: dict = {}

    async def fake_upload(
        *, server_url, token, tar_bytes, pr_url, repo_full_name,
        plugin_version, session_id, redaction_count_client,
        platform="claude-code", timeout=60.0,
        git_branch=None, git_commit=None,
    ):
        captured["git_branch"] = git_branch
        captured["git_commit"] = git_commit
        return UploadResult(trace_id="t1", short_id="abc", trace_url="https://x/t/abc")

    with patch("vibeshub_client.pipeline.upload_bundle", new=fake_upload), \
         patch("vibeshub_client.pipeline.post_pr_comment"):
        result = await run_share_pipeline(
            reader=reader,
            hook_input=hook_input,
            options=RunOptions(
                server_url="https://x",
                token="t",
                pr_url=None,
                repo_full_name="alice/repo",
                session_id="sess1",
            ),
        )

    assert result.uploaded is True
    assert captured["git_branch"] == "feature/port"
    assert captured["git_commit"] is not None
    assert len(captured["git_commit"]) == 40


def test_capture_git_info_survives_git_failure():
    """Git capture is best effort: a share must never fail because git did."""
    from vibeshub_client.pipeline import _capture_git_info

    with patch(
        "vibeshub_client.pipeline.git_info", side_effect=OSError("boom")
    ):
        assert _capture_git_info({"cwd": "/fake/cwd"}) == (None, None)


def test_capture_git_info_drops_values_that_cannot_ride_a_header():
    """HTTP header values are latin-1 on the wire, so a branch name outside it
    would raise mid-upload. Dropping the name beats losing the share."""
    from vibeshub_client.pipeline import _capture_git_info

    with patch(
        "vibeshub_client.pipeline.git_info",
        return_value=("feature/日本語", "a" * 40),
    ):
        assert _capture_git_info({"cwd": "/fake/cwd"}) == (None, "a" * 40)


def test_platform_label_cursor():
    from vibeshub_client.pipeline import _platform_label
    assert _platform_label("cursor") == "Cursor"
    assert _platform_label("codex") == "Codex CLI"
    assert _platform_label("claude-code") == "Claude Code"
