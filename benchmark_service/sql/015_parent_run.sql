-- Generated at: 2026-05-21 22:28:00 MSK
-- TZ-18 D7: parent/child rerun metadata.

ALTER TABLE benchmark.benchmark_runs
    ADD COLUMN IF NOT EXISTS parent_run_id TEXT;

ALTER TABLE benchmark.benchmark_runs
    ADD COLUMN IF NOT EXISTS case_ids_filter TEXT[];

ALTER TABLE benchmark.benchmark_runs
    ADD COLUMN IF NOT EXISTS prompt_version_override TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'benchmark_runs_parent_run_id_fkey'
          AND conrelid = 'benchmark.benchmark_runs'::regclass
    ) THEN
        ALTER TABLE benchmark.benchmark_runs
            ADD CONSTRAINT benchmark_runs_parent_run_id_fkey
            FOREIGN KEY (parent_run_id)
            REFERENCES benchmark.benchmark_runs(benchmark_run_id)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_benchmark_runs_parent_run_id
    ON benchmark.benchmark_runs(parent_run_id);
