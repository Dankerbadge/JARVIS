from __future__ import annotations

import re
from typing import Any

from .friction_mining import FrictionMiningStore


class FrictionSourceAdapter:
    _SUMMARY_FIELDS = (
        "summary",
        "text",
        "content",
        "review",
        "complaint",
        "message",
        "body",
    )
    _TITLE_FIELDS = (
        "title",
        "headline",
        "subject",
    )
    _TAG_FIELDS = (
        "symptom_tags",
        "tags",
        "labels",
        "keywords",
    )

    _TAG_VOCAB = {
        "paywall",
        "pricing",
        "crash",
        "bugs",
        "sync",
        "latency",
        "slow",
        "ux",
        "onboarding",
        "notifications",
        "ads",
        "battery",
        "privacy",
        "risk",
        "slippage",
        "spread",
        "calibration",
        "forecast",
        "false_positive",
        "drift",
    }

    @staticmethod
    def _parse_tag_value(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            parts = re.split(r"[,;\n\t]+", value)
            return [part.strip().lower() for part in parts if part.strip()]
        if isinstance(value, (list, tuple, set)):
            out: list[str] = []
            for item in value:
                text = str(item).strip().lower()
                if text:
                    out.append(text)
            return out
        text = str(value).strip().lower()
        return [text] if text else []

    @staticmethod
    def _resolve_segment(record: dict[str, Any], default_segment: str) -> str:
        for key in ("segment", "user_segment", "cohort", "persona", "tier"):
            value = str(record.get(key) or "").strip().lower()
            if value:
                return value
        return str(default_segment or "general").strip().lower() or "general"

    @staticmethod
    def _resolve_severity(record: dict[str, Any], default_severity: int | float | None) -> int | float | None:
        explicit = record.get("severity")
        if explicit is not None:
            return explicit
        rating = record.get("rating")
        if rating is not None:
            try:
                rating_value = float(rating)
            except (TypeError, ValueError):
                rating_value = None
            if rating_value is not None:
                return max(1.0, min(5.0, round(6.0 - rating_value)))
        sentiment = record.get("sentiment_score")
        if sentiment is not None:
            try:
                sentiment_value = float(sentiment)
            except (TypeError, ValueError):
                sentiment_value = None
            if sentiment_value is not None:
                negativity = (1.0 - max(-1.0, min(1.0, sentiment_value))) / 2.0
                return max(1.0, min(5.0, round(1.0 + (4.0 * negativity))))
        return default_severity

    @staticmethod
    def _resolve_frustration_score(
        record: dict[str, Any],
        default_score: int | float | None,
        severity: int | float | None,
    ) -> int | float | None:
        explicit = record.get("frustration_score")
        if explicit is not None:
            return explicit
        sentiment = record.get("sentiment_score")
        if sentiment is not None:
            try:
                sentiment_value = float(sentiment)
            except (TypeError, ValueError):
                sentiment_value = None
            if sentiment_value is not None:
                return (1.0 - max(-1.0, min(1.0, sentiment_value))) / 2.0
        if severity is not None:
            try:
                return max(0.0, min(1.0, float(severity) / 5.0))
            except (TypeError, ValueError):
                pass
        return default_score

    @classmethod
    def _extract_summary(cls, record: dict[str, Any]) -> str:
        title = ""
        for key in cls._TITLE_FIELDS:
            value = str(record.get(key) or "").strip()
            if value:
                title = value
                break
        text = ""
        for key in cls._SUMMARY_FIELDS:
            value = str(record.get(key) or "").strip()
            if value:
                text = value
                break
        if title and text and title.lower() not in text.lower():
            return f"{title}: {text}"
        return text or title

    @classmethod
    def _extract_tags(cls, summary: str, record: dict[str, Any]) -> list[str]:
        tags: list[str] = []
        for key in cls._TAG_FIELDS:
            tags.extend(cls._parse_tag_value(record.get(key)))
        lowered = str(summary or "").lower()
        for candidate in cls._TAG_VOCAB:
            if candidate.replace("_", " ") in lowered:
                tags.append(candidate)
        normalized: list[str] = []
        for tag in tags:
            value = re.sub(r"[^a-z0-9_\-\s]+", "", str(tag or "").strip().lower())
            value = re.sub(r"\s+", "_", value)
            value = value.strip("_")
            if value:
                normalized.append(value)
        return sorted(set(normalized))

    @staticmethod
    def _build_evidence(record: dict[str, Any]) -> dict[str, Any]:
        evidence: dict[str, Any] = {}
        for key in (
            "id",
            "record_id",
            "review_id",
            "ticket_id",
            "url",
            "rating",
            "sentiment_score",
            "created_at",
            "occurred_at",
            "app_version",
            "platform",
        ):
            if key in record and record.get(key) is not None:
                evidence[key] = record.get(key)
        return evidence

    def ingest_feedback_batch(
        self,
        *,
        store: FrictionMiningStore,
        domain: str,
        source: str,
        records: list[dict[str, Any]],
        default_segment: str = "general",
        default_severity: int | float | None = 3,
        default_frustration_score: int | float | None = None,
        status: str = "open",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for index, raw in enumerate(list(records or [])):
            if not isinstance(raw, dict):
                skipped.append({"index": int(index), "reason": "record_not_object"})
                continue
            summary = self._extract_summary(raw)
            if not summary:
                skipped.append({"index": int(index), "reason": "missing_summary"})
                continue
            severity = self._resolve_severity(raw, default_severity)
            frustration_score = self._resolve_frustration_score(
                raw,
                default_score=default_frustration_score,
                severity=severity,
            )
            segment = self._resolve_segment(raw, default_segment)
            tags = self._extract_tags(summary, raw)
            signal_metadata = dict(metadata or {})
            signal_metadata["batch_index"] = int(index)
            if raw.get("source_context") is not None:
                signal_metadata["source_context"] = raw.get("source_context")
            created.append(
                store.record_signal(
                    domain=domain,
                    source=source,
                    summary=summary,
                    segment=segment,
                    severity=severity,
                    frustration_score=frustration_score,
                    symptom_tags=tags,
                    evidence=self._build_evidence(raw),
                    metadata=signal_metadata,
                    status=status,
                )
            )
        return {
            "domain": str(domain or "").strip().lower(),
            "source": str(source or "").strip().lower(),
            "requested_count": len(list(records or [])),
            "ingested_count": len(created),
            "skipped_count": len(skipped),
            "signals": created,
            "skipped": skipped,
        }
