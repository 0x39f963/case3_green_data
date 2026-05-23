# Case 3 SQL Security System

Система генерирует SQL по текстовой задаче аналитика и проверяет запрос на
ошибки безопасности до выдачи результата. Основной сценарий: пользователь
пишет задачу, pipeline подбирает контекст схемы и RAG-подсказки, генерирует
SQL, прогоняет deterministic guard, EXPLAIN-песочницу и LLM-аудитор, после
чего либо одобряет запрос, либо переписывает его до безопасного варианта.

## Компоненты

| Компонент | Папки и файлы | Ответственность |
|---|---|---|
| Customer contract | `TASK-3/baseline1.py` | Базовый контракт классов и результата, совместимый с заданием. |
| Marina RAG | `TASK-3/marina-case3-rag/` | Схема БД, компактная схема, PostgreSQL/RAG knowledge base и builder индексов. |
| Schema overlay | `deploy/schema_overlay.json`, `deploy/postgres-init/01-schema.sql` | Бизнес-слой поверх DDL и SQL-инициализация хранилища. |
| Prompt layer | `app/prompts/`, `app/prompt_registry.py` | System/user prompts, реестр промптов и seed-скрипт. |
| Prompt check | `app/prompt_check.py`, `app/prompt_check_llm.py` | Предварительная проверка пользовательской задачи на prompt injection и policy bypass. |
| Intent classifier | `app/intent_classifier.py` | Быстрая классификация намерения запроса без LLM. |
| SQL parser and guard | `app/sql_parsing.py`, `app/sql_guard.py`, `app/audit_tools.py`, `app/sensitive_detector.py` | AST-разбор SQL, deterministic security rules, признаки sensitive data. |
| Classifier | `app/classifier/` | Rule/ML/encoder ensemble и LLM judge для классов риска. |
| RAG adapter | `app/rag_adapter.py` | Выбор релевантного schema/security контекста для generator и auditor. |
| Generator | `app/generator.py`, `app/generator_selector.py` | Генерация SQL, multi-candidate режим и выбор лучшего кандидата. |
| Tool loop | `app/tool_loop.py`, `app/tools/` | Agentic tool-calling режим: проверка hallucination, sensitive fields, approved joins и EXPLAIN. |
| Auditor | `app/auditor.py` | Hybrid-аудит SQL: deterministic findings + LLM security review. |
| Orchestrator | `app/orchestrator.py`, `app/pipeline.py`, `app/pipeline_service.py` | LangGraph pipeline, customer-compatible entrypoint и async-сервисный слой. |
| HTTP API | `app/api.py` | FastAPI endpoints `GET /health` и `POST /run`. |
| Trace and storage | `app/trace.py`, `app/audit_log.py`, `app/audit_storage.py`, `app/prompt_trace.py` | JSON-трассы, audit log, prompt trace и optional PostgreSQL persistence. |
| Eval | `data/eval/`, `scripts/`, `tests/` | Golden datasets, smoke scripts, offline eval и regression tests. |

## Как работает pipeline

```text
user task
  |
  v
intent_classify
  |
  v
prompt_check
  |
  v
retrieve RAG context
  |
  v
generate SQL
  |
  v
sql_guard
  |
  v
EXPLAIN sandbox
  |
  v
hybrid audit
  |
  v
decide
  |---- approved/refusal ----> SystemResult
  |
  |---- revise --------------> retrieve RAG context
```

Цикл ограничен `max_iterations` и по контракту не должен уходить в
бесконечное исправление. Каждый шаг пишет структурированное событие в trace,
чтобы можно было восстановить, какой контекст был подан модели, какой SQL
получился и почему он был одобрен или отклонен.

## Структура репозитория

```text
case3/
|-- README.md
|-- requirements.txt
|-- TASK-3/
|   |-- baseline1.py
|   `-- marina-case3-rag/
|       |-- README.md
|       |-- data_model.sql
|       |-- schema.json
|       |-- schema_compact.json
|       |-- docs/
|       `-- rag_pipeline/
|           |-- build_indices.py
|           |-- fetch_pg_docs.py
|           |-- rag_tools.py
|           |-- schema_parser.py
|           `-- knowledge_base/
|-- app/
|   |-- api.py
|   |-- pipeline.py
|   |-- pipeline_service.py
|   |-- orchestrator.py
|   |-- llm_provider.py
|   |-- generator.py
|   |-- generator_selector.py
|   |-- auditor.py
|   |-- rag_adapter.py
|   |-- sql_guard.py
|   |-- sql_parsing.py
|   |-- explain_sandbox.py
|   |-- prompt_check.py
|   |-- prompt_check_llm.py
|   |-- tool_loop.py
|   |-- tools/
|   |-- classifier/
|   |-- prompts/
|   `-- static/audit_reviews/sql_event_specs.json
|-- data/
|   `-- eval/
|       |-- dataset.schema.json
|       |-- dataset_v0_1.jsonl
|       |-- dataset_v1_0.jsonl
|       |-- golden_*.jsonl
|       |-- golden_dataset_*.csv
|       |-- redteam_holdout.jsonl
|       `-- regression_cases.jsonl
|-- deploy/
|   |-- schema_overlay.json
|   `-- postgres-init/01-schema.sql
|-- scripts/
|   |-- dataset_build.py
|   |-- eval_common.py
|   |-- eval_dataset.py
|   |-- regression_add.py
|   |-- seed_system_prompts.py
|   |-- smoke_pipeline.py
|   |-- smoke_step2.py
|   `-- smoke_step3.py
`-- tests/
    |-- fixtures/
    `-- test_*.py
```

## Коммиты по слоям

История разбита так, чтобы каждый слой можно было смотреть отдельно:

```text
chore: bootstrap repo with readme and requirements
feat(contracts): baseline contract and vuln taxonomy
feat(schema): rag schema, business overlay, ddl
feat(ast-guard): pglast parsing and deterministic guard
feat(prompts): system and user prompts with registry seed
feat(prompt-check): prompt injection guard layer
feat(classifier): staged classifier and llm judge
feat(rag-adapter): generation and security rag adapter
feat(generator): sql generator and candidate selector
feat(auditor): hybrid security auditor
feat(orchestrator): pipeline runtime api and traces
feat(eval): golden datasets eval scripts smoke and tests
docs: rewrite public readme and close clone gaps
```

## Сервисные контейнеры и контуры

| Контур | Ответственность |
|---|---|
| `app` | FastAPI-приложение. Принимает `/run`, запускает orchestrator, возвращает `SystemResult`, пишет trace. |
| `postgres` | Хранение prompt registry, audit storage и служебных сущностей. DDL лежит в `deploy/postgres-init/01-schema.sql`. |
| `local-llm-proxy` | Optional OpenAI-compatible endpoint для локальных моделей. Используется, если выбран local LLM contour. |
| `external-llm` | OpenRouter/OpenAI-compatible провайдер для generator, auditor и judge. |
| `eval-runner` | Одноразовый job/процесс для `scripts/eval_dataset.py`, smoke scripts и `pytest`. |

Имена контейнеров могут отличаться в конкретном окружении, но роли остаются
такими: API выполняет pipeline, PostgreSQL хранит состояние, LLM-контур
отвечает за model calls, eval-runner проверяет качество.

## Быстрый запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Для live LLM-режима нужен ключ провайдера:

```bash
export LLM_MODE=prod_demo
read -rsp "OpenRouter key: " OPENROUTER_API_KEY
export OPENROUTER_API_KEY
echo
```

Запуск API:

```bash
uvicorn app.api:app --host 0.0.0.0 --port 8000
```

Проверка:

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"task":"Покажи активных сотрудников по подразделениям","max_iterations":2}'
```

Deterministic smoke без реального LLM:

```bash
SMOKE_FAKE_LLM=1 python3 scripts/smoke_pipeline.py
python3 scripts/smoke_step2.py
python3 scripts/smoke_step3.py
```

Тесты:

```bash
python3 -m pytest tests -q
```

## Основные настройки

| Переменная | Назначение |
|---|---|
| `LLM_MODE` | Общий контур выбора backend/model preset. |
| `OPENROUTER_API_KEY` | Ключ для OpenRouter-compatible live вызовов. |
| `OPENROUTER_BASE_URL` | Base URL OpenRouter-compatible API, если нужен override. |
| `LLM_GENERATOR_MODEL` | Override модели генератора. |
| `STAGE_4_ENABLED` | Включение LLM judge/auditor stage. |
| `PROMPT_CHECK_LLM_ENABLED` | Включение LLM-части prompt-check. |
| `GENERATOR_TOOL_MODE` | Включение tool-calling режима генератора. |
| `POSTGRES_DSN` | Optional DSN для prompt registry/audit storage. |
| `TRACES_DIR` | Каталог JSON-трасс. По умолчанию `/app/data/traces`. |

## Eval и качество

Golden и regression наборы лежат в `data/eval/`. Основные команды:

```bash
python3 scripts/eval_dataset.py
python3 scripts/eval_dataset.py --enforce-gate
python3 scripts/dataset_build.py --help
```

Eval проверяет recall по критичным классам, false positive budget на safe
readonly кейсах, latency и наличие evidence spans. Smoke scripts покрывают
контракт pipeline, taxonomy prompt/sql checks и RAG/tool-loop интеграцию.
