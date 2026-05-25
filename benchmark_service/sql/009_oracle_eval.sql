-- Generated at: 2026-05-20 17:30:24 MSK
-- Migration 009: oracle_eval_runs for golden v1.1 verdicts.

SET search_path TO benchmark, public;

CREATE TABLE IF NOT EXISTS oracle_eval_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    category_id TEXT,
    oracle_type TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('pass', 'fail', 'error')),
    severity TEXT,
    ast_semantic_ok BOOLEAN,
    assertions_jsonb JSONB DEFAULT '[]'::jsonb,
    reasons_jsonb JSONB DEFAULT '[]'::jsonb,
    pipeline_decision TEXT,
    pipeline_final_sql TEXT,
    elapsed_sec NUMERIC,
    error_message TEXT,
    llm_mode TEXT,
    llm_generator_model TEXT,
    dataset_version TEXT DEFAULT '1.1',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_oracle_eval_run_id
    ON oracle_eval_runs(run_id);

CREATE INDEX IF NOT EXISTS idx_oracle_eval_oracle_type
    ON oracle_eval_runs(oracle_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_oracle_eval_case_id
    ON oracle_eval_runs(case_id);
