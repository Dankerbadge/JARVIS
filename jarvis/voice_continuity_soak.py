from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class VoiceContinuitySoakStore:
    """Stores multi-day voice/text continuity soak runs and turn-level metrics."""

    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_soak_runs (
                run_id TEXT PRIMARY KEY,
                label TEXT,
                metadata_json TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_soak_turns (
                turn_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                surface_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                channel_type TEXT,
                modality TEXT NOT NULL,
                expected_mode TEXT,
                selected_mode TEXT,
                mode_match INTEGER,
                contract_hash TEXT,
                user_model_revision TEXT,
                pushback_calibration_revision TEXT,
                continuity_ok INTEGER NOT NULL,
                continuity_mismatches_json TEXT NOT NULL,
                mismatch_suppressed INTEGER NOT NULL,
                phase_a_target_ms REAL,
                phase_b_target_ms REAL,
                phase_c_target_ms REAL,
                phase_a_observed_ms REAL,
                phase_b_observed_ms REAL,
                phase_c_observed_ms REAL,
                phase_a_delta_ms REAL,
                phase_b_delta_ms REAL,
                phase_c_delta_ms REAL,
                interrupted INTEGER NOT NULL,
                interruption_recovered INTEGER NOT NULL,
                pushback_triggered INTEGER NOT NULL,
                pushback_outcome TEXT NOT NULL,
                tone_before_json TEXT NOT NULL,
                tone_after_json TEXT NOT NULL,
                tone_drift_json TEXT NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_voice_soak_turns_run
            ON voice_soak_turns(run_id, created_at DESC)
            """
        )
        self.conn.commit()

    def start_run(
        self,
        *,
        run_id: str | None = None,
        label: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_run_id = str(run_id or "").strip() or _new_id("vsr")
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT OR REPLACE INTO voice_soak_runs(
                run_id, label, metadata_json, started_at, updated_at
            ) VALUES (?, ?, ?, COALESCE((SELECT started_at FROM voice_soak_runs WHERE run_id = ?), ?), ?)
            """,
            (
                normalized_run_id,
                str(label or "").strip() or None,
                json.dumps(dict(metadata or {}), sort_keys=True),
                normalized_run_id,
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.get_run(normalized_run_id) or {}

    def touch_run(self, run_id: str) -> None:
        self.conn.execute(
            "UPDATE voice_soak_runs SET updated_at = ? WHERE run_id = ?",
            (_utc_now_iso(), str(run_id)),
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM voice_soak_runs WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
        if not row:
            return None
        return self._run_row_to_dict(row)

    def list_runs(self, *, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM voice_soak_runs
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [self._run_row_to_dict(row) for row in rows]

    def record_turn(
        self,
        *,
        run_id: str,
        surface_id: str,
        session_id: str,
        channel_type: str | None,
        modality: str,
        expected_mode: str | None,
        selected_mode: str | None,
        mode_match: bool | None,
        contract_hash: str | None,
        user_model_revision: str | None,
        pushback_calibration_revision: str | None,
        continuity_ok: bool,
        continuity_mismatches: list[str] | None,
        mismatch_suppressed: bool,
        phase_a_target_ms: float | None,
        phase_b_target_ms: float | None,
        phase_c_target_ms: float | None,
        phase_a_observed_ms: float | None,
        phase_b_observed_ms: float | None,
        phase_c_observed_ms: float | None,
        interrupted: bool,
        interruption_recovered: bool,
        pushback_triggered: bool,
        pushback_outcome: str,
        tone_before: dict[str, Any] | None,
        tone_after: dict[str, Any] | None,
        tone_drift: dict[str, Any] | None,
        note: str | None = None,
    ) -> dict[str, Any]:
        normalized_turn_id = _new_id("vst")
        normalized_run_id = str(run_id).strip()
        now = _utc_now_iso()

        def _delta(observed: float | None, target: float | None) -> float | None:
            if observed is None or target is None:
                return None
            return float(observed) - float(target)

        phase_a_delta = _delta(phase_a_observed_ms, phase_a_target_ms)
        phase_b_delta = _delta(phase_b_observed_ms, phase_b_target_ms)
        phase_c_delta = _delta(phase_c_observed_ms, phase_c_target_ms)

        self.conn.execute(
            """
            INSERT INTO voice_soak_turns(
                turn_id, run_id, surface_id, session_id, channel_type, modality,
                expected_mode, selected_mode, mode_match, contract_hash, user_model_revision,
                pushback_calibration_revision, continuity_ok, continuity_mismatches_json, mismatch_suppressed,
                phase_a_target_ms, phase_b_target_ms, phase_c_target_ms,
                phase_a_observed_ms, phase_b_observed_ms, phase_c_observed_ms,
                phase_a_delta_ms, phase_b_delta_ms, phase_c_delta_ms,
                interrupted, interruption_recovered, pushback_triggered, pushback_outcome,
                tone_before_json, tone_after_json, tone_drift_json, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_turn_id,
                normalized_run_id,
                str(surface_id or "").strip() or "voice:unknown",
                str(session_id or "").strip() or "default",
                str(channel_type or "").strip() or None,
                str(modality or "voice").strip().lower() or "voice",
                str(expected_mode or "").strip().lower() or None,
                str(selected_mode or "").strip().lower() or None,
                None if mode_match is None else (1 if mode_match else 0),
                str(contract_hash or "").strip() or None,
                str(user_model_revision or "").strip() or None,
                str(pushback_calibration_revision or "").strip() or None,
                1 if continuity_ok else 0,
                json.dumps(list(continuity_mismatches or []), sort_keys=True),
                1 if mismatch_suppressed else 0,
                phase_a_target_ms,
                phase_b_target_ms,
                phase_c_target_ms,
                phase_a_observed_ms,
                phase_b_observed_ms,
                phase_c_observed_ms,
                phase_a_delta,
                phase_b_delta,
                phase_c_delta,
                1 if interrupted else 0,
                1 if interruption_recovered else 0,
                1 if pushback_triggered else 0,
                str(pushback_outcome or "none").strip().lower() or "none",
                json.dumps(dict(tone_before or {}), sort_keys=True),
                json.dumps(dict(tone_after or {}), sort_keys=True),
                json.dumps(dict(tone_drift or {}), sort_keys=True),
                str(note or "").strip() or None,
                now,
            ),
        )
        self.touch_run(normalized_run_id)
        self.conn.commit()
        return self.get_turn(normalized_turn_id) or {}

    def get_turn(self, turn_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM voice_soak_turns WHERE turn_id = ?",
            (str(turn_id),),
        ).fetchone()
        if not row:
            return None
        return self._turn_row_to_dict(row)

    def list_turns(self, *, run_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if run_id:
            rows = self.conn.execute(
                """
                SELECT *
                FROM voice_soak_turns
                WHERE run_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (str(run_id), max(1, int(limit))),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT *
                FROM voice_soak_turns
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [self._turn_row_to_dict(row) for row in rows]

    def close(self) -> None:
        self.conn.close()

    def _run_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "label": row["label"],
            "metadata": json.loads(row["metadata_json"]),
            "started_at": row["started_at"],
            "updated_at": row["updated_at"],
        }

    def _turn_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "turn_id": row["turn_id"],
            "run_id": row["run_id"],
            "surface_id": row["surface_id"],
            "session_id": row["session_id"],
            "channel_type": row["channel_type"],
            "modality": row["modality"],
            "expected_mode": row["expected_mode"],
            "selected_mode": row["selected_mode"],
            "mode_match": None if row["mode_match"] is None else bool(row["mode_match"]),
            "contract_hash": row["contract_hash"],
            "user_model_revision": row["user_model_revision"],
            "pushback_calibration_revision": row["pushback_calibration_revision"],
            "continuity_ok": bool(row["continuity_ok"]),
            "continuity_mismatches": json.loads(row["continuity_mismatches_json"]),
            "mismatch_suppressed": bool(row["mismatch_suppressed"]),
            "latency": {
                "target_ms": {
                    "phase_a_presence": row["phase_a_target_ms"],
                    "phase_b_first_useful": row["phase_b_target_ms"],
                    "phase_c_deep_followup": row["phase_c_target_ms"],
                },
                "observed_ms": {
                    "phase_a_presence": row["phase_a_observed_ms"],
                    "phase_b_first_useful": row["phase_b_observed_ms"],
                    "phase_c_deep_followup": row["phase_c_observed_ms"],
                },
                "delta_ms": {
                    "phase_a_presence": row["phase_a_delta_ms"],
                    "phase_b_first_useful": row["phase_b_delta_ms"],
                    "phase_c_deep_followup": row["phase_c_delta_ms"],
                },
            },
            "interrupted": bool(row["interrupted"]),
            "interruption_recovered": bool(row["interruption_recovered"]),
            "pushback_triggered": bool(row["pushback_triggered"]),
            "pushback_outcome": row["pushback_outcome"],
            "tone_before": json.loads(row["tone_before_json"]),
            "tone_after": json.loads(row["tone_after_json"]),
            "tone_drift": json.loads(row["tone_drift_json"]),
            "note": row["note"],
            "created_at": row["created_at"],
        }
