#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

DB_EPS = 1e-12


@dataclass
class WindowScore:
    source: str
    start_sec: float
    end_sec: float
    duration_sec: float
    score: float
    rms_dbfs: float
    silence_pct: float
    hf_to_body_ratio: float
    low_mid_ratio: float


@dataclass
class SourceConfig:
    name: str
    path: Path
    score_min: float
    silence_max: float
    hf_ratio_max: float
    max_count: int
    min_count: int
    avoid_first_n_sec: float


@dataclass
class ExportedClip:
    clip_filename: str
    source: str
    source_audio: str
    start_sec: float
    end_sec: float
    duration_sec: float
    selection_pass: int
    score: float
    raw_rms_dbfs: float
    raw_silence_pct: float
    hf_to_body_ratio: float
    low_mid_ratio: float
    gain_db: float
    post_rms_dbfs: float
    post_peak_dbfs: float


def to_db(v: float | np.ndarray) -> float | np.ndarray:
    return 20.0 * np.log10(np.maximum(v, DB_EPS))


def read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sample_rate = wf.getframerate()
        sample_width = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sample_width != 2:
        raise ValueError(f"Expected 16-bit WAV: {path}")

    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, sample_rate


def write_wav_mono(path: Path, mono: np.ndarray, sample_rate: int) -> None:
    clipped = np.clip(mono, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def frame_rms_db(x: np.ndarray, sr: int, frame_sec: float = 0.05, hop_sec: float = 0.025) -> np.ndarray:
    frame = max(1, int(sr * frame_sec))
    hop = max(1, int(sr * hop_sec))
    if len(x) < frame:
        rms = math.sqrt(float(np.mean(x * x)))
        return np.array([float(to_db(rms))], dtype=np.float64)

    out: list[float] = []
    for i in range(0, len(x) - frame + 1, hop):
        chunk = x[i : i + frame]
        rms = math.sqrt(float(np.mean(chunk * chunk)))
        out.append(float(to_db(rms)))
    return np.array(out, dtype=np.float64)


def score_windows(
    mono: np.ndarray,
    sr: int,
    source_name: str,
    win_sec: float,
    hop_sec: float,
) -> list[WindowScore]:
    win = int(sr * win_sec)
    hop = int(sr * hop_sec)
    n_fft = 4096
    local_hop = n_fft // 2
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    window = np.hanning(n_fft).astype(np.float32)

    def band_ratio(spec: np.ndarray, lo: float, hi: float) -> float:
        m = (freqs >= lo) & (freqs < hi)
        return float(np.sum(spec[m]) / (np.sum(spec) + DB_EPS))

    scored: list[WindowScore] = []
    for start in range(0, len(mono) - win + 1, hop):
        segment = mono[start : start + win]
        rms = math.sqrt(float(np.mean(segment * segment)))
        rms_db = float(to_db(rms))

        seg_rms_db = frame_rms_db(segment, sr)
        silence_pct = float(np.mean(seg_rms_db < -45.0) * 100.0)

        mags = []
        for i in range(0, len(segment) - n_fft + 1, local_hop):
            frame = segment[i : i + n_fft]
            frame_rms = math.sqrt(float(np.mean(frame * frame)))
            if to_db(frame_rms) < -45.0:
                continue
            mags.append(np.abs(np.fft.rfft(frame * window)))

        if not mags:
            continue

        spec = np.mean(np.vstack(mags), axis=0)
        low_mid = band_ratio(spec, 80.0, 1000.0)
        body = band_ratio(spec, 80.0, 4000.0)
        high = band_ratio(spec, 4000.0, 16000.0)
        hf_ratio = float(high / max(body, DB_EPS))

        # Composite quality score tuned for voice model training prep.
        score = 100.0
        if rms_db < -30.0:
            score -= (abs(rms_db + 30.0) * 3.0)
        if rms_db > -14.0:
            score -= (abs(rms_db + 14.0) * 2.5)
        if silence_pct > 15.0:
            score -= (silence_pct - 15.0) * 1.2
        if hf_ratio > 0.55:
            score -= (hf_ratio - 0.55) * 120.0
        if low_mid < 0.30:
            score -= (0.30 - low_mid) * 120.0

        scored.append(
            WindowScore(
                source=source_name,
                start_sec=float(start / sr),
                end_sec=float((start + win) / sr),
                duration_sec=win_sec,
                score=float(score),
                rms_dbfs=rms_db,
                silence_pct=silence_pct,
                hf_to_body_ratio=hf_ratio,
                low_mid_ratio=float(low_mid),
            )
        )

    return scored


def overlap_or_too_close(a: WindowScore, b: WindowScore, min_gap_sec: float) -> bool:
    return not (a.end_sec + min_gap_sec <= b.start_sec or a.start_sec >= b.end_sec + min_gap_sec)


def relaxation_passes(cfg: SourceConfig) -> list[tuple[float, float, float]]:
    return [
        (cfg.score_min, cfg.silence_max, cfg.hf_ratio_max),
        (cfg.score_min - 2.0, cfg.silence_max + 1.5, cfg.hf_ratio_max + 0.03),
        (cfg.score_min - 4.0, cfg.silence_max + 3.0, cfg.hf_ratio_max + 0.06),
        (cfg.score_min - 7.0, cfg.silence_max + 4.5, cfg.hf_ratio_max + 0.10),
    ]


def select_windows_with_relaxation(
    windows: list[WindowScore],
    cfg: SourceConfig,
    min_gap_sec: float,
) -> tuple[list[tuple[WindowScore, int]], dict[str, float]]:
    selected: list[tuple[WindowScore, int]] = []
    used_keys: set[tuple[float, float]] = set()

    passes = relaxation_passes(cfg)
    for pass_idx, (score_min, silence_max, hf_ratio_max) in enumerate(passes, start=1):
        candidates = [
            w
            for w in windows
            if w.start_sec >= cfg.avoid_first_n_sec
            and w.score >= score_min
            and w.silence_pct <= silence_max
            and w.hf_to_body_ratio <= hf_ratio_max
            and (w.start_sec, w.end_sec) not in used_keys
        ]

        candidates.sort(key=lambda w: (w.score, -w.hf_to_body_ratio, -w.low_mid_ratio), reverse=True)

        for w in candidates:
            if any(overlap_or_too_close(w, s[0], min_gap_sec) for s in selected):
                continue
            selected.append((w, pass_idx))
            used_keys.add((w.start_sec, w.end_sec))
            if len(selected) >= cfg.max_count:
                break

        if len(selected) >= cfg.max_count:
            break

    selected.sort(key=lambda x: x[0].start_sec)

    diag = {
        "target_max": float(cfg.max_count),
        "target_min": float(cfg.min_count),
        "selected": float(len(selected)),
        "met_min_target": float(len(selected) >= cfg.min_count),
    }
    return selected, diag


def apply_fade_in_out(seg: np.ndarray, sr: int, fade_ms: float) -> np.ndarray:
    n = int(sr * (fade_ms / 1000.0))
    if n <= 1 or len(seg) < 2 * n:
        return seg

    out = seg.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    out[:n] *= ramp
    out[-n:] *= ramp[::-1]
    return out


def normalize_segment(
    seg: np.ndarray,
    target_rms_dbfs: float,
    peak_ceiling_dbfs: float,
) -> tuple[np.ndarray, float, float, float]:
    in_rms = math.sqrt(float(np.mean(seg * seg)))
    if in_rms < DB_EPS:
        return seg, 0.0, float(to_db(DB_EPS)), float(to_db(np.max(np.abs(seg)) + DB_EPS))

    target_rms = 10 ** (target_rms_dbfs / 20.0)
    gain = target_rms / in_rms
    out = seg * gain

    peak = float(np.max(np.abs(out)))
    peak_ceiling = 10 ** (peak_ceiling_dbfs / 20.0)
    if peak > peak_ceiling:
        out = out * (peak_ceiling / peak)

    out_rms = math.sqrt(float(np.mean(out * out)))
    out_peak = float(np.max(np.abs(out)))
    gain_db = float(to_db(math.sqrt(float(np.mean(out * out))) / max(in_rms, DB_EPS)))
    return out, gain_db, float(to_db(out_rms)), float(to_db(out_peak + DB_EPS))


def clear_previous_outputs(out_dir: Path) -> None:
    if not out_dir.exists():
        return

    for p in out_dir.glob("*.wav"):
        p.unlink()
    for name in ["clip_manifest.csv", "README.md", "selection_diagnostics.json"]:
        f = out_dir / name
        if f.exists():
            f.unlink()


def build_default_sources(study_dir: Path) -> list[SourceConfig]:
    return [
        SourceConfig(
            name="JARVIS_1",
            path=study_dir / "JARVIS_1.wav",
            score_min=99.0,
            silence_max=10.0,
            hf_ratio_max=0.58,
            max_count=60,
            min_count=40,
            avoid_first_n_sec=0.0,
        ),
        SourceConfig(
            name="JARVIS_II",
            path=study_dir / "JARVIS_II.wav",
            score_min=96.0,
            silence_max=9.0,
            hf_ratio_max=0.58,
            max_count=2,
            min_count=1,
            avoid_first_n_sec=3.0,
        ),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select and export clean voice training clips.")
    parser.add_argument(
        "--study-dir",
        type=Path,
        default=Path("/Users/dankerbadge/Documents/J.A.R.V.I.S/analysis/jarvis_study"),
    )
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--window-sec", type=float, default=6.0)
    parser.add_argument("--hop-sec", type=float, default=3.0)
    parser.add_argument("--min-gap-sec", type=float, default=0.12)
    parser.add_argument("--target-rms", type=float, default=-23.0)
    parser.add_argument("--peak-ceiling", type=float, default=-1.0)
    parser.add_argument("--fade-ms", type=float, default=12.0)
    parser.add_argument("--keep-existing", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    study_dir = args.study_dir
    out_dir = args.out_dir or (study_dir / "clean_training_clips")
    out_dir.mkdir(parents=True, exist_ok=True)

    if not args.keep_existing:
        clear_previous_outputs(out_dir)

    sources = build_default_sources(study_dir)

    all_exports: list[ExportedClip] = []
    diagnostics: dict[str, object] = {
        "window_sec": args.window_sec,
        "hop_sec": args.hop_sec,
        "min_gap_sec": args.min_gap_sec,
        "target_rms_dbfs": args.target_rms,
        "peak_ceiling_dbfs": args.peak_ceiling,
        "fade_ms": args.fade_ms,
        "sources": {},
    }

    for src in sources:
        mono, sr = read_wav_mono(src.path)
        windows = score_windows(
            mono=mono,
            sr=sr,
            source_name=src.name,
            win_sec=args.window_sec,
            hop_sec=args.hop_sec,
        )

        selected, diag = select_windows_with_relaxation(
            windows=windows,
            cfg=src,
            min_gap_sec=args.min_gap_sec,
        )

        diagnostics["sources"][src.name] = {
            "config": {
                "path": str(src.path),
                "score_min": src.score_min,
                "silence_max": src.silence_max,
                "hf_ratio_max": src.hf_ratio_max,
                "max_count": src.max_count,
                "min_count": src.min_count,
                "avoid_first_n_sec": src.avoid_first_n_sec,
            },
            "window_count": len(windows),
            "selection": diag,
            "relaxation_passes": [
                {
                    "pass": i + 1,
                    "score_min": p[0],
                    "silence_max": p[1],
                    "hf_ratio_max": p[2],
                }
                for i, p in enumerate(relaxation_passes(src))
            ],
        }

        for idx, (w, pass_idx) in enumerate(selected, start=1):
            start_i = int(round(w.start_sec * sr))
            end_i = int(round(w.end_sec * sr))
            seg = mono[start_i:end_i]

            seg, gain_db, post_rms_dbfs, post_peak_dbfs = normalize_segment(
                seg=seg,
                target_rms_dbfs=args.target_rms,
                peak_ceiling_dbfs=args.peak_ceiling,
            )
            seg = apply_fade_in_out(seg, sr=sr, fade_ms=args.fade_ms)

            out_name = (
                f"{src.name.lower()}_{idx:03d}_"
                f"{int(w.start_sec * 1000):07d}_{int(w.end_sec * 1000):07d}.wav"
            )
            out_path = out_dir / out_name
            write_wav_mono(out_path, seg, sr)

            all_exports.append(
                ExportedClip(
                    clip_filename=out_name,
                    source=src.name,
                    source_audio=str(src.path),
                    start_sec=w.start_sec,
                    end_sec=w.end_sec,
                    duration_sec=w.duration_sec,
                    selection_pass=pass_idx,
                    score=w.score,
                    raw_rms_dbfs=w.rms_dbfs,
                    raw_silence_pct=w.silence_pct,
                    hf_to_body_ratio=w.hf_to_body_ratio,
                    low_mid_ratio=w.low_mid_ratio,
                    gain_db=gain_db,
                    post_rms_dbfs=post_rms_dbfs,
                    post_peak_dbfs=post_peak_dbfs,
                )
            )

    all_exports.sort(key=lambda c: (c.source, c.start_sec))

    manifest_path = out_dir / "clip_manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(asdict(all_exports[0]).keys()) if all_exports else [
                "clip_filename",
                "source",
                "source_audio",
                "start_sec",
                "end_sec",
                "duration_sec",
                "selection_pass",
                "score",
                "raw_rms_dbfs",
                "raw_silence_pct",
                "hf_to_body_ratio",
                "low_mid_ratio",
                "gain_db",
                "post_rms_dbfs",
                "post_peak_dbfs",
            ],
        )
        writer.writeheader()
        for row in all_exports:
            writer.writerow(asdict(row))

    diag_path = out_dir / "selection_diagnostics.json"
    diag_path.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")

    total_dur = sum(c.duration_sec for c in all_exports)
    by_source: dict[str, int] = {}
    by_pass: dict[int, int] = {}
    for c in all_exports:
        by_source[c.source] = by_source.get(c.source, 0) + 1
        by_pass[c.selection_pass] = by_pass.get(c.selection_pass, 0) + 1

    lines = [
        "# Clean Training Clips",
        "",
        "Auto-selected and normalized windows for voice model training.",
        "",
        "## Rules",
        "",
        f"- Window: {args.window_sec:.2f}s, hop: {args.hop_sec:.2f}s, minimum gap: {args.min_gap_sec:.2f}s.",
        "- Composite quality score based on loudness, silence, brightness (HF/body), and low-mid body.",
        "- Adaptive threshold relaxation across up to 4 passes to meet per-source targets without over-admitting low quality windows.",
        f"- Normalization: target RMS {args.target_rms:.1f} dBFS, peak ceiling {args.peak_ceiling:.1f} dBFS.",
        f"- Fade in/out: {args.fade_ms:.1f} ms to avoid clip-boundary clicks.",
        "- Export format: mono WAV, 48kHz, 16-bit PCM.",
        "",
        "## Result",
        "",
        f"- Total clips: {len(all_exports)}",
        f"- Total duration: {total_dur:.1f}s ({total_dur / 60.0:.2f} min)",
    ]
    for source in sorted(by_source):
        lines.append(f"- {source}: {by_source[source]} clips")
    lines.append("- Selection pass usage:")
    for pass_idx in sorted(by_pass):
        lines.append(f"  - pass {pass_idx}: {by_pass[pass_idx]} clips")

    readme_path = out_dir / "README.md"
    readme_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {len(all_exports)} clips to {out_dir}")
    print(f"Manifest: {manifest_path}")
    print(f"Diagnostics: {diag_path}")
    print(f"Summary: {readme_path}")


if __name__ == "__main__":
    main()
