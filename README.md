Generated at: 2026-05-20 16:33:07 MSK

# Case 3. SQL Security System

Полная документация проекта: [docs/README.md](docs/README.md).

## Endpoints и порты

| Порт | Сервис | Источник | Назначение |
|---|---|---|---|
| 8000 | Docker `app` (FastAPI) | `deploy/docker-compose.yml::app` | Канонический endpoint: `POST /run`, `GET /health`, web UI `/`, `/chat`, web API `/web/api/*`. |
| 18000 | host uvicorn (legacy) | ручной запуск на хосте | Prod_demo для заказчика. Тот же контракт, но без перезапуска не подхватит обновления `app/api.py`. Переход на Docker app:8000 - предпочтительный путь, 18000 деприкейтится. |
| 18081 | Docker `benchmark_api` (FastAPI) | `deploy/benchmark-compose.yml::benchmark_api` | Внутренний benchmark store: `/v1/benchmarks/*`, `/v1/datasets` и другие служебные ручки. Не для UI-пользователей. |
| 11434 | `local-llm-proxy` (Ollama) | `deploy/docker-compose.yml::local-llm-proxy` | Локальный LLM proxy (Qwen3 8B). Профиль `local-llm`, не запускается по умолчанию. |
