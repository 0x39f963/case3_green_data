-- Generated at: 2026-05-22 16:35:00 MSK
-- TZ-21: judge-audit analysis jobs and hypothesis registry.

SET search_path TO benchmark, public;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS analysis_jobs (
    job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    benchmark_run_id TEXT NOT NULL,
    backend TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'partial', 'aborted')),
    missing_only BOOLEAN NOT NULL DEFAULT true,
    config_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    log_path TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS case_analysis_reports (
    report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID REFERENCES analysis_jobs(job_id) ON DELETE SET NULL,
    benchmark_run_id TEXT NOT NULL,
    trace_id TEXT NOT NULL REFERENCES benchmark.pipeline_runs(trace_id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    score_id UUID REFERENCES benchmark.case_quality_scores(score_id) ON DELETE SET NULL,
    oracle_eval_id BIGINT REFERENCES benchmark.oracle_eval_runs(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'ok'
        CHECK (status IN ('ok', 'parse_error', 'runtime_error', 'quota_exhausted', 'timeout')),
    summary TEXT,
    root_cause_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
    hypotheses_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_response_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    reviewer_backend TEXT,
    reviewer_model TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS improvement_hypotheses (
    hypothesis_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_key TEXT NOT NULL UNIQUE,
    target_area TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    patch_hint TEXT,
    prompt_type TEXT,
    prompt_id TEXT,
    before_text TEXT,
    after_text TEXT,
    failure_signature TEXT,
    embedding_jsonb JSONB,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    confidence NUMERIC(4,3) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'accepted', 'rejected', 'implemented', 'superseded')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hypothesis_evidence (
    hypothesis_id UUID NOT NULL REFERENCES improvement_hypotheses(hypothesis_id) ON DELETE CASCADE,
    report_id UUID NOT NULL REFERENCES case_analysis_reports(report_id) ON DELETE CASCADE,
    trace_id TEXT NOT NULL,
    score_id UUID,
    oracle_eval_id BIGINT,
    similarity_score NUMERIC(5,4),
    evidence_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (hypothesis_id, report_id)
);

CREATE INDEX IF NOT EXISTS idx_analysis_jobs_run
    ON analysis_jobs(benchmark_run_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_case_analysis_reports_run
    ON case_analysis_reports(benchmark_run_id, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_case_analysis_reports_trace_reviewer
    ON case_analysis_reports(trace_id, reviewer_backend, reviewer_model);

CREATE INDEX IF NOT EXISTS idx_improvement_hypotheses_target
    ON improvement_hypotheses(target_area, severity, status);

CREATE INDEX IF NOT EXISTS idx_improvement_hypotheses_title_trgm
    ON improvement_hypotheses USING gin (title gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_improvement_hypotheses_signature_trgm
    ON improvement_hypotheses USING gin (failure_signature gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_hypothesis_evidence_trace
    ON hypothesis_evidence(trace_id);
