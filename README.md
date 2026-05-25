# Case 3 SQL Security System

Система генерирует SQL по текстовой задаче аналитика и проверяет запрос на
ошибки безопасности до выдачи результата. Основной сценарий: пользователь
пишет задачу, pipeline подбирает контекст схемы и RAG-подсказки, генерирует
SQL, прогоняет deterministic guard, EXPLAIN-песочницу и LLM-аудитор, после
чего либо одобряет запрос, либо переписывает его до безопасного варианта.

## ВАЖНО: руководство по установке

Полная инструкция по установке и запуску проекта находится здесь:

**https://docs.google.com/document/d/1uwaJmfR2Oj1zPNOFoAdLo6NMALZHyrof-MwC7H39CYs/edit?tab=t.0**

Видео-документация и записи демонстраций находятся здесь:

**https://drive.google.com/drive/folders/1JDI-x89BRgNCVdKpRc6MjqfjRAxaqMAH?usp=sharing**

Скриншоты интерфейса в репозитории:

**[`docs/defense/screenshots/`](docs/defense/screenshots/)**

## Документация по запуску

Каноническая инструкция для новой машины лежит в [`docs/README.md`](docs/README.md):
там описаны compose-сервисы, env-файлы, сборка, RAG init, local Ollama и
smoke-проверки. Для локальных Qwen/Claude CLI/Codex CLI см. отдельно
[`docs/local-llm-setup.md`](docs/local-llm-setup.md). Реальные ключи и auth
директории не хранятся в репозитории: `.env`, `deploy/benchmark.env`,
`~/.claude`, `~/.codex` и `~/.claude.json` остаются только на машине деплоя.

## Что сделано в проекте

Проект решает задачу безопасной генерации SQL для аналитика. Пользователь
пишет обычный текстовый запрос, система строит SQL, проверяет его на ошибки,
объясняет найденные риски и при необходимости перегенерирует запрос.

| Критерий | Что сделано |
|---|---|
| Точность генерации SQL | Добавлен отдельный измеритель Execution Accuracy: `scripts/eval_execution_accuracy.py`. Он запускает сгенерированный SQL и эталонный SQL на тестовой PostgreSQL и сравнивает результат. Целевой порог в коде - 70%. Последний локальный файл `data/eval/execution_accuracy/ea_20260519T190500_summary.json` фиксирует таймауты локальной модели, поэтому его нельзя использовать как успешное подтверждение 70%; для финальной защиты нужен свежий прогон на рабочем backend. |
| Покрытие классов уязвимостей | Судья и guard покрывают больше 5 классов: SQL injection, DML/DDL, раскрытие чувствительных данных, ошибки схемы, тяжелые запросы, prompt injection и техническую неопределенность. |
| Итеративный цикл | Если судья нашел проблему, генератор получает замечания и историю предыдущего SQL. Следующая итерация строится с учетом запретов, найденных классов риска и подсказок по исправлению. |
| Аналитика и отчетность | В trace и web-интерфейсе показываются риск по итерациям, число итераций до одобрения, найденные классы риска, итоговый SQL, причины блокировки и детали EXPLAIN. |
| Прозрачность для пользователя | Лог аудита написан в формате, понятном аналитику: что было найдено, почему это риск, что исправлено и почему финальный запрос можно принять или нужно отправить человеку. |
| Воспроизводимость | В репозиторий добавлены Docker/Compose, init SQL, example env-файлы без секретов, smoke-скрипты, тесты и документация. Реальные ключи остаются на машине запуска. |
| Архитектурное обоснование | Архитектура разделяет генерацию, проверку правил, LLM-аудит, выполнение `EXPLAIN`, хранение трасс и UI. Это позволяет отдельно улучшать качество SQL, безопасность и отчетность. |

### Измеренные артефакты качества

| Артефакт | Что показывает |
|---|---|
| [`data/eval/reports/case3_sqlsec_eval_20260518_001000.json`](data/eval/reports/case3_sqlsec_eval_20260518_001000.json) | 780 кейсов, verdict `PASS`, critical recall по ключевым security-классам = 100%, evidence span hit rate около 99.2%, false negative по critical-классам отсутствуют. |
| [`data/eval/reports/encoder_compare_v1_v2_golden_20260520_133601.json`](data/eval/reports/encoder_compare_v1_v2_golden_20260520_133601.json) | На golden holdout v2 encoder проходит gate: `DIRECT_SENSITIVE`, `HALLUCINATED_TABLE`, `HALLUCINATED_COLUMN`, `WRONG_JOIN_PATH` закрыты с recall 100%, `EXCESSIVE_SCOPE` - около 78.9%. |
| [`data/eval/reports/model_compare_2026-05-17.json`](data/eval/reports/model_compare_2026-05-17.json) | Сравнение моделей на 100 строках: approval rate 91-100%, среднее число итераций 1.00-1.09. |
| [`data/golden/validation_report.md`](data/golden/validation_report.md) | Golden Dataset v2 проверен: 35 ручных эталонов после исправления готовы к использованию. |
| [`data/eval/execution_accuracy/ea_20260519T190500_summary.json`](data/eval/execution_accuracy/ea_20260519T190500_summary.json) | Технический прогон Execution Accuracy есть, но последний локальный запуск не является успешным доказательством: 3/3 кейса упали по timeout. Для финального значения EA нужно перезапустить этот замер на рабочем backend. |

### Как мы добиваемся качества SQL

Качество достигается не одним большим промптом, а несколькими слоями:

- RAG подставляет генератору только релевантные таблицы, поля и бизнес-правила.
- Генератор получает текущую дату и часовой пояс, поэтому корректнее понимает запросы вроде "за последний месяц".
- Проверка схемы ловит несуществующие таблицы, поля и неверные JOIN.
- Guard не дает пройти опасным конструкциям: DDL/DML, `SELECT *`, отсутствие лимита, доступ к PII без причины.
- Итеративный цикл возвращает генератору конкретные замечания судьи, а не просто просит "попробовать еще раз".
- Execution Accuracy проверяется отдельным скриптом: сравниваются не строки SQL, а фактический результат выполнения на тестовой базе.

### Классы уязвимостей

Основная группировка лежит в [`data/audit_groups/groups.yaml`](data/audit_groups/groups.yaml).

| Группа | Что ловим | Примеры меток |
|---|---|---|
| SQL injection и технические атаки | Тавтологии, UNION-вывод, задержки, несколько операторов в одном запросе. | `SQL_INJ_CLASSIC`, `SQL_INJ_UNION`, `SQL_INJ_TIME`, `MULTI_STATEMENT` |
| DML/DDL и привилегии | Попытки менять данные или структуру БД. | `DML_NO_WHERE`, `DDL_FORBIDDEN`, `TRUNCATE`, `PRIV_ESCALATE` |
| Раскрытие данных и PII | Прямой или избыточный доступ к чувствительным данным. | `DIRECT_SENSITIVE`, `SCHEMA_LEAK`, `EXCESSIVE_SCOPE` |
| Галлюцинации схемы | Таблицы, поля или связи, которых нет в разрешенной схеме. | `HALLUCINATED_TABLE`, `HALLUCINATED_COLUMN`, `WRONG_JOIN_PATH` |
| Качество и стоимость запроса | Слишком широкие или тяжелые запросы. | `SELECT_STAR`, `NO_PAGINATION`, `COST_DOS`, `CROSS_JOIN_EXPLOSION` |
| Prompt injection | Попытки обойти правила через текст пользовательской задачи. | `PROMPT_IGNORE_GUARDRAILS`, `PROMPT_FS_READ`, `PROMPT_SCHEMA_EXFIL` |
| Технические сигналы | Сломанный SQL, недостаточный контекст или неопределенный аудит. | `BROKEN_SQL`, `INSUFFICIENT_CONTEXT`, `AUDIT_UNCERTAIN` |

### Аналитические интерфейсы

Проект дает несколько способов смотреть результат без чтения кода:

- Web chat: интерактивная генерация SQL, выбор модели, прогресс pipeline и финальный вердикт.
- Trace viewer: просмотр шагов pipeline, SQL по итерациям, risk score и событий аудита.
- Benchmark UI: запуск batch-проверок по датасетам и сравнение моделей.
- HTML-отчеты: сохраненные отчеты по eval, benchmark и defense-демо.
- JSON-трассы: машинно-читаемые артефакты для повторного анализа и отладки.

### ASCII-схема проекта и артефактов

```text
case3/
|-- README.md                         # публичное описание проекта
|-- requirements.txt                  # Python-зависимости
|-- .env.example                      # пример env без секретов
|-- app/                              # основной backend и UI
|   |-- api.py                        # FastAPI /health и /run
|   |-- web_chat.py                   # web chat на localhost
|   |-- orchestrator.py               # итеративный цикл generate -> audit -> revise
|   |-- generator*.py                 # генерация SQL и выбор кандидата
|   |-- auditor.py                    # LLM-аудит + итоговый вердикт
|   |-- sql_guard.py                  # deterministic-проверки SQL
|   |-- rag_adapter.py                # контекст схемы и security hints
|   |-- llm_provider.py               # OpenRouter, Ollama, Claude CLI, Codex CLI
|   |-- templates/                    # HTML-страницы
|   `-- static/                       # CSS/JS интерфейсов
|-- benchmark_service/                # API и БД для batch benchmark
|   |-- api.py
|   |-- db.py
|   `-- sql/                          # миграции benchmark-хранилища
|-- deploy/                           # деплой без секретов
|   |-- docker-compose.yml
|   |-- benchmark-compose.yml
|   |-- env.example
|   |-- benchmark.env.example
|   |-- server_deploy.sh
|   |-- server_smoke.sh
|   `-- postgres-init/                # init SQL для Postgres
|-- docs/                             # документация
|   |-- README.md                     # локальная инструкция запуска
|   |-- local-llm-setup.md            # Ollama/Claude/Codex CLI
|   |-- current-state-architecture.md
|   |-- defense/
|   |   |-- walkthrough.md
|   |   |-- qa.md
|   |   `-- screenshots/              # скриншоты для защиты
|   `-- benchmark_store.md
|-- data/                             # датасеты и артефакты
|   |-- audit_groups/                 # группы классов уязвимостей
|   |-- bench/
|   |   |-- requests/                 # benchmark request datasets
|   |   |-- reports/                  # локальные отчеты benchmark
|   |   `-- runs/                     # локальные результаты запусков
|   |-- eval/
|   |   |-- reports/                  # eval HTML/JSON отчеты
|   |   |-- execution_accuracy/       # EA summary/results
|   |   `-- golden_*.jsonl            # golden/regression наборы
|   |-- golden/                       # golden v2 описание и validation report
|   |-- rag/                          # RAG-артефакты
|   |-- traces/                       # локальные JSON-трассы запусков
|   `-- web_chats/                    # локальные сессии web chat
|-- scripts/                          # запуск, eval, smoke, benchmark
|   |-- eval_execution_accuracy.py
|   |-- bench_run_dataset.py
|   |-- local_llm_smoke.py
|   |-- prepare_init_sql.py
|   `-- smoke_*.py
|-- tests/                            # regression/unit tests
`-- TASK-3/                           # исходные материалы задания и RAG-база
```

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
