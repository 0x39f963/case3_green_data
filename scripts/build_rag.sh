#!/usr/bin/env bash
# Сборка поисковой памяти Марины.
# Запускает парсер DDL, потом сборщик FAISS-индексов.
# Этот же сценарий зашит в команду сервиса rag-init из docker-compose.yml -
# здесь он продублирован для ручного запуска на хосте.

set -euo pipefail

# Папка, в которой лежит код Марины. По умолчанию относительно репозитория.
RAG_ROOT="${RAG_ROOT:-$(cd "$(dirname "$0")/.." && pwd)/TASK-3/marina-case3-rag}"

cd "$RAG_ROOT"

echo "==> Парсю schema.json"
python rag_pipeline/schema_parser.py

echo "==> Собираю FAISS-индексы"
python rag_pipeline/build_indices.py

echo "Готово. Индексы лежат в $RAG_ROOT/rag_pipeline/indices"
