"""Offline evaluation against the labeled sample tickets.

Runs the production analysis code (Pipeline.analyze) on every ticket in data/sample_tickets.jsonl and
reports triage accuracy, review-routing recall/precision, latency, and cost. Use it to compare models
or prompt versions before changing production config.

    python scripts/run_eval.py                                  # uses .env settings
    python scripts/run_eval.py --triage-model claude-sonnet-5-5 # compare a different triage model
    python scripts/run_eval.py --no-draft                       # triage only (cheaper, faster)
    python scripts/run_eval.py --mode mock                      # smoke-test the script, no API key
    python scripts/run_eval.py --data data/holdout_tickets.jsonl  # held-out set (never tune on it)

Live mode calls the real API and costs money (about $0.30 for the 40 tickets with drafts).
"""

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.llm.client import AnthropicClient, CallRecord, MockClient  # noqa: E402
from app.llm.prompts import PROMPT_VERSION  # noqa: E402
from app.observability import configure_logging, percentile  # noqa: E402
from app.pipeline.orchestrator import Analysis, Pipeline  # noqa: E402
from app.pipeline.retrieval import KnowledgeBase  # noqa: E402

PRIORITY_ORDER = ["P1", "P2", "P3", "P4"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["live", "mock"], default=None)
    parser.add_argument("--triage-model", default=None)
    parser.add_argument("--draft-model", default=None)
    parser.add_argument("--grounding", choices=["full_context", "bm25"], default=None)
    parser.add_argument("--no-draft", action="store_true")
    parser.add_argument("--data", default="data/sample_tickets.jsonl",
                        help="labeled tickets; data/holdout_tickets.jsonl is never used for tuning")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    overrides = {k: v for k, v in {
        "llm_mode": args.mode, "triage_model": args.triage_model, "draft_model": args.draft_model,
        "grounding": args.grounding,
    }.items() if v}
    settings = Settings(**overrides, log_level="WARNING")
    configure_logging(settings.log_level)

    records: list[CallRecord] = []
    lock = threading.Lock()

    def sink(record: CallRecord) -> None:
        with lock:
            records.append(record)

    if settings.llm_mode == "live":
        if not settings.anthropic_api_key:
            sys.exit("Live mode needs ANTHROPIC_API_KEY (or pass --mode mock).")
        llm = AnthropicClient(settings, sink=sink)
    else:
        llm = MockClient(sink=sink)

    pipeline = Pipeline(Database(":memory:"), llm, KnowledgeBase.from_dir(settings.kb_dir), settings)
    data_path = ROOT / args.data
    tickets = [json.loads(l) for l in data_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    tickets = tickets[: args.limit]

    print(f"Evaluating {len(tickets)} tickets | mode={settings.llm_mode} triage={settings.triage_model} "
          f"draft={'off' if args.no_draft else settings.draft_model} grounding={pipeline.grounding}")
    started = time.perf_counter()

    def run(t: dict) -> tuple[dict, Analysis]:
        return t, pipeline.analyze(t["subject"], t["body"], with_draft=not args.no_draft)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, tickets))
    wall_s = time.perf_counter() - started

    report = build_report(results, records, settings, args, wall_s, pipeline.grounding)
    print_report(report)

    out_dir = ROOT / "eval_results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{settings.llm_mode}-{data_path.stem}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nFull results: {out.relative_to(ROOT)}")


def build_report(
    results: list[tuple[dict, Analysis]], records: list[CallRecord], settings: Settings, args,
    wall_s: float, grounding: str,
) -> dict:
    rows, failures = [], 0
    tp = fp = fn = tn = 0
    cat_ok = pri_ok = pri_close = 0
    # Grounding (only for tickets with a draft): did the draft cite the right article(s)?
    grounded_hits = grounded_n = cited_total = cited_correct = false_grounding = no_answer_n = 0
    for t, a in results:
        if a.triage is None:
            failures += 1
            rows.append({"id": t["id"], "error": a.error})
            continue
        predicted_review = bool(a.review_reasons)
        cat_ok += a.triage.category == t["expected_category"]
        pri_ok += a.triage.priority == t["expected_priority"]
        pri_close += abs(PRIORITY_ORDER.index(a.triage.priority) - PRIORITY_ORDER.index(t["expected_priority"])) <= 1
        if t["should_review"] and predicted_review: tp += 1
        elif t["should_review"]: fn += 1
        elif predicted_review: fp += 1
        else: tn += 1
        cited = a.draft.cited_article_ids if a.draft else None
        expected = set(t.get("expected_articles", []))
        if cited is not None:
            cited_total += len(cited)
            cited_correct += len(expected & set(cited))
            if expected:
                grounded_n += 1
                grounded_hits += bool(expected & set(cited))
            else:
                no_answer_n += 1
                false_grounding += bool(cited)
        rows.append({
            "id": t["id"], "subject": t["subject"],
            "category": [t["expected_category"], a.triage.category],
            "priority": [t["expected_priority"], a.triage.priority],
            "review": [t["should_review"], predicted_review],
            "reasons": a.review_reasons, "flags": a.flags, "confidence": a.triage.confidence,
            "articles": [x.id for x in a.articles], "cited": cited,
            "expected_articles": sorted(expected), "error": a.error,
        })

    scored = len(results) - failures
    steps = {}
    for step in sorted({r.step for r in records}):
        ok = [r for r in records if r.step == step and r.outcome == "ok"]
        latencies = [r.latency_ms for r in ok]
        steps[step] = {
            "calls": sum(r.step == step for r in records),
            "failed_attempts": sum(r.step == step and r.outcome != "ok" for r in records),
            "latency_ms_p50": percentile(latencies, 50),
            "latency_ms_p95": percentile(latencies, 95),
            "avg_input_tokens": round(sum(r.input_tokens for r in ok) / len(ok)) if ok else None,
            "avg_output_tokens": round(sum(r.output_tokens for r in ok) / len(ok)) if ok else None,
            "cache_hit_rate": _r(sum(r.cache_read_tokens > 0 for r in ok), len(ok)),
            "cost_usd": round(sum(r.cost_usd or 0 for r in records if r.step == step), 4),
        }
    total_cost = sum(r.cost_usd or 0 for r in records)

    return {
        "config": {
            "mode": settings.llm_mode, "triage_model": settings.triage_model,
            "draft_model": None if args.no_draft else settings.draft_model,
            "draft_effort": settings.draft_effort, "grounding": grounding,
            "data": args.data, "prompt_version": PROMPT_VERSION,
        },
        "summary": {
            "tickets": len(results), "failures": failures,
            "category_accuracy": _r(cat_ok, scored), "priority_accuracy": _r(pri_ok, scored),
            "priority_within_one": _r(pri_close, scored),
            "review_recall": _r(tp, tp + fn), "review_precision": _r(tp, tp + fp),
            "review_confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
            "grounding_recall": _r(grounded_hits, grounded_n),
            "citation_precision": _r(cited_correct, cited_total),
            "false_grounding_rate": _r(false_grounding, no_answer_n),
            "total_cost_usd": round(total_cost, 4), "cost_per_ticket_usd": _r(total_cost, len(results), 5),
            "wall_clock_s": round(wall_s, 1),
        },
        "steps": steps,
        "tickets": rows,
    }


def print_report(report: dict) -> None:
    s = report["summary"]
    print("\n| Metric | Value |\n|---|---|")
    for key in ("category_accuracy", "priority_accuracy", "priority_within_one", "review_recall",
                "review_precision", "grounding_recall", "citation_precision", "false_grounding_rate",
                "failures", "cost_per_ticket_usd", "total_cost_usd", "wall_clock_s"):
        print(f"| {key} | {s[key]} |")
    print(f"\nReview routing confusion: {s['review_confusion']}")
    print("\n| Step | Calls | Failed attempts | p50 ms | p95 ms | Avg in/out tokens | Cache hit rate | Cost $ |\n|---|---|---|---|---|---|---|---|")
    for step, m in report["steps"].items():
        print(f"| {step} | {m['calls']} | {m['failed_attempts']} | {m['latency_ms_p50']} | {m['latency_ms_p95']} | "
              f"{m['avg_input_tokens']}/{m['avg_output_tokens']} | {m['cache_hit_rate']} | {m['cost_usd']} |")
    wrong_grounding = [t for t in report["tickets"] if t.get("cited") is not None and (
        set(t["cited"]) != set(t["expected_articles"]))]
    if wrong_grounding:
        print("\nGrounding differences (expected -> cited):")
        for t in wrong_grounding:
            print(f"  {t['id']} {t['subject'][:40]:40} {t['expected_articles']} -> {t['cited']}")
    misses = [t for t in report["tickets"] if "category" in t and (
        t["category"][0] != t["category"][1] or t["priority"][0] != t["priority"][1] or t["review"][0] != t["review"][1])]
    if misses:
        print("\nDisagreements (expected -> predicted):")
        for t in misses:
            print(f"  {t['id']} {t['subject'][:40]:40} cat {t['category'][0]}->{t['category'][1]} "
                  f"pri {t['priority'][0]}->{t['priority'][1]} review {t['review'][0]}->{t['review'][1]} {t['reasons']}")
    for t in report["tickets"]:
        if "category" not in t:
            print(f"  {t['id']} FAILED: {t['error']}")


def _r(num: float, den: float, digits: int = 3) -> float | None:
    return round(num / den, digits) if den else None


if __name__ == "__main__":
    main()
