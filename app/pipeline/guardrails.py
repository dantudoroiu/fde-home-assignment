"""Deterministic checks around the model.

These rules decide what a human must look at. They are plain code rather than prompt instructions
because they must be predictable, testable, and auditable, and must not be talked out of by a ticket.
"""

import re

from app.models import DraftResult, TriageResult

# --- PII redaction (before anything is sent to the model) -----------------------------------------

_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|api[_ -]?key|secret|token)\b(\s*[:=]\s*|\s+is\s+)(\S+)"
)


def _luhn_ok(digits: str) -> bool:
    total, double = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if double:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        double = not double
    return total % 10 == 0


def redact_pii(text: str) -> tuple[str, int]:
    """Mask payment card numbers (Luhn-checked) and obvious credentials. Returns (text, count).

    This is a narrow safety net, not a DLP solution: names, addresses, and free-form secrets pass
    through. The README lists this as a known limitation.
    """
    count = 0

    def _card(match: re.Match[str]) -> str:
        nonlocal count
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            count += 1
            return "[REDACTED CARD]"
        return match.group(0)

    def _secret(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}{match.group(2)}[REDACTED]"

    text = _CARD_RE.sub(_card, text)
    text = _SECRET_RE.sub(_secret, text)
    return text, count


# --- Draft checks ------------------------------------------------------------------------------

_COMMITMENT_RE = re.compile(
    # "credit card" is a payment method, not a promise of credit.
    r"(?i)\b(refund(ed)?|credit(s|ed)?(?!\s*card)|reimburs\w*|compensat\w*|discount\w*|free of charge|"
    r"waive\w*|guarantee\w*|will be (fixed|resolved|deployed) (by|within|on))\b"
)


def validate_citations(draft: DraftResult, known_ids: frozenset[str]) -> tuple[DraftResult, list[str]]:
    """Drop citations to articles that don't exist; flag the draft if any were dropped."""
    valid = [cid for cid in draft.cited_article_ids if cid in known_ids]
    if len(valid) == len(draft.cited_article_ids):
        return draft, []
    return draft.model_copy(update={"cited_article_ids": valid}), ["invalid_citation"]


def detect_commitments(reply: str) -> list[str]:
    """Flag replies that mention money or dates. Policy: only a human may commit to those."""
    return ["mentions_refund_or_commitment"] if _COMMITMENT_RE.search(reply) else []


# --- Routing -------------------------------------------------------------------------------------

SENSITIVE_CATEGORIES = frozenset({"security", "data_privacy"})
# Flags that force review. Others (e.g. pii_redacted) are informational only.
REVIEW_FLAGS = frozenset({
    "invalid_citation",
    "mentions_refund_or_commitment",
    "no_kb_match",  # bm25: retrieval found nothing
    "draft_unavailable",
})
# full context: the draft relies on no article. That matters when the reply makes factual claims, but
# not for a "thanks, passed to the product team" reply to a feature request or praise.
UNGROUNDED_FLAG = "no_kb_citation"
UNGROUNDED_OK_CATEGORIES = frozenset({"feature_request"})


def review_reasons(triage: TriageResult, flags: list[str], confidence_threshold: float) -> list[str]:
    """Why a human must review this ticket before replying. An empty list means low-risk."""
    reasons: list[str] = []
    if triage.priority == "P1":
        reasons.append("priority_p1")
    if triage.sentiment == "angry":
        reasons.append("angry_customer")
    if triage.category in SENSITIVE_CATEGORIES:
        reasons.append(f"sensitive_category:{triage.category}")
    reasons.extend(f"risk:{signal}" for signal in triage.risk_signals)
    if triage.confidence < confidence_threshold:
        reasons.append("low_confidence")
    reasons.extend(flag for flag in flags if flag in REVIEW_FLAGS)
    low_stakes = triage.category in UNGROUNDED_OK_CATEGORIES or triage.sentiment == "positive"
    if UNGROUNDED_FLAG in flags and not low_stakes:
        reasons.append(UNGROUNDED_FLAG)
    return list(dict.fromkeys(reasons))  # dedupe, keep order
