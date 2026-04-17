#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MILESTONE="${1:-M9}"
OUT_ZIP="${2:-${ROOT_DIR}/../JARVIS_${MILESTONE}_CLEAN_WIRED.zip}"
PKG_NAME="JARVIS_${MILESTONE}_CLEAN_WIRED"

if [[ -z "${MILESTONE}" ]]; then
  echo "Milestone cannot be empty." >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
STAGE_DIR="${TMP_DIR}/${PKG_NAME}"
mkdir -p "${STAGE_DIR}"

cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

# Explicit allowlist: only package source, tests, and milestone/spec docs.
rsync -a --prune-empty-dirs \
  --include='/README.md' \
  --include='/SYSTEM_SPEC.md' \
  --include='/SYSTEM_SPEC_BASELINE.md' \
  --include='/MILESTONE_*_SPEC.md' \
  --include='/scripts/***' \
  --include='/.github/' \
  --include='/.github/workflows/' \
  --include='/.github/workflows/***' \
  --include='/jarvis/***' \
  --include='/tests/***' \
  --exclude='*' \
  "${ROOT_DIR}/" "${STAGE_DIR}/"

# Remove non-source runtime/cache artifacts if present.
find "${STAGE_DIR}" -type d \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.mypy_cache' \) -prune -exec rm -rf {} +
find "${STAGE_DIR}" -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '.DS_Store' \) -delete

MANIFEST_PATH="${STAGE_DIR}/RELEASE_MANIFEST.json"
REPORT_PATH="${STAGE_DIR}/RELEASE_SCAN_REPORT.json"
python3 "${ROOT_DIR}/scripts/verify_release_clean.py" \
  --root "${STAGE_DIR}" \
  --manifest "${MANIFEST_PATH}" \
  --report "${REPORT_PATH}" \
  --strict

mkdir -p "$(dirname "${OUT_ZIP}")"
rm -f "${OUT_ZIP}"

(
  cd "${TMP_DIR}"
  zip -rq "${OUT_ZIP}" "${PKG_NAME}"
)

echo "Built: ${OUT_ZIP}"
shasum -a 256 "${OUT_ZIP}"
ls -lh "${OUT_ZIP}"
cp "${MANIFEST_PATH}" "${OUT_ZIP%.zip}.manifest.json"
cp "${REPORT_PATH}" "${OUT_ZIP%.zip}.scan_report.json"
echo "Manifest: ${OUT_ZIP%.zip}.manifest.json"
echo "Scan report: ${OUT_ZIP%.zip}.scan_report.json"
