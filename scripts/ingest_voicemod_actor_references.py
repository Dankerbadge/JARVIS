#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import urllib.request
import wave
from array import array
from datetime import datetime
from pathlib import Path

import noisereduce as nr
import numpy as np
from scipy import signal

DB_EPS = 1e-12
RNNOISE_MODEL_URLS = [
    "https://raw.githubusercontent.com/GregorR/rnnoise-models/master/somnolent-hogwash-2018-09-01/sh.rnnn",
    "https://raw.githubusercontent.com/GregorR/rnnoise-models/master/leavened-quisling-2018-08-31/lq.rnnn",
    "https://raw.githubusercontent.com/GregorR/rnnoise-models/master/conjoined-burgers-2018-08-28/cb.rnnn",
]


def to_db(v: float | np.ndarray) -> float | np.ndarray:
    return 20.0 * np.log10(np.maximum(v, DB_EPS))


def read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sample_rate = wf.getframerate()
        sample_width = wf.getsampwidth()
        frame_count = wf.getnframes()
        raw = wf.readframes(frame_count)
    if sample_width != 2:
        raise ValueError(f"Expected 16-bit WAV: {path}")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    return x, sample_rate


def write_wav_mono(path: Path, x: np.ndarray, sample_rate: int) -> None:
    y = np.clip(x, -1.0, 1.0)
    pcm = (y * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def frame_rms_db(x: np.ndarray, sample_rate: int, frame_sec: float = 0.02, hop_sec: float = 0.01) -> np.ndarray:
    frame = max(1, int(sample_rate * frame_sec))
    hop = max(1, int(sample_rate * hop_sec))
    if len(x) < frame:
        rms = math.sqrt(float(np.mean(x * x)))
        return np.array([float(to_db(rms + DB_EPS))], dtype=np.float64)
    out: list[float] = []
    for i in range(0, len(x) - frame + 1, hop):
        chunk = x[i : i + frame]
        rms = math.sqrt(float(np.mean(chunk * chunk)))
        out.append(float(to_db(rms + DB_EPS)))
    return np.array(out, dtype=np.float64)


def spectral_stats(x: np.ndarray, sample_rate: int) -> tuple[float, float]:
    n_fft = 4096
    hop = n_fft // 2
    if len(x) < n_fft:
        return 0.0, 0.0
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    win = np.hanning(n_fft).astype(np.float32)
    mags = []
    for i in range(0, len(x) - n_fft + 1, hop):
        frame = x[i : i + n_fft]
        rms_db = float(to_db(math.sqrt(float(np.mean(frame * frame))) + DB_EPS))
        if rms_db < -42.0:
            continue
        mags.append(np.abs(np.fft.rfft(frame * win)) + 1e-12)
    if not mags:
        return 0.0, 0.0
    spec = np.mean(np.vstack(mags), axis=0)
    total = float(np.sum(spec) + DB_EPS)

    def band(lo: float, hi: float) -> float:
        mask = (freqs >= lo) & (freqs < hi)
        return float(np.sum(spec[mask]) / total)

    body = band(80.0, 4000.0)
    hiss = band(8000.0, 16000.0)
    return (hiss / max(body, DB_EPS)), body


def harmonicity_ac_peak(x: np.ndarray, sample_rate: int) -> float:
    frame = 2048
    hop = 1024
    lag_min = int(sample_rate / 300.0)
    lag_max = min(frame - 1, int(sample_rate / 70.0))
    if len(x) < frame:
        return 0.0
    vals: list[float] = []
    win = np.hanning(frame).astype(np.float32)
    for i in range(0, len(x) - frame + 1, hop):
        f = x[i : i + frame]
        rms_db = float(to_db(math.sqrt(float(np.mean(f * f))) + DB_EPS))
        if rms_db < -36.0:
            continue
        y = (f * win) - np.mean(f * win)
        spec = np.fft.rfft(y, n=4096)
        ac = np.fft.irfft(spec * np.conj(spec), n=4096)[:frame]
        if ac[0] <= 0:
            continue
        peak = float(np.max(ac[lag_min:lag_max])) if lag_max > lag_min else 0.0
        vals.append(peak / float(ac[0] + DB_EPS))
    if not vals:
        return 0.0
    return float(np.median(np.array(vals, dtype=np.float64)))


def highpass(x: np.ndarray, sample_rate: int, cutoff_hz: float = 65.0) -> np.ndarray:
    sos = signal.butter(2, cutoff_hz, btype="highpass", fs=sample_rate, output="sos")
    return signal.sosfiltfilt(sos, x).astype(np.float32)


def dehum(x: np.ndarray, sample_rate: int) -> np.ndarray:
    y = x.astype(np.float32)
    for f0 in (60.0, 120.0, 180.0, 240.0):
        if f0 >= (sample_rate / 2.0 - 100.0):
            continue
        b, a = signal.iirnotch(w0=f0, Q=30.0, fs=sample_rate)
        y = signal.filtfilt(b, a, y).astype(np.float32)
    return y


def deesser(x: np.ndarray, sample_rate: int, threshold_ratio: float = 0.23, max_reduction_db: float = 2.2) -> np.ndarray:
    sos_sib = signal.butter(2, [5200.0, 9200.0], btype="bandpass", fs=sample_rate, output="sos")
    sos_body = signal.butter(2, [120.0, 4200.0], btype="bandpass", fs=sample_rate, output="sos")
    sib = signal.sosfiltfilt(sos_sib, x)
    body = signal.sosfiltfilt(sos_body, x)
    env_s = signal.sosfiltfilt(signal.butter(1, 18.0, btype="lowpass", fs=sample_rate, output="sos"), np.abs(sib))
    env_b = signal.sosfiltfilt(signal.butter(1, 18.0, btype="lowpass", fs=sample_rate, output="sos"), np.abs(body))
    ratio = env_s / np.maximum(env_b, 1e-6)
    over = np.maximum(0.0, ratio - threshold_ratio)
    if float(np.max(over)) <= 0.0:
        return x.astype(np.float32)
    depth = np.clip(over / max(threshold_ratio, 1e-4), 0.0, 1.0)
    max_lin = 10 ** (-max_reduction_db / 20.0)
    gain = np.clip(1.0 - depth * (1.0 - max_lin), max_lin, 1.0)
    return (x - sib + (sib * gain)).astype(np.float32)


def normalize_rms(x: np.ndarray, target_rms_dbfs: float = -23.0, clamp_db: float = 8.0) -> np.ndarray:
    rms = math.sqrt(float(np.mean(x * x)))
    gain_db = float(target_rms_dbfs - float(to_db(rms + DB_EPS)))
    gain_db = float(np.clip(gain_db, -clamp_db, clamp_db))
    gain = 10 ** (gain_db / 20.0)
    return (x * gain).astype(np.float32)


def soft_limiter(x: np.ndarray, ceiling_dbfs: float = -1.1) -> np.ndarray:
    ceiling = 10 ** (ceiling_dbfs / 20.0)
    return (np.tanh(x / max(ceiling, 1e-6)) * ceiling).astype(np.float32)


def apply_fade(x: np.ndarray, sample_rate: int, ms: float = 8.0) -> np.ndarray:
    n = int(sample_rate * (ms / 1000.0))
    if n <= 1 or len(x) < 2 * n:
        return x
    y = x.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    y[:n] *= ramp
    y[-n:] *= ramp[::-1]
    return y


def detect_segments(
    x: np.ndarray,
    sample_rate: int,
    *,
    threshold_db: float = -42.5,
    min_duration_sec: float = 1.4,
    max_duration_sec: float = 7.0,
) -> list[tuple[int, int]]:
    frame = max(1, int(sample_rate * 0.02))
    hop = max(1, int(sample_rate * 0.01))
    if len(x) < frame:
        return [(0, len(x))] if len(x) > 0 else []
    values = frame_rms_db(x, sample_rate, frame_sec=0.02, hop_sec=0.01)
    starts = [i for i in range(0, len(x) - frame + 1, hop)]
    voiced = values > threshold_db
    runs: list[tuple[int, int]] = []
    run_start = None
    for idx, flag in enumerate(voiced):
        if flag and run_start is None:
            run_start = idx
        if (not flag) and run_start is not None:
            s = starts[run_start]
            e = starts[idx - 1] + frame
            runs.append((s, e))
            run_start = None
    if run_start is not None:
        runs.append((starts[run_start], starts[len(voiced) - 1] + frame))

    if not runs:
        return []

    padded: list[tuple[int, int]] = []
    pad = int(sample_rate * 0.08)
    for start, end in runs:
        padded.append((max(0, start - pad), min(len(x), end + pad)))

    merged: list[tuple[int, int]] = []
    gap_merge = int(sample_rate * 0.2)
    for start, end in padded:
        if not merged:
            merged.append((start, end))
            continue
        prev_s, prev_e = merged[-1]
        if start - prev_e <= gap_merge:
            merged[-1] = (prev_s, max(prev_e, end))
        else:
            merged.append((start, end))

    out: list[tuple[int, int]] = []
    min_len = int(sample_rate * min_duration_sec)
    max_len = int(sample_rate * max_duration_sec)
    split_step = int(sample_rate * max(1.2, max_duration_sec - 0.6))
    for start, end in merged:
        length = end - start
        if length < min_len:
            continue
        if length <= max_len:
            out.append((start, end))
            continue
        cursor = start
        while cursor < end:
            seg_end = min(end, cursor + max_len)
            if seg_end - cursor >= min_len:
                out.append((cursor, seg_end))
            if seg_end >= end:
                break
            cursor += split_step
    return out


def _secondary_cleanup_for_hiss(x: np.ndarray, sample_rate: int) -> np.ndarray:
    y = nr.reduce_noise(
        y=x.astype(np.float32),
        sr=sample_rate,
        stationary=True,
        prop_decrease=0.52,
        time_mask_smooth_ms=80,
        freq_mask_smooth_hz=320,
        n_std_thresh_stationary=1.5,
    ).astype(np.float32)
    # Trim only harsh air band after denoise while preserving articulation body.
    sos = signal.butter(2, 9800.0, btype="lowpass", fs=sample_rate, output="sos")
    y = signal.sosfiltfilt(sos, y).astype(np.float32)
    y = deesser(y, sample_rate, threshold_ratio=0.21, max_reduction_db=2.8)
    return y


def process_segment(
    x: np.ndarray,
    sample_rate: int,
    *,
    target_rms: float,
    max_hiss_ratio: float,
) -> np.ndarray:
    y = (x - np.mean(x)).astype(np.float32)
    y = highpass(y, sample_rate, cutoff_hz=65.0)
    y = dehum(y, sample_rate)
    hiss_ratio, _ = spectral_stats(y, sample_rate)
    if hiss_ratio >= 0.06:
        y = nr.reduce_noise(
            y=y,
            sr=sample_rate,
            stationary=True,
            prop_decrease=0.35,
            time_mask_smooth_ms=70,
            freq_mask_smooth_hz=300,
            n_std_thresh_stationary=1.6,
        ).astype(np.float32)
    y = deesser(y, sample_rate, threshold_ratio=0.23, max_reduction_db=2.2)
    hiss_after, _ = spectral_stats(y, sample_rate)
    if hiss_after > max_hiss_ratio:
        y = _secondary_cleanup_for_hiss(y, sample_rate)
    y = normalize_rms(y, target_rms_dbfs=target_rms, clamp_db=8.0)
    y = soft_limiter(y, ceiling_dbfs=-1.1)
    y = apply_fade(y, sample_rate, ms=8.0)
    return y


def calc_clip_metrics(x: np.ndarray, sample_rate: int) -> tuple[float, float, float]:
    rms_series = frame_rms_db(x, sample_rate, frame_sec=0.05, hop_sec=0.025)
    silence_pct = float(np.mean(rms_series < -45.0) * 100.0)
    hiss_ratio, _ = spectral_stats(x, sample_rate)
    harm = harmonicity_ac_peak(x, sample_rate)
    return silence_pct, hiss_ratio, harm


def convert_mp3_to_wav(input_path: Path, output_path: Path, *, sample_rate: int = 24000) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "afconvert",
        "-f",
        "WAVE",
        "-d",
        f"LEI16@{int(sample_rate)}",
        "-c",
        "1",
        str(input_path),
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"afconvert failed for {input_path}: {(proc.stderr or proc.stdout).strip()}")


def convert_audio_with_ffmpeg(
    *,
    input_path: Path,
    output_path: Path,
    sample_rate: int,
    ffmpeg_bin: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(int(sample_rate)),
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"ffmpeg convert failed for {input_path}: {detail}")


def ensure_rnnoise_model(path: Path, *, download: bool) -> Path | None:
    if path.exists() and path.is_file():
        return path
    if not download:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    for url in RNNOISE_MODEL_URLS:
        try:
            with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310 - fixed trusted URLs
                blob = response.read()
            if not blob:
                continue
            path.write_bytes(blob)
            return path
        except Exception:
            continue
    return None


def isolate_with_ffmpeg(
    *,
    input_path: Path,
    output_path: Path,
    ffmpeg_bin: str,
    rnnoise_model: Path | None,
    profile: str,
    sample_rate: int = 24000,
) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    profile_norm = str(profile or "standard").strip().lower()
    if profile_norm not in {"standard", "strong"}:
        profile_norm = "standard"
    if profile_norm == "strong":
        afftdn = "afftdn=nf=-27"
        deesser = "deesser=i=0.34"
    else:
        afftdn = "afftdn=nf=-24"
        deesser = "deesser=i=0.28"

    filters = ["highpass=f=65", "lowpass=f=7600"]
    if rnnoise_model is not None and rnnoise_model.exists():
        filters.append(f"arnndn=m={rnnoise_model}:mix=1")
    filters.extend([afftdn, deesser])
    filter_chain = ",".join(filters)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(int(sample_rate)),
        "-af",
        filter_chain,
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode == 0


def segment_quality_score(
    *,
    duration_sec: float,
    silence_pct: float,
    hiss_ratio: float,
    harmonicity: float,
) -> float:
    harm = float(np.clip(harmonicity / 0.62, 0.0, 1.0))
    hiss = float(np.clip(1.0 - (hiss_ratio / 0.16), 0.0, 1.0))
    silence = float(np.clip(1.0 - (silence_pct / 20.0), 0.0, 1.0))
    dur = float(np.clip(duration_sec / 4.8, 0.0, 1.0))
    return (0.46 * harm) + (0.31 * hiss) + (0.18 * silence) + (0.05 * dur)


def build_creator_upload_bundle(
    *,
    work_dir: Path,
    clips_dir: Path,
    rows: list[dict[str, str]],
    target_total_sec: float,
    max_clips: int,
) -> dict[str, object]:
    bundle_dir = work_dir / "elevenlabs_creator_bundle"
    bundle_clips_dir = bundle_dir / "clips"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_clips_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[tuple[float, dict[str, str], Path, float]] = []
    for row in rows:
        clip_name = str(row.get("clip_filename") or "").strip()
        if not clip_name:
            continue
        clip_path = clips_dir / clip_name
        if not clip_path.exists():
            continue
        try:
            duration = float(row.get("duration_sec") or 0.0)
            silence = float(row.get("silence_pct") or 100.0)
            hiss = float(row.get("hiss_ratio") or 1.0)
            harm = float(row.get("harmonicity") or 0.0)
        except (TypeError, ValueError):
            continue
        quality = segment_quality_score(
            duration_sec=duration,
            silence_pct=silence,
            hiss_ratio=hiss,
            harmonicity=harm,
        )
        candidates.append((quality, row, clip_path, duration))

    candidates.sort(
        key=lambda item: (
            item[0],
            float(item[1].get("harmonicity") or 0.0),
            -float(item[1].get("hiss_ratio") or 0.0),
        ),
        reverse=True,
    )
    selected: list[tuple[float, dict[str, str], Path, float]] = []
    total = 0.0
    for candidate in candidates:
        _, row, _, duration = candidate
        if len(selected) >= max(1, int(max_clips)):
            break
        if total >= float(target_total_sec):
            break
        selected.append(candidate)
        total += max(0.0, float(duration))

    manifest_path = bundle_dir / "manifest.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "rank",
                "clip_filename",
                "duration_sec",
                "silence_pct",
                "hiss_ratio",
                "harmonicity",
                "quality_score",
                "source_path",
            ],
        )
        writer.writeheader()
        for idx, (quality, row, clip_path, duration) in enumerate(selected, start=1):
            dst = bundle_clips_dir / clip_path.name
            shutil.copy2(clip_path, dst)
            writer.writerow(
                {
                    "rank": idx,
                    "clip_filename": clip_path.name,
                    "duration_sec": f"{float(duration):.6f}",
                    "silence_pct": str(row.get("silence_pct") or ""),
                    "hiss_ratio": str(row.get("hiss_ratio") or ""),
                    "harmonicity": str(row.get("harmonicity") or ""),
                    "quality_score": f"{quality:.6f}",
                    "source_path": str(clip_path),
                }
            )

    summary = {
        "bundle_dir": str(bundle_dir),
        "clip_count": len(selected),
        "target_total_sec": float(target_total_sec),
        "actual_total_sec": round(float(total), 6),
        "manifest": str(manifest_path),
    }
    (bundle_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def sum_wav_durations(clips_dir: Path) -> float:
    total = 0.0
    for wav_path in sorted(clips_dir.glob("*.wav")):
        with wave.open(str(wav_path), "rb") as wf:
            total += wf.getnframes() / float(wf.getframerate())
    return float(total)


def stitch_preview(clips: list[Path], out_path: Path, count: int, gap_ms: int = 120) -> None:
    if not clips:
        return
    chosen = clips[: max(1, int(count))]
    with wave.open(str(chosen[0]), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        sample_rate = wf.getframerate()
    if channels != 1 or width != 2:
        return
    gap_frames = int(sample_rate * (gap_ms / 1000.0))
    gap = array("h", [0] * gap_frames)
    merged = array("h")
    for idx, clip in enumerate(chosen):
        with wave.open(str(clip), "rb") as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != sample_rate:
                continue
            data = array("h")
            data.frombytes(wf.readframes(wf.getnframes()))
            merged.extend(data)
        if idx < len(chosen) - 1:
            merged.extend(gap)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(merged.tobytes())


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Ingest Voicemod actor references into active JARVIS pack.")
    parser.add_argument(
        "--inputs",
        nargs="+",
        type=Path,
        default=[
            Path("/Users/dankerbadge/Downloads/jarvis-introduciton-made-with-Voicemod.mp3"),
            Path("/Users/dankerbadge/Downloads/jarvis-power-source-made-with-Voicemod.mp3"),
        ],
        help="Input MP3/WAV actor reference recordings.",
    )
    parser.add_argument(
        "--pack-root",
        type=Path,
        default=root / ".jarvis" / "voice" / "training_assets" / "jarvis_actor_isolated_actor_match",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=root / "analysis" / "jarvis_study" / "new_actor_refs",
    )
    parser.add_argument(
        "--ffmpeg-isolate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run pre-isolation on each input with ffmpeg denoise + rnnoise before segmentation.",
    )
    parser.add_argument("--ffmpeg-bin", default="ffmpeg")
    parser.add_argument(
        "--rnnoise-model",
        type=Path,
        default=root / "analysis" / "jarvis_study" / "new_actor_refs" / "rnnoise" / "sh.rnnn",
    )
    parser.add_argument(
        "--download-rnnoise-model",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Download an rnnoise model if missing for ffmpeg arnndn filtering.",
    )
    parser.add_argument(
        "--ffmpeg-profile",
        choices=["standard", "strong"],
        default="strong",
        help="Noise suppression profile used in ffmpeg pre-isolation.",
    )
    parser.add_argument("--target-rms", type=float, default=-23.0)
    parser.add_argument("--min-segment-sec", type=float, default=1.4)
    parser.add_argument("--max-segment-sec", type=float, default=7.0)
    parser.add_argument("--max-hiss-ratio", type=float, default=0.13)
    parser.add_argument("--max-silence-pct", type=float, default=14.5)
    parser.add_argument("--min-harmonicity", type=float, default=0.36)
    parser.add_argument(
        "--max-clips-per-input",
        type=int,
        default=48,
        help="Maximum accepted clips to keep per input file after quality ranking.",
    )
    parser.add_argument(
        "--replace-existing-ref-clips",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Remove prior jarvis_ref_vm_* clips from target pack before ingesting.",
    )
    parser.add_argument("--preview-count", type=int, default=10)
    parser.add_argument(
        "--build-creator-bundle",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Create an ElevenLabs Creator upload bundle (best clips + manifest).",
    )
    parser.add_argument("--creator-bundle-target-sec", type=float, default=180.0)
    parser.add_argument("--creator-bundle-max-clips", type=int, default=40)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pack_root = args.pack_root.resolve()
    clips_dir = (pack_root / "clips").resolve()
    manifest_path = (pack_root / "manifest.csv").resolve()
    metadata_path = (pack_root / "metadata.json").resolve()
    preview_path = (pack_root / "preview_reel.wav").resolve()
    work_dir = args.work_dir.resolve()
    normalized_dir = work_dir / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)

    if bool(args.replace_existing_ref_clips):
        for old_clip in sorted(clips_dir.glob("jarvis_ref_vm_*.wav")):
            try:
                old_clip.unlink()
            except OSError:
                pass

    existing_prefix_count = len([p for p in clips_dir.glob("jarvis_ref_vm_*.wav") if p.is_file()])
    clip_counter = existing_prefix_count + 1
    new_rows: list[dict[str, str]] = []
    new_clip_paths: list[Path] = []
    isolation_report: list[dict[str, object]] = []

    rnnoise_path: Path | None = None
    ffmpeg_available = bool(shutil.which(str(args.ffmpeg_bin)))
    if bool(args.ffmpeg_isolate) and ffmpeg_available:
        rnnoise_path = ensure_rnnoise_model(
            args.rnnoise_model.expanduser().resolve(),
            download=bool(args.download_rnnoise_model),
        )

    for input_path_raw in args.inputs:
        input_path = input_path_raw.expanduser().resolve()
        if not input_path.exists():
            raise FileNotFoundError(f"Input not found: {input_path}")
        normalized_wav = normalized_dir / f"{input_path.stem}.wav"
        if ffmpeg_available:
            convert_audio_with_ffmpeg(
                input_path=input_path,
                output_path=normalized_wav,
                sample_rate=24000,
                ffmpeg_bin=str(args.ffmpeg_bin),
            )
        elif input_path.suffix.lower() == ".wav":
            normalized_wav.write_bytes(input_path.read_bytes())
        else:
            convert_mp3_to_wav(input_path, normalized_wav, sample_rate=24000)

        analysis_wav = normalized_wav
        used_ffmpeg_isolation = False
        if bool(args.ffmpeg_isolate) and ffmpeg_available:
            isolated_wav = normalized_dir / f"{input_path.stem}.isolated.wav"
            ok = isolate_with_ffmpeg(
                input_path=normalized_wav,
                output_path=isolated_wav,
                ffmpeg_bin=str(args.ffmpeg_bin),
                rnnoise_model=rnnoise_path,
                profile=str(args.ffmpeg_profile),
                sample_rate=24000,
            )
            if ok and isolated_wav.exists():
                analysis_wav = isolated_wav
                used_ffmpeg_isolation = True

        x, sample_rate = read_wav_mono(analysis_wav)
        segments = detect_segments(
            x,
            sample_rate,
            threshold_db=-43.0,
            min_duration_sec=float(args.min_segment_sec),
            max_duration_sec=float(args.max_segment_sec),
        )
        if not segments:
            segments = [(0, len(x))]

        accepted_for_input: list[tuple[float, int, int, np.ndarray, float, float, float]] = []
        for start, end in segments:
            chunk = x[start:end]
            if len(chunk) < int(sample_rate * float(args.min_segment_sec)):
                continue
            y = process_segment(
                chunk,
                sample_rate,
                target_rms=float(args.target_rms),
                max_hiss_ratio=float(args.max_hiss_ratio),
            )
            if len(y) <= 0:
                continue
            silence_pct, hiss_ratio, harm = calc_clip_metrics(y, sample_rate)
            if hiss_ratio > float(args.max_hiss_ratio):
                continue
            if silence_pct > float(args.max_silence_pct):
                continue
            if harm < float(args.min_harmonicity):
                continue
            duration_sec = len(y) / float(sample_rate)
            quality = segment_quality_score(
                duration_sec=duration_sec,
                silence_pct=silence_pct,
                hiss_ratio=hiss_ratio,
                harmonicity=harm,
            )
            accepted_for_input.append((quality, start, end, y, silence_pct, hiss_ratio, harm))

        accepted_for_input.sort(key=lambda item: item[0], reverse=True)
        max_keep = max(1, int(args.max_clips_per_input))
        accepted_for_input = accepted_for_input[:max_keep]

        kept_count = 0
        for quality, start, end, y, silence_pct, hiss_ratio, harm in accepted_for_input:
            start_ms = int(round((start / sample_rate) * 1000.0))
            end_ms = int(round((end / sample_rate) * 1000.0))
            clip_name = f"jarvis_ref_vm_{clip_counter:03d}_{start_ms:07d}_{end_ms:07d}.wav"
            clip_counter += 1
            out_clip = clips_dir / clip_name
            write_wav_mono(out_clip, y, sample_rate)
            duration_sec = len(y) / float(sample_rate)
            new_rows.append(
                {
                    "clip_filename": clip_name,
                    "source": "JARVIS_REF",
                    "start_sec": f"{start / float(sample_rate):.3f}",
                    "duration_sec": f"{duration_sec:.6f}",
                    "selected": "reference",
                    "reason": f"voicemod_reference_{input_path.stem}",
                    "score_delta": "0.000000",
                    "clarity_delta": f"{quality:.6f}",
                    "silence_pct": f"{silence_pct:.6f}",
                    "hiss_ratio": f"{hiss_ratio:.6f}",
                    "harmonicity": f"{harm:.6f}",
                    "actor_match_score": "",
                    "movie_match_score": "",
                }
            )
            new_clip_paths.append(out_clip)
            kept_count += 1

        isolation_report.append(
            {
                "input": str(input_path),
                "normalized_wav": str(normalized_wav),
                "analysis_wav": str(analysis_wav),
                "ffmpeg_isolated": bool(used_ffmpeg_isolation),
                "segments_detected": len(segments),
                "segments_kept": kept_count,
            }
        )

    if not new_rows:
        raise RuntimeError("No usable segments detected from provided inputs.")

    if not manifest_path.exists():
        fieldnames = list(new_rows[0].keys())
        existing_rows: list[dict[str, str]] = []
    else:
        with manifest_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            existing_rows = [dict(row) for row in reader if isinstance(row, dict)]
        if not fieldnames:
            fieldnames = list(new_rows[0].keys())

    for key in new_rows[0].keys():
        if key not in fieldnames:
            fieldnames.append(key)

    if bool(args.replace_existing_ref_clips):
        existing_rows = [
            row
            for row in existing_rows
            if not str(row.get("clip_filename") or "").strip().startswith("jarvis_ref_vm_")
        ]

    existing_names = {str(row.get("clip_filename") or "").strip() for row in existing_rows}
    deduped_new = [row for row in new_rows if str(row.get("clip_filename") or "").strip() not in existing_names]
    merged_rows = existing_rows + deduped_new
    with manifest_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in merged_rows:
            writer.writerow(row)

    metadata: dict[str, object]
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                metadata = {}
        except (OSError, json.JSONDecodeError):
            metadata = {}
    else:
        metadata = {}

    total_duration_sec = sum_wav_durations(clips_dir)
    metadata["clip_count"] = len(list(clips_dir.glob("*.wav")))
    metadata["total_duration_sec"] = round(total_duration_sec, 6)
    metadata["updated_at"] = datetime.now().isoformat()
    supplemental = metadata.get("supplemental_references")
    history = supplemental.get("history") if isinstance(supplemental, dict) else []
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "ingested_at": datetime.now().isoformat(),
            "inputs": [str(path.expanduser().resolve()) for path in args.inputs],
            "added_clip_count": len(deduped_new),
            "added_clips": [row["clip_filename"] for row in deduped_new],
            "work_dir": str(work_dir),
            "ffmpeg_isolate": bool(args.ffmpeg_isolate),
            "ffmpeg_profile": str(args.ffmpeg_profile),
            "rnnoise_model": str(rnnoise_path) if rnnoise_path is not None else None,
            "max_clips_per_input": int(args.max_clips_per_input),
        }
    )
    metadata["supplemental_references"] = {
        "count": len(history),
        "history": history[-10:],
    }
    metadata["last_isolation_report"] = isolation_report
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    stitch_preview(sorted(clips_dir.glob("*.wav")), preview_path, count=int(args.preview_count))

    creator_bundle_summary: dict[str, object] | None = None
    if bool(args.build_creator_bundle):
        creator_rows = [
            row
            for row in merged_rows
            if str(row.get("clip_filename") or "").strip().startswith("jarvis_ref_vm_")
        ]
        creator_bundle_summary = build_creator_upload_bundle(
            work_dir=work_dir,
            clips_dir=clips_dir,
            rows=creator_rows,
            target_total_sec=float(args.creator_bundle_target_sec),
            max_clips=int(args.creator_bundle_max_clips),
        )

    ingest_report = {
        "ingested_at": datetime.now().isoformat(),
        "input_count": len(args.inputs),
        "added_clip_count": len(deduped_new),
        "new_clip_paths": [str(path) for path in new_clip_paths],
        "ffmpeg_isolate": bool(args.ffmpeg_isolate),
        "ffmpeg_profile": str(args.ffmpeg_profile),
        "rnnoise_model": str(rnnoise_path) if rnnoise_path is not None else None,
        "isolation_report": isolation_report,
        "creator_bundle": creator_bundle_summary,
    }
    ingest_report_path = work_dir / "voicemod_ingest_report.json"
    ingest_report_path.write_text(json.dumps(ingest_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"[ok] ingest complete for {len(deduped_new)} clips")
    print(f"[ok] pack_root: {pack_root}")
    print(f"[ok] manifest: {manifest_path}")
    print(f"[ok] metadata: {metadata_path}")
    print(f"[ok] preview_reel: {preview_path}")
    if creator_bundle_summary is not None:
        print(f"[ok] creator_bundle: {creator_bundle_summary.get('bundle_dir')}")
        print(f"[ok] creator_bundle_manifest: {creator_bundle_summary.get('manifest')}")
        print(f"[ok] creator_bundle_clip_count: {creator_bundle_summary.get('clip_count')}")
    print(f"[ok] ingest_report: {ingest_report_path}")
    if new_clip_paths:
        print("[ok] new clips:")
        for clip_path in new_clip_paths:
            print(f"  - {clip_path}")


if __name__ == "__main__":
    main()
