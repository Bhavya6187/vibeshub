import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

_HANDOFF_PATH = (
    Path(__file__).resolve().parent.parent / "commands" / "handoff.py"
)


def _load_handoff():
    """Load the command by path so the tests exercise the exact file the
    slash command runs. Loading it puts the plugin root on sys.path and
    imports the plugin modules the command depends on; nothing else runs
    (the rest of the module is defs plus a __main__ guard)."""
    spec = importlib.util.spec_from_file_location("handoff_cmd", _HANDOFF_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _claude_reader():
    return types.SimpleNamespace(platform_id=lambda: "claude-code")


def _codex_reader():
    return types.SimpleNamespace(platform_id=lambda: "codex")


def _wire(mod, monkeypatch, *, pipeline, run_import, argv=("handoff",)):
    """Replace every seam the command reaches through: no network, no gh, no
    git."""
    monkeypatch.setattr(mod, "run_share_pipeline", pipeline)
    monkeypatch.setattr(mod, "run_import", run_import)
    monkeypatch.setattr(mod, "get_gh_token", lambda: "tok")
    monkeypatch.setattr(mod, "select_adapter", lambda payload: _claude_reader())
    monkeypatch.setattr(sys, "argv", list(argv))
    monkeypatch.setenv("VIBESHUB_SERVER_URL", "https://vibeshub.ai")


def test_handoff_uploads_then_imports(monkeypatch, capsys):
    mod = _load_handoff()
    calls = {}

    async def fake_pipeline(*, reader, hook_input, options):
        calls["upload"] = {
            "session_id": options.session_id,
            "pr_url": options.pr_url,
            "repo_full_name": options.repo_full_name,
        }
        return types.SimpleNamespace(
            uploaded=True,
            trace_url="https://vibeshub.ai/t/abc123",
            skip_reason=None,
        )

    def fake_run_import(ref, target, *, server, cwd, checkout):
        calls["import"] = {
            "ref": ref, "target": target, "server": server,
            "cwd": cwd, "checkout": checkout,
        }
        print("placed /tmp/x.jsonl")
        print("resume with: codex resume 0191-abc")
        return 0

    _wire(mod, monkeypatch, pipeline=fake_pipeline, run_import=fake_run_import)
    monkeypatch.setattr(mod, "_resolve_target", lambda *, arg: (None, "a/repo"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")

    with pytest.raises(SystemExit) as exc:
        mod.main()

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "trace uploaded: https://vibeshub.ai/t/abc123" in out
    assert out.rstrip().endswith("resume with: codex resume 0191-abc")
    assert calls["upload"]["session_id"] == "sess-1"
    assert calls["upload"]["repo_full_name"] == "a/repo"
    assert calls["import"]["ref"] == "https://vibeshub.ai/t/abc123"
    assert calls["import"]["target"] == "codex"
    assert calls["import"]["server"] == "https://vibeshub.ai"
    assert calls["import"]["cwd"] == os.getcwd()
    assert calls["import"]["checkout"] is False


def test_handoff_forwards_pr_argument(monkeypatch, capsys):
    mod = _load_handoff()
    calls = {}

    async def fake_pipeline(*, reader, hook_input, options):
        calls["upload"] = {"pr_url": options.pr_url}
        return types.SimpleNamespace(
            uploaded=True,
            trace_url="https://vibeshub.ai/t/abc123",
            skip_reason=None,
        )

    def fake_run_import(ref, target, *, server, cwd, checkout):
        print("resume with: codex resume 0191-abc")
        return 0

    def fake_resolve_target(*, arg):
        calls["target_arg"] = arg
        return "https://github.com/a/r/pull/7", None

    _wire(
        mod, monkeypatch, pipeline=fake_pipeline,
        run_import=fake_run_import, argv=("handoff", "7"),
    )
    monkeypatch.setattr(mod, "_resolve_target", fake_resolve_target)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")

    with pytest.raises(SystemExit) as exc:
        mod.main()

    assert exc.value.code == 0
    assert calls["target_arg"] == "7"
    assert calls["upload"]["pr_url"] == "https://github.com/a/r/pull/7"


def test_handoff_aborts_on_skipped_upload(monkeypatch, capsys):
    mod = _load_handoff()
    calls = {}

    async def fake_pipeline(*, reader, hook_input, options):
        calls["upload"] = True
        return types.SimpleNamespace(
            uploaded=False, trace_url=None,
            skip_reason="no transcript found",
        )

    def fake_run_import(ref, target, *, server, cwd, checkout):
        calls["import"] = True
        return 0

    _wire(mod, monkeypatch, pipeline=fake_pipeline, run_import=fake_run_import)
    monkeypatch.setattr(mod, "_resolve_target", lambda *, arg: (None, None))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")

    with pytest.raises(SystemExit) as exc:
        mod.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "no transcript found" in captured.err
    assert calls["upload"] is True
    assert "import" not in calls
    assert "resume with:" not in captured.out


def test_handoff_reports_placement_failure_with_trace_url(monkeypatch, capsys):
    mod = _load_handoff()

    async def fake_pipeline(*, reader, hook_input, options):
        return types.SimpleNamespace(
            uploaded=True,
            trace_url="https://vibeshub.ai/t/abc123",
            skip_reason=None,
        )

    def fake_run_import(ref, target, *, server, cwd, checkout):
        raise mod.ImportTraceError("boom")

    _wire(mod, monkeypatch, pipeline=fake_pipeline, run_import=fake_run_import)
    monkeypatch.setattr(mod, "_resolve_target", lambda *, arg: (None, None))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")

    with pytest.raises(SystemExit) as exc:
        mod.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "trace uploaded: https://vibeshub.ai/t/abc123" in captured.out
    assert "boom" in captured.err
    assert "https://vibeshub.ai/t/abc123" in captured.err
    assert "import-trace" in captured.err


def test_handoff_requires_session_id(monkeypatch, capsys):
    mod = _load_handoff()
    calls = {}

    async def fake_pipeline(*, reader, hook_input, options):
        calls["upload"] = True
        return types.SimpleNamespace(
            uploaded=True, trace_url="https://vibeshub.ai/t/abc123",
            skip_reason=None,
        )

    def fake_run_import(ref, target, *, server, cwd, checkout):
        calls["import"] = True
        return 0

    _wire(mod, monkeypatch, pipeline=fake_pipeline, run_import=fake_run_import)
    monkeypatch.setattr(mod, "_resolve_target", lambda *, arg: (None, None))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

    with pytest.raises(SystemExit) as exc:
        mod.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "must be run inside a Claude Code session" in captured.err
    assert calls == {}
    assert captured.out == ""


def test_handoff_refuses_non_claude_reader(monkeypatch, capsys):
    """CODEX_HOME set in a Claude Code shell makes select_adapter hand back the
    Codex reader even though CLAUDE_CODE_SESSION_ID is present. Uploading then
    would port the newest Codex rollout, not this conversation, so the command
    must refuse instead."""
    mod = _load_handoff()
    calls = {}

    async def fake_pipeline(*, reader, hook_input, options):
        calls["upload"] = True
        return types.SimpleNamespace(
            uploaded=True, trace_url="https://vibeshub.ai/t/abc123",
            skip_reason=None,
        )

    def fake_run_import(ref, target, *, server, cwd, checkout):
        calls["import"] = True
        return 0

    _wire(mod, monkeypatch, pipeline=fake_pipeline, run_import=fake_run_import)
    monkeypatch.setattr(mod, "select_adapter", lambda payload: _codex_reader())
    monkeypatch.setattr(mod, "_resolve_target", lambda *, arg: (None, None))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    monkeypatch.setenv("CODEX_HOME", "/home/u/.codex")

    with pytest.raises(SystemExit) as exc:
        mod.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "CODEX_HOME" in captured.err
    assert "share-trace" in captured.err
    assert calls == {}
    assert captured.out == ""


def test_handoff_notes_skip_reason_on_successful_upload(monkeypatch, capsys):
    """run_share_pipeline reports partial failures (a failed PR comment) via
    skip_reason even when uploaded is True. share-trace prints it; so must
    handoff, and on stderr so the stdout last line stays the resume command."""
    mod = _load_handoff()

    async def fake_pipeline(*, reader, hook_input, options):
        return types.SimpleNamespace(
            uploaded=True,
            trace_url="https://vibeshub.ai/t/abc123",
            skip_reason="comment failed: HTTP 403",
        )

    def fake_run_import(ref, target, *, server, cwd, checkout):
        print("resume with: codex resume 0191-abc")
        return 0

    _wire(mod, monkeypatch, pipeline=fake_pipeline, run_import=fake_run_import)
    monkeypatch.setattr(mod, "_resolve_target", lambda *, arg: (None, None))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")

    with pytest.raises(SystemExit) as exc:
        mod.main()

    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "note: comment failed: HTTP 403" in captured.err
    assert captured.out.rstrip().endswith("resume with: codex resume 0191-abc")


def test_handoff_retry_command_quotes_plugin_root(monkeypatch, capsys):
    """A plugin root with spaces must still yield a copy-pasteable command."""
    mod = _load_handoff()

    async def fake_pipeline(*, reader, hook_input, options):
        return types.SimpleNamespace(
            uploaded=True,
            trace_url="https://vibeshub.ai/t/abc123",
            skip_reason=None,
        )

    def fake_run_import(ref, target, *, server, cwd, checkout):
        raise mod.ImportTraceError("boom")

    _wire(mod, monkeypatch, pipeline=fake_pipeline, run_import=fake_run_import)
    monkeypatch.setattr(mod, "_resolve_target", lambda *, arg: (None, None))
    monkeypatch.setattr(mod, "_PLUGIN_ROOT", Path("/Users/a/My Plugins/cli"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")

    with pytest.raises(SystemExit) as exc:
        mod.main()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert '"/Users/a/My Plugins/cli/commands/import-trace.py"' in err


def test_handoff_md_invokes_the_command_script():
    body = (_HANDOFF_PATH.parent / "handoff.md").read_text(encoding="utf-8")
    assert "commands/handoff.py" in body
    assert "—" not in body
