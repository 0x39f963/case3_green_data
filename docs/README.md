# Case 3. SQL Security System

Учебный продукт для проверки безопасности SQL-запросов на базе данных GreenData.
Аналитик пишет задачу на естественном языке, система генерирует SQL под эту
задачу, проверяет его на типичные риски и при необходимости переписывает.
До пяти попыток. На выходе - финальный SQL и человекочитаемый отчет.

## Endpoints и порты

| Порт | Сервис | Источник | Назначение |
|---|---|---|---|
| 8000 | Docker `app` (FastAPI) | `deploy/docker-compose.yml::app` | Канонический endpoint: `POST /run`, `GET /health`, web UI `/`, `/chat`, web API `/web/api/*`. |
| 18000 | host uvicorn (legacy) | ручной запуск на хосте | Prod_demo для заказчика. Тот же контракт, но без перезапуска не подхватит обновления `app/api.py`. Переход на Docker app:8000 - предпочтительный путь, 18000 деприкейтится. |
| 18081 | Docker `benchmark_api` (FastAPI) | `deploy/benchmark-compose.yml::benchmark_api` | Внутренний benchmark store: `/v1/benchmarks/*`, `/v1/datasets` и другие служебные ручки. Не для UI-пользователей. |
| 11434 | `local-llm-proxy` (Ollama) | `deploy/docker-compose.yml::local-llm-proxy` | Локальный LLM proxy (Qwen3 8B). Профиль `local-llm`, не запускается по умолчанию. |

## Из чего состоит сборка

Один Docker Compose, семь сервисов:

| Сервис | Порт | Профиль | Зачем |
|---|---|---|---|
| postgres | 5432 | основной | Тестовая база с дампом схемы GreenData (60 таблиц) |
| rag-init | - | init | Парсит DDL и собирает FAISS-индексы поисковой памяти. Запускается один раз |
| app | 8000 | основной | FastAPI: /run, /health, веб-чат `/chat`, история и batch-аудиты |
| ui | 8501 | основной | Streamlit-чат для аналитика |
| trace-viewer | 8502 | основной | HTML-просмотрщик истории прогонов pipeline |
| eval | - | eval | Одноразовый прогон оценочного датасета |
| local-llm-proxy | 11434 | local-llm | Локальная OpenAI-совместимая модель (Ollama по умолчанию) |

## Как запустить локально

Шаги по порядку, выполнять из корня репозитория.

1. Скопировать пример переменных окружения и заполнить значения:
   ```bash
   cp .env.example .env
   ```
   Минимум, что нужно: выбрать `LLM_MODE` (`dev_local`, `prod_demo`,
   `mixed` или `local_openai`), выбрать `LLM_GENERATOR_MODEL` и заполнить
   ключи для выбранного режима.

2. Подготовить безопасный init-скрипт для PostgreSQL:
   ```bash
   python scripts/prepare_init_sql.py
   ```
   Скрипт вырежет из исходного дампа GreenData строки с `CREATE DATABASE` и
   `\connect`, без которых контейнер postgres не поднимается.

3. Собрать образы и поднять базу:
   ```bash
   docker compose -f deploy/docker-compose.yml --env-file .env build
   docker compose -f deploy/docker-compose.yml --env-file .env up -d postgres
   ```

4. Один раз собрать индексы памяти:
   ```bash
   docker compose -f deploy/docker-compose.yml --env-file .env --profile init up rag-init
   ```
   На холодном старте занимает 1-2 минуты, скачивает модель эмбеддингов.

5. (Опционально) Поднять локальную модель, если выбран `LLM_MODE=local_openai`:
   ```bash
   docker compose -f deploy/docker-compose.yml --env-file .env --profile local-llm up -d local-llm-proxy
   ```
   Compose сам запускает `ollama pull` для `qwen2.5-coder:7b`,
   `arctic-text2sql-r1:7b` и `qwen3:8b`.

6. Поднять рабочие сервисы:
   ```bash
   docker compose -f deploy/docker-compose.yml --env-file .env up -d app ui trace-viewer
   ```

7. Открыть в браузере:
   - веб-чат с URL-сессиями: <http://localhost:8000/chat>;
   - Streamlit-чат аналитика: <http://localhost:8501>;
   - HTTP-эндпоинт: <http://localhost:8000/health>;
   - просмотрщик трасс: <http://localhost:8502>.

Если порт 8000 занят, задайте `APP_PORT`, например `APP_PORT=18000`, и
откройте `http://localhost:18000/chat`.

## Дымовая проверка

После того как все сервисы поднялись, можно прогнать два сценария:

```bash
docker compose -f deploy/docker-compose.yml --env-file .env --profile eval run --rm eval
```

Сценарий A (безопасный) ожидает одобрение за 1-2 итерации.
Сценарий B (опасный) ожидает отклонение и переписывание.
Полные JSON-трассы появятся в просмотрщике на 8502.

### Docker smoke через OpenRouter

Если локальные порты 5432 и 8000 заняты, используйте временный override
вне репозитория:

```yaml
services:
  postgres:
    ports: !override
      - "15432:5432"
  app:
    ports: !override
      - "18000:8000"
```

Для smoke через OpenRouter в `.env` должны быть заданы:

```env
LLM_MODE=prod_demo
LLM_GENERATOR_MODEL=qwen3-5-9b
LLM_MODEL_AUDITOR=openai/gpt-4o-mini
OPENROUTER_API_KEY=...
POSTGRES_DSN=postgresql://demo:demo@postgres:5432/demo_db
POSTGRES_AUDIT_DSN=postgresql://audit_ro:audit_ro_password@postgres:5432/demo_db
```

Команды проверки:

```bash
docker compose -p case3smoke -f deploy/docker-compose.yml -f /tmp/case3-compose-smoke.yml --env-file .env build
docker compose -p case3smoke -f deploy/docker-compose.yml -f /tmp/case3-compose-smoke.yml --env-file .env up -d postgres
docker compose -p case3smoke -f deploy/docker-compose.yml -f /tmp/case3-compose-smoke.yml --env-file .env --profile init up rag-init
docker compose -p case3smoke -f deploy/docker-compose.yml -f /tmp/case3-compose-smoke.yml --env-file .env --profile eval run --rm eval
```

Ожидаемый итог в логе: `Итог smoke: PASS`. Если OpenRouter endpoint выбранной
модели недоступен, можно временно заменить `LLM_GENERATOR_MODEL` на другой
поддержанный профиль.

## Demo сценарии

Defense demo поддерживает два режима.

### Сценарий A: локальный Ollama

```bash
cp .env.example .env
LLM_MODE=local_openai docker compose -f deploy/docker-compose.yml --env-file .env --profile local-llm up -d
```

UI доступен на <http://localhost:8501>. В sidebar выбрать
`Локальный режим (Ollama)`. `/run` получит `llm_mode=local_openai`, генератор
и аудитор пойдут в локальный OpenAI-compatible endpoint. Основная локальная
модель по умолчанию: `qwen3.5:9b` (`local-qwen3-5-9b` в UI).

Новый веб-чат доступен в том же FastAPI-сервисе: `/chat`, `/history`.
История хранится в `WEB_CHAT_DIR` как JSON-файлы; каждый чат имеет
стабильный URL `/chat/{chat_id}`.

При локальном запуске через `uvicorn app.api:app` приложение само читает
корневой `.env` и не перетирает переменные, уже заданные окружением. В Docker
по-прежнему используется `--env-file .env`.

### Сценарий B: cloud OpenRouter

```bash
cp .env.example .env
# заполнить OPENROUTER_API_KEY
LLM_MODE=prod_demo docker compose -f deploy/docker-compose.yml --env-file .env up -d
```

UI доступен на <http://localhost:8501>. В sidebar выбрать
`Облачный режим (OpenRouter)`. `/run` получит `llm_mode=prod_demo`.

### Автоматический walkthrough

```bash
bash scripts/demo_walkthrough.sh
```

По умолчанию скрипт запускает оба demo режима через deterministic smoke без
внешних ключей. Для live Docker demo:

```bash
DEMO_RUN_DOCKER=1 bash scripts/demo_walkthrough.sh
```

## Тестовый Telegram-бот

Бот нужен команде для быстрых ручных прогонов pipeline: пользователь пишет
обычный текст задачи, бот вызывает FastAPI `/run`, подтягивает JSON-трассу и
возвращает standalone HTML-отчет файлом.

### Что нужно настроить

В `.env` заполнить:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USERS=12345678,23456789
BOT_API_URL=http://localhost:8000
BOT_REPORT_DIR=data/bot/reports
BOT_USER_PREFS_PATH=data/bot/user_prefs.json
BOT_MODELS_CONFIG=deploy/bot_models.json
BOT_REPORT_DELIVERY_MODE=local
```

FastAPI должен быть доступен по `BOT_API_URL`. Для локального запуска:

```bash
uvicorn app.api:app --host 0.0.0.0 --port 8000
python scripts/run_test_bot.py
```

### Команды

| Команда | Что делает |
|---|---|
| `/start` | краткое описание бота |
| `/help` | справка по командам |
| `/models` | показывает inline-кнопки моделей из `deploy/bot_models.json` |
| `/current` | показывает выбранную модель пользователя |

Любой текст без `/` запускает pipeline. Выбор модели хранится в
`data/bot/user_prefs.json`, файл не коммитится.

### Где смотреть отчеты

В режиме `BOT_REPORT_DELIVERY_MODE=local` бот присылает HTML через
Telegram `sendDocument`; локальная копия лежит в `data/bot/reports/`.
Фаза `php_upload` пока оставлена как заглушка: контракт upload endpoint
описан в ТЗ, реализация PHP-сервера не входит в текущую ветку.

Smoke без Telegram и HTTP:

```bash
python scripts/smoke_bot.py
```

## Веб-чат и batch-аудиты

FastAPI теперь обслуживает рабочий shell без Telegram:

| Path | Назначение |
|---|---|
| `/chat` | новый чат, отправка natural language задачи в pipeline |
| `/chat/{chat_id}` | deep-link на конкретную серверную сессию |
| `/history` | история запросов с поиском и фильтром по статусу |
| `/audits/batch-cases` | единый workbench для batch traces, Oracle, Judge-audit и гипотез |
| `/audits/runs/{run_id}` | обзор прогресса run и вход в полный audit report |

Ручной `/import` удалён; для batch-разборов используется
`/audits/batch-cases?run_id=<benchmark_run_id>&report=run`.

## Структура репозитория

```
.
+-- app/                    наш Python-код: оркестратор, генератор, аудитор, UI, API
+-- scripts/                вспомогательные скрипты (подготовка init.sql, сборка RAG, smoke)
+-- deploy/                 Dockerfile, docker-compose.yml, postgres-init, примеры env
+-- docs/                   эта документация
+-- data/                   тома для трасс и индексов (gitignored)
+-- TASK-3/                 материалы заказчика (не менять)
```

## Контуры LLM

Настройка идет на двух уровнях. `LLM_GENERATOR_MODEL` выбирает генератор и
его провайдера. `LLM_MODE` задает аудитора и общий сценарий окружения:

- `dev_local` - аудитор через CLI claude (оффлайн-сбор данных и отладка);
- `prod_demo` - аудитор через OpenRouter (защита проекта на маленьких моделях);
- `mixed` - аудитор через CLI claude, генератор обычно через OpenRouter-профиль;
- `local_openai` - аудитор через локальную OpenAI-совместимую модель (Ollama, vLLM, LM Studio).

Генератор выбирается отдельно через `LLM_GENERATOR_MODEL`. Поддерживаются три
профиля:

| Значение | Провайдер | Модель |
|---|---|---|
| `qwen-coder-7b` | OpenRouter | `qwen/qwen2.5-coder-7b-instruct` |
| `arctic-text2sql-7b` | Ollama/local OpenAI API | `arctic-text2sql-r1:7b` |
| `qwen3-8b` | OpenRouter | `qwen/qwen3-8b` |

Для локального Ollama/OpenAI-compatible режима используйте тот же ключ
`qwen3-8b`, но с `LLM_MODE=local_openai`; фактическая локальная модель будет
`qwen3:8b`. Для Ollama по умолчанию включен `LOCAL_LLM_USE_NATIVE_OLLAMA=true`,
потому что Qwen3 через `/v1/chat/completions` может возвращать reasoning без
основного `content`.

В `/health`, JSON-трассе и `SystemResult.metadata` пишутся выбранные backend,
model key и фактическое имя модели. Неизвестный `LLM_GENERATOR_MODEL` считается
ошибкой конфигурации и возвращает HTTP 400.

Точечный override для роли: переменные `LLM_BACKEND_GENERATOR` и
`LLM_BACKEND_AUDITOR` переопределяют только backend роли независимо от контура.
Если вручную задан `LLM_BACKEND_GENERATOR`, можно дополнительно задать
`LLM_MODEL_GENERATOR`.

CLI-варианты медленные (5-15 секунд на ответ), поэтому подходят для оффлайн-задач,
но не для интерактивного чата под новый бюджет 300/600 секунд.

## Локальная модель и dataset smoke

Проверка сырого inference:

```bash
python scripts/local_llm_smoke.py \
  --base-url http://localhost:11434/v1 \
  --model qwen3:8b
```

Проверка на нескольких строках regression dataset без полного pipeline:

```bash
python scripts/local_llm_dataset_smoke.py \
  --dataset data/eval/regression_cases.jsonl \
  --limit 3 \
  --base-url http://localhost:11434/v1 \
  --model qwen3:8b
```

Скрипт сохраняет JSON-отчет в `data/eval/reports/local_llm_smoke_*.json`.

## Серверный deploy

Для отдельного сервера подготовлены два скрипта:

```bash
ENV_FILE=/opt/case3/.env COMPOSE_PROJECT_NAME=case3 bash deploy/server_deploy.sh
APP_PORT=8000 TRACE_VIEWER_PORT=8502 STREAMLIT_PORT=8501 bash deploy/server_smoke.sh
```

Флаги:
- `RUN_RAG_INIT=0` - пропустить пересборку RAG-индексов;
- `ENABLE_LOCAL_LLM=1` - поднять `local-llm-proxy`;
- `ENABLE_BENCHMARK=1` - поднять benchmark store отдельным compose.

## Латентность и ошибки API

Контракт `/run`:

| Ситуация | Ответ |
|---|---|
| Конфигурация некорректна | HTTP 400 |
| Провайдер модели недоступен, вернул 5xx, 429 или не ответил | HTTP 503 |
| Полный pipeline дольше `LATENCY_HARD_SEC` | HTTP 504 |
| Полный pipeline дольше `LATENCY_SOFT_SEC`, но быстрее hard | HTTP 200 и `metadata.latency_warning=true` |

Значения по умолчанию: `LLM_CALL_TIMEOUT_SEC=300`, `LATENCY_SOFT_SEC=300`,
`LATENCY_HARD_SEC=600`. Retry и auto-fallback в этой ветке не используются:
ошибка провайдера быстро возвращается клиенту.

## Архитектура проверки и аудита

LangGraph остается из семи узлов: retrieve, generate, sql_guard,
explain_sandbox, audit, decide, revise. Быстрые правила используют `pglast`
как основной PostgreSQL AST parser, а `sqlparse` оставлен только для простых
regex/token checks из MVP.

EXPLAIN выполняется через `POSTGRES_AUDIT_DSN` под ролью `audit_ro`.
Роль имеет только `CONNECT`, `USAGE` и `SELECT`; запись истории идет через
обычный `POSTGRES_DSN`. Таблицы `audit_runs` и `audit_iterations` создаются
в `deploy/postgres-init/02-audit-role.sql` и хранят финальный run и JSONB
уязвимостей по итерациям.

## Защита проекта

Материалы защиты лежат в `docs/defense/`:

| Файл | Назначение |
|---|---|
| `docs/defense/walkthrough.md` | 5-минутный сценарий защиты |
| `docs/defense/qa.md` | ответы на типовые вопросы комиссии |
| `docs/defense/screenshots/` | PNG для Streamlit UI и trace_viewer |

## Dataset, ML stages и eval

Step5 classifier ML артефакты лежат в `data/eval/` и `app/classifier/models/`.
Raw материалы кладутся в `data/eval/raw/{manual,synthetic,public}/` и не
коммитятся. Финальные файлы коммитятся:

| Файл | Назначение |
|---|---|
| `data/eval/dataset_v1_0.jsonl` | 3,750 labeled rows для train/valid/test |
| `data/eval/dataset_v0_1.jsonl` | исторический 2K checkpoint Step4 |
| `data/eval/regression_cases.jsonl` | permanent regression cases |
| `data/eval/redteam_holdout.jsonl` | private-style holdout, не используется в train/valid |
| `data/eval/fp_budget_v1_0.json` | measured block FP и target budget |

Основные команды:

```bash
python3 scripts/dataset_build.py --validate
python3 scripts/train_ml_stage2.py --dataset data/eval/dataset_v1_0.jsonl
python3 scripts/train_encoder_stage3.py --dataset data/eval/dataset_v1_0.jsonl
python3 scripts/eval_dataset.py --encoder v2_0 --dataset dataset_v1_0 --strict-gate
python3 scripts/eval_encoder_compare.py --baseline v1_0 --candidate v2_0 --dataset dataset_v1_0
python3 scripts/eval_dataset.py --enforce-gate --sprint 2
python3 scripts/eval_model_compare.py --models qwen-coder-7b,arctic-text2sql-7b,qwen3-8b
python3 scripts/ablation.py
```

Stage 2 хранит Logistic Regression и LightGBM модели с per-label thresholds.
Stage 3 включен по умолчанию и использует `encoder_v2_0/`: frozen
`intfloat/multilingual-e5-small` embeddings + sklearn head. Старый
`encoder_v1_0/` остается fallback через
`CLASSIFIER_ENCODER_PATH=app/classifier/models/encoder_v1_0`. Stage 5
агрегирует rules, ML, encoder и judge findings в единое решение.

Регрессионная политика жесткая: любой false negative по critical labels
становится permanent case. Добавить кейс можно так:

```bash
python3 scripts/regression_add.py --case-id case3_sqlsec_000797 --reason critical_false_negative
```

`scripts/eval_dataset.py --auto-regression` добавляет такие кейсы из eval
прогона автоматически.

## Известные ограничения

- На холодном старте загрузка модели эмбеддингов занимает около 30 секунд.
- При невалидном JSON-ответе аудитора подмешивается уязвимость `AUDIT_UNCERTAIN`
  с риском выше порога, и запрос автоматически отклоняется. Текст ошибки разбора
  попадает в трассу, чтобы можно было поправить промпт или модель.
- Кросс-чек чувствительных полей `DIRECT_SENSITIVE` ловит прямое обращение к
  колонкам по подстроке имени таблицы и колонки. Чувствительные поля, скрытые
  через view, CTE, alias, функцию или PL/pgSQL процедуру, в текущем MVP не
  отслеживаются. Полноценный разбор требует анализа происхождения колонок через
  AST - задача для следующей итерации.
- Парсер схемы пропускает несколько внешних ключей с `ON DELETE CASCADE`. На
  работу цикла это не влияет, но описание двух служебных таблиц неполное.
- Финальный SQL мы никогда не выполняем на боевых данных. EXPLAIN запускается
  в отдельной READ ONLY транзакции с обязательным откатом, multi-statement до
  EXPLAIN не доходит - его ловит детерминированное правило `MULTI_STATEMENT`.
