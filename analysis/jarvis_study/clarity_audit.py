#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import wave
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

DB_EPS = 1e-12


@dataclass
class ClarityRow:
    clip_filename: str
    source: str
    duration_sec: float
    post_rms_dbfs: float
    silence_pct: float
    hiss_ratio_8k_16k_to_80_4k: float
    presence_ratio_2k_5k: float
    spectral_flatness: float
    harmonicity_ac_peak: float
    clarity_score: float
    rank: int


def to_db(v: float | np.ndarray) -> float | np.ndarray:
    return 20.0 * np.log10(np.maximum(v, DB_EPS))


def read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        ch = wf.getnchannels()
        sr = wf.getframerate()
        sw = wf.getsampwidth()
        n = wf.getnframes()
        raw = wf.readframes(n)

    if sw != 2:
        raise ValueError(f"Expected 16-bit WAV: {path}")

    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def frame_iter(x: np.ndarray, frame: int, hop: int):
    if len(x) < frame:
        yield x
        return
    for i in range(0, len(x) - frame + 1, hop):
        yield x[i : i + frame]


def frame_rms_db(x: np.ndarray, sr: int, frame_sec: float = 0.05, hop_sec: float = 0.025) -> np.ndarray:
    frame = max(1, int(sr * frame_sec))
    hop = max(1, int(sr * hop_sec))
    vals = []
    for f in frame_iter(x, frame, hop):
        rms = math.sqrt(float(np.mean(f * f)))
        vals.append(float(to_db(rms)))
    return np.array(vals, dtype=np.float64)


def harmonicity_ac_peak(x: np.ndarray, sr: int) -> float:
    frame = 2048
    hop = 1024
    lag_min = int(sr / 300.0)
    lag_max = min(frame - 1, int(sr / 70.0))
    if len(x) < frame:
        return 0.0

    vals = []
    win = np.hanning(frame).astype(np.float32)
    for f in frame_iter(x, frame, hop):
        rms_db = float(to_db(math.sqrt(float(np.mean(f * f)))))
        if rms_db < -35.0:
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


def spectral_stats(x: np.ndarray, sr: int) -> tuple[float, float, float]:
    n_fft = 4096
    hop = n_fft // 2
    if len(x) < n_fft:
        return 0.0, 0.0, 1.0

    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    win = np.hanning(n_fft).astype(np.float32)

    mags = []
    flat_vals = []
    for f in frame_iter(x, n_fft, hop):
        rms_db = float(to_db(math.sqrt(float(np.mean(f * f)))))
        if rms_db < -40.0:
            continue
        m = np.abs(np.fft.rfft(f * win)) + 1e-12
        mags.append(m)
        gm = float(np.exp(np.mean(np.log(m))))
        am = float(np.mean(m))
        flat_vals.append(gm / max(am, 1e-12))

    if not mags:
        return 0.0, 0.0, 1.0

    s = np.mean(np.vstack(mags), axis=0)
    tot = float(np.sum(s) + DB_EPS)

    def band(lo: float, hi: float) -> float:
        m = (freqs >= lo) & (freqs < hi)
        return float(np.sum(s[m]) / tot)

    body = band(80.0, 4000.0)
    hiss = band(8000.0, 16000.0)
    presence = band(2000.0, 5000.0)
    flat = float(np.median(np.array(flat_vals, dtype=np.float64)))

    hiss_ratio = hiss / max(body, DB_EPS)
    return hiss_ratio, presence, flat


def score_clip(
    post_rms_dbfs: float,
    silence_pct: float,
    hiss_ratio: float,
    presence_ratio: float,
    flatness: float,
    harmonicity: float,
) -> float:
    score = 100.0

    # Loudness consistency for intelligibility.
    if post_rms_dbfs < -25.5:
        score -= (abs(post_rms_dbfs + 25.5) * 3.0)
    if post_rms_dbfs > -20.0:
        score -= (abs(post_rms_dbfs + 20.0) * 2.0)

    # Penalize overly silent windows.
    if silence_pct > 12.0:
        score -= (silence_pct - 12.0) * 1.8

    # Penalize hiss-heavy windows.
    if hiss_ratio > 0.55:
        score -= (hiss_ratio - 0.55) * 55.0

    # Reward articulation presence; penalize muffled.
    if presence_ratio < 0.13:
        score -= (0.13 - presence_ratio) * 120.0
    if presence_ratio > 0.34:
        score -= (presence_ratio - 0.34) * 40.0

    # Flat/noise-like spectra hurt clarity.
    if flatness > 0.27:
        score -= (flatness - 0.27) * 120.0

    # Strong harmonicity helps perceived clarity.
    score += max(0.0, (harmonicity - 0.24) * 35.0)

    return float(score)


def main() -> None:
    base_dir = Path(
        "/Users/dankerbadge/Documents/J.A.R.V.I.S/analysis/jarvis_study/clean_training_clips_deep_strict_filled_aggressive"
    )
    manifest = base_dir / "clip_manifest.csv"
    rows = list(csv.DictReader(manifest.open()))

    out_rows: list[ClarityRow] = []
    for r in rows:
        clip = base_dir / r["clip_filename"]
        x, sr = read_wav_mono(clip)

        rms = math.sqrt(float(np.mean(x * x)))
        post_rms = float(to_db(rms))
        fr = frame_rms_db(x, sr)
        silence = float(np.mean(fr < -45.0) * 100.0)

        hiss_ratio, presence_ratio, flatness = spectral_stats(x, sr)
        harm = harmonicity_ac_peak(x, sr)

        clarity = score_clip(
            post_rms_dbfs=post_rms,
            silence_pct=silence,
            hiss_ratio=hiss_ratio,
            presence_ratio=presence_ratio,
            flatness=flatness,
            harmonicity=harm,
        )

        out_rows.append(
            ClarityRow(
                clip_filename=r["clip_filename"],
                source=r["source"],
                duration_sec=float(r["duration_sec"]),
                post_rms_dbfs=post_rms,
                silence_pct=silence,
                hiss_ratio_8k_16k_to_80_4k=hiss_ratio,
                presence_ratio_2k_5k=presence_ratio,
                spectral_flatness=flatness,
                harmonicity_ac_peak=harm,
                clarity_score=clarity,
                rank=0,
            )
        )

    out_rows.sort(key=lambda x: x.clarity_score, reverse=True)
    for i, r in enumerate(out_rows, start=1):
        r.rank = i

    # Save audit CSV.
    audit_csv = base_dir / "clarity_audit.csv"
    with audit_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(out_rows[0]).keys()))
        writer.writeheader()
        for r in out_rows:
            writer.writerow(asdict(r))

    # Build a clarity-prioritized subset while preserving some coverage.
    keep: list[ClarityRow] = []
    for r in out_rows:
        if r.clarity_score >= 92.0:
            keep.append(r)
    if len(keep) < 28:
        keep = out_rows[:28]

    subset_dir = base_dir.parent / "clean_training_clips_deep_strict_filled_aggressive_clarity"
    subset_dir.mkdir(parents=True, exist_ok=True)
    for p in subset_dir.glob("*.wav"):
        p.unlink()
    for n in ["clip_manifest_clarity.csv", "README.md", "clarity_playlist.m3u"]:
        q = subset_dir / n
        if q.exists():
            q.unlink()

    import shutil

    for r in keep:
        shutil.copy2(base_dir / r.clip_filename, subset_dir / r.clip_filename)

    subset_csv = subset_dir / "clip_manifest_clarity.csv"
    with subset_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(keep[0]).keys()))
        writer.writeheader()
        for r in keep:
            writer.writerow(asdict(r))

    playlist = subset_dir / "clarity_playlist.m3u"
    lines = ["#EXTM3U"]
    for r in keep:
        lines.append(f"#EXTINF:-1,rank={r.rank} clarity={r.clarity_score:.2f} {r.source}")
        lines.append(r.clip_filename)
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")

    total = sum(r.duration_sec for r in keep)
    readme = subset_dir / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# Clarity Subset",
                "",
                "Auto-selected clarity-prioritized subset from strict-filled-aggressive clips.",
                "",
                "## Result",
                "",
                f"- Selected clips: {len(keep)}",
                f"- Total duration: {total:.1f}s ({total/60.0:.2f} min)",
                f"- Source pack: {base_dir}",
                "- Ranked by objective clarity score (hiss/presence/flatness/harmonicity/loudness).",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Wrote audit: {audit_csv}")
    print(f"Wrote clarity subset: {subset_dir}")
    print(f"Subset clips: {len(keep)}")


if __name__ == "__main__":
    main()
