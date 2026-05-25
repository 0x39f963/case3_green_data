ALTER TABLE benchmark.dataset_cases
    ADD COLUMN IF NOT EXISTS expected_runtime_decision TEXT,
    ADD COLUMN IF NOT EXISTS expected_runtime_decision_alternatives TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    ADD COLUMN IF NOT EXISTS vuln_class_unmapped BOOLEAN,
    ADD COLUMN IF NOT EXISTS attack_prompt TEXT,
    ADD COLUMN IF NOT EXISTS safe_rewrite TEXT,
    ADD COLUMN IF NOT EXISTS evidence_span TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    ADD COLUMN IF NOT EXISTS faiss_targets JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];
