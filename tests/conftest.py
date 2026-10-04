from pathlib import Path

import pytest

from app.config import ROOT_DIR, Settings
from app.db import Database
from app.llm.client import LLMError
from app.models import DraftResult, Entity, TriageResult
from app.pipeline.retrieval import KnowledgeBase


class ScriptedClient:
    """LLMClient test double: returns (or raises) queued results per step, and records every call."""

    def __init__(self, triage=None, draft=None):
        self.responses = {"triage": triage, "draft": draft}
        self.calls: list[dict] = []

    def generate(self, *, step, model, system, user, schema, max_tokens, effort=None, ticket_id=None):
        self.calls.append({"step": step, "model": model, "user": user, "ticket_id": ticket_id})
        result = self.responses[step]
        if isinstance(result, Exception):
            raise result
        return result


def make_triage(**overrides) -> TriageResult:
    base = dict(
        category="billing", priority="P3", sentiment="neutral",
        summary="Customer asks why their invoice is higher than usual.",
        entities=[Entity(type="invoice_id", value="INV-20417")], risk_signals=[], confidence=0.9,
    )
    return TriageResult(**{**base, **overrides})


def make_draft(**overrides) -> DraftResult:
    base = dict(
        reply="Hi,\n\nMid-cycle seat changes are prorated, see our billing guide.\n\nBest regards,\nBrightdesk Support",
        cited_article_ids=["KB-005"], questions_for_customer=[],
    )
    return DraftResult(**{**base, **overrides})


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None, llm_mode="mock", database_path=tmp_path / "test.db",
        kb_dir=ROOT_DIR / "kb", log_level="WARNING",
    )


@pytest.fixture
def db(settings: Settings) -> Database:
    database = Database(settings.database_path)
    database.init_schema()
    return database


@pytest.fixture(scope="session")
def kb() -> KnowledgeBase:
    return KnowledgeBase.from_dir(ROOT_DIR / "kb")


__all__ = ["ScriptedClient", "make_triage", "make_draft", "LLMError"]
