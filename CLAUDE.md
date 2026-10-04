# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository.

## What this is
An AI-assisted **support ticket triage** app for a fictional B2B SaaS support team ("Brightdesk").
Incoming tickets are classified and prioritized by Claude, matched to knowledge-base articles, and given a
grounded draft reply that a human agent accepts, edits, or rejects. Built as an interview home assignment:
a small, coherent end-to-end slice that favors depth over breadth.

## Commands
```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env                 # set ANTHROPIC_API_KEY, or LLM_MODE=mock
python -m uvicorn app.main:app --reload   # UI at http://localhost:8000
python scripts/seed.py                 # load data/sample_tickets.jsonl into the running app
python -m pytest                       # unit tests (no network, MockClient only)
# Always use `python -m ...`: Smart App Control on this machine blocks pip's unsigned uvicorn.exe/pytest.exe
python scripts/run_eval.py --triage-model claude-haiku-4-5   # live eval: accuracy / latency / cost
```

## Architecture map
- `app/main.py`: FastAPI app, lifespan, router wiring
- `app/config.py`: all settings (models, timeouts, prices, thresholds), env-overridable
- `app/models.py`: Pydantic schemas (Ticket, TriageResult, DraftResult, Feedback)
- `app/db.py`: SQLite repository (tickets, llm_calls, feedback)
- `app/llm/client.py`: `LLMClient` protocol, `AnthropicClient`, `MockClient`
- `app/llm/prompts.py`: versioned system prompts
- `app/pipeline/`: `triage` → `draft` → `guardrails`, run by `orchestrator`. Grounding defaults to the whole KB
  in the cached draft system prompt (`GROUNDING=full_context`); `retrieval.py` loads the KB and provides the BM25
  fallback (`GROUNDING=bm25`, or automatic when the KB exceeds `FULL_CONTEXT_MAX_TOKENS`)
- Keep the draft system prompt byte-stable (no per-request data, articles sorted) or prompt caching breaks
- `app/observability.py`: structlog setup and metrics aggregation
- `app/api/` (JSON API), `app/web/` + `app/templates/` (Jinja2 + HTMX agent UI)
- `kb/`: knowledge-base articles; `data/`: labeled sample tickets; `scripts/`: seed and eval

## Conventions (follow these)
- **All LLM calls go through `app/llm/client.py`.** Nothing else imports `anthropic`.
- **Every LLM output is validated against a Pydantic model.** On a validation failure, retry once, then degrade.
- **Routing and guardrails are deterministic Python** in `pipeline/guardrails.py`, not prompt instructions.
- **Drafts are never auto-sent.** A human always makes the final decision.
- **AI is never on the critical path.** If an LLM step fails, the ticket lands in the queue as `ai_unavailable`.
- **Log every LLM call** with model, latency_ms, token usage (incl. cache), cost_usd, stop_reason, prompt_version.
- Ticket text is untrusted data: wrap it in delimiters and never let it change instructions.
- Bump `PROMPT_VERSION` in `prompts.py` whenever a prompt changes.
- Keep it simple: stdlib `sqlite3`, no ORM, no extra frameworks unless clearly justified.

## Models
- Triage: `claude-haiku-4-5` (fast, cheap, high volume)
- Draft: `claude-sonnet-5-5` (better writing and grounding)
- Both are overridable via env (`TRIAGE_MODEL`, `DRAFT_MODEL`). Use exact IDs with no date suffixes.

## Testing rules
- Unit tests use `MockClient` only. No network and no API key needed.
- Live-model quality is measured by `scripts/run_eval.py`, never by pytest. It costs money and is non-deterministic.
- Each new failure-mode handler gets a test in `tests/test_orchestrator.py` or `tests/test_guardrails.py`.

## Out of scope (do not build unless asked)
Auth/multi-tenancy, real helpdesk integration (Zendesk etc.), vector DB/embeddings, sending emails,
fine-tuning, full PII/DLP, production deployment. These are documented in the README as extensions.
