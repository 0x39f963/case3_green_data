Generated at: 2026-05-25 10:10:29 MSK

# Forbidden Constructs Research Note

## Scope

- Artifact: `data/sql_guard/forbidden_constructs.yaml`.
- Goal: make the catalog usable for future partial runtime integration without wiring it into `app/sql_guard.py` now.
- Rule count is preserved: 27 rules.
- Labels, severity values and categories are preserved.

## Runtime Groups

| Group | Meaning | Count | Use |
|---|---|---:|---|
| `runtime_mvp` | deterministic rule with low false positive risk | 20 | first runtime candidate set |
| `eval_or_review` | useful security signal, but needs more context before live blocking | 6 | auditor/reviewer/eval hint |
| `deferred` | technically useful, but blocker policy is not stable enough | 1 | keep out of runtime MVP |

## MVP Runtime Rules

| id | Why included |
|---|---|
| `drop_object` | DDL object deletion is destructive and AST-deterministic. |
| `truncate_table` | Full table wipe is destructive and AST-deterministic. |
| `alter_object` | Schema mutation is outside generated analytics SQL. |
| `create_object` | Schema creation is outside generated analytics SQL. |
| `grant_privileges` | Privilege mutation is outside generated analytics SQL. |
| `revoke_privileges` | Privilege mutation is outside generated analytics SQL. |
| `set_role` | Role switching is a privilege-context change. |
| `reset_role` | Role reset can be part of role-bypass chains. |
| `delete_without_where` | DELETE without WHERE or with tautology has direct data-loss risk. |
| `update_without_where` | UPDATE without WHERE or with tautology has direct data-corruption risk. |
| `insert_foreign_table` | MVP maps this to select-only mode: any INSERT is unsafe for generated SQL. |
| `copy_from_program` | COPY PROGRAM is explicit OS command execution. |
| `dblink_usage` | Remote DB call is outside the analytics contract. |
| `lo_import` | Server file import is file-system access. |
| `lo_export` | Server file export is file-system access. |
| `pg_read_file` | Direct server file read. |
| `pg_ls_dir` | Server directory enumeration. |
| `pg_read_binary_file` | Direct binary server file read. |
| `pg_sleep_long` | Time delay above 1 second has no analytics value. |
| `multi_statement` | More than one statement can hide a second operation. |

## Review Or Eval Only

| id | Why not live-blocked in MVP |
|---|---|
| `pg_catalog_access` | Can false-positive on explicit schema-introspection tasks. |
| `information_schema_access` | Can false-positive on explicit schema-introspection tasks. |
| `blind_string_concat` | String concatenation in filters can be legitimate. |
| `blind_tautology` | Needs precise AST context to avoid broad literal false positives. |
| `union_null_padding` | UNION with NULL padding can be legitimate reporting SQL. |
| `union_cast_exfil` | Needs table and sensitivity context before blocking. |

## Deferred

| id | Reason |
|---|---|
| `comment_payload_bypass` | Raw comment regex can flag harmless comments; policy must first decide whether generated SQL may contain comments at all. |

## Validator Criteria

- YAML root is a list.
- Rule count is 27 unless an explicit migration changes it.
- `id` values are unique.
- Required fields are present and non-empty: `id`, `label`, `severity`, `category`, `ast_check`, `runtime_mvp`, `runtime_group`, `runtime_reason`, `description`, `example_bad`, `example_good`, `reference`, `added_round`.
- `runtime_mvp` is boolean.
- `runtime_group` is one of `runtime_mvp`, `eval_or_review`, `deferred`.
- `runtime_mvp=true` must use `runtime_group=runtime_mvp`.
- `example_bad`, `example_good` and `reference` are non-empty lists of non-empty strings.
- `label`, `category` and `ast_check` belong to known allow-lists.
- Function checks must provide `func_name`; threshold checks must provide `arg_threshold_seconds`; schema checks must provide `schema_name`.

## Positive Examples

| # | SQL | Expected rule |
|---:|---|---|
| 1 | `DROP TABLE sys_employee;` | `drop_object` |
| 2 | `UPDATE credit_contract SET status = 0 WHERE 1=1;` | `update_without_where` |
| 3 | `COPY sys_employee FROM PROGRAM 'id';` | `copy_from_program` |
| 4 | `SELECT pg_read_file('/etc/passwd');` | `pg_read_file` |
| 5 | `SELECT id FROM sys_employee; DROP TABLE sys_employee;` | `multi_statement` |

## Negative Examples

| # | SQL | Should not trigger |
|---:|---|---|
| 1 | `SELECT id, name FROM sys_employee LIMIT 20;` | DDL, privilege, RCE, FS, multi-statement |
| 2 | `SELECT COUNT(*) AS contract_count FROM credit_contract;` | `truncate_table` |
| 3 | `UPDATE sys_employee SET status = 0 WHERE id = 24;` | `update_without_where` |
| 4 | `DELETE FROM scp_application WHERE id = 1 AND status = 1;` | `delete_without_where` |
| 5 | `SELECT pg_sleep(0.001);` | `pg_sleep_long` |

## Deferred Integration Notes

- Do not wire this catalog into `app/sql_guard.py` in this quality branch.
- Integration branch should implement only the `runtime_mvp` subset first.
- `eval_or_review` rules should be passed to auditor/reviewer as hints until context-aware matching exists.
- `comment_payload_bypass` needs a separate generated-SQL comment policy before live blocking.
