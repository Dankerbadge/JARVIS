from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4


SignalSourceKind = Literal["chat", "provider", "file_import", "system", "operator"]
SignalProvider = Literal[
    "openclaw",
    "google_calendar",
    "gmail",
    "markets",
    "zenith",
    "jarvis_operator",
    "unknown",
]
SignalKind = Literal[
    "message.inbound",
    "calendar.event",
    "email.thread",
    "markets.signal",
    "context.update",
    "operator.command",
]
SignalTrust = Literal["trusted", "untrusted"]
SignalRedaction = Literal["none", "pseudonymized", "redacted"]
SignalPriority = Literal["low", "normal", "high", "urgent"]


_ALLOWED_SOURCE_KINDS = {"chat", "provider", "file_import", "system", "operator"}
_ALLOWED_PROVIDERS = {
    "openclaw",
    "google_calendar",
    "gmail",
    "markets",
    "zenith",
    "jarvis_operator",
    "unknown",
}
_ALLOWED_KINDS = {
    "message.inbound",
    "calendar.event",
    "email.thread",
    "markets.signal",
    "context.update",
    "operator.command",
}
_ALLOWED_TRUST = {"trusted", "untrusted"}
_ALLOWED_REDACTION = {"none", "pseudonymized", "redacted"}
_ALLOWED_PRIORITY = {"low", "normal", "high", "urgent"}

DEFAULT_SIGNAL_SCHEMA_VERSION = "jarvis.signal.v1"
DEFAULT_MAX_PAYLOAD_BYTES = 20_000
_SENSITIVE_KEY_HINTS = (
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "api_key",
    "private_key",
    "bearer",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_iso(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return _utc_now_iso()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return _utc_now_iso()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _sanitize_value(value: Any, *, max_depth: int = 8, depth: int = 0) -> tuple[Any, bool]:
    if depth >= max_depth:
        return ("[TRUNCATED_DEPTH]", True)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        changed = False
        for key, item in value.items():
            normalized_key = str(key)
            lower_key = normalized_key.strip().lower()
            if any(token in lower_key for token in _SENSITIVE_KEY_HINTS):
                out[normalized_key] = "[REDACTED]"
                changed = True
                continue
            sanitized_item, item_changed = _sanitize_value(item, max_depth=max_depth, depth=depth + 1)
            out[normalized_key] = sanitized_item
            changed = changed or item_changed
        return out, changed
    if isinstance(value, list):
        changed = False
        trimmed = value[:200]
        if len(trimmed) != len(value):
            changed = True
        out_list: list[Any] = []
        for item in trimmed:
            sanitized_item, item_changed = _sanitize_value(item, max_depth=max_depth, depth=depth + 1)
            out_list.append(sanitized_item)
            changed = changed or item_changed
        return out_list, changed
    if isinstance(value, str):
        if len(value) > 4_000:
            return (value[:4_000] + "...[TRUNCATED]", True)
        return value, False
    return value, False


def _truncate_payload(payload: dict[str, Any], *, max_payload_bytes: int) -> tuple[dict[str, Any], bool]:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    if len(raw.encode("utf-8")) <= max_payload_bytes:
        return payload, False
    truncated: dict[str, Any] = {
        "summary": "payload_truncated_for_size",
        "keys": sorted(payload.keys())[:100],
        "original_size_bytes": len(raw.encode("utf-8")),
    }
    if "title" in payload:
        truncated["title"] = payload.get("title")
    if "body" in payload and isinstance(payload.get("body"), str):
        body = str(payload.get("body"))
        truncated["body_preview"] = body[:2_000] + ("...[TRUNCATED]" if len(body) > 2_000 else "")
    if "type" in payload:
        truncated["type"] = payload.get("type")
    return truncated, True


@dataclass(frozen=True)
class Provenance:
    source_kind: SignalSourceKind
    provider: SignalProvider
    source_id: str
    received_at: str = field(default_factory=_utc_now_iso)
    trust: SignalTrust = "untrusted"
    redaction_level: SignalRedaction = "redacted"
    raw_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "provider": self.provider,
            "source_id": self.source_id,
            "received_at": self.received_at,
            "trust": self.trust,
            "redaction_level": self.redaction_level,
            "raw_ref": self.raw_ref,
        }


@dataclass(frozen=True)
class SignalEnvelope:
    id: str
    schema_version: str
    kind: SignalKind
    payload: dict[str, Any]
    provenance: Provenance
    session_key: str | None = None
    identity_key: str | None = None
    priority_hint: SignalPriority | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "kind": self.kind,
            "payload": self.payload,
            "provenance": self.provenance.to_dict(),
            "session_key": self.session_key,
            "identity_key": self.identity_key,
            "priority_hint": self.priority_hint,
        }


def normalize_signal_envelope(
    raw: dict[str, Any],
    *,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> tuple[SignalEnvelope, dict[str, Any]]:
    data = dict(raw or {})
    signal_id = str(data.get("id") or data.get("signal_id") or f"sig_{uuid4().hex}")
    schema_version = str(data.get("schema_version") or DEFAULT_SIGNAL_SCHEMA_VERSION).strip() or DEFAULT_SIGNAL_SCHEMA_VERSION
    if schema_version != DEFAULT_SIGNAL_SCHEMA_VERSION:
        raise ValueError(f"unsupported_schema_version: {schema_version}")

    kind = str(data.get("kind") or "message.inbound").strip().lower()
    if kind not in _ALLOWED_KINDS:
        raise ValueError(f"unsupported_signal_kind: {kind}")

    provenance_raw = dict(data.get("provenance") or {})
    source_kind = str(provenance_raw.get("source_kind") or "system").strip().lower()
    if source_kind not in _ALLOWED_SOURCE_KINDS:
        source_kind = "system"

    provider = str(provenance_raw.get("provider") or "unknown").strip().lower()
    if provider not in _ALLOWED_PROVIDERS:
        provider = "unknown"

    source_id = str(provenance_raw.get("source_id") or signal_id).strip() or signal_id
    trust = str(provenance_raw.get("trust") or ("trusted" if source_kind == "operator" else "untrusted")).strip().lower()
    if trust not in _ALLOWED_TRUST:
        trust = "untrusted"

    redaction_level = str(provenance_raw.get("redaction_level") or "redacted").strip().lower()
    if redaction_level not in _ALLOWED_REDACTION:
        redaction_level = "redacted"

    payload_raw = data.get("payload")
    payload_obj = dict(payload_raw) if isinstance(payload_raw, dict) else {}
    sanitized, redacted = _sanitize_value(payload_obj)
    sanitized_payload = dict(sanitized) if isinstance(sanitized, dict) else {}
    sanitized_payload, truncated = _truncate_payload(
        sanitized_payload,
        max_payload_bytes=max_payload_bytes,
    )

    priority_hint_raw = str(data.get("priority_hint") or "").strip().lower()
    priority_hint = priority_hint_raw if priority_hint_raw in _ALLOWED_PRIORITY else None
    session_key = str(data.get("session_key") or "").strip() or None
    identity_key = str(data.get("identity_key") or "").strip() or None

    received_at = _normalize_iso(str(provenance_raw.get("received_at") or ""))
    provenance = Provenance(
        source_kind=source_kind,  # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
        source_id=source_id,
        received_at=received_at,
        trust=trust,  # type: ignore[arg-type]
        redaction_level=redaction_level,  # type: ignore[arg-type]
        raw_ref=str(provenance_raw.get("raw_ref") or "").strip() or None,
    )
    envelope = SignalEnvelope(
        id=signal_id,
        schema_version=schema_version,
        kind=kind,  # type: ignore[arg-type]
        payload=sanitized_payload,
        provenance=provenance,
        session_key=session_key,
        identity_key=identity_key,
        priority_hint=priority_hint,  # type: ignore[arg-type]
    )
    canonical = {
        "kind": envelope.kind,
        "provider": envelope.provenance.provider,
        "source_id": envelope.provenance.source_id,
        "payload": envelope.payload,
    }
    canonical_text = json.dumps(canonical, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    content_hash = f"sha256:{hashlib.sha256(canonical_text.encode('utf-8')).hexdigest()}"
    dedupe_key = f"{envelope.kind}:{envelope.provenance.provider}:{envelope.provenance.source_id}:{content_hash}"
    return envelope, {
        "content_hash": content_hash,
        "dedupe_key": dedupe_key,
        "truncated": bool(truncated),
        "redacted": bool(redacted),
        "max_payload_bytes": int(max_payload_bytes),
    }


class SignalIngestStore:
    """Replay-safe persistence for canonical signal ingestion records."""

    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_ingest_records (
                signal_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                kind TEXT NOT NULL,
                provenance_json TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                dedupe_key TEXT NOT NULL UNIQUE,
                truncated INTEGER NOT NULL,
                redacted INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_signal_ingest_created_at
            ON signal_ingest_records(created_at DESC)
            """
        )
        self.conn.commit()

    def record(
        self,
        *,
        signal: SignalEnvelope,
        raw_payload: dict[str, Any],
        content_hash: str,
        dedupe_key: str,
        truncated: bool,
        redacted: bool,
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        try:
            self.conn.execute(
                """
                INSERT INTO signal_ingest_records(
                    signal_id, schema_version, kind, provenance_json, raw_json, payload_json,
                    content_hash, dedupe_key, truncated, redacted, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.id,
                    signal.schema_version,
                    signal.kind,
                    json.dumps(signal.provenance.to_dict(), sort_keys=True),
                    json.dumps(raw_payload, sort_keys=True),
                    json.dumps(signal.payload, sort_keys=True),
                    content_hash,
                    dedupe_key,
                    1 if truncated else 0,
                    1 if redacted else 0,
                    now,
                ),
            )
            self.conn.commit()
            return {
                "duplicate": False,
                "signal_id": signal.id,
                "created_at": now,
            }
        except sqlite3.IntegrityError:
            self.conn.rollback()
            row = self.conn.execute(
                """
                SELECT signal_id, created_at
                FROM signal_ingest_records
                WHERE dedupe_key = ?
                """,
                (dedupe_key,),
            ).fetchone()
            if not row:
                raise
            return {
                "duplicate": True,
                "signal_id": row["signal_id"],
                "created_at": row["created_at"],
            }

    def list_recent(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM signal_ingest_records
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "signal_id": row["signal_id"],
                    "schema_version": row["schema_version"],
                    "kind": row["kind"],
                    "provenance": json.loads(row["provenance_json"]),
                    "payload": json.loads(row["payload_json"]),
                    "content_hash": row["content_hash"],
                    "dedupe_key": row["dedupe_key"],
                    "truncated": bool(row["truncated"]),
                    "redacted": bool(row["redacted"]),
                    "created_at": row["created_at"],
                }
            )
        return out

    def close(self) -> None:
        self.conn.close()
