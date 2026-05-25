Generated at: 2026-05-25 10:11:56 MSK

# PR Artifacts Quality Upgrade Report - TZ-02

## summary

Выполнен TZ-02: улучшен каталог `data/sql_guard/forbidden_constructs.yaml` без подключения к runtime.
Сохранены 27 правил, все `id`, `label`, `severity`, `category` оставлены без переименования или пересчета.
Добавлены `runtime_mvp`, `runtime_group`, `runtime_reason`, исправлены пустые и слабые examples, уточнены широкие `ast_check`.
Добавлены research note и validator script для следующей integration branch.

## changed_files

| File | What changed | Why |
|---|---|---|
| `data/sql_guard/forbidden_constructs.yaml` | runtime eligibility для 27 rules, corrected examples, narrower `ast_check` values | убрать пустые поля, снизить false positive risk, дать machine-check mapping |
| `data/sql_guard/forbidden_constructs_research.md` | new research note with groups, criteria, 5 positive and 5 negative examples | объяснить runtime subset and deferred rules |
| `scripts/validate_forbidden_constructs.py` | new validator for YAML catalog | enforce required fields, allow-lists, runtime fields and examples |

## before_after_examples

| # | Rule | Before | After | Reason |
|---:|---|---|---|---|
| 1 | `insert_foreign_table` | `example_bad: [""]`, `example_good: [""]` | concrete INSERT bad examples and SELECT good example | пустые required fields мешали validator/runtime mapping |
| 2 | `insert_foreign_table` | `ast_check: schema_access` | `ast_check: insert_blocked_select_only_mode` | foreign-table nuance требует catalog; MVP read-only блокирует InsertStmt |
| 3 | `dblink_usage` | no `added_round` | `added_round: "2026-05-23"` | missing required metadata |
| 4 | `copy_from_program` | `example_good` was a string | `example_good` is a list with safe SELECT | единый YAML contract для examples |
| 5 | `multi_statement` | `ast_check: node_type` | `ast_check: statement_count_gt_1` | node type не описывал реальную проверку |
| 6 | `comment_payload_bypass` | `ast_check: node_type`, `runtime_mvp` absent | `ast_check: raw_sql_comment_regex`, `runtime_group: deferred` | raw comment regex рискован для live blocking |
| 7 | `blind_string_concat` | broad `node_type` | `where_or_having_concat_operator`, `eval_or_review` | снижает риск блокировать легитимную конкатенацию |
| 8 | `pg_sleep_long` | `func_call_with_arg` | `func_call_numeric_arg_gt` with `arg_threshold_seconds: 1` | правило зависит от threshold, а не от любого вызова |
| 9 | `delete_without_where` / `update_without_where` | broad `node_type` | `dml_without_where_or_tautology` | блокировать только no-WHERE/trivial-WHERE, а не любой DML node |
| 10 | DDL/privilege/RCE/FS rules | placeholder good examples like comments | safe SELECT examples | модель и reviewer видят разрешенную альтернативу, а не пустой комментарий |

## checks_run

| Command | Result | Notes |
|---|---|---|
| `python3 scripts/validate_forbidden_constructs.py` | PASS | 27 rules, 20 runtime_mvp, 7 review/deferred |
| `python3 -m py_compile scripts/validate_forbidden_constructs.py` | PASS | validator syntax OK |
| YAML audit via `yaml.safe_load` | PASS | 27 rules, 0 empty required fields, runtime fields present for all rules |
| `rg -n "runtime_mvp\|runtime_reason\|runtime_group" data/sql_guard/forbidden_constructs.yaml` | PASS | 27 runtime field groups visible |
| `pytest -q tests/test_pii_masking.py tests/test_ast_oracle_check.py` | FAIL | collection failed: `ModuleNotFoundError: app` without project root in `PYTHONPATH` |
| `PYTHONPATH=. pytest -q tests/test_pii_masking.py tests/test_ast_oracle_check.py` | PASS | 66 passed |

## deferred_items

- `app/sql_guard.py` runtime integration is intentionally deferred.
- `eval_or_review` rules are not live blockers yet: schema introspection, blind concat, tautology and UNION exfil need context-aware checks.
- `comment_payload_bypass` stays deferred until generated-SQL comment policy is explicit.
- Table-specific foreign-scope INSERT logic is deferred; MVP maps the rule to read-only generated SQL.
- No PII tags, labels, severity values or join keys were changed.

## risks

- New `ast_check` names are data contract values for future integration; runtime code does not consume them yet.
- `runtime_mvp` is a recommended first subset, not proof that implementation already exists.
- `pg_catalog_access` and `information_schema_access` remain review-only to avoid blocking explicit schema-introspection tasks.

## verdict

READY_FOR_INTEGRATION
