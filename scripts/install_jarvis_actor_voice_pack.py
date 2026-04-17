#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
import shutil
import wave
from array import array
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


@dataclass
class SelectedClip:
    clip_filename: str
    source: str
    start_sec: float
    selected: str
    reason: str
    score_delta: float
    clarity_delta: float
    silence_pct: float
    hiss_ratio: float
    harmonicity: float
    actor_match_score: float | None = None
    movie_match_score: float | None = None


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    default_source_dir = (
        root
        / "analysis"
        / "jarvis_study"
        / "clean_training_clips_deep_strict_filled_aggressive_clarity_strict_hybrid_finetuned_polished"
    )
    default_selection_csv = default_source_dir / "polish_selection.csv"
    default_pack_root = root / ".jarvis" / "voice" / "training_assets" / "jarvis_actor_isolated_v1"
    default_exports_dir = root / "exports"

    parser = argparse.ArgumentParser(
        description="Install isolated JARVIS actor clips into workspace voice training assets.",
    )
    parser.add_argument("--source-dir", type=Path, default=default_source_dir)
    parser.add_argument("--selection-csv", type=Path, default=default_selection_csv)
    parser.add_argument("--pack-root", type=Path, default=default_pack_root)
    parser.add_argument("--exports-dir", type=Path, default=default_exports_dir)
    parser.add_argument(
        "--profile",
        choices=["strict", "extended", "actor_match"],
        default="strict",
        help="strict = cleaner subset for low-noise cloning; extended = larger coverage set.",
    )
    parser.add_argument(
        "--preview-count",
        type=int,
        default=8,
        help="How many clips to stitch into preview_reel.wav",
    )
    return parser.parse_args()


def load_rows(selection_csv: Path) -> list[dict[str, str]]:
    if not selection_csv.exists():
        raise FileNotFoundError(f"Selection CSV not found: {selection_csv}")
    with selection_csv.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def strict_accept(row: dict[str, str]) -> bool:
    selected = row["selected"]
    reason = row["reason"]

    if selected == "polished":
        hiss = float(row["polish_hiss_ratio"])
        silence = float(row["polish_silence_pct"])
        harmonicity = float(row["polish_harmonicity"])
        return hiss <= 0.11 and silence <= 11.5 and harmonicity >= 0.39

    if selected == "input" and reason == "kept_input_not_better":
        hiss = float(row["orig_hiss_ratio"])
        silence = float(row["orig_silence_pct"])
        harmonicity = float(row["orig_harmonicity"])
        return hiss <= 0.09 and silence <= 8.5 and harmonicity >= 0.40

    return False


def extended_accept(row: dict[str, str]) -> bool:
    selected = row["selected"]
    if selected == "polished":
        return True
    # Keep only safer retained inputs for extended profile.
    return row["reason"] == "kept_input_not_better"


def actor_match_accept(row: dict[str, str]) -> bool:
    selected = str(row.get("selected") or "").strip().lower()
    reason = str(row.get("reason") or "").strip().lower()
    if selected not in {"polished", "input", "master"}:
        return False
    if "rejected" in reason or "discard" in reason:
        return False

    if selected == "polished":
        hiss = float(row.get("polish_hiss_ratio") or 0.0)
        silence = float(row.get("polish_silence_pct") or 0.0)
        harmonicity = float(row.get("polish_harmonicity") or 0.0)
    elif selected == "master":
        hiss = float(row.get("master_hiss_ratio") or row.get("hiss_ratio") or 0.0)
        silence = float(row.get("master_silence_pct") or row.get("silence_pct") or 0.0)
        harmonicity = float(row.get("master_harmonicity") or row.get("harmonicity") or 0.0)
    else:
        hiss = float(row.get("orig_hiss_ratio") or row.get("hiss_ratio") or 0.0)
        silence = float(row.get("orig_silence_pct") or row.get("silence_pct") or 0.0)
        harmonicity = float(row.get("orig_harmonicity") or row.get("harmonicity") or 0.0)
    return hiss <= 0.13 and silence <= 14.5 and harmonicity >= 0.36


def _robust_mad(values: list[float], fallback: float) -> float:
    if not values:
        return fallback
    med = float(statistics.median(values))
    mad = float(statistics.median([abs(v - med) for v in values]))
    return max(mad, fallback)


def _infer_source_from_path(path: str) -> str:
    raw = str(path or "").strip().lower()
    if "jarvis_ii" in raw or "jarvis ii" in raw:
        return "JARVIS_II"
    return "JARVIS_1"


def _load_movie_reference_index() -> dict[str, list[tuple[float, float, float]]]:
    root = Path(__file__).resolve().parents[1]
    study_metrics = root / "analysis" / "jarvis_study" / "voice_study_metrics.json"
    out: dict[str, list[tuple[float, float, float]]] = {
        "JARVIS_1:top": [],
        "JARVIS_1:bottom": [],
        "JARVIS_II:top": [],
        "JARVIS_II:bottom": [],
    }
    if not study_metrics.exists():
        return out
    try:
        payload = json.loads(study_metrics.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    if not isinstance(payload, list):
        return out
    for item in payload:
        if not isinstance(item, dict):
            continue
        source = _infer_source_from_path(str(item.get("path") or ""))
        for key in ("top_windows", "bottom_windows"):
            windows = item.get(key)
            if not isinstance(windows, list):
                continue
            for window in windows:
                if not isinstance(window, dict):
                    continue
                try:
                    start = float(window.get("start_sec"))
                    end = float(window.get("end_sec"))
                    score = float(window.get("score"))
                except (TypeError, ValueError):
                    continue
                bucket = f"{source}:{'top' if key == 'top_windows' else 'bottom'}"
                out.setdefault(bucket, []).append((start, end, score))
    return out


def _window_alignment_score(
    *,
    source: str,
    start_sec: float,
    index: dict[str, list[tuple[float, float, float]]],
) -> float:
    src = str(source or "").strip().upper() or "JARVIS_1"
    top_windows = index.get(f"{src}:top") or []
    bottom_windows = index.get(f"{src}:bottom") or []

    top_boost = 0.0
    for start, end, score in top_windows:
        center = (start + end) / 2.0
        half = max(0.25, (end - start) / 2.0)
        distance = abs(float(start_sec) - center)
        normalized = max(0.0, 1.0 - (distance / (half + 2.0)))
        strength = min(1.0, max(0.0, (score - 70.0) / 30.0))
        top_boost = max(top_boost, normalized * strength)

    bottom_penalty = 0.0
    for start, end, score in bottom_windows:
        center = (start + end) / 2.0
        half = max(0.25, (end - start) / 2.0)
        distance = abs(float(start_sec) - center)
        normalized = max(0.0, 1.0 - (distance / (half + 2.0)))
        weakness = min(1.0, max(0.0, (100.0 - score) / 100.0))
        bottom_penalty = max(bottom_penalty, normalized * weakness)

    return (0.24 * top_boost) - (0.27 * bottom_penalty)


def _compute_actor_match_scores(clips: list[SelectedClip]) -> list[SelectedClip]:
    if not clips:
        return []
    hiss = [c.hiss_ratio for c in clips]
    silence = [c.silence_pct for c in clips]
    harmonicity = [c.harmonicity for c in clips]
    clarity_delta = [c.clarity_delta for c in clips]
    score_delta = [c.score_delta for c in clips]

    med_hiss = float(statistics.median(hiss))
    med_silence = float(statistics.median(silence))
    med_harm = float(statistics.median(harmonicity))
    med_clarity_delta = float(statistics.median(clarity_delta))
    med_score_delta = float(statistics.median(score_delta))

    mad_hiss = _robust_mad(hiss, 0.01)
    mad_silence = _robust_mad(silence, 0.9)
    mad_harm = _robust_mad(harmonicity, 0.02)
    mad_clarity = _robust_mad(clarity_delta, 0.12)
    mad_score = _robust_mad(score_delta, 0.12)

    movie_reference_index = _load_movie_reference_index()
    interim: list[tuple[SelectedClip, float, float]] = []
    for clip in clips:
        z_hiss = abs(clip.hiss_ratio - med_hiss) / mad_hiss
        z_silence = abs(clip.silence_pct - med_silence) / mad_silence
        z_harm = abs(clip.harmonicity - med_harm) / mad_harm
        z_clarity = max(0.0, (med_clarity_delta - clip.clarity_delta) / mad_clarity)
        z_score = max(0.0, (med_score_delta - clip.score_delta) / mad_score)
        penalty = (
            (0.36 * min(z_hiss, 4.0))
            + (0.22 * min(z_silence, 4.0))
            + (0.34 * min(z_harm, 4.0))
            + (0.18 * min(z_clarity, 3.0))
            + (0.18 * min(z_score, 3.0))
        )
        actor_match_score_raw = 1.0 / (1.0 + penalty)
        movie_score_raw = float(actor_match_score_raw)
        movie_score_raw += _window_alignment_score(
            source=clip.source,
            start_sec=clip.start_sec,
            index=movie_reference_index,
        )
        if str(clip.source or "").strip().upper() == "JARVIS_1":
            movie_score_raw += 0.06
        else:
            movie_score_raw += 0.02
        reason_norm = str(clip.reason or "").strip().lower()
        if "silence_up" in reason_norm:
            movie_score_raw -= 0.07
        if "harm_down" in reason_norm:
            movie_score_raw -= 0.06
        if "hiss_up" in reason_norm:
            movie_score_raw -= 0.06
        if "not_better" in reason_norm:
            movie_score_raw -= 0.025
        movie_score_raw = max(0.001, movie_score_raw)
        interim.append((clip, float(actor_match_score_raw), float(movie_score_raw)))

    actor_raw_values = [item[1] for item in interim]
    actor_raw_min = min(actor_raw_values)
    actor_raw_max = max(actor_raw_values)
    actor_span = actor_raw_max - actor_raw_min
    movie_raw_values = [item[2] for item in interim]
    movie_raw_min = min(movie_raw_values)
    movie_raw_max = max(movie_raw_values)
    movie_span = movie_raw_max - movie_raw_min
    scored: list[SelectedClip] = []
    for clip, actor_raw_score, movie_raw_score in interim:
        if actor_span <= 1e-9:
            actor_match_score = 1.0
        else:
            normalized = (actor_raw_score - actor_raw_min) / actor_span
            actor_match_score = 0.35 + (0.65 * normalized)
        if movie_span <= 1e-9:
            movie_match_score = 1.0
        else:
            movie_normalized = (movie_raw_score - movie_raw_min) / movie_span
            movie_match_score = 0.35 + (0.65 * movie_normalized)
        scored.append(
            SelectedClip(
                clip_filename=clip.clip_filename,
                source=clip.source,
                start_sec=clip.start_sec,
                selected=clip.selected,
                reason=clip.reason,
                score_delta=clip.score_delta,
                clarity_delta=clip.clarity_delta,
                silence_pct=clip.silence_pct,
                hiss_ratio=clip.hiss_ratio,
                harmonicity=clip.harmonicity,
                actor_match_score=round(float(actor_match_score), 6),
                movie_match_score=round(float(movie_match_score), 6),
            )
        )
    return scored


def _refine_actor_match_selection(clips: list[SelectedClip]) -> list[SelectedClip]:
    scored = _compute_actor_match_scores(clips)
    if len(scored) <= 24:
        return sorted(scored, key=lambda c: (c.source, c.start_sec))

    ranked = sorted(
        scored,
        key=lambda c: (
            (0.62 * float(c.movie_match_score or 0.0))
            + (0.38 * float(c.actor_match_score or 0.0)),
            float(c.movie_match_score or 0.0),
            c.harmonicity,
            -c.hiss_ratio,
        ),
        reverse=True,
    )
    keep_count = max(24, int(round(len(ranked) * 0.8)))
    keep = ranked[:keep_count]

    # Preserve source diversity so we do not collapse continuity phrasing coverage.
    all_sources = sorted({c.source for c in ranked})
    kept_sources = {c.source for c in keep}
    if kept_sources != set(all_sources):
        drop_pool = sorted(
            keep,
            key=lambda c: (
                (0.62 * float(c.movie_match_score or 0.0))
                + (0.38 * float(c.actor_match_score or 0.0))
            ),
        )
        for source in all_sources:
            if source in kept_sources:
                continue
            source_best = next((c for c in ranked if c.source == source), None)
            if source_best is None:
                continue
            if drop_pool:
                drop_candidate = drop_pool.pop(0)
                keep = [c for c in keep if c.clip_filename != drop_candidate.clip_filename]
            keep.append(source_best)
            kept_sources.add(source)

    keep = sorted(keep, key=lambda c: (c.source, c.start_sec))
    return keep


def materialize_selection(rows: list[dict[str, str]], profile: str) -> list[SelectedClip]:
    out: list[SelectedClip] = []
    for row in rows:
        if profile == "strict":
            keep = strict_accept(row)
        elif profile == "extended":
            keep = extended_accept(row)
        else:
            keep = actor_match_accept(row)
        if not keep:
            continue

        if row["selected"] == "polished":
            silence_pct = float(row["polish_silence_pct"])
            hiss_ratio = float(row["polish_hiss_ratio"])
            harmonicity = float(row["polish_harmonicity"])
        else:
            silence_pct = float(row["orig_silence_pct"])
            hiss_ratio = float(row["orig_hiss_ratio"])
            harmonicity = float(row["orig_harmonicity"])

        out.append(
            SelectedClip(
                clip_filename=row["clip_filename"],
                source=row["source"],
                start_sec=float(row["start_sec"]),
                selected=row["selected"],
                reason=row["reason"],
                score_delta=float(row["delta_score"]),
                clarity_delta=float(row["delta_clarity"]),
                silence_pct=silence_pct,
                hiss_ratio=hiss_ratio,
                harmonicity=harmonicity,
            )
        )

    out.sort(key=lambda c: (c.source, c.start_sec))
    if profile == "actor_match":
        return _refine_actor_match_selection(out)
    return out


def wipe_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        frames = wf.getnframes()
        sr = wf.getframerate()
        return float(frames / sr if sr > 0 else 0.0)


def stitch_preview(clips: list[Path], out_path: Path, count: int, gap_ms: int = 120) -> None:
    if not clips:
        return

    chosen = clips[: max(1, count)]
    first = chosen[0]
    with wave.open(str(first), "rb") as wf:
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
    if ch != 1 or sw != 2:
        # Unexpected format; skip preview reel.
        return

    gap_frames = int(sr * (gap_ms / 1000.0))
    gap = array("h", [0] * gap_frames)
    merged = array("h")
    for i, clip in enumerate(chosen):
        with wave.open(str(clip), "rb") as wf:
            c = wf.getnchannels()
            s = wf.getsampwidth()
            r = wf.getframerate()
            if c != 1 or s != 2 or r != sr:
                continue
            data = array("h")
            data.frombytes(wf.readframes(wf.getnframes()))
            merged.extend(data)
        if i < len(chosen) - 1:
            merged.extend(gap)

    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(merged.tobytes())


def write_csv_manifest(path: Path, clips: list[SelectedClip], durations: dict[str, float]) -> None:
    fields = [
        "clip_filename",
        "source",
        "start_sec",
        "duration_sec",
        "selected",
        "reason",
        "score_delta",
        "clarity_delta",
        "silence_pct",
        "hiss_ratio",
        "harmonicity",
        "actor_match_score",
        "movie_match_score",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for c in clips:
            writer.writerow(
                {
                    "clip_filename": c.clip_filename,
                    "source": c.source,
                    "start_sec": f"{c.start_sec:.3f}",
                    "duration_sec": f"{durations[c.clip_filename]:.6f}",
                    "selected": c.selected,
                    "reason": c.reason,
                    "score_delta": f"{c.score_delta:.6f}",
                    "clarity_delta": f"{c.clarity_delta:.6f}",
                    "silence_pct": f"{c.silence_pct:.6f}",
                    "hiss_ratio": f"{c.hiss_ratio:.6f}",
                    "harmonicity": f"{c.harmonicity:.6f}",
                    "actor_match_score": (
                        f"{float(c.actor_match_score):.6f}"
                        if c.actor_match_score is not None
                        else ""
                    ),
                    "movie_match_score": (
                        f"{float(c.movie_match_score):.6f}"
                        if c.movie_match_score is not None
                        else ""
                    ),
                }
            )


def write_playlist(path: Path, clips: list[SelectedClip]) -> None:
    lines = ["#EXTM3U"]
    for c in clips:
        line = (
            f"#EXTINF:-1,{c.source} start={c.start_sec:.3f}s "
            f"sel={c.selected} hiss={c.hiss_ratio:.4f} sil={c.silence_pct:.2f}%"
        )
        if c.actor_match_score is not None:
            line = f"{line} match={float(c.actor_match_score):.3f}"
        if c.movie_match_score is not None:
            line = f"{line} movie={float(c.movie_match_score):.3f}"
        lines.append(line)
        lines.append(f"clips/{c.clip_filename}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_readme(path: Path, profile: str, clips: list[SelectedClip], total_sec: float) -> None:
    minutes = total_sec / 60.0
    actor_scores = [float(c.actor_match_score) for c in clips if c.actor_match_score is not None]
    movie_scores = [float(c.movie_match_score) for c in clips if c.movie_match_score is not None]
    actor_score_line = (
        f"- Mean actor match score: {statistics.fmean(actor_scores):.3f}"
        if actor_scores
        else "- Mean actor match score: n/a"
    )
    movie_score_line = (
        f"- Mean movie match score: {statistics.fmean(movie_scores):.3f}"
        if movie_scores
        else "- Mean movie match score: n/a"
    )
    path.write_text(
        "\n".join(
            [
                "# JARVIS Actor Isolated Voice Pack",
                "",
                "Curated from purchased/consented actor remodulation clips and processed for lower noise/music bleed.",
                "",
                "## Profile",
                "",
                f"- Mode: `{profile}`",
                "- `strict`: cleaner subset (preferred for cloning/training input).",
                "- `extended`: larger subset with broader phrase coverage.",
                "- `actor_match`: high-coverage actor-likeness subset with relaxed but bounded clarity guards.",
                "",
                "## Stats",
                "",
                f"- Clips: {len(clips)}",
                f"- Total duration: {total_sec:.2f} sec ({minutes:.2f} min)",
                actor_score_line,
                movie_score_line,
                "",
                "## Files",
                "",
                "- `clips/*.wav`",
                "- `manifest.csv`",
                "- `playlist.m3u`",
                "- `preview_reel.wav`",
                "- `metadata.json`",
                "",
                "## Notes",
                "",
                "- All clips are mono 48kHz 16-bit WAV.",
                "- This pack is intended for private local JARVIS development/research workflow.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def write_metadata(path: Path, profile: str, clips: list[SelectedClip], total_sec: float, source_dir: Path) -> None:
    actor_scores = [float(c.actor_match_score) for c in clips if c.actor_match_score is not None]
    movie_scores = [float(c.movie_match_score) for c in clips if c.movie_match_score is not None]
    payload = {
        "version": 1,
        "created_at": datetime.now().isoformat(),
        "profile": profile,
        "clip_count": len(clips),
        "total_duration_sec": round(total_sec, 6),
        "actor_match_score_mean": (round(statistics.fmean(actor_scores), 6) if actor_scores else None),
        "actor_match_score_min": (round(min(actor_scores), 6) if actor_scores else None),
        "movie_match_score_mean": (round(statistics.fmean(movie_scores), 6) if movie_scores else None),
        "movie_match_score_min": (round(min(movie_scores), 6) if movie_scores else None),
        "source_dir": str(source_dir),
        "selection_rules": {
            "strict": {
                "polished": "hiss<=0.11, silence<=11.5, harmonicity>=0.39",
                "retained_input": "reason=kept_input_not_better, hiss<=0.09, silence<=8.5, harmonicity>=0.40",
            },
            "extended": {
                "polished": "include all polished selections",
                "retained_input": "include only kept_input_not_better",
            },
            "actor_match": {
                "selected": "polished|input|master",
                "bounds": "hiss<=0.13, silence<=14.5, harmonicity>=0.36",
                "exclude_reasons": "contains rejected/discard",
                "outlier_trim": "keep top ~80% by robust actor-match score, min 24 clips, preserve source diversity",
                "movie_reference": "boost clip windows aligned with top_windows and avoid bottom_windows from analysis/jarvis_study/voice_study_metrics.json",
            },
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_active_pointer(path: Path, pack_root: Path, export_zip: Path, profile: str) -> None:
    payload = {
        "active_pack": str(pack_root),
        "export_zip": str(export_zip),
        "profile": profile,
        "updated_at": datetime.now().isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def zip_pack(pack_root: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as zf:
        for file in sorted(pack_root.rglob("*")):
            if file.is_file():
                zf.write(file, arcname=str(file.relative_to(pack_root)))


def main() -> None:
    args = parse_args()

    source_dir = args.source_dir.resolve()
    selection_csv = args.selection_csv.resolve()
    pack_root = args.pack_root.resolve()
    exports_dir = args.exports_dir.resolve()
    profile = args.profile

    if not source_dir.exists():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    rows = load_rows(selection_csv)
    selected = materialize_selection(rows, profile=profile)
    if not selected:
        raise RuntimeError("No clips selected. Try --profile extended or check source files.")

    clips_dir = pack_root / "clips"
    wipe_dir(pack_root)
    clips_dir.mkdir(parents=True, exist_ok=True)

    durations: dict[str, float] = {}
    copied: list[Path] = []
    for clip in selected:
        src = source_dir / clip.clip_filename
        if not src.exists():
            continue
        dst = clips_dir / clip.clip_filename
        shutil.copy2(src, dst)
        copied.append(dst)
        durations[clip.clip_filename] = wav_duration_seconds(dst)

    selected = [c for c in selected if c.clip_filename in durations]
    if not selected:
        raise RuntimeError("Selected clip files were not found in source directory.")

    total_sec = sum(durations.values())

    write_csv_manifest(pack_root / "manifest.csv", selected, durations)
    write_playlist(pack_root / "playlist.m3u", selected)
    write_metadata(pack_root / "metadata.json", profile, selected, total_sec, source_dir)
    write_readme(pack_root / "README.md", profile, selected, total_sec)
    stitch_preview(copied, pack_root / "preview_reel.wav", count=args.preview_count)

    today = datetime.now().strftime("%Y-%m-%d")
    zip_path = exports_dir / f"JARVIS_ACTOR_ISOLATED_VOICE_PACK_{profile.upper()}_{today}.zip"
    zip_pack(pack_root, zip_path)

    pointer = pack_root.parents[1] / "ACTIVE_VOICE_PACK.json"
    write_active_pointer(pointer, pack_root, zip_path, profile)

    print(f"[ok] installed pack: {pack_root}")
    print(f"[ok] clips: {len(selected)}")
    print(f"[ok] total duration sec: {total_sec:.3f}")
    print(f"[ok] export zip: {zip_path}")
    print(f"[ok] active pointer: {pointer}")


if __name__ == "__main__":
    main()
