-- Generated at: 2026-05-22 14:28:00 MSK
-- TZ-23: stored run-level audit summaries for the batch audit workbench.

SET search_path TO benchmark, public;

CREATE TABLE IF NOT EXISTS run_audit_reports (
    report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    benchmark_run_id TEXT NOT NULL REFERENCES benchmark_runs(benchmark_run_id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'completed',
    source TEXT NOT NULL DEFAULT 'deterministic',
    summary_text TEXT,
    raw_response_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_run_audit_reports_run
    ON run_audit_reports(benchmark_run_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_run_audit_reports_status
    ON run_audit_reports(status, updated_at DESC);
