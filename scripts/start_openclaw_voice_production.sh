#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${ROOT_DIR}/.jarvis/runtime"
PID_FILE="${RUNTIME_DIR}/jarvis_server.pid"
LOG_FILE="${RUNTIME_DIR}/jarvis_server.log"
OLLAMA_ENDPOINT="${JARVIS_OLLAMA_ENDPOINT:-http://127.0.0.1:11434/api/tags}"

REPO_PATH="${ROOT_DIR}"
DB_PATH="${ROOT_DIR}/.jarvis/jarvis.db"
HOST="127.0.0.1"
PORT="8765"

SKIP_CONFIG=0
OPEN_DASHBOARD=0
FOREGROUND_SERVER=0

load_env_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    return 0
  fi
  set -a
  # shellcheck disable=SC1090
  source "${path}"
  set +a
}

for arg in "$@"; do
  case "$arg" in
    --skip-config)
      SKIP_CONFIG=1
      ;;
    --open-dashboard)
      OPEN_DASHBOARD=1
      ;;
    --foreground-server)
      FOREGROUND_SERVER=1
      ;;
    *)
      echo "[error] unknown arg: $arg"
      echo "usage: $0 [--skip-config] [--open-dashboard] [--foreground-server]"
      exit 1
      ;;
  esac
done

load_env_file "${ROOT_DIR}/.env"
load_env_file "${HOME}/.openclaw/.env"

mkdir -p "${RUNTIME_DIR}"

if ! command -v ollama >/dev/null 2>&1; then
  echo "[error] ollama CLI not found."
  echo "[hint] install with: brew install ollama"
  exit 1
fi

if ! command -v openclaw >/dev/null 2>&1; then
  echo "[error] openclaw CLI not found."
  echo "[hint] run: curl -fsSL https://openclaw.ai/install.sh | bash"
  exit 1
fi

if (( SKIP_CONFIG == 0 )); then
  "${ROOT_DIR}/scripts/setup_openclaw_voice_prod.sh" --apply
fi

echo "[step] validating OpenClaw config..."
openclaw config validate

echo "[step] restarting OpenClaw gateway..."
openclaw gateway restart >/dev/null
openclaw gateway status --deep

wait_for_ollama() {
  local retries=80
  local i
  if curl -sS --max-time 1 "${OLLAMA_ENDPOINT}" >/dev/null 2>&1; then
    return 0
  fi

  if [[ -d "/Applications/Ollama.app" ]]; then
    open "/Applications/Ollama.app" >/dev/null 2>&1 || true
  fi

  for (( i=0; i<retries; i++ )); do
    if curl -sS --max-time 1 "${OLLAMA_ENDPOINT}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done

  nohup ollama serve >/dev/null 2>&1 &
  for (( i=0; i<retries; i++ )); do
    if curl -sS --max-time 1 "${OLLAMA_ENDPOINT}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

pick_ollama_model() {
  local requested="${JARVIS_COGNITION_MODEL:-}"
  if [[ -n "${requested}" ]]; then
    echo "${requested}"
    return 0
  fi

  local models
  models="$(ollama list 2>/dev/null | awk 'NR>1 && NF{print $1}')"

  local candidate
  for candidate in "llama3.2:3b-instruct" "llama3.2:3b" "qwen2.5:3b-instruct" "qwen2.5:3b" "mistral:7b-instruct"; do
    if echo "${models}" | rg -qx "${candidate}" >/dev/null 2>&1; then
      echo "${candidate}"
      return 0
    fi
  done

  local first
  first="$(echo "${models}" | sed -n '1p')"
  if [[ -n "${first}" ]]; then
    echo "${first}"
    return 0
  fi

  echo "[step] no local ollama models detected; pulling llama3.2:3b..."
  ollama pull llama3.2:3b >/dev/null
  echo "llama3.2:3b"
}

stop_existing_server() {
  local pids
  pids="$(lsof -nP -iTCP:${PORT} -sTCP:LISTEN -t 2>/dev/null || true)"
  if [[ -z "${pids}" ]]; then
    return
  fi
  while read -r pid; do
    [[ -z "${pid}" ]] && continue
    local cmd
    cmd="$(ps -p "${pid}" -o command= 2>/dev/null || true)"
    if [[ "${cmd}" == *"jarvis.cli serve"* ]]; then
      kill "${pid}" 2>/dev/null || true
    else
      echo "[error] port ${PORT} is used by non-JARVIS process (pid=${pid}): ${cmd}"
      exit 1
    fi
  done <<< "${pids}"
}

wait_for_health() {
  local retries=60
  local i
  for (( i=0; i<retries; i++ )); do
    if curl -sS "http://${HOST}:${PORT}/api/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

print_active_voice_pack() {
  python3 - "${ROOT_DIR}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
pointer_path = root / ".jarvis" / "voice" / "ACTIVE_VOICE_PACK.json"
if not pointer_path.exists():
    print(f"[warn] active voice pack pointer missing: {pointer_path}")
    sys.exit(0)

try:
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    print(f"[warn] failed to read active voice pack pointer: {exc}")
    sys.exit(0)

pack_raw = str(pointer.get("active_pack") or "").strip()
if not pack_raw:
    print("[warn] ACTIVE_VOICE_PACK.json has no active_pack field.")
    sys.exit(0)
pack_path = Path(pack_raw).expanduser()
if not pack_path.is_absolute():
    pack_path = (root / pack_path).resolve()
profile = str(pointer.get("profile") or "unknown").strip() or "unknown"
updated = str(pointer.get("updated_at") or "").strip()

clip_count = None
duration = None
metadata_path = pack_path / "metadata.json"
if metadata_path.exists():
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        metadata = {}
    if isinstance(metadata, dict):
        if metadata.get("clip_count") is not None:
            try:
                clip_count = int(metadata.get("clip_count"))
            except (TypeError, ValueError):
                clip_count = None
        if metadata.get("total_duration_sec") is not None:
            try:
                duration = round(float(metadata.get("total_duration_sec")), 3)
            except (TypeError, ValueError):
                duration = None

clips_dir = pack_path / "clips"
clip_files = 0
if clips_dir.exists():
    for item in clips_dir.iterdir():
        if item.is_file() and item.suffix.lower() in {".wav", ".mp3", ".m4a", ".flac", ".ogg"}:
            clip_files += 1

if not pack_path.exists():
    print(f"[warn] active voice pack path not found: {pack_path}")
    sys.exit(0)

resolved_clip_count = clip_count if clip_count is not None else clip_files
resolved_duration = duration if duration is not None else 0.0
quality_tier = "none"
if int(resolved_clip_count or 0) >= 20 and float(resolved_duration or 0.0) >= 120.0:
    quality_tier = "production"
elif int(resolved_clip_count or 0) >= 8 and float(resolved_duration or 0.0) >= 45.0:
    quality_tier = "development"
elif int(resolved_clip_count or 0) >= 1:
    quality_tier = "seed"
continuity_ready = bool(int(clip_files or 0) >= 5 and quality_tier in {"development", "production"})

print(f"[voice] active pack: {pack_path.name} (profile={profile})")
print(f"[voice] pack root: {pack_path}")
if resolved_clip_count is not None:
    if duration is not None:
        print(f"[voice] clips={resolved_clip_count}, total_duration_sec={duration}")
    else:
        print(f"[voice] clips={resolved_clip_count}")
elif duration is not None:
    print(f"[voice] total_duration_sec={duration}")
print(f"[voice] clip_files_detected={clip_files}, quality_tier={quality_tier}, continuity_ready={str(continuity_ready).lower()}")
if updated:
    print(f"[voice] pointer updated_at={updated}")
if not continuity_ready:
    print("[warn] voice pack continuity coverage is low; include >=5 clean clips and >=45s total for stable production continuity.")
PY
}

echo "[step] starting JARVIS operator server..."
stop_existing_server

echo "[step] ensuring ollama is reachable..."
if ! wait_for_ollama; then
  echo "[error] ollama API not reachable at ${OLLAMA_ENDPOINT}."
  echo "[hint] open /Applications/Ollama.app and retry."
  exit 1
fi

COG_MODEL="$(pick_ollama_model)"
echo "[step] using cognition backend=ollama model=${COG_MODEL}"
CODEX_MODEL="${JARVIS_CODEX_MODEL:-gpt-5.4}"
echo "[step] codex delegation enabled (model=${CODEX_MODEL}, auto_execute=true)"
print_active_voice_pack

if (( FOREGROUND_SERVER == 1 )); then
  echo "[info] running server in foreground (Ctrl+C stops it)."
  exec env \
    JARVIS_COGNITION_BACKEND=ollama \
    JARVIS_COGNITION_MODEL="${COG_MODEL}" \
    JARVIS_CODEX_DELEGATION_ENABLED=true \
    JARVIS_CODEX_AUTO_EXECUTE=true \
    JARVIS_CODEX_MODEL="${CODEX_MODEL}" \
    python3 -m jarvis.cli serve \
    --repo-path "${REPO_PATH}" \
    --db-path "${DB_PATH}" \
    --host "${HOST}" \
    --port "${PORT}"
fi

nohup env \
  JARVIS_COGNITION_BACKEND=ollama \
  JARVIS_COGNITION_MODEL="${COG_MODEL}" \
  JARVIS_CODEX_DELEGATION_ENABLED=true \
  JARVIS_CODEX_AUTO_EXECUTE=true \
  JARVIS_CODEX_MODEL="${CODEX_MODEL}" \
  python3 -m jarvis.cli serve \
  --repo-path "${REPO_PATH}" \
  --db-path "${DB_PATH}" \
  --host "${HOST}" \
  --port "${PORT}" \
  >"${LOG_FILE}" 2>&1 &
echo "$!" > "${PID_FILE}"

if ! wait_for_health; then
  echo "[error] JARVIS server failed to become healthy."
  echo "[info] tail ${LOG_FILE}:"
  tail -n 80 "${LOG_FILE}" || true
  exit 1
fi

echo "[ok] production voice path is live."
echo "[ok] JARVIS API: http://${HOST}:${PORT}/api/health"
echo "[ok] server pid: $(cat "${PID_FILE}")"
echo "[ok] server log: ${LOG_FILE}"
echo
echo "[next] use OpenClaw Talk Mode (macOS app) as primary voice surface."
echo "[next] fallback harness only: python scripts/jarvis_voice_chat.py --start-server"

if (( OPEN_DASHBOARD == 1 )); then
  openclaw dashboard >/dev/null 2>&1 || true
fi
