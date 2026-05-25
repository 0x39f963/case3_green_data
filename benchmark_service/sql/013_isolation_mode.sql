-- Generated at: 2026-05-21 22:28:00 MSK
-- TZ-18 D1: clean-run isolation mode for benchmark runs.

ALTER TABLE benchmark.pipeline_runs
    ADD COLUMN IF NOT EXISTS isolation_mode TEXT DEFAULT 'production';

ALTER TABLE benchmark.benchmark_runs
    ADD COLUMN IF NOT EXISTS isolation_mode TEXT DEFAULT 'production';

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_isolation_mode
    ON benchmark.pipeline_runs(isolation_mode);

COMMENT ON COLUMN benchmark.pipeline_runs.isolation_mode
    IS 'clean | production | snapshot';
