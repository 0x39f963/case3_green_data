-- Insight 7: surface policy_label / security_risk_score / quality_risk_score
-- / refusal_message / banned_identifiers в pipeline_runs, чтобы UI и API
-- могли показать структурированную причину отказа без чтения JSON-трассы.

ALTER TABLE benchmark.pipeline_runs
    ADD COLUMN IF NOT EXISTS policy_label TEXT,
    ADD COLUMN IF NOT EXISTS security_risk_score NUMERIC,
    ADD COLUMN IF NOT EXISTS quality_risk_score NUMERIC,
    ADD COLUMN IF NOT EXISTS refusal_message TEXT,
    ADD COLUMN IF NOT EXISTS banned_identifiers TEXT[];

CREATE INDEX IF NOT EXISTS pipeline_runs_policy_label_idx
    ON benchmark.pipeline_runs (policy_label);
