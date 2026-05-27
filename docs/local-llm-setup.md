# Local LLM + CLI backends setup

Минимальный workflow для запуска `case3_app` с тремя локальными бэкендами:

1. **Local Ollama** (`local_openai` контур) — реальные Ollama-модели через host-published endpoint `host.docker.internal:11434`.
2. **Claude Code CLI** (`claude_cli` контур) — устанавливается в image, auth берётся из `~/.claude` хоста.
3. **Codex CLI** (`codex_cli` контур) — то же самое, auth из `~/.codex` хоста.

## 1. Подготовка хоста

```bash
# Один раз залогиниться на хост-машине (открывает браузерный flow)
claude login           # -> ~/.claude/, ~/.claude.json
codex login            # -> ~/.codex/

# Подтвердить:
claude --version       # -> 2.1.132 (Claude Code)
codex --version        # -> codex-cli 0.129.0
```

## 2. Запуск стека через compose

```bash
cd deploy
docker compose --profile local-llm up -d --build app local-llm-proxy
```

Что произойдёт:

- `local-llm-proxy` (Ollama) поднимется и опубликует порт `11434` на host.
- `app` (case3_app) пересоберётся с установленным claude + codex (см. `deploy/Dockerfile`, `npm install -g`).
- Auth-директории `~/.claude`, `~/.codex`, `~/.claude.json` смонтируются read-write в `/home/appuser/.{claude,codex}` контейнера.
- CLI subprocess запускается с `HOME=/home/appuser`, чтобы Claude Code не падал на root/sudo restrictions.
- `LOCAL_LLM_BASE_URL=http://host.docker.internal:11434/v1` - канонический endpoint для app и batch runtime.
- `TABLE_KNOWLEDGE_V2_ENABLED=true` и `RAG_BRIDGE_BLACKLIST_ROLES=bridge_multiselect` задефолчены.

## 3. Verification

```bash
# Подтянуть реальные локальные модели, если их ещё нет
docker exec case3_local_llm ollama pull qwen3.5:9b
docker exec case3_local_llm ollama pull qwen3:8b
docker exec case3_local_llm ollama pull qwen2.5-coder:7b

# Smoke-check CLI бэкендов
docker exec case3_app python scripts/smoke_cli_backends.py

# Проверить что UI считает их доступными
curl -s http://127.0.0.1:18002/web/api/config \
  | jq '.models[] | select(.backend|test("cli|local_openai")) | {key,backend,provider_model,available_by_config,config_hint}'
```

Ожидаемое:

```json
{"key":"claude-cli","backend":"anthropic_cli","available_by_config":true,"config_hint":""}
{"key":"codex-cli","backend":"codex_cli","available_by_config":true,"config_hint":""}
{"key":"local-qwen3-5-9b","backend":"local_openai","provider_model":"qwen3.5:9b","available_by_config":true,"config_hint":""}
{"key":"local-qwen3-8b","backend":"local_openai","provider_model":"qwen3:8b","available_by_config":true,"config_hint":""}
```

`or-qwen3-5-9b` — это OpenRouter id `qwen/qwen3.5-9b`.
`local-qwen3-5-9b` — это Ollama id `qwen3.5:9b`. Имена похожи, но это разные
provider namespaces; для Ollama правильная команда `ollama pull qwen3.5:9b`.

## 4. Troubleshooting

| Симптом | Причина | Что делать |
|---|---|---|
| `config_error: CLI claude не найден` | image не пересобран после Phase 7 | `docker compose build app` |
| `auth failure: ... Залогинься на хосте` | `~/.claude` / `~/.codex` пустые на хосте | `claude login` / `codex login` |
| `provider_unavailable: Ollama provider unavailable: ... name resolution` | runtime не видит `host.docker.internal` или контейнер поднят без `extra_hosts` | проверить `docker exec case3_app getent hosts host.docker.internal`, затем пересоздать только `app` с compose |
| `available_by_config=false` для CLI presets | binary не в PATH контейнера | проверить `docker exec case3_app which claude codex` |
| `available_by_config=false` для local preset | модель не скачана в Ollama | `docker exec case3_local_llm ollama list`, затем `ollama pull <provider_model>` |
| `CLI claude quota/rate limit` | Claude auth есть, но лимит аккаунта исчерпан | дождаться reset; это не ошибка интеграции |

## 5. Запрет

Не комитить `~/.claude.json` или его копии. Это auth-токен — он остаётся только на хосте + в read-write mount. Если в логах вдруг появилось — redact и пересоздать токен.
