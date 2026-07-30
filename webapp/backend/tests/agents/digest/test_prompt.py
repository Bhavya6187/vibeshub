from app.agents.digest.prompt import SYSTEM_PROMPT
from app.agents.digest.schema import Digest


def test_prompt_names_every_digest_field():
    for field in Digest.model_fields:
        assert f'"{field}"' in SYSTEM_PROMPT, f"prompt missing {field}"


def test_prompt_does_not_mention_dropped_files_field():
    # '"files"' would not match '"file_notes"'; this pins the removal.
    assert '"files"' not in SYSTEM_PROMPT


def test_prompt_teaches_item_templates_and_search_voice():
    assert "chose X over Y because Z" in SYSTEM_PROMPT
    assert "tried X, abandoned because Y" in SYSTEM_PROMPT
    assert "full-text searched" in SYSTEM_PROMPT
