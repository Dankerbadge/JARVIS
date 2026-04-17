#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PATH="${ROOT_DIR}/.venv-voice"
if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON_BIN="${PYTHON_BIN}"
elif [[ -x "${VENV_PATH}/bin/python" ]]; then
  PYTHON_BIN="${VENV_PATH}/bin/python"
else
  python3 -m venv "${VENV_PATH}"
  PYTHON_BIN="${VENV_PATH}/bin/python"
fi

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

load_env_file "${ROOT_DIR}/.env"
load_env_file "${HOME}/.openclaw/.env"

if [[ -z "${ELEVENLABS_API_KEY:-}" ]]; then
  echo "[error] ELEVENLABS_API_KEY is not set."
  echo "[next] set ELEVENLABS_API_KEY in one of:"
  echo "  ${ROOT_DIR}/.env"
  echo "  ${HOME}/.openclaw/.env"
  echo "  ${0}"
  exit 2
fi

echo "[step] installing ElevenLabs SDK dependencies..."
"${PYTHON_BIN}" -m pip install --upgrade --disable-pip-version-check elevenlabs python-dotenv

echo "[step] creating/reusing actor clone and binding OpenClaw..."
"${PYTHON_BIN}" "${ROOT_DIR}/scripts/enable_actor_voice_clone.py" \
  --model-id "${JARVIS_ELEVEN_MODEL_ID:-eleven_v3}" \
  --render-sample \
  --sample-out "${ROOT_DIR}/exports/voice_samples/jarvis_actor_match_sample.mp3" \
  "$@"

echo "[step] rendering SDK sample clip..."
"${PYTHON_BIN}" "${ROOT_DIR}/scripts/elevenlabs_tts_sample.py" \
  --model-id "${JARVIS_ELEVEN_MODEL_ID:-eleven_v3}" \
  --out "${ROOT_DIR}/exports/voice_samples/jarvis_actor_match_sample_sdk.mp3"

echo "[ok] JARVIS actor voice provisioning complete."
echo "[ok] sample clip: ${ROOT_DIR}/exports/voice_samples/jarvis_actor_match_sample.mp3"
echo "[ok] sdk sample clip: ${ROOT_DIR}/exports/voice_samples/jarvis_actor_match_sample_sdk.mp3"
