#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE_PATH="${ROOT_DIR}/configs/openclaw.voice.production.template.json"
PREVIEW_PATH="${ROOT_DIR}/exports/openclaw.voice.production.preview.json"
TARGET_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-${HOME}/.openclaw/openclaw.json}"

INSTALL_PREREQS=0
APPLY_CONFIG=0

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
    --install-prereqs)
      INSTALL_PREREQS=1
      ;;
    --apply)
      APPLY_CONFIG=1
      ;;
    *)
      echo "[error] unknown arg: $arg"
      echo "usage: $0 [--install-prereqs] [--apply]"
      exit 1
      ;;
  esac
done

echo "[info] root: ${ROOT_DIR}"
echo "[info] template: ${TEMPLATE_PATH}"
echo "[info] target: ${TARGET_CONFIG_PATH}"

load_env_file "${ROOT_DIR}/.env"
load_env_file "${HOME}/.openclaw/.env"

if [[ ! -f "${TEMPLATE_PATH}" ]]; then
  echo "[error] missing template: ${TEMPLATE_PATH}"
  exit 1
fi

mkdir -p "${ROOT_DIR}/exports"
mkdir -p "$(dirname "${TARGET_CONFIG_PATH}")"

node_major="0"
if command -v node >/dev/null 2>&1; then
  node_version="$(node -v 2>/dev/null || true)"
  node_major="$(echo "${node_version}" | sed -E 's/^v([0-9]+).*/\1/' || echo 0)"
  echo "[info] node version: ${node_version}"
else
  node_version="missing"
  echo "[warn] node not found in PATH"
fi

if [[ "${node_major}" =~ ^[0-9]+$ ]] && (( node_major < 22 )); then
  echo "[warn] Node.js 22+ is required for current OpenClaw. Current: ${node_version}"
  if (( INSTALL_PREREQS == 1 )); then
    if [[ -s "${HOME}/.nvm/nvm.sh" ]]; then
      # shellcheck disable=SC1091
      source "${HOME}/.nvm/nvm.sh"
      echo "[info] installing Node 22 via nvm..."
      nvm install 22
      nvm alias default 22
      nvm use 22
      hash -r
      echo "[info] node after nvm switch: $(node -v)"
    elif command -v brew >/dev/null 2>&1; then
      echo "[info] installing node@22 via brew..."
      brew install node@22
      echo "[warn] brew installed node@22; ensure it is first on PATH for this shell."
    else
      echo "[warn] cannot auto-install Node 22 (nvm and brew not found)."
    fi
  fi
fi

if command -v openclaw >/dev/null 2>&1; then
  echo "[info] openclaw path: $(command -v openclaw)"
  if ! openclaw --version 2>/dev/null; then
    echo "[warn] openclaw is installed but currently not runnable with this shell environment."
  fi
else
  echo "[warn] openclaw CLI not found"
  if (( INSTALL_PREREQS == 1 )); then
    echo "[info] installing openclaw via upstream installer..."
    curl -fsSL https://openclaw.ai/install.sh | bash
  fi
fi

if command -v whisper-cli >/dev/null 2>&1; then
  echo "[info] whisper-cli found: $(command -v whisper-cli)"
else
  echo "[warn] whisper-cli not found"
  if (( INSTALL_PREREQS == 1 )); then
    if command -v brew >/dev/null 2>&1; then
      echo "[info] installing whisper-cpp via brew..."
      brew install whisper-cpp
    else
      echo "[warn] brew not found; cannot auto-install whisper-cpp."
    fi
  fi
fi

python3 - "${TEMPLATE_PATH}" "${TARGET_CONFIG_PATH}" "${PREVIEW_PATH}" "${APPLY_CONFIG}" <<'PY'
import json
import os
import shutil
import sys
from pathlib import Path

template_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
preview_path = Path(sys.argv[3])
apply_flag = bool(int(sys.argv[4]))

template = json.loads(template_path.read_text(encoding="utf-8"))
existing = {}
if target_path.exists():
    try:
        existing = json.loads(target_path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            existing = {}
    except Exception:
        existing = {}

def deep_merge(dst, src):
    if isinstance(dst, dict) and isinstance(src, dict):
        out = dict(dst)
        for key, value in src.items():
            if key in out:
                out[key] = deep_merge(out[key], value)
            else:
                out[key] = value
        return out
    return src

def delete_path(root, path):
    cur = root
    for key in path[:-1]:
        if not isinstance(cur, dict) or key not in cur:
            return
        cur = cur[key]
    if isinstance(cur, dict):
        cur.pop(path[-1], None)

merged = deep_merge(existing, template)

# Select a working default provider automatically.
has_eleven = bool(str(os.getenv("ELEVENLABS_API_KEY") or "").strip())
has_openai = bool(str(os.getenv("OPENAI_API_KEY") or "").strip())
selected_provider = "microsoft"
if has_eleven:
    selected_provider = "elevenlabs"
elif has_openai:
    selected_provider = "openai"

talk = merged.setdefault("talk", {})
if isinstance(talk, dict):
    talk["provider"] = selected_provider
    providers = talk.setdefault("providers", {})
    if isinstance(providers, dict):
        providers.setdefault("microsoft", {"enabled": True})

messages = merged.setdefault("messages", {})
if isinstance(messages, dict):
    tts = messages.setdefault("tts", {})
    if isinstance(tts, dict):
        tts["provider"] = selected_provider
        providers = tts.setdefault("providers", {})
        if isinstance(providers, dict):
            providers.setdefault("microsoft", {"enabled": True})

# Remove stale keys that are invalid for current OpenClaw schema.
for path in [
    ("messages", "tts", "fallbackProviders"),
    ("talk", "voiceId"),
    ("talk", "modelId"),
    ("talk", "defaultDirective"),
]:
    delete_path(merged, path)

preview_path.parent.mkdir(parents=True, exist_ok=True)
preview_path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"[info] wrote preview: {preview_path}")
print(f"[info] selected voice provider: {selected_provider}")

if apply_flag:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        backup_path = target_path.with_suffix(target_path.suffix + ".bak")
        shutil.copy2(target_path, backup_path)
        print(f"[info] backup: {backup_path}")
    target_path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[info] applied merged config: {target_path}")
PY

if (( APPLY_CONFIG == 1 )) && command -v openclaw >/dev/null 2>&1; then
  validate_ok=0
  if [[ -s "${HOME}/.nvm/nvm.sh" ]]; then
    # shellcheck disable=SC1091
    source "${HOME}/.nvm/nvm.sh"
    nvm use 22 >/dev/null 2>&1 || true
  fi
  if openclaw config validate >/tmp/openclaw_config_validate.log 2>&1; then
    validate_ok=1
    echo "[info] openclaw config validation: ok"
  else
    echo "[error] openclaw config validation failed:"
    sed -n '1,200p' /tmp/openclaw_config_validate.log
  fi
  if (( validate_ok == 0 )); then
    backup_path="${TARGET_CONFIG_PATH}.bak"
    if [[ -f "${backup_path}" ]]; then
      cp -f "${backup_path}" "${TARGET_CONFIG_PATH}"
      echo "[warn] restored backup config: ${backup_path}"
    fi
    exit 1
  fi
fi

echo
echo "[next] export provider/model env vars before launch:"
echo "  export ELEVENLABS_API_KEY='...'"
echo "  export OPENAI_API_KEY='...'"
echo "  export WHISPER_CPP_MODEL='/absolute/path/to/ggml-base.en.bin'"
echo
echo "[next] openclaw checks:"
echo "  openclaw doctor"
echo "  openclaw gateway status"
echo
echo "[next] JARVIS voice prepare endpoint:"
echo "  curl -sS -X POST http://127.0.0.1:8765/api/presence/voice/reply/prepare \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"text\":\"Give me a concise update.\",\"surface_id\":\"voice:owner\",\"session_id\":\"talk-1\"}'"
