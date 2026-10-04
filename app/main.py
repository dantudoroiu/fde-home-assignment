import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request

from app.api.routes import router as api_router
from app.config import Settings, get_settings
from app.db import Database
from app.llm.client import AnthropicClient, CallRecord, LLMClient, MockClient, log_sink
from app.observability import configure_logging, get_logger
from app.pipeline.orchestrator import Pipeline
from app.pipeline.retrieval import KnowledgeBase
from app.web.routes import router as web_router

log = get_logger(__name__)


def build_llm(settings: Settings, db: Database) -> LLMClient:
    def sink(record: CallRecord) -> None:
        log_sink(record)
        db.record_llm_call(record.__dict__)

    if settings.llm_mode == "live":
        if not settings.anthropic_api_key:
            raise RuntimeError("LLM_MODE=live requires ANTHROPIC_API_KEY (or use LLM_MODE=mock)")
        return AnthropicClient(settings, sink=sink)
    return MockClient(sink=sink)


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_path)
        db.init_schema()
        kb = KnowledgeBase.from_dir(settings.kb_dir)
        app.state.settings = settings
        app.state.db = db
        app.state.pipeline = Pipeline(db, llm or build_llm(settings, db), kb, settings)
        log.info(
            "app_started", llm_mode=settings.llm_mode, triage_model=settings.triage_model,
            draft_model=settings.draft_model, kb_articles=len(kb.articles),
        )
        yield

    app = FastAPI(title="Brightdesk Ticket Triage", lifespan=lifespan)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    app.include_router(api_router)
    app.include_router(web_router)
    return app


app = create_app()
