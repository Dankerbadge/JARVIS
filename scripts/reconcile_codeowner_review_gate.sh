#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
usage: reconcile_codeowner_review_gate.sh [--repo-slug <owner/repo>] [--branch <name>] [--min-collaborators <int>] [--required-status-check <context>]... [--required-status-check-strict <true|false|preserve>] [--source-workflow-run-id <id>] [--source-workflow-run-conclusion <value>] [--source-workflow-name <name>] [--source-workflow-event <event>] [--source-workflow-run-url <url>] [--apply]

Reconciles GitHub branch-protection review requirements against collaborator count:
- enable when collaborator_count >= min_collaborators
- disable otherwise (single-maintainer deadlock safety)

Defaults:
- repo slug: inferred from `git remote get-url origin`
- branch: main
- min collaborators: 2
- required status checks: unchanged (unless one or more `--required-status-check` values are provided)
- required status-check strict mode: preserve current branch-protection strict mode (`--required-status-check-strict` overrides)
- source workflow provenance: none (unless explicitly provided)
- mode: dry-run (set --apply to patch branch protection)
USAGE
}

REPO_SLUG=""
BRANCH="main"
MIN_COLLABORATORS=2
REQUIRED_STATUS_CHECKS=()
REQUIRED_STATUS_CHECK_STRICT_MODE="preserve"
SOURCE_WORKFLOW_RUN_ID=""
SOURCE_WORKFLOW_RUN_CONCLUSION=""
SOURCE_WORKFLOW_NAME=""
SOURCE_WORKFLOW_EVENT=""
SOURCE_WORKFLOW_RUN_URL=""
APPLY_MODE="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-slug)
      if [[ $# -lt 2 ]]; then
        echo "error: --repo-slug requires a value"
        exit 2
      fi
      REPO_SLUG="$2"
      shift 2
      ;;
    --branch)
      if [[ $# -lt 2 ]]; then
        echo "error: --branch requires a value"
        exit 2
      fi
      BRANCH="$2"
      shift 2
      ;;
    --min-collaborators)
      if [[ $# -lt 2 ]]; then
        echo "error: --min-collaborators requires a value"
        exit 2
      fi
      MIN_COLLABORATORS="$2"
      shift 2
      ;;
    --required-status-check)
      if [[ $# -lt 2 ]]; then
        echo "error: --required-status-check requires a value"
        exit 2
      fi
      REQUIRED_STATUS_CHECKS+=("$2")
      shift 2
      ;;
    --required-status-check-strict)
      if [[ $# -lt 2 ]]; then
        echo "error: --required-status-check-strict requires a value"
        exit 2
      fi
      REQUIRED_STATUS_CHECK_STRICT_MODE="$2"
      shift 2
      ;;
    --source-workflow-run-id)
      if [[ $# -lt 2 ]]; then
        echo "error: --source-workflow-run-id requires a value"
        exit 2
      fi
      SOURCE_WORKFLOW_RUN_ID="$2"
      shift 2
      ;;
    --source-workflow-run-conclusion)
      if [[ $# -lt 2 ]]; then
        echo "error: --source-workflow-run-conclusion requires a value"
        exit 2
      fi
      SOURCE_WORKFLOW_RUN_CONCLUSION="$2"
      shift 2
      ;;
    --source-workflow-name)
      if [[ $# -lt 2 ]]; then
        echo "error: --source-workflow-name requires a value"
        exit 2
      fi
      SOURCE_WORKFLOW_NAME="$2"
      shift 2
      ;;
    --source-workflow-event)
      if [[ $# -lt 2 ]]; then
        echo "error: --source-workflow-event requires a value"
        exit 2
      fi
      SOURCE_WORKFLOW_EVENT="$2"
      shift 2
      ;;
    --source-workflow-run-url)
      if [[ $# -lt 2 ]]; then
        echo "error: --source-workflow-run-url requires a value"
        exit 2
      fi
      SOURCE_WORKFLOW_RUN_URL="$2"
      shift 2
      ;;
    --apply)
      APPLY_MODE="true"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1"
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$REPO_SLUG" ]]; then
  REMOTE_URL="$(git remote get-url origin 2>/dev/null || true)"
  if [[ -z "$REMOTE_URL" ]]; then
    echo "error: could not infer repo slug from origin; pass --repo-slug <owner/repo>"
    exit 2
  fi
  REPO_SLUG="$(
    python3 - <<'PY' "$REMOTE_URL"
import re
import sys

url = sys.argv[1].strip()
match = re.search(r"github\.com[:/](?P<slug>[^/]+/[^/.]+)(?:\.git)?$", url)
print(match.group("slug") if match else "")
PY
  )"
  if [[ -z "$REPO_SLUG" ]]; then
    echo "error: failed to parse GitHub repo slug from origin URL: $REMOTE_URL"
    exit 2
  fi
fi

COLLABORATORS_JSON="$(gh api "repos/${REPO_SLUG}/collaborators?per_page=100")"
PROTECTION_JSON="$(gh api "repos/${REPO_SLUG}/branches/${BRANCH}/protection")"
REQUIRED_STATUS_CHECKS_JSON="$(
  python3 - <<'PY' "${REQUIRED_STATUS_CHECKS[@]}"
import json
import sys

contexts = sorted(
    {
        str(item or "").strip()
        for item in sys.argv[1:]
        if str(item or "").strip()
    }
)
print(json.dumps(contexts))
PY
)"

SUMMARY_JSON="$(
  python3 - <<'PY' "$COLLABORATORS_JSON" "$PROTECTION_JSON" "$REPO_SLUG" "$BRANCH" "$MIN_COLLABORATORS" "$REQUIRED_STATUS_CHECKS_JSON" "$REQUIRED_STATUS_CHECK_STRICT_MODE" "$SOURCE_WORKFLOW_RUN_ID" "$SOURCE_WORKFLOW_RUN_CONCLUSION" "$SOURCE_WORKFLOW_NAME" "$SOURCE_WORKFLOW_EVENT" "$SOURCE_WORKFLOW_RUN_URL"
import json
import sys

collaborators = json.loads(sys.argv[1] or "[]")
protection = json.loads(sys.argv[2] or "{}")
repo_slug = str(sys.argv[3] or "")
branch = str(sys.argv[4] or "main")
min_collaborators = max(1, int(sys.argv[5] or 2))
required_status_checks = json.loads(sys.argv[6] or "[]")
required_status_check_strict_mode = str(sys.argv[7] or "preserve").strip().lower() or "preserve"
source_workflow_run_id = str(sys.argv[8] or "").strip()
source_workflow_run_conclusion = str(sys.argv[9] or "").strip()
source_workflow_name = str(sys.argv[10] or "").strip()
source_workflow_event = str(sys.argv[11] or "").strip()
source_workflow_run_url = str(sys.argv[12] or "").strip()
if not isinstance(required_status_checks, list):
    required_status_checks = []

reviews = dict(protection.get("required_pull_request_reviews") or {})
current_required_approving_review_count = int(reviews.get("required_approving_review_count") or 1)
dismiss_stale_reviews = bool(reviews.get("dismiss_stale_reviews"))
current_require_last_push_approval = bool(reviews.get("require_last_push_approval"))
current_require_code_owner_reviews = bool(reviews.get("require_code_owner_reviews"))
collaborator_logins = sorted(
    {
        str((item or {}).get("login") or "").strip()
        for item in list(collaborators or [])
        if str((item or {}).get("login") or "").strip()
    }
)
collaborator_count = len(collaborator_logins)
desired_require_code_owner_reviews = collaborator_count >= min_collaborators
desired_required_approving_review_count = (
    max(1, current_required_approving_review_count)
    if desired_require_code_owner_reviews
    else 0
)
desired_require_last_push_approval = (
    current_require_last_push_approval
    if desired_require_code_owner_reviews
    else False
)
review_change_needed = (
    current_require_code_owner_reviews != desired_require_code_owner_reviews
    or current_required_approving_review_count != desired_required_approving_review_count
    or current_require_last_push_approval != desired_require_last_push_approval
)
required_status_check_contexts = sorted(
    {
        str(item or "").strip()
        for item in list(required_status_checks or [])
        if str(item or "").strip()
    }
)
status_checks = dict(protection.get("required_status_checks") or {})
current_status_check_contexts = sorted(
    {
        str(item or "").strip()
        for item in list(status_checks.get("contexts") or [])
        if str(item or "").strip()
    }
)
current_status_check_strict = bool(status_checks.get("strict"))
desired_status_check_contexts = sorted(
    set(current_status_check_contexts) | set(required_status_check_contexts)
)
if required_status_check_strict_mode in {"true", "1", "yes", "y", "on"}:
    desired_status_check_strict = True
elif required_status_check_strict_mode in {"false", "0", "no", "n", "off"}:
    desired_status_check_strict = False
elif required_status_check_strict_mode in {"preserve", "current", "inherit"}:
    desired_status_check_strict = bool(current_status_check_strict)
else:
    raise SystemExit(
        "invalid_required_status_check_strict_mode:"
        + str(required_status_check_strict_mode)
    )
status_checks_change_needed = (
    current_status_check_contexts != desired_status_check_contexts
    or bool(current_status_check_strict) != bool(desired_status_check_strict)
)
change_needed = bool(review_change_needed or status_checks_change_needed)

summary = {
    "repo_slug": repo_slug,
    "branch": branch,
    "min_collaborators": int(min_collaborators),
    "collaborator_count": int(collaborator_count),
    "collaborators": collaborator_logins,
    "required_pull_request_reviews": {
        "current_required_approving_review_count": int(current_required_approving_review_count),
        "desired_required_approving_review_count": int(desired_required_approving_review_count),
        "dismiss_stale_reviews": bool(dismiss_stale_reviews),
        "current_require_last_push_approval": bool(current_require_last_push_approval),
        "desired_require_last_push_approval": bool(desired_require_last_push_approval),
        "current_require_code_owner_reviews": bool(current_require_code_owner_reviews),
        "desired_require_code_owner_reviews": bool(desired_require_code_owner_reviews),
        "change_needed": bool(review_change_needed),
    },
    "required_status_checks": {
        "required_contexts": required_status_check_contexts,
        "strict_mode": required_status_check_strict_mode,
        "current_contexts": current_status_check_contexts,
        "desired_contexts": desired_status_check_contexts,
        "current_strict": bool(current_status_check_strict),
        "desired_strict": bool(desired_status_check_strict),
        "change_needed": bool(status_checks_change_needed),
    },
    "reconcile_provenance": {
        "source_workflow_run_id": source_workflow_run_id or None,
        "source_workflow_run_conclusion": source_workflow_run_conclusion or None,
        "source_workflow_name": source_workflow_name or None,
        "source_workflow_event": source_workflow_event or None,
        "source_workflow_run_url": source_workflow_run_url or None,
    },
    "change_needed": bool(change_needed),
}
print(json.dumps(summary))
PY
)"

REVIEW_CHANGE_NEEDED="$(
  python3 - <<'PY' "$SUMMARY_JSON"
import json
import sys
obj = json.loads(sys.argv[1] or "{}")
print("true" if bool((obj.get("required_pull_request_reviews") or {}).get("change_needed")) else "false")
PY
)"

STATUS_CHECKS_CHANGE_NEEDED="$(
  python3 - <<'PY' "$SUMMARY_JSON"
import json
import sys
obj = json.loads(sys.argv[1] or "{}")
print("true" if bool((obj.get("required_status_checks") or {}).get("change_needed")) else "false")
PY
)"

CHANGE_NEEDED="$(
  python3 - <<'PY' "$SUMMARY_JSON"
import json
import sys
obj = json.loads(sys.argv[1] or "{}")
print("true" if bool(obj.get("change_needed")) else "false")
PY
)"

if [[ "$APPLY_MODE" == "true" && "$CHANGE_NEEDED" == "true" ]]; then
  if [[ "$REVIEW_CHANGE_NEEDED" == "true" ]]; then
    PATCH_JSON="$(
      python3 - <<'PY' "$SUMMARY_JSON"
import json
import sys
obj = json.loads(sys.argv[1] or "{}")
reviews = dict(obj.get("required_pull_request_reviews") or {})
payload = {
    "dismiss_stale_reviews": bool(reviews.get("dismiss_stale_reviews")),
    "require_code_owner_reviews": bool(reviews.get("desired_require_code_owner_reviews")),
    "required_approving_review_count": int(reviews.get("desired_required_approving_review_count") or 0),
    "require_last_push_approval": bool(reviews.get("desired_require_last_push_approval")),
}
print(json.dumps(payload))
PY
    )"
    TMP_PATCH_FILE="$(mktemp)"
    printf '%s' "$PATCH_JSON" > "$TMP_PATCH_FILE"
    gh api -X PATCH "repos/${REPO_SLUG}/branches/${BRANCH}/protection/required_pull_request_reviews" \
      --input "$TMP_PATCH_FILE" \
      >/dev/null
    rm -f "$TMP_PATCH_FILE"
  fi

  if [[ "$STATUS_CHECKS_CHANGE_NEEDED" == "true" ]]; then
    PATCH_JSON="$(
      python3 - <<'PY' "$SUMMARY_JSON"
import json
import sys
obj = json.loads(sys.argv[1] or "{}")
status_checks = dict(obj.get("required_status_checks") or {})
payload = {
    "strict": bool(status_checks.get("desired_strict")),
    "contexts": list(status_checks.get("desired_contexts") or []),
}
print(json.dumps(payload))
PY
    )"
    TMP_PATCH_FILE="$(mktemp)"
    printf '%s' "$PATCH_JSON" > "$TMP_PATCH_FILE"
    gh api -X PATCH "repos/${REPO_SLUG}/branches/${BRANCH}/protection/required_status_checks" \
      --input "$TMP_PATCH_FILE" \
      >/dev/null
    rm -f "$TMP_PATCH_FILE"
  fi

  SUMMARY_JSON="$(
    python3 - <<'PY' "$SUMMARY_JSON"
import json
import sys
obj = json.loads(sys.argv[1] or "{}")
reviews = dict(obj.get("required_pull_request_reviews") or {})
reviews["current_require_code_owner_reviews"] = bool(reviews.get("desired_require_code_owner_reviews"))
reviews["current_required_approving_review_count"] = int(reviews.get("desired_required_approving_review_count") or 0)
reviews["current_require_last_push_approval"] = bool(reviews.get("desired_require_last_push_approval"))
reviews["change_needed"] = False
obj["required_pull_request_reviews"] = reviews
status_checks = dict(obj.get("required_status_checks") or {})
status_checks["current_contexts"] = list(status_checks.get("desired_contexts") or [])
status_checks["current_strict"] = bool(status_checks.get("desired_strict"))
status_checks["change_needed"] = False
obj["required_status_checks"] = status_checks
obj["change_needed"] = False
obj["applied"] = True
print(json.dumps(obj))
PY
  )"
else
  SUMMARY_JSON="$(
    python3 - <<'PY' "$SUMMARY_JSON" "$APPLY_MODE"
import json
import sys
obj = json.loads(sys.argv[1] or "{}")
obj["applied"] = bool(sys.argv[2].strip().lower() == "true")
print(json.dumps(obj))
PY
  )"
fi

python3 - <<'PY' "$SUMMARY_JSON"
import json
import sys
obj = json.loads(sys.argv[1] or "{}")
print(json.dumps(obj, indent=2))
PY
