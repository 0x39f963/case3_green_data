-- TZ-4 follow-up F-9: expose final_sql as a first-class column.
-- Reviewer (TZ-6) and analyst queries need full SQL without scanning JSONB.
ALTER TABLE benchmark.pipeline_runs
    ADD COLUMN IF NOT EXISTS final_sql_text TEXT;

-- final_sql_len semantics: NULL when SQL is absent, length(text) otherwise.
ALTER TABLE benchmark.pipeline_runs
    ALTER COLUMN final_sql_len DROP NOT NULL;

CREATE INDEX IF NOT EXISTS pipeline_runs_final_sql_sha256_idx
    ON benchmark.pipeline_runs (final_sql_sha256)
    WHERE final_sql_sha256 IS NOT NULL;
