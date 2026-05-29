-- Generated at: 2026-05-29 13:18:52 MSK

ALTER TABLE benchmark.pipeline_runs
    ADD COLUMN IF NOT EXISTS abstain_reason TEXT;

CREATE INDEX IF NOT EXISTS pipeline_runs_abstain_reason_idx
    ON benchmark.pipeline_runs (abstain_reason);
