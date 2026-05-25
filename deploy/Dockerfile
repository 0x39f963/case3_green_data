# Образ нашей сборки. Один Dockerfile собирает все Python-сервисы:
# app, ui, rag-init, trace-viewer, eval. Различает их docker-compose командой.
# Базируемся на тонком python:3.11-slim чтобы образ оставался компактным.

FROM python:3.11-slim AS base

# Не пишем .pyc, не буферизуем stdout, чтобы логи сразу видеть в docker logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Системные пакеты, которые нужны нашим зависимостям.
# libpq для psycopg2, build-essential для редких сборок из исходников,
# curl для health-checkов через docker.
# Node + npm нужны только если явно собираем образ со встроенными
# Claude Code / Codex CLI. Для локального стенда CLI обычно монтируются
# из хостового ~/.local, чтобы не зависеть от сети при пересборке.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        ca-certificates \
        ripgrep \
        git \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# CLI-провайдеры запускаются subprocess'ом без root: Claude Code запрещает
# --dangerously-skip-permissions под root/sudo, а Codex/Claude auth лежит в
# домашней директории пользователя.
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash appuser \
    && mkdir -p /home/appuser/.claude /home/appuser/.codex /home/appuser/tmp \
    && chown -R appuser:appuser /home/appuser

# Phase 7 (2026-05-21): фоновые CLI-бэкенды (Claude Code / Codex)
# устанавливаются глобально по тем же версиям, что и в anvi-orchestra,
# так чтобы UI presets `claude_cli` и `codex_cli` действительно работали
# внутри контейнера. Auth-токены монтируются как volumes из docker-compose.
ARG CODEX_CLI_VERSION=0.129.0
ARG CLAUDE_CODE_VERSION=2.1.132
ARG INSTALL_CLI_TOOLS=false
ENV CODEX_CLI_VERSION=${CODEX_CLI_VERSION} \
    CLAUDE_CODE_VERSION=${CLAUDE_CODE_VERSION}
RUN if [ "${INSTALL_CLI_TOOLS}" = "true" ]; then \
        npm install -g \
            "@openai/codex@${CODEX_CLI_VERSION}" \
            "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
        && npm cache clean --force \
        && rm -rf /root/.npm \
        && codex --version \
        && claude --version; \
    else \
        echo "Skipping npm CLI install; mount host claude/codex binaries for local compose."; \
    fi

WORKDIR /app

# Сначала только зависимости. Это дает докеру переиспользовать слой при
# любых изменениях исходников, ускоряет повторные сборки.
COPY requirements.txt /app/requirements.txt
COPY TASK-3/marina-case3-rag/requirements.txt /app/marina-requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir -r /app/marina-requirements.txt

ARG HF_E5_MODEL=intfloat/multilingual-e5-small
RUN mkdir -p /app/data/model_cache \
    && python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${HF_E5_MODEL}', cache_folder='/app/data/model_cache')"

# Теперь весь исходник. Структура каталога в контейнере повторяет репозиторий,
# чтобы относительные импорты работали без сюрпризов.
COPY app /app/app
COPY benchmark_service /app/benchmark_service
COPY scripts /app/scripts
COPY TASK-3 /app/TASK-3
# Runtime RAG metadata lives outside app/: bridge-table filtering depends on
# data/rag/v2/table_knowledge_index_v2.csv, and schema overlay depends on
# deploy/schema_overlay*.json. Without these files the container passes host
# tests but the live UI falls back to legacy ms_* bridge hits.
COPY deploy /app/deploy
COPY data/rag/v2 /app/data/rag/v2
COPY data/eval /app/data/eval

# Гарантируем, что папки для рантайма существуют. Они монтируются как тома
# из docker-compose, но если запускать образ без томов - все равно работает.
RUN mkdir -p /app/data/traces /app/data/model_cache

# Делаем код Марины и наш app видимым для python -m.
ENV PYTHONPATH=/app:/app/TASK-3/marina-case3-rag

# По умолчанию открываем FastAPI приложения app/api.py. UI и trace-viewer
# переопределяют команду в docker-compose.yml.
EXPOSE 8000 8501 8502

CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
