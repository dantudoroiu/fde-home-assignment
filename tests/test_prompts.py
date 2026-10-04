from app.llm.prompts import DRAFT_RULES, build_draft_system, build_draft_user
from tests.conftest import make_triage


def test_full_context_system_prompt_contains_every_article(kb):
    system = build_draft_system(kb.articles)
    for article in kb.articles:
        assert f'<article id="{article.id}"' in system


def test_system_prompt_is_stable_for_prompt_caching(kb):
    # Any byte difference between calls breaks the cache prefix: order must not depend on input order.
    assert build_draft_system(kb.articles) == build_draft_system(list(reversed(kb.articles)))


def test_bm25_system_prompt_has_rules_only():
    assert build_draft_system(None) == DRAFT_RULES


def test_full_context_user_turn_has_ticket_but_no_articles():
    user = build_draft_user("Subject line", "Ticket body", make_triage(), articles=None)
    assert "<article" not in user and "<articles>" not in user
    assert "<ticket>" in user and "Ticket body" in user


def test_bm25_user_turn_marks_empty_retrieval():
    user = build_draft_user("s", "b", make_triage(), articles=[])
    assert "(no relevant articles found)" in user
