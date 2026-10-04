from app.config import Settings
from app.llm.client import LLMClient
from app.llm.prompts import TRIAGE_SYSTEM, build_triage_user
from app.models import TriageResult


def run_triage(
    llm: LLMClient, settings: Settings, subject: str, body: str, ticket_id: int | None = None
) -> TriageResult:
    return llm.generate(
        step="triage",
        model=settings.triage_model,
        system=TRIAGE_SYSTEM,
        user=build_triage_user(subject, body),
        schema=TriageResult,
        max_tokens=settings.triage_max_tokens,
        effort=settings.triage_effort,
        ticket_id=ticket_id,
    )
