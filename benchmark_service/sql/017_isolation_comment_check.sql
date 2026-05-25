-- Generated at: 2026-05-21 23:45:00 MSK
-- TZ-19 rework: document and constrain isolation_mode values.

COMMENT ON COLUMN benchmark.benchmark_runs.isolation_mode
    IS 'clean | production | snapshot';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'pipeline_runs_isolation_mode_check'
          AND conrelid = 'benchmark.pipeline_runs'::regclass
    ) THEN
        ALTER TABLE benchmark.pipeline_runs
            ADD CONSTRAINT pipeline_runs_isolation_mode_check
            CHECK (isolation_mode IN ('clean', 'production', 'snapshot'));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'benchmark_runs_isolation_mode_check'
          AND conrelid = 'benchmark.benchmark_runs'::regclass
    ) THEN
        ALTER TABLE benchmark.benchmark_runs
            ADD CONSTRAINT benchmark_runs_isolation_mode_check
            CHECK (isolation_mode IN ('clean', 'production', 'snapshot'));
    END IF;
END $$;
