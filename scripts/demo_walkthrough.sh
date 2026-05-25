#!/usr/bin/env bash
# Defense demo automation for B3 Step5.

set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
RUN_DOCKER="${DEMO_RUN_DOCKER:-0}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-case3demo}"
COMPOSE_CMD=(docker compose -p "$COMPOSE_PROJECT" -f deploy/docker-compose.yml --env-file .env)
export TRACES_DIR="${TRACES_DIR:-.cursor/!tmp/!ARTEFACTS/2026-05-17/logs/demo_walkthrough_traces}"

run_smokes() {
  SMOKE_FAKE_LLM=true "$PYTHON_BIN" scripts/smoke_pipeline.py
  "$PYTHON_BIN" scripts/smoke_step2.py
  "$PYTHON_BIN" scripts/smoke_step3.py
}

run_local() {
  echo "=== Demo 1: local Ollama ==="
  export LLM_MODE=local_openai
  unset LLM_BACKEND_GENERATOR
  unset LLM_BACKEND_AUDITOR
  export LLM_GENERATOR_MODEL=qwen-coder-7b
  if [[ "$RUN_DOCKER" == "1" ]]; then
    "${COMPOSE_CMD[@]}" --profile local-llm up -d
    sleep "${DEMO_WARMUP_LOCAL_SEC:-30}"
  fi
  run_smokes
}

run_cloud() {
  echo "=== Demo 2: cloud OpenRouter ==="
  export LLM_MODE=prod_demo
  unset LLM_BACKEND_GENERATOR
  unset LLM_BACKEND_AUDITOR
  export LLM_GENERATOR_MODEL=qwen3-8b
  if [[ "$RUN_DOCKER" == "1" ]]; then
    "${COMPOSE_CMD[@]}" --profile local-llm down
    "${COMPOSE_CMD[@]}" up -d
    sleep "${DEMO_WARMUP_CLOUD_SEC:-10}"
  fi
  run_smokes
}

run_local
run_cloud

echo "=== Demo walkthrough smoke: PASS ==="
