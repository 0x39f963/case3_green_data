CREATE TABLE IF NOT EXISTS benchmark.audit_reviews (
    review_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES benchmark.pipeline_runs(trace_id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    model_key TEXT NOT NULL,
    benchmark_run_id TEXT NOT NULL,
    reviewer_backend TEXT NOT NULL,
    reviewer_model TEXT NOT NULL,
    reviewer_prompt_version TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('pass', 'fail', 'needs_review', 'error')),
    reviewer_latency_sec NUMERIC,
    reviewer_tokens_total INTEGER,
    reviewer_cost_usd NUMERIC(12,6),
    raw_response_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (trace_id, reviewer_backend, reviewer_model, reviewer_prompt_version)
);

CREATE TABLE IF NOT EXISTS benchmark.audit_step_scores (
    id BIGSERIAL PRIMARY KEY,
    review_id TEXT NOT NULL REFERENCES benchmark.audit_reviews(review_id) ON DELETE CASCADE,
    node TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'minor_issue', 'major_issue', 'unknown')),
    score SMALLINT CHECK (score >= 0 AND score <= 100),
    evidence TEXT,
    fix_hint TEXT
);

CREATE TABLE IF NOT EXISTS benchmark.audit_sql_correctness (
    review_id TEXT PRIMARY KEY REFERENCES benchmark.audit_reviews(review_id) ON DELETE CASCADE,
    class TEXT NOT NULL CHECK (class IN ('correct', 'unsafe', 'wrong_schema', 'wrong_semantics', 'overblocked', 'underblocked', 'broken_sql', 'unknown')),
    confidence NUMERIC,
    explanation TEXT,
    expected_vs_actual_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS benchmark.improvement_suggestions (
    suggestion_id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL REFERENCES benchmark.audit_reviews(review_id) ON DELETE CASCADE,
    target_area TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('P0', 'P1', 'P2')),
    title TEXT NOT NULL,
    details TEXT NOT NULL,
    patch_hint TEXT,
    linked_node TEXT,
    content_sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benchmark.audit_suggestion_clusters (
    cluster_id TEXT PRIMARY KEY,
    target_area TEXT NOT NULL,
    label TEXT NOT NULL,
    exemplar_suggestion_id TEXT,
    size INTEGER NOT NULL DEFAULT 0,
    members_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS benchmark.kb_patch_candidates (
    patch_id TEXT PRIMARY KEY,
    suggestion_id TEXT NOT NULL REFERENCES benchmark.improvement_suggestions(suggestion_id) ON DELETE CASCADE,
    target_file TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('add', 'modify', 'remove')),
    payload_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'reviewed', 'merged', 'rejected')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_reviews_benchmark_run_idx
    ON benchmark.audit_reviews(benchmark_run_id);

CREATE INDEX IF NOT EXISTS audit_reviews_reviewer_idx
    ON benchmark.audit_reviews(reviewer_backend, reviewer_model);

CREATE INDEX IF NOT EXISTS improvement_suggestions_target_area_idx
    ON benchmark.improvement_suggestions(target_area);

CREATE INDEX IF NOT EXISTS improvement_suggestions_severity_idx
    ON benchmark.improvement_suggestions(severity);

CREATE INDEX IF NOT EXISTS improvement_suggestions_content_sha256_idx
    ON benchmark.improvement_suggestions(content_sha256);

CREATE INDEX IF NOT EXISTS audit_sql_correctness_class_idx
    ON benchmark.audit_sql_correctness(class);

CREATE OR REPLACE TRIGGER audit_reviews_updated_at
BEFORE UPDATE ON benchmark.audit_reviews
FOR EACH ROW EXECUTE FUNCTION benchmark.set_updated_at();

CREATE OR REPLACE TRIGGER improvement_suggestions_updated_at
BEFORE UPDATE ON benchmark.improvement_suggestions
FOR EACH ROW EXECUTE FUNCTION benchmark.set_updated_at();

CREATE OR REPLACE TRIGGER kb_patch_candidates_updated_at
BEFORE UPDATE ON benchmark.kb_patch_candidates
FOR EACH ROW EXECUTE FUNCTION benchmark.set_updated_at();
