# Eval runs behind the README numbers

These are the saved outputs of `scripts/run_eval.py` for every live run the main README quotes. Each
file records the configuration and, per ticket, the expected and predicted values: category,
priority, review decision and reasons, cited articles, and confidence. The raw output folder
`eval_results/` is git-ignored; these are the runs worth keeping.

| File | What it is | README table |
|---|---|---|
| `2026-10-04_grounding-full-kb_sample.json` | 40 sample tickets, whole KB in the draft prompt, prompt `2026-10-04.1` | Eval results: "full KB" row |
| `2026-10-04_grounding-bm25_sample.json` | same tickets and prompt, BM25 top-3 retrieval | Eval results: "BM25" row |
| `2026-10-05_prompt-v0-baseline_sample.json` | second run of prompt `2026-10-04.1`, used as the "before" | Prompt iteration: sample "before" |
| `2026-10-05_prompt-v1-p1-regression_sample.json` | prompt `2026-10-05.1`: better priority, but two real P1s dropped to P2 | Prompt iteration: "it went wrong once" |
| `2026-10-05_prompt-v2-final_sample.json` | prompt `2026-10-05.2` (current) | Prompt iteration: sample "after" |
| `2026-10-05_prompt-v0-baseline_holdout.json` | 12 held-out tickets, old prompt, measured *before* changing it | Prompt iteration: held-out "before" |
| `2026-10-05_prompt-v2-final_holdout.json` | 12 held-out tickets, prompt `2026-10-05.2` | Prompt iteration: held-out "after" |

## Re-scoring with the current labels

After these runs, label review changed two tickets to `should_review: true`: T33 (a cancellation)
and T35 (an unexplained server error). The `summary` stored in each file still reflects the labels
at run time. To see the numbers the README reports, re-score the saved predictions against the
current labels. This needs no API key and costs nothing:

```powershell
python scripts/rescore_eval.py docs/eval_runs/2026-10-05_prompt-v2-final_sample.json
python scripts/rescore_eval.py docs/eval_runs/*.json      # all runs
```

Only the review metrics change; category, priority, grounding and cost are unaffected by those two
labels.
