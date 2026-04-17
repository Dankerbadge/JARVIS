from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
import re
from typing import Any, Iterable, Mapping, Sequence

from .outcomes import PathFeedback, build_path_feedback


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _normalize_text(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9/_:\-.]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "unknown-failure-family"


@dataclass(frozen=True)
class RootCauseCandidate:
    path: str
    score: float
    reasons: tuple[str, ...]
    protected: bool = False
    signals: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RootCauseReport:
    repo_id: str
    branch: str
    failure_family: str
    candidates: tuple[RootCauseCandidate, ...]
    confidence: float
    signals: tuple[str, ...]
    evidence: dict[str, Any]
    created_at: str = field(default_factory=utc_now_iso)

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "branch": self.branch,
            "failure_family": self.failure_family,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "confidence": self.confidence,
            "signals": list(self.signals),
            "evidence": self.evidence,
            "created_at": self.created_at,
        }


def infer_failure_family(ci_failure: Mapping[str, Any]) -> str:
    explicit = ci_failure.get("failure_family")
    if explicit:
        return _normalize_text(str(explicit))

    summary = ci_failure.get("summary") or ci_failure.get("error_summary")
    if summary:
        return _normalize_text(str(summary))

    failed_tests = ci_failure.get("failed_tests") or []
    if failed_tests:
        return _normalize_text(str(failed_tests[0]))

    failed_paths = ci_failure.get("failed_paths") or ci_failure.get("implicated_paths") or []
    if failed_paths:
        return _normalize_text(str(failed_paths[0]))

    return "unknown-failure-family"


def _basename_tokens(path: str) -> set[str]:
    p = PurePosixPath(path)
    name = p.name.lower()
    stem = p.stem.lower()
    tokens = {name, stem, path.lower()}
    tokens.update(part.lower() for part in p.parts if part)
    return tokens


def _text_mentions_path(text: str, path: str) -> bool:
    lowered = text.lower()
    tokens = _basename_tokens(path)
    return any(token and token in lowered for token in tokens)


def _shared_parent(path: str, other_paths: Sequence[str]) -> bool:
    parent = str(PurePosixPath(path).parent)
    if not parent or parent == ".":
        return False
    for other in other_paths:
        other_parent = str(PurePosixPath(other).parent)
        if other_parent == parent and other != path:
            return True
    return False


class RootCauseScorer:
    def __init__(
        self,
        *,
        protected_paths: tuple[str, ...] = ("ui/",),
        owned_paths: tuple[str, ...] = ("service.py", "ui/", "api/", "zenith/", "jarvis/"),
        max_candidates: int = 10,
    ) -> None:
        self.protected_paths = protected_paths
        self.owned_paths = owned_paths
        self.max_candidates = max_candidates

    def _is_protected(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self.protected_paths)

    def _is_owned(self, path: str) -> bool:
        return any(path == entry or path.startswith(entry) for entry in self.owned_paths)

    def _candidate_paths(
        self,
        *,
        changed_files: Sequence[str],
        dirty_files: Sequence[str],
        failed_paths: Sequence[str],
        path_feedback: Mapping[str, PathFeedback],
    ) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for bucket in (failed_paths, changed_files, dirty_files, tuple(path_feedback.keys())):
            for path in bucket:
                if path and path not in seen:
                    ordered.append(path)
                    seen.add(path)
        return ordered

    def _candidate_score(
        self,
        path: str,
        *,
        changed_files: set[str],
        dirty_files: set[str],
        failed_paths: set[str],
        test_hints: str,
        summary_text: str,
        path_feedback: Mapping[str, PathFeedback],
        all_failed_paths: Sequence[str],
    ) -> RootCauseCandidate:
        score = 0.05
        reasons: list[str] = []
        signals: list[str] = []

        if path in failed_paths:
            score += 0.34
            reasons.append("path appears directly in failed paths")
            signals.append("ci.failed_path")

        if path in changed_files:
            score += 0.22
            reasons.append("path is present in latest repo delta")
            signals.append("git.changed")

        if path in dirty_files:
            score += 0.10
            reasons.append("path is currently dirty in working tree")
            signals.append("git.dirty")

        if _text_mentions_path(test_hints, path):
            score += 0.12
            reasons.append("failed tests mention this path or module")
            signals.append("ci.test_hint")

        if _text_mentions_path(summary_text, path):
            score += 0.11
            reasons.append("failure summary/stacktrace mentions this path")
            signals.append("ci.summary_hint")

        if _shared_parent(path, all_failed_paths):
            score += 0.06
            reasons.append("path shares a directory with another failed path")
            signals.append("ci.shared_parent")

        if self._is_owned(path):
            score += 0.04
            reasons.append("path belongs to Zenith-owned scope")
            signals.append("zenith.owned_scope")

        feedback = path_feedback.get(path)
        if feedback:
            positive = min(0.25, (feedback.success_count * 0.08) + (feedback.partial_count * 0.04))
            negative = min(
                0.28,
                (feedback.failure_count * 0.07) + (feedback.regression_count * 0.12),
            )
            if positive > 0:
                score += positive
                reasons.append(
                    f"historical outcomes help this path ({feedback.success_count} success, {feedback.partial_count} partial)"
                )
                signals.append("history.positive")
            if negative > 0:
                score -= negative
                reasons.append(
                    f"historical outcomes penalize this path ({feedback.failure_count} failure, {feedback.regression_count} regression)"
                )
                signals.append("history.negative")

        protected = self._is_protected(path)
        if protected:
            reasons.append("path is protected and remains approval-gated")
            signals.append("policy.protected")

        return RootCauseCandidate(
            path=path,
            score=round(clamp(score), 4),
            reasons=tuple(reasons),
            protected=protected,
            signals=tuple(signals),
        )

    def rank(
        self,
        *,
        repo_id: str,
        branch: str,
        repo_delta: Mapping[str, Any] | None,
        ci_failure: Mapping[str, Any],
        recent_outcomes: Iterable[Mapping[str, Any] | Any] = (),
    ) -> RootCauseReport:
        failure_family = infer_failure_family(ci_failure)

        changed_files = tuple(str(path) for path in (repo_delta or {}).get("changed_files", []))
        dirty_files = tuple(str(path) for path in (repo_delta or {}).get("dirty_files", []))
        failed_paths_raw = ci_failure.get("failed_paths") or ci_failure.get("implicated_paths") or []
        failed_paths = tuple(str(path) for path in failed_paths_raw)
        failed_tests = tuple(str(item) for item in ci_failure.get("failed_tests", []))
        summary_parts = [
            str(ci_failure.get("summary", "")),
            str(ci_failure.get("stacktrace", "")),
            str(ci_failure.get("error_excerpt", "")),
            str(ci_failure.get("error_summary", "")),
        ]
        summary_text = "\n".join(part for part in summary_parts if part)
        test_hint_text = "\n".join(failed_tests)

        feedback = build_path_feedback(recent_outcomes, failure_family=failure_family, branch=branch)
        candidate_paths = self._candidate_paths(
            changed_files=changed_files,
            dirty_files=dirty_files,
            failed_paths=failed_paths,
            path_feedback=feedback,
        )

        candidates = [
            self._candidate_score(
                path,
                changed_files=set(changed_files),
                dirty_files=set(dirty_files),
                failed_paths=set(failed_paths),
                test_hints=test_hint_text,
                summary_text=summary_text,
                path_feedback=feedback,
                all_failed_paths=failed_paths,
            )
            for path in candidate_paths
        ]
        candidates.sort(key=lambda item: (-item.score, item.path))
        ranked = tuple(candidates[: self.max_candidates])

        top_score = ranked[0].score if ranked else 0.0
        runner_up = ranked[1].score if len(ranked) > 1 else 0.0
        separation = max(0.0, top_score - runner_up)
        confidence = clamp(0.42 + (top_score * 0.42) + (separation * 0.35), 0.0, 0.99)

        signal_ids: list[str] = []
        if ci_failure.get("report_id"):
            signal_ids.append(f"ci:{ci_failure['report_id']}")
        if repo_delta and repo_delta.get("head_sha"):
            signal_ids.append(f"git:{repo_delta['head_sha']}")

        evidence = {
            "changed_files": list(changed_files),
            "dirty_files": list(dirty_files),
            "failed_paths": list(failed_paths),
            "failed_tests": list(failed_tests),
            "summary": ci_failure.get("summary") or ci_failure.get("error_summary"),
            "head_sha": ci_failure.get("head_sha") or (repo_delta or {}).get("head_sha"),
            "path_feedback": {
                path: {
                    "success_count": item.success_count,
                    "partial_count": item.partial_count,
                    "failure_count": item.failure_count,
                    "regression_count": item.regression_count,
                    "last_touched_at": item.last_touched_at,
                    "net_signal": item.net_signal,
                }
                for path, item in feedback.items()
            },
        }

        return RootCauseReport(
            repo_id=repo_id,
            branch=branch,
            failure_family=failure_family,
            candidates=ranked,
            confidence=round(confidence, 4),
            signals=tuple(signal_ids),
            evidence=evidence,
        )

