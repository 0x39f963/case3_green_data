CREATE SCHEMA IF NOT EXISTS benchmark;

CREATE TABLE IF NOT EXISTS benchmark.schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benchmark.datasets (
    dataset_id TEXT NOT NULL,
    version TEXT NOT NULL,
    path TEXT,
    rows_count INTEGER,
    meta_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id, version)
);

CREATE TABLE IF NOT EXISTS benchmark.dataset_cases (
    case_id TEXT PRIMARY KEY,
    dataset_id TEXT,
    dataset_version TEXT,
    family TEXT,
    language TEXT,
    expected_decision TEXT,
    expected_labels TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    task TEXT,
    seed_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (dataset_id, dataset_version)
        REFERENCES benchmark.datasets(dataset_id, version)
        ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS benchmark.benchmark_runs (
    benchmark_run_id TEXT PRIMARY KEY,
    dataset_id TEXT,
    dataset_version TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    model_matrix TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    config_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    total_cases INTEGER,
    completed_cases INTEGER,
    status TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benchmark.pipeline_runs (
    trace_id TEXT PRIMARY KEY,
    benchmark_run_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    model_key TEXT NOT NULL,
    llm_mode TEXT,
    decision TEXT,
    approved BOOLEAN,
    needs_human BOOLEAN,
    human_reason TEXT,
    iterations_used INTEGER,
    overall_risk_score NUMERIC,
    duration_sec NUMERIC,
    generator_backend TEXT,
    generator_model TEXT,
    generator_provider TEXT,
    auditor_backend TEXT,
    auditor_model TEXT,
    final_sql_sha256 TEXT,
    final_sql_len INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (benchmark_run_id, case_id, model_key)
);

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

CREATE TABLE IF NOT EXISTS benchmark.pipeline_steps (
    id BIGSERIAL PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES benchmark.pipeline_runs(trace_id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    node TEXT,
    iteration INTEGER,
    event_started_at TIMESTAMPTZ,
    duration_sec NUMERIC,
    inputs_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    outputs_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    details_summary_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (trace_id, step_index)
);

CREATE TABLE IF NOT EXISTS benchmark.llm_calls (
    id BIGSERIAL PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES benchmark.pipeline_runs(trace_id) ON DELETE CASCADE,
    node TEXT,
    iteration INTEGER,
    role TEXT NOT NULL CHECK (role IN ('generator', 'auditor', 'prompt_check', 'other')),
    backend TEXT,
    provider TEXT,
    model TEXT,
    generation_id TEXT,
    prompt_type TEXT,
    prompt_id TEXT,
    prompt_version INTEGER,
    prompt_sha256 TEXT,
    prompt_chars INTEGER,
    response_chars INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    reasoning_tokens INTEGER,
    cached_tokens INTEGER,
    cache_write_tokens INTEGER,
    cost_usd NUMERIC(12,6),
    cost_credits NUMERIC(12,6),
    usage_source TEXT NOT NULL DEFAULT 'unavailable'
        CHECK (usage_source IN ('inline', 'generation_api', 'unavailable')),
    usage_raw_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    latency_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benchmark.findings (
    id BIGSERIAL PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES benchmark.pipeline_runs(trace_id) ON DELETE CASCADE,
    node TEXT,
    label TEXT,
    severity TEXT,
    confidence NUMERIC,
    detector TEXT,
    evidence_span TEXT,
    payload_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benchmark.faiss_hits (
    id BIGSERIAL PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES benchmark.pipeline_runs(trace_id) ON DELETE CASCADE,
    node TEXT,
    index_name TEXT CHECK (index_name IN ('generation', 'security')),
    source TEXT,
    score NUMERIC,
    table_name TEXT,
    vuln_class TEXT,
    doc_id TEXT,
    content_excerpt TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benchmark.explain_results (
    id BIGSERIAL PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES benchmark.pipeline_runs(trace_id) ON DELETE CASCADE,
    ok BOOLEAN,
    skipped BOOLEAN,
    error TEXT,
    plan_text TEXT,
    plan_jsonb JSONB,
    rows_est NUMERIC,
    cost_est NUMERIC,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benchmark.raw_payloads (
    trace_id TEXT PRIMARY KEY REFERENCES benchmark.pipeline_runs(trace_id) ON DELETE CASCADE,
    payload_jsonb JSONB NOT NULL,
    payload_sha256 TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload_size_bytes INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION benchmark.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER datasets_updated_at
BEFORE UPDATE ON benchmark.datasets
FOR EACH ROW EXECUTE FUNCTION benchmark.set_updated_at();

CREATE OR REPLACE TRIGGER dataset_cases_updated_at
BEFORE UPDATE ON benchmark.dataset_cases
FOR EACH ROW EXECUTE FUNCTION benchmark.set_updated_at();

CREATE OR REPLACE TRIGGER benchmark_runs_updated_at
BEFORE UPDATE ON benchmark.benchmark_runs
FOR EACH ROW EXECUTE FUNCTION benchmark.set_updated_at();

CREATE OR REPLACE TRIGGER pipeline_runs_updated_at
BEFORE UPDATE ON benchmark.pipeline_runs
FOR EACH ROW EXECUTE FUNCTION benchmark.set_updated_at();

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

CREATE OR REPLACE TRIGGER llm_calls_updated_at
BEFORE UPDATE ON benchmark.llm_calls
FOR EACH ROW EXECUTE FUNCTION benchmark.set_updated_at();

CREATE OR REPLACE TRIGGER findings_updated_at
BEFORE UPDATE ON benchmark.findings
FOR EACH ROW EXECUTE FUNCTION benchmark.set_updated_at();

CREATE OR REPLACE TRIGGER faiss_hits_updated_at
BEFORE UPDATE ON benchmark.faiss_hits
FOR EACH ROW EXECUTE FUNCTION benchmark.set_updated_at();

CREATE OR REPLACE TRIGGER explain_results_updated_at
BEFORE UPDATE ON benchmark.explain_results
FOR EACH ROW EXECUTE FUNCTION benchmark.set_updated_at();

CREATE OR REPLACE TRIGGER raw_payloads_updated_at
BEFORE UPDATE ON benchmark.raw_payloads
FOR EACH ROW EXECUTE FUNCTION benchmark.set_updated_at();
