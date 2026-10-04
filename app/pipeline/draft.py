from app.config import Settings
from app.llm.client import LLMClient
from app.llm.prompts import build_draft_user
from app.models import Article, DraftResult, TriageResult


def run_draft(
    llm: LLMClient,
    settings: Settings,
    system: str,
    subject: str,
    body: str,
    triage: TriageResult,
    articles: list[Article] | None,
    ticket_id: int | None = None,
) -> DraftResult:
    """`system` is prebuilt once per Pipeline (it may contain the whole KB). `articles` is the
    retrieved subset in bm25 mode and None in full-context mode."""
    return llm.generate(
        step="draft",
        model=settings.draft_model,
        system=system,
        user=build_draft_user(subject, body, triage, articles),
        schema=DraftResult,
        max_tokens=settings.draft_max_tokens,
        effort=settings.draft_effort,
        ticket_id=ticket_id,
    )
