Generated at: 2026-05-18 22:13:46 MSK

## Benchmark Store

`benchmark_service` - отдельный FastAPI-сервис для хранения benchmark-прогонов SQL Security pipeline.

### Назначение

- принимает JSON payload одного прогона: `trace`, `system_result`, optional `report_data`, dataset/case/model metadata;
- сохраняет payload целиком в `benchmark.raw_payloads.payload_jsonb`;
- нормализует данные в `pipeline_runs`, `pipeline_steps`, `llm_calls`, `findings`, `faiss_hits`, `explain_results`;
- работает в отдельном Docker Compose stack с отдельным Postgres и томом.

### Запуск

```bash
cp deploy/benchmark.env.example deploy/benchmark.env
# set BENCHMARK_INGEST_TOKEN to a private value
docker compose -f deploy/benchmark-compose.yml --env-file deploy/benchmark.env up -d --build
curl -fsS http://localhost:18080/health
```

Порты по умолчанию:

| service | host port | container port |
|---|---:|---:|
| benchmark-api | 18080 | 8080 |
| benchmark-postgres | 15432 | 5432 |

### Auth

P0 использует bearer-token:

```http
Authorization: Bearer <BENCHMARK_INGEST_TOKEN>
```

Поведение:

| case | status |
|---|---:|
| нет `Authorization` | 401 |
| неверный token | 403 |
| тело больше `BENCHMARK_MAX_BODY_MB` | 413 |

Token берется только из ENV. Ротация P0 - сменить ENV и перезапустить сервис.
При старте сервис отказывается запускаться, если token пустой, равен `change-me-32-chars-min` или короче 32 символов.
`BENCHMARK_HMAC_SECRET` зарезервирован для P1 и в текущем P0-контуре не проверяется.

### API

| method | path | auth | purpose |
|---|---|---|---|
| GET | `/health` | no | DB connectivity without schema details |
| POST | `/v1/ingest/run` | bearer | ingest one run |
| POST | `/v1/ingest/batch` | bearer | ingest up to 50 runs |
| POST | `/v1/datasets/{dataset_id}/cases` | bearer | upsert dataset cases from JSON array, `{items:[...]}` or JSONL |
| POST | `/v1/runs/register` | bearer | register benchmark batch header |
| GET | `/v1/runs/{trace_id}` | bearer | fetch normalized run |
| GET | `/v1/runs?benchmark_run_id=...` | bearer | list runs |
| GET | `/v1/metrics/summary` | bearer | aggregate by model/decision/family/date |
| GET | `/v1/datasets` | bearer | list dataset versions |
| GET | `/v1/admin/version` | bearer | DB migration, service version and git sha |

### Dataset Cases

```bash
jq -s '.[0:5]' data/bench/requests/adversarial_sql_requests_v0_1.jsonl \
  | curl -fsS -X POST \
      -H "Authorization: Bearer $BENCHMARK_INGEST_TOKEN" \
      -H "Content-Type: application/json" \
      --data-binary @- \
      http://localhost:18080/v1/datasets/adversarial_sql_requests/cases
```

Повторная загрузка тех же `case_id` делает UPSERT и не создает дубликаты.
Если `client_meta.case` или `report_data.case_meta` пришли в ingest payload, сервис создает минимальную строку `dataset_cases`, не затирая уже загруженную семантику.

### Idempotency

- тот же `trace_id` -> update дочерних строк в одной транзакции;
- новый `trace_id` при той же тройке `(benchmark_run_id, case_id, model_key)` -> `409 duplicate_logical_run` с `existing_trace_id`;
- `POST /v1/ingest/run?replace=true` удаляет старый `trace_id` и вставляет новый.

### Upload Trace

```bash
python3 scripts/bench_upload_trace.py \
  --trace data/traces/20260517T231152_88aee73b.json \
  --url http://localhost:18080 \
  --token "$BENCHMARK_INGEST_TOKEN" \
  --benchmark-run-id bench_smoke \
  --dataset-id adversarial_sql_requests \
  --dataset-version v0.1 \
  --case-id bench_req_000001 \
  --model-key gpt-5-4-nano
```

Скрипт печатает `request_sha256` и `request_size_bytes`; для одиночного ingest эти значения должны совпадать с `raw_payloads.payload_sha256` и `raw_payloads.payload_size_bytes`.

Batch ingest считает `payload_sha256` по canonical serialization одного item: `json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`. Ответ batch содержит `sha256_mode: "canonical_sha256"`.

### Migrations

```bash
BENCHMARK_DSN=postgresql://bench:bench@localhost:15432/bench \
python3 -m benchmark_service.migrate
```

Миграции лежат в `benchmark_service/sql/NNN_*.sql`, применяются по возрастанию версии и пишутся в `benchmark.schema_migrations`.

### VPS Notes

- сервис рассчитан на работу за reverse proxy с HTTPS;
- P0 не содержит HMAC и analyst-token, это P1;
- raw payload может содержать prompts и SQL, поэтому наружу нельзя отдавать доступ без token;
- backup P0 - стандартный `pg_dump` отдельной БД `bench`.
