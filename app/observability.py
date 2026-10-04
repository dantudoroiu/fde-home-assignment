"""Structured logging and metrics.

Logs are JSON lines (one event per line) so they can be shipped to any log backend as-is. Request and
ticket IDs are bound via contextvars, so every line in a request or pipeline run can be correlated.

Metrics are computed from the database rather than in-process counters: they survive restarts and
there is a single source of truth. At higher volume this would move to Prometheus/OpenTelemetry.
"""

import logging
import statistics
import sys
from typing import Any

import structlog

from app.db import Database


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    return round(statistics.quantiles(values, n=100, method="inclusive")[int(pct) - 1], 1)


def compute_metrics(db: Database) -> dict[str, Any]:
    calls = db.list_llm_calls()
    feedback = db.list_feedback()
    status_counts = db.status_counts()

    by_step: dict[str, dict[str, Any]] = {}
    for step in sorted({c["step"] for c in calls}):
        step_calls = [c for c in calls if c["step"] == step]
        ok = [c for c in step_calls if c["outcome"] == "ok"]
        outcomes: dict[str, int] = {}
        for c in step_calls:
            outcomes[c["outcome"]] = outcomes.get(c["outcome"], 0) + 1
        latencies = [c["latency_ms"] for c in ok]
        by_step[step] = {
            "calls": len(step_calls),
            "outcomes": outcomes,
            "latency_ms_p50": percentile(latencies, 50),
            "latency_ms_p95": percentile(latencies, 95),
            "avg_input_tokens": _avg([c["input_tokens"] for c in ok]),
            "avg_output_tokens": _avg([c["output_tokens"] for c in ok]),
            "cache_read_tokens": sum(c["cache_read_tokens"] for c in step_calls),
            "cost_usd": round(sum(c["cost_usd"] or 0 for c in step_calls), 4),
        }

    total_cost = sum(c["cost_usd"] or 0 for c in calls)
    processed = sum(n for s, n in status_counts.items() if s not in ("received", "processing"))
    pipeline_ms = db.pipeline_latencies_ms()

    actions: dict[str, int] = {}
    for f in feedback:
        actions[f["action"]] = actions.get(f["action"], 0) + 1
    category_agreement = [
        f["corrected_category"] in (None, f["ai_category"]) for f in feedback if f["ai_category"]
    ]
    priority_agreement = [
        f["corrected_priority"] in (None, f["ai_priority"]) for f in feedback if f["ai_priority"]
    ]

    return {
        "tickets": {"total": sum(status_counts.values()), "by_status": status_counts},
        "ai_unavailable_rate": _ratio(status_counts.get("ai_unavailable", 0), processed),
        "pipeline_latency_ms": {
            "p50": percentile(pipeline_ms, 50),
            "p95": percentile(pipeline_ms, 95),
        },
        "llm": {
            "total_calls": len(calls),
            "total_cost_usd": round(total_cost, 4),
            "cost_per_processed_ticket_usd": _ratio(total_cost, processed, digits=5),
            "by_step": by_step,
        },
        "agent_feedback": {
            "total": len(feedback),
            "actions": actions,
            "draft_accept_rate": _ratio(actions.get("accepted", 0), len(feedback)),
            "category_agreement": _ratio(sum(category_agreement), len(category_agreement)),
            "priority_agreement": _ratio(sum(priority_agreement), len(priority_agreement)),
            "avg_edit_ratio": _avg([f["edit_ratio"] for f in feedback if f["edit_ratio"] is not None]),
        },
    }


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _ratio(num: float, den: float, digits: int = 3) -> float | None:
    return round(num / den, digits) if den else None
