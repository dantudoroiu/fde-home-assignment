"""Domain schemas.

TriageResult and DraftResult are the contracts with the LLM: they are sent to the API as JSON schemas
(structured outputs) and every response is validated against them locally. Fields deliberately have no
defaults so the generated schema marks all of them as required.
"""

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "billing",
    "technical",
    "account_access",
    "bug_report",
    "feature_request",
    "security",
    "data_privacy",
    "other",
]
Priority = Literal["P1", "P2", "P3", "P4"]
Sentiment = Literal["positive", "neutral", "frustrated", "angry"]
RiskSignal = Literal[
    "outage",
    "data_loss",
    "security_incident",
    "legal_threat",
    "churn_risk",
    "prompt_injection",
    "account_ownership_change",  # possible social engineering: authority must be verified
]
EntityType = Literal["account_id", "invoice_id", "email", "error_code", "product_area", "other"]

TicketStatus = Literal["received", "processing", "ready", "needs_review", "ai_unavailable", "done"]
FeedbackAction = Literal["accepted", "edited", "rejected"]

CATEGORIES: tuple[str, ...] = Category.__args__  # type: ignore[attr-defined]
PRIORITIES: tuple[str, ...] = Priority.__args__  # type: ignore[attr-defined]


class Entity(BaseModel):
    type: EntityType
    value: str


class TriageResult(BaseModel):
    category: Category
    priority: Priority
    sentiment: Sentiment
    summary: str = Field(description="One sentence, at most 25 words, written for a support agent.")
    entities: list[Entity]
    risk_signals: list[RiskSignal]
    confidence: float = Field(ge=0, le=1, description="Confidence in category and priority, 0 to 1.")


class DraftResult(BaseModel):
    reply: str = Field(description="Plain-text reply to the customer, ready for an agent to review.")
    cited_article_ids: list[str] = Field(description="IDs of the KB articles the reply relies on.")
    questions_for_customer: list[str] = Field(
        description="Information still needed from the customer. Empty if none."
    )


class TicketIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8000)
    customer_email: str = Field(min_length=3, max_length=254)


class FeedbackIn(BaseModel):
    action: FeedbackAction
    final_reply: str | None = None
    corrected_category: Category | None = None
    corrected_priority: Priority | None = None


class Article(BaseModel):
    id: str
    title: str
    body: str
