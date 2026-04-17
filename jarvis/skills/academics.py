from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import EventEnvelope, PlanArtifact, PlanStep, new_id, utc_now_iso
from ..state_index import (
    latest_academic_overview_key,
    latest_academic_schedule_context_key,
    latest_academic_suppression_windows_key,
    latest_course_risk_key,
    latest_deadline_cluster_key,
    latest_study_recommendation_key,
)


def _parse_due_iso(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _hours_until(due_at: str | None, *, now: datetime | None = None) -> float | None:
    due = _parse_due_iso(due_at)
    if due is None:
        return None
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (due - current).total_seconds() / 3600.0


class AcademicsSkill:
    def __init__(self, workspace_path: str | Path) -> None:
        self.workspace_path = Path(workspace_path).resolve()

    def extract_candidates(self, event: EventEnvelope) -> list[dict[str, Any]]:
        if not event.source_type.startswith("academic."):
            return []
        payload = dict(event.payload)
        course_id = str(payload.get("course_id") or "unknown_course")
        term_id = str(payload.get("term_id") or "current_term")
        source_kind = str(payload.get("ingestion_source_kind") or "unknown")
        source_provider = str(payload.get("ingestion_provider") or "").strip() or None
        source_refs = [event.event_id]
        candidates: list[dict[str, Any]] = []

        overview_key = latest_academic_overview_key(term_id)
        summary_excerpt = str(payload.get("message_excerpt") or payload.get("title") or "")[:220]
        candidates.append(
            {
                "kind": "entity",
                "id": new_id("ent"),
                "entity_key": overview_key,
                "entity_type": "Artifact",
                "value": {
                    "term_id": term_id,
                    "last_event_type": event.source_type,
                    "last_event_payload": payload,
                    "course_id": course_id,
                    "signal_source_kind": source_kind,
                    "signal_provider": source_provider,
                    "latest_summary_excerpt": summary_excerpt,
                    "updated_from": event.event_id,
                },
                "confidence": 0.9,
                "source_refs": source_refs,
                "last_verified_at": utc_now_iso(),
            }
        )

        if event.source_type in {
            "academic.assignment_due",
            "academic.exam_scheduled",
            "academic.risk_signal",
        }:
            due_at = payload.get("due_at") or payload.get("exam_at")
            hours = _hours_until(str(due_at) if due_at else None)
            severity = "medium"
            if hours is not None and hours <= 24:
                severity = "critical"
            elif hours is not None and hours <= 72:
                severity = "high"
            if event.source_type == "academic.risk_signal":
                severity = str(payload.get("severity") or severity)
            candidates.append(
                {
                    "kind": "entity",
                    "id": new_id("ent"),
                    "entity_key": latest_course_risk_key(course_id),
                    "entity_type": "Risk",
                    "value": {
                        "project": "academics",
                        "domain": "academics",
                        "course_id": course_id,
                        "term_id": term_id,
                        "reason": event.source_type,
                        "severity": severity,
                        "due_at": due_at,
                        "hours_until_due": hours,
                        "title": payload.get("title") or payload.get("name") or "",
                        "source": event.source,
                        "signal_source_kind": source_kind,
                        "signal_provider": source_provider,
                    },
                    "confidence": 0.86,
                    "source_refs": source_refs,
                    "last_verified_at": utc_now_iso(),
                }
            )

        if event.source_type in {
            "academic.class_scheduled",
            "academic.study_window",
            "academic.suppression_window",
        }:
            window_start = payload.get("window_start_at") or payload.get("starts_at")
            window_end = payload.get("window_end_at") or payload.get("ends_at") or window_start
            window_kind = str(payload.get("window_type") or "class_session")
            schedule_key = latest_academic_schedule_context_key(term_id)
            suppression_key = latest_academic_suppression_windows_key(term_id)
            candidates.append(
                {
                    "kind": "entity",
                    "id": new_id("ent"),
                    "entity_key": schedule_key,
                    "entity_type": "Artifact",
                    "value": {
                        "term_id": term_id,
                        "course_id": course_id,
                        "last_window": {
                            "kind": window_kind,
                            "start_at": window_start,
                            "end_at": window_end,
                            "title": payload.get("title"),
                        },
                        "signal_source_kind": source_kind,
                        "signal_provider": source_provider,
                        "updated_from": event.event_id,
                    },
                    "confidence": 0.86,
                    "source_refs": source_refs,
                    "last_verified_at": utc_now_iso(),
                }
            )
            candidates.append(
                {
                    "kind": "entity",
                    "id": new_id("ent"),
                    "entity_key": suppression_key,
                    "entity_type": "Artifact",
                    "value": {
                        "term_id": term_id,
                        "windows": [
                            {
                                "kind": window_kind,
                                "start_at": window_start,
                                "end_at": window_end,
                                "course_id": course_id,
                            }
                        ],
                        "signal_source_kind": source_kind,
                        "signal_provider": source_provider,
                        "updated_from": event.event_id,
                    },
                    "confidence": 0.84,
                    "source_refs": source_refs,
                    "last_verified_at": utc_now_iso(),
                }
            )

        if event.source_type in {"academic.assignment_due", "academic.exam_scheduled"}:
            due_at = payload.get("due_at") or payload.get("exam_at")
            cluster_items = payload.get("cluster_items")
            if not isinstance(cluster_items, list):
                cluster_items = [
                    {
                        "course_id": course_id,
                        "title": payload.get("title") or payload.get("name") or "",
                        "due_at": due_at,
                        "kind": event.source_type,
                    }
                ]
            candidates.append(
                {
                    "kind": "entity",
                    "id": new_id("ent"),
                    "entity_key": latest_deadline_cluster_key(term_id),
                    "entity_type": "Artifact",
                    "value": {
                        "term_id": term_id,
                        "items": cluster_items,
                        "signal_source_kind": source_kind,
                        "signal_provider": source_provider,
                        "updated_from": event.event_id,
                    },
                    "confidence": 0.84,
                    "source_refs": source_refs,
                    "last_verified_at": utc_now_iso(),
                }
            )

        if event.source_type in {"academic.study_window", "academic.reading_assigned"}:
            candidates.append(
                {
                    "kind": "entity",
                    "id": new_id("ent"),
                    "entity_key": latest_study_recommendation_key(course_id),
                    "entity_type": "Artifact",
                    "value": {
                        "course_id": course_id,
                        "term_id": term_id,
                        "recommended_block_minutes": int(payload.get("minutes") or 60),
                        "recommended_topics": payload.get("topics") or [],
                        "reason": event.source_type,
                        "signal_source_kind": source_kind,
                        "signal_provider": source_provider,
                        "updated_from": event.event_id,
                    },
                    "confidence": 0.8,
                    "source_refs": source_refs,
                    "last_verified_at": utc_now_iso(),
                }
            )

        if event.source_type in {"academic.announcement", "academic.professor_message", "academic.syllabus_item"}:
            lowered = summary_excerpt.lower()
            if any(token in lowered for token in ("urgent", "deadline", "exam", "required", "attendance")):
                candidates.append(
                    {
                        "kind": "entity",
                        "id": new_id("ent"),
                        "entity_key": latest_course_risk_key(course_id),
                        "entity_type": "Risk",
                        "value": {
                            "project": "academics",
                            "domain": "academics",
                            "course_id": course_id,
                            "term_id": term_id,
                            "reason": "academic.material_risk_signal",
                            "severity": "medium",
                            "title": payload.get("title") or payload.get("name") or "",
                            "message_excerpt": summary_excerpt,
                            "source": event.source,
                            "signal_source_kind": source_kind,
                            "signal_provider": source_provider,
                        },
                        "confidence": 0.74,
                        "source_refs": source_refs,
                        "last_verified_at": utc_now_iso(),
                    }
                )

        if event.source_type == "academic.grade_update":
            grade = payload.get("grade")
            if isinstance(grade, (int, float)) and float(grade) < 80:
                candidates.append(
                    {
                        "kind": "entity",
                        "id": new_id("ent"),
                        "entity_key": latest_course_risk_key(course_id),
                        "entity_type": "Risk",
                        "value": {
                            "project": "academics",
                            "domain": "academics",
                            "course_id": course_id,
                            "term_id": term_id,
                            "reason": "academic.grade_update_low",
                            "severity": "high",
                            "grade": float(grade),
                            "source": event.source,
                            "signal_source_kind": source_kind,
                            "signal_provider": source_provider,
                        },
                        "confidence": 0.85,
                        "source_refs": source_refs,
                        "last_verified_at": utc_now_iso(),
                    }
                )
        return candidates

    def propose_plan(self, active_risks: list[dict[str, Any]]) -> PlanArtifact | None:
        academic_risks = []
        for risk in active_risks:
            value = risk.get("value", {})
            domain = str(value.get("domain") or value.get("project") or "").lower()
            if domain == "academics":
                academic_risks.append(risk)
        if not academic_risks:
            return None
        top = academic_risks[0]["value"]
        course_id = str(top.get("course_id") or "unknown_course")
        term_id = str(top.get("term_id") or "current_term")
        reason = str(top.get("reason") or "academic.risk_signal")
        severity = str(top.get("severity") or "medium").lower()
        high_urgency = severity in {"high", "critical"}
        steps: list[PlanStep] = [
            PlanStep(
                action_class="P0",
                proposed_action="academics_collect_context",
                expected_effect="Collected current course/deadline context for the selected risk.",
                rollback="none",
                payload={
                    "domain": "academics",
                    "course_id": course_id,
                    "term_id": term_id,
                    "reason": reason,
                    "severity": severity,
                },
            ),
            PlanStep(
                action_class="P1",
                proposed_action="academics_generate_study_recommendation",
                expected_effect="Generated a bounded study recommendation with priorities and time blocks.",
                rollback="discard_recommendation",
                payload={
                    "domain": "academics",
                    "course_id": course_id,
                    "term_id": term_id,
                    "risk_reason": reason,
                    "hours_until_due": top.get("hours_until_due"),
                    "title": top.get("title") or "",
                },
            ),
        ]
        approval_requirements: list[str] = []
        if high_urgency:
            steps.append(
                PlanStep(
                    action_class="P2",
                    proposed_action="academics_prepare_study_block",
                    expected_effect="Prepared a tentative study calendar block for review.",
                    rollback="remove_tentative_study_block",
                    payload={
                        "domain": "academics",
                        "course_id": course_id,
                        "term_id": term_id,
                        "minutes": 90,
                        "deadline_at": top.get("due_at"),
                    },
                    requires_approval=True,
                )
            )
            approval_requirements.append("P2 approval for tentative calendar study block")
            steps.append(
                PlanStep(
                    action_class="P2",
                    proposed_action="academics_draft_professor_email",
                    expected_effect="Prepared an email draft artifact for professor communication.",
                    rollback="discard_email_draft",
                    payload={
                        "domain": "academics",
                        "course_id": course_id,
                        "term_id": term_id,
                        "subject_hint": f"Course {course_id} deadline support",
                        "context_reason": reason,
                    },
                    requires_approval=True,
                )
            )
            approval_requirements.append("P2 approval for professor email draft artifact")
        return PlanArtifact(
            intent="stabilize_academic_risk",
            priority="high" if high_urgency else "medium",
            reasoning_summary=(
                f"Academic risk detected for {course_id} ({reason}); generated bounded study-response plan."
            ),
            steps=steps,
            approval_requirements=approval_requirements,
            expires_at=utc_now_iso(),
        )

    def tool_collect_context(self, payload: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        return {
            "domain": "academics",
            "course_id": payload.get("course_id"),
            "term_id": payload.get("term_id"),
            "reason": payload.get("reason"),
            "severity": payload.get("severity"),
            "dry_run": dry_run,
        }

    def tool_generate_study_recommendation(self, payload: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        hours = payload.get("hours_until_due")
        urgency = "normal"
        if isinstance(hours, (int, float)) and float(hours) <= 48:
            urgency = "high"
        block_minutes = 50 if urgency == "normal" else 90
        return {
            "domain": "academics",
            "course_id": payload.get("course_id"),
            "term_id": payload.get("term_id"),
            "urgency": urgency,
            "recommended_blocks": [
                {"minutes": block_minutes, "label": "focused problem-solving"},
                {"minutes": 25, "label": "review and recap"},
            ],
            "dry_run": dry_run,
        }

    def tool_prepare_study_block(self, payload: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        return {
            "domain": "academics",
            "course_id": payload.get("course_id"),
            "term_id": payload.get("term_id"),
            "minutes": int(payload.get("minutes") or 90),
            "deadline_at": payload.get("deadline_at"),
            "status": "tentative_block_prepared",
            "dry_run": dry_run,
        }

    def tool_draft_professor_email(self, payload: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        course_id = str(payload.get("course_id") or "course")
        reason = str(payload.get("context_reason") or "schedule coordination")
        body = (
            f"Subject: {payload.get('subject_hint') or f'{course_id} update'}\n\n"
            f"Hello Professor,\n\n"
            f"I'm writing regarding {course_id}. I identified a risk around {reason} and "
            "have prepared a structured study plan. If appropriate, I would appreciate any "
            "guidance on priorities for the next 48 hours.\n\n"
            "Thank you."
        )
        return {
            "domain": "academics",
            "course_id": course_id,
            "term_id": payload.get("term_id"),
            "subject": payload.get("subject_hint") or f"{course_id} update",
            "draft_body": body,
            "status": "draft_prepared",
            "dry_run": dry_run,
        }

    def register_tools(self) -> dict[str, Any]:
        return {
            "academics_collect_context": self.tool_collect_context,
            "academics_generate_study_recommendation": self.tool_generate_study_recommendation,
            "academics_prepare_study_block": self.tool_prepare_study_block,
            "academics_draft_professor_email": self.tool_draft_professor_email,
        }
