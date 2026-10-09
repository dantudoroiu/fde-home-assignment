"""Recompute eval metrics from a saved run against the current labels, with no API calls.

run_eval.py stores every prediction per ticket. When labels are corrected later, re-scoring the saved
predictions shows what the run scores under the corrected labels, and lets anyone reproduce the README
numbers from the committed files in docs/eval_runs/.

    python scripts/rescore_eval.py docs/eval_runs/2026-10-05_prompt-v2-final_sample.json
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABEL_FILES = [ROOT / "data" / "sample_tickets.jsonl", ROOT / "data" / "holdout_tickets.jsonl"]
PRIORITY_ORDER = ["P1", "P2", "P3", "P4"]


def load_labels() -> dict[str, dict]:
    labels = {}
    for path in LABEL_FILES:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                ticket = json.loads(line)
                labels[ticket["id"]] = ticket
    return labels


def rescore(run: dict, labels: dict[str, dict]) -> dict:
    n = cat_ok = pri_ok = pri_close = 0
    tp = fp = fn = tn = 0
    grounded_hits = grounded_n = cited_total = cited_correct = false_grounding = no_answer_n = 0
    for row in run["tickets"]:
        if "category" not in row:  # failed ticket: no prediction to score
            continue
        label = labels[row["id"]]
        n += 1
        predicted_category, predicted_priority, predicted_review = row["category"][1], row["priority"][1], row["review"][1]
        cat_ok += predicted_category == label["expected_category"]
        pri_ok += predicted_priority == label["expected_priority"]
        pri_close += abs(PRIORITY_ORDER.index(predicted_priority) - PRIORITY_ORDER.index(label["expected_priority"])) <= 1
        expected_review = label["should_review"]
        tp += expected_review and predicted_review
        fn += expected_review and not predicted_review
        fp += predicted_review and not expected_review
        tn += not expected_review and not predicted_review

        cited = row.get("cited")
        if cited is None:
            continue
        expected = set(label.get("expected_articles", []))
        cited_total += len(cited)
        cited_correct += len(expected & set(cited))
        if expected:
            grounded_n += 1
            grounded_hits += bool(expected & set(cited))
        else:
            no_answer_n += 1
            false_grounding += bool(cited)

    return {
        "tickets_scored": n,
        "category_accuracy": _r(cat_ok, n),
        "priority_accuracy": _r(pri_ok, n),
        "priority_within_one": _r(pri_close, n),
        "review_recall": _r(tp, tp + fn),
        "review_precision": _r(tp, tp + fp),
        "review_confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "grounding_recall": _r(grounded_hits, grounded_n),
        "citation_precision": _r(cited_correct, cited_total),
        "false_grounding_rate": _r(false_grounding, no_answer_n),
        "cost_per_ticket_usd": run["summary"].get("cost_per_ticket_usd"),
    }


def _r(num: float, den: float) -> float | None:
    return round(num / den, 3) if den else None


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    labels = load_labels()
    # Expand globs ourselves: PowerShell passes "*.json" through literally.
    paths = [p for arg in sys.argv[1:] for p in (sorted(Path().glob(arg)) if "*" in arg else [Path(arg)])]
    for arg in paths:
        run = json.loads(Path(arg).read_text(encoding="utf-8"))
        new = rescore(run, labels)
        old = run["summary"]
        print(f"\n{Path(arg).name}  {run.get('config', {})}")
        print(f"| Metric | Saved (labels at run time) | Rescored (current labels) |\n|---|---|---|")
        for key, value in new.items():
            if key in ("tickets_scored", "review_confusion"):
                continue
            print(f"| {key} | {old.get(key)} | {value} |")
        print(f"review confusion now: {new['review_confusion']}")


if __name__ == "__main__":
    main()
