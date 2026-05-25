Generated at: 2026-05-18 00:02:42 MSK

## Defense Q&A

## Why not GPT-4 only?

GreenData needs a cost-aware and locally runnable path. B3 supports small 7B/8B generator profiles and keeps safety outside the model: deterministic rules, schema overlay, EXPLAIN sandbox, classifier stages and abstain logic.

## Why 33 risk classes?

The taxonomy covers more than web SQL injection. It includes classic SQLi, PostgreSQL-specific mutation, sensitive exposure, schema leakage, reliability/cost risks, prompt-to-SQL attacks and generation-quality failures. The original 9 baseline labels remain compatible.

## What if the model becomes outdated?

The generator is behind `LLM_GENERATOR_MODEL` and `LLM_MODE`. The safety layer is model-agnostic: rules, dataset eval, ML thresholds and judge contract stay the same while the model profile can change.

## How is data privacy handled?

The project does not execute final SQL on production data. EXPLAIN runs under `audit_ro` with read-only permissions. Training data is synthetic + public-style mutations in this checkout; raw external data is not committed.

## Why allow 90 seconds hard timeout?

B3 uses 2 candidates, up to 5 iterations, 9 tool checks, classifier stages, LLM judge and EXPLAIN sandbox. The PR recommends 60s soft / 90s hard so hard cases can finish safely instead of being cut at 60 seconds.

## What happens after 60 seconds?

The request continues. If it finishes before 90 seconds, API returns HTTP 200 and sets `metadata.latency_warning=true`. The UI can show a warning. After 90 seconds API returns HTTP 504.

## What happens if GreenData rejects 90 seconds?

Fallback is documented: set `LLM_MULTI_CANDIDATE=false`, reduce default max iterations to 3, update env/docs and record the recall risk as open.

## What are the final metrics?

On v1.0 eval report `case3_sqlsec_eval_20260517_235913.json`: 780 eval rows, 9/9 Sprint 2 critical labels at recall 1.00, safe block FP 0.00, classifier p95 latency 356 ms.

## Is this production-ready?

It is defense-ready for B3 MVP. Production hardening remains: explainability UI, cache/refresh infra, private redteam rotation and possible full encoder fine-tune.

## Why does the dataset have deterministic generated rows?

Raw files are absent in this checkout. `scripts/dataset_build.py` creates reproducible v1.0 rows from templates so train/eval can be verified end to end. Manual GreenData examples can be added under `data/eval/raw/` later.

## Why oversample DML/DDL/privilege?

The source family table sums to 3,350 rows while the B3 requirement is 3,750 rows. The missing 400 rows are assigned to rare critical DML/DDL/privilege classes to improve support for `COPY_EXPORT`, `PRIV_ESCALATE`, `PLPGSQL_UNSAFE` and `DYNAMIC_EXECUTE`.

## What prevents false positives on safe analytics?

The v1.0 evaluator separates `gold_safe_readonly` from hard negatives and measures block false positives. Prompt labels from ML/encoder are suppressed when there is no attack prompt, which removed the observed safe-query FP caused by numeric prompt-token leakage.

## Why not implement B3-EXPLAIN now?

The master decision Q-G4 defers visual explainability to B3-EXPLAIN. Current defense uses audit log and trace_viewer; highlight UI is a follow-up branch.
