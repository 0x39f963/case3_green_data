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

## False Positive Risks Per Rule (eval_or_review and deferred)

Для каждого правила вне runtime_mvp ниже фиксируется: 1-2 конкретных SQL-паттерна, на которых rule выстрелит ложно, и какой контекст нужен validator-у, чтобы fp устранить.

### pg_catalog_access (eval_or_review)

False positive patterns:
- `SELECT relname FROM pg_class WHERE relkind = 'r';` — legitimate schema introspection for an explicit «list user tables» task.
- `SELECT attname FROM pg_attribute WHERE attrelid = 'sys_employee'::regclass;` — legitimate column lookup for a documentation task.

Context required: task intent flag «schema_introspection_allowed=true» либо явный whitelist для pg_class/pg_attribute, если задача относится к metadata-домену.

### information_schema_access (eval_or_review)

False positive patterns:
- `SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';` — task асит список таблиц.
- `SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'sys_employee';` — task для документации схемы.

Context required: то же, что и для `pg_catalog_access`. Желателен явный список разрешённых information_schema views.

### blind_string_concat (eval_or_review)

False positive patterns:
- `SELECT first_name || ' ' || sur_name AS full_name FROM sys_employee;` — легитимная конкатенация в SELECT-проекции, не в WHERE.
- `SELECT id WHERE comment LIKE '%' || $1 || '%';` — параметризованная конкатенация для LIKE-поиска, безопасна с подстановкой параметра.

Context required: правило должно различать конкатенацию в SELECT vs WHERE/HAVING; параметризованные конкатенации (`||` с `$N`) безопасны, raw string injection — нет.

### blind_tautology (eval_or_review)

False positive patterns:
- `SELECT 1 AS sentinel FROM sys_employee LIMIT 1;` — sentinel constant select, не tautology в WHERE.
- `SELECT COUNT(*) FROM credit_contract WHERE status = 1 OR (status = 1 AND amount > 0);` — формальная избыточность, но не классический `OR 1=1`.

Context required: AST-локализация: tautology must быть в WHERE/HAVING, и значение левой и правой стороны должно быть либо литералом, либо ссылкой на ту же колонку.

### union_null_padding (eval_or_review)

False positive patterns:
- `SELECT id, name FROM credit_contract UNION SELECT id, NULL FROM scp_application;` — легитимный отчёт, где у одной из таблиц нет имени.
- `SELECT id, NULL::int AS year FROM count_turnover;` — типизованный NULL в одном SELECT (не UNION).

Context required: правило срабатывает только если UNION между разными «sensitivity-доменами» (например, PII-таблица + lookup-таблица). Нужен sensitivity-aware mapping таблиц.

### union_cast_exfil (eval_or_review)

False positive patterns:
- `SELECT id::text FROM sys_employee UNION SELECT id::text FROM sys_company;` — обычный UNION с кастами для совместимости типов.
- `SELECT id, amount::text FROM credit_contract;` — каст без UNION, безопасный.

Context required: правило выстреливает только при UNION между чувствительной и не-чувствительной таблицами; одиночные касты в SELECT/проекции — не повод для блокировки.

### comment_payload_bypass (deferred)

False positive patterns:
- `SELECT /* report 2026-Q1 */ id, status FROM credit_contract LIMIT 100;` — легитимный inline-комментарий с пометкой отчёта.
- `-- generated by Marina@2026-05-23\nSELECT id FROM sys_employee LIMIT 10;` — авторский комментарий, не payload.

Context required: explicit comment policy — допустимы ли any comments в generated SQL вообще; whitelist форматов (например, `/* report ... */`); regex должен ловить признаки payload-обхода (несколько `--`, попытки скрыть keywords), а не любой comment.

## Deferred Integration Notes

- Do not wire this catalog into `app/sql_guard.py` in this quality branch.
- Integration branch should implement only the `runtime_mvp` subset first.
- `eval_or_review` rules should be passed to auditor/reviewer as hints until context-aware matching exists.
- `comment_payload_bypass` needs a separate generated-SQL comment policy before live blocking.
