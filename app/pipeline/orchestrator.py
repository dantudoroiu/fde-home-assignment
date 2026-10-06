"""Runs one ticket through redact → triage → (retrieve) → draft → guardrails and stores the outcome.

Grounding: in full_context mode (default) the whole KB is in the draft system prompt and the articles
recorded for the ticket are the ones the draft cited; in bm25 mode the top-k retrieved articles are
sent with the ticket. Full context falls back to bm25 if the KB exceeds the configured token budget.

Degradation policy (AI is never on the critical path):
- triage fails       → status ai_unavailable; the ticket is handled manually like before the tool existed
- draft fails        → triage is kept, status needs_review with reason draft_unavailable
- anything unexpected → ai_unavailable; a ticket is never left stuck in "processing"

`analyze()` is pure (no persistence) so scripts/run_eval.py measures exactly the production logic.
"""

import time
from dataclasses import dataclass, field

import structlog

from app.config import Settings
from app.db import Database
from app.llm.client import LLMClient, LLMError
from app.llm.prompts import build_draft_system
from app.models import Article, DraftResult, TriageResult
from app.observability import get_logger
from app.pipeline.draft import run_draft
from app.pipeline.guardrails import detect_commitments, redact_pii, review_reasons, validate_citations
from app.pipeline.retrieval import KnowledgeBase
from app.pipeline.triage import run_triage

log = get_logger(__name__)


@dataclass
class Analysis:
    status: str
    triage: TriageResult | None = None
    draft: DraftResult | None = None
    articles: list[Article] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    error: str | None = None


class Pipeline:
    def __init__(self, db: Database, llm: LLMClient, kb: KnowledgeBase, settings: Settings):
        self.db = db
        self.llm = llm
        self.kb = kb
        self.settings = settings

        self.grounding = settings.grounding
        if self.grounding == "full_context" and kb.estimated_tokens > settings.full_context_max_tokens:
            log.warning(
                "kb_too_large_for_full_context", kb_tokens=kb.estimated_tokens,
                budget=settings.full_context_max_tokens, fallback="bm25",
            )
            self.grounding = "bm25"
        # Built once so every draft call sends a byte-identical system prompt (prompt-cache hits).
        self.draft_system = build_draft_system(kb.articles if self.grounding == "full_context" else None)

    def find_orphaned(self) -> list[int]:
        """Tickets whose background processing was lost (server stopped or crashed after the 202 but
        before the pipeline finished). Background tasks live only in memory, so these would otherwise
        sit in received/processing forever.

        Call this at startup *before* the server accepts requests: in a single-process deployment no
        pipeline can be running yet, so every ticket in these states is orphaned, and no new ticket
        can be mistaken for one. With several workers this moves to a durable queue.
        """
        orphaned = self.db.ticket_ids_with_status(("received", "processing"))
        if orphaned:
            log.warning("orphaned_tickets_found", count=len(orphaned), ticket_ids=orphaned)
        return orphaned

    def reprocess_all(self, ticket_ids: list[int]) -> None:
        for ticket_id in ticket_ids:
            self.process(ticket_id)  # never raises: process() handles its own failures

    def process(self, ticket_id: int) -> None:
        """Background-task entry point: analyze a stored ticket and persist the result."""
        with structlog.contextvars.bound_contextvars(ticket_id=ticket_id):
            started = time.perf_counter()
            try:
                ticket = self.db.get_ticket(ticket_id)
                if ticket is None:
                    log.warning("ticket_not_found")
                    return
                self.db.update_ticket(ticket_id, status="processing", error=None)
                log.info("pipeline_started")
                result = self.analyze(ticket["subject"], ticket["body"], ticket_id)
            except Exception:
                log.exception("pipeline_crashed")
                result = Analysis(status="ai_unavailable", error="internal_error")

            elapsed = int((time.perf_counter() - started) * 1000)
            self.db.update_ticket(
                ticket_id,
                status=result.status,
                triage=result.triage.model_dump() if result.triage else None,
                draft=result.draft.model_dump() if result.draft else None,
                article_ids=[a.id for a in result.articles],
                flags=result.flags,
                review_reasons=result.review_reasons,
                error=result.error,
                pipeline_ms=elapsed,
            )
            log.info(
                "pipeline_finished", status=result.status, error=result.error,
                category=result.triage.category if result.triage else None,
                priority=result.triage.priority if result.triage else None,
                review_reasons=result.review_reasons, flags=result.flags, pipeline_ms=elapsed,
                grounding=self.grounding, kb_version=self.kb.version,
            )

    def analyze(self, subject: str, body: str, ticket_id: int | None = None, with_draft: bool = True) -> Analysis:
        subject, n_subject = redact_pii(subject)
        body, n_body = redact_pii(body)
        flags = ["pii_redacted"] if n_subject + n_body else []

        try:
            triage = run_triage(self.llm, self.settings, subject, body, ticket_id)
        except LLMError as exc:
            log.warning("triage_failed", kind=exc.kind)
            return Analysis(status="ai_unavailable", flags=flags, error=f"triage:{exc.kind}")

        retrieved: list[Article] | None = None  # None = full context: the model sees the whole KB
        if self.grounding == "bm25":
            query = f"{subject} {body} {triage.summary} {triage.category.replace('_', ' ')}"
            retrieved = self.kb.search(query, k=self.settings.retrieval_top_k)
            if not retrieved:
                flags.append("no_kb_match")

        draft, error = None, None
        if with_draft:
            try:
                draft = run_draft(
                    self.llm, self.settings, self.draft_system, subject, body, triage, retrieved, ticket_id
                )
            except LLMError as exc:
                log.warning("draft_failed", kind=exc.kind)
                flags.append("draft_unavailable")
                error = f"draft:{exc.kind}"

        if draft is not None:
            draft, citation_flags = validate_citations(draft, self.kb.ids)
            flags += citation_flags + detect_commitments(draft.reply)
            if self.grounding == "full_context" and not draft.cited_article_ids:
                flags.append("no_kb_citation")

        # Articles shown to the agent: what the draft actually relied on (full context), or what
        # retrieval found (bm25).
        if self.grounding == "full_context":
            articles = self.kb.get(draft.cited_article_ids) if draft else []
        else:
            articles = retrieved or []

        reasons = review_reasons(triage, flags, self.settings.confidence_threshold)
        return Analysis(
            status="needs_review" if reasons else "ready",
            triage=triage, draft=draft, articles=articles, flags=flags,
            review_reasons=reasons, error=error,
        )
