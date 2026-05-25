#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
PROJECT="${COMPOSE_PROJECT_NAME:-case3}"
RUN_RAG_INIT="${RUN_RAG_INIT:-1}"
ENABLE_LOCAL_LLM="${ENABLE_LOCAL_LLM:-0}"
ENABLE_BENCHMARK="${ENABLE_BENCHMARK:-0}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE"
  echo "Create it from .env.example and fill secrets before deploy."
  exit 2
fi

compose() {
  docker compose -p "$PROJECT" -f "$ROOT/deploy/docker-compose.yml" --env-file "$ENV_FILE" "$@"
}

echo "Preparing postgres init SQL"
python "$ROOT/scripts/prepare_init_sql.py"

echo "Building app image"
compose build app

echo "Starting postgres"
compose up -d postgres

if [[ "$RUN_RAG_INIT" == "1" ]]; then
  echo "Building RAG indices"
  compose --profile init up rag-init
fi

if [[ "$ENABLE_LOCAL_LLM" == "1" ]]; then
  echo "Starting local LLM proxy"
  compose --profile local-llm up -d local-llm-proxy
fi

echo "Starting app, Streamlit UI and trace viewer"
compose up -d app ui trace-viewer

if [[ "$ENABLE_BENCHMARK" == "1" ]]; then
  BENCH_ENV="${BENCH_ENV:-$ROOT/deploy/benchmark.env}"
  if [[ ! -f "$BENCH_ENV" ]]; then
    echo "Missing benchmark env file: $BENCH_ENV"
    exit 2
  fi
  docker compose -p "${PROJECT}-bench" -f "$ROOT/deploy/benchmark-compose.yml" --env-file "$BENCH_ENV" up -d --build
fi

echo "Deploy complete. Run deploy/server_smoke.sh for checks."
