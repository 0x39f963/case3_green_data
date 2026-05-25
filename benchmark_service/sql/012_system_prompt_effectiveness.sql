-- Generated at: 2026-05-21 17:34:13 MSK
-- Prompt registry metadata for benchmark llm calls and candidate analytics.

ALTER TABLE benchmark.llm_calls
    ADD COLUMN IF NOT EXISTS prompt_type TEXT,
    ADD COLUMN IF NOT EXISTS prompt_id TEXT,
    ADD COLUMN IF NOT EXISTS prompt_version INTEGER,
    ADD COLUMN IF NOT EXISTS prompt_sha256 TEXT;

ALTER TABLE benchmark.generator_candidate_metrics
    ADD COLUMN IF NOT EXISTS prompt_type TEXT,
    ADD COLUMN IF NOT EXISTS prompt_id TEXT,
    ADD COLUMN IF NOT EXISTS prompt_version INTEGER,
    ADD COLUMN IF NOT EXISTS prompt_sha256 TEXT;

CREATE INDEX IF NOT EXISTS idx_llm_calls_prompt
    ON benchmark.llm_calls (prompt_type, prompt_id, prompt_version)
    WHERE prompt_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_generator_candidate_prompt
    ON benchmark.generator_candidate_metrics (prompt_type, prompt_id, prompt_version, generator_model, temperature)
    WHERE prompt_id IS NOT NULL;

CREATE OR REPLACE VIEW benchmark.system_prompt_effectiveness AS
SELECT
    prompt_type,
    prompt_id,
    prompt_version,
    generator_model,
    temperature,
    count(*) AS candidate_count,
    avg((selected_by_selector)::int) AS selector_win_rate,
    avg((run_approved)::int) FILTER (WHERE selected_by_selector) AS run_approve_rate,
    avg((run_needs_human)::int) FILTER (WHERE selected_by_selector) AS needs_human_rate,
    avg(selected_iteration_risk_score) FILTER (WHERE selected_by_selector) AS avg_risk_score
FROM benchmark.generator_candidate_metrics
GROUP BY prompt_type, prompt_id, prompt_version, generator_model, temperature;
