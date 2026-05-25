-- Generated at: 2026-05-21 17:34:13 MSK
-- Runtime registry for versioned system prompts.

CREATE TABLE IF NOT EXISTS system_prompts (
    id TEXT PRIMARY KEY,
    prompt_type TEXT NOT NULL,
    version INTEGER NOT NULL,
    name TEXT NOT NULL,
    text TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'active', 'archived')),
    is_default BOOLEAN NOT NULL DEFAULT false,
    created_by TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (prompt_type, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS system_prompts_one_default_per_type
ON system_prompts (prompt_type)
WHERE is_default = true AND status = 'active';

CREATE OR REPLACE FUNCTION set_system_prompts_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS system_prompts_updated_at ON system_prompts;
CREATE TRIGGER system_prompts_updated_at
BEFORE UPDATE ON system_prompts
FOR EACH ROW EXECUTE FUNCTION set_system_prompts_updated_at();

GRANT SELECT ON system_prompts TO audit_ro;
