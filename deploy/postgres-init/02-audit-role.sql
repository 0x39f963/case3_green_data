-- audit_ro: read-only роль для проверки плана запросов.
-- Используется приложением в EXPLAIN sandbox и будущих дашбордах.
-- Роль не имеет права на DML/DDL операции.
-- Таблицы audit_runs и audit_iterations хранят историю прогонов.
-- Запись идет под ролью demo через POSTGRES_DSN.

CREATE ROLE audit_ro WITH LOGIN PASSWORD 'audit_ro_password';

GRANT CONNECT ON DATABASE demo_db TO audit_ro;
GRANT USAGE ON SCHEMA public TO audit_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO audit_ro;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO audit_ro;

CREATE TABLE IF NOT EXISTS audit_runs (
    run_id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    final_sql TEXT,
    approved BOOLEAN,
    iterations_used INTEGER,
    llm_mode TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_iterations (
    run_id TEXT REFERENCES audit_runs(run_id) ON DELETE CASCADE,
    iteration INTEGER NOT NULL,
    sql_query TEXT,
    vulnerabilities JSONB,
    overall_risk_score NUMERIC(4,2),
    audit_summary TEXT,
    revision_notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    PRIMARY KEY (run_id, iteration)
);

CREATE INDEX IF NOT EXISTS audit_iterations_run_idx
    ON audit_iterations(run_id);

CREATE INDEX IF NOT EXISTS audit_runs_created_idx
    ON audit_runs(created_at);

GRANT SELECT ON audit_runs TO audit_ro;
GRANT SELECT ON audit_iterations TO audit_ro;
