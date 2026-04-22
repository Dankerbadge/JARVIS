from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..models import utc_now_iso


class FrictionMiningStore:
    _STOP_WORDS = {
        "a",
        "an",
        "and",
        "app",
        "apps",
        "are",
        "at",
        "be",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "too",
        "with",
    }

    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS friction_signals (
                friction_id TEXT PRIMARY KEY,
                domain TEXT NOT NULL,
                source TEXT NOT NULL,
                segment TEXT NOT NULL,
                summary TEXT NOT NULL,
                normalized_summary TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                severity INTEGER NOT NULL,
                frustration_score REAL NOT NULL,
                symptom_tags_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_friction_signals_domain_status_updated
            ON friction_signals(domain, status, updated_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_friction_signals_canonical_updated
            ON friction_signals(canonical_key, updated_at DESC)
            """
        )
        self.conn.commit()

    @staticmethod
    def _normalize_token(token: str) -> str:
        cleaned = re.sub(r"[^a-z0-9]+", "", str(token or "").strip().lower())
        if len(cleaned) > 4 and cleaned.endswith("s"):
            cleaned = cleaned[:-1]
        return cleaned

    @classmethod
    def _normalize_summary(cls, summary: str) -> str:
        lowered = str(summary or "").strip().lower()
        lowered = re.sub(r"[^a-z0-9\s]+", " ", lowered)
        return re.sub(r"\s+", " ", lowered).strip()

    @classmethod
    def _canonical_key(cls, summary: str, symptom_tags: list[str]) -> str:
        normalized = cls._normalize_summary(summary)
        tokens: list[str] = []
        for part in normalized.split():
            normalized_part = cls._normalize_token(part)
            if not normalized_part:
                continue
            if normalized_part in cls._STOP_WORDS:
                continue
            tokens.append(normalized_part)
        if not tokens:
            for tag in list(symptom_tags or []):
                normalized_tag = cls._normalize_token(tag)
                if normalized_tag:
                    tokens.append(normalized_tag)
        if not tokens:
            return "misc"
        return " ".join(tokens[:8])

    @staticmethod
    def _normalize_tags(tags: list[str] | None) -> list[str]:
        out: list[str] = []
        for item in list(tags or []):
            value = str(item or "").strip().lower()
            value = re.sub(r"\s+", "_", value)
            value = re.sub(r"[^a-z0-9_\-]+", "", value)
            if value:
                out.append(value)
        return sorted(set(out))

    @staticmethod
    def _clamp_severity(value: int | float | None) -> int:
        if value is None:
            return 3
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 3
        return max(1, min(parsed, 5))

    @staticmethod
    def _clamp_score(value: int | float | None, *, fallback: float) -> float:
        if value is None:
            return fallback
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return fallback
        return max(0.0, min(parsed, 1.0))

    def record_signal(
        self,
        *,
        domain: str,
        source: str,
        summary: str,
        segment: str = "general",
        severity: int | float | None = 3,
        frustration_score: int | float | None = None,
        symptom_tags: list[str] | None = None,
        evidence: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "open",
        friction_id: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> dict[str, Any]:
        normalized_summary = self._normalize_summary(summary)
        tags = self._normalize_tags(symptom_tags)
        severity_score = self._clamp_severity(severity)
        score = self._clamp_score(frustration_score, fallback=float(severity_score) / 5.0)
        now = utc_now_iso()
        payload = {
            "friction_id": str(friction_id or f"frc_{uuid4().hex}"),
            "domain": str(domain or "unknown").strip().lower() or "unknown",
            "source": str(source or "unknown").strip().lower() or "unknown",
            "segment": str(segment or "general").strip().lower() or "general",
            "summary": str(summary or "").strip(),
            "normalized_summary": normalized_summary,
            "canonical_key": self._canonical_key(normalized_summary, tags),
            "severity": severity_score,
            "frustration_score": score,
            "symptom_tags": tags,
            "evidence": dict(evidence or {}),
            "metadata": dict(metadata or {}),
            "status": str(status or "open").strip().lower() or "open",
            "created_at": str(created_at or now),
            "updated_at": str(updated_at or now),
        }
        self.conn.execute(
            """
            INSERT INTO friction_signals (
                friction_id, domain, source, segment, summary, normalized_summary, canonical_key,
                severity, frustration_score, symptom_tags_json, evidence_json, metadata_json,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["friction_id"],
                payload["domain"],
                payload["source"],
                payload["segment"],
                payload["summary"],
                payload["normalized_summary"],
                payload["canonical_key"],
                payload["severity"],
                payload["frustration_score"],
                json.dumps(payload["symptom_tags"], sort_keys=True),
                json.dumps(payload["evidence"], sort_keys=True),
                json.dumps(payload["metadata"], sort_keys=True),
                payload["status"],
                payload["created_at"],
                payload["updated_at"],
            ),
        )
        self.conn.commit()
        return payload

    @staticmethod
    def _row_to_signal(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "friction_id": row["friction_id"],
            "domain": row["domain"],
            "source": row["source"],
            "segment": row["segment"],
            "summary": row["summary"],
            "normalized_summary": row["normalized_summary"],
            "canonical_key": row["canonical_key"],
            "severity": int(row["severity"]),
            "frustration_score": float(row["frustration_score"]),
            "symptom_tags": json.loads(str(row["symptom_tags_json"] or "[]")),
            "evidence": json.loads(str(row["evidence_json"] or "{}")),
            "metadata": json.loads(str(row["metadata_json"] or "{}")),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_signals(
        self,
        *,
        domain: str | None = None,
        source: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if domain is not None:
            clauses.append("domain = ?")
            params.append(str(domain).strip().lower())
        if source is not None:
            clauses.append("source = ?")
            params.append(str(source).strip().lower())
        if status is not None:
            clauses.append("status = ?")
            params.append(str(status).strip().lower())
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM friction_signals
            {where_sql}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (*params, max(1, int(limit))),
        ).fetchall()
        return [self._row_to_signal(row) for row in rows]

    def summarize_common_displeasures(
        self,
        *,
        domain: str | None = None,
        min_count: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        signals = self.list_signals(domain=domain, limit=max(200, int(limit) * 40))
        grouped: dict[str, list[dict[str, Any]]] = {}
        for signal in signals:
            key = str(signal.get("canonical_key") or "misc").strip() or "misc"
            grouped.setdefault(key, []).append(signal)

        clusters: list[dict[str, Any]] = []
        min_required = max(1, int(min_count))
        for key, items in grouped.items():
            if len(items) < min_required:
                continue
            severity_avg = sum(float(item.get("severity") or 0.0) for item in items) / len(items)
            frustration_avg = sum(float(item.get("frustration_score") or 0.0) for item in items) / len(items)
            impact_score = float(len(items)) * ((0.6 * severity_avg) + (0.4 * (frustration_avg * 5.0)))
            domain_counts = Counter(str(item.get("domain") or "unknown") for item in items)
            source_counts = Counter(str(item.get("source") or "unknown") for item in items)
            segment_counts = Counter(str(item.get("segment") or "general") for item in items)
            tag_counts: Counter[str] = Counter()
            for item in items:
                for tag in list(item.get("symptom_tags") or []):
                    value = str(tag or "").strip()
                    if value:
                        tag_counts[value] += 1
            representative = max(
                items,
                key=lambda item: (
                    float(item.get("severity") or 0.0),
                    float(item.get("frustration_score") or 0.0),
                    str(item.get("updated_at") or ""),
                ),
            )
            clusters.append(
                {
                    "canonical_key": key,
                    "signal_count": len(items),
                    "avg_severity": round(severity_avg, 4),
                    "avg_frustration_score": round(frustration_avg, 4),
                    "impact_score": round(impact_score, 4),
                    "dominant_domain": str(domain_counts.most_common(1)[0][0] if domain_counts else "unknown"),
                    "domain_breakdown": [
                        {"domain": name, "count": int(count)}
                        for name, count in domain_counts.most_common(3)
                    ],
                    "top_sources": [{"source": name, "count": int(count)} for name, count in source_counts.most_common(3)],
                    "top_segments": [
                        {"segment": name, "count": int(count)} for name, count in segment_counts.most_common(3)
                    ],
                    "top_tags": [{"tag": name, "count": int(count)} for name, count in tag_counts.most_common(5)],
                    "example_summary": str(representative.get("summary") or ""),
                    "example_signal_ids": [str(item.get("friction_id") or "") for item in items[:5]],
                    "latest_seen_at": max(str(item.get("updated_at") or "") for item in items),
                }
            )

        clusters.sort(
            key=lambda item: (
                -float(item.get("impact_score") or 0.0),
                -int(item.get("signal_count") or 0),
                str(item.get("canonical_key") or ""),
            )
        )
        limited = clusters[: max(1, int(limit))]
        return {
            "domain": str(domain).strip().lower() if domain is not None else None,
            "total_signals": len(signals),
            "cluster_count": len(clusters),
            "clusters": limited,
        }

    def close(self) -> None:
        self.conn.close()
