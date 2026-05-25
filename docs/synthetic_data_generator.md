Generated at: 2026-05-18 20:43:11 MSK

## Synthetic Data Generator

`scripts/fill_synthetic_db.py` appends synthetic benchmark rows into the active GreenData PostgreSQL schema.

## Scope

- Writes only tables listed in `deploy/schema_overlay.json`.
- Reads active PK/FK metadata from PostgreSQL catalogs, not from `01-schema.sql`.
- Uses append-only inserts: no `TRUNCATE`, `DELETE`, `DROP`, `ALTER`.
- Keeps `audit_runs` and `audit_iterations` untouched.

## Example

```bash
.venv/bin/python scripts/fill_synthetic_db.py \
  --dsn postgresql://demo:demo@localhost:15433/demo_db \
  --target-rows 1000000 \
  --seed 20260518 \
  --batch-size 5000 \
  --schema public \
  --overlay deploy/schema_overlay.json
```

## Checks

```bash
.venv/bin/python scripts/fill_synthetic_db.py \
  --dsn postgresql://demo:demo@localhost:15433/demo_db \
  --target-rows 1000000 \
  --dry-run

.venv/bin/python scripts/fill_synthetic_db.py \
  --dsn postgresql://demo:demo@localhost:15433/demo_db \
  --validate-only
```

## Reports

- JSON reports: `data/synthetic/reports/<run_id>.json`
- Logs: `data/synthetic/logs/<run_id>.log`
- DSN passwords are masked or omitted.
- Reports contain aggregates, FK/PII checks, distinct ratios and sha256 fragments, not raw rows.
- `sequences_skipped_reason="no_serial_sequence"` means the schema uses explicit `bigint id` columns without SERIAL/IDENTITY sequences.
- `pii_check.checked_with_regex` counts text PII fields checked by regex; `pii_check.skipped_non_text` counts tagged numeric/date fields where regex is not meaningful.

## Local Reset

For local debug cleanup before the benchmark runner:

```bash
.venv/bin/python scripts/synthetic_reset.py \
  --dsn postgresql://demo:demo@localhost:15433/demo_db
```

Without the explicit flag this is a dry run. To execute destructive cleanup of overlay tables only:

```bash
.venv/bin/python scripts/synthetic_reset.py \
  --dsn postgresql://demo:demo@localhost:15433/demo_db \
  --i-know-what-i-do
```

## Budget Note

Budget mode is `weighted_floor`. Every table gets a hard floor of 20 rows, and the remainder
`target_rows - 60 * 20` is spread across tables by class-normalized weights
(`WEIGHTS[class] / table_count_in(class)`). This keeps the share of each cardinality class
stable regardless of how many tables fall into it: one `very_large` table absorbs roughly
70% of the bonus on the current 60-table overlay; the rest goes to `large`, then `medium`,
then `small`. `--target-rows` below 1200 (60 tables × floor 20) is rejected at startup.

## Sequence handling

The current public.* schema declares `id bigint NOT NULL` without `SERIAL` / `GENERATED IDENTITY`,
so `pg_get_serial_sequence` returns NULL for every table and the loader records
`sequences_updated=0` with `sequences_skipped_reason="no_serial_sequence"`. This is expected
on this schema; if you add an `IDENTITY` column later, the loader will start updating
sequences automatically through `setval(pg_get_serial_sequence(...), max(id))`.
