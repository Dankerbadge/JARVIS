#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
usage: reconcile_codeowner_review_gate.sh [--repo-slug <owner/repo>] [--branch <name>] [--min-collaborators <int>] [--apply]

Reconciles GitHub branch-protection review requirements against collaborator count:
- enable when collaborator_count >= min_collaborators
- disable otherwise (single-maintainer deadlock safety)

Defaults:
- repo slug: inferred from `git remote get-url origin`
- branch: main
- min collaborators: 2
- mode: dry-run (set --apply to patch branch protection)
USAGE
}

REPO_SLUG=""
BRANCH="main"
MIN_COLLABORATORS=2
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

SUMMARY_JSON="$(
  python3 - <<'PY' "$COLLABORATORS_JSON" "$PROTECTION_JSON" "$REPO_SLUG" "$BRANCH" "$MIN_COLLABORATORS"
import json
import sys

collaborators = json.loads(sys.argv[1] or "[]")
protection = json.loads(sys.argv[2] or "{}")
repo_slug = str(sys.argv[3] or "")
branch = str(sys.argv[4] or "main")
min_collaborators = max(1, int(sys.argv[5] or 2))

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
change_needed = (
    current_require_code_owner_reviews != desired_require_code_owner_reviews
    or current_required_approving_review_count != desired_required_approving_review_count
    or current_require_last_push_approval != desired_require_last_push_approval
)

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
        "change_needed": bool(change_needed),
    },
}
print(json.dumps(summary))
PY
)"

CHANGE_NEEDED="$(
  python3 - <<'PY' "$SUMMARY_JSON"
import json
import sys
obj = json.loads(sys.argv[1] or "{}")
print("true" if bool(((obj.get("required_pull_request_reviews") or {}).get("change_needed"))) else "false")
PY
)"

if [[ "$APPLY_MODE" == "true" && "$CHANGE_NEEDED" == "true" ]]; then
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
