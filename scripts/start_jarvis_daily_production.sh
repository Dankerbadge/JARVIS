#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_PATH="$(cd "${SCRIPT_DIR}/.." && pwd)"
DB_PATH="${REPO_PATH}/.jarvis/jarvis.db"
HOST="127.0.0.1"
PORT="8765"
BACKGROUND="false"
RESTART="true"
LOW_POWER="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-path)
      REPO_PATH="$2"
      shift 2
      ;;
    --db-path)
      DB_PATH="$2"
      shift 2
      ;;
    --host)
      HOST="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --background)
      BACKGROUND="true"
      shift
      ;;
    --low-power)
      LOW_POWER="true"
      shift
      ;;
    --no-restart)
      RESTART="false"
      shift
      ;;
    *)
      echo "[error] unknown argument: $1" >&2
      echo "usage: $0 [--repo-path PATH] [--db-path PATH] [--host HOST] [--port PORT] [--background] [--low-power] [--no-restart]" >&2
      exit 1
      ;;
  esac
done

REPO_PATH="$(cd "${REPO_PATH}" && pwd)"
mkdir -p "${REPO_PATH}/.jarvis/runtime"
cd "${REPO_PATH}"

if [[ ! -f "${REPO_PATH}/.venv-voice/bin/activate" ]]; then
  echo "[error] missing virtualenv: ${REPO_PATH}/.venv-voice" >&2
  exit 1
fi

source "${REPO_PATH}/.venv-voice/bin/activate"
export PYTHONPATH="${REPO_PATH}:${PYTHONPATH:-}"

if [[ -f "${REPO_PATH}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO_PATH}/.env"
  set +a
fi

# Frozen daily stack defaults.
export JARVIS_COGNITION_BACKEND="${JARVIS_COGNITION_BACKEND:-ollama}"
export JARVIS_COGNITION_MODEL="${JARVIS_COGNITION_MODEL:-qwen3:14b}"
export JARVIS_COGNITION_AUTO_PREFER="${JARVIS_COGNITION_AUTO_PREFER:-qwen3:14b,qwen3:30b,gemma3:27b,qwen3:8b,llama3.2:3b-instruct,llama3.2:3b,qwen2.5:3b-instruct,qwen2.5:3b,mistral:7b-instruct}"
export JARVIS_PRESENCE_MODEL_FIRST="${JARVIS_PRESENCE_MODEL_FIRST:-true}"
export JARVIS_PRESENCE_MODEL_TIMEOUT_SECONDS="${JARVIS_PRESENCE_MODEL_TIMEOUT_SECONDS:-20}"
export JARVIS_DIALOGUE_EMBED_RERANK_ENABLED="${JARVIS_DIALOGUE_EMBED_RERANK_ENABLED:-true}"
export JARVIS_DIALOGUE_EMBED_MODEL="${JARVIS_DIALOGUE_EMBED_MODEL:-mxbai-embed-large}"
export JARVIS_DIALOGUE_FLAG_RERANK_ENABLED="${JARVIS_DIALOGUE_FLAG_RERANK_ENABLED:-false}"
export JARVIS_DIALOGUE_FLAG_RERANK_MODEL="${JARVIS_DIALOGUE_FLAG_RERANK_MODEL:-BAAI/bge-reranker-v2-m3}"
export JARVIS_DIALOGUE_RETRIEVE_LIMIT="${JARVIS_DIALOGUE_RETRIEVE_LIMIT:-8}"
export JARVIS_DIALOGUE_RETRIEVE_CANDIDATE_LIMIT="${JARVIS_DIALOGUE_RETRIEVE_CANDIDATE_LIMIT:-32}"
export JARVIS_DIALOGUE_EMBED_BLEND_WEIGHT="${JARVIS_DIALOGUE_EMBED_BLEND_WEIGHT:-0.30}"
export JARVIS_DIALOGUE_FLAG_BLEND_WEIGHT="${JARVIS_DIALOGUE_FLAG_BLEND_WEIGHT:-0.40}"
export JARVIS_DIALOGUE_MIN_SCORE="${JARVIS_DIALOGUE_MIN_SCORE:-0.00}"
export JARVIS_OLLAMA_ENDPOINT="${JARVIS_OLLAMA_ENDPOINT:-http://127.0.0.1:11434/api/generate}"
export JARVIS_RUNTIME_STATUS_WARM_PROBE_TEXT="${JARVIS_RUNTIME_STATUS_WARM_PROBE_TEXT:-warm probe: reply in one short sentence confirming you are online}"
export JARVIS_RUNTIME_STATUS_WARM_PROBE_ATTEMPTS="${JARVIS_RUNTIME_STATUS_WARM_PROBE_ATTEMPTS:-3}"
export JARVIS_RUNTIME_STATUS_WARM_PROBE_TIMEOUT_SECONDS="${JARVIS_RUNTIME_STATUS_WARM_PROBE_TIMEOUT_SECONDS:-30}"
export JARVIS_RUNTIME_STATUS_WARM_PROBE_DELAY_SECONDS="${JARVIS_RUNTIME_STATUS_WARM_PROBE_DELAY_SECONDS:-1.0}"

if [[ "${LOW_POWER}" == "true" ]]; then
  echo "[profile] low-power mode enabled"
  export JARVIS_LOW_POWER_MODE="true"
  export JARVIS_PRESENCE_MODEL_TIMEOUT_SECONDS="${JARVIS_PRESENCE_MODEL_TIMEOUT_SECONDS:-12}"
  export JARVIS_PARTNER_DIALOGUE_TIMEOUT_SECONDS="${JARVIS_PARTNER_DIALOGUE_TIMEOUT_SECONDS:-10}"
  export JARVIS_PARTNER_DEEP_TIMEOUT_SECONDS="${JARVIS_PARTNER_DEEP_TIMEOUT_SECONDS:-12}"
  export JARVIS_PARTNER_RETRIEVAL_MIN_SNIPPETS="${JARVIS_PARTNER_RETRIEVAL_MIN_SNIPPETS:-3}"
  export JARVIS_PARTNER_RETRIEVAL_TARGET_SNIPPETS="${JARVIS_PARTNER_RETRIEVAL_TARGET_SNIPPETS:-4}"
  export JARVIS_PARTNER_DEEP_RETRIEVAL_MIN_SNIPPETS="${JARVIS_PARTNER_DEEP_RETRIEVAL_MIN_SNIPPETS:-3}"
  export JARVIS_PARTNER_DEEP_RETRIEVAL_TARGET_SNIPPETS="${JARVIS_PARTNER_DEEP_RETRIEVAL_TARGET_SNIPPETS:-4}"
  export JARVIS_DIALOGUE_FLAG_RERANK_ENABLED="false"
  export JARVIS_PARTNER_DEEP_MODEL=""
  export JARVIS_PARTNER_DIALOGUE_ESCALATION_MODEL=""
  export JARVIS_PARTNER_DIALOGUE_MODEL="${JARVIS_PARTNER_DIALOGUE_MODEL:-qwen3:14b}"
fi

if ! command -v ollama >/dev/null 2>&1; then
  echo "[error] ollama CLI not found in PATH." >&2
  exit 1
fi

if ! curl -fsS --max-time 1 "$(python3 - <<'PY'
import os, urllib.parse
endpoint = os.getenv("JARVIS_OLLAMA_ENDPOINT", "http://127.0.0.1:11434/api/generate")
parsed = urllib.parse.urlparse(endpoint)
if not parsed.scheme or not parsed.netloc:
    print("http://127.0.0.1:11434/api/tags")
else:
    print(urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/api/tags", "", "", "")))
PY
)" >/dev/null 2>&1; then
  echo "[init] starting Ollama service..."
  nohup ollama serve >/dev/null 2>&1 &
  sleep 2
fi

echo "[check] runtime status preflight..."
python3 "${REPO_PATH}/scripts/jarvis_runtime_status.py" \
  --repo-path "${REPO_PATH}" \
  --db-path "${DB_PATH}" \
  --strict >/dev/null

if [[ "${RESTART}" == "true" ]]; then
  LISTEN_PID="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN || true)"
  if [[ -n "${LISTEN_PID}" ]]; then
    echo "[init] stopping process on port ${PORT} (pid ${LISTEN_PID})..."
    kill "${LISTEN_PID}" 2>/dev/null || true
    sleep 1
  fi
fi

LOG_PATH="${REPO_PATH}/.jarvis/runtime/jarvis_daily_server.log"
PID_PATH="${REPO_PATH}/.jarvis/runtime/jarvis_daily_server.pid"

if [[ "${BACKGROUND}" == "true" ]]; then
  echo "[start] launching JARVIS server in background..."
  nohup python3 -m jarvis.cli serve \
    --repo-path "${REPO_PATH}" \
    --db-path "${DB_PATH}" \
    --host "${HOST}" \
    --port "${PORT}" >"${LOG_PATH}" 2>&1 &
  SERVER_PID="$!"
  echo "${SERVER_PID}" > "${PID_PATH}"
  sleep 1
  if ! ps -p "${SERVER_PID}" >/dev/null 2>&1; then
    echo "[error] server process exited immediately (pid ${SERVER_PID})." >&2
    echo "[error] tailing log ${LOG_PATH}:" >&2
    tail -n 80 "${LOG_PATH}" >&2 || true
    exit 1
  fi

  READY="false"
  for _ in {1..20}; do
    if curl -fsS "http://${HOST}:${PORT}/api/health" >/dev/null 2>&1; then
      READY="true"
      break
    fi
    sleep 1
  done
  if [[ "${READY}" != "true" ]]; then
    echo "[error] server did not become healthy within 20s." >&2
    echo "[error] tailing log ${LOG_PATH}:" >&2
    tail -n 120 "${LOG_PATH}" >&2 || true
    exit 1
  fi

  echo "[warmup] priming model-backed reply path..."
  WARMUP_PAYLOAD='{"text":"warmup: reply in one short sentence confirming online readiness","surface_id":"dm:owner","session_id":"startup-warmup","context":{"source":"startup_warmup","force_model_presence_reply":true,"disable_codex_auto_delegate":true,"disable_self_inquiry":true}}'
  WARMUP_OK="false"
  for _ in {1..3}; do
    if curl -fsS --max-time 18 \
      -X POST "http://${HOST}:${PORT}/api/presence/reply/prepare" \
      -H 'Content-Type: application/json' \
      -d "${WARMUP_PAYLOAD}" >/dev/null 2>&1; then
      WARMUP_OK="true"
      break
    fi
    sleep 1
  done
  if [[ "${WARMUP_OK}" != "true" ]]; then
    echo "[warn] warmup request timed out or failed after retries; continuing with server online." >&2
  fi

  # Touch retrieval snapshot endpoint after warmup to ensure read path is live.
  if ! curl -fsS --max-time 5 "http://${HOST}:${PORT}/api/presence/dialogue/retrieval" >/dev/null 2>&1; then
    echo "[warn] retrieval status probe failed after warmup; continuing." >&2
  fi

  STRICT_OK="false"
  for _ in {1..3}; do
    if python3 "${REPO_PATH}/scripts/jarvis_runtime_status.py" \
      --repo-path "${REPO_PATH}" \
      --db-path "${DB_PATH}" \
      --host "${HOST}" \
      --port "${PORT}" \
      --check-server \
      --strict >/dev/null; then
      STRICT_OK="true"
      break
    fi
    sleep 2
  done
  if [[ "${STRICT_OK}" != "true" ]]; then
    echo "[error] strict runtime gate failed after warmup." >&2
    echo "[error] runtime status snapshot:" >&2
    python3 "${REPO_PATH}/scripts/jarvis_runtime_status.py" \
      --repo-path "${REPO_PATH}" \
      --db-path "${DB_PATH}" \
      --host "${HOST}" \
      --port "${PORT}" \
      --check-server >&2 || true
    exit 1
  fi

  echo "[ok] server pid: ${SERVER_PID}"
  echo "[ok] log: ${LOG_PATH}"
  echo "[ok] status: http://${HOST}:${PORT}/api/health"
  exit 0
fi

echo "[start] launching JARVIS server in foreground..."
exec python3 -m jarvis.cli serve \
  --repo-path "${REPO_PATH}" \
  --db-path "${DB_PATH}" \
  --host "${HOST}" \
  --port "${PORT}"
