from app.agents.ask.schema import AskAnswer, AskCitation, validate_citations


def _answer(citations):
    return AskAnswer(answer_markdown="Because of X.", citations=citations)


def test_session_citation_with_unknown_short_id_dropped():
    ans = _answer([
        AskCitation(type="session", title="ok", trace_short_id="known123"),
        AskCitation(type="chapter", title="bad", trace_short_id="ghost999",
                    anchor_uuid="u1"),
    ])
    kept = validate_citations(
        ans, valid_short_ids={"known123"}, repo_full_name="alice/x",
    )
    assert [c.trace_short_id for c in kept] == ["known123"]


def test_session_citation_without_short_id_dropped():
    ans = _answer([AskCitation(type="session", title="no id")])
    kept = validate_citations(
        ans, valid_short_ids={"known123"}, repo_full_name="alice/x",
    )
    assert kept == []


def test_pr_citation_without_url_gets_synthesized_url():
    ans = _answer([AskCitation(type="pr", title="the PR", pr_number=7)])
    kept = validate_citations(
        ans, valid_short_ids=set(), repo_full_name="alice/x",
    )
    assert kept[0].url == "https://github.com/alice/x/pull/7"


def test_pr_citation_without_number_or_url_dropped():
    ans = _answer([AskCitation(type="pr", title="mystery")])
    kept = validate_citations(
        ans, valid_short_ids=set(), repo_full_name="alice/x",
    )
    assert kept == []


def test_commit_and_file_citations_require_url():
    ans = _answer([
        AskCitation(type="commit", title="c",
                    url="https://github.com/alice/x/commit/abc"),
        AskCitation(type="file", title="f"),
    ])
    kept = validate_citations(
        ans, valid_short_ids=set(), repo_full_name="alice/x",
    )
    assert [c.type for c in kept] == ["commit"]


def test_prompt_has_no_em_dashes():
    from app.agents.ask.prompt import SYSTEM_PROMPT

    assert "—" not in SYSTEM_PROMPT
    assert "citations" in SYSTEM_PROMPT
