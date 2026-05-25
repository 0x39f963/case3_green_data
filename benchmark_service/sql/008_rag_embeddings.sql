-- Generated at: 2026-05-19 15:55:00 MSK
-- Phase 2 — Универсальная таблица RAG-эмбеддингов в Postgres.
--
-- Зеркалит подход конкурента (marussiakuz1/case3-rag,
-- gd_app.rag_embeddings): один многоиндексный store с index_name
-- selector + float4[] эмбеддинги + JSONB metadata + текст.
-- Поиск — через numpy cosine на стороне Python (см. app/rag_adapter).
--
-- На MVP Phase 2 используем только index_name='solutions' для уроков
-- мета-аудитора. Позже сюда же можно перенести 'generation' (219 docs)
-- и 'security' (22 docs) из FAISS-файлов upstream marina-case3-rag
-- одним скриптом scripts/migrate_faiss_to_postgres.py.
--
-- Идемпотентно: CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS benchmark.rag_embeddings (
    id            BIGSERIAL PRIMARY KEY,
    index_name    TEXT NOT NULL,
    text          TEXT NOT NULL,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding     FLOAT4[] NOT NULL,
    source_trace_id TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rag_embeddings_index_name
    ON benchmark.rag_embeddings (index_name);

CREATE INDEX IF NOT EXISTS idx_rag_embeddings_source_trace
    ON benchmark.rag_embeddings (source_trace_id)
    WHERE source_trace_id IS NOT NULL;

-- Флаг meta_audited в pipeline_runs — чтобы cron meta_auditor не
-- разбирал один trace дважды. Default false для существующих строк.
ALTER TABLE benchmark.pipeline_runs
    ADD COLUMN IF NOT EXISTS meta_audited BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_meta_audited
    ON benchmark.pipeline_runs (meta_audited, created_at)
    WHERE meta_audited = FALSE;
