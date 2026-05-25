CREATE TABLE IF NOT EXISTS benchmark.generator_candidate_metrics (
    id BIGSERIAL PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES benchmark.pipeline_runs(trace_id) ON DELETE CASCADE,
    benchmark_run_id TEXT,
    case_id TEXT,
    model_key TEXT,
    llm_mode TEXT,
    generator_backend TEXT,
    generator_model TEXT,
    generator_provider TEXT,
    iteration INTEGER NOT NULL,
    candidate_index INTEGER NOT NULL,
    temperature NUMERIC(4,2),
    temperature_applied BOOLEAN,
    prompt_type TEXT,
    prompt_id TEXT,
    prompt_version INTEGER,
    prompt_sha256 TEXT,
    sql_sha256 TEXT,
    sql_len INTEGER,
    selected_by_selector BOOLEAN NOT NULL DEFAULT false,
    selector_broken BOOLEAN,
    selector_critical_count INTEGER,
    selector_finding_count INTEGER,
    selector_labels TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    selected_iteration_audit_approved BOOLEAN,
    selected_iteration_risk_score NUMERIC,
    run_decision TEXT,
    run_approved BOOLEAN,
    run_needs_human BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (trace_id, iteration, candidate_index)
);

CREATE INDEX IF NOT EXISTS generator_candidate_metrics_trace_id_idx
    ON benchmark.generator_candidate_metrics(trace_id);

CREATE INDEX IF NOT EXISTS generator_candidate_metrics_temperature_idx
    ON benchmark.generator_candidate_metrics(generator_model, prompt_id, prompt_version, temperature);

CREATE OR REPLACE VIEW benchmark.temperature_candidate_summary AS
SELECT
    generator_model,
    prompt_type,
    prompt_id,
    prompt_version,
    temperature,
    count(*) AS candidates_total,
    avg((selected_by_selector)::int) AS selector_win_rate,
    avg((run_approved)::int) FILTER (WHERE selected_by_selector) AS run_approve_rate,
    avg((run_needs_human)::int) FILTER (WHERE selected_by_selector) AS needs_human_rate,
    avg(selected_iteration_risk_score) FILTER (WHERE selected_by_selector) AS avg_selected_risk
FROM benchmark.generator_candidate_metrics
GROUP BY generator_model, prompt_type, prompt_id, prompt_version, temperature;
