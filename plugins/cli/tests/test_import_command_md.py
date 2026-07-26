"""Pins the contract import.md relies on: a trailing --to claude wins."""
import importlib.util
import sys
from pathlib import Path

import pytest

_COMMANDS = Path(__file__).resolve().parents[1] / "commands"


def _load_import_trace():
    spec = importlib.util.spec_from_file_location(
        "import_trace_cmd", _COMMANDS / "import-trace.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(monkeypatch, argv):
    mod = _load_import_trace()
    seen = {}

    def fake_run_import(ref, target, *, server, cwd, checkout):
        seen.update(ref=ref, target=target, checkout=checkout)
        return 0

    monkeypatch.setattr(mod, "run_import", fake_run_import)
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as e:
        mod.main()
    assert e.value.code == 0
    return seen


def test_appended_to_claude_reaches_run_import(monkeypatch):
    """The plain `/import <ref> --checkout` shape: import.md appends the
    target the user never types."""
    seen = _run(
        monkeypatch,
        ["import-trace", "https://vibeshub.ai/t/abc", "--checkout",
         "--to", "claude"],
    )
    assert seen == {
        "ref": "https://vibeshub.ai/t/abc", "target": "claude",
        "checkout": True,
    }


def test_trailing_to_claude_wins(monkeypatch):
    """import.md appends `--to claude` after $ARGUMENTS, so the wrapper is
    only correct while argparse lets the last occurrence win: a user who
    types their own --to must not send this command to Codex."""
    seen = _run(
        monkeypatch,
        ["import-trace", "https://vibeshub.ai/t/abc",
         "--to", "codex", "--to", "claude"],
    )
    assert seen == {
        "ref": "https://vibeshub.ai/t/abc", "target": "claude",
        "checkout": False,
    }


def test_import_md_appends_to_claude():
    body = (_COMMANDS / "import.md").read_text(encoding="utf-8")
    assert "commands/import-trace.py" in body
    assert body.rstrip().endswith('$ARGUMENTS --to claude')
    assert "—" not in body
