from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _day_key(now: datetime | None = None) -> str:
    return (now or _utc_now()).date().isoformat()


class DigestArchiveService:
    """Daily digest export + archive index for operator-facing async review."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.archive_root = self.db_path.parent / "archive"
        self.archive_root.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS digest_exports (
                day_key TEXT PRIMARY KEY,
                domains_json TEXT NOT NULL,
                markdown_path TEXT NOT NULL,
                html_path TEXT NOT NULL,
                json_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def _render_markdown(self, digest: dict[str, Any]) -> str:
        lines: list[str] = []
        lines.append(f"# JARVIS Daily Digest — {digest['day_key']}")
        lines.append("")
        lines.append("## Unified Priorities")
        for item in digest.get("priorities", []):
            lines.append(
                f"- [{item.get('domain', 'unknown')}] {item.get('reason') or item.get('risk_key')} "
                f"(confidence={item.get('confidence')})"
            )
        if not digest.get("priorities"):
            lines.append("- No active high-confidence priorities.")

        lines.append("")
        lines.append("## Morning Synthesis")
        morning = digest.get("morning") or {}
        lines.append(str(morning.get("narrative") or "No morning synthesis narrative."))

        lines.append("")
        lines.append("## Evening Synthesis")
        evening = digest.get("evening") or {}
        lines.append(str(evening.get("narrative") or "No evening synthesis narrative yet."))

        lines.append("")
        lines.append("## Interrupt Summary")
        intr = digest.get("interrupt_summary") or {}
        lines.append(
            f"- delivered: {intr.get('delivered', 0)} | suppressed: {intr.get('suppressed', 0)} | "
            f"snoozed: {intr.get('snoozed', 0)} | acknowledged: {intr.get('acknowledged', 0)}"
        )

        lines.append("")
        lines.append("## Pending Approvals")
        pending = digest.get("pending_approvals") or []
        if not pending:
            lines.append("- None")
        for item in pending:
            lines.append(
                f"- {item.get('approval_id')}: {item.get('action_desc')} "
                f"(plan={item.get('plan_id')}, step={item.get('step_id')})"
            )

        lines.append("")
        lines.append("## Backend Provenance")
        backend = digest.get("backend") or {}
        lines.append(
            f"- backend={backend.get('name')} model={backend.get('model')} "
            f"mode={backend.get('mode')}"
        )

        return "\n".join(lines).strip() + "\n"

    def _render_html(self, digest: dict[str, Any], markdown_body: str) -> str:
        safe_body = html.escape(markdown_body)
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>JARVIS Digest {html.escape(digest['day_key'])}</title>"
            "<style>body{font-family: ui-sans-serif,system-ui; max-width:920px; margin:2rem auto;"
            "line-height:1.5;padding:0 1rem;}pre{white-space:pre-wrap;background:#f6f8fa;padding:1rem;"
            "border-radius:8px;}h1{font-size:1.45rem;}</style></head><body>"
            f"<h1>JARVIS Daily Digest — {html.escape(digest['day_key'])}</h1>"
            f"<pre>{safe_body}</pre>"
            "</body></html>"
        )

    def _collect_digest(self, runtime: Any, *, day_key: str) -> dict[str, Any]:
        morning = runtime.synthesis_engine.store.get("morning", day_key) or {}
        evening = runtime.synthesis_engine.store.get("evening", day_key) or {}
        latest_thought = runtime.cognition.store.latest() or {}
        risks = runtime.state_graph.get_active_entities("Risk")

        priorities = []
        for risk in sorted(risks, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)[:8]:
            value = risk.get("value") or {}
            priorities.append(
                {
                    "domain": str(value.get("domain") or value.get("project") or "unknown"),
                    "risk_key": risk.get("entity_key"),
                    "reason": value.get("reason"),
                    "confidence": risk.get("confidence"),
                }
            )

        interrupts = runtime.list_interrupts(status="all", limit=200)
        summary = {
            "delivered": sum(1 for item in interrupts if item.get("status") == "delivered"),
            "suppressed": sum(1 for item in interrupts if item.get("status") == "suppressed"),
            "snoozed": sum(1 for item in interrupts if item.get("status") == "snoozed"),
            "acknowledged": sum(1 for item in interrupts if item.get("status") == "acknowledged"),
        }

        pending = runtime.security.list_approvals(status="pending")[:20]
        domains = sorted({str(item.get("domain") or "unknown") for item in priorities})

        return {
            "day_key": day_key,
            "generated_at": _utc_now_iso(),
            "domains": domains,
            "priorities": priorities,
            "morning": morning,
            "evening": evening,
            "interrupt_summary": summary,
            "pending_approvals": pending,
            "backend": {
                "name": latest_thought.get("backend_name"),
                "model": latest_thought.get("backend_model"),
                "mode": latest_thought.get("backend_mode"),
            },
        }

    def export_daily_digest(self, runtime: Any, *, day_key: str | None = None) -> dict[str, Any]:
        resolved_day = day_key or _day_key()
        digest = self._collect_digest(runtime, day_key=resolved_day)

        markdown_path = self.archive_root / f"{resolved_day}.digest.md"
        html_path = self.archive_root / f"{resolved_day}.digest.html"
        json_path = self.archive_root / f"{resolved_day}.digest.json"

        markdown_body = self._render_markdown(digest)
        html_body = self._render_html(digest, markdown_body)

        markdown_path.write_text(markdown_body, encoding="utf-8")
        html_path.write_text(html_body, encoding="utf-8")
        json_path.write_text(json.dumps(digest, indent=2, sort_keys=True), encoding="utf-8")

        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO digest_exports(
                day_key, domains_json, markdown_path, html_path, json_path, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(day_key) DO UPDATE SET
                domains_json = excluded.domains_json,
                markdown_path = excluded.markdown_path,
                html_path = excluded.html_path,
                json_path = excluded.json_path,
                updated_at = excluded.updated_at
            """,
            (
                resolved_day,
                json.dumps(digest.get("domains") or [], sort_keys=True),
                str(markdown_path),
                str(html_path),
                str(json_path),
                now,
                now,
            ),
        )
        self.conn.commit()
        return {
            "day_key": resolved_day,
            "domains": digest.get("domains") or [],
            "markdown_path": str(markdown_path),
            "html_path": str(html_path),
            "json_path": str(json_path),
            "generated_at": now,
        }

    def maybe_export_daily(self, runtime: Any, *, day_key: str | None = None) -> dict[str, Any]:
        resolved_day = day_key or _day_key()
        row = self.conn.execute(
            "SELECT * FROM digest_exports WHERE day_key = ?",
            (resolved_day,),
        ).fetchone()
        if row:
            return {
                "day_key": row["day_key"],
                "domains": json.loads(row["domains_json"]),
                "markdown_path": row["markdown_path"],
                "html_path": row["html_path"],
                "json_path": row["json_path"],
                "generated_at": row["updated_at"],
                "already_exists": True,
            }
        generated = self.export_daily_digest(runtime, day_key=resolved_day)
        generated["already_exists"] = False
        return generated

    def list_exports(self, *, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM digest_exports
            ORDER BY day_key DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "day_key": row["day_key"],
                    "domains": json.loads(row["domains_json"]),
                    "markdown_path": row["markdown_path"],
                    "html_path": row["html_path"],
                    "json_path": row["json_path"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return out

    def get_export(self, day_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM digest_exports WHERE day_key = ?",
            (day_key,),
        ).fetchone()
        if not row:
            return None
        return {
            "day_key": row["day_key"],
            "domains": json.loads(row["domains_json"]),
            "markdown_path": row["markdown_path"],
            "html_path": row["html_path"],
            "json_path": row["json_path"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def close(self) -> None:
        self.conn.close()
