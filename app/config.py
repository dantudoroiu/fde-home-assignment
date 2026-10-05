"""Application settings. Everything tunable lives here and can be overridden via env or .env."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent

# USD per million tokens: (input, output). Cache writes bill at 1.25x input, cache reads at 0.1x input.
# Source: Anthropic public pricing (first-party API). Update when prices change.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    # "mock" runs the whole app without an API key using a deterministic fake model.
    llm_mode: Literal["live", "mock"] = "mock"
    anthropic_api_key: str | None = None

    triage_model: str = "claude-haiku-4-5"
    triage_max_tokens: int = 1024
    triage_effort: str | None = None  # Haiku 4.5 does not accept the effort parameter

    draft_model: str = "claude-sonnet-5-5"
    draft_max_tokens: int = 4096  # includes adaptive-thinking tokens on Sonnet 5.5
    draft_effort: str | None = "low"  # short support replies don't benefit from deep reasoning

    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2  # SDK-level retries for 408/409/429/5xx and connection errors
    # Cap on simultaneous API calls per process. Background tasks run in a threadpool, so a burst of
    # tickets would otherwise fire dozens of calls at once and hit the org's concurrency limit (429).
    llm_max_concurrency: int = 4
    refusal_fallback: bool = True  # server-side refusal fallback on models that support it

    database_path: Path = ROOT_DIR / "triage.db"
    kb_dir: Path = ROOT_DIR / "kb"
    log_level: str = "INFO"

    # full_context: the whole KB sits in the (cached) draft system prompt; right for small KBs.
    # bm25: keyword retrieval of the top-k articles per ticket; the scaling path for large KBs.
    grounding: Literal["full_context", "bm25"] = "full_context"
    full_context_max_tokens: int = 50_000  # above this the app falls back to bm25 at startup
    retrieval_top_k: int = 3
    confidence_threshold: float = 0.7


@lru_cache
def get_settings() -> Settings:
    return Settings()


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Cost of one call from its usage numbers. Returns None for models without a known price."""
    # The API may echo a dated snapshot ID (claude-haiku-4-5-20251001); price it as the base model.
    prices = MODEL_PRICES.get(model) or MODEL_PRICES.get(re.sub(r"-\d{8}$", "", model))
    if prices is None:
        return None
    input_price, output_price = prices
    total = (
        input_tokens * input_price
        + cache_write_tokens * input_price * CACHE_WRITE_MULTIPLIER
        + cache_read_tokens * input_price * CACHE_READ_MULTIPLIER
        + output_tokens * output_price
    )
    return round(total / 1_000_000, 6)
