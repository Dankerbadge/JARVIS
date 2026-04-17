#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import shutil
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

DB_EPS = 1e-12


@dataclass
class Metrics:
    rms_dbfs: float
    peak_dbfs: float
    clipped_pct: float
    silence_pct: float
    hiss_ratio: float
    presence_ratio: float
    spectral_flatness: float
    harmonicity: float
    centroid_hz: float
    clarity_score: float


@dataclass
class TargetProfile:
    rms_dbfs: float
    rms_mad: float
    hiss_ratio: float
    hiss_mad: float
    presence_ratio: float
    presence_mad: float
    spectral_flatness: float
    flat_mad: float
    harmonicity: float
    harm_mad: float
    centroid_hz: float
    centroid_mad: float


@dataclass
class TuneParams:
    presence_db: float
    hiss_db: float
    tilt_db: float
    deesser_db_reduction: float
    denoise_db_reduction: float
    continuity_gain_db: float


@dataclass
class DecisionRow:
    clip_filename: str
    source: str
    start_sec: float
    selected_variant: str
    reason: str
    orig_total_score: float
    tuned_total_score: float
    delta_total_score: float
    orig_clarity: float
    tuned_clarity: float
    delta_clarity: float
    orig_rms_dbfs: float
    tuned_rms_dbfs: float
    orig_peak_dbfs: float
    tuned_peak_dbfs: float
    orig_silence_pct: float
    tuned_silence_pct: float
    orig_hiss_ratio: float
    tuned_hiss_ratio: float
    orig_presence_ratio: float
    tuned_presence_ratio: float
    orig_flatness: float
    tuned_flatness: float
    orig_harmonicity: float
    tuned_harmonicity: float
    orig_centroid_hz: float
    tuned_centroid_hz: float
    clipped_pct_orig: float
    clipped_pct_tuned: float
    eq_presence_db: float
    eq_hiss_db: float
    eq_tilt_db: float
    deesser_db_reduction: float
    denoise_db_reduction: float
    continuity_gain_db: float


def to_db(v: float | np.ndarray) -> float | np.ndarray:
    return 20.0 * np.log10(np.maximum(v, DB_EPS))


def robust_mad(arr: np.ndarray, fallback: float) -> float:
    if len(arr) == 0:
        return fallback
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    return max(mad, fallback)


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


def frame_iter(x: np.ndarray, frame: int, hop: int):
    if len(x) < frame:
        yield x
        return
    for i in range(0, len(x) - frame + 1, hop):
        yield x[i : i + frame]


def frame_rms_db(x: np.ndarray, sr: int, frame_sec: float = 0.05, hop_sec: float = 0.025) -> np.ndarray:
    frame = max(1, int(sr * frame_sec))
    hop = max(1, int(sr * hop_sec))
    out = []
    for f in frame_iter(x, frame, hop):
        out.append(float(to_db(math.sqrt(float(np.mean(f * f))))))
    return np.array(out, dtype=np.float64)


def highpass_first_order(x: np.ndarray, sr: int, cutoff_hz: float = 60.0) -> np.ndarray:
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
        spec[:, i] = np.fft.rfft(x[s : s + n_fft] * win)
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
    reduction_strength: float = 0.78,
    floor: float = 0.14,
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

    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    shape = np.ones_like(freqs, dtype=np.float32)
    shape[freqs < 180] = 0.58
    shape[freqs > 10500] = 0.82
    shape = shape[:, None]

    ratio = (noise_prof * reduction_strength * shape) / np.maximum(mag, 1e-8)
    gain = np.clip(1.0 - ratio, floor, 1.0)
    gain = smooth2d(gain, kf=7, kt=5)

    out_spec = mag * gain * ph
    y = istft(out_spec, win=win, hop=hop)[: len(x)]

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
    max_reduction_db: float = 3.2,
) -> tuple[np.ndarray, float]:
    sib = bandpass_first_order(x, sr, band_lo, band_hi)
    body = bandpass_first_order(x, sr, 120.0, 4200.0)
    env_s = lowpass_first_order(np.abs(sib), sr, 18.0)
    env_b = lowpass_first_order(np.abs(body), sr, 18.0)
    ratio = env_s / np.maximum(env_b, 1e-5)

    over = np.maximum(0.0, ratio - threshold_ratio)
    if np.max(over) <= 0:
        return x, 0.0

    depth = np.clip(over / max(threshold_ratio, 1e-4), 0.0, 1.0)
    max_lin = 10 ** (-max_reduction_db / 20.0)
    gain = np.clip(1.0 - depth * (1.0 - max_lin), max_lin, 1.0)
    y = x - sib + (sib * gain)
    red_db = float(to_db(np.mean(np.abs(sib)) / max(np.mean(np.abs(sib * gain)), 1e-8)))
    return y.astype(np.float32), red_db


def apply_fade(x: np.ndarray, sr: int, ms: float = 10.0) -> np.ndarray:
    n = int(sr * (ms / 1000.0))
    if n <= 1 or len(x) < 2 * n:
        return x
    y = x.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    y[:n] *= ramp
    y[-n:] *= ramp[::-1]
    return y


def soft_limiter(x: np.ndarray, ceiling_dbfs: float = -1.15) -> np.ndarray:
    ceiling = 10 ** (ceiling_dbfs / 20.0)
    return (np.tanh(x / max(ceiling, 1e-6)) * ceiling).astype(np.float32)


def spectral_stats(x: np.ndarray, sr: int) -> tuple[float, float, float, float]:
    n_fft = 4096
    hop = n_fft // 2
    if len(x) < n_fft:
        return 0.0, 0.0, 1.0, 0.0

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
        return 0.0, 0.0, 1.0, 0.0

    s = np.mean(np.vstack(mags), axis=0)
    tot = float(np.sum(s) + DB_EPS)
    centroid = float(np.sum(freqs * s) / tot)

    def band(lo: float, hi: float) -> float:
        m = (freqs >= lo) & (freqs < hi)
        return float(np.sum(s[m]) / tot)

    body = band(80.0, 4000.0)
    hiss = band(8000.0, 16000.0)
    presence = band(2000.0, 5000.0)
    flat = float(np.median(np.array(flat_vals, dtype=np.float64)))
    return hiss / max(body, DB_EPS), presence, flat, centroid


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


def score_clarity(
    post_rms_dbfs: float,
    silence_pct: float,
    hiss_ratio: float,
    presence_ratio: float,
    flatness: float,
    harmonicity: float,
) -> float:
    score = 100.0
    if post_rms_dbfs < -25.5:
        score -= (abs(post_rms_dbfs + 25.5) * 3.0)
    if post_rms_dbfs > -20.0:
        score -= (abs(post_rms_dbfs + 20.0) * 2.0)
    if silence_pct > 12.0:
        score -= (silence_pct - 12.0) * 1.8
    if hiss_ratio > 0.55:
        score -= (hiss_ratio - 0.55) * 55.0
    if presence_ratio < 0.13:
        score -= (0.13 - presence_ratio) * 120.0
    if presence_ratio > 0.34:
        score -= (presence_ratio - 0.34) * 40.0
    if flatness > 0.27:
        score -= (flatness - 0.27) * 120.0
    score += max(0.0, (harmonicity - 0.24) * 35.0)
    return float(score)


def analyze_metrics(x: np.ndarray, sr: int) -> Metrics:
    rms = math.sqrt(float(np.mean(x * x)))
    rms_db = float(to_db(rms))
    peak = float(np.max(np.abs(x)))
    peak_db = float(to_db(peak + DB_EPS))
    clip_pct = float(np.mean(np.abs(x) > 0.995) * 100.0)
    fr = frame_rms_db(x, sr)
    silence_pct = float(np.mean(fr < -45.0) * 100.0)
    hiss_ratio, presence_ratio, flatness, centroid = spectral_stats(x, sr)
    harm = harmonicity_ac_peak(x, sr)
    clarity = score_clarity(
        post_rms_dbfs=rms_db,
        silence_pct=silence_pct,
        hiss_ratio=hiss_ratio,
        presence_ratio=presence_ratio,
        flatness=flatness,
        harmonicity=harm,
    )
    return Metrics(
        rms_dbfs=rms_db,
        peak_dbfs=peak_db,
        clipped_pct=clip_pct,
        silence_pct=silence_pct,
        hiss_ratio=hiss_ratio,
        presence_ratio=presence_ratio,
        spectral_flatness=flatness,
        harmonicity=harm,
        centroid_hz=centroid,
        clarity_score=clarity,
    )


def build_target_profile(metrics: list[Metrics]) -> TargetProfile:
    arr_rms = np.array([m.rms_dbfs for m in metrics], dtype=np.float64)
    arr_hiss = np.array([m.hiss_ratio for m in metrics], dtype=np.float64)
    arr_presence = np.array([m.presence_ratio for m in metrics], dtype=np.float64)
    arr_flat = np.array([m.spectral_flatness for m in metrics], dtype=np.float64)
    arr_harm = np.array([m.harmonicity for m in metrics], dtype=np.float64)
    arr_cent = np.array([m.centroid_hz for m in metrics], dtype=np.float64)
    return TargetProfile(
        rms_dbfs=float(np.median(arr_rms)),
        rms_mad=robust_mad(arr_rms, fallback=1.2),
        hiss_ratio=float(np.median(arr_hiss)),
        hiss_mad=robust_mad(arr_hiss, fallback=0.02),
        presence_ratio=float(np.median(arr_presence)),
        presence_mad=robust_mad(arr_presence, fallback=0.015),
        spectral_flatness=float(np.median(arr_flat)),
        flat_mad=robust_mad(arr_flat, fallback=0.01),
        harmonicity=float(np.median(arr_harm)),
        harm_mad=robust_mad(arr_harm, fallback=0.02),
        centroid_hz=float(np.median(arr_cent)),
        centroid_mad=robust_mad(arr_cent, fallback=140.0),
    )


def profile_distance(m: Metrics, t: TargetProfile) -> float:
    d = []
    d.append(abs(m.hiss_ratio - t.hiss_ratio) / max(t.hiss_mad, 0.015))
    d.append(abs(m.presence_ratio - t.presence_ratio) / max(t.presence_mad, 0.012))
    d.append(abs(m.spectral_flatness - t.spectral_flatness) / max(t.flat_mad, 0.008))
    d.append(abs(m.harmonicity - t.harmonicity) / max(t.harm_mad, 0.018))
    d.append(abs(m.centroid_hz - t.centroid_hz) / max(t.centroid_mad, 120.0))
    return float(np.mean(np.clip(np.array(d, dtype=np.float64), 0.0, 4.0)))


def total_score(m: Metrics, t: TargetProfile) -> float:
    pd = profile_distance(m, t)
    penalty = 0.0
    if m.peak_dbfs > -0.75:
        penalty += (m.peak_dbfs + 0.75) * 4.0
    if m.clipped_pct > 0.08:
        penalty += (m.clipped_pct - 0.08) * 2.5
    return float(m.clarity_score - (6.0 * pd) - penalty)


def normalize_rms(x: np.ndarray, target_dbfs: float, gain_db: float | None = None) -> tuple[np.ndarray, float]:
    rms = math.sqrt(float(np.mean(x * x)))
    if rms < DB_EPS:
        return x, 0.0
    if gain_db is None:
        gain = (10 ** (target_dbfs / 20.0)) / rms
        gain_db_use = float(to_db(gain))
    else:
        gain = 10 ** (gain_db / 20.0)
        gain_db_use = gain_db
    return (x * gain).astype(np.float32), gain_db_use


def continuity_smooth_gains(raw_gain_db: list[float], clamp_db: float = 1.6, alpha: float = 0.35) -> list[float]:
    if not raw_gain_db:
        return []
    med = float(np.median(np.array(raw_gain_db, dtype=np.float64)))
    out = [float(np.clip(raw_gain_db[0], med - clamp_db, med + clamp_db))]
    for g in raw_gain_db[1:]:
        sm = ((1.0 - alpha) * out[-1]) + (alpha * g)
        out.append(float(np.clip(sm, med - clamp_db, med + clamp_db)))
    return out


def parse_clip_info(path: Path) -> tuple[str, float]:
    stem = path.stem.lower()
    parts = stem.split("_")
    if len(parts) < 5:
        return "unknown", 0.0
    source = "JARVIS_II" if parts[1] == "ii" else "JARVIS_1"
    try:
        start_sec = int(parts[3]) / 1000.0
    except ValueError:
        start_sec = 0.0
    return source, start_sec


def clear_output_dir(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.glob("*.wav"):
        p.unlink()
    for name in ["finetune_selection.csv", "finetune_playlist.m3u", "README.md"]:
        q = out_dir / name
        if q.exists():
            q.unlink()


def tune_clip_once(x: np.ndarray, sr: int, m: Metrics, target: TargetProfile) -> tuple[np.ndarray, TuneParams]:
    y = (x - np.mean(x)).astype(np.float32)
    y = highpass_first_order(y, sr, cutoff_hz=60.0)

    # Match articulation and hiss profile with very constrained EQ moves.
    presence = bandpass_first_order(y, sr, 2000.0, 5000.0)
    hiss = bandpass_first_order(y, sr, 8000.0, 15000.0)
    air = highpass_first_order(y, sr, 9500.0)

    presence_db = float(np.clip((target.presence_ratio - m.presence_ratio) * 60.0, -1.4, 1.9))
    hiss_db = float(np.clip(-(m.hiss_ratio - target.hiss_ratio) * 30.0, -2.8, 0.6))
    tilt_db = float(np.clip(-(m.centroid_hz - target.centroid_hz) / 900.0, -1.0, 1.0))

    p_gain = 10 ** (presence_db / 20.0)
    h_gain = 10 ** (hiss_db / 20.0)
    t_gain = 10 ** (tilt_db / 20.0)

    y = (y - presence + (presence * p_gain)).astype(np.float32)
    y = (y - hiss + (hiss * h_gain)).astype(np.float32)
    y = (y + ((t_gain - 1.0) * air)).astype(np.float32)

    # Adaptive de-esser and conditional denoise to reduce brittle highs.
    deesser_threshold = 0.24 if m.hiss_ratio <= target.hiss_ratio else 0.20
    deesser_strength = 2.6 if m.hiss_ratio <= target.hiss_ratio else 3.8
    y, deess_db = dynamic_deesser(
        y,
        sr,
        threshold_ratio=deesser_threshold,
        max_reduction_db=deesser_strength,
    )

    nr_db = 0.0
    if m.hiss_ratio > (target.hiss_ratio + 0.015) or m.spectral_flatness > (target.spectral_flatness + 0.008):
        y, nr_db = spectral_denoise(y, sr, reduction_strength=0.82, floor=0.14)

    return y.astype(np.float32), TuneParams(
        presence_db=presence_db,
        hiss_db=hiss_db,
        tilt_db=tilt_db,
        deesser_db_reduction=deess_db,
        denoise_db_reduction=nr_db,
        continuity_gain_db=0.0,
    )


def hard_guard_failure(orig: Metrics, tuned: Metrics, min_delta_score: float, delta_score: float) -> str | None:
    if tuned.harmonicity < (orig.harmonicity - 0.012):
        return "harmonicity_drop"
    if tuned.spectral_flatness > (orig.spectral_flatness + 0.015):
        return "flatness_increase"
    if tuned.silence_pct > (orig.silence_pct + 2.0):
        return "silence_increase"
    if tuned.hiss_ratio > (orig.hiss_ratio + 0.030):
        return "hiss_increase"
    if tuned.peak_dbfs > -0.35:
        return "peak_too_hot"
    if tuned.clipped_pct > max(orig.clipped_pct + 0.02, 0.08):
        return "clipping_risk"
    if delta_score < min_delta_score:
        return "score_not_improved"
    return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fine tune hybrid voice pack with regression-safe A/B selection.")
    p.add_argument(
        "--input-dir",
        type=Path,
        default=Path(
            "/Users/dankerbadge/Documents/J.A.R.V.I.S/analysis/jarvis_study/clean_training_clips_deep_strict_filled_aggressive_clarity_strict_hybrid"
        ),
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "/Users/dankerbadge/Documents/J.A.R.V.I.S/analysis/jarvis_study/clean_training_clips_deep_strict_filled_aggressive_clarity_strict_hybrid_finetuned"
        ),
    )
    p.add_argument("--target-rms", type=float, default=-23.0)
    p.add_argument("--ceiling-dbfs", type=float, default=-1.15)
    p.add_argument("--fade-ms", type=float, default=10.0)
    p.add_argument("--min-delta-score", type=float, default=0.05)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    in_dir = args.input_dir
    out_dir = args.output_dir
    clear_output_dir(out_dir)

    clips = sorted(in_dir.glob("*.wav"))
    if not clips:
        raise RuntimeError(f"No WAV files found in: {in_dir}")

    originals = []
    for p in clips:
        x, sr = read_wav_mono(p)
        source, start_sec = parse_clip_info(p)
        originals.append({"path": p, "x": x, "sr": sr, "source": source, "start_sec": start_sec})

    metrics_orig = [analyze_metrics(o["x"], o["sr"]) for o in originals]
    target = build_target_profile(metrics_orig)

    # First pass: adaptive cleanup/EQ.
    for o, m in zip(originals, metrics_orig):
        y, params = tune_clip_once(o["x"], o["sr"], m, target)
        o["y_pre"] = y
        o["params"] = params

    # Second pass: continuity-safe gain smoothing by source.
    for source in sorted({o["source"] for o in originals}):
        idx = [i for i, o in enumerate(originals) if o["source"] == source]
        idx.sort(key=lambda i: originals[i]["start_sec"])

        raw = []
        for i in idx:
            rms = math.sqrt(float(np.mean(originals[i]["y_pre"] * originals[i]["y_pre"])))
            raw.append(float(args.target_rms - float(to_db(rms + DB_EPS))))
        smooth = continuity_smooth_gains(raw_gain_db=raw, clamp_db=1.6, alpha=0.35)

        for k, i in enumerate(idx):
            y = originals[i]["y_pre"]
            y, _ = normalize_rms(y, target_dbfs=args.target_rms, gain_db=smooth[k])
            y = soft_limiter(y, ceiling_dbfs=args.ceiling_dbfs)
            y = apply_fade(y, originals[i]["sr"], ms=args.fade_ms)
            originals[i]["y_tuned"] = y
            originals[i]["params"].continuity_gain_db = smooth[k]

    decisions: list[DecisionRow] = []
    tuned_kept = 0
    for o, mo in zip(originals, metrics_orig):
        mt = analyze_metrics(o["y_tuned"], o["sr"])
        score_o = total_score(mo, target)
        score_t = total_score(mt, target)
        delta = score_t - score_o

        guard = hard_guard_failure(mo, mt, args.min_delta_score, delta)
        use_tuned = guard is None
        reason = "improved_and_safe" if use_tuned else f"kept_original_{guard}"

        dst = out_dir / o["path"].name
        if use_tuned:
            write_wav_mono(dst, o["y_tuned"], o["sr"])
            tuned_kept += 1
        else:
            shutil.copy2(o["path"], dst)

        p = o["params"]
        decisions.append(
            DecisionRow(
                clip_filename=o["path"].name,
                source=o["source"],
                start_sec=o["start_sec"],
                selected_variant=("tuned" if use_tuned else "original"),
                reason=reason,
                orig_total_score=score_o,
                tuned_total_score=score_t,
                delta_total_score=delta,
                orig_clarity=mo.clarity_score,
                tuned_clarity=mt.clarity_score,
                delta_clarity=(mt.clarity_score - mo.clarity_score),
                orig_rms_dbfs=mo.rms_dbfs,
                tuned_rms_dbfs=mt.rms_dbfs,
                orig_peak_dbfs=mo.peak_dbfs,
                tuned_peak_dbfs=mt.peak_dbfs,
                orig_silence_pct=mo.silence_pct,
                tuned_silence_pct=mt.silence_pct,
                orig_hiss_ratio=mo.hiss_ratio,
                tuned_hiss_ratio=mt.hiss_ratio,
                orig_presence_ratio=mo.presence_ratio,
                tuned_presence_ratio=mt.presence_ratio,
                orig_flatness=mo.spectral_flatness,
                tuned_flatness=mt.spectral_flatness,
                orig_harmonicity=mo.harmonicity,
                tuned_harmonicity=mt.harmonicity,
                orig_centroid_hz=mo.centroid_hz,
                tuned_centroid_hz=mt.centroid_hz,
                clipped_pct_orig=mo.clipped_pct,
                clipped_pct_tuned=mt.clipped_pct,
                eq_presence_db=p.presence_db,
                eq_hiss_db=p.hiss_db,
                eq_tilt_db=p.tilt_db,
                deesser_db_reduction=p.deesser_db_reduction,
                denoise_db_reduction=p.denoise_db_reduction,
                continuity_gain_db=p.continuity_gain_db,
            )
        )

    # Persist decision table sorted by timeline.
    decisions.sort(key=lambda r: (r.source, r.start_sec))
    csv_path = out_dir / "finetune_selection.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(decisions[0]).keys()))
        writer.writeheader()
        for d in decisions:
            writer.writerow(asdict(d))

    # Playlist.
    playlist = out_dir / "finetune_playlist.m3u"
    lines = ["#EXTM3U"]
    for d in decisions:
        lines.append(
            f"#EXTINF:-1,{d.source} start={d.start_sec:.3f}s sel={d.selected_variant} "
            f"dScore={d.delta_total_score:+.3f} dClr={d.delta_clarity:+.3f}"
        )
        lines.append(d.clip_filename)
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Summary.
    arr_delta_score = np.array([d.delta_total_score for d in decisions], dtype=np.float64)
    arr_delta_clarity = np.array([d.delta_clarity for d in decisions], dtype=np.float64)
    arr_orig_score = np.array([d.orig_total_score for d in decisions], dtype=np.float64)
    arr_final_score = np.array(
        [(d.tuned_total_score if d.selected_variant == "tuned" else d.orig_total_score) for d in decisions],
        dtype=np.float64,
    )
    arr_orig_clarity = np.array([d.orig_clarity for d in decisions], dtype=np.float64)
    arr_final_clarity = np.array(
        [(d.tuned_clarity if d.selected_variant == "tuned" else d.orig_clarity) for d in decisions],
        dtype=np.float64,
    )

    readme = out_dir / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# Fine-Tuned Hybrid Pack",
                "",
                "Regression-safe fine-tuning pass on the hybrid clarity-strict pack.",
                "",
                "## Result",
                "",
                f"- Input clips: {len(decisions)}",
                f"- Tuned selected: {tuned_kept}",
                f"- Original retained: {len(decisions) - tuned_kept}",
                f"- Mean raw delta total score (tuned - original): {float(np.mean(arr_delta_score)):+.3f}",
                f"- Median raw delta total score (tuned - original): {float(np.median(arr_delta_score)):+.3f}",
                f"- Mean final total score gain vs original baseline: {float(np.mean(arr_final_score - arr_orig_score)):+.3f}",
                f"- Mean final clarity gain vs original baseline: {float(np.mean(arr_final_clarity - arr_orig_clarity)):+.3f}",
                "",
                "## Continuity",
                "",
                "- Gain smoothing is applied per source in timeline order before final A/B selection.",
                "- Per-clip hard guards block regressions in harmonicity, flatness, hiss, silence, and peak/clipping risk.",
                "",
                "## Files",
                "",
                "- `finetune_selection.csv`",
                "- `finetune_playlist.m3u`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Fine-tuned pack written to: {out_dir}")
    print(f"Tuned selected: {tuned_kept}/{len(decisions)}")


if __name__ == "__main__":
    main()
