SET search_path TO benchmark, public;

CREATE INDEX IF NOT EXISTS idx_improvement_suggestions_review_target
  ON improvement_suggestions(review_id, target_area);

CREATE INDEX IF NOT EXISTS idx_audit_reviews_created_at
  ON audit_reviews(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_reviews_verdict
  ON audit_reviews(verdict);

CREATE INDEX IF NOT EXISTS idx_audit_reviews_benchmark_run
  ON audit_reviews(benchmark_run_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_step_scores_review_id
  ON audit_step_scores(review_id);
