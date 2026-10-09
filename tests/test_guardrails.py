from app.pipeline.guardrails import detect_commitments, redact_pii, review_reasons, validate_citations
from tests.conftest import make_draft, make_triage

THRESHOLD = 0.7


# --- routing ----------------------------------------------------------------------------------

def test_routine_ticket_needs_no_review():
    assert review_reasons(make_triage(), flags=[], confidence_threshold=THRESHOLD) == []


def test_p1_angry_and_risk_signals_all_reported():
    triage = make_triage(priority="P1", sentiment="angry", risk_signals=["outage", "churn_risk"])
    assert review_reasons(triage, [], THRESHOLD) == ["priority_p1", "angry_customer", "risk:outage", "risk:churn_risk"]


def test_sensitive_category_always_reviewed_even_when_confident():
    triage = make_triage(category="data_privacy", priority="P4", confidence=0.99)
    assert review_reasons(triage, [], THRESHOLD) == ["sensitive_category:data_privacy"]


def test_low_confidence_triggers_review():
    assert review_reasons(make_triage(confidence=0.5), [], THRESHOLD) == ["low_confidence"]


def test_prompt_injection_signal_triggers_review():
    triage = make_triage(risk_signals=["prompt_injection"])
    assert "risk:prompt_injection" in review_reasons(triage, [], THRESHOLD)


def test_account_ownership_change_triggers_review_even_when_calm_and_confident():
    # Regression: "our admin left, make me admin" was calm, P2 and confident, so nothing fired.
    triage = make_triage(category="account_access", priority="P3", confidence=0.95,
                         risk_signals=["account_ownership_change"])
    assert review_reasons(triage, [], THRESHOLD) == ["risk:account_ownership_change"]


def test_only_review_flags_force_review():
    reasons = review_reasons(make_triage(), ["pii_redacted", "mentions_refund_or_commitment"], THRESHOLD)
    assert reasons == ["mentions_refund_or_commitment"]


def test_ungrounded_draft_needs_review():
    assert review_reasons(make_triage(), ["no_kb_citation"], THRESHOLD) == ["no_kb_citation"]


def test_ungrounded_draft_ok_for_feature_requests_and_praise():
    assert review_reasons(make_triage(category="feature_request", priority="P4"), ["no_kb_citation"], THRESHOLD) == []
    assert review_reasons(make_triage(category="other", sentiment="positive"), ["no_kb_citation"], THRESHOLD) == []


# --- citations & commitments ------------------------------------------------------------------

def test_hallucinated_citation_is_stripped_and_flagged():
    draft = make_draft(cited_article_ids=["KB-005", "KB-999"])
    cleaned, flags = validate_citations(draft, frozenset({"KB-005"}))
    assert cleaned.cited_article_ids == ["KB-005"]
    assert flags == ["invalid_citation"]


def test_valid_citations_untouched():
    draft = make_draft()
    cleaned, flags = validate_citations(draft, frozenset({"KB-005"}))
    assert cleaned is draft and flags == []


def test_refund_promise_is_flagged():
    assert detect_commitments("We have refunded the duplicate charge.") == ["mentions_refund_or_commitment"]
    assert detect_commitments("This will be fixed by Friday.") == ["mentions_refund_or_commitment"]


def test_neutral_reply_not_flagged():
    assert detect_commitments("You can update your card under Settings > Billing.") == []


def test_credit_card_is_not_a_promise_of_credit():
    # Routine payment-method replies (T07) must not be forced into review.
    assert detect_commitments("You can update your credit card under Settings > Billing.") == []
    assert detect_commitments("We'll credit your account for the outage.") == ["mentions_refund_or_commitment"]


# --- PII --------------------------------------------------------------------------------------

def test_card_number_redacted():
    text, n = redact_pii("New card is 4242 4242 4242 4242, exp 09/29.")
    assert "4242" not in text and "[REDACTED CARD]" in text and n == 1


def test_non_card_digit_runs_kept():
    # 16 digits failing the Luhn check (e.g. an order number) must not be masked.
    text, n = redact_pii("Order 1234567812345678 shipped")
    assert n == 0 and "1234567812345678" in text


def test_password_redacted():
    text, n = redact_pii("My password is Summer2026! and it fails")
    assert "Summer2026" not in text and n == 1
