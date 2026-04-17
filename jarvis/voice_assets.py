from __future__ import annotations

import copy
import csv
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any


class VoiceAssetPackStore:
    """Resolves the active local voice asset pack used by JARVIS voice flows."""

    def __init__(self, repo_path: str | Path) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.pointer_path = self.repo_path / ".jarvis" / "voice" / "ACTIVE_VOICE_PACK.json"
        self._cache_key: tuple[Any, ...] | None = None
        self._cache_payload: dict[str, Any] | None = None

    def _resolve_path(self, raw_value: Any) -> Path | None:
        value = str(raw_value or "").strip()
        if not value:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (self.repo_path / path).resolve()
        return path

    @staticmethod
    def _safe_read_json(path: Path) -> dict[str, Any] | None:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return loaded if isinstance(loaded, dict) else None

    @staticmethod
    def _make_pack_id(*, pack_root: str, profile: str | None, updated_at: str | None) -> str:
        seed = "|".join([pack_root, str(profile or ""), str(updated_at or "")])
        return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _audio_clip_count(clips_dir: Path) -> int:
        if not clips_dir.exists():
            return 0
        count = 0
        for item in clips_dir.iterdir():
            if not item.is_file():
                continue
            if item.suffix.lower() in {".wav", ".mp3", ".m4a", ".flac", ".ogg"}:
                count += 1
        return count

    @staticmethod
    def _safe_read_csv_rows(path: Path) -> list[dict[str, str]] | None:
        try:
            with path.open("r", encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
        except OSError:
            return None
        return [row for row in rows if isinstance(row, dict)] if rows else []

    @staticmethod
    def _parse_float(raw_value: Any) -> float | None:
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _quantile(values: list[float], q: float) -> float | None:
        if not values:
            return None
        ordered = sorted(float(value) for value in values)
        index = int(round((len(ordered) - 1) * max(0.0, min(1.0, float(q)))))
        return float(ordered[max(0, min(index, len(ordered) - 1))])

    @classmethod
    def _extract_metric(
        cls,
        row: dict[str, str],
        *,
        generic_key: str,
        polished_key: str,
        master_key: str,
        input_key: str,
    ) -> float | None:
        selected = str(row.get("selected") or "").strip().lower()
        candidates: list[str] = []
        if selected == "polished":
            candidates.extend([polished_key, master_key])
        elif selected == "master":
            candidates.append(master_key)
        elif selected == "input":
            candidates.append(input_key)
        candidates.extend([generic_key, polished_key, master_key, input_key])
        seen: set[str] = set()
        for key in candidates:
            if key in seen:
                continue
            seen.add(key)
            value = cls._parse_float(row.get(key))
            if value is not None:
                return value
        return None

    @classmethod
    def _load_clip_quality_summary(cls, pack_root: Path) -> tuple[dict[str, Any] | None, Path | None]:
        candidate_names = [
            "manifest.csv",
            "master_selection.csv",
            "polish_selection.csv",
            "finetune_selection.csv",
            "hybrid_selection.csv",
            "enhance_manifest.csv",
            "clip_manifest.csv",
        ]
        manifest_path = next((pack_root / name for name in candidate_names if (pack_root / name).exists()), None)
        if manifest_path is None:
            return None, None
        rows = cls._safe_read_csv_rows(manifest_path)
        if rows is None:
            return None, manifest_path
        if not rows:
            return {
                "manifest_rows": 0,
                "metrics_rows": 0,
                "coverage_ratio": 0.0,
                "score": 0.0,
                "grade": "weak",
            }, manifest_path

        hiss_values: list[float] = []
        silence_values: list[float] = []
        harmonicity_values: list[float] = []
        clarity_delta_values: list[float] = []
        score_delta_values: list[float] = []
        actor_match_scores: list[float] = []
        movie_match_scores: list[float] = []
        duration_values: list[float] = []
        metric_rows = 0

        for row in rows:
            hiss_value = cls._extract_metric(
                row,
                generic_key="hiss_ratio",
                polished_key="polish_hiss_ratio",
                master_key="master_hiss_ratio",
                input_key="orig_hiss_ratio",
            )
            silence_value = cls._extract_metric(
                row,
                generic_key="silence_pct",
                polished_key="polish_silence_pct",
                master_key="master_silence_pct",
                input_key="orig_silence_pct",
            )
            harmonicity_value = cls._extract_metric(
                row,
                generic_key="harmonicity",
                polished_key="polish_harmonicity",
                master_key="master_harmonicity",
                input_key="orig_harmonicity",
            )
            clarity_delta = cls._extract_metric(
                row,
                generic_key="clarity_delta",
                polished_key="delta_clarity",
                master_key="delta_clarity",
                input_key="delta_clarity",
            )
            score_delta = cls._extract_metric(
                row,
                generic_key="score_delta",
                polished_key="delta_score",
                master_key="delta_total_score",
                input_key="delta_total_score",
            )
            actor_match_score = cls._parse_float(row.get("actor_match_score"))
            movie_match_score = cls._parse_float(row.get("movie_match_score"))
            duration_value = cls._parse_float(row.get("duration_sec"))
            if hiss_value is not None:
                hiss_values.append(hiss_value)
            if silence_value is not None:
                silence_values.append(silence_value)
            if harmonicity_value is not None:
                harmonicity_values.append(harmonicity_value)
            if clarity_delta is not None:
                clarity_delta_values.append(clarity_delta)
            if score_delta is not None:
                score_delta_values.append(score_delta)
            if actor_match_score is not None:
                actor_match_scores.append(actor_match_score)
            if movie_match_score is not None:
                movie_match_scores.append(movie_match_score)
            if duration_value is not None and duration_value > 0.0:
                duration_values.append(duration_value)
            if any(value is not None for value in (hiss_value, silence_value, harmonicity_value)):
                metric_rows += 1

        if metric_rows <= 0:
            return {
                "manifest_rows": len(rows),
                "metrics_rows": 0,
                "coverage_ratio": 0.0,
                "score": 0.0,
                "grade": "weak",
            }, manifest_path

        mean_hiss = statistics.fmean(hiss_values) if hiss_values else None
        p90_hiss = cls._quantile(hiss_values, 0.9)
        mean_silence = statistics.fmean(silence_values) if silence_values else None
        p90_silence = cls._quantile(silence_values, 0.9)
        mean_harmonicity = statistics.fmean(harmonicity_values) if harmonicity_values else None
        p10_harmonicity = cls._quantile(harmonicity_values, 0.1)
        mean_clarity_delta = statistics.fmean(clarity_delta_values) if clarity_delta_values else None
        mean_score_delta = statistics.fmean(score_delta_values) if score_delta_values else None
        mean_actor_match_score = statistics.fmean(actor_match_scores) if actor_match_scores else None
        p10_actor_match_score = cls._quantile(actor_match_scores, 0.1)
        mean_movie_match_score = statistics.fmean(movie_match_scores) if movie_match_scores else None
        p10_movie_match_score = cls._quantile(movie_match_scores, 0.1)
        mean_duration = statistics.fmean(duration_values) if duration_values else None
        p10_duration = cls._quantile(duration_values, 0.1)
        p90_duration = cls._quantile(duration_values, 0.9)
        cadence_variation_cv = None
        if mean_duration is not None and mean_duration > 1e-9 and len(duration_values) >= 2:
            cadence_variation_cv = float(statistics.pstdev(duration_values) / mean_duration)

        quality_score = 1.0
        if mean_hiss is not None and mean_hiss > 0.09:
            quality_score -= min(0.34, (mean_hiss - 0.09) * 2.0)
        if p90_hiss is not None and p90_hiss > 0.14:
            quality_score -= min(0.18, (p90_hiss - 0.14) * 1.1)
        if mean_silence is not None and mean_silence > 10.0:
            quality_score -= min(0.28, (mean_silence - 10.0) * 0.021)
        if p90_silence is not None and p90_silence > 15.0:
            quality_score -= min(0.16, (p90_silence - 15.0) * 0.013)
        if mean_harmonicity is not None and mean_harmonicity < 0.4:
            quality_score -= min(0.2, (0.4 - mean_harmonicity) * 2.4)
        if p10_harmonicity is not None and p10_harmonicity < 0.34:
            quality_score -= min(0.12, (0.34 - p10_harmonicity) * 1.8)
        if mean_clarity_delta is not None and mean_clarity_delta < 0.0:
            quality_score -= min(0.08, abs(mean_clarity_delta) * 0.25)
        if mean_score_delta is not None and mean_score_delta < 0.0:
            quality_score -= min(0.1, abs(mean_score_delta) * 0.2)
        if mean_actor_match_score is not None and mean_actor_match_score < 0.6:
            quality_score -= min(0.15, (0.6 - mean_actor_match_score) * 0.45)
        if p10_actor_match_score is not None and p10_actor_match_score < 0.44:
            quality_score -= min(0.1, (0.44 - p10_actor_match_score) * 0.4)
        if mean_movie_match_score is not None and mean_movie_match_score < 0.58:
            quality_score -= min(0.15, (0.58 - mean_movie_match_score) * 0.5)
        if p10_movie_match_score is not None and p10_movie_match_score < 0.42:
            quality_score -= min(0.1, (0.42 - p10_movie_match_score) * 0.45)
        quality_score = max(0.0, min(1.0, quality_score))

        cadence_score = 1.0
        if mean_duration is not None:
            if mean_duration < 1.2:
                cadence_score -= min(0.2, (1.2 - mean_duration) * 0.22)
            elif mean_duration > 5.6:
                cadence_score -= min(0.18, (mean_duration - 5.6) * 0.08)
        if cadence_variation_cv is not None and cadence_variation_cv > 0.45:
            cadence_score -= min(0.24, (cadence_variation_cv - 0.45) * 0.6)
        if p10_duration is not None and p10_duration < 1.0:
            cadence_score -= min(0.11, (1.0 - p10_duration) * 0.22)
        if p90_duration is not None and p90_duration > 8.0:
            cadence_score -= min(0.16, (p90_duration - 8.0) * 0.08)
        if mean_silence is not None:
            if mean_silence > 11.5:
                cadence_score -= min(0.14, (mean_silence - 11.5) * 0.017)
            elif mean_silence < 3.5:
                cadence_score -= min(0.08, (3.5 - mean_silence) * 0.03)
        if p90_silence is not None and p90_silence > 17.0:
            cadence_score -= min(0.12, (p90_silence - 17.0) * 0.015)
        cadence_score = max(0.0, min(1.0, cadence_score))

        annunciation_score = 1.0
        if mean_hiss is not None and mean_hiss > 0.09:
            annunciation_score -= min(0.28, (mean_hiss - 0.09) * 2.4)
        if p90_hiss is not None and p90_hiss > 0.14:
            annunciation_score -= min(0.16, (p90_hiss - 0.14) * 1.5)
        if mean_harmonicity is not None and mean_harmonicity < 0.41:
            annunciation_score -= min(0.25, (0.41 - mean_harmonicity) * 2.8)
        if p10_harmonicity is not None and p10_harmonicity < 0.35:
            annunciation_score -= min(0.15, (0.35 - p10_harmonicity) * 2.0)
        if mean_clarity_delta is not None and mean_clarity_delta < 0.0:
            annunciation_score -= min(0.12, abs(mean_clarity_delta) * 0.32)
        if mean_actor_match_score is not None and mean_actor_match_score < 0.62:
            annunciation_score -= min(0.1, (0.62 - mean_actor_match_score) * 0.4)
        if mean_movie_match_score is not None and mean_movie_match_score < 0.6:
            annunciation_score -= min(0.1, (0.6 - mean_movie_match_score) * 0.45)
        if mean_silence is not None and mean_silence > 13.5:
            annunciation_score -= min(0.06, (mean_silence - 13.5) * 0.015)
        annunciation_score = max(0.0, min(1.0, annunciation_score))

        def _score_grade(score: float) -> str:
            if score >= 0.85:
                return "strong"
            if score >= 0.75:
                return "good"
            if score >= 0.62:
                return "marginal"
            return "weak"

        if quality_score >= 0.85:
            grade = "strong"
        elif quality_score >= 0.75:
            grade = "good"
        elif quality_score >= 0.62:
            grade = "marginal"
        else:
            grade = "weak"

        summary: dict[str, Any] = {
            "manifest_rows": len(rows),
            "metrics_rows": metric_rows,
            "coverage_ratio": round(float(metric_rows) / float(len(rows)), 4),
            "mean_hiss_ratio": (round(mean_hiss, 6) if mean_hiss is not None else None),
            "p90_hiss_ratio": (round(p90_hiss, 6) if p90_hiss is not None else None),
            "mean_silence_pct": (round(mean_silence, 6) if mean_silence is not None else None),
            "p90_silence_pct": (round(p90_silence, 6) if p90_silence is not None else None),
            "mean_harmonicity": (round(mean_harmonicity, 6) if mean_harmonicity is not None else None),
            "p10_harmonicity": (round(p10_harmonicity, 6) if p10_harmonicity is not None else None),
            "mean_clarity_delta": (round(mean_clarity_delta, 6) if mean_clarity_delta is not None else None),
            "mean_score_delta": (round(mean_score_delta, 6) if mean_score_delta is not None else None),
            "mean_actor_match_score": (
                round(mean_actor_match_score, 6)
                if mean_actor_match_score is not None
                else None
            ),
            "p10_actor_match_score": (
                round(p10_actor_match_score, 6)
                if p10_actor_match_score is not None
                else None
            ),
            "mean_movie_match_score": (
                round(mean_movie_match_score, 6)
                if mean_movie_match_score is not None
                else None
            ),
            "p10_movie_match_score": (
                round(p10_movie_match_score, 6)
                if p10_movie_match_score is not None
                else None
            ),
            "mean_duration_sec": (round(mean_duration, 6) if mean_duration is not None else None),
            "p10_duration_sec": (round(p10_duration, 6) if p10_duration is not None else None),
            "p90_duration_sec": (round(p90_duration, 6) if p90_duration is not None else None),
            "cadence_variation_cv": (
                round(cadence_variation_cv, 6)
                if cadence_variation_cv is not None
                else None
            ),
            "cadence_score": round(cadence_score, 4),
            "cadence_grade": _score_grade(cadence_score),
            "annunciation_score": round(annunciation_score, 4),
            "annunciation_grade": _score_grade(annunciation_score),
            "score": round(quality_score, 4),
            "grade": grade,
        }
        return summary, manifest_path

    @staticmethod
    def _quality_tier(*, clip_count: int | None, total_duration_sec: float | None) -> str:
        clips = int(clip_count or 0)
        duration = float(total_duration_sec or 0.0)
        if clips >= 20 and duration >= 120.0:
            return "production"
        if clips >= 8 and duration >= 45.0:
            return "development"
        if clips >= 1:
            return "seed"
        return "none"

    def _build_cache_key(self) -> tuple[Any, ...]:
        pointer_exists = self.pointer_path.exists()
        if not pointer_exists:
            return ("pointer_missing",)
        try:
            pointer_stat = self.pointer_path.stat()
        except OSError:
            return ("pointer_unreadable",)

        pointer = self._safe_read_json(self.pointer_path)
        active_pack = self._resolve_path((pointer or {}).get("active_pack")) if pointer else None
        if not active_pack or not active_pack.exists():
            return ("pointer_only", pointer_stat.st_mtime_ns, pointer_stat.st_size, str(active_pack or ""))

        metadata_path = active_pack / "metadata.json"
        try:
            metadata_stat = metadata_path.stat() if metadata_path.exists() else None
        except OSError:
            metadata_stat = None
        clips_dir = active_pack / "clips"
        try:
            clips_stat = clips_dir.stat() if clips_dir.exists() else None
        except OSError:
            clips_stat = None
        return (
            "pack",
            pointer_stat.st_mtime_ns,
            pointer_stat.st_size,
            str(active_pack),
            metadata_stat.st_mtime_ns if metadata_stat else None,
            metadata_stat.st_size if metadata_stat else None,
            clips_stat.st_mtime_ns if clips_stat else None,
        )

    def get_active_pack(self, *, refresh: bool = False) -> dict[str, Any]:
        cache_key = self._build_cache_key()
        if not refresh and self._cache_key == cache_key and self._cache_payload is not None:
            return copy.deepcopy(self._cache_payload)

        result: dict[str, Any] = {
            "active": False,
            "continuity_ready": False,
            "quality_tier": "none",
            "pointer_path": str(self.pointer_path),
            "pointer_exists": self.pointer_path.exists(),
            "pack_root": None,
            "pack_name": None,
            "pack_exists": False,
            "profile": None,
            "updated_at": None,
            "export_zip": None,
            "export_zip_exists": False,
            "metadata_path": None,
            "manifest_path": None,
            "playlist_path": None,
            "preview_reel_path": None,
            "clips_dir": None,
            "clip_count": None,
            "clip_file_count": 0,
            "total_duration_sec": None,
            "version": None,
            "created_at": None,
            "pack_id": None,
            "consistency": {
                "clip_count_matches_files": None,
                "duration_per_clip_sec": None,
            },
            "clip_quality": None,
            "blocking_issues": [],
            "warnings": [],
            "issues": [],
            "recommended_action": None,
        }
        blocking_issues: list[str] = result["blocking_issues"]
        warnings: list[str] = result["warnings"]
        if not self.pointer_path.exists():
            blocking_issues.append("pointer_missing")
            result["issues"] = list(blocking_issues) + list(warnings)
            result["recommended_action"] = "run_install_voice_pack"
            self._cache_key = cache_key
            self._cache_payload = copy.deepcopy(result)
            return result

        pointer = self._safe_read_json(self.pointer_path)
        if not pointer:
            blocking_issues.append("pointer_invalid_json")
            result["issues"] = list(blocking_issues) + list(warnings)
            result["recommended_action"] = "repair_pointer_json"
            self._cache_key = cache_key
            self._cache_payload = copy.deepcopy(result)
            return result

        result["profile"] = str(pointer.get("profile") or "").strip() or None
        result["updated_at"] = str(pointer.get("updated_at") or "").strip() or None
        export_zip = self._resolve_path(pointer.get("export_zip"))
        if export_zip:
            result["export_zip"] = str(export_zip)
            result["export_zip_exists"] = export_zip.exists()

        pack_root = self._resolve_path(pointer.get("active_pack"))
        if not pack_root:
            blocking_issues.append("active_pack_missing")
            result["issues"] = list(blocking_issues) + list(warnings)
            result["recommended_action"] = "set_active_pack_path"
            self._cache_key = cache_key
            self._cache_payload = copy.deepcopy(result)
            return result

        result["pack_root"] = str(pack_root)
        result["pack_name"] = pack_root.name
        result["pack_exists"] = pack_root.exists()
        result["metadata_path"] = str(pack_root / "metadata.json")
        result["manifest_path"] = str(pack_root / "manifest.csv")
        result["playlist_path"] = str(pack_root / "playlist.m3u")
        result["preview_reel_path"] = str(pack_root / "preview_reel.wav")
        result["clips_dir"] = str(pack_root / "clips")
        result["pack_id"] = self._make_pack_id(
            pack_root=str(pack_root),
            profile=result["profile"],
            updated_at=result["updated_at"],
        )

        if not pack_root.exists():
            blocking_issues.append("active_pack_not_found")
            result["issues"] = list(blocking_issues) + list(warnings)
            result["recommended_action"] = "reinstall_voice_pack"
            self._cache_key = cache_key
            self._cache_payload = copy.deepcopy(result)
            return result

        metadata_path = pack_root / "metadata.json"
        metadata = self._safe_read_json(metadata_path)
        if not metadata:
            warnings.append("metadata_invalid_or_missing")
        else:
            clip_count = metadata.get("clip_count")
            duration = metadata.get("total_duration_sec")
            try:
                result["clip_count"] = int(clip_count) if clip_count is not None else None
            except (TypeError, ValueError):
                result["clip_count"] = None
                warnings.append("metadata_clip_count_invalid")
            try:
                result["total_duration_sec"] = (
                    round(float(duration), 6) if duration is not None else None
                )
            except (TypeError, ValueError):
                result["total_duration_sec"] = None
                warnings.append("metadata_total_duration_invalid")
            result["version"] = metadata.get("version")
            result["created_at"] = (
                str(metadata.get("created_at") or "").strip() or None
            )

        clips_dir = pack_root / "clips"
        if not clips_dir.exists():
            blocking_issues.append("clips_dir_missing")
            result["clip_file_count"] = 0
        else:
            result["clip_file_count"] = self._audio_clip_count(clips_dir)
            if int(result["clip_file_count"]) <= 0:
                blocking_issues.append("clips_empty")

        if result["clip_count"] is None and int(result["clip_file_count"]) > 0:
            result["clip_count"] = int(result["clip_file_count"])
        if (
            result["clip_count"] is not None
            and int(result["clip_file_count"]) > 0
        ):
            result["consistency"]["clip_count_matches_files"] = (
                int(result["clip_count"]) == int(result["clip_file_count"])
            )
            if not bool(result["consistency"]["clip_count_matches_files"]):
                warnings.append("clip_count_mismatch")
        if (
            result["total_duration_sec"] is not None
            and result["clip_count"] is not None
            and int(result["clip_count"]) > 0
        ):
            result["consistency"]["duration_per_clip_sec"] = round(
                float(result["total_duration_sec"]) / float(result["clip_count"]),
                6,
            )

        clip_quality, resolved_manifest = self._load_clip_quality_summary(pack_root)
        if resolved_manifest is not None:
            result["manifest_path"] = str(resolved_manifest)
        result["clip_quality"] = clip_quality
        if isinstance(clip_quality, dict):
            score = self._parse_float(clip_quality.get("score"))
            coverage = self._parse_float(clip_quality.get("coverage_ratio"))
            if coverage is not None and coverage < 0.6:
                warnings.append("clarity_metrics_coverage_low")
            if score is not None:
                if score < 0.62:
                    warnings.append("clarity_quality_low")
                elif score < 0.75:
                    warnings.append("clarity_quality_marginal")

        quality_tier = self._quality_tier(
            clip_count=(int(result["clip_count"]) if result["clip_count"] is not None else None),
            total_duration_sec=(
                float(result["total_duration_sec"])
                if result["total_duration_sec"] is not None
                else None
            ),
        )
        result["quality_tier"] = quality_tier
        continuity_ready = bool(
            not blocking_issues
            and int(result["clip_file_count"]) >= 5
            and quality_tier in {"development", "production"}
        )
        result["continuity_ready"] = continuity_ready
        result["active"] = bool(not blocking_issues)
        if result["active"] and not continuity_ready:
            warnings.append("continuity_coverage_low")
        if not result["active"]:
            result["recommended_action"] = "repair_voice_pack_install"

        result["issues"] = list(blocking_issues) + list(warnings)
        self._cache_key = cache_key
        self._cache_payload = copy.deepcopy(result)
        return result
