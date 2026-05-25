-- Generated at: 2026-05-21 22:28:00 MSK
-- TZ-18 D3: strict per-case smart-judge scores.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS benchmark.case_quality_scores (
    score_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id TEXT NOT NULL REFERENCES benchmark.pipeline_runs(trace_id) ON DELETE CASCADE,
    benchmark_run_id TEXT NOT NULL,
    reviewer_backend TEXT NOT NULL,
    reviewer_model TEXT NOT NULL,
    reviewer_prompt_id TEXT NOT NULL,
    reviewer_prompt_version TEXT,
    reviewer_prompt_sha256 TEXT,

    sql_correctness SMALLINT CHECK (sql_correctness BETWEEN 0 AND 10),
    security SMALLINT CHECK (security BETWEEN 0 AND 10),
    intent_fidelity SMALLINT CHECK (intent_fidelity BETWEEN 0 AND 10),
    schema_usage SMALLINT CHECK (schema_usage BETWEEN 0 AND 10),
    rag_facts_used SMALLINT CHECK (rag_facts_used BETWEEN 0 AND 10),
    decision_rationale SMALLINT CHECK (decision_rationale BETWEEN 0 AND 10),
    performance SMALLINT CHECK (performance BETWEEN 0 AND 10),
    robustness SMALLINT CHECK (robustness BETWEEN 0 AND 10),
    retry_efficiency SMALLINT CHECK (retry_efficiency BETWEEN 0 AND 10),
    overall_score NUMERIC(4,2) GENERATED ALWAYS AS (
        (COALESCE(sql_correctness,0) + COALESCE(security,0)*1.5 + COALESCE(intent_fidelity,0)*1.2
         + COALESCE(schema_usage,0) + COALESCE(rag_facts_used,0)*0.7 + COALESCE(decision_rationale,0)*0.8
         + COALESCE(performance,0)*0.6 + COALESCE(robustness,0)*0.8 + COALESCE(retry_efficiency,0)*0.4) / 8.0
    ) STORED,

    patch_target_area TEXT,
    patch_severity TEXT,
    patch_title TEXT,
    patch_details TEXT,
    patch_hint TEXT,
    patch_examples_jsonb JSONB,

    reviewer_latency_ms INTEGER,
    reviewer_walltime_sec NUMERIC(8,3),
    reviewer_tokens_in INTEGER,
    reviewer_tokens_out INTEGER,
    reviewer_cached_tokens INTEGER,
    reviewer_cost_usd NUMERIC(10,6),
    reviewer_raw_jsonb JSONB,
    reviewer_status TEXT DEFAULT 'ok',
    reviewer_error_text TEXT,

    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_case_quality_scores_run
    ON benchmark.case_quality_scores(benchmark_run_id);

CREATE INDEX IF NOT EXISTS idx_case_quality_scores_patch
    ON benchmark.case_quality_scores(patch_target_area, patch_severity);

CREATE UNIQUE INDEX IF NOT EXISTS idx_case_quality_scores_trace_reviewer
    ON benchmark.case_quality_scores(trace_id, reviewer_backend, reviewer_model);
