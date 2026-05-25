Generated at: 2026-05-18 21:16:43 MSK

# Adversarial SQL Requests Changelog

## v0.2 - 2026-05-18

- Added runtime decision fields for ТЗ-5: `expected_runtime_decision` and optional `expected_runtime_decision_alternatives`.
- Preserved semantic reviewer field `expected_decision`, including legacy `block`.
- Mapped all `block` rows to runtime `abstain`; v0.2 decision pairs are `approve->approve`, `revise->revise`, `abstain->abstain`, `block->abstain`.
- Added `vuln_class_unmapped` to separate expected empty FAISS security classes from missing annotation.
- Removed default boilerplate `task_suffix` markers from v0.2 user tasks; v0.1 generation still supports the legacy suffix for comparison.
- Added natural per-row detail in v0.2 user tasks to keep V-3/V-15 uniqueness without `Контекст проверки` / `Review context` markers.
- Enforced `attack_prompt` coverage for `prompt_policy_bypass`, `classic_injection`, `union_schema_leak`.
- Enforced `safe_rewrite` coverage for semantic `approve` and `revise` rows.

Artifacts:

| Version | JSONL | Rows | sha256 |
|---|---|---:|---|
| v0.1 | `data/bench/requests/adversarial_sql_requests_v0_1.jsonl` | 120 | `8f2789ea6e1e7a4897376ede86e78155915cf8886bb449df5d523f7d4a7813c3` |
| v0.2 | `data/bench/requests/adversarial_sql_requests_v0_2.jsonl` | 120 | `ca85329b0792126b212e62fce0c9d29075082791050976a4284f153e510b7177` |

Reports:

| Version | Report |
|---|---|
| v0.1 | `data/bench/reports/adversarial_sql_requests_v0_1.json` |
| v0.2 | `data/bench/reports/adversarial_sql_requests_v0_2.json` |
