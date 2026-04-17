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
class Profile:
    rms_dbfs: float
    rms_mad: float
    hiss_ratio: float
    hiss_mad: float
    presence_ratio: float
    presence_mad: float
    flatness: float
    flat_mad: float
    harmonicity: float
    harm_mad: float
    centroid_hz: float
    centroid_mad: float


@dataclass
class Params:
    presence_db: float
    hiss_db: float
    tilt_db: float
    deesser_db: float
    denoise_db: float
    continuity_gain_db: float


@dataclass
class Decision:
    clip_filename: str
    source: str
    start_sec: float
    selected: str
    reason: str
    orig_score: float
    polish_score: float
    delta_score: float
    orig_clarity: float
    polish_clarity: float
    delta_clarity: float
    orig_rms_dbfs: float
    polish_rms_dbfs: float
    orig_peak_dbfs: float
    polish_peak_dbfs: float
    orig_silence_pct: float
    polish_silence_pct: float
    orig_hiss_ratio: float
    polish_hiss_ratio: float
    orig_presence_ratio: float
    polish_presence_ratio: float
    orig_flatness: float
    polish_flatness: float
    orig_harmonicity: float
    polish_harmonicity: float
    orig_centroid_hz: float
    polish_centroid_hz: float
    orig_clipped_pct: float
    polish_clipped_pct: float
    presence_db: float
    hiss_db: float
    tilt_db: float
    deesser_db: float
    denoise_db: float
    continuity_gain_db: float


def to_db(v: float | np.ndarray) -> float | np.ndarray:
    return 20.0 * np.log10(np.maximum(v, DB_EPS))


def robust_mad(v: np.ndarray, fallback: float) -> float:
    if len(v) == 0:
        return fallback
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med)))
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
    vals = []
    for f in frame_iter(x, frame, hop):
        vals.append(float(to_db(math.sqrt(float(np.mean(f * f))))))
    return np.array(vals, dtype=np.float64)


def highpass_first_order(x: np.ndarray, sr: int, cutoff_hz: float = 58.0) -> np.ndarray:
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
        k = np.ones(kf, dtype=np.float32) / float(kf)
        y = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), axis=0, arr=y)
    if kt > 1:
        k = np.ones(kt, dtype=np.float32) / float(kt)
        y = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), axis=1, arr=y)
    return y


def spectral_denoise_light(
    x: np.ndarray,
    sr: int,
    reduction_strength: float = 0.56,
    floor: float = 0.34,
) -> tuple[np.ndarray, float]:
    n_fft = 2048
    hop = 512
    spec, win = stft(x, n_fft=n_fft, hop=hop)
    mag = np.abs(spec)
    ph = np.exp(1j * np.angle(spec))

    e = np.mean(mag, axis=0)
    thr = np.quantile(e, 0.16)
    noise_idx = e <= thr
    if not np.any(noise_idx):
        noise_idx = e <= np.median(e)
    noise = np.median(mag[:, noise_idx], axis=1, keepdims=True)

    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    shape = np.ones_like(freqs, dtype=np.float32)
    shape[freqs < 220] = 0.62
    shape[freqs > 9800] = 0.82
    shape = shape[:, None]

    ratio = (noise * reduction_strength * shape) / np.maximum(mag, 1e-8)
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
    threshold_ratio: float = 0.225,
    max_reduction_db: float = 2.3,
) -> tuple[np.ndarray, float]:
    sib = bandpass_first_order(x, sr, 5200.0, 9200.0)
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


def apply_fade(x: np.ndarray, sr: int, ms: float = 8.0) -> np.ndarray:
    n = int(sr * (ms / 1000.0))
    if n <= 1 or len(x) < 2 * n:
        return x
    y = x.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    y[:n] *= ramp
    y[-n:] *= ramp[::-1]
    return y


def soft_limiter(x: np.ndarray, ceiling_dbfs: float = -1.1) -> np.ndarray:
    ceiling = 10 ** (ceiling_dbfs / 20.0)
    return (np.tanh(x / max(ceiling, 1e-6)) * ceiling).astype(np.float32)


def normalize_rms(x: np.ndarray, target_dbfs: float, forced_gain_db: float | None = None) -> tuple[np.ndarray, float]:
    rms = math.sqrt(float(np.mean(x * x)))
    if rms < DB_EPS:
        return x, 0.0
    if forced_gain_db is None:
        gain = (10 ** (target_dbfs / 20.0)) / rms
        gain_db = float(to_db(gain))
    else:
        gain = 10 ** (forced_gain_db / 20.0)
        gain_db = forced_gain_db
    return (x * gain).astype(np.float32), gain_db


def spectral_stats(x: np.ndarray, sr: int) -> tuple[float, float, float, float]:
    n_fft = 4096
    hop = n_fft // 2
    if len(x) < n_fft:
        return 0.0, 0.0, 1.0, 0.0
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    win = np.hanning(n_fft).astype(np.float32)
    mags = []
    flats = []
    for f in frame_iter(x, n_fft, hop):
        rms_db = float(to_db(math.sqrt(float(np.mean(f * f)))))
        if rms_db < -40.0:
            continue
        m = np.abs(np.fft.rfft(f * win)) + 1e-12
        mags.append(m)
        gm = float(np.exp(np.mean(np.log(m))))
        am = float(np.mean(m))
        flats.append(gm / max(am, 1e-12))
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
    flatness = float(np.median(np.array(flats, dtype=np.float64)))
    return hiss / max(body, DB_EPS), presence, flatness, centroid


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


def clarity_score(rms_dbfs: float, silence_pct: float, hiss_ratio: float, presence: float, flatness: float, harm: float) -> float:
    score = 100.0
    if rms_dbfs < -25.5:
        score -= (abs(rms_dbfs + 25.5) * 3.0)
    if rms_dbfs > -20.0:
        score -= (abs(rms_dbfs + 20.0) * 2.0)
    if silence_pct > 12.0:
        score -= (silence_pct - 12.0) * 1.8
    if hiss_ratio > 0.55:
        score -= (hiss_ratio - 0.55) * 55.0
    if presence < 0.13:
        score -= (0.13 - presence) * 120.0
    if presence > 0.34:
        score -= (presence - 0.34) * 40.0
    if flatness > 0.27:
        score -= (flatness - 0.27) * 120.0
    score += max(0.0, (harm - 0.24) * 35.0)
    return float(score)


def analyze(x: np.ndarray, sr: int) -> Metrics:
    rms = math.sqrt(float(np.mean(x * x)))
    peak = float(np.max(np.abs(x)))
    clipped = float(np.mean(np.abs(x) > 0.995) * 100.0)
    fr = frame_rms_db(x, sr)
    silence = float(np.mean(fr < -45.0) * 100.0)
    hiss, presence, flatness, centroid = spectral_stats(x, sr)
    harm = harmonicity_ac_peak(x, sr)
    clarity = clarity_score(
        rms_dbfs=float(to_db(rms)),
        silence_pct=silence,
        hiss_ratio=hiss,
        presence=presence,
        flatness=flatness,
        harm=harm,
    )
    return Metrics(
        rms_dbfs=float(to_db(rms)),
        peak_dbfs=float(to_db(peak + DB_EPS)),
        clipped_pct=clipped,
        silence_pct=silence,
        hiss_ratio=hiss,
        presence_ratio=presence,
        spectral_flatness=flatness,
        harmonicity=harm,
        centroid_hz=centroid,
        clarity_score=clarity,
    )


def make_profile(metrics: list[Metrics]) -> Profile:
    rms = np.array([m.rms_dbfs for m in metrics], dtype=np.float64)
    hiss = np.array([m.hiss_ratio for m in metrics], dtype=np.float64)
    pres = np.array([m.presence_ratio for m in metrics], dtype=np.float64)
    flat = np.array([m.spectral_flatness for m in metrics], dtype=np.float64)
    harm = np.array([m.harmonicity for m in metrics], dtype=np.float64)
    cent = np.array([m.centroid_hz for m in metrics], dtype=np.float64)
    return Profile(
        rms_dbfs=float(np.median(rms)),
        rms_mad=robust_mad(rms, 1.1),
        hiss_ratio=float(np.median(hiss)),
        hiss_mad=robust_mad(hiss, 0.018),
        presence_ratio=float(np.median(pres)),
        presence_mad=robust_mad(pres, 0.012),
        flatness=float(np.median(flat)),
        flat_mad=robust_mad(flat, 0.008),
        harmonicity=float(np.median(harm)),
        harm_mad=robust_mad(harm, 0.018),
        centroid_hz=float(np.median(cent)),
        centroid_mad=robust_mad(cent, 120.0),
    )


def profile_distance(m: Metrics, p: Profile) -> float:
    vals = np.array(
        [
            abs(m.hiss_ratio - p.hiss_ratio) / max(p.hiss_mad, 0.014),
            abs(m.presence_ratio - p.presence_ratio) / max(p.presence_mad, 0.010),
            abs(m.spectral_flatness - p.flatness) / max(p.flat_mad, 0.006),
            abs(m.harmonicity - p.harmonicity) / max(p.harm_mad, 0.016),
            abs(m.centroid_hz - p.centroid_hz) / max(p.centroid_mad, 100.0),
        ],
        dtype=np.float64,
    )
    return float(np.mean(np.clip(vals, 0.0, 4.0)))


def total_score(m: Metrics, p: Profile) -> float:
    pd = profile_distance(m, p)
    pen = 0.0
    if m.peak_dbfs > -0.7:
        pen += (m.peak_dbfs + 0.7) * 3.0
    if m.clipped_pct > 0.08:
        pen += (m.clipped_pct - 0.08) * 2.2
    return float(m.clarity_score - (5.0 * pd) - pen)


def parse_clip_info(path: Path) -> tuple[str, float]:
    parts = path.stem.lower().split("_")
    if len(parts) < 5:
        return "unknown", 0.0
    source = "JARVIS_II" if parts[1] == "ii" else "JARVIS_1"
    try:
        start_sec = int(parts[3]) / 1000.0
    except ValueError:
        start_sec = 0.0
    return source, start_sec


def smooth_gains(raw: list[float], clamp_db: float = 0.9, alpha: float = 0.22) -> list[float]:
    if not raw:
        return []
    med = float(np.median(np.array(raw, dtype=np.float64)))
    out = [float(np.clip(raw[0], med - clamp_db, med + clamp_db))]
    for g in raw[1:]:
        v = ((1.0 - alpha) * out[-1]) + (alpha * g)
        out.append(float(np.clip(v, med - clamp_db, med + clamp_db)))
    return out


def polish_once(x: np.ndarray, sr: int, m: Metrics, p: Profile) -> tuple[np.ndarray, Params]:
    y = (x - np.mean(x)).astype(np.float32)
    y = highpass_first_order(y, sr, cutoff_hz=58.0)

    presence = bandpass_first_order(y, sr, 2000.0, 5000.0)
    hiss = bandpass_first_order(y, sr, 7600.0, 14500.0)
    air = highpass_first_order(y, sr, 9000.0)

    presence_db = float(np.clip((p.presence_ratio - m.presence_ratio) * 35.0, -0.9, 1.1))
    hiss_db = float(np.clip(-(m.hiss_ratio - p.hiss_ratio) * 16.0, -1.2, 0.4))
    tilt_db = float(np.clip((p.centroid_hz - m.centroid_hz) / 1800.0, -0.5, 0.5))

    pg = 10 ** (presence_db / 20.0)
    hg = 10 ** (hiss_db / 20.0)
    tg = 10 ** (tilt_db / 20.0)

    y = (y - presence + (presence * pg)).astype(np.float32)
    y = (y - hiss + (hiss * hg)).astype(np.float32)
    y = (y + ((tg - 1.0) * air)).astype(np.float32)

    deess_thr = 0.228 if m.hiss_ratio <= p.hiss_ratio else 0.214
    deess_max = 2.0 if m.hiss_ratio <= p.hiss_ratio else 2.6
    y, deess_db = dynamic_deesser(y, sr, threshold_ratio=deess_thr, max_reduction_db=deess_max)

    denoise_db = 0.0
    if m.hiss_ratio > (p.hiss_ratio + 0.030) and m.silence_pct <= 9.0:
        y, denoise_db = spectral_denoise_light(y, sr, reduction_strength=0.56, floor=0.34)

    return y.astype(np.float32), Params(
        presence_db=presence_db,
        hiss_db=hiss_db,
        tilt_db=tilt_db,
        deesser_db=deess_db,
        denoise_db=denoise_db,
        continuity_gain_db=0.0,
    )


def guard_reason(orig: Metrics, pol: Metrics, delta_score: float, delta_clarity: float, min_delta: float) -> str | None:
    if pol.silence_pct > (orig.silence_pct + 1.0):
        return "silence_up"
    if pol.harmonicity < (orig.harmonicity - 0.008):
        return "harm_down"
    if pol.spectral_flatness > (orig.spectral_flatness + 0.010):
        return "flat_up"
    if pol.hiss_ratio > (orig.hiss_ratio + 0.020):
        return "hiss_up"
    if pol.peak_dbfs > -0.35:
        return "peak_hot"
    if pol.clipped_pct > max(orig.clipped_pct + 0.015, 0.08):
        return "clip_risk"
    if delta_score < min_delta and delta_clarity < 0.08:
        return "not_better"
    return None


def clear_output(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.glob("*.wav"):
        p.unlink()
    for n in ["polish_selection.csv", "polish_playlist.m3u", "README.md"]:
        q = out_dir / n
        if q.exists():
            q.unlink()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Final polish pass with strict non-regression A/B selection.")
    p.add_argument(
        "--input-dir",
        type=Path,
        default=Path(
            "/Users/dankerbadge/Documents/J.A.R.V.I.S/analysis/jarvis_study/clean_training_clips_deep_strict_filled_aggressive_clarity_strict_hybrid_finetuned"
        ),
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "/Users/dankerbadge/Documents/J.A.R.V.I.S/analysis/jarvis_study/clean_training_clips_deep_strict_filled_aggressive_clarity_strict_hybrid_finetuned_polished"
        ),
    )
    p.add_argument("--ceiling-dbfs", type=float, default=-1.1)
    p.add_argument("--fade-ms", type=float, default=8.0)
    p.add_argument("--min-delta-score", type=float, default=0.02)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    in_dir = args.input_dir
    out_dir = args.output_dir
    clear_output(out_dir)

    clips = sorted(in_dir.glob("*.wav"))
    if not clips:
        raise RuntimeError(f"No clips in: {in_dir}")

    data = []
    for p in clips:
        x, sr = read_wav_mono(p)
        source, start = parse_clip_info(p)
        data.append({"path": p, "x": x, "sr": sr, "source": source, "start": start})

    orig_metrics = [analyze(d["x"], d["sr"]) for d in data]
    prof = make_profile(orig_metrics)
    target_rms = prof.rms_dbfs

    for d, m in zip(data, orig_metrics):
        y, params = polish_once(d["x"], d["sr"], m, prof)
        d["y_pre"] = y
        d["params"] = params

    # Continuity-safe gain smoothing by source and timeline.
    for source in sorted({d["source"] for d in data}):
        idx = [i for i, d in enumerate(data) if d["source"] == source]
        idx.sort(key=lambda i: data[i]["start"])

        raw_gain = []
        for i in idx:
            rms = math.sqrt(float(np.mean(data[i]["y_pre"] * data[i]["y_pre"])))
            raw_gain.append(float(target_rms - float(to_db(rms + DB_EPS))))
        smooth = smooth_gains(raw_gain, clamp_db=0.9, alpha=0.22)

        for k, i in enumerate(idx):
            y = data[i]["y_pre"]
            y, gdb = normalize_rms(y, target_rms, forced_gain_db=smooth[k])
            y = soft_limiter(y, ceiling_dbfs=args.ceiling_dbfs)
            y = apply_fade(y, data[i]["sr"], ms=args.fade_ms)
            data[i]["y_pol"] = y
            data[i]["params"].continuity_gain_db = gdb

    decisions: list[Decision] = []
    selected_polish = 0
    for d, mo in zip(data, orig_metrics):
        mp = analyze(d["y_pol"], d["sr"])
        so = total_score(mo, prof)
        sp = total_score(mp, prof)
        ds = sp - so
        dc = mp.clarity_score - mo.clarity_score
        why = guard_reason(mo, mp, ds, dc, args.min_delta_score)
        keep_pol = why is None

        out_file = out_dir / d["path"].name
        if keep_pol:
            write_wav_mono(out_file, d["y_pol"], d["sr"])
            selected_polish += 1
            reason = "polished_safe_improvement"
        else:
            shutil.copy2(d["path"], out_file)
            reason = f"kept_input_{why}"

        p = d["params"]
        decisions.append(
            Decision(
                clip_filename=d["path"].name,
                source=d["source"],
                start_sec=d["start"],
                selected=("polished" if keep_pol else "input"),
                reason=reason,
                orig_score=so,
                polish_score=sp,
                delta_score=ds,
                orig_clarity=mo.clarity_score,
                polish_clarity=mp.clarity_score,
                delta_clarity=dc,
                orig_rms_dbfs=mo.rms_dbfs,
                polish_rms_dbfs=mp.rms_dbfs,
                orig_peak_dbfs=mo.peak_dbfs,
                polish_peak_dbfs=mp.peak_dbfs,
                orig_silence_pct=mo.silence_pct,
                polish_silence_pct=mp.silence_pct,
                orig_hiss_ratio=mo.hiss_ratio,
                polish_hiss_ratio=mp.hiss_ratio,
                orig_presence_ratio=mo.presence_ratio,
                polish_presence_ratio=mp.presence_ratio,
                orig_flatness=mo.spectral_flatness,
                polish_flatness=mp.spectral_flatness,
                orig_harmonicity=mo.harmonicity,
                polish_harmonicity=mp.harmonicity,
                orig_centroid_hz=mo.centroid_hz,
                polish_centroid_hz=mp.centroid_hz,
                orig_clipped_pct=mo.clipped_pct,
                polish_clipped_pct=mp.clipped_pct,
                presence_db=p.presence_db,
                hiss_db=p.hiss_db,
                tilt_db=p.tilt_db,
                deesser_db=p.deesser_db,
                denoise_db=p.denoise_db,
                continuity_gain_db=p.continuity_gain_db,
            )
        )

    decisions.sort(key=lambda r: (r.source, r.start_sec))
    csv_path = out_dir / "polish_selection.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(decisions[0]).keys()))
        writer.writeheader()
        for row in decisions:
            writer.writerow(asdict(row))

    playlist = out_dir / "polish_playlist.m3u"
    lines = ["#EXTM3U"]
    for r in decisions:
        lines.append(
            f"#EXTINF:-1,{r.source} start={r.start_sec:.3f}s sel={r.selected} "
            f"dScore={r.delta_score:+.3f} dClr={r.delta_clarity:+.3f}"
        )
        lines.append(r.clip_filename)
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")

    raw_delta = np.array([r.delta_score for r in decisions], dtype=np.float64)
    final_gain = np.array([r.delta_score if r.selected == "polished" else 0.0 for r in decisions], dtype=np.float64)
    final_clarity = np.array([r.delta_clarity if r.selected == "polished" else 0.0 for r in decisions], dtype=np.float64)

    readme = out_dir / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# Polished Finetuned Pack",
                "",
                "Final micro-polish pass with non-regression guards and continuity-safe gain smoothing.",
                "",
                "## Result",
                "",
                f"- Input clips: {len(decisions)}",
                f"- Polished selected: {selected_polish}",
                f"- Input retained: {len(decisions) - selected_polish}",
                f"- Mean raw delta score (polish-input): {float(np.mean(raw_delta)):+.3f}",
                f"- Median raw delta score (polish-input): {float(np.median(raw_delta)):+.3f}",
                f"- Mean final score gain vs input baseline: {float(np.mean(final_gain)):+.3f}",
                f"- Mean final clarity gain vs input baseline: {float(np.mean(final_clarity)):+.3f}",
                "",
                "## Notes",
                "",
                "- This pass uses smaller EQ/de-esser moves than finetune to preserve continuity.",
                "- Denoise runs only on high-hiss clips and is intentionally conservative.",
                "- A/B guards block silence, harmonicity, and clipping regressions.",
                "",
                "## Files",
                "",
                "- `polish_selection.csv`",
                "- `polish_playlist.m3u`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Polished pack written to: {out_dir}")
    print(f"Polished selected: {selected_polish}/{len(decisions)}")


if __name__ == "__main__":
    main()
