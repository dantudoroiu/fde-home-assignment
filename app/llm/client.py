"""The only module that talks to a model.

`LLMClient.generate()` takes a prompt and a Pydantic schema and returns a validated instance of that
schema, or raises `LLMError` with a machine-readable `kind`. Callers never see SDK exceptions, raw text,
or partial output. Every attempt (successful or not) is reported to a `CallSink` for logging, cost
accounting, and the audit trail.
"""

import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Protocol, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from app.config import Settings, estimate_cost_usd
from app.llm.prompts import PROMPT_VERSION
from app.models import DraftResult, Entity, TriageResult
from app.observability import get_logger
from app.pipeline.retrieval import tokenize

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

# Models that accept the server-side refusal fallback in its "default" form.
FALLBACK_CAPABLE_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    """A model call that produced no usable output.

    kind is one of: timeout, rate_limit, connection, api_error, config, refusal, truncated,
    invalid_output.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind = kind


@dataclass
class CallRecord:
    ticket_id: int | None
    step: str
    model: str
    prompt_version: str
    attempt: int
    outcome: str  # "ok" or an LLMError kind
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None
    stop_reason: str | None = None
    error: str | None = None


CallSink = Callable[[CallRecord], None]


def log_sink(record: CallRecord) -> None:
    level = log.info if record.outcome == "ok" else log.warning
    level("llm_call", **asdict(record))


class LLMClient(Protocol):
    def generate(
        self,
        *,
        step: str,
        model: str,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int,
        effort: str | None = None,
        ticket_id: int | None = None,
    ) -> T: ...


class AnthropicClient:
    """Claude via the official SDK, using structured outputs plus local validation.

    The API is asked for JSON matching the schema, but constraints such as numeric ranges are not
    enforced server-side, so the response is always re-validated with Pydantic. A validation failure
    is retried once (fresh sample), then surfaced as `invalid_output`.
    """

    MAX_VALIDATION_ATTEMPTS = 2

    def __init__(self, settings: Settings, sink: CallSink = log_sink, sdk: Any = None):
        self._settings = settings
        self._sink = sink
        # Bounds in-flight API calls (including SDK retries) across all background-task threads.
        self._slots = threading.BoundedSemaphore(settings.llm_max_concurrency)
        self._sdk = sdk or anthropic.Anthropic(
            api_key=settings.anthropic_api_key or None,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    def generate(
        self,
        *,
        step: str,
        model: str,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int,
        effort: str | None = None,
        ticket_id: int | None = None,
    ) -> T:
        request = self._build_request(model, system, user, schema, max_tokens, effort)
        for attempt in range(1, self.MAX_VALIDATION_ATTEMPTS + 1):
            record = CallRecord(
                ticket_id=ticket_id, step=step, model=model, prompt_version=PROMPT_VERSION,
                attempt=attempt, outcome="ok", latency_ms=0,
            )
            try:
                # Wait for a free slot first, so latency_ms measures the API call, not the queue.
                with self._slots:
                    started = time.perf_counter()
                    response = self._sdk.beta.messages.create(**request)
            except Exception as exc:  # mapped to LLMError below; never leaks SDK types
                record.latency_ms = _elapsed_ms(started)
                error = _map_sdk_error(exc)
                record.outcome, record.error = error.kind, str(exc)[:500]
                self._sink(record)
                raise error from exc

            record.latency_ms = _elapsed_ms(started)
            self._apply_usage(record, response)

            # Check the stop reason before reading content: a refusal or truncation has no valid JSON.
            if response.stop_reason == "refusal":
                record.outcome = "refusal"
                self._sink(record)
                raise LLMError("refusal", "model declined the request")
            if response.stop_reason == "max_tokens":
                record.outcome = "truncated"
                self._sink(record)
                raise LLMError("truncated", f"hit max_tokens={max_tokens}")

            text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
            try:
                result = schema.model_validate_json(text)
            except ValidationError as exc:
                record.outcome, record.error = "invalid_output", f"{exc.error_count()} errors: {text[:300]}"
                self._sink(record)
                if attempt < self.MAX_VALIDATION_ATTEMPTS:
                    continue
                raise LLMError("invalid_output", "response failed schema validation") from exc

            self._sink(record)
            return result
        raise AssertionError("unreachable")

    def _build_request(
        self, model: str, system: str, user: str, schema: type[BaseModel], max_tokens: int, effort: str | None
    ) -> dict[str, Any]:
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)}
        }
        if effort:
            output_config["effort"] = effort
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            # The system prompt is static, so mark it cacheable. Prefixes shorter than the model's
            # minimum cacheable length are silently not cached; check cache_read_tokens in the logs.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }
        if self._settings.refusal_fallback and model in FALLBACK_CAPABLE_MODELS:
            request["betas"] = [FALLBACK_BETA]
            request["fallbacks"] = "default"
        return request

    @staticmethod
    def _apply_usage(record: CallRecord, response: Any) -> None:
        usage = response.usage
        record.model = getattr(response, "model", None) or record.model  # reflects a fallback model
        record.stop_reason = response.stop_reason
        record.input_tokens = usage.input_tokens or 0
        record.output_tokens = usage.output_tokens or 0
        record.cache_read_tokens = getattr(usage, "cache_read_input_tokens", None) or 0
        record.cache_write_tokens = getattr(usage, "cache_creation_input_tokens", None) or 0
        record.cost_usd = estimate_cost_usd(
            record.model, record.input_tokens, record.output_tokens,
            record.cache_read_tokens, record.cache_write_tokens,
        )


def _map_sdk_error(exc: Exception) -> LLMError:
    # Order matters: subclasses before their parents (timeout < connection, rate limit < status).
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMError("timeout", "request timed out after retries")
    if isinstance(exc, anthropic.RateLimitError):
        return LLMError("rate_limit", "rate limited after retries")
    if isinstance(
        exc,
        (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
         anthropic.BadRequestError, anthropic.NotFoundError),
    ):
        return LLMError("config", f"non-retryable request error: {exc}")
    if isinstance(exc, anthropic.APIStatusError):
        return LLMError("api_error", f"API returned {exc.status_code}")
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError("connection", "could not reach the API")
    return LLMError("api_error", f"unexpected error: {type(exc).__name__}")


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


class MockClient:
    """Deterministic, keyword-based stand-in for the model.

    Lets reviewers run the full app and UI without an API key. It is intentionally simple, not a
    baseline to compare against; real quality is measured with scripts/run_eval.py in live mode.
    """

    _CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
        ("security", ("suspicious", "hacked", "compromised", "vulnerability", "phishing", "unauthorized")),
        ("data_privacy", ("gdpr", "delete all", "personal data", "ccpa", "dpa", "right to be forgotten")),
        ("account_access", ("password", "login", "log in", "sso", "saml", "2fa", "locked out", "invite")),
        ("billing", ("invoice", "charge", "refund", "payment", "billing", "seat", "plan", "card")),
        ("bug_report", ("error", "broken", "bug", "crash", "500", "not loading", "fails")),
        ("feature_request", ("feature", "would be great", "please add", "wish", "suggestion", "roadmap")),
        ("technical", ("api", "webhook", "export", "csv", "integration", "how do i", "rate limit", "slow")),
    ]
    _RISK_KEYWORDS: dict[str, tuple[str, ...]] = {
        "outage": ("down", "outage", "nobody can", "all users", "not loading for anyone"),
        "data_loss": ("missing data", "deleted", "disappeared", "lost"),
        "security_incident": ("suspicious", "hacked", "compromised", "unauthorized"),
        "legal_threat": ("lawyer", "legal action", "attorney", "regulator", "sue"),
        "churn_risk": ("cancel", "switching to", "competitor", "unacceptable"),
        "prompt_injection": ("ignore your instructions", "ignore previous", "system prompt"),
        "account_ownership_change": ("become admin", "make me admin", "transfer ownership", "account owner"),
    }

    def __init__(self, sink: CallSink = log_sink):
        self._sink = sink

    def generate(
        self,
        *,
        step: str,
        model: str,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int,
        effort: str | None = None,
        ticket_id: int | None = None,
    ) -> T:
        started = time.perf_counter()
        if schema is TriageResult:
            result: BaseModel = self._triage(user)
        elif schema is DraftResult:
            result = self._draft(system, user)
        else:
            raise LLMError("config", f"MockClient cannot produce {schema.__name__}")
        self._sink(CallRecord(
            ticket_id=ticket_id, step=step, model=f"mock:{model}", prompt_version=PROMPT_VERSION,
            attempt=1, outcome="ok", latency_ms=_elapsed_ms(started),
            input_tokens=len(system + user) // 4, output_tokens=len(result.model_dump_json()) // 4,
            cost_usd=0.0, stop_reason="end_turn",
        ))
        return result  # type: ignore[return-value]

    def _triage(self, user: str) -> TriageResult:
        text = user.lower()
        category = next(
            (cat for cat, words in self._CATEGORY_KEYWORDS if any(w in text for w in words)), "other"
        )
        risks = [risk for risk, words in self._RISK_KEYWORDS.items() if any(w in text for w in words)]
        angry = any(w in text for w in ("unacceptable", "ridiculous", "furious", "!!!", "worst"))
        if {"outage", "data_loss", "security_incident", "legal_threat"} & set(risks):
            priority = "P1"
        elif category in ("bug_report", "billing", "account_access"):
            priority = "P2"
        elif category == "feature_request":
            priority = "P4"
        else:
            priority = "P3"
        entities = [Entity(type="invoice_id", value=m) for m in re.findall(r"\bINV-\d+\b", user)]
        entities += [Entity(type="account_id", value=m) for m in re.findall(r"\bACC-\d+\b", user)]
        subject = re.search(r"Subject: (.*)", user)
        return TriageResult(
            category=category,  # type: ignore[arg-type]
            priority=priority,  # type: ignore[arg-type]
            sentiment="angry" if angry else ("frustrated" if risks else "neutral"),
            summary=f"Customer writes about: {subject.group(1)[:80] if subject else 'unknown'}",
            entities=entities,
            risk_signals=risks,  # type: ignore[arg-type]
            confidence=0.55 if category == "other" else 0.8,
        )

    _ARTICLE_RE = re.compile(r'<article id="([^"]+)" title="([^"]+)">\n(.*?)\n</article>', re.S)

    @classmethod
    def _draft(cls, system: str, user: str) -> DraftResult:
        ticket_part = user.split("<ticket>", 1)[-1]
        # Articles arrive in the system prompt (full context) or the user turn (bm25). Cite the one
        # sharing the most words with the ticket, or nothing if none shares at least two.
        ticket_terms = set(tokenize(ticket_part))
        best: tuple[int, str, str] | None = None
        for aid, title, body in cls._ARTICLE_RE.findall(system + "\n" + user):
            shared = len(ticket_terms & set(tokenize(f"{title} {body}")))
            if shared >= 2 and (best is None or shared > best[0]):
                best = (shared, aid, title)
        if best:
            cited = [best[1]]
            pointer = f'Our article "{best[2]}" ({best[1]}) covers this.'
        else:
            cited = []
            pointer = "Our team is looking into this and will follow up shortly."
        subject = re.search(r"Subject: (.*)", ticket_part)
        reply = (
            "Hi there,\n\n"
            f"Thanks for reaching out about \"{subject.group(1) if subject else 'your request'}\". {pointer}\n\n"
            "If anything is still unclear, just reply to this email and we will help.\n\n"
            "Best regards,\nBrightdesk Support\n\n[mock draft: set LLM_MODE=live for real output]"
        )
        return DraftResult(reply=reply, cited_article_ids=cited, questions_for_customer=[])
