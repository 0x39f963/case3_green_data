-- Generated at: 2026-05-19 15:30:00 MSK
-- Phase 0 latency-fields migration.
-- Расширяет benchmark.llm_calls под детальные тайминги, которые
-- пишет app/llm_provider.py.LLMResponse: per-call walltime,
-- явный retry-loop (SDK auto-retry отключён), OpenRouter HTTP-заголовки
-- (x-openrouter-provider, x-request-id), сам retry_log как jsonb.
--
-- Цель — связать Phase 0 диагностику 20-30с патологии Gemini 3 Flash
-- с benchmark store: данные тех же таймингов, что показывает Latency
-- Breakdown в Telegram HTML, теперь queryable из Postgres.
--
-- Идемпотентно: ADD COLUMN IF NOT EXISTS.

ALTER TABLE benchmark.llm_calls
    ADD COLUMN IF NOT EXISTS walltime_sec NUMERIC(10, 4);

ALTER TABLE benchmark.llm_calls
    ADD COLUMN IF NOT EXISTS retries_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE benchmark.llm_calls
    ADD COLUMN IF NOT EXISTS retry_total_wait_sec NUMERIC(10, 3) NOT NULL DEFAULT 0;

ALTER TABLE benchmark.llm_calls
    ADD COLUMN IF NOT EXISTS retry_log_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE benchmark.llm_calls
    ADD COLUMN IF NOT EXISTS provider_header TEXT;

ALTER TABLE benchmark.llm_calls
    ADD COLUMN IF NOT EXISTS request_id_header TEXT;

ALTER TABLE benchmark.llm_calls
    ADD COLUMN IF NOT EXISTS response_headers_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Индексы под аналитические запросы: «какой провайдер был медленнее»,
-- «сколько вызовов было с retry», «средний walltime по модели».
CREATE INDEX IF NOT EXISTS idx_llm_calls_provider_header
    ON benchmark.llm_calls (provider_header)
    WHERE provider_header IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_llm_calls_walltime_sec
    ON benchmark.llm_calls (walltime_sec)
    WHERE walltime_sec IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_llm_calls_retries
    ON benchmark.llm_calls (retries_count)
    WHERE retries_count > 0;
