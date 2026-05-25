Generated at: 2026-05-25 20:49:03 MSK

## Executive Summary

Документ фиксирует состояние текущего checkout на момент аудита. Ветка не чистая: `git status --short` показывает измененный `app/rag_adapter.py` и много untracked runtime/UI/benchmark файлов; этот документ описывает именно такое рабочее дерево.

Текущая система - FastAPI-приложение SQL Security Studio вокруг pipeline `SQLSecuritySystem.run`: natural language задача проходит intent-классификацию, prompt-check, RAG/schema retrieval, SQL generation, deterministic guard, read-only `EXPLAIN`, hybrid audit, decision и optional revise-loop. Выход - `SystemResult`, JSON trace, best-effort audit storage и UI/report surfaces.

Главное уточнение к high-level схеме из ТЗ: в текущем коде entry point графа - `intent_classify`, а не `prompt_check`. Узел `prompt_check` идет вторым. HTML-route `/import` в текущем `app/web_chat.py` не найден; старые docs упоминают import, но `docs/README.md` пишет, что ручной `/import` удален.

Критичные storage слои разделены:
- main demo Postgres: GreenData schema, `audit_runs`, `audit_iterations`, `system_prompts`;
- `audit_ro`: read-only role для `EXPLAIN`;
- benchmark Postgres: `benchmark.*` normalized store, `rag_embeddings`, oracle, smart-judge, analysis hypotheses;
- file storage: `data/traces`, `data/web_chats`, `data/bot`, `data/eval`, model cache.

Evidence:
- command: `git status --short`
- command: `find app -maxdepth 2 -type f | sort`
- `app/orchestrator.py` - graph, node order, `SystemResult` assembly
- `app/api.py` - FastAPI app, `/run`, `/health`, router includes
- `docs/README.md` - current user-facing docs, `/import` removal note

## Repo Map

| path | role | current notes |
|---|---|---|
| `app/api.py` | FastAPI root | `/run`, `/health`, `/prompts/candidates`, includes web and audit routers |
| `app/orchestrator.py` | pipeline graph | 9 named nodes including `intent_classify` and `revise` |
| `app/pipeline_service.py` | async API wrapper | latency budget, ContextVar overrides, timeout |
| `app/generator.py` | SQL generator | prompt registry, multi-candidate, optional tool-loop |
| `app/auditor.py` | hybrid auditor | classifier + security RAG + model audit |
| `app/rag_adapter.py` | RAG/schema layer | Marina FAISS, overlay v2, table_knowledge_v2, solutions |
| `app/web_chat.py` | personal cabinet shell | chat/history/prompts/runs detail/progress APIs |
| `app/web_audits.py` | benchmark UI router | runs, compare, batch cases, tariffs, job APIs |
| `benchmark_service/` | benchmark store | separate FastAPI + migrations + normalized tables |
| `deploy/` | runtime config | main compose, benchmark compose, Postgres init, overlays |
| `scripts/` | ops/eval/benchmark | smoke, RAG build, eval, smart-judge, oracle, synthetic |
| `tests/` | pytest coverage | route, UI API, RAG, business alignment, oracle, benchmark |
| `.cursor/arch-spec/` | specs | useful context, some stale vs code noted in Known Gaps |

Evidence:
- command: `find app -maxdepth 2 -type f | sort`
- command: `find deploy -maxdepth 3 -type f | sort`
- command: `find docs -maxdepth 3 -type f | sort`
- command: `find scripts -maxdepth 2 -type f | sort`
- command: `find tests -maxdepth 2 -type f | sort`
- `.cursor/arch-spec/L0/repo-map.md`
- `.cursor/arch-spec/L1/architecture.md`

## Runtime Services

`deploy/docker-compose.yml` defines 8 services:

| service | port/profile | responsibility |
|---|---|---|
| `postgres` | host `${POSTGRES_PORT:-15433}` -> 5432 | demo DB with GreenData schema and app tables |
| `rag-init` | profile `init` | runs Marina `schema_parser.py` and `build_indices.py` |
| `rag-init-v2` | profile `init` | loads `table_knowledge_v2` into benchmark PG |
| `app` | `${APP_PORT:-8000}` -> 8000 | FastAPI `/run`, web cabinet, audit UI routes |
| `ui` | `${STREAMLIT_PORT:-8501}` | Streamlit analyst UI |
| `trace-viewer` | `${TRACE_VIEWER_PORT:-8502}` | separate trace browser from `app.trace_viewer` |
| `eval` | profile `eval` | one-shot `scripts/smoke_pipeline.py` |
| `local-llm-proxy` | profile `local-llm`, 11434 | Ollama runtime, pulls local Qwen models |

`deploy/benchmark-compose.yml` defines 2 services:

| service | port | responsibility |
|---|---|---|
| `benchmark-postgres` | `${BENCH_PG_PORT:-15432}` -> 5432 | benchmark DB |
| `benchmark-api` | `${BENCH_API_PORT:-18080}` -> 8080 | Benchmark Store API |

Runtime flow:

```text
browser / Streamlit / bot / curl
  -> case3_app:8000 app.api
      -> app.pipeline_service.execute_run
      -> app.orchestrator.SQLSecuritySystem.run
      -> data/traces/*.json + optional main Postgres audit tables

benchmark UI in app
  -> benchmark_api via BENCHMARK_API_URL
  -> benchmark-postgres benchmark schema
```

Evidence:
- `deploy/docker-compose.yml` - services, volumes, env and ports
- `deploy/benchmark-compose.yml` - benchmark Postgres/API services
- `docs/README.md` - service purpose and documented ports

## Functional Layer

FastAPI root app:
- `GET /health` returns current LLM mode/model info plus RAG diagnostics.
- `POST /run` executes one pipeline run and returns serialized `SystemResult.__dict__`.
- `GET /prompts/candidates` renders prompt candidate page.
- `GET /web/api/prompt-candidates` scans trace files and returns candidate metrics.
- `app.include_router(web_router)` wires chat/history/prompts/report routes.
- `app.include_router(audits_router)` wires benchmark UI/job routes.

Web-chat and report routes in current code:

| route | purpose |
|---|---|
| `/` | redirect/shell entry to chat |
| `/chat`, `/chat/new`, `/chat/{chat_id}` | chat workflow and deep links |
| `/history` | saved chat/run history |
| `/settings/prompts`, `/prompts/system` | system prompt registry editor |
| `/runs/{trace_id}` | on-the-fly report from trace JSON or saved bot HTML |
| `/web/api/config` | model catalog, judge/prompt-check config |
| `/web/api/chats*` | chat CRUD, message run, progress |
| `/web/api/system-prompts*` | prompt CRUD and default switching |
| `/web/api/traces/{trace_id}` | raw trace JSON |
| `/web/api/traces/{trace_id}/prompts` | normalized prompt timeline |

Benchmark UI routes:

| route | purpose |
|---|---|
| `/audits/runs` | batch run list and new batch controls |
| `/audits/runs/{benchmark_run_id}` | run progress/detail dashboard |
| `/audits/runs/compare` | compare selected benchmark runs |
| `/audits/batch-cases` | workbench for cases, Oracle, Judge-audit, hypotheses |
| `/settings/tariffs` | model tariff settings |

Evidence:
- command: `rg -n "@(app|router)\\.(get|post|patch|delete|put)|include_router" app/api.py app/web_chat.py app/web_audits.py benchmark_service -g '*.py'`
- `app/api.py` - root app and `/run` contract
- `app/web_chat.py` - web cabinet APIs and report route
- `app/web_audits.py` - benchmark UI routes and job APIs
- `app/templates/_shell.html` - left toolbar navigation

## Pipeline Layer

Current graph:

```text
intent_classify
  -> prompt_check
  -> retrieve
  -> generate
  -> sql_guard
  -> explain_sandbox
  -> audit
  -> decide
       | approve/abstain/refuse -> END
       | revise                 -> revise -> retrieve
```

Pipeline state fields include task, iteration, histories, prompt findings, last SQL/audit/explain, RAG context, allowed tables/columns, business requirements, policy labels, intent metadata, trace, generator and auditor.

| node | input state | output state | trace shape | failure/skip |
|---|---|---|---|---|
| `_node_intent_classify` | `task` | `intent_kind`, `intent_confidence`, `intent_anchors` | outputs intent, details matched anchors | deterministic, no LLM |
| `_node_prompt_check` | `task` | `prompt_risk_findings` | inputs enabled/backend/provider; details regex/LLM findings and prompt meta if present | disabled returns zero findings; LLM error marks unavailable |
| `_node_retrieve` | `task`, prompt findings, isolation | `last_generation_context`, `last_solutions_context`, `allowed_tables`, `allowed_columns`, `allowed_objects` | context length, allowed tables, RAG hits/sources/timings | high-risk prompt skips retrieval; v2 RAG degraded does not fail run |
| `_node_generate` | task, context, history, audit feedback, allowed objects, banned identifiers, intent | `iteration`, `sql_history`, `last_sql`, `policy_label`, `business_requirements` | SQL, candidate count, selected index, prompt/candidates/selector/AST/diff/business details | prompt risk returns deterministic sentinel SQL |
| `_node_sql_guard` | `last_sql`, context, business reqs | `last_guard_findings` | vuln count, findings, evidence, AST, business alignment | sentinel skips guard |
| `_node_explain_sandbox` | `last_sql`, guard findings | `last_explain`, `last_explain_error` | ok/skipped/plan/error/sub_timings | skips on sentinel or blocking guard labels; no DSN skips safely |
| `_node_audit` | SQL, explain error, guard findings, prompt findings | `audit_history`, `iterations_log`, `last_audit`, `approved`, `banned_identifiers` | approved/risk/vuln count plus auditor details | sentinel and early AST barrier skip model audit |
| `_node_decide` | audit, prompt findings, policy, iteration | `approved`, `decision`, `needs_human`, `human_reason`, `policy_label` | decision, needs human, risk split, reason flags | max iterations/repeat/low judge -> abstain |
| `_node_revise` | `last_audit`, `iterations_log` | updated revision notes | revision notes | best-effort saves iteration |

`SQLSecuritySystem.run` wraps graph execution, saves audit run header before graph, catches config/provider/runtime errors differently, attaches result to trace, writes final trace and saves audit run.

Evidence:
- `app/orchestrator.py` - `PipelineState`, node functions, `_build_graph`, `SQLSecuritySystem.run`
- `app/pipeline.py` - contract-compatible entry point
- `app/pipeline_service.py` - async wrapper, latency budget and per-request overrides
- `TASK-3/baseline1.py` - baseline `SystemResult`, `AuditResult`, `Vulnerability`

## Agent And Model Layer

Roles in current code:

| role | path | backend/prompt | tools | behavior |
|---|---|---|---|---|
| generator | `app/generator.py` | `llm_provider.get_llm("generator")`, `generator_system` or tool-mode prompt | optional generator tools | sync in caller thread; multi-candidate can be parallel |
| selector | `app/generator_selector.py` | deterministic, no model | `sql_guard.check`, business alignment | ranks candidates; business blockers before quality |
| sql_guard | `app/sql_guard.py` | deterministic pglast/sqlparse rules | RAG sensitive/table policy helpers | security/quality label split |
| explain_sandbox | `app/explain_sandbox.py` | no model | PostgreSQL `EXPLAIN` via read-only DSN | single statement, read-only transaction, rollback |
| auditor | `app/auditor.py` | `llm_provider.get_llm("auditor")`, `auditor_system` | classifier, security RAG, optional grouped runner | hybrid rule/model audit, JSON response expected |
| classifier stage 4 judge | `app/classifier/judge.py` | `llm_provider.get_judge_llm()`, `classifier_judge_system` | no tool calls found | semantic safety judge for selected labels |
| prompt-check judge | `app/prompt_check_llm.py` | `llm_provider.get_prompt_check_llm()`, `prompt_check_judge_system` | no tool calls found | request-ingestion safety classifier |
| smart judge | `scripts/bench_smart_judge_worker.py`, `scripts/bench_judge_existing_run.py` | benchmark backend/model payload, defaults Codex CLI in models/API | benchmark store data | offline/posthoc case scoring |
| benchmark reviewer | `scripts/bench_audit_runs.py` | reviewer backends `codex_cli`, `anthropic_cli`, `openrouter` | Benchmark Store API | suggest-only review persistence |
| oracle | `scripts/bench_oracle_existing_run.py`, `scripts/_oracle/*` | deterministic oracle dispatchers | AST/reference assertions | posthoc correctness verdicts |
| analysis job | `scripts/bench_analyze_judge_reports.py`, `app/analysis_job_supervisor.py` | default `codex_cli`/`gpt-5.5` | benchmark DB rows | stores hypotheses and evidence |

Runtime model contours in `app/llm_provider.py`:

| contour | generator backend | auditor backend |
|---|---|---|
| `dev_local` | `anthropic_cli` | `anthropic_cli` |
| `claude_cli` | `anthropic_cli` | `anthropic_cli` |
| `prod_demo` | `openrouter` | `openrouter` |
| `mixed` | `openrouter` | `anthropic_cli` |
| `local_openai` | `local_openai` | `local_openai` |

Current runtime backend set in code is `openrouter`, `local_openai`, `anthropic_cli`. `codex_cli` is present in benchmark scripts and UI defaults for posthoc jobs, but not in `SUPPORTED_BACKENDS` for live `/run`; live `codex_reasoning_effort` is accepted by request model and env wrapper, but a live Codex backend is `[unknown]` / not found in `app/llm_provider.py`.

Generator catalogs:
- OpenRouter keys include `qwen3-5-9b`, `qwen3-coder-30b-a3b`, `qwen3-235b-a22b-2507`, `qwen3-32b`, `llama-3-3-70b-instruct`, `gpt-5-4-mini`, `kimi-k2-6`, `gpt-5-4-nano`, `gemini-3-1-flash-lite`, `claude-haiku-4-5`.
- Local keys include `qwen3-5-9b`, `qwen3-8b`, `qwen-coder-7b`, `arctic-text2sql-7b`.
- Local aliases include `local-qwen3-5-9b` and `qwen/qwen3.5-9b`.

Timeout/retry:
- one LLM call timeout from `LLM_CALL_TIMEOUT_SEC`, code default 30, compose default 300;
- OpenAI-compatible client has explicit retry loop with `LLM_PROVIDER_MAX_RETRIES` default 2 and backoff `LLM_PROVIDER_RETRY_BACKOFF` default 1.5;
- CLI subprocess uses same call timeout, filtered env, optional demotion to `CLI_RUN_AS_USER`;
- CLI candidate parallelism uses `LLM_CLI_PARALLEL_CANDIDATES`, `LLM_CLI_MAX_PARALLEL`, `LLM_CLI_PARALLEL_BACKENDS`.

Evidence:
- `app/llm_provider.py` - contours, catalogs, backend selection, clients, stage4/prompt-check options
- `app/generator.py` - prompt registry, multi-candidate, tool-loop behavior
- `app/generator_selector.py` - deterministic selector and business block ranking
- `app/auditor.py` - classifier/auditor/model audit flow
- `benchmark_service/models.py` - benchmark judge/oracle/analysis start payloads
- `scripts/bench_audit_runs.py`, `scripts/bench_judge_existing_run.py`, `scripts/bench_oracle_existing_run.py`, `scripts/bench_analyze_judge_reports.py`

## Tool Layer

There are two separate tool layers.

Audit/classifier tools in `app/audit_tools.py`:

| tool | labels/purpose |
|---|---|
| `check_statement_boundary` | `MULTI_STATEMENT`, `COMMENT_TRUNCATION` |
| `check_classic_sqli` | classic SQL injection, union, tautology, time delay |
| `check_plpgsql` | PL/pgSQL, dynamic execute, privilege escalation |
| `check_mutation` | DML/DDL/truncate/copy/insert unsafe |
| `check_data_exposure` | direct sensitive, schema leak, select star |
| `check_reliability` | pagination, cross join, cost, non-sargable, recursion |
| `check_generation_quality` | hallucinated table/column, broken SQL, unsafe cast |
| `check_business_alignment` | `MISSING_REQUIRED_FILTER`, `BUSINESS_MISMATCH` |
| `judge_sensitive_exposure`, `judge_semantic_correction` | stage judge wrapper |

Generator tools in `app/tools/*.py` are OpenAI/OpenRouter function specs:

| tool | input | result |
|---|---|---|
| `check_hallucination` | SQL | unknown tables/columns and parse error |
| `get_sensitive_fields` | table list | sensitive field map |
| `explain_query` | SQL | read-only explain result |
| `get_approved_joins` | two tables | approved join keys and hint |

Tool-loop:

```text
model draft SQL
  -> assistant.tool_calls[]
  -> app.tools.dispatch(name, arguments)
  -> JSON tool result as role=tool
  -> model revises or emits final text
  -> tool_compliance: required/called/missing/last_results/ok
```

Evidence:
- `app/audit_tools.py` - LangChain `@tool` wrappers
- `app/classifier/rules.py` - Stage 1 consumes audit tools
- `app/tools/__init__.py` - generator tool registry and dispatch
- `app/tools/check_hallucination.py`
- `app/tools/get_sensitive_fields.py`
- `app/tools/explain_query.py`
- `app/tools/get_approved_joins.py`
- `app/tool_loop.py` - OpenAI tool-call loop and compliance summary

## Storage Layer

Storage split:

```text
main demo Postgres
  - GreenData public tables from deploy/postgres-init/01-schema.sql
  - audit_runs / audit_iterations
  - system_prompts

read-only audit role
  - audit_ro
  - SELECT grants
  - EXPLAIN sandbox via POSTGRES_AUDIT_DSN

benchmark Postgres
  - benchmark datasets/runs/cases/steps/llm/findings/hits/explain/raw payloads
  - benchmark.rag_embeddings
  - oracle_eval_runs / case_quality_scores / analysis hypotheses / tariffs

file storage
  - data/traces/*.json
  - data/web_chats/*.json
  - data/bot/reports/*.html and prefs
  - data/eval datasets/reports
  - data/model_cache and RAG indices volumes
```

Best-effort vs blocking:
- `audit_storage` silently skips writes when `POSTGRES_DSN` or driver is missing, and catches DB driver errors.
- `Trace.save()` is part of pipeline result path and writes final JSON; `flush_partial()` swallows `OSError`.
- `explain_sandbox` skips if DSN or `psycopg2` missing, but returns errors for invalid single statement or PostgreSQL errors.
- `prompt_registry.get_default_prompt()` falls back to files when DB unavailable; missing fallback prompt file raises.
- benchmark store API requires DB and auth token for mutating protected endpoints.

Evidence:
- `app/audit_storage.py` - best-effort Postgres writes
- `app/trace.py` - final and partial JSON file writes
- `app/web_chat.py` - chat JSON and trace reads
- `app/test_report.py` - report data from trace
- `benchmark_service/sql/*.sql` - benchmark schema
- `deploy/docker-compose.yml` - volumes and env

## PostgreSQL

Main demo DB:
- `deploy/postgres-init/01-schema.sql` creates 60 public GreenData tables in the inspected DDL.
- `deploy/postgres-init/02-audit-role.sql` creates `audit_runs`, `audit_iterations`, indexes and read-only `audit_ro`.
- `deploy/postgres-init/03-system-prompts.sql` creates `system_prompts` and one-default-per-type index.

DSN roles without copying secrets:

| env | role | use |
|---|---|---|
| `POSTGRES_DSN` | app write role from compose env | `audit_storage`, prompt registry, general app DB writes |
| `POSTGRES_AUDIT_DSN` | `audit_ro` read-only role | `explain_sandbox.run_explain` |
| `BENCHMARK_DSN` | benchmark role | RAG v2/solutions search, benchmark migrations/store |

PostgreSQL diagram:

```text
app.api / orchestrator
  |
  +-- POSTGRES_DSN ------------------> demo_db
  |                                      +-- GreenData public tables
  |                                      +-- audit_runs
  |                                      +-- audit_iterations
  |                                      +-- system_prompts
  |
  +-- POSTGRES_AUDIT_DSN ------------> demo_db
  |                                      +-- audit_ro SELECT + EXPLAIN only
  |
  +-- BENCHMARK_DSN -----------------> benchmark DB
                                         +-- benchmark.pipeline_runs
                                         +-- benchmark.llm_calls
                                         +-- benchmark.generator_candidate_metrics
                                         +-- benchmark.rag_embeddings
                                         +-- benchmark.oracle_eval_runs
                                         +-- benchmark.case_analysis_reports
```

Benchmark DB:
- base tables: `datasets`, `dataset_cases`, `benchmark_runs`, `pipeline_runs`, `pipeline_steps`, `llm_calls`, `findings`, `faiss_hits`, `explain_results`, `raw_payloads`;
- RAG: `benchmark.rag_embeddings(index_name, text, metadata, embedding, source_trace_id)`;
- posthoc: `oracle_eval_runs`, `case_quality_scores`, `analysis_jobs`, `case_analysis_reports`, `improvement_hypotheses`, `hypothesis_evidence`;
- economics: `model_tariffs`, cost fields in `llm_calls`;
- views include `temperature_candidate_summary` and `system_prompt_effectiveness`.

Evidence:
- command: `rg -n "^CREATE TABLE" deploy/postgres-init/01-schema.sql deploy/postgres-init/02-audit-role.sql deploy/postgres-init/03-system-prompts.sql benchmark_service/sql/*.sql`
- `deploy/postgres-init/02-audit-role.sql`
- `deploy/postgres-init/03-system-prompts.sql`
- `app/audit_storage.py`
- `app/explain_sandbox.py`
- `app/prompt_registry.py`
- `benchmark_service/sql/001_init.sql`
- `benchmark_service/sql/008_rag_embeddings.sql`
- `benchmark_service/sql/009_oracle_eval.sql`
- `benchmark_service/sql/019_analysis_hypotheses.sql`

## FAISS And RAG

Current RAG bundle:

```text
task text
  |
  +--> table_knowledge_v2 in benchmark.rag_embeddings
  +--> schema_overlay_v2 / schema_overlay business blocks
  +--> Marina FAISS legacy generation context and hits
  +--> optional solutions context from benchmark.rag_embeddings(index_name='solutions')
  |
  v
generation context bundle
  |
  +--> SQLGenerator prompt
  +--> schema_link allowed tables/columns
  +--> trace details.rag_sources
```

Implementation details:
- `app/rag_adapter.py` loads overlay from `deploy/schema_overlay_v2.json` first, with legacy fallback paths present in constants.
- `data/rag/v2/table_knowledge_index_v2.csv` has 62 lines: one generated-at line, one header, 60 data rows and 47 CSV fields.
- `table_knowledge_v2` search uses benchmark Postgres `benchmark.rag_embeddings`, `multilingual-e5-small`, top K 6, min similarity 0.45, max prompt chars 1500.
- Bridge filter excludes role `bridge_multiselect` by default; current CSV has 13 bridge rows.
- Legacy Marina FAISS remains under `TASK-3/marina-case3-rag/rag_pipeline/indices`: generation/security FAISS + metadata.
- Sensitive fields merge Marina, overlay `pii_tags`, and regex auto-detect unless `SENSITIVE_AUTO_DETECT` disables it.
- `schema_link` selects tables from RAG context plus lexical table/alias hits, then exposes all columns for selected tables.
- Solutions context is opt-in by isolation: `production` includes it, `clean` skips unless `USE_SOLUTIONS_LESSONS=true`.
- RAG diagnostics for UI health count `table_knowledge_v2` and `solutions` rows without loading embedder.

Fallback policy:
- v2 disabled, empty, below threshold, DB error, `psycopg2` missing or embedder failure returns empty v2 block plus meta/fallback reason, not a failed `/run`.
- legacy FAISS context can act as fallback when v2 yields no hits.
- cache: `get_generation_context_bundle` uses `lru_cache(maxsize=128)` by task; several RAG helpers are cached/timed.

Evidence:
- `app/rag_adapter.py` - RAG functions, caches, v2 search, bridge filter, sensitive merge
- `data/rag/v2/README.md`
- command: `wc -l data/rag/v2/table_knowledge_index_v2.csv`
- command: `awk ... data/rag/v2/table_knowledge_index_v2.csv` - 47 fields and 13 bridge rows
- `scripts/build_rag.sh`
- `scripts/build_table_knowledge_v2.py`
- `scripts/build_sensitive_inventory.py`
- `deploy/schema_overlay.json`, `deploy/schema_overlay_v2.json`
- `TASK-3/marina-case3-rag/rag_pipeline/`

## Trace And Observability

Trace contract:
- trace id format: UTC timestamp `YYYYMMDDTHHMMSS` plus 8 hex chars, e.g. `20260525T..._abcdefgh`;
- top-level: `request_id`, `task`, `started_at`, `finished_at`, `duration_sec`, `events`, `result`, `error`;
- event: `node`, `started_at`, `inputs`, `outputs`, `details`, `duration_sec`;
- partial trace adds `partial: true` and is atomically written by `.tmp` + replace after each node.

Trace/report lineage:

```text
orchestrator node event
  -> Trace.step(...)
  -> Trace.flush_partial()
  -> data/traces/<trace_id>.json
  -> /web/api/chats/{chat_id}/progress
  -> /runs/{trace_id}
  -> app.test_report.render()
  -> prompt timeline / RAG drawers / selector candidates / business cards
  -> optional benchmark ingest
```

Observability surfaces:
- `/runs/{trace_id}` renders canonical HTML from trace JSON or returns prebuilt bot report if it exists.
- `/web/api/traces/{trace_id}/prompts` returns prompt timeline from `prompt_trace`.
- `test_report` builds RAG blocks, business alignment metrics, selector candidate views, latency drawers and prompt timeline.
- benchmark ingest normalizes trace into steps, LLM calls, findings, hits, explain and candidate metrics.

Evidence:
- `app/trace.py` - request id, event structure, partial/final writes
- `app/orchestrator.py` - trace events in each node
- `app/prompt_trace.py` - prompt timeline normalization
- `app/test_report.py` - report data and drawers
- `app/web_chat.py` - `/runs/{trace_id}`, progress and prompt routes
- `benchmark_service/ingest.py` - trace normalization into benchmark tables
- `tests/test_trace_flush_partial.py`
- `tests/test_run_detail_report.py`

## Prompt Registry

Registry data model:
- table `system_prompts`: `id`, `prompt_type`, `version`, `name`, `text`, `text_sha256`, `status`, `is_default`, author/notes/timestamps;
- partial unique index enforces one active default per prompt type;
- active prompt text is effectively versioned; UI save-as-default creates a new default version.

Current prompt types in `PROMPT_FILES`:

| prompt_type | fallback file |
|---|---|
| `generator_system` | `generator_system.txt` |
| `generator_tool_mode_system` | `generator_tool_mode_system.txt` |
| `generator_tools_system` | `generator_system_tools.txt` |
| `auditor_system` | `auditor_system.txt` |
| `semantic_judge_system` | `semantic_judge_system.txt` |
| `quality_reviewer_system` | `bench_reviewer_system.txt` |
| `bench_reviewer_system` | `bench_reviewer_system.txt` |
| `bench_reviewer_user` | `bench_reviewer_user.txt` |
| `classifier_judge_system` | `classifier_judge_system.txt` |
| `prompt_check_judge_system` | `prompt_check_judge_system.txt` |
| `case_quality_judge_system` | `case_quality_judge_system.txt` |
| `judge_audit_hypothesis_system` | `judge_audit_hypothesis_system.txt` |

Runtime contract:

```text
generator/auditor/judge/checker
  -> prompt_registry.get_default_prompt(prompt_type)
  -> DB active default OR file fallback
  -> LLM call
  -> trace details.prompt_meta + prompt_request_sha256
```

Fallback reasons include DB disabled/not configured/driver missing/DB unavailable/default not found. Missing fallback file is blocking.

Evidence:
- `app/prompt_registry.py` - schema SQL, prompt types, fallback, CRUD
- `deploy/postgres-init/03-system-prompts.sql`
- `app/generator.py` - `generator_system`, `generator_tool_mode_system`
- `app/auditor.py` - `auditor_system`
- `app/prompt_check_llm.py`
- `app/classifier/judge.py`
- `app/web_chat.py` - prompt registry endpoints
- `tests/test_system_prompt_registry.py`
- `tests/test_prompt_trace.py`

## Benchmark And Audit Runs

Benchmark Store purpose:
- accepts run payloads with trace/system_result/report_data/case/model metadata;
- stores raw payload and normalized rows;
- provides run/case list, detail, compare, tariffs, metrics, audit reviews, Oracle and analysis endpoints;
- launches runner/smart-judge/oracle/analysis subprocesses.

Main benchmark APIs:

| path | purpose |
|---|---|
| `/v1/benchmarks/runs` | start/list batch runs |
| `/v1/benchmarks/runs/{id}` | run detail |
| `/v1/benchmarks/runs/{id}/progress` | progress |
| `/v1/benchmarks/runs/compare` | compare runs |
| `/v1/benchmarks/cases` | case list/filter |
| `/v1/benchmarks/cases/{trace_id}` | case detail |
| `/v1/ingest/run`, `/v1/ingest/batch` | ingest traces |
| `/v1/audit/targets`, `/v1/audit/reviews` | reviewer flow |
| `/v1/tariffs` | model tariff CRUD |

Background jobs:
- Smart Judge: `/web/api/benchmarks/runs/{id}/judge/start|status|abort` starts `scripts/bench_judge_existing_run.py`.
- Oracle: `/web/api/benchmarks/runs/{id}/oracle/start|status|abort` starts `scripts/bench_oracle_existing_run.py`.
- Analysis: `/web/api/benchmarks/runs/{id}/analysis/start|status|abort` starts `scripts/bench_analyze_judge_reports.py`.
- `app.api` startup lifespan calls `reconcile_orphan_jobs()` for analysis, judge and oracle supervisors.

Posthoc mechanics:
- Smart Judge scores cases and writes `benchmark.case_quality_scores`.
- Oracle loads golden cases, dispatches assertion logic from `scripts/_oracle/*`, writes `oracle_eval_runs`.
- Analysis reads smart-judge + Oracle inputs and writes `case_analysis_reports`, `improvement_hypotheses`, `hypothesis_evidence`.

Evidence:
- `benchmark_service/api.py`
- `benchmark_service/models.py`
- `benchmark_service/ingest.py`
- `benchmark_service/runner_supervisor.py`
- `app/web_audits.py`
- `app/judge_job_supervisor.py`
- `app/oracle_job_supervisor.py`
- `app/analysis_job_supervisor.py`
- `scripts/bench_run_dataset.py`
- `scripts/bench_smart_judge_worker.py`
- `scripts/bench_judge_existing_run.py`
- `scripts/bench_oracle_existing_run.py`
- `scripts/bench_analyze_judge_reports.py`
- `docs/benchmark_store.md`
- `docs/bench_audit_feedback.md`

## Security And Business Alignment

Security controls:
- prompt risk precheck combines deterministic regex findings and optional LLM prompt-check judge;
- SQL guard uses pglast/sqlparse, forbidden AST checks, sensitive field policy, business alignment and label taxonomy;
- `explain_sandbox` enforces one statement and `SET TRANSACTION READ ONLY`;
- auditor merges classifier/rules/LLM audit and blocks only security bucket labels, while quality labels can become advisory approval;
- sentinel SQL is used for refusal policies and skips downstream quality audit/guard noise.

Business alignment after select-winner fix:
- `business_alignment.extract_requirements()` extracts `time_range`, `status_filter`, `group_by`;
- business labels are `MISSING_REQUIRED_FILTER` and `BUSINESS_MISMATCH`;
- `generator_selector` treats business blockers before security/quality counts and reports `selector_reason`;
- `sql_guard` includes business labels in `SECURITY_LABELS`, so they block;
- trace/report/UI surfaces include `business_requirements`, `business_alignment_findings`, `business_alignment_labels`, `selector_reason`;
- prompt candidate API exposes business labels for chart/card badges.

Evidence:
- `app/business_alignment.py`
- `app/generator_selector.py`
- `app/sql_guard.py`
- `app/audit_tools.py`
- `app/classifier/rules.py`
- `app/orchestrator.py`
- `app/api.py` - prompt candidate rows
- `app/test_report.py` - business alignment card/drawer
- `tests/test_business_alignment.py`
- `tests/test_generator_selector_business_alignment.py`
- `tests/test_prompt_candidates_api.py`

## UI / личный кабинет

Navigation:

```text
left toolbar
  +-- Chat              /chat
  +-- History           /history
  +-- Prompts           /settings/prompts
  +-- Prompt Runs       /prompts/candidates
  +-- Runs              /audits/runs
  +-- Cases             /audits/batch-cases
  +-- Tariffs           /settings/tariffs
```

Page inventory:

| page | template | assets | data/actions |
|---|---|---|---|
| `/chat` | `web_chat.html` | `web_chat.css`, `web_chat.js`, shared CSS/JS | create chat, send message, model/judge/prompt-check config, copy SQL, open report/prompts |
| `/chat/{chat_id}` | `web_chat.html` | same | load chat by URL, render previous messages, progress polling |
| `/history` | `web_chat.html` | same | list chats, filters/search in JS |
| `/settings/prompts` | `web_chat.html` | same | prompt registry CRUD APIs |
| `/prompts/candidates` | `prompt_candidates.html` | [unknown from inspected snippets] | `/web/api/prompt-candidates`, chart/candidate cards |
| `/runs/{trace_id}` | `test_report.html` or `run_detail_placeholder.html` | `test_report.*` | read trace/report, prompt timeline, JSON drawers |
| `/audits/runs` | `audit_runs.html` | `audit_run_compare.css/js`, `web_chat.css` | list/start benchmark runs |
| `/audits/runs/{benchmark_run_id}` | `audit_run_detail.html` | `run_detail_per_stage.js`, `run_detail_insights.js`, audit CSS | progress, start/abort smart-judge/oracle/analysis, rerun failed, export CSV |
| `/audits/runs/compare` | `audit_run_compare.html` | `audit_run_compare.js/css` | compare runs, export comparison CSV |
| `/audits/batch-cases` | `batch_cases.html` | `batch_cases.js/css` | case workbench, Oracle panel, Judge-audit, hypotheses, run report |
| `/settings/tariffs` | `settings_tariffs.html` | `settings_tariffs.js`, audit CSS | tariff list/upsert/delete via Benchmark API |
| trace viewer `/` and `/trace/{id}` | `trace_index.html`, `trace_detail.html` | trace-viewer app | separate service, reads traces directory |

`/import` route is `[unknown]` / not found in current decorators. `docs/README.md` says manual `/import` removed.

Evidence:
- `app/templates/_shell.html`
- `app/templates/web_chat.html`
- `app/templates/prompt_candidates.html`
- `app/templates/audit_runs.html`
- `app/templates/audit_run_detail.html`
- `app/templates/audit_run_compare.html`
- `app/templates/batch_cases.html`
- `app/templates/settings_tariffs.html`
- `app/templates/trace_index.html`
- `app/templates/trace_detail.html`
- `app/static/web/web_chat.js`
- `app/static/audit_reviews/*.js`
- `app/web_chat.py`
- `app/web_audits.py`
- `app/trace_viewer.py`

## Data Contracts

Core contracts:

| contract | path | fields/shape |
|---|---|---|
| `Vulnerability` | `TASK-3/baseline1.py` | `vuln_class`, `risk_score`, `description`, `recommendation`, `line_hint` |
| `AuditResult` | `TASK-3/baseline1.py` | `approved`, `vulnerabilities`, `overall_risk_score`, `summary` |
| `IterationLog` | `TASK-3/baseline1.py` | timestamp, iteration, SQL, audit result, revision notes |
| `SystemResult` | `TASK-3/baseline1.py` | final SQL, approved, iterations, audit log, metadata |
| `RunRequest` | `app/api.py` | task, model overrides, judge/prompt-check overrides, iterations, profile, isolation |
| `/run` response | `app/pipeline_service.py` | serialized dataclass preserving nested fields |
| `PromptRecord` | `app/prompt_registry.py` | id/type/version/text/hash/status/default/source/fallback |
| trace event | `app/trace.py` | node/start/inputs/outputs/details/duration |
| prompt candidate row | `app/api.py` | trace/candidate/model/temp/prompt/sql/quality/business fields |
| `RunPayload` | `benchmark_service/models.py` | trace id, benchmark run, dataset/case/model, system result, trace, report data |
| benchmark start payloads | `benchmark_service/models.py` | run/judge/oracle/analysis start schemas |

Evidence:
- `TASK-3/baseline1.py`
- `app/api.py`
- `app/pipeline_service.py`
- `app/prompt_registry.py`
- `app/trace.py`
- `benchmark_service/models.py`
- `benchmark_service/ingest.py`

## Configuration And Env

Key env groups:

| group | env examples | default/source |
|---|---|---|
| latency | `LATENCY_SOFT_SEC`, `LATENCY_HARD_SEC`, `LLM_CALL_TIMEOUT_SEC` | code 300/600/30; compose sets LLM timeout 300 |
| LLM mode | `LLM_MODE`, `LLM_GENERATOR_MODEL`, `LLM_BACKEND_GENERATOR`, `LLM_BACKEND_AUDITOR` | `prod_demo`, `qwen3-8b` |
| OpenRouter | `OPENROUTER_BASE_URL`, `OPENROUTER_API_KEY`, provider-only envs | base default official OpenRouter URL |
| local LLM | `LOCAL_LLM_BASE_URL`, `LOCAL_LLM_API_KEY`, `LOCAL_LLM_USE_NATIVE_OLLAMA` | compose local proxy |
| stage judge | `STAGE_4_ENABLED`, `STAGE_4_BACKEND`, `STAGE_4_OPENROUTER_PROVIDER_ONLY` | compose enables stage 4 |
| prompt-check | `PROMPT_CHECK_LLM_ENABLED`, backend/model/provider envs | compose enables local qwen2.5 0.5B |
| generation | `LLM_MULTI_CANDIDATE`, `LLM_PARALLEL_CANDIDATES`, `LLM_GENERATOR_TEMPERATURES`, `GENERATOR_TOOL_MODE` | multi true; compose temps `0.3,0.6` |
| RAG | `TABLE_KNOWLEDGE_V2_ENABLED`, `RAG_BRIDGE_BLACKLIST_ROLES`, `USE_SOLUTIONS_LESSONS`, `SENSITIVE_AUTO_DETECT` | v2 and bridge filter enabled by compose |
| DB | `POSTGRES_DSN`, `POSTGRES_AUDIT_DSN`, `BENCHMARK_DSN` | compose builds from env placeholders |
| UI/files | `TRACES_DIR`, `WEB_CHAT_DIR`, `BOT_REPORT_DIR`, `TRACE_VIEWER_URL` | compose volumes |
| benchmark | `BENCHMARK_API_URL`, `BENCHMARK_INGEST_TOKEN`, `BENCHMARK_API_TOKEN`, `BENCHMARK_MAX_BODY_MB` | benchmark env/compose |
| jobs | `SMART_JUDGE_DEFAULT_BACKEND`, `SMART_JUDGE_DEFAULT_MODEL`, `BENCHMARK_RUNNER_API_URL` | scripts/API defaults |

No secret values are copied in this document. DSNs are documented by variable name and role only.

Evidence:
- `deploy/docker-compose.yml`
- `deploy/benchmark-compose.yml`
- `deploy/env.example`
- `deploy/benchmark.env.example`
- `app/llm_provider.py`
- `app/pipeline_service.py`
- `app/rag_adapter.py`
- `app/web_chat.py`
- `app/web_audits.py`
- `benchmark_service/auth.py`

## Tests And Verification

Test groups in checkout:
- business alignment and selector: `test_business_alignment.py`, `test_generator_selector_business_alignment.py`;
- UI/API shell: `test_web_shell.py`, `test_web_chat.py`, `test_chat_progress_endpoint.py`, `test_run_detail_report.py`;
- benchmark UI/API/store: `test_audit_reviews_ui_api.py`, `test_benchmark_service_api.py`, candidate/tariff/cost tests;
- RAG: `test_rag_bridge_filter.py`, `test_rag_context_bundle.py`, `test_rag_table_knowledge_v2.py`;
- tools: `test_phase5_tools.py`, `test_phase5_tool_loop.py`, `test_tool_mode_matrix_report.py`;
- LLM/provider config: `test_stage4_backend_select.py`, `test_provider_matrix_security.py`, CLI/local lifecycle tests;
- Oracle/smart judge: `test_oracle_*`, `test_smart_judge_*`, `test_tz21_oracle_cases_analysis.py`;
- SQL guard/AST: `test_ast_*`, `test_sql_guard_integration.py`, golden fixes;
- prompt registry/trace: `test_system_prompt_registry.py`, `test_prompt_trace.py`, `test_prompt_candidates_api.py`.

Required targeted commands for this docs branch:

```bash
pytest tests/test_business_alignment.py tests/test_generator_selector_business_alignment.py tests/test_prompt_candidates_api.py
pytest tests/test_web_shell.py tests/test_web_chat.py tests/test_run_detail_report.py
pytest tests/test_audit_reviews_ui_api.py tests/test_benchmark_service_api.py
```

Current execution status: to be filled after test run in this iteration.

Evidence:
- command: `rg -n "def test_|class Test" tests/*.py | sed -E 's/:.*//' | sort | uniq -c`
- `tests/test_business_alignment.py`
- `tests/test_generator_selector_business_alignment.py`
- `tests/test_prompt_candidates_api.py`
- `tests/test_web_shell.py`
- `tests/test_web_chat.py`
- `tests/test_run_detail_report.py`
- `tests/test_audit_reviews_ui_api.py`
- `tests/test_benchmark_service_api.py`

## Known Gaps

| gap | evidence | impact |
|---|---|---|
| live graph starts with `intent_classify`, while user-provided expected diagram starts at `prompt_check` | `app/orchestrator.py` graph edges | docs must use current code, not old expected diagram |
| live `/import` route not found | route `rg`; `docs/README.md` says removed | older specs mentioning `/import` are stale |
| Codex live backend not present in `SUPPORTED_BACKENDS` | `app/llm_provider.py` | Codex is benchmark/posthoc path, not confirmed for `/run` |
| `app/rag_adapter.py` is modified in working tree | `git status --short` | RAG behavior may differ from last commit |
| many benchmark/UI files are untracked | `git status --short` | current-state doc covers working tree, not committed baseline |
| prompt candidate template assets not fully traced in this audit | `[unknown]` | page route/API verified; detailed JS/CSS mapping may need deeper UI audit |
| old specs mention web import and Codex CLI runtime | `.cursor/arch-spec/*` vs code | stale specs should be updated separately |
| no screenshots were captured for UI responsiveness | no `/playwright-ui-test` requested for screenshots | no responsive correctness claim is made |

Bug-to-file quick map:

| symptom | start reading |
|---|---|
| `/run` returns 400/503/504 | `app/api.py`, `app/pipeline_service.py`, `app/llm_provider.py` |
| pipeline decision is wrong | `app/orchestrator.py`, `app/auditor.py`, `app/sql_guard.py` |
| selected SQL misses business filter | `app/business_alignment.py`, `app/generator_selector.py`, `app/sql_guard.py` |
| RAG suggests bridge tables | `app/rag_adapter.py`, `data/rag/v2/table_knowledge_index_v2.csv` |
| prompt version missing in report | `app/prompt_registry.py`, `app/prompt_trace.py`, `app/test_report.py` |
| `/chat` progress hangs | `app/web_chat.py`, `app/trace.py`, `data/traces/<id>.json` |
| benchmark run stuck | `benchmark_service/runner_supervisor.py`, `app/*_job_supervisor.py`, `data/bench/logs` |
| Oracle rows empty | `scripts/bench_oracle_existing_run.py`, `scripts/_oracle/*`, `benchmark_service/sql/009_oracle_eval.sql` |
| Judge-audit hypotheses empty | `scripts/bench_analyze_judge_reports.py`, `benchmark_service/sql/019_analysis_hypotheses.sql` |
| EXPLAIN is skipped/error | `app/explain_sandbox.py`, `POSTGRES_AUDIT_DSN`, `deploy/postgres-init/02-audit-role.sql` |

Troubleshooting checklist:
- check `git status --short` before comparing behavior with committed specs;
- check `/health` for LLM mode, model and RAG diagnostics;
- inspect `data/traces/<trace_id>.json` before changing pipeline code;
- inspect `details.rag_sources` for v2/legacy/solutions fallback reasons;
- inspect `details.selector_scores` and `business_alignment_findings` for candidate ranking;
- for benchmark UI, compare app API token env with Benchmark Store token env;
- for stuck jobs, run startup or call status endpoints to trigger reaping/reconcile;
- for DB failures, separate main `POSTGRES_DSN`, audit `POSTGRES_AUDIT_DSN`, benchmark `BENCHMARK_DSN`.

Evidence:
- `git status --short`
- `app/orchestrator.py`
- `app/llm_provider.py`
- `app/web_chat.py`
- `docs/README.md`
- `.cursor/arch-spec/L2/modules/llm-provider.md`
- `.cursor/arch-spec/L2/modules/web-chat.md`
- `.cursor/arch-spec/L3/runtime/benchmark-audit-runtime.md`

## Appendix: ASCII diagrams

Runtime flow:

```text
User / analyst
  |
  +--> Web UI /chat /history /settings/prompts /prompts/candidates
  |      |
  |      v
  +--> FastAPI app.api
         |
         +--> pipeline_service.execute_run
         |      |
         |      v
         |   orchestrator.SQLSecuritySystem.run
         |      |
         |      +--> intent_classify
         |      +--> prompt_check
         |      +--> retrieve
         |      +--> generate
         |      +--> sql_guard
         |      +--> explain_sandbox
         |      +--> audit
         |      +--> decide
         |      +--> revise -> retrieve
         |
         +--> web_chat routes and progress
         +--> web_audits routes and job supervisors
         +--> prompt candidates API
         +--> settings/prompts API
```

Storage:

```text
data/traces/*.json
  |
  +--> /runs/{trace_id}
  +--> Benchmark ingest -> benchmark.pipeline_runs / steps / llm_calls / findings

POSTGRES_DSN -> demo_db
  +-- GreenData public tables
  +-- audit_runs / audit_iterations
  +-- system_prompts

POSTGRES_AUDIT_DSN -> demo_db read-only audit_ro
  +-- EXPLAIN only

BENCHMARK_DSN -> benchmark DB
  +-- benchmark.rag_embeddings
  +-- benchmark oracle/smart-judge/analysis tables
```

RAG:

```text
task text
  |
  +-- table_knowledge_v2 -> benchmark.rag_embeddings
  +-- schema_overlay_v2 -> allowed tables/columns and policy
  +-- Marina FAISS -> generation/security hits
  +-- solutions lessons -> benchmark.rag_embeddings(index_name='solutions')
  |
  v
generation context + rag_sources diagnostics
```

UI navigation:

```text
_shell.html
  |
  +-- Chat          -> /chat
  +-- History       -> /history
  +-- Prompts       -> /settings/prompts
  +-- Prompt Runs   -> /prompts/candidates
  +-- Runs          -> /audits/runs
  +-- Cases         -> /audits/batch-cases
  +-- Tariffs       -> /settings/tariffs
```

Trace to benchmark lineage:

```text
Trace.step event
  -> data/traces/<trace_id>.json
  -> app.test_report.build_report_data
  -> BenchmarkClient.ingest
  -> benchmark.raw_payloads
  -> benchmark.pipeline_steps / llm_calls / findings / faiss_hits / explain_results
  -> benchmark UI pages and compare reports
```

Evidence:
- `app/orchestrator.py`
- `app/trace.py`
- `app/test_report.py`
- `benchmark_service/ingest.py`
- `app/templates/_shell.html`
- `app/rag_adapter.py`
