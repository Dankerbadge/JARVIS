#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import wave
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

DB_EPS = 1e-12


@dataclass
class EnhanceRow:
    clip_filename: str
    duration_sec: float
    in_rms_dbfs: float
    out_rms_dbfs: float
    in_peak_dbfs: float
    out_peak_dbfs: float
    noise_reduction_db_est: float
    sibilance_reduction_db_est: float


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


def write_wav_mono(path: Path, x: np.ndarray, sr: int) -> None:
    y = np.clip(x, -1.0, 1.0)
    pcm = (y * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def highpass_first_order(x: np.ndarray, sr: int, cutoff_hz: float = 65.0) -> np.ndarray:
    if len(x) == 0:
        return x
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    dt = 1.0 / sr
    alpha = rc / (rc + dt)
    y = np.zeros_like(x)
    y[0] = x[0]
    for i in range(1, len(x)):
        y[i] = alpha * (y[i - 1] + x[i] - x[i - 1])
    return y


def lowpass_first_order(x: np.ndarray, sr: int, cutoff_hz: float) -> np.ndarray:
    if len(x) == 0:
        return x
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    dt = 1.0 / sr
    alpha = dt / (rc + dt)
    y = np.zeros_like(x)
    y[0] = alpha * x[0]
    for i in range(1, len(x)):
        y[i] = y[i - 1] + alpha * (x[i] - y[i - 1])
    return y


def bandpass_first_order(x: np.ndarray, sr: int, lo_hz: float, hi_hz: float) -> np.ndarray:
    return highpass_first_order(lowpass_first_order(x, sr, hi_hz), sr, lo_hz)


def stft(x: np.ndarray, n_fft: int = 2048, hop: int = 512) -> tuple[np.ndarray, np.ndarray]:
    win = np.hanning(n_fft).astype(np.float32)
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))

    n_frames = 1 + (len(x) - n_fft) // hop
    spec = np.empty((n_fft // 2 + 1, n_frames), dtype=np.complex64)
    for i in range(n_frames):
        s = i * hop
        frame = x[s : s + n_fft] * win
        spec[:, i] = np.fft.rfft(frame)
    return spec, win


def istft(spec: np.ndarray, win: np.ndarray, hop: int = 512) -> np.ndarray:
    n_fft = (spec.shape[0] - 1) * 2
    n_frames = spec.shape[1]
    out_len = n_fft + hop * (n_frames - 1)
    y = np.zeros(out_len, dtype=np.float32)
    wsum = np.zeros(out_len, dtype=np.float32)

    for i in range(n_frames):
        s = i * hop
        frame = np.fft.irfft(spec[:, i], n=n_fft).astype(np.float32)
        y[s : s + n_fft] += frame * win
        wsum[s : s + n_fft] += win * win

    nz = wsum > 1e-8
    y[nz] /= wsum[nz]
    return y


def smooth2d(x: np.ndarray, kf: int = 5, kt: int = 5) -> np.ndarray:
    y = x
    if kf > 1:
        kernel_f = np.ones(kf, dtype=np.float32) / float(kf)
        y = np.apply_along_axis(lambda v: np.convolve(v, kernel_f, mode="same"), axis=0, arr=y)
    if kt > 1:
        kernel_t = np.ones(kt, dtype=np.float32) / float(kt)
        y = np.apply_along_axis(lambda v: np.convolve(v, kernel_t, mode="same"), axis=1, arr=y)
    return y


def spectral_denoise(
    x: np.ndarray,
    sr: int,
    n_fft: int = 2048,
    hop: int = 512,
    noise_quantile: float = 0.14,
    reduction_strength: float = 0.85,
    floor: float = 0.12,
) -> tuple[np.ndarray, float]:
    spec, win = stft(x, n_fft=n_fft, hop=hop)
    mag = np.abs(spec)
    ph = np.exp(1j * np.angle(spec))

    frame_energy = np.mean(mag, axis=0)
    thr = np.quantile(frame_energy, noise_quantile)
    noise_idx = frame_energy <= thr
    if not np.any(noise_idx):
        noise_idx = frame_energy <= np.median(frame_energy)
    noise_prof = np.median(mag[:, noise_idx], axis=1, keepdims=True)

    # Frequency-shaped noise reduction: lighter below 200 Hz and above 10 kHz
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    shape = np.ones_like(freqs, dtype=np.float32)
    shape[freqs < 200] = 0.55
    shape[freqs > 10000] = 0.75
    shape = shape[:, None]

    ratio = (noise_prof * reduction_strength * shape) / np.maximum(mag, 1e-8)
    gain = np.clip(1.0 - ratio, floor, 1.0)
    gain = smooth2d(gain, kf=7, kt=5)

    out_spec = mag * gain * ph
    y = istft(out_spec, win=win, hop=hop)
    y = y[: len(x)]

    in_noise = np.median(mag[:, noise_idx])
    out_noise = np.median(np.abs(out_spec)[:, noise_idx]) if np.any(noise_idx) else in_noise
    nr_db = float(to_db(in_noise / max(out_noise, 1e-8))) if out_noise > 0 else 0.0
    return y.astype(np.float32), nr_db


def dynamic_deesser(
    x: np.ndarray,
    sr: int,
    band_lo: float = 5200.0,
    band_hi: float = 9200.0,
    threshold_ratio: float = 0.22,
    max_reduction_db: float = 3.0,
) -> tuple[np.ndarray, float]:
    sib = bandpass_first_order(x, sr, band_lo, band_hi)
    body = bandpass_first_order(x, sr, 120.0, 4200.0)

    # Envelope estimation
    env_s = lowpass_first_order(np.abs(sib), sr, 18.0)
    env_b = lowpass_first_order(np.abs(body), sr, 18.0)
    ratio = env_s / np.maximum(env_b, 1e-5)

    over = np.maximum(0.0, ratio - threshold_ratio)
    if np.max(over) <= 0:
        return x, 0.0

    depth = np.clip(over / max(threshold_ratio, 1e-4), 0.0, 1.0)
    max_lin = 10 ** (-max_reduction_db / 20.0)
    gain = 1.0 - depth * (1.0 - max_lin)
    gain = np.clip(gain, max_lin, 1.0)

    y = x - sib + sib * gain
    red_db = float(to_db(np.mean(np.abs(sib)) / max(np.mean(np.abs(sib * gain)), 1e-8)))
    return y.astype(np.float32), red_db


def gentle_presence_eq(x: np.ndarray, sr: int, presence_boost_db: float = 1.4, air_cut_db: float = -0.8) -> np.ndarray:
    presence = bandpass_first_order(x, sr, 2200.0, 4800.0)
    air = highpass_first_order(x, sr, 9500.0)
    p_gain = 10 ** (presence_boost_db / 20.0)
    a_gain = 10 ** (air_cut_db / 20.0)
    y = x + (p_gain - 1.0) * presence + (a_gain - 1.0) * air
    return y.astype(np.float32)


def apply_fade(x: np.ndarray, sr: int, ms: float = 12.0) -> np.ndarray:
    n = int(sr * (ms / 1000.0))
    if n <= 1 or len(x) < 2 * n:
        return x
    y = x.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    y[:n] *= ramp
    y[-n:] *= ramp[::-1]
    return y


def soft_limiter(x: np.ndarray, ceiling_dbfs: float = -1.2) -> np.ndarray:
    ceiling = 10 ** (ceiling_dbfs / 20.0)
    # Smooth soft clip preserving transients better than hard clipping.
    y = np.tanh(x / max(ceiling, 1e-6)) * ceiling
    return y.astype(np.float32)


def normalize_rms(x: np.ndarray, target_dbfs: float = -23.0) -> np.ndarray:
    rms = math.sqrt(float(np.mean(x * x)))
    if rms < DB_EPS:
        return x
    gain = (10 ** (target_dbfs / 20.0)) / rms
    return (x * gain).astype(np.float32)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Enhance a voice clip pack for clarity.")
    p.add_argument(
        "--input-dir",
        type=Path,
        default=Path(
            "/Users/dankerbadge/Documents/J.A.R.V.I.S/analysis/jarvis_study/clean_training_clips_deep_strict_filled_aggressive_clarity_strict"
        ),
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "/Users/dankerbadge/Documents/J.A.R.V.I.S/analysis/jarvis_study/clean_training_clips_deep_strict_filled_aggressive_clarity_strict_enhanced"
        ),
    )
    p.add_argument("--target-rms", type=float, default=-23.0)
    p.add_argument("--ceiling-dbfs", type=float, default=-1.2)
    p.add_argument("--fade-ms", type=float, default=12.0)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    in_dir: Path = args.input_dir
    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    for p in out_dir.glob("*.wav"):
        p.unlink()
    for n in ["enhance_manifest.csv", "README.md"]:
        q = out_dir / n
        if q.exists():
            q.unlink()

    clips = sorted(in_dir.glob("*.wav"))
    if not clips:
        raise RuntimeError(f"No WAV clips found in {in_dir}")

    rows: list[EnhanceRow] = []
    for clip in clips:
        x, sr = read_wav_mono(clip)
        in_rms = float(to_db(math.sqrt(float(np.mean(x * x)))))
        in_peak = float(to_db(float(np.max(np.abs(x))) + 1e-8))

        y = x - float(np.mean(x))
        y = highpass_first_order(y, sr, cutoff_hz=65.0)
        y, nr_db = spectral_denoise(y, sr)
        y, deess_db = dynamic_deesser(y, sr)
        y = gentle_presence_eq(y, sr)
        y = normalize_rms(y, target_dbfs=args.target_rms)
        y = soft_limiter(y, ceiling_dbfs=args.ceiling_dbfs)
        y = apply_fade(y, sr, ms=args.fade_ms)

        out_rms = float(to_db(math.sqrt(float(np.mean(y * y)))))
        out_peak = float(to_db(float(np.max(np.abs(y))) + 1e-8))

        write_wav_mono(out_dir / clip.name, y, sr)
        rows.append(
            EnhanceRow(
                clip_filename=clip.name,
                duration_sec=len(y) / sr,
                in_rms_dbfs=in_rms,
                out_rms_dbfs=out_rms,
                in_peak_dbfs=in_peak,
                out_peak_dbfs=out_peak,
                noise_reduction_db_est=nr_db,
                sibilance_reduction_db_est=deess_db,
            )
        )

    manifest = out_dir / "enhance_manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))

    avg_nr = float(np.mean([r.noise_reduction_db_est for r in rows]))
    avg_ds = float(np.mean([r.sibilance_reduction_db_est for r in rows]))
    total_sec = sum(r.duration_sec for r in rows)

    readme = out_dir / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# Enhanced Voice Pack",
                "",
                "Enhanced with conservative clarity-focused DSP.",
                "",
                "## Chain",
                "",
                "- DC removal + gentle 65Hz high-pass",
                "- Spectral denoise (conservative floor)",
                "- Dynamic de-esser (5.2k-9.2k)",
                "- Gentle presence EQ (+2k to +4.8k) and slight air control",
                "- RMS normalization and soft limiting",
                "",
                "## Result",
                "",
                f"- Clips: {len(rows)}",
                f"- Duration: {total_sec:.1f}s ({total_sec/60.0:.2f} min)",
                f"- Avg estimated noise reduction: {avg_nr:.2f} dB",
                f"- Avg estimated sibilance reduction: {avg_ds:.2f} dB",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Wrote enhanced clips: {out_dir}")
    print(f"Manifest: {manifest}")
    print(f"Summary: {readme}")


if __name__ == "__main__":
    main()
