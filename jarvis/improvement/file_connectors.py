from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


class FeedbackFileConnector:
    @staticmethod
    def _detect_format(path: Path, file_format: str | None) -> str:
        if file_format is not None:
            resolved = str(file_format).strip().lower()
            if resolved in {"json", "jsonl", "ndjson", "csv"}:
                return "jsonl" if resolved == "ndjson" else resolved
            raise ValueError(f"unsupported_feedback_file_format:{file_format}")
        suffix = path.suffix.strip().lower()
        if suffix in {".jsonl", ".ndjson"}:
            return "jsonl"
        if suffix == ".csv":
            return "csv"
        return "json"

    @staticmethod
    def _to_record(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        return {str(key): value.get(key) for key in value.keys()}

    def _load_json(self, path: Path) -> list[dict[str, Any]]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [record for item in payload if (record := self._to_record(item)) is not None]
        if isinstance(payload, dict):
            for key in ("records", "items", "feedback", "rows"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [record for item in value if (record := self._to_record(item)) is not None]
            direct = self._to_record(payload)
            return [direct] if direct is not None else []
        return []

    def _load_jsonl(self, path: Path) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = str(raw_line or "").strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            record = self._to_record(parsed)
            if record is not None:
                out.append(record)
        return out

    def _load_csv(self, path: Path) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                out.append({str(key): value for key, value in dict(row or {}).items()})
        return out

    def load_records(
        self,
        *,
        path: str | Path,
        file_format: str | None = None,
    ) -> dict[str, Any]:
        input_path = Path(path).expanduser().resolve()
        if not input_path.exists():
            raise FileNotFoundError(str(input_path))
        fmt = self._detect_format(input_path, file_format)
        if fmt == "json":
            records = self._load_json(input_path)
        elif fmt == "jsonl":
            records = self._load_jsonl(input_path)
        elif fmt == "csv":
            records = self._load_csv(input_path)
        else:
            raise ValueError(f"unsupported_feedback_file_format:{fmt}")
        return {
            "input_path": str(input_path),
            "input_format": fmt,
            "records": records,
            "record_count": len(records),
        }


class MetricsArtifactAdapter:
    @staticmethod
    def _as_metrics_dict(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        out: dict[str, Any] = {}
        for key, raw in value.items():
            try:
                out[str(key)] = float(raw)
            except (TypeError, ValueError):
                continue
        return out

    @staticmethod
    def _first_non_empty_dict(*candidates: Any) -> dict[str, Any]:
        for candidate in candidates:
            resolved = MetricsArtifactAdapter._as_metrics_dict(candidate)
            if resolved:
                return resolved
        return {}

    @staticmethod
    def _extract_sample_size(payload: dict[str, Any]) -> int | None:
        for key in ("sample_size", "n", "count", "num_samples", "num_events"):
            value = payload.get(key)
            if value is None:
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed >= 0:
                return parsed
        for key in ("candidate", "evaluation", "results"):
            block = payload.get(key)
            if not isinstance(block, dict):
                continue
            nested = MetricsArtifactAdapter._extract_sample_size(block)
            if nested is not None:
                return nested
        return None

    def load_experiment_inputs(self, *, path: str | Path) -> dict[str, Any]:
        input_path = Path(path).expanduser().resolve()
        if not input_path.exists():
            raise FileNotFoundError(str(input_path))
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid_experiment_artifact:expected_json_object")

        baseline = self._first_non_empty_dict(
            payload.get("baseline_metrics"),
            (payload.get("baseline") or {}).get("metrics") if isinstance(payload.get("baseline"), dict) else None,
            payload.get("baseline") if isinstance(payload.get("baseline"), dict) else None,
        )
        candidate = self._first_non_empty_dict(
            payload.get("candidate_metrics"),
            (payload.get("candidate") or {}).get("metrics") if isinstance(payload.get("candidate"), dict) else None,
            payload.get("candidate") if isinstance(payload.get("candidate"), dict) else None,
        )
        guardrails = self._first_non_empty_dict(
            payload.get("guardrail_metrics"),
            payload.get("risk_metrics"),
            (payload.get("guardrails") or {}).get("metrics") if isinstance(payload.get("guardrails"), dict) else None,
            (payload.get("risk") or {}).get("metrics") if isinstance(payload.get("risk"), dict) else None,
        )

        if not baseline:
            raise ValueError("invalid_experiment_artifact:missing_baseline_metrics")
        if not candidate:
            raise ValueError("invalid_experiment_artifact:missing_candidate_metrics")

        environment = str(payload.get("environment") or payload.get("mode") or "sandbox").strip().lower() or "sandbox"
        notes = str(payload.get("notes") or payload.get("description") or "").strip() or None
        source_trace_id = str(payload.get("source_trace_id") or "").strip() or None
        sample_size = self._extract_sample_size(payload)

        return {
            "input_path": str(input_path),
            "baseline_metrics": baseline,
            "candidate_metrics": candidate,
            "guardrail_metrics": guardrails,
            "sample_size": sample_size,
            "environment": environment,
            "notes": notes,
            "source_trace_id": source_trace_id,
            "metadata": {
                "artifact_keys": sorted(payload.keys()),
            },
        }
