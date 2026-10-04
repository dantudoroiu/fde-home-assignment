import pytest


@pytest.mark.parametrize(
    "query, expected_id",
    [
        ("I never got the forgot password reset email", "KB-001"),
        ("SAML response signature invalid after Okta certificate rotation", "KB-002"),
        ("lost phone authenticator app recovery codes", "KB-003"),
        ("invoice higher than usual proration seats", "KB-005"),
        ("API returns 429 too many requests", "KB-008"),
        ("webhook X-Brightdesk-Signature mismatch", "KB-009"),
        ("GDPR article 17 deletion of personal data", "KB-012"),
        ("suspicious sign-in from another country", "KB-013"),
    ],
)
def test_expected_article_in_top_3(kb, query, expected_id):
    assert expected_id in [a.id for a in kb.search(query, k=3)]


def test_unrelated_query_returns_nothing(kb):
    assert kb.search("zebra giraffe safari", k=3) == []


def test_off_topic_ticket_with_generic_words_returns_nothing(kb):
    # Regression: generic words ("new", "one", "customer") used to be enough to match unrelated articles.
    query = (
        "I need a new pair of headphones. My old pair broke. I need a new one urgently as I have a "
        "meeting with an important customer"
    )
    assert kb.search(query, k=3) == []


def test_empty_query_returns_nothing(kb):
    assert kb.search("the and of", k=3) == []
