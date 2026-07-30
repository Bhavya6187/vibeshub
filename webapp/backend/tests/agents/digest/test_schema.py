import pytest
from pydantic import ValidationError

from app.agents.digest.schema import Chapter, Digest, FileNote, strip_em_dashes


def _digest(**over) -> Digest:
    base = dict(
        ask="Add a /healthcheck route",
        decisions=["Chose inline route in app/main.py over a router because YAGNI"],
        dead_ends=[],
        learnings=[],
        tests="none",
        chapters=[],
    )
    base.update(over)
    return Digest(**base)


def test_digest_accepts_new_shape():
    d = _digest(
        dead_ends=["Tried a separate APIRouter, abandoned as overkill"],
        learnings=["TestClient needs raise_server_exceptions=False"],
    )
    assert d.decisions[0].startswith("Chose inline route")
    assert d.learnings == ["TestClient needs raise_server_exceptions=False"]


def test_digest_rejects_missing_ask():
    with pytest.raises(ValidationError):
        Digest(  # type: ignore[call-arg]
            decisions=["d"], dead_ends=[], learnings=[],
            tests="t", chapters=[],
        )


def test_digest_rejects_old_string_shape():
    # Pre-restructure digests stored decisions/dead_ends as prose strings.
    # They must NOT validate; the API boundary hides them until the
    # re-digest backfill replaces them.
    with pytest.raises(ValidationError):
        Digest.model_validate({
            "ask": "a", "decisions": "prose", "files": "f",
            "tests": "t", "dead_ends": "prose", "chapters": [],
        })


def test_digest_has_no_files_field():
    assert "files" not in Digest.model_fields


def test_list_item_counts_are_capped():
    with pytest.raises(ValidationError):
        _digest(decisions=[f"d{i}" for i in range(7)])
    with pytest.raises(ValidationError):
        _digest(dead_ends=[f"e{i}" for i in range(5)])
    with pytest.raises(ValidationError):
        _digest(learnings=[f"l{i}" for i in range(6)])


def test_list_item_length_is_capped():
    with pytest.raises(ValidationError):
        _digest(decisions=["x" * 201])


def test_digest_caps_chapters_at_10():
    chapters = [
        Chapter(anchor_uuid=f"uuid-{i}", title=f"t{i}", caption=f"c{i}")
        for i in range(11)
    ]
    with pytest.raises(ValidationError):
        _digest(chapters=chapters)


def test_digest_caps_field_lengths():
    with pytest.raises(ValidationError):
        _digest(ask="x" * 201)


def test_chapter_caps_title_and_caption():
    with pytest.raises(ValidationError):
        Chapter(anchor_uuid="u", title="x" * 81, caption="c")
    with pytest.raises(ValidationError):
        Chapter(anchor_uuid="u", title="t", caption="x" * 161)


def test_strip_em_dashes_replaces_with_comma_between_words():
    assert strip_em_dashes("a — b") == "a, b"


def test_strip_em_dashes_handles_sentence_breaks():
    assert strip_em_dashes("one — two — three") == "one, two, three"


def test_strip_em_dashes_strips_unicode_em_dash_only():
    assert strip_em_dashes("file-name foo-bar") == "file-name foo-bar"


def test_digest_defaults_lists_to_empty():
    d = Digest.model_validate({"ask": "a", "tests": "d"})
    assert d.decisions == []
    assert d.dead_ends == []
    assert d.learnings == []
    assert d.file_notes == []


def test_file_note_round_trips():
    d = Digest.model_validate({
        "ask": "a", "tests": "d",
        "file_notes": [{"path": "src/x.ts", "caption": "Tighten the loop"}],
    })
    assert d.file_notes == [FileNote(path="src/x.ts", caption="Tighten the loop")]
