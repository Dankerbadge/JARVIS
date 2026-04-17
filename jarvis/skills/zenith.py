from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..correlation import RootCauseReport
from ..models import EventEnvelope, PlanArtifact, PlanStep, new_id, utc_now_iso
from ..state_index import latest_ci_failure_key, latest_repo_delta_key


@dataclass
class DiffProposal:
    relative_path: str
    patch: str
    changed: bool
    summary: str


class RepoDiffEngine:
    def __init__(self, repo_path: str | Path) -> None:
        self.repo_path = Path(repo_path).resolve()
        if not self.repo_path.exists() or not self.repo_path.is_dir():
            raise FileNotFoundError(f"Repo path does not exist: {self.repo_path}")

    def _resolve(self, relative_path: str) -> Path:
        target = (self.repo_path / relative_path).resolve()
        if self.repo_path not in target.parents and target != self.repo_path:
            raise ValueError(f"Path escapes repo root: {relative_path}")
        return target

    def generate_patch(self, relative_path: str, before: str, after: str) -> str:
        before_lines = before.splitlines()
        after_lines = after.splitlines()
        diff = difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
            n=3,
            lineterm="",
        )
        text = "\n".join(diff)
        return f"{text}\n" if text else ""

    def preview_file_replacement(
        self, relative_path: str, search: str, replacement: str
    ) -> DiffProposal:
        file_path = self._resolve(relative_path)
        if not file_path.exists():
            return DiffProposal(
                relative_path=relative_path,
                patch="",
                changed=False,
                summary=f"{relative_path} not found under repo root.",
            )

        before = file_path.read_text(encoding="utf-8")
        if search not in before:
            return DiffProposal(
                relative_path=relative_path,
                patch="",
                changed=False,
                summary=f"No matching text for replacement in {relative_path}.",
            )

        after = before.replace(search, replacement)
        patch = self.generate_patch(relative_path, before, after)
        return DiffProposal(
            relative_path=relative_path,
            patch=patch,
            changed=True,
            summary=f"Prepared patch preview for {relative_path}.",
        )

    def apply_file_replacement(
        self, relative_path: str, search: str, replacement: str
    ) -> DiffProposal:
        proposal = self.preview_file_replacement(relative_path, search, replacement)
        if not proposal.changed:
            return proposal

        file_path = self._resolve(relative_path)
        before = file_path.read_text(encoding="utf-8")
        after = before.replace(search, replacement)
        file_path.write_text(after, encoding="utf-8")
        return proposal

    def search_and_preview(
        self,
        *,
        glob_pattern: str,
        search: str,
        replacement: str,
        max_files: int = 20,
    ) -> list[DiffProposal]:
        proposals: list[DiffProposal] = []
        for path in self.repo_path.rglob(glob_pattern):
            if len(proposals) >= max_files:
                break
            if not path.is_file():
                continue
            if ".git" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if search not in text:
                continue
            rel = str(path.relative_to(self.repo_path))
            patch = self.generate_patch(rel, text, text.replace(search, replacement))
            proposals.append(
                DiffProposal(
                    relative_path=rel,
                    patch=patch,
                    changed=True,
                    summary=f"Patch preview for {rel}.",
                )
            )
        return proposals


class ZenithSkill:
    def __init__(self, repo_path: str | Path) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.diff_engine = RepoDiffEngine(self.repo_path)

    def extract_candidates(self, event: EventEnvelope) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        payload = event.payload
        project = str(payload.get("project", "default"))
        repo_id = str(payload.get("repo_id") or payload.get("repo_path") or "unknown")
        branch = str(payload.get("branch") or "unknown")

        if event.source_type == "ci" and str(payload.get("status", "")).lower() == "failed":
            candidates.append(
                {
                    "kind": "entity",
                    "id": new_id("ent"),
                    "entity_key": f"risk:{project}:ci_failed",
                    "entity_type": "Risk",
                    "value": {
                        "project": project,
                        "severity": "high",
                        "reason": "ci_failed",
                        "source": event.source,
                    },
                    "confidence": 0.92,
                    "source_refs": [event.event_id],
                    "last_verified_at": utc_now_iso(),
                }
            )

        deadline_hours = payload.get("deadline_hours")
        if isinstance(deadline_hours, (int, float)) and deadline_hours < 48:
            candidates.append(
                {
                    "kind": "entity",
                    "id": new_id("ent"),
                    "entity_key": f"risk:{project}:deadline_pressure",
                    "entity_type": "Risk",
                    "value": {
                        "project": project,
                        "severity": "high",
                        "reason": "deadline_lt_48h",
                        "deadline_hours": deadline_hours,
                    },
                    "confidence": 0.87,
                    "source_refs": [event.event_id],
                    "last_verified_at": utc_now_iso(),
                }
            )

        if event.source_type == "repo_change":
            changed_count = int(payload.get("changed_count", 0))
            if changed_count > 0 and bool(payload.get("protected_ui_changed")):
                candidates.append(
                    {
                        "kind": "entity",
                        "id": new_id("ent"),
                        "entity_key": f"risk:{project}:protected_ui_change",
                        "entity_type": "Risk",
                        "value": {
                            "project": project,
                            "severity": "high",
                            "reason": "protected_ui_changed",
                            "changed_count": changed_count,
                        },
                        "confidence": 0.8,
                        "source_refs": [event.event_id],
                        "last_verified_at": utc_now_iso(),
                    }
                )

        if event.source_type == "repo.git_delta":
            candidates.append(
                {
                    "kind": "entity",
                    "id": new_id("ent"),
                    "entity_key": latest_repo_delta_key(repo_id, branch),
                    "entity_type": "Artifact",
                    "value": {
                        "project": project,
                        "repo_id": repo_id,
                        "branch": branch,
                        "base_branch": payload.get("base_branch"),
                        "head_sha": payload.get("head_sha"),
                        "merge_base": payload.get("merge_base"),
                        "commit_range": payload.get("commit_range"),
                        "commits": payload.get("commits", []),
                        "changed_files": payload.get("changed_files", []),
                        "dirty_files": payload.get("dirty_files", []),
                        "pr_candidate": bool(payload.get("pr_candidate", False)),
                        "protected_ui_changed": bool(payload.get("protected_ui_changed", False)),
                        "updated_from": event.event_id,
                    },
                    "confidence": 0.95,
                    "source_refs": [event.event_id],
                    "last_verified_at": utc_now_iso(),
                }
            )
            if bool(payload.get("protected_ui_changed")):
                changed_count = len(payload.get("changed_files", [])) + len(
                    payload.get("dirty_files", [])
                )
                candidates.append(
                    {
                        "kind": "entity",
                        "id": new_id("ent"),
                        "entity_key": f"risk:{project}:git_delta_ui_change",
                        "entity_type": "Risk",
                        "value": {
                            "project": project,
                            "severity": "high",
                            "reason": "git_delta_protected_ui_changed",
                            "changed_count": changed_count,
                            "repo_id": repo_id,
                            "branch": branch,
                        },
                        "confidence": 0.83,
                        "source_refs": [event.event_id],
                        "last_verified_at": utc_now_iso(),
                    }
                )

        if event.source_type == "ci.failure":
            implicated_paths = payload.get("implicated_paths", [])
            candidates.append(
                {
                    "kind": "entity",
                    "id": new_id("ent"),
                    "entity_key": latest_ci_failure_key(repo_id, branch),
                    "entity_type": "Artifact",
                    "value": {
                        "project": project,
                        "repo_id": repo_id,
                        "branch": branch,
                        "head_sha": payload.get("head_sha"),
                        "status": payload.get("status"),
                        "job_name": payload.get("job_name"),
                        "error_summary": payload.get("error_summary"),
                        "implicated_paths": implicated_paths,
                        "zenith_owned": bool(payload.get("zenith_owned", False)),
                        "protected_ui_changed": bool(payload.get("protected_ui_changed", False)),
                        "report_file": payload.get("report_file"),
                        "updated_from": event.event_id,
                    },
                    "confidence": 0.95,
                    "source_refs": [event.event_id],
                    "last_verified_at": utc_now_iso(),
                }
            )
            if bool(payload.get("zenith_owned")):
                candidates.append(
                    {
                        "kind": "entity",
                        "id": new_id("ent"),
                        "entity_key": f"risk:{project}:ci_failure_zenith_paths",
                        "entity_type": "Risk",
                        "value": {
                            "project": project,
                            "severity": "high",
                            "reason": "ci_failure_zenith_paths",
                            "repo_id": repo_id,
                            "branch": branch,
                            "implicated_paths": implicated_paths,
                        },
                        "confidence": 0.91,
                        "source_refs": [event.event_id],
                        "last_verified_at": utc_now_iso(),
                    }
                )

        return candidates

    def _build_plan(
        self,
        *,
        project: str,
        intent: str,
        reasoning_summary: str,
        include_protected_ui_step: bool,
        context_payload: dict[str, Any],
    ) -> PlanArtifact:
        steps = [
            PlanStep(
                action_class="P0",
                proposed_action="zenith_collect_risk_context",
                expected_effect="Current risk context is available for prioritization.",
                rollback="none",
                payload=context_payload,
            ),
            PlanStep(
                action_class="P1",
                proposed_action="zenith_generate_patch_preview",
                expected_effect="Repo-aware patch preview generated for review.",
                rollback="discard_preview",
                payload={
                    "glob_pattern": "*.py",
                    "search": "TODO_ZENITH",
                    "replacement": "DONE_ZENITH",
                },
            ),
        ]
        approval_requirements: list[str] = []
        if include_protected_ui_step:
            steps.append(
                PlanStep(
                    action_class="P2",
                    proposed_action="zenith_apply_protected_ui_patch",
                    expected_effect="Approved protected UI change applied with audit trail.",
                    rollback="git_revert_or_file_restore",
                    payload={
                        "relative_path": "ui/zenith_ui.txt",
                        "search": "TODO_UI",
                        "replacement": "READY_UI",
                    },
                    requires_approval=True,
                )
            )
            approval_requirements.append("P2 approval for protected UI patch")

        return PlanArtifact(
            intent=intent,
            priority="high",
            reasoning_summary=reasoning_summary,
            steps=steps,
            approval_requirements=approval_requirements,
            expires_at=utc_now_iso(),
        )

    def propose_git_delta_plan(self, payload: dict[str, Any]) -> PlanArtifact | None:
        changed_files = payload.get("changed_files", [])
        dirty_files = payload.get("dirty_files", [])
        if not changed_files and not dirty_files:
            return None
        project = str(payload.get("project", "zenith"))
        branch = str(payload.get("branch", "unknown"))
        include_protected_ui_step = bool(payload.get("protected_ui_changed", False))
        return self._build_plan(
            project=project,
            intent="review_git_delta_for_regressions",
            reasoning_summary=(
                f"Branch-aware git delta detected on {branch}; generated a bounded Zenith review plan."
            ),
            include_protected_ui_step=include_protected_ui_step,
            context_payload={
                "project": project,
                "repo_id": payload.get("repo_id") or payload.get("repo_path"),
                "branch": branch,
                "base_branch": payload.get("base_branch"),
                "head_sha": payload.get("head_sha"),
                "commit_range": payload.get("commit_range"),
                "pr_candidate": bool(payload.get("pr_candidate", False)),
            },
        )

    def propose_ci_failure_plan(
        self,
        payload: dict[str, Any],
        latest_git_delta: dict[str, Any] | None = None,
    ) -> PlanArtifact | None:
        if str(payload.get("status", "")).lower() not in {"failed", "failure", "error", "errored"}:
            return None
        if not bool(payload.get("zenith_owned", False)):
            return None
        project = str(payload.get("project", "zenith"))
        branch = str(payload.get("branch", "unknown"))
        include_protected_ui_step = bool(payload.get("protected_ui_changed", False))
        if latest_git_delta:
            include_protected_ui_step = include_protected_ui_step or bool(
                latest_git_delta.get("protected_ui_changed", False)
            )
        return self._build_plan(
            project=project,
            intent="triage_ci_failure_with_repo_context",
            reasoning_summary=(
                f"CI failure on {branch} implicates Zenith-owned paths; generated higher-confidence triage plan."
            ),
            include_protected_ui_step=include_protected_ui_step,
            context_payload={
                "project": project,
                "repo_id": payload.get("repo_id") or payload.get("repo_path"),
                "branch": branch,
                "head_sha": payload.get("head_sha"),
                "job_name": payload.get("job_name"),
                "error_summary": payload.get("error_summary"),
                "implicated_paths": payload.get("implicated_paths", []),
                "failed_paths": payload.get("failed_paths", payload.get("implicated_paths", [])),
                "failed_tests": payload.get("failed_tests", []),
                "latest_git_delta": latest_git_delta or {},
            },
        )

    def propose_root_cause_plan(
        self,
        *,
        ci_failure_payload: dict[str, Any],
        correlation_report: RootCauseReport,
        latest_git_delta: dict[str, Any] | None,
        source_signal_kind: str,
    ) -> PlanArtifact | None:
        if not correlation_report.candidates:
            return None
        project = str(ci_failure_payload.get("project", "zenith"))
        repo_id = str(
            ci_failure_payload.get("repo_id")
            or ci_failure_payload.get("repo_path")
            or (latest_git_delta or {}).get("repo_id")
            or "unknown"
        )
        branch = str(ci_failure_payload.get("branch") or (latest_git_delta or {}).get("branch") or "unknown")
        ranked_paths = [candidate.path for candidate in correlation_report.candidates]
        include_protected_ui_step = any(candidate.protected for candidate in correlation_report.candidates[:3])
        if bool(ci_failure_payload.get("protected_ui_changed")):
            include_protected_ui_step = True

        if source_signal_kind == "repo.git_delta":
            intent = "refresh_root_cause_after_git_delta"
            reasoning_summary = (
                f"Refreshed correlated root-cause ranking on {branch} after new repo delta."
            )
        else:
            intent = "triage_ci_failure_with_root_cause_correlation"
            reasoning_summary = (
                f"Correlated CI failure on {branch}; generated ranked root-cause plan with confidence "
                f"{correlation_report.confidence:.2f}."
            )

        return self._build_plan(
            project=project,
            intent=intent,
            reasoning_summary=reasoning_summary,
            include_protected_ui_step=include_protected_ui_step,
            context_payload={
                "project": project,
                "repo_id": repo_id,
                "branch": branch,
                "head_sha": ci_failure_payload.get("head_sha"),
                "failure_family": correlation_report.failure_family,
                "correlation_confidence": correlation_report.confidence,
                "ranked_paths": ranked_paths,
                "root_cause_report": correlation_report.as_dict(),
                "failed_paths": ci_failure_payload.get("failed_paths", ci_failure_payload.get("implicated_paths", [])),
                "failed_tests": ci_failure_payload.get("failed_tests", []),
                "latest_git_delta": latest_git_delta or {},
                "source_signals": list(correlation_report.signals),
            },
        )

    def propose_plan(self, active_risks: list[dict[str, Any]]) -> PlanArtifact | None:
        if not active_risks:
            return None
        primary_risk = active_risks[0]["value"]
        project = str(primary_risk.get("project", "default"))
        return self._build_plan(
            project=project,
            intent="stabilize_release_branch",
            reasoning_summary=f"High project risk detected for {project}.",
            include_protected_ui_step=True,
            context_payload={"project": project},
        )

    def tool_collect_risk_context(self, payload: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        return {
            "project": payload.get("project"),
            "repo_path": str(self.repo_path),
            "dry_run": dry_run,
        }

    def tool_generate_patch_preview(self, payload: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        proposals = self.diff_engine.search_and_preview(
            glob_pattern=str(payload.get("glob_pattern", "*.py")),
            search=str(payload.get("search", "TODO_ZENITH")),
            replacement=str(payload.get("replacement", "DONE_ZENITH")),
            max_files=10,
        )
        serialized = [
            {
                "file": proposal.relative_path,
                "summary": proposal.summary,
                "patch": proposal.patch,
            }
            for proposal in proposals
        ]
        return {"count": len(serialized), "proposals": serialized, "dry_run": dry_run}

    def tool_apply_protected_ui_patch(self, payload: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        relative_path = str(payload.get("relative_path", "ui/zenith_ui.txt"))
        search = str(payload.get("search", "TODO_UI"))
        replacement = str(payload.get("replacement", "READY_UI"))
        proposal = self.diff_engine.preview_file_replacement(relative_path, search, replacement)
        result = {
            "file": relative_path,
            "changed": proposal.changed,
            "patch": proposal.patch,
            "summary": proposal.summary,
            "dry_run": dry_run,
        }
        if dry_run or not proposal.changed:
            return result

        applied = self.diff_engine.apply_file_replacement(relative_path, search, replacement)
        result["summary"] = applied.summary
        return result

    def register_tools(self) -> dict[str, Any]:
        return {
            "zenith_collect_risk_context": self.tool_collect_risk_context,
            "zenith_generate_patch_preview": self.tool_generate_patch_preview,
            "zenith_apply_protected_ui_patch": self.tool_apply_protected_ui_patch,
        }
