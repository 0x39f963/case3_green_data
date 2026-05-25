Generated at: 2026-05-18 16:49:50 MSK

## Adversarial SQL Benchmark Dataset v0.1

- Назначение: request-level датасет для SQL Security pipeline без исполнения SQL.
- Основной файл: `data/bench/requests/adversarial_sql_requests_v0_1.jsonl`.
- Контракт строки: `data/bench/requests/adversarial_sql_requests.schema.json`.
- Шаблоны: `data/bench/requests/templates/<family>.yaml`.
- Coverage report: `data/bench/reports/adversarial_sql_requests_v0_1.json`.

## Generate

```bash
python3 scripts/bench_generate_requests.py \
  --rows 120 \
  --version v0_1 \
  --seed 20260518 \
  --templates data/bench/requests/templates \
  --schema-overlay deploy/schema_overlay.json \
  --out data/bench/requests/adversarial_sql_requests_v0_1.jsonl \
  --report data/bench/reports/adversarial_sql_requests_v0_1.json
```

## Validate

```bash
python3 scripts/bench_validate_requests.py \
  --dataset data/bench/requests/adversarial_sql_requests_v0_1.jsonl \
  --schema data/bench/requests/adversarial_sql_requests.schema.json \
  --schema-overlay deploy/schema_overlay.json \
  --security-meta TASK-3/marina-case3-rag/rag_pipeline/indices/security_meta.json \
  --strict
```

## Notes

- `seed_sql` нужен только как reference для expected labels и не исполняется.
- `expected_labels` берутся из текущего `app.sql_guard.ALL_LABELS`.
- В ТЗ Family Mix суммируется в 130 строк, а AC требует 120±2. P0 использует 120 строк с отклонением не больше 2 по каждому семейству.
- Runtime `metadata.decision` сейчас не содержит `block`; в датасете `block` хранится как expected outcome для будущего batch runner.
