import json
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def test_codex_manifest_exposes_skills_directory():
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text()
    )

    assert manifest["skills"] == "./skills/"


def test_share_trace_skill_is_packaged_for_codex():
    skill = PLUGIN_ROOT / "skills" / "share-trace" / "SKILL.md"

    text = skill.read_text()
    assert "name: share-trace" in text
    assert "commands/share-trace.py" in text
    assert "vibeshub:share-trace" in text


def test_share_trace_skill_documents_codex_slash_invocation():
    skill = PLUGIN_ROOT / "skills" / "share-trace" / "SKILL.md"

    text = skill.read_text()
    assert "/vibeshub:share-trace" in text
    assert "/share-trace" in text
    assert "Codex surfaces plugin skills" in text


def test_import_trace_skill_is_packaged_for_codex():
    skill = PLUGIN_ROOT / "skills" / "import-trace" / "SKILL.md"

    text = skill.read_text()
    assert "name: import-trace" in text
    assert "commands/import-trace.py" in text
    assert "/vibeshub:import-trace" in text
    assert "--to codex" in text and "--to claude" in text
    assert "--checkout" in text


def test_import_trace_skill_uses_codex_native_helper_instructions():
    skill = PLUGIN_ROOT / "skills" / "import-trace" / "SKILL.md"

    text = skill.read_text()
    assert "Resolve the plugin root" in text
    assert "Do not use commands/import-trace.md" in text
    assert "CLAUDE_PLUGIN_ROOT" not in text
    assert "$ARGUMENTS" not in text


def test_share_trace_skill_uses_codex_native_helper_instructions():
    skill = PLUGIN_ROOT / "skills" / "share-trace" / "SKILL.md"

    text = skill.read_text()
    assert "Resolve the plugin root" in text
    assert "Do not use commands/share-trace.md" in text
    assert "CLAUDE_PLUGIN_ROOT" not in text
    assert "$ARGUMENTS" not in text
