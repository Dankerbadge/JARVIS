from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class ActionClass(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


@dataclass
class EnforcementDecision:
    allowed: bool
    reason: str


class SecurityManager:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                action_class TEXT NOT NULL,
                action_desc TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                approved_by TEXT,
                approved_at TEXT,
                denied_by TEXT,
                denied_at TEXT,
                expires_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                audit_id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                plan_id TEXT,
                step_id TEXT,
                action_class TEXT,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                details_json TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS rollback_markers (
                marker_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                marker_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS prepared_actions (
                prepare_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                action_class TEXT NOT NULL,
                action_desc TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                committed_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS security_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                kill_switch_enabled INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_packets (
                approval_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                packet_json TEXT NOT NULL,
                markdown TEXT NOT NULL,
                sandbox_json TEXT NOT NULL,
                preflight_json TEXT NOT NULL,
                touched_files_json TEXT NOT NULL,
                patch_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS publication_receipts (
                approval_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                publication_json TEXT NOT NULL,
                pr_payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_reviews (
                approval_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                repo_slug TEXT NOT NULL,
                review_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS review_artifacts (
                approval_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                repo_id TEXT NOT NULL,
                repo_slug TEXT NOT NULL,
                pr_number TEXT NOT NULL,
                branch TEXT NOT NULL,
                artifact_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS review_feedback (
                approval_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                repo_id TEXT NOT NULL,
                repo_slug TEXT NOT NULL,
                pr_number TEXT NOT NULL,
                branch TEXT NOT NULL,
                feedback_json TEXT NOT NULL,
                review_summary_json TEXT NOT NULL,
                comments_json TEXT NOT NULL,
                requested_reviewers_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS review_timeline_cursor (
                approval_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                repo_id TEXT NOT NULL,
                repo_slug TEXT NOT NULL,
                pr_number TEXT NOT NULL,
                branch TEXT NOT NULL,
                timeline_cursor TEXT,
                recent_events_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS merge_outcomes (
                approval_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                repo_id TEXT NOT NULL,
                repo_slug TEXT NOT NULL,
                pr_number TEXT NOT NULL,
                branch TEXT NOT NULL,
                merge_outcome TEXT NOT NULL,
                review_decision TEXT,
                outcome_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            "INSERT OR IGNORE INTO security_state(id, kill_switch_enabled) VALUES (1, 0)"
        )
        self._ensure_column("approvals", "denied_by", "TEXT")
        self._ensure_column("approvals", "denied_at", "TEXT")
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, column_sql: str) -> None:
        cols = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(col["name"] == column for col in cols):
            return
        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_sql}")

    def set_kill_switch(self, enabled: bool) -> None:
        self.conn.execute(
            "UPDATE security_state SET kill_switch_enabled = ? WHERE id = 1",
            (1 if enabled else 0,),
        )
        self.conn.commit()

    def is_kill_switch_enabled(self) -> bool:
        row = self.conn.execute(
            "SELECT kill_switch_enabled FROM security_state WHERE id = 1"
        ).fetchone()
        return bool(row["kill_switch_enabled"])

    def request_approval(
        self,
        *,
        plan_id: str,
        step_id: str,
        action_class: ActionClass,
        action_desc: str,
        ttl_minutes: int = 60,
    ) -> str:
        approval_id = _new_id("apr")
        now = datetime.now(timezone.utc)
        self.conn.execute(
            """
            INSERT INTO approvals (
                approval_id, plan_id, step_id, action_class, action_desc,
                status, requested_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                approval_id,
                plan_id,
                step_id,
                action_class.value,
                action_desc,
                now.isoformat(),
                (now + timedelta(minutes=ttl_minutes)).isoformat(),
            ),
        )
        self.conn.commit()
        return approval_id

    def approve(self, approval_id: str, approved_by: str = "user") -> None:
        self.conn.execute(
            """
            UPDATE approvals
            SET status = 'approved', approved_by = ?, approved_at = ?, denied_by = NULL, denied_at = NULL
            WHERE approval_id = ?
            """,
            (approved_by, _utc_now_iso(), approval_id),
        )
        self.conn.commit()

    def deny(self, approval_id: str, denied_by: str = "user") -> None:
        self.conn.execute(
            """
            UPDATE approvals
            SET status = 'denied', denied_by = ?, denied_at = ?
            WHERE approval_id = ?
            """,
            (denied_by, _utc_now_iso(), approval_id),
        )
        self.conn.commit()

    def _approval_is_valid(self, approval_id: str) -> bool:
        row = self.conn.execute(
            "SELECT status, expires_at FROM approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return False
        if row["status"] != "approved":
            return False
        expires_at = datetime.fromisoformat(row["expires_at"])
        return expires_at > datetime.now(timezone.utc)

    def find_approval(
        self,
        *,
        plan_id: str,
        step_id: str,
        statuses: list[str] | None = None,
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM approvals WHERE plan_id = ? AND step_id = ?"
        params: list[Any] = [plan_id, step_id]
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            query += f" AND status IN ({placeholders})"
            params.extend(statuses)
        query += " ORDER BY requested_at DESC LIMIT 1"
        row = self.conn.execute(query, tuple(params)).fetchone()
        if not row:
            return None
        return {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "action_class": row["action_class"],
            "action_desc": row["action_desc"],
            "status": row["status"],
            "requested_at": row["requested_at"],
            "approved_by": row["approved_by"],
            "approved_at": row["approved_at"],
            "denied_by": row["denied_by"],
            "denied_at": row["denied_at"],
            "expires_at": row["expires_at"],
        }

    def list_approvals(
        self,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if status and status != "all":
            rows = self.conn.execute(
                "SELECT * FROM approvals WHERE status = ? ORDER BY requested_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM approvals ORDER BY requested_at DESC"
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            results.append(
                {
                    "approval_id": row["approval_id"],
                    "plan_id": row["plan_id"],
                    "step_id": row["step_id"],
                    "action_class": row["action_class"],
                    "action_desc": row["action_desc"],
                    "status": row["status"],
                    "requested_at": row["requested_at"],
                    "approved_by": row["approved_by"],
                    "approved_at": row["approved_at"],
                    "denied_by": row["denied_by"],
                    "denied_at": row["denied_at"],
                    "expires_at": row["expires_at"],
                }
            )
        return results

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "action_class": row["action_class"],
            "action_desc": row["action_desc"],
            "status": row["status"],
            "requested_at": row["requested_at"],
            "approved_by": row["approved_by"],
            "approved_at": row["approved_at"],
            "denied_by": row["denied_by"],
            "denied_at": row["denied_at"],
            "expires_at": row["expires_at"],
        }

    def store_approval_packet(
        self,
        *,
        approval_id: str,
        plan_id: str,
        step_id: str,
        packet: dict[str, Any],
        markdown: str,
        sandbox: dict[str, Any],
        preflight: dict[str, Any],
        touched_files: list[str],
        patch_text: str,
    ) -> None:
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO approval_packets (
                approval_id, plan_id, step_id, packet_json, markdown, sandbox_json,
                preflight_json, touched_files_json, patch_text, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(approval_id) DO UPDATE SET
                plan_id = excluded.plan_id,
                step_id = excluded.step_id,
                packet_json = excluded.packet_json,
                markdown = excluded.markdown,
                sandbox_json = excluded.sandbox_json,
                preflight_json = excluded.preflight_json,
                touched_files_json = excluded.touched_files_json,
                patch_text = excluded.patch_text,
                updated_at = excluded.updated_at
            """,
            (
                approval_id,
                plan_id,
                step_id,
                json.dumps(packet, sort_keys=True),
                markdown,
                json.dumps(sandbox, sort_keys=True),
                json.dumps(preflight, sort_keys=True),
                json.dumps(touched_files, sort_keys=True),
                patch_text,
                now,
                now,
            ),
        )
        self.conn.commit()

    def get_approval_packet(self, approval_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM approval_packets WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "packet": json.loads(row["packet_json"]),
            "markdown": row["markdown"],
            "sandbox": json.loads(row["sandbox_json"]),
            "preflight": json.loads(row["preflight_json"]),
            "touched_files": json.loads(row["touched_files_json"]),
            "patch_text": row["patch_text"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def find_approval_packet(
        self,
        *,
        plan_id: str,
        step_id: str,
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM approval_packets
            WHERE plan_id = ? AND step_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (plan_id, step_id),
        ).fetchone()
        if not row:
            return None
        return {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "packet": json.loads(row["packet_json"]),
            "markdown": row["markdown"],
            "sandbox": json.loads(row["sandbox_json"]),
            "preflight": json.loads(row["preflight_json"]),
            "touched_files": json.loads(row["touched_files_json"]),
            "patch_text": row["patch_text"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def store_publication_receipt(
        self,
        *,
        approval_id: str,
        plan_id: str,
        step_id: str,
        publication: dict[str, Any],
        pr_payload: dict[str, Any],
    ) -> None:
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO publication_receipts (
                approval_id, plan_id, step_id, publication_json, pr_payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(approval_id) DO UPDATE SET
                plan_id = excluded.plan_id,
                step_id = excluded.step_id,
                publication_json = excluded.publication_json,
                pr_payload_json = excluded.pr_payload_json,
                updated_at = excluded.updated_at
            """,
            (
                approval_id,
                plan_id,
                step_id,
                json.dumps(publication, sort_keys=True),
                json.dumps(pr_payload, sort_keys=True),
                now,
                now,
            ),
        )
        self.conn.commit()

    def get_publication_receipt(self, approval_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM publication_receipts WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "publication": json.loads(row["publication_json"]),
            "pr_payload": json.loads(row["pr_payload_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def find_publication_receipt(
        self,
        *,
        plan_id: str,
        step_id: str,
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM publication_receipts
            WHERE plan_id = ? AND step_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (plan_id, step_id),
        ).fetchone()
        if not row:
            return None
        return {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "publication": json.loads(row["publication_json"]),
            "pr_payload": json.loads(row["pr_payload_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


    def store_provider_review(
        self,
        *,
        approval_id: str,
        plan_id: str,
        step_id: str,
        provider: str,
        repo_slug: str,
        review: dict[str, Any],
    ) -> None:
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO provider_reviews (
                approval_id, plan_id, step_id, provider, repo_slug, review_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(approval_id) DO UPDATE SET
                plan_id = excluded.plan_id,
                step_id = excluded.step_id,
                provider = excluded.provider,
                repo_slug = excluded.repo_slug,
                review_json = excluded.review_json,
                updated_at = excluded.updated_at
            """,
            (
                approval_id,
                plan_id,
                step_id,
                provider,
                repo_slug,
                json.dumps(review, sort_keys=True),
                now,
                now,
            ),
        )
        self.conn.commit()

    def get_provider_review(self, approval_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM provider_reviews WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "provider": row["provider"],
            "repo_slug": row["repo_slug"],
            "review": json.loads(row["review_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def find_provider_review(
        self,
        *,
        plan_id: str,
        step_id: str,
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM provider_reviews
            WHERE plan_id = ? AND step_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (plan_id, step_id),
        ).fetchone()
        if not row:
            return None
        return {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "provider": row["provider"],
            "repo_slug": row["repo_slug"],
            "review": json.loads(row["review_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def store_review_artifact(
        self,
        *,
        approval_id: str,
        plan_id: str,
        step_id: str,
        provider: str,
        repo_id: str,
        repo_slug: str,
        pr_number: str,
        branch: str,
        artifact: dict[str, Any],
    ) -> None:
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO review_artifacts (
                approval_id, plan_id, step_id, provider, repo_id, repo_slug,
                pr_number, branch, artifact_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(approval_id) DO UPDATE SET
                plan_id = excluded.plan_id,
                step_id = excluded.step_id,
                provider = excluded.provider,
                repo_id = excluded.repo_id,
                repo_slug = excluded.repo_slug,
                pr_number = excluded.pr_number,
                branch = excluded.branch,
                artifact_json = excluded.artifact_json,
                updated_at = excluded.updated_at
            """,
            (
                approval_id,
                plan_id,
                step_id,
                provider,
                repo_id,
                repo_slug,
                pr_number,
                branch,
                json.dumps(artifact, sort_keys=True),
                now,
                now,
            ),
        )
        self.conn.commit()

    def get_review_artifact(self, approval_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM review_artifacts WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "provider": row["provider"],
            "repo_id": row["repo_id"],
            "repo_slug": row["repo_slug"],
            "pr_number": row["pr_number"],
            "branch": row["branch"],
            "artifact": json.loads(row["artifact_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def find_review_artifact(
        self,
        *,
        plan_id: str,
        step_id: str,
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM review_artifacts
            WHERE plan_id = ? AND step_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (plan_id, step_id),
        ).fetchone()
        if not row:
            return None
        return {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "provider": row["provider"],
            "repo_id": row["repo_id"],
            "repo_slug": row["repo_slug"],
            "pr_number": row["pr_number"],
            "branch": row["branch"],
            "artifact": json.loads(row["artifact_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_recent_review_artifacts(
        self,
        *,
        limit: int = 100,
        repo_id: str | None = None,
        since_updated_at: str | None = None,
    ) -> list[dict[str, Any]]:
        resolved_limit = max(1, int(limit))
        if repo_id is None and since_updated_at is None:
            rows = self.conn.execute(
                """
                SELECT *
                FROM review_artifacts
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (resolved_limit,),
            ).fetchall()
        elif repo_id is not None and since_updated_at is None:
            rows = self.conn.execute(
                """
                SELECT *
                FROM review_artifacts
                WHERE repo_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (str(repo_id), resolved_limit),
            ).fetchall()
        elif repo_id is None and since_updated_at is not None:
            rows = self.conn.execute(
                """
                SELECT *
                FROM review_artifacts
                WHERE updated_at > ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (str(since_updated_at), resolved_limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT *
                FROM review_artifacts
                WHERE repo_id = ? AND updated_at > ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (str(repo_id), str(since_updated_at), resolved_limit),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "approval_id": row["approval_id"],
                    "plan_id": row["plan_id"],
                    "step_id": row["step_id"],
                    "provider": row["provider"],
                    "repo_id": row["repo_id"],
                    "repo_slug": row["repo_slug"],
                    "pr_number": row["pr_number"],
                    "branch": row["branch"],
                    "artifact": json.loads(row["artifact_json"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return out

    def store_review_feedback(
        self,
        *,
        approval_id: str,
        plan_id: str,
        step_id: str,
        provider: str,
        repo_id: str,
        repo_slug: str,
        pr_number: str,
        branch: str,
        feedback: dict[str, Any],
        review_summary: dict[str, Any],
        comments: dict[str, Any],
        requested_reviewers: list[str],
    ) -> None:
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO review_feedback (
                approval_id, plan_id, step_id, provider, repo_id, repo_slug,
                pr_number, branch, feedback_json, review_summary_json, comments_json,
                requested_reviewers_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(approval_id) DO UPDATE SET
                plan_id = excluded.plan_id,
                step_id = excluded.step_id,
                provider = excluded.provider,
                repo_id = excluded.repo_id,
                repo_slug = excluded.repo_slug,
                pr_number = excluded.pr_number,
                branch = excluded.branch,
                feedback_json = excluded.feedback_json,
                review_summary_json = excluded.review_summary_json,
                comments_json = excluded.comments_json,
                requested_reviewers_json = excluded.requested_reviewers_json,
                updated_at = excluded.updated_at
            """,
            (
                approval_id,
                plan_id,
                step_id,
                provider,
                repo_id,
                repo_slug,
                pr_number,
                branch,
                json.dumps(feedback, sort_keys=True),
                json.dumps(review_summary, sort_keys=True),
                json.dumps(comments, sort_keys=True),
                json.dumps(requested_reviewers, sort_keys=True),
                now,
                now,
            ),
        )
        self.conn.commit()

    def get_review_feedback(self, approval_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM review_feedback WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "provider": row["provider"],
            "repo_id": row["repo_id"],
            "repo_slug": row["repo_slug"],
            "pr_number": row["pr_number"],
            "branch": row["branch"],
            "feedback": json.loads(row["feedback_json"]),
            "review_summary": json.loads(row["review_summary_json"]),
            "comments": json.loads(row["comments_json"]),
            "requested_reviewers": json.loads(row["requested_reviewers_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def find_review_feedback(
        self,
        *,
        plan_id: str,
        step_id: str,
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM review_feedback
            WHERE plan_id = ? AND step_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (plan_id, step_id),
        ).fetchone()
        if not row:
            return None
        return {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "provider": row["provider"],
            "repo_id": row["repo_id"],
            "repo_slug": row["repo_slug"],
            "pr_number": row["pr_number"],
            "branch": row["branch"],
            "feedback": json.loads(row["feedback_json"]),
            "review_summary": json.loads(row["review_summary_json"]),
            "comments": json.loads(row["comments_json"]),
            "requested_reviewers": json.loads(row["requested_reviewers_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def store_review_timeline_cursor(
        self,
        *,
        approval_id: str,
        plan_id: str,
        step_id: str,
        provider: str,
        repo_id: str,
        repo_slug: str,
        pr_number: str,
        branch: str,
        timeline_cursor: str | None,
        recent_events: list[dict[str, Any]],
    ) -> None:
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO review_timeline_cursor (
                approval_id, plan_id, step_id, provider, repo_id, repo_slug,
                pr_number, branch, timeline_cursor, recent_events_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(approval_id) DO UPDATE SET
                plan_id = excluded.plan_id,
                step_id = excluded.step_id,
                provider = excluded.provider,
                repo_id = excluded.repo_id,
                repo_slug = excluded.repo_slug,
                pr_number = excluded.pr_number,
                branch = excluded.branch,
                timeline_cursor = excluded.timeline_cursor,
                recent_events_json = excluded.recent_events_json,
                updated_at = excluded.updated_at
            """,
            (
                approval_id,
                plan_id,
                step_id,
                provider,
                repo_id,
                repo_slug,
                pr_number,
                branch,
                timeline_cursor,
                json.dumps(recent_events, sort_keys=True),
                now,
                now,
            ),
        )
        self.conn.commit()

    def get_review_timeline_cursor(self, approval_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM review_timeline_cursor WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "provider": row["provider"],
            "repo_id": row["repo_id"],
            "repo_slug": row["repo_slug"],
            "pr_number": row["pr_number"],
            "branch": row["branch"],
            "timeline_cursor": row["timeline_cursor"],
            "recent_events": json.loads(row["recent_events_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def find_review_timeline_cursor(
        self,
        *,
        plan_id: str,
        step_id: str,
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM review_timeline_cursor
            WHERE plan_id = ? AND step_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (plan_id, step_id),
        ).fetchone()
        if not row:
            return None
        return {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "provider": row["provider"],
            "repo_id": row["repo_id"],
            "repo_slug": row["repo_slug"],
            "pr_number": row["pr_number"],
            "branch": row["branch"],
            "timeline_cursor": row["timeline_cursor"],
            "recent_events": json.loads(row["recent_events_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def store_merge_outcome(
        self,
        *,
        approval_id: str,
        plan_id: str,
        step_id: str,
        provider: str,
        repo_id: str,
        repo_slug: str,
        pr_number: str,
        branch: str,
        merge_outcome: str,
        review_decision: str | None,
        outcome: dict[str, Any],
    ) -> None:
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO merge_outcomes (
                approval_id, plan_id, step_id, provider, repo_id, repo_slug,
                pr_number, branch, merge_outcome, review_decision, outcome_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(approval_id) DO UPDATE SET
                plan_id = excluded.plan_id,
                step_id = excluded.step_id,
                provider = excluded.provider,
                repo_id = excluded.repo_id,
                repo_slug = excluded.repo_slug,
                pr_number = excluded.pr_number,
                branch = excluded.branch,
                merge_outcome = excluded.merge_outcome,
                review_decision = excluded.review_decision,
                outcome_json = excluded.outcome_json,
                updated_at = excluded.updated_at
            """,
            (
                approval_id,
                plan_id,
                step_id,
                provider,
                repo_id,
                repo_slug,
                pr_number,
                branch,
                merge_outcome,
                review_decision,
                json.dumps(outcome, sort_keys=True),
                now,
                now,
            ),
        )
        self.conn.commit()

    def get_merge_outcome(self, approval_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM merge_outcomes WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "provider": row["provider"],
            "repo_id": row["repo_id"],
            "repo_slug": row["repo_slug"],
            "pr_number": row["pr_number"],
            "branch": row["branch"],
            "merge_outcome": row["merge_outcome"],
            "review_decision": row["review_decision"],
            "outcome": json.loads(row["outcome_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def find_merge_outcome(
        self,
        *,
        plan_id: str,
        step_id: str,
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM merge_outcomes
            WHERE plan_id = ? AND step_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (plan_id, step_id),
        ).fetchone()
        if not row:
            return None
        return {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "step_id": row["step_id"],
            "provider": row["provider"],
            "repo_id": row["repo_id"],
            "repo_slug": row["repo_slug"],
            "pr_number": row["pr_number"],
            "branch": row["branch"],
            "merge_outcome": row["merge_outcome"],
            "review_decision": row["review_decision"],
            "outcome": json.loads(row["outcome_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_recent_merge_outcomes(
        self,
        *,
        limit: int = 100,
        repo_id: str | None = None,
        since_updated_at: str | None = None,
    ) -> list[dict[str, Any]]:
        resolved_limit = max(1, int(limit))
        if repo_id is None and since_updated_at is None:
            rows = self.conn.execute(
                """
                SELECT *
                FROM merge_outcomes
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (resolved_limit,),
            ).fetchall()
        elif repo_id is not None and since_updated_at is None:
            rows = self.conn.execute(
                """
                SELECT *
                FROM merge_outcomes
                WHERE repo_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (str(repo_id), resolved_limit),
            ).fetchall()
        elif repo_id is None and since_updated_at is not None:
            rows = self.conn.execute(
                """
                SELECT *
                FROM merge_outcomes
                WHERE updated_at > ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (str(since_updated_at), resolved_limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT *
                FROM merge_outcomes
                WHERE repo_id = ? AND updated_at > ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (str(repo_id), str(since_updated_at), resolved_limit),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "approval_id": row["approval_id"],
                    "plan_id": row["plan_id"],
                    "step_id": row["step_id"],
                    "provider": row["provider"],
                    "repo_id": row["repo_id"],
                    "repo_slug": row["repo_slug"],
                    "pr_number": row["pr_number"],
                    "branch": row["branch"],
                    "merge_outcome": row["merge_outcome"],
                    "review_decision": row["review_decision"],
                    "outcome": json.loads(row["outcome_json"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return out

    def find_provider_review_by_ref(
        self,
        *,
        repo_id: str,
        pr_number: str,
        branch: str,
    ) -> dict[str, Any] | None:
        rows = self.conn.execute(
            """
            SELECT *
            FROM provider_reviews
            ORDER BY updated_at DESC
            """,
        ).fetchall()
        for row in rows:
            review = json.loads(row["review_json"])
            metadata = review.get("metadata") or {}
            candidate_repo_id = str(metadata.get("repo_id") or review.get("repo_slug") or "").strip()
            candidate_number = str(review.get("number", "")).strip()
            candidate_branch = str(review.get("head_branch", "")).strip()
            if (
                candidate_repo_id == str(repo_id).strip()
                and candidate_number == str(pr_number).strip()
                and candidate_branch == str(branch).strip()
            ):
                return {
                    "approval_id": row["approval_id"],
                    "plan_id": row["plan_id"],
                    "step_id": row["step_id"],
                    "provider": row["provider"],
                    "repo_slug": row["repo_slug"],
                    "review": review,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
        return None

    def enforce(
        self,
        action_class: ActionClass,
        *,
        requires_approval: bool = False,
        approval_id: str | None = None,
    ) -> EnforcementDecision:
        if self.is_kill_switch_enabled():
            raise PermissionError("Global kill switch is enabled.")
        if action_class == ActionClass.P4:
            raise PermissionError("P4 actions are prohibited.")
        if action_class == ActionClass.P3:
            if not approval_id or not self._approval_is_valid(approval_id):
                raise PermissionError("P3 action requires explicit valid approval.")
        if action_class == ActionClass.P2 and requires_approval:
            if not approval_id or not self._approval_is_valid(approval_id):
                raise PermissionError("P2 action requires valid approval by policy.")
        return EnforcementDecision(allowed=True, reason="allowed")

    def prepare_action(
        self,
        *,
        plan_id: str,
        step_id: str,
        action_class: ActionClass,
        action_desc: str,
    ) -> str:
        prepare_id = _new_id("prep")
        self.conn.execute(
            """
            INSERT INTO prepared_actions (
                prepare_id, plan_id, step_id, action_class, action_desc, status, created_at
            ) VALUES (?, ?, ?, ?, ?, 'prepared', ?)
            """,
            (
                prepare_id,
                plan_id,
                step_id,
                action_class.value,
                action_desc,
                _utc_now_iso(),
            ),
        )
        self.conn.commit()
        return prepare_id

    def commit_action(self, prepare_id: str) -> None:
        self.conn.execute(
            """
            UPDATE prepared_actions
            SET status = 'committed', committed_at = ?
            WHERE prepare_id = ?
            """,
            (_utc_now_iso(), prepare_id),
        )
        self.conn.commit()

    def add_rollback_marker(
        self,
        *,
        plan_id: str,
        step_id: str,
        marker: dict[str, Any],
    ) -> str:
        marker_id = _new_id("rbk")
        self.conn.execute(
            """
            INSERT INTO rollback_markers (
                marker_id, plan_id, step_id, marker_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (marker_id, plan_id, step_id, json.dumps(marker, sort_keys=True), _utc_now_iso()),
        )
        self.conn.commit()
        return marker_id

    def audit(
        self,
        *,
        action: str,
        status: str,
        details: dict[str, Any],
        plan_id: str | None = None,
        step_id: str | None = None,
        action_class: ActionClass | None = None,
    ) -> str:
        audit_id = _new_id("adt")
        self.conn.execute(
            """
            INSERT INTO audit_log (
                audit_id, ts, plan_id, step_id, action_class, action, status, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                _utc_now_iso(),
                plan_id,
                step_id,
                action_class.value if action_class else None,
                action,
                status,
                json.dumps(details, sort_keys=True),
            ),
        )
        self.conn.commit()
        return audit_id

    def close(self) -> None:
        self.conn.close()
