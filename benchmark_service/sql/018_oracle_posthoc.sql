-- Generated at: 2026-05-22 16:35:00 MSK
-- TZ-21: trace-level Oracle rows for posthoc batch finalization.

SET search_path TO benchmark, public;

ALTER TABLE oracle_eval_runs
    ADD COLUMN IF NOT EXISTS trace_id TEXT,
    ADD COLUMN IF NOT EXISTS model_key TEXT,
    ADD COLUMN IF NOT EXISTS oracle_test_id TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'oracle_eval_runs_trace_id_fkey'
          AND conrelid = 'benchmark.oracle_eval_runs'::regclass
    ) THEN
        ALTER TABLE oracle_eval_runs
            ADD CONSTRAINT oracle_eval_runs_trace_id_fkey
            FOREIGN KEY (trace_id)
            REFERENCES benchmark.pipeline_runs(trace_id)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_oracle_eval_runs_unique_trace
    ON oracle_eval_runs(run_id, trace_id, oracle_type)
    WHERE trace_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_oracle_eval_trace_id
    ON oracle_eval_runs(trace_id)
    WHERE trace_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_oracle_eval_run_trace_verdict
    ON oracle_eval_runs(run_id, trace_id, verdict)
    WHERE trace_id IS NOT NULL;
