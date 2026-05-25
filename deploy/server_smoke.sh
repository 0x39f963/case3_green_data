#!/usr/bin/env bash
set -euo pipefail

APP_PORT="${APP_PORT:-8000}"
TRACE_VIEWER_PORT="${TRACE_VIEWER_PORT:-8502}"
STREAMLIT_PORT="${STREAMLIT_PORT:-8501}"

APP_URL="${APP_URL:-http://localhost:$APP_PORT}"
TRACE_URL="${TRACE_URL:-http://localhost:$TRACE_VIEWER_PORT}"
STREAMLIT_URL="${STREAMLIT_URL:-http://localhost:$STREAMLIT_PORT}"

check_json() {
  local name="$1"
  local url="$2"
  echo "Checking $name: $url"
  curl -fsS "$url" >/tmp/case3_smoke.json
  python -m json.tool /tmp/case3_smoke.json >/dev/null
}

check_http() {
  local name="$1"
  local url="$2"
  echo "Checking $name: $url"
  curl -fsS "$url" >/dev/null
}

check_json "app health" "$APP_URL/health"
check_json "web config" "$APP_URL/web/api/config"
check_http "web chat shell" "$APP_URL/chat"
check_json "trace viewer health" "$TRACE_URL/health"
check_http "streamlit" "$STREAMLIT_URL"

echo "Server smoke PASS"
