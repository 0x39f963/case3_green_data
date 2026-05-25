Generated at: 2026-05-19 00:55:00 MSK

## Benchmark Audit Feedback

### Goal

`scripts/bench_audit_runs.py` reviews stored benchmark pipeline runs and saves reviewer feedback as local JSONL plus Store rows. The runner is suggest-only: it never edits prompts, FAISS knowledge base, schema overlay, or SQL guard code.

### Flow

1. Read audit targets from Benchmark Store: `/v1/audit/targets`.
2. Build compact input in `data/bench/audits/<benchmark_run_id>/inputs/<trace_id>.json`.
3. In `--dry-run`, stop after writing inputs.
4. In real mode, call one reviewer backend and validate output against `data/bench/audits/reviewer_output.schema.json`.
5. Save review rows through `/v1/audit/reviews`.
6. Write local archive `reviews.jsonl` and aggregate `suggestions.json`.

### CLI

```bash
python scripts/bench_audit_runs.py \
  --store-url http://localhost:18080 \
  --store-token "$BENCHMARK_INGEST_TOKEN" \
  --benchmark-run-id bench_20260518_001 \
  --reviewer-backend codex_cli \
  --reviewer-model gpt-5.5 \
  --limit 3
```

Useful options:
- `--dry-run`: write compact input only.
- `--resume`: skip traces already reviewed with same backend, model, and prompt version.
- `--prompt-version <sha>`: pin version instead of hashing prompt files.
- `--prompt-version-file <path>`: read the pinned prompt version from a text file.
- `--max-prompt-chars 30000`: enforce compact input budget.
- `--trace-id`, `--case-id`, `--family`: filter targets.
- `--reviewer-retry-attempts 3`: retry OpenRouter 429/5xx responses.
- `--reviewer-retry-backoff-sec 1.0`: exponential backoff base for reviewer retries.
- `--store-retry-attempts 3`: retry Benchmark Store requests, including review upsert.

### Store Tables

Migration `benchmark_service/sql/004_audit_feedback.sql` adds:
- `benchmark.audit_reviews`
- `benchmark.audit_step_scores`
- `benchmark.audit_sql_correctness`
- `benchmark.improvement_suggestions`
- P1-ready tables: `audit_suggestion_clusters`, `kb_patch_candidates`

### Safety

- Reviewer input includes prompt excerpts and SHA-256, not full prompt files.
- Tokens are used only as HTTP headers or env values, never inserted into reviewer prompt.
- Suggestions contain `patch_hint` text only. No live project file is changed by the runner.

### Reviewer Backends

| Backend | Use case | Notes |
|---|---|---|
| `codex_cli` | Local Codex review without network credentials in runner config. | The runner writes the full prompt to a temp file and pipes it to `codex exec`; this avoids very long stdin literals in shell commands. Usage is usually unavailable, so Store token and cost columns stay `NULL`. |
| `anthropic_cli` | Claude CLI reviewer. | Uses `--output-format json` and extracts `result`, `usage`, and `total_cost_usd` when the installed CLI returns them. Older CLI versions that do not support JSON output should fail with a clear backend error. |
| `openrouter` | HTTP reviewer through OpenRouter. | Requires `OPENROUTER_API_KEY`. HTTP 429 and 5xx are retried with exponential backoff; 4xx except 429 fail fast. |

### Prompt Version Workflow

Default prompt version is:

```text
sha256(system_prompt + "\n----\n" + user_template)
```

Any edit to `app/prompts/bench_reviewer_system.txt` or `app/prompts/bench_reviewer_user.txt` creates a new version. With `--resume`, traces reviewed under an older prompt version are processed again, while traces reviewed under the same version are skipped.

For CI, write the selected version to a file and pass:

```bash
python scripts/bench_audit_runs.py \
  --store-url http://localhost:18080 \
  --store-token "$BENCHMARK_INGEST_TOKEN" \
  --benchmark-run-id bench_20260518_001 \
  --prompt-version-file data/bench/audits/prompt-version.txt \
  --resume
```

### Runtime Decision Rule

The runtime pipeline emits `approve`, `revise`, or `abstain`. Dataset `expected_decision="block"` maps to runtime `abstain`; the pipeline enforces the block intent by refusing to produce an approved SQL answer. Reviewers must not mark a case wrong only because expected is `block` and actual runtime decision is `abstain`.

### Error Scenarios

| Scenario | Runner result | Stored review |
|---|---|---|
| Store unavailable before target fetch | exit `3`, `STORE_UNREACHABLE` | none |
| Reviewer returns invalid JSON after retry | exit `1`, `PARTIAL_ERROR` | `verdict=error`, no suggestions |
| Reviewer backend transport error | exit `1`, `PARTIAL_ERROR` | `verdict=error`, raw error in review payload |
| Prompt input cannot fit `--max-prompt-chars` | exit `2`, `CONFIG_ERROR` | none |
| Cost budget exceeded after a review | exit `4`, `COST_BUDGET` | completed reviews remain stored |

### Cost Estimation

Reviewer cost depends on model pricing and compact input size. Use this table as planning math, not billing truth:

| Traces | Avg input chars | Approx input tokens | Notes |
|---:|---:|---:|---|
| 3 | 17,000 | 13k total | Safe live smoke sample. |
| 20 | 20,000 | 100k total | Good reviewer calibration batch. |
| 100 | 30,000 | 750k total | Needs cost guard and retry budget. |

For OpenRouter, set `--max-cost-usd` before large runs. Usage and cost are stored as `NULL` when the backend does not provide usage metadata; they are never forced to zero.
