-- Generated at: 2026-05-21 22:28:00 MSK
-- TZ-18 D2: model tariff table and cost source marker.

CREATE TABLE IF NOT EXISTS benchmark.model_tariffs (
    preset_key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    backend TEXT NOT NULL,
    provider_model TEXT NOT NULL,
    price_per_1k_in NUMERIC(10,6) DEFAULT 0,
    price_per_1k_out NUMERIC(10,6) DEFAULT 0,
    price_per_1k_cached NUMERIC(10,6) DEFAULT 0,
    price_per_1k_reasoning NUMERIC(10,6) DEFAULT 0,
    currency TEXT DEFAULT 'USD',
    valid_from TIMESTAMPTZ DEFAULT now(),
    source TEXT,
    is_quota_equivalent BOOLEAN DEFAULT false,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE benchmark.llm_calls
    ADD COLUMN IF NOT EXISTS cost_source TEXT;

CREATE INDEX IF NOT EXISTS idx_llm_calls_cost_source
    ON benchmark.llm_calls(cost_source)
    WHERE cost_source IS NOT NULL;

INSERT INTO benchmark.model_tariffs (
    preset_key, display_name, backend, provider_model,
    price_per_1k_in, price_per_1k_out, price_per_1k_cached, price_per_1k_reasoning,
    currency, valid_from, source, is_quota_equivalent, notes
) VALUES
    ('openrouter-gemini-2-5-flash', 'Gemini 2.5 Flash', 'openrouter', 'google/gemini-2.5-flash',
     0.000300, 0.002500, 0.000000, 0.000000, 'USD', now(), 'openrouter_pricing', false, 'Seed checked 2026-05-21 against public OpenRouter pricing pages.'),
    ('openrouter-gemini-2-5-pro', 'Gemini 2.5 Pro', 'openrouter', 'google/gemini-2.5-pro',
     0.001250, 0.010000, 0.000125, 0.000000, 'USD', now(), 'openrouter_pricing', false, 'Seed checked 2026-05-21 against public OpenRouter pricing pages.'),
    ('openrouter-qwen-235b', 'Qwen3 235B', 'openrouter', 'qwen/qwen-3-235b-a22b',
     0.000455, 0.001820, 0.000000, 0.000000, 'USD', now(), 'openrouter_pricing', false, 'Route-level OpenRouter public price snapshot.'),
    ('claude-cli-sonnet', 'Claude Sonnet 4.6', 'claude_cli', 'claude-sonnet-4-6',
     0.003000, 0.015000, 0.000300, 0.000000, 'USD', now(), 'anthropic_pricing', true, 'Quota-equivalent: billed from subscription/quota, USD is reporting equivalent.'),
    ('codex-cli-gpt-5-5', 'Codex gpt-5.5', 'codex_cli', 'gpt-5-5',
     0.005000, 0.030000, 0.000500, 0.000000, 'USD', now(), 'openai_pricing', true, 'Quota-equivalent reporting seed for Codex CLI.'),
    ('codex_cli-gpt-5.5', 'Codex gpt-5.5', 'codex_cli', 'gpt-5.5',
     0.005000, 0.030000, 0.000500, 0.000000, 'USD', now(), 'openai_pricing', true, 'Compatibility key for raw backend-model lookup.'),
    ('local-qwen3-5-9b', 'Qwen3.5 9B (local)', 'local_ollama', 'qwen3.5:9b',
     0.000000, 0.000000, 0.000000, 0.000000, 'USD', now(), 'manual', false, 'Self-hosted, zero marginal token tariff.')
ON CONFLICT (preset_key) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    backend = EXCLUDED.backend,
    provider_model = EXCLUDED.provider_model,
    price_per_1k_in = EXCLUDED.price_per_1k_in,
    price_per_1k_out = EXCLUDED.price_per_1k_out,
    price_per_1k_cached = EXCLUDED.price_per_1k_cached,
    price_per_1k_reasoning = EXCLUDED.price_per_1k_reasoning,
    currency = EXCLUDED.currency,
    source = EXCLUDED.source,
    is_quota_equivalent = EXCLUDED.is_quota_equivalent,
    notes = EXCLUDED.notes,
    updated_at = now();
