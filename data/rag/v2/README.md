Generated at: 2026-05-20 08:28:22 MSK

## table_knowledge_index_v2

- artifact: `.cursor/!tmp/!all/2026-05-20/1-table_knowledge_index_v2.csv`
- goal: stronger table-level knowledge index for SQL generation over all GreenData tables.
- input sources: `schema.json`, legacy `generation_meta.json`, `deploy/schema_overlay.json`.
- old FAISS files are read-only inputs; no old index file is modified.
- CSV row 1 is timestamp metadata; row 2 is the header for ingestion.

## Content

- rows: 60
- expected tables from schema.json: 60
- fields: business domain, entity role, grain, aliases, query triggers, column groups, incoming/outgoing relations, approved joins, SQL route hints, safe SQL skeleton, LLM index text.
- low-confidence rows: 45; mostly TODO descriptions, empty aliases, or technical multiselect tables.

## Brainstorm notes

- retrieval unit: one rich row per table, because SQL generation first needs to choose source tables before narrowing columns.
- main matching signals: `business_description`, `aliases`, `natural_language_triggers`, `use_when`, `query_route_notes`, `related_tables`, `llm_index_text`.
- example route for "заявки по предельным рейтингам клиентов": start from `scp_application`, `scp_project_ans` or `scp_decision_quest`; join client through `sys_company` or `participant_app`/`application_obj`; inspect `clc_grade_id`, `risk_zone_id`, `est_credit_limit`, `exp_limit_credit_rub` and related limit/risk columns.
- column payload strategy: keep all 1872 source columns in `all_columns_compact`, but embed `llm_index_text` first to avoid noisy retrieval.

## Recommended ingestion

- skip first CSV row before reading the header.
- embed `llm_index_text` as the main document text.
- keep `table_name`, `business_domain`, `entity_role`, `related_tables`, `approved_joins`, `sensitive_columns` as metadata filters.
- for generation prompts, combine `llm_index_text` with `all_columns_compact` only for the top retrieved tables.

## VERIFICATION_REPORT

- criteria_status: PASS - CSV has one row for each schema table; legacy index was not overwritten; key relation and semantic fields are populated.
- evidence_refs: `schema.json.metadata.total_tables=60`; `generation_meta.json` has 60 schema docs; generated CSV data rows=60.
- blockers: none.
- verdict: PASS.
- next_action: load the CSV into a new retrieval collection and run top-k checks on analyst queries about applications, clients, limits, ratings and СЭБ checks.
