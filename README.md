# Brightdesk Ticket Triage: AI-assisted support workflow

[![tests](https://github.com/dantudoroiu/fde-home-assignment/actions/workflows/tests.yml/badge.svg)](https://github.com/dantudoroiu/fde-home-assignment/actions/workflows/tests.yml)

A small, end-to-end application that helps a non-technical **customer support team** handle incoming
tickets faster and more safely.

For every new ticket, Claude:
- sets **category, priority, sentiment and risk signals**
- finds the relevant **knowledge-base articles**
- writes a **grounded draft reply**

Deterministic rules then decide whether a human must review the ticket before replying. The agent
always has the final say: they accept, edit or reject the draft, and that feedback becomes the quality
signal.

> Customer context and value: [docs/CUSTOMER_CONTEXT.md](docs/CUSTOMER_CONTEXT.md) · Design note and diagrams: [docs/DESIGN.md](docs/DESIGN.md)

---

## Quick start

Requirements: Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
copy .env.example .env                 # macOS/Linux: cp .env.example .env
```

> **Windows notes:** if `Activate.ps1` is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
> once, or skip activation and use `.\.venv\Scripts\python` instead of `python`. Commands use
> `python -m uvicorn` / `python -m pytest` because Smart App Control can block the unsigned
> `uvicorn.exe` / `pytest.exe` launchers that pip generates. Clone into a short path (e.g. `C:\dev\`):
> the Anthropic SDK has deeply nested files, and `pip install` fails partway (leaving a broken install)
> when paths exceed Windows' 260-character limit, unless long paths are enabled.

**Option A: no API key (mock mode, the default).** A deterministic keyword-based fake model stands in
for Claude, so the whole workflow and UI can be explored offline.

```powershell
python -m uvicorn app.main:app --reload
python scripts/seed.py                 # in a second terminal: submits 40 sample tickets
```

**Option B: with Claude.** In `.env`, set `LLM_MODE=live` and `ANTHROPIC_API_KEY=sk-ant-...`, then run
the same commands.

Open http://localhost:8000:
- **Queue:** tickets needing review come first, then by AI priority. It refreshes live.
- **Ticket page:** the customer message, AI triage, review reasons, KB matches, an editable draft,
  and the LLM calls with tokens, latency and cost.
- **New ticket:** submit your own ticket, for example an angry outage report or a prompt-injection
  attempt.
- **Metrics:** status counts, latency, cost per ticket and agent feedback rates.

Other commands:

```powershell
python -m pytest                              # 78 tests, no network, a few seconds
python scripts/run_eval.py                    # accuracy / grounding / latency / cost on the labeled set (live: ~$0.30 per run)
python scripts/run_eval.py --grounding bm25   # compare grounding strategies on the same tickets
python scripts/run_eval.py --triage-model claude-sonnet-5-5 --no-draft   # compare triage models
```

API: `POST /api/tickets`, `GET /api/tickets[?status=]`, `GET /api/tickets/{id}`,
`POST /api/tickets/{id}/feedback`, `POST /api/tickets/{id}/reprocess`, `GET /api/metrics`,
`GET /healthz`. Interactive docs are at `/docs`.

---

## How it works

```
ticket ─► redact PII ─► triage (Haiku) ─► draft (Sonnet + whole KB, cached) ─► guardrails ─► queue
            │                │                          │                         │
            │                └──── LLM client: structured output, local validation, retry, ┘
            │                      timeout/retries, cost + token logging, audit table
            └─ the model never sees card numbers/passwords; the agent still sees the original
```

1. **Intake.** `POST /api/tickets` stores the ticket and returns `202` immediately. The pipeline runs as
   a background task, so intake never waits on the model.
2. **Triage** (`claude-haiku-4-5`) returns a validated `TriageResult`:
   - category, priority P1–P4, sentiment, summary
   - extracted entities
   - risk signals (`outage`, `data_loss`, `security_incident`, `legal_threat`, `churn_risk`,
     `prompt_injection`)
   - confidence
3. **Grounding.** The whole knowledge base (`kb/*.md`, 13 articles, ~3.5k tokens) is part of the
   draft system prompt. It is built once at startup and prompt-cached, so there is no retrieval step to
   miss the right article. If the KB grows past `FULL_CONTEXT_MAX_TOKENS`, the app falls back to BM25
   retrieval of the top 3 articles (`GROUNDING=bm25`). See the decisions log for BM25 vs. full context
   vs. an agent.
4. **Draft** (`claude-sonnet-5-5`, low effort) returns a validated `DraftResult`: reply text, cited
   article IDs, and questions still to ask the customer. The ticket page shows the articles the draft
   actually cited.
5. **Guardrails** (plain Python, [app/pipeline/guardrails.py](app/pipeline/guardrails.py)):
   - strip citations to articles that don't exist
   - flag replies that mention refunds, credits or fix dates
   - compute **review reasons**: P1, angry customer, security or privacy category, any risk signal,
     confidence below 0.7, failed draft, a draft citing no article (except feature requests and praise,
     where a reply makes no factual claims)
   - any reason makes the ticket `needs_review`; otherwise it is `ready`
6. **Agent UI.** The agent edits and sends, or rejects the draft and writes their own reply, and can
   correct the category and priority. The feedback is stored with an `edit_ratio`.

Architecture, lifecycle and sequence diagrams, plus a decisions log, are in [docs/DESIGN.md](docs/DESIGN.md).

### Project layout
```
app/
  main.py              FastAPI app factory, lifespan, request-id middleware
  config.py            settings (env-overridable), model price table, cost function
  models.py            Pydantic schemas: LLM contracts + API payloads
  db.py                SQLite repository (tickets, llm_calls, feedback)
  observability.py     structlog JSON logging, metrics from DB
  llm/client.py        LLMClient protocol, AnthropicClient, MockClient   ← only place that calls a model
  llm/circuit_breaker.py   fails calls fast after repeated API outages
  llm/prompts.py       versioned system prompts
  pipeline/            triage, draft, guardrails, orchestrator; retrieval.py = KB loading + BM25 fallback
  api/routes.py        JSON API        web/routes.py + templates/   agent UI (Jinja2 + HTMX)
kb/                    knowledge-base articles (fictional)
data/sample_tickets.jsonl   40 labeled tickets (seed data + eval gold set)
data/holdout_tickets.jsonl  12 held-out labeled tickets (never used for prompt tuning)
scripts/               seed.py, run_eval.py, rescore_eval.py (re-score saved runs, no API calls)
tests/                 pytest suite (MockClient / fake SDK only)
```

---

## Assumptions

- The customer is a mid-size B2B SaaS support team: about 12 agents and about 600 tickets/day, in
  English plus some other languages. Details are in [CUSTOMER_CONTEXT.md](docs/CUSTOMER_CONTEXT.md).
- Tickets arrive from an existing helpdesk (Zendesk, Freshdesk and so on). This app is the AI layer
  behind a webhook, and the built-in UI stands in for the helpdesk sidebar. Nothing is emailed.
- The knowledge base is small and curated. Articles are trusted, ticket text is not.
- Agents remain accountable for every reply. **No auto-send**; the design does not aim at full
  automation.
- Sending ticket text to the Anthropic API is acceptable under the customer's data-processing terms,
  after redacting card numbers and credentials.
- Ticket bodies are capped at 8,000 characters. Anything longer is rejected with a clear error rather
  than silently truncated.

---

## Trade-offs: latency, cost, accuracy

### Model choice
| Step | Model | Why |
|---|---|---|
| Triage | `claude-haiku-4-5` | Runs on every ticket. Short structured classification, where speed and cost matter most. |
| Draft | `claude-sonnet-5-5`, `effort=low` | Customer-facing text that needs grounding and tone. Low effort keeps latency down, because short replies gain little from deep reasoning. |

Both are env settings (`TRIAGE_MODEL`, `DRAFT_MODEL`, `DRAFT_EFFORT`). The point of `run_eval.py` is to
make this choice with data: run it with different models and compare accuracy, routing recall,
latency and cost on the same labeled set.

### Cost (per ticket)
The default row is **measured** by `run_eval.py` on the 40 sample tickets: triage averages about 1,450
input and 65 output tokens; drafting reads about 4.5k cached KB tokens plus about 135 new input tokens,
and writes about 380 output tokens. The other rows are derived from those token counts at list prices.

| Configuration | $ / ticket | $ / day at 600 tickets | $ / month |
|---|---|---|---|
| Haiku triage + Sonnet draft, full KB cached (default, measured) | $0.0068 | ~$4 | ~$120 |
| Same, BM25 retrieval instead of full KB (measured) | $0.0076 | ~$4.50 | ~$135 |
| Sonnet for both (derived) | ~$0.0085 | ~$5 | ~$150 |
| Opus 5.5 for both (derived) | ~$0.017 | ~$10 | ~$300 |

**Takeaway for the customer:** at this volume model cost is negligible next to agent time (about 18
agent-hours a day saved, see the customer note). So **accuracy and escalation recall should drive the
model choice, not price.** Cost becomes the deciding factor only at much higher volumes, or when
re-processing backlogs, where the Batch API halves the price.

**Prompt caching** is why the full-KB prompt is *cheaper* than retrieval despite being longer. The draft
system prompt (rules + whole KB) is byte-identical on every call, so after the first call it is read
from cache at 10% of the input price. The measured draft cache hit rate was 100%. The default cache
lifetime is 5 minutes, and at about one ticket per minute during business hours it stays warm; the
first ticket after a quiet period pays a one-off cache write (1.25×). The triage prompt is shorter
than the minimum cacheable length, so it is not cached (and is cheap anyway on Haiku).

### Latency
- Triage on Haiku is typically 1–3 s. Drafting on Sonnet at low effort is a few seconds. The pipeline
  total is about 3–8 s, measured as p50/p95 on `/metrics`.
- That is fine for an asynchronous queue, because agents rarely open a ticket within seconds of
  arrival, and intake itself returns in milliseconds.
- If latency mattered more:
  - stream the draft into the UI
  - run triage and drafting in parallel (the draft uses the triage only as a hint)
  - start drafting only when an agent opens the ticket, which also saves cost on tickets closed
    without a reply

### Accuracy
- **Grounding:** drafts must cite the articles they rely on; unknown IDs are stripped and flagged,
  and a draft citing nothing goes to review.
- **Calibration:** confidence below 0.7 routes to review. The threshold is a config value, tuned from
  eval and shadow-mode data.
- **Recall over precision** in routing, because a missed P1 costs far more than an extra review.
- **Feedback loop:** category and priority corrections plus `edit_ratio` give continuous production
  accuracy metrics, not just offline evals.

### Eval results
Live runs on the 40 labeled tickets, plus the mock client as a "keyword rules" baseline. The
grounding comparison ran on prompt `2026-10-04.1`; the **last row is the configuration the app ships
with** (prompt `2026-10-05.2`, after the priority fix described below). Every live run quoted in this README is committed in
[docs/eval_runs/](docs/eval_runs/), and `python scripts/rescore_eval.py docs/eval_runs/*.json`
reproduces the numbers from the saved predictions, with no API key needed.

| Configuration | Category acc. | Priority acc. | Review recall | Review precision | Grounding recall | Citation precision | Draft p50 / p95 | $ / ticket |
|---|---|---|---|---|---|---|---|---|
| Keyword rules (mock mode) | 0.70 | 0.45 | 0.82 | 0.64 | n/a | n/a | n/a | $0 |
| Haiku + Sonnet, **full KB** (default) | 0.78 | 0.70 | 0.95 | 1.00 | **1.00** | 0.73 | 4.3 / 5.5 s | **$0.0068** |
| Haiku + Sonnet, BM25 top 3 | 0.80 | 0.65 | 0.89 | 0.89 | 0.94 | **0.84** | 4.3 / 5.2 s | $0.0076 |
| **Shipped:** full KB, prompt `2026-10-05.2` | 0.80 | **0.78** | **1.00** | 0.90 | 0.97 | 0.80 | 4.4 / 6.0 s | $0.0071 |

- Review metrics use the reviewed labels. Label review changed two tickets to `should_review: true`
  (T33, a cancellation; T35, an unexplained server error), and the metrics were recomputed from the
  saved predictions in [docs/eval_runs/](docs/eval_runs/).
- *Grounding recall:* the draft cited an expected article. *Citation precision:* the share of cited
  articles that were expected.
- How to read it:
  - **Full KB never missed the right article.** BM25 missed exactly the vocabulary-gap cases: T37
    "admin left the company" and T38 with two issues.
  - **Full KB is also cheaper,** thanks to caching.
  - **Its weakness is over-citing.** Seeing everything, it adds secondary articles, for example the
    invoice article next to the refund policy, and cites something on 3 of 8 tickets where no article
    really applies. BM25's pre-filter gives higher precision. Some of those "misses" are debatable
    labels: citing webhooks for a Slack-alerts feature request is a reasonable workaround.
- **Triage is identical code in both runs.** The 2-point difference in category accuracy is
  run-to-run noise, so differences that small on 40 tickets are not meaningful.
- The keyword baseline gets obvious cases right, but misses mixed, vague and non-English tickets, and
  can't write replies.

### Prompt iteration: fixing priority over-rating (measured on a held-out set)
Two runs of prompt `2026-10-04.1` showed the same bias: **every** priority error was one level too
high, mostly P3 → P2, so the cause was the rubric, not noise. Tickets T37 "our admin left, make me
admin" (possible social engineering) also slipped through review because nothing in the rules covered
it.

Changes in prompt `2026-10-05.2`:
- **Priority rubric rewritten** around business impact:
  - P3 is the default, and how-to questions stay P3 even when the user is blocked
  - urgency words don't raise priority
  - P2 means several users blocked or an incorrect charge
  - P1 explicitly includes a whole company locked out and any *suspected* compromise
- **New risk signal `account_ownership_change`,** which always forces review.

To avoid overfitting the 40 tickets, 12 **new held-out tickets** (`data/holdout_tickets.jsonl`) were
written and measured with the old prompt *before* changing it, and are never used for tuning.

| | Sample set (40), before → after | **Held-out set (12), before → after** |
|---|---|---|
| Priority accuracy | 0.60 → 0.78 | **0.58 → 0.92** |
| Category accuracy | 0.80 → 0.80 | 0.67 → 0.92 |
| Review recall | 0.95 → **1.00** | 0.67 → 0.83 |
| Review precision | 0.95 → 0.90 | 1.00 → 1.00 |
| $ / ticket | $0.0076 → $0.0071 | $0.0089 → $0.0072 |

- **It went wrong once, and that was caught.** The first version of the rubric (`2026-10-05.1`) dropped
  two real P1s, a company-wide SSO lockout and a suspicious login, to P2 because of the "when torn,
  choose lower" rule. `2026-10-05.2` limits that rule to P2-vs-P3 and spells out P1.
- **Remaining priority errors on risky tickets now err upward** (a vulnerability report or a GDPR
  request rated P1), which is the safe direction for queue ordering.
- **Still missed: H05, a disputed invoice ("charged for 48 seats, we have 30").** It's money owed, but
  the draft didn't promise a refund, so no rule fired. The natural fix is a `billing_dispute` risk
  signal, deliberately left for a separate, measured iteration.

---

## Failure modes and how they're handled

| Failure | Handling | Where |
|---|---|---|
| API timeout, 429, 5xx, connection error | SDK retries (`LLM_MAX_RETRIES`, default 2) with an explicit timeout, then mapped to a typed `LLMError` | `llm/client.py` |
| Anthropic outage (many failures in a row) | **Circuit breaker:** after 5 consecutive availability failures (timeout, 429, 5xx, connection) it opens, and calls fail immediately as `circuit_open` for 60 s. Then one trial call probes: success closes it, failure re-opens it. Bad answers (invalid JSON, refusals) don't count, because they prove the API is up. Measured in a simulated outage: tickets arriving while it's open land in `ai_unavailable` in **~9 ms instead of 7–22 s**. State is shown on `/healthz` | `llm/circuit_breaker.py`, `llm/client.py` |
| Burst of tickets exceeds the org's concurrency limit | At most `LLM_MAX_CONCURRENCY` (default 4) calls in flight; the rest wait for a slot. Found when seeding 40 tickets on a new account: 4 tickets hit 429 *"concurrent requests exceeded"* and correctly landed in `ai_unavailable`, and they recovered with "Retry AI" after the fix | `llm/client.py` |
| Triage fails | Ticket goes to `ai_unavailable` and is handled manually exactly as before. "Retry AI" button. | `orchestrator.py` |
| Draft fails | Triage is kept (the queue is still sorted); `needs_review` with `draft_unavailable` | `orchestrator.py` |
| Output fails schema or constraints | One fresh retry, then `invalid_output` (degrades as above) | `llm/client.py` |
| Model refuses / output truncated | `stop_reason` checked before parsing → `refusal` / `truncated`; a server-side refusal fallback is enabled on Sonnet | `llm/client.py` |
| Prompt injection in the ticket | Ticket wrapped in `<ticket>` tags as untrusted data; model has no tools or actions; triage reports `prompt_injection` → forced review; drafts never auto-sent | prompts, guardrails |
| Hallucinated KB citations | Stripped against the KB index and flagged for review | `guardrails.py` |
| Promises of refunds, credits or dates | Regex flag → forced review (the prompt also forbids them) | `guardrails.py` |
| Card numbers or passwords in the ticket | Luhn-checked card and credential redaction before the model call | `guardrails.py` |
| Bug in the pipeline | Caught at the top level; the ticket is never stuck in `processing` | `orchestrator.py` |
| Server crashes or restarts after the `202` but before the pipeline finishes | The ticket is already saved, but the background task is lost (it lives only in memory). At startup, tickets left in `received`/`processing` are found *before* requests are accepted (so a new ticket can't be processed twice) and re-run in a background thread; a warning `orphaned_tickets_found` is logged | `main.py`, `orchestrator.py` |
| Empty or oversized input | `422` with a clear message; no silent truncation | `models.py` |
| Bad API key or model name | Mapped to `config` (non-retryable), visible in logs; app refuses to start in live mode without a key | `client.py`, `main.py` |

---

## Observability

- **Structured JSON logs** (structlog) with `request_id` (also returned as the `x-request-id` header)
  and `ticket_id` on every line of a request or pipeline run.
- **Every LLM attempt** is logged and stored in `llm_calls` with:
  - step, model, prompt version, attempt number
  - outcome, latency, input/output/cache tokens
  - cost in USD, stop reason, error
- Answering "why did ticket 123 get this draft, and what did it cost?" is a single query.
- **`/api/metrics`** (and the `/metrics` page):
  - tickets by status and AI-unavailable rate
  - pipeline p50/p95; per-step call outcomes, p50/p95 latency, tokens and cost
  - cost per processed ticket
  - agent accept/edit/reject rates, average edit ratio, category and priority agreement
- **`/healthz`** reports DB reachability, LLM mode and the circuit breaker state (`closed` / `open` / `half_open`).

In production I would add: OpenTelemetry traces spanning webhook → pipeline → API call; alerts on
AI-unavailable rate, p95 latency, cost per day and a drop in acceptance rate; and a dashboard broken
down by `prompt_version`.

---

## Performance and scalability

**Bottlenecks, in order:**
1. **LLM latency and rate limits.** The pipeline time is about 95% model calls.
2. **The in-process background tasks.** They are lost on restart, and concurrency is bounded by one
   process's threadpool.
3. **SQLite writes.** Fine at hundreds of tickets per day, but a single writer.

**Measurement:** per-step latency, tokens and cost for every call are already recorded, so bottlenecks
show up on `/metrics` without extra tooling. `run_eval.py` gives repeatable latency and cost numbers
per configuration. Next I would load-test intake (locust) separately from the model path, since
intake is cheap and the model is the real limit.

**Scaling path:**
- **Queue and workers:** a queue (SQS, Redis, Celery) with a worker pool sized to the Anthropic rate
  limits. Retries with backoff and a dead-letter queue replace in-process background tasks. Today's
  per-process cap (`LLM_MAX_CONCURRENCY`) doesn't coordinate across processes, so with several API
  instances the limit has to move to the queue: total workers × calls per worker ≤ the org limit.
- **Database:** Postgres instead of SQLite. The repository layer is the only code that changes.
- **API layer:** stateless and horizontally scalable.
- **Backlogs:** the **Message Batches API** for backlogs and nightly re-triage after a prompt change,
  at 50% cost with no latency requirement.
- **Cost controls:** lazy drafting (only when an agent opens the ticket), cache the KB in the system
  prompt, and route trivial categories to cheaper paths.
- **At 10× volume** (about 6,000/day) this is still only about 4 requests a minute on average. The
  design limit is burst handling, which the queue absorbs, not raw throughput.

---

## Testing

`pytest` runs 78 tests in about 3 s with **no network access**, locally and on every push via GitHub
Actions ([.github/workflows/tests.yml](.github/workflows/tests.yml)):

| File | What it proves |
|---|---|
| `test_guardrails.py` | routing rules, citation stripping, commitment detection ("credit card" is not a promise of credit), PII redaction (incl. a Luhn false-positive guard) |
| `test_orchestrator.py` | happy path; triage failure → `ai_unavailable`; draft failure keeps triage; unexpected exception never leaves a ticket stuck; the model never sees redacted PII; ticket text is delimited; full-context vs. BM25 grounding paths; an ungrounded draft goes to review; an oversized KB falls back to BM25 |
| `test_llm_client.py` | the real `AnthropicClient` against a fake SDK: circuit breaker stops API calls after repeated outages, including calls already queued during a burst, and isn't tripped by bad answers; invalid output retried once then fails, refusal and truncation never parsed, each SDK error mapped to the right kind, cost computed from usage, refusal fallback only on Sonnet |
| `test_prompts.py` | the draft system prompt contains the whole KB and is byte-identical regardless of article order (prompt-cache stability); in full-context mode the user turn carries no articles |
| `test_circuit_breaker.py` | opens after N consecutive failures, a success resets the count, exactly one trial call after the cooldown, the trial closes or re-opens it (fake clock, no waiting) |
| `test_retrieval.py` | the expected KB article appears in the top 3 for representative queries; unrelated or off-topic queries return nothing (regression test for generic words like "new"/"customer" matching articles) |
| `test_api.py` | full lifecycle through HTTP (create → triage → feedback → metrics), outage still accepts tickets and stays in the AI-unavailable rate after an agent replies manually (a manual reply isn't counted as an accepted draft), input validation, feedback recorded only once per ticket, a finished ticket can't be re-run, crash recovery at startup, UI pages render |

Model *quality* is deliberately not tested in pytest, because it is non-deterministic and costs money.
It is measured by `scripts/run_eval.py` against the 40 labeled tickets in `data/sample_tickets.jsonl`.
These include edge cases: prompt injection, Spanish, a vague one-liner, mixed issues, legal threats, a
leaked API key, a card number and a password.

---

## Known limitations

- **Small, synthetic eval set** (40 tickets). Good enough to compare configurations, but not
  statistically strong. Real shadow-mode data is the next step.
- **PII redaction is narrow:** card numbers and obvious credentials only. Names, addresses and free-form
  secrets reach the model. This is not a DLP solution.
- **The confidence score is self-reported** by the model and only roughly calibrated. The 0.7 threshold
  should be tuned on real data.
- **Background tasks are in-process:** work is lost on a crash and only recovered at the *next startup*.
  The recovery assumes a single server process: with several workers, one worker's startup would
  re-run tickets another worker is still processing. In production a durable queue (SQS, Redis) with
  acknowledgements replaces both the background tasks and this recovery step.
- **No auth, no multi-tenancy, no rate limiting** on the API.
- **Full-context grounding has a size ceiling:** past roughly 100 articles, or the
  `FULL_CONTEXT_MAX_TOKENS` budget, the prompt gets expensive on cache misses and the model's attention
  is spread across more irrelevant text. The app then falls back to BM25, which has its own weakness
  (next point).
- **Drafts over-cite in full-context mode** (73% citation precision). This is harmless to the
  customer, because citations are for the agent, but it makes "Articles used by the draft" noisier.
- **BM25 misses synonyms** (fallback mode only): a ticket that shares no vocabulary with the right article won't retrieve
  it. The draft then says the team will follow up, and `no_kb_match` forces review.
- **English-centric prompts and KB:** non-English tickets are triaged and answered in their language,
  but the KB is English only.
- **Commitment detection is a regex:** it can over-flag, for example "no refund is possible", which is
  the safe direction.

## Intentionally left out (and why)

| Left out | Why | How I'd add it |
|---|---|---|
| Real helpdesk integration | Not needed to show the workflow; the API is shaped like a webhook target | Zendesk trigger → `POST /api/tickets`; write the draft back as an internal note |
| Embeddings / vector DB | Not needed: the whole KB fits in a cached prompt | Hybrid BM25 + embeddings once the KB outgrows full context |
| Agent with tools | No benefit for 13 articles (it would just read them all), and it costs latency and testability | The real win is **customer-data tools**: `get_account_status`, `get_invoice`, the status page. Ticket T27 "workspace read-only" can only be answered well by checking the account's payment status, and no article can do that. |
| Auto-send | Trust has to be earned with data first | Per-category allowlist gated on measured acceptance rate |
| Streaming drafts | Async queue UX doesn't need it | `messages.stream` + SSE to the ticket page |
| Auth, multi-tenancy, deployment | Not what this assignment evaluates | SSO via the helpdesk, a tenant column, a container on a managed platform |
| Learning from feedback | Needs volume first | Use edited replies as few-shot examples and suggestions for KB gaps |

---

## Use of AI in this project

**Where AI was used** (Claude Code, with a project [CLAUDE.md](CLAUDE.md) that sets the conventions):
- **Planning:** turning the brief into an implementation plan, and pressure-testing the design (failure
  modes, observability, scaling questions).
- **Scaffolding and implementation:** the FastAPI app, LLM client, pipeline, templates and scripts were
  generated with AI and reviewed by me. SDK usage, such as structured outputs and error classes, was
  checked against the installed SDK rather than taken from memory.
- **Test generation:** test cases were drafted by AI from the failure-mode table and reviewed for
  meaning (each one maps to a real failure mode, not just coverage).
- **Synthetic data:** the fictional KB articles and the 40 sample tickets.
- **Documentation:** first drafts of this README and the design note.

**How I worked with it:** I believe my most important contribution was choosing the direction and
questioning the coding agent at every step, iterating on the design before and during implementation.
Rather than reviewing every line, I accepted changes only with evidence: tests, the eval, or running
the app myself. I then walked through the core functionality so I can explain every part.
For example, a test ticket I submitted by hand (an off-topic question about headphones) showed keyword
retrieval attaching unrelated articles. That made me challenge the grounding design, which led to the
grounding decision below. Seeding all 40 tickets against the live API exposed a concurrency limit (429)
that no unit test could catch, which led to the `LLM_MAX_CONCURRENCY` cap.

**Where I deliberately did not rely on AI:**
- **Problem framing:** choosing the workflow, the customer scenario and the assumptions.
- **Routing policy:** which situations require a human, the recall-over-precision stance, and the
  "never auto-send" rule. These are product and risk decisions.
- **Gold labels:** I reviewed the expected category, priority and review decision for each sample
  ticket myself, because an eval graded against AI-generated labels would mostly measure the AI
  agreeing with itself.
- **Trade-off analysis:** model choice, cost interpretation and rollout plan.
- **Grounding strategy:** after manual testing exposed keyword retrieval returning unrelated
  articles, I weighed BM25 vs. the full KB in context vs. an agent with KB tools. I chose full context
  for a small KB and had both modes measured on the same eval before accepting the change.
- **Runtime design principle:** the LLM produces *signals and drafts*; deterministic code makes
  *decisions*. AI is never on the critical path of the support workflow.
