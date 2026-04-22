from __future__ import annotations

import csv
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def _resolve_path_value(payload: Any, path: Any) -> Any:
    if path is None:
        return payload
    if isinstance(path, (list, tuple)):
        parts = [str(item).strip() for item in path if str(item).strip()]
    else:
        parts = [part.strip() for part in str(path or "").split(".") if part.strip()]
    value: Any = payload
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
            continue
        if isinstance(value, list):
            try:
                idx = int(part)
            except (TypeError, ValueError):
                return None
            if idx < 0 or idx >= len(value):
                return None
            value = value[idx]
            continue
        return None
    return value


class FeedbackFeedPuller:
    _APPLE_APP_STORE_PRESET = "apple_app_store_reviews_csv"
    _GOOGLE_PLAY_PRESET = "google_play_reviews_csv"
    _SUPPORTED_SOURCE_PRESETS = {
        _APPLE_APP_STORE_PRESET,
        _GOOGLE_PLAY_PRESET,
    }

    _APPLE_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
        "id": ("review_id", "id", "identifier", "review id"),
        "title": ("title", "review title"),
        "review": ("review", "content", "body", "text", "comment"),
        "summary": ("summary", "review", "content", "body", "text", "comment"),
        "rating": ("rating", "score", "stars", "star rating"),
        "created_at": ("submission_date", "date", "created_at", "timestamp", "created"),
        "app_version": ("app_version", "version", "app version"),
        "country": ("storefront", "country", "territory"),
        "language": ("language", "locale"),
        "developer_response": ("developer_response", "response", "reply"),
    }
    _GOOGLE_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
        "id": ("reviewid", "review_id", "id"),
        "title": ("title", "headline"),
        "review": ("content", "review", "text", "body", "comment"),
        "summary": ("summary", "content", "review", "text", "body", "comment"),
        "rating": ("score", "rating", "stars"),
        "created_at": ("at", "created_at", "timestamp", "created", "review_date"),
        "app_version": ("reviewcreatedversion", "app_version", "version"),
        "language": ("language", "review_language", "locale"),
        "country": ("country", "storefront"),
        "developer_response": ("replycontent", "reply_content", "developer_response", "response"),
        "developer_response_at": ("repliedat", "reply_at", "response_at"),
    }

    @staticmethod
    def _normalize_key(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

    @classmethod
    def _resolve_record_value(cls, record: dict[str, Any], candidates: tuple[str, ...]) -> Any:
        for candidate in candidates:
            if candidate in record:
                value = record.get(candidate)
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                return value
        normalized_lookup = {
            cls._normalize_key(key): value
            for key, value in dict(record or {}).items()
        }
        for candidate in candidates:
            normalized_candidate = cls._normalize_key(candidate)
            if not normalized_candidate:
                continue
            if normalized_candidate not in normalized_lookup:
                continue
            value = normalized_lookup.get(normalized_candidate)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
        return None

    @classmethod
    def _normalize_review_record(
        cls,
        record: dict[str, Any],
        *,
        field_candidates: dict[str, tuple[str, ...]],
        platform: str,
        source_schema: str,
    ) -> dict[str, Any]:
        out = dict(record)
        for target, candidates in dict(field_candidates).items():
            value = cls._resolve_record_value(record, candidates)
            if value is None:
                continue
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    continue
            out[str(target)] = value
        if not str(out.get("platform") or "").strip():
            out["platform"] = platform
        if not str(out.get("summary") or "").strip() and str(out.get("review") or "").strip():
            out["summary"] = str(out.get("review") or "").strip()
        if not str(out.get("review") or "").strip() and str(out.get("summary") or "").strip():
            out["review"] = str(out.get("summary") or "").strip()
        out["source_schema"] = source_schema
        return out

    @classmethod
    def _apply_source_preset(
        cls,
        records: list[dict[str, Any]],
        *,
        source_preset: str | None,
        feed_name: str,
    ) -> list[dict[str, Any]]:
        if source_preset is None:
            return [dict(item) for item in records]
        normalized_preset = str(source_preset or "").strip().lower()
        if not normalized_preset:
            return [dict(item) for item in records]
        if normalized_preset not in cls._SUPPORTED_SOURCE_PRESETS:
            raise ValueError(f"unsupported_source_preset:{source_preset}:{feed_name}")

        normalized_rows: list[dict[str, Any]] = []
        for row in records:
            current = dict(row or {})
            if normalized_preset == cls._APPLE_APP_STORE_PRESET:
                normalized_rows.append(
                    cls._normalize_review_record(
                        current,
                        field_candidates=cls._APPLE_FIELD_CANDIDATES,
                        platform="ios",
                        source_schema=cls._APPLE_APP_STORE_PRESET,
                    )
                )
                continue
            normalized_rows.append(
                cls._normalize_review_record(
                    current,
                    field_candidates=cls._GOOGLE_FIELD_CANDIDATES,
                    platform="android",
                    source_schema=cls._GOOGLE_PLAY_PRESET,
                )
            )
        return normalized_rows

    def _detect_format(self, *, url: str, explicit_format: str | None, content_type: str | None = None) -> str:
        if explicit_format is not None:
            value = str(explicit_format).strip().lower()
            if value in {"json", "jsonl", "ndjson", "csv", "rss", "xml"}:
                return "jsonl" if value == "ndjson" else ("rss" if value == "xml" else value)
            raise ValueError(f"unsupported_feed_format:{explicit_format}")

        lowered_type = str(content_type or "").lower()
        if "application/json" in lowered_type or "text/json" in lowered_type:
            return "json"
        if "application/x-ndjson" in lowered_type:
            return "jsonl"
        if "text/csv" in lowered_type:
            return "csv"
        if "application/rss+xml" in lowered_type or "application/xml" in lowered_type or "text/xml" in lowered_type:
            return "rss"

        lower_url = str(url).lower()
        if lower_url.endswith(".jsonl") or lower_url.endswith(".ndjson"):
            return "jsonl"
        if lower_url.endswith(".csv"):
            return "csv"
        if lower_url.endswith(".rss") or lower_url.endswith(".xml"):
            return "rss"
        return "json"

    @staticmethod
    def _read_source(url: str, *, timeout_seconds: float = 20.0, headers: dict[str, Any] | None = None) -> tuple[str, str | None]:
        raw = str(url or "").strip()
        if not raw:
            raise ValueError("empty_source_url")

        path_candidate = Path(raw).expanduser()
        if "://" not in raw and path_candidate.exists():
            text = path_candidate.read_text(encoding="utf-8")
            return text, None

        request = urllib.request.Request(raw)
        for key, value in dict(headers or {}).items():
            k = str(key).strip()
            if not k:
                continue
            request.add_header(k, str(value))
        with urllib.request.urlopen(request, timeout=float(timeout_seconds)) as response:
            content_type = response.headers.get("Content-Type")
            encoding = response.headers.get_content_charset("utf-8")
            body = response.read().decode(encoding, errors="replace")
        return body, content_type

    @staticmethod
    def _records_from_json(text: str, *, records_path: Any = None) -> list[dict[str, Any]]:
        payload = json.loads(text)
        block = _resolve_path_value(payload, records_path)
        if block is None:
            block = payload
        if isinstance(block, list):
            return [dict(item) for item in block if isinstance(item, dict)]
        if isinstance(block, dict):
            return [dict(block)]
        return []

    @staticmethod
    def _records_from_jsonl(text: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for raw in str(text or "").splitlines():
            line = str(raw or "").strip()
            if not line or line.startswith("#"):
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(dict(parsed))
        return rows

    @staticmethod
    def _records_from_csv(text: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        reader = csv.DictReader(str(text or "").splitlines())
        for row in reader:
            rows.append({str(key): value for key, value in dict(row or {}).items()})
        return rows

    @staticmethod
    def _rss_record(item: ET.Element) -> dict[str, Any]:
        def _find_text(name: str) -> str | None:
            elem = item.find(name)
            if elem is None:
                return None
            value = (elem.text or "").strip()
            return value or None

        record: dict[str, Any] = {
            "title": _find_text("title"),
            "summary": _find_text("title") or _find_text("description") or "",
            "review": _find_text("description") or "",
            "url": _find_text("link"),
            "created_at": _find_text("pubDate"),
            "id": _find_text("guid") or _find_text("link"),
        }
        return {key: value for key, value in record.items() if value is not None}

    @staticmethod
    def _records_from_rss(text: str) -> list[dict[str, Any]]:
        root = ET.fromstring(text)
        items = list(root.findall("./channel/item"))
        if not items:
            items = list(root.findall(".//item"))
        return [FeedbackFeedPuller._rss_record(item) for item in items]

    @staticmethod
    def _apply_mapping(
        records: list[dict[str, Any]],
        *,
        mapping: dict[str, Any] | None,
        static_fields: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not mapping:
            passthrough_rows = [dict(item) for item in records]
            if static_fields:
                for row in passthrough_rows:
                    for key, value in dict(static_fields or {}).items():
                        row[str(key)] = value
            return passthrough_rows
        mapped_rows: list[dict[str, Any]] = []
        for item in records:
            row: dict[str, Any] = {}
            for target, source in dict(mapping or {}).items():
                value = _resolve_path_value(item, source)
                if value is not None:
                    row[str(target)] = value
            for key, value in dict(static_fields or {}).items():
                row[str(key)] = value
            if row:
                mapped_rows.append(row)
        return mapped_rows

    @staticmethod
    def _write_jsonl(*, records: list[dict[str, Any]], output_path: Path, append: bool = False) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with output_path.open(mode, encoding="utf-8") as handle:
            for row in records:
                handle.write(json.dumps(dict(row), sort_keys=True) + "\n")

    def pull_feed(
        self,
        *,
        config_path: Path,
        feed: dict[str, Any],
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        name = str(feed.get("name") or "feed").strip() or "feed"
        source_url = str(feed.get("url") or feed.get("source") or "").strip()
        if not source_url:
            raise ValueError(f"feed_missing_url:{name}")
        url = source_url
        if "://" not in source_url:
            source_path = Path(source_url).expanduser()
            if not source_path.is_absolute():
                relative_candidate = (config_path.parent / source_path).resolve()
                if relative_candidate.exists():
                    source_path = relative_candidate
            if source_path.exists():
                url = str(source_path.resolve())

        body, content_type = self._read_source(
            url,
            timeout_seconds=float(feed.get("timeout_seconds") or timeout_seconds),
            headers=dict(feed.get("headers") or {}) if isinstance(feed.get("headers"), dict) else None,
        )
        fmt = self._detect_format(
            url=url,
            explicit_format=(str(feed.get("format")) if feed.get("format") is not None else None),
            content_type=content_type,
        )

        records_path = feed.get("records_path")
        if records_path is None:
            records_path = feed.get("items_path")

        if fmt == "json":
            records = self._records_from_json(body, records_path=records_path)
        elif fmt == "jsonl":
            records = self._records_from_jsonl(body)
        elif fmt == "csv":
            records = self._records_from_csv(body)
        elif fmt == "rss":
            records = self._records_from_rss(body)
        else:
            raise ValueError(f"unsupported_feed_format:{fmt}")

        source_preset = str(feed.get("source_preset") or "").strip().lower() or None
        records = self._apply_source_preset(
            records,
            source_preset=source_preset,
            feed_name=name,
        )

        mapped = self._apply_mapping(
            records,
            mapping=dict(feed.get("mapping") or {}) if isinstance(feed.get("mapping"), dict) else None,
            static_fields=(
                dict(feed.get("static_fields") or {}) if isinstance(feed.get("static_fields"), dict) else None
            ),
        )
        limit_raw = feed.get("limit")
        if limit_raw is not None:
            try:
                limit = max(0, int(limit_raw))
            except (TypeError, ValueError):
                limit = len(mapped)
        else:
            limit = len(mapped)
        mapped = mapped[:limit]

        output_raw = feed.get("output_path")
        if output_raw is None:
            raise ValueError(f"feed_missing_output_path:{name}")
        output_path = Path(str(output_raw)).expanduser()
        if not output_path.is_absolute():
            output_path = (config_path.parent / output_path).resolve()
        else:
            output_path = output_path.resolve()

        write_mode = str(feed.get("write_mode") or feed.get("output_mode") or "overwrite").strip().lower()
        if write_mode not in {"overwrite", "append"}:
            raise ValueError(f"unsupported_write_mode:{write_mode}:{name}")

        self._write_jsonl(
            records=mapped,
            output_path=output_path,
            append=(write_mode == "append"),
        )
        return {
            "name": name,
            "source_url": source_url,
            "resolved_source": url,
            "format": fmt,
            "source_preset": source_preset,
            "write_mode": write_mode,
            "record_count": len(records),
            "written_count": len(mapped),
            "output_path": str(output_path),
        }

    def pull_from_config(
        self,
        *,
        config_path: str | Path,
        feed_names: list[str] | None = None,
        allow_missing: bool = False,
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        resolved = Path(config_path).expanduser().resolve()
        loaded = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("invalid_feed_config:expected_json_object")
        feeds = list(loaded.get("feeds") or loaded.get("feed_jobs") or [])

        selected_names = {str(name).strip() for name in list(feed_names or []) if str(name).strip()}
        selected: list[dict[str, Any]] = []
        for item in feeds:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if selected_names and name not in selected_names:
                continue
            selected.append(item)

        runs: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for index, feed in enumerate(selected):
            name = str(feed.get("name") or f"feed_{index}")
            try:
                result = self.pull_feed(
                    config_path=resolved,
                    feed=feed,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                if allow_missing:
                    errors.append(
                        {
                            "index": index,
                            "name": name,
                            "status": "skipped",
                            "error": str(exc),
                        }
                    )
                    continue
                raise
            runs.append(result)

        return {
            "config_path": str(resolved),
            "feed_count": len(selected),
            "run_count": len(runs),
            "error_count": len(errors),
            "runs": runs,
            "errors": errors,
            "status": "ok" if not errors else "warning",
        }
