-- Generated at: 2026-05-22 13:35:31 MSK
-- TZ-22: case-level Oracle idempotency and persisted benchmark progress block.

SET search_path TO benchmark, public;

ALTER TABLE benchmark_runs
    ADD COLUMN IF NOT EXISTS benchmark_progress JSONB NOT NULL DEFAULT '{}'::jsonb;

DO $$
DECLARE
    duplicate_count integer;
BEGIN
    SELECT count(*)
    INTO duplicate_count
    FROM (
        SELECT run_id, case_id, oracle_type
        FROM oracle_eval_runs
        WHERE case_id IS NOT NULL
          AND trace_id IS NOT NULL
        GROUP BY run_id, case_id, oracle_type
        HAVING count(*) > 1
    ) d;

    IF duplicate_count > 0 THEN
        RAISE EXCEPTION
            'Cannot create case-level Oracle unique index: % duplicate posthoc keys exist',
            duplicate_count;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_oracle_eval_runs_unique_case_posthoc
    ON oracle_eval_runs(run_id, case_id, oracle_type)
    WHERE case_id IS NOT NULL
      AND trace_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_benchmark_runs_progress_oracle
    ON benchmark_runs USING gin ((benchmark_progress -> 'oracle'));
