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
class SourceConfig:
    name: str
    path: Path
    max_count: int
    min_count: int
    avoid_first_n_sec: float


@dataclass
class WindowCandidate:
    source: str
    start_sec: float
    end_sec: float
    duration_sec: float
    rms_dbfs: float
    silence_pct: float
    hf_to_body_ratio: float
    low_mid_ratio: float
    spectral_centroid_hz: float
    pitch_hz: float
    spectral_cosine: float
    spectral_vector: np.ndarray
    quality_score: float
    profile_distance: float
    final_score: float


@dataclass
class ExportedClip:
    clip_filename: str
    source: str
    source_audio: str
    start_sec: float
    end_sec: float
    refined_start_sec: float
    refined_end_sec: float
    duration_sec: float
    continuity_block: int
    continuity_seq_in_block: int
    selection_pass: int
    quality_score: float
    profile_distance: float
    final_score: float
    raw_rms_dbfs: float
    raw_silence_pct: float
    hf_to_body_ratio: float
    low_mid_ratio: float
    spectral_centroid_hz: float
    pitch_hz: float
    spectral_cosine: float
    gain_db: float
    post_rms_dbfs: float
    post_peak_dbfs: float


@dataclass
class VoiceProfile:
    centroid_median: float
    centroid_mad: float
    hf_median: float
    hf_mad: float
    low_mid_median: float
    low_mid_mad: float
    pitch_median: float
    pitch_mad: float
    spectral_vector: np.ndarray


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


def clear_output_dir(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.glob("*.wav"):
        p.unlink()
    for name in ["clip_manifest.csv", "README.md", "selection_diagnostics.json", "continuity_playlist.m3u"]:
        p = out_dir / name
        if p.exists():
            p.unlink()


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


def pitch_median_autocorr(x: np.ndarray, sr: int) -> float:
    frame = 2048
    hop = 1024
    if len(x) < frame:
        return float("nan")

    window = np.hanning(frame).astype(np.float32)
    lag_min = int(sr / 300.0)
    lag_max = min(frame - 1, int(sr / 70.0))

    vals: list[float] = []
    voiced_idx = []
    for i in range(0, len(x) - frame + 1, hop):
        chunk = x[i : i + frame]
        rms = math.sqrt(float(np.mean(chunk * chunk)))
        if to_db(rms) > -35.0:
            voiced_idx.append(i)

    if not voiced_idx:
        return float("nan")

    if len(voiced_idx) > 120:
        step = len(voiced_idx) / 120
        voiced_idx = [voiced_idx[int(i * step)] for i in range(120)]

    nfft = 4096
    for start in voiced_idx:
        f = x[start : start + frame] * window
        f = f - np.mean(f)
        if float(np.max(np.abs(f))) < 1e-5:
            continue

        spec = np.fft.rfft(f, n=nfft)
        ac = np.fft.irfft(spec * np.conj(spec), n=nfft)[:frame]
        if ac[0] <= 0:
            continue

        region = ac[lag_min:lag_max]
        if len(region) == 0:
            continue
        lag = int(np.argmax(region)) + lag_min
        if ac[lag] < 0.18 * ac[0]:
            continue

        f0 = sr / float(lag)
        if 70.0 <= f0 <= 300.0:
            vals.append(f0)

    if not vals:
        return float("nan")
    return float(np.median(np.array(vals, dtype=np.float64)))


def spectral_features(x: np.ndarray, sr: int) -> tuple[float, float, float, np.ndarray]:
    n_fft = 4096
    hop = n_fft // 2
    if len(x) < n_fft:
        return float("nan"), float("nan"), float("nan"), np.zeros(5, dtype=np.float64)

    window = np.hanning(n_fft).astype(np.float32)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    mags = []
    for i in range(0, len(x) - n_fft + 1, hop):
        frame = x[i : i + n_fft]
        rms = math.sqrt(float(np.mean(frame * frame)))
        if to_db(rms) < -45.0:
            continue
        mags.append(np.abs(np.fft.rfft(frame * window)))

    if not mags:
        return float("nan"), float("nan"), float("nan"), np.zeros(5, dtype=np.float64)

    spec = np.mean(np.vstack(mags), axis=0)
    total = float(np.sum(spec) + DB_EPS)
    centroid = float(np.sum(freqs * spec) / total)

    def band(lo: float, hi: float) -> float:
        m = (freqs >= lo) & (freqs < hi)
        return float(np.sum(spec[m]) / total)

    b1 = band(80.0, 250.0)
    b2 = band(250.0, 1000.0)
    b3 = band(1000.0, 4000.0)
    b4 = band(4000.0, 8000.0)
    b5 = band(8000.0, 16000.0)
    low_mid = b1 + b2
    body = b1 + b2 + b3
    high = b4 + b5
    hf_ratio = float(high / max(body, DB_EPS))

    vec = np.array([b1, b2, b3, b4, b5], dtype=np.float64)
    norm = float(np.linalg.norm(vec) + DB_EPS)
    vec = vec / norm
    return centroid, hf_ratio, low_mid, vec


def base_quality_score(rms_dbfs: float, silence_pct: float, hf_ratio: float, low_mid: float) -> float:
    score = 100.0
    if rms_dbfs < -30.0:
        score -= (abs(rms_dbfs + 30.0) * 3.0)
    if rms_dbfs > -14.0:
        score -= (abs(rms_dbfs + 14.0) * 2.5)
    if silence_pct > 15.0:
        score -= (silence_pct - 15.0) * 1.2
    if hf_ratio > 0.55:
        score -= (hf_ratio - 0.55) * 120.0
    if low_mid < 0.30:
        score -= (0.30 - low_mid) * 120.0
    return float(score)


def robust_mad(values: np.ndarray, fallback: float) -> float:
    if len(values) == 0:
        return fallback
    med = float(np.median(values))
    mad = float(np.median(np.abs(values - med)))
    return max(mad, fallback)


def compute_profile_distance(c: WindowCandidate, profile: VoiceProfile) -> float:
    d_cent = abs(c.spectral_centroid_hz - profile.centroid_median) / max(profile.centroid_mad, 120.0)
    d_hf = abs(c.hf_to_body_ratio - profile.hf_median) / max(profile.hf_mad, 0.06)
    d_lowmid = abs(c.low_mid_ratio - profile.low_mid_median) / max(profile.low_mid_mad, 0.05)

    if math.isnan(c.pitch_hz):
        d_pitch = 1.0
    else:
        d_pitch = abs(c.pitch_hz - profile.pitch_median) / max(profile.pitch_mad, 10.0)

    d = float(np.mean(np.clip(np.array([d_cent, d_hf, d_lowmid, d_pitch], dtype=np.float64), 0.0, 4.0)))
    return d


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + DB_EPS
    return float(np.dot(a, b) / denom)


def extract_candidates(
    mono: np.ndarray,
    sr: int,
    source: str,
    profile: VoiceProfile | None,
    win_sec: float,
    hop_sec: float,
) -> list[WindowCandidate]:
    win = int(sr * win_sec)
    hop = int(sr * hop_sec)

    out: list[WindowCandidate] = []
    for start in range(0, len(mono) - win + 1, hop):
        seg = mono[start : start + win]

        rms = math.sqrt(float(np.mean(seg * seg)))
        rms_db = float(to_db(rms))
        silence_pct = float(np.mean(frame_rms_db(seg, sr) < -45.0) * 100.0)
        centroid, hf_ratio, low_mid, vec = spectral_features(seg, sr)
        pitch_hz = pitch_median_autocorr(seg, sr)

        q = base_quality_score(rms_db, silence_pct, hf_ratio, low_mid)

        if profile is not None:
            cos = cosine_similarity(vec, profile.spectral_vector)
            tmp = WindowCandidate(
                source=source,
                start_sec=float(start / sr),
                end_sec=float((start + win) / sr),
                duration_sec=win_sec,
                rms_dbfs=rms_db,
                silence_pct=silence_pct,
                hf_to_body_ratio=hf_ratio,
                low_mid_ratio=low_mid,
                spectral_centroid_hz=centroid,
                pitch_hz=pitch_hz,
                spectral_cosine=cos,
                spectral_vector=vec,
                quality_score=q,
                profile_distance=0.0,
                final_score=0.0,
            )
            pd = compute_profile_distance(tmp, profile)
            # Final score pushes both recording quality and profile consistency.
            final = q - (7.0 * pd) + (5.0 * cos)
            tmp.profile_distance = pd
            tmp.final_score = float(final)
            out.append(tmp)
        else:
            out.append(
                WindowCandidate(
                    source=source,
                    start_sec=float(start / sr),
                    end_sec=float((start + win) / sr),
                    duration_sec=win_sec,
                    rms_dbfs=rms_db,
                    silence_pct=silence_pct,
                    hf_to_body_ratio=hf_ratio,
                    low_mid_ratio=low_mid,
                    spectral_centroid_hz=centroid,
                    pitch_hz=pitch_hz,
                    spectral_cosine=0.0,
                    spectral_vector=vec,
                    quality_score=q,
                    profile_distance=0.0,
                    final_score=q,
                )
            )

    return out


def build_reference_profile(candidates: list[WindowCandidate]) -> VoiceProfile:
    # Use only high-confidence windows from the primary source.
    seed = [
        c
        for c in candidates
        if c.quality_score >= 99.0
        and c.silence_pct <= 8.0
        and c.hf_to_body_ratio <= 0.55
        and not math.isnan(c.pitch_hz)
    ]
    if not seed:
        seed = [c for c in candidates if not math.isnan(c.pitch_hz)]
    if not seed:
        raise RuntimeError("Could not build reference profile: no valid windows.")

    seed.sort(key=lambda c: c.quality_score, reverse=True)
    seed = seed[:35]

    cent = np.array([c.spectral_centroid_hz for c in seed], dtype=np.float64)
    hf = np.array([c.hf_to_body_ratio for c in seed], dtype=np.float64)
    low_mid = np.array([c.low_mid_ratio for c in seed], dtype=np.float64)
    pitch = np.array([c.pitch_hz for c in seed], dtype=np.float64)

    vec = np.mean(np.vstack([c.spectral_vector for c in seed]), axis=0)
    target_vec = vec / (np.linalg.norm(vec) + DB_EPS)

    return VoiceProfile(
        centroid_median=float(np.median(cent)),
        centroid_mad=robust_mad(cent, fallback=140.0),
        hf_median=float(np.median(hf)),
        hf_mad=robust_mad(hf, fallback=0.06),
        low_mid_median=float(np.median(low_mid)),
        low_mid_mad=robust_mad(low_mid, fallback=0.05),
        pitch_median=float(np.median(pitch)),
        pitch_mad=robust_mad(pitch, fallback=11.0),
        spectral_vector=target_vec,
    )


def overlap_or_too_close(a: WindowCandidate, b: WindowCandidate, gap_sec: float) -> bool:
    return not (a.end_sec + gap_sec <= b.start_sec or a.start_sec >= b.end_sec + gap_sec)


def pick_with_relaxation(
    windows: list[WindowCandidate],
    cfg: SourceConfig,
    min_gap_sec: float,
    profile_mode: str,
) -> tuple[list[tuple[WindowCandidate, int]], dict[str, object]]:
    selected: list[tuple[WindowCandidate, int]] = []

    if profile_mode == "strict":
        passes = [
            {"final_min": 99.0, "profile_max": 0.95, "silence_max": 9.0, "hf_max": 0.56},
            {"final_min": 97.0, "profile_max": 1.15, "silence_max": 10.0, "hf_max": 0.58},
            {"final_min": 95.0, "profile_max": 1.30, "silence_max": 11.0, "hf_max": 0.60},
        ]
    else:
        passes = [
            {"final_min": 98.0, "profile_max": 1.15, "silence_max": 10.0, "hf_max": 0.58},
            {"final_min": 95.0, "profile_max": 1.35, "silence_max": 11.5, "hf_max": 0.62},
            {"final_min": 92.0, "profile_max": 1.70, "silence_max": 13.0, "hf_max": 0.66},
            {"final_min": 89.0, "profile_max": 2.10, "silence_max": 14.5, "hf_max": 0.70},
        ]

    windows_sorted = sorted(windows, key=lambda c: (c.final_score, c.quality_score), reverse=True)
    used: set[tuple[float, float]] = set()

    for i, p in enumerate(passes, start=1):
        candidates = [
            c
            for c in windows_sorted
            if c.start_sec >= cfg.avoid_first_n_sec
            and c.final_score >= p["final_min"]
            and c.profile_distance <= p["profile_max"]
            and c.silence_pct <= p["silence_max"]
            and c.hf_to_body_ratio <= p["hf_max"]
            and (c.start_sec, c.end_sec) not in used
        ]
        for c in candidates:
            if any(overlap_or_too_close(c, s[0], min_gap_sec) for s in selected):
                continue
            selected.append((c, i))
            used.add((c.start_sec, c.end_sec))
            if len(selected) >= cfg.max_count:
                break
        if len(selected) >= cfg.max_count:
            break

    selected.sort(key=lambda x: x[0].start_sec)
    diag = {
        "target_min": cfg.min_count,
        "target_max": cfg.max_count,
        "selected": len(selected),
        "met_min_target": len(selected) >= cfg.min_count,
        "passes": passes,
    }
    return selected, diag


def compute_large_gaps(
    selected: list[tuple[WindowCandidate, int]],
    gap_threshold_sec: float,
) -> list[dict[str, float]]:
    windows = sorted([w for w, _ in selected], key=lambda w: w.start_sec)
    gaps: list[dict[str, float]] = []
    for i in range(len(windows) - 1):
        g = windows[i + 1].start_sec - windows[i].end_sec
        if g > gap_threshold_sec:
            gaps.append(
                {
                    "start_sec": windows[i].end_sec,
                    "end_sec": windows[i + 1].start_sec,
                    "duration_sec": g,
                }
            )
    gaps.sort(key=lambda x: x["duration_sec"], reverse=True)
    return gaps


def gap_fill_thresholds(profile_mode: str, strength: str) -> tuple[float, float, float, float]:
    if profile_mode == "strict":
        if strength == "conservative":
            return (92.0, 1.90, 13.5, 0.68)
        if strength == "aggressive":
            return (87.0, 2.60, 16.0, 0.80)
        return (90.0, 2.15, 14.5, 0.72)

    if strength == "conservative":
        return (89.0, 2.10, 14.5, 0.70)
    if strength == "aggressive":
        return (85.0, 2.80, 16.5, 0.84)
    return (87.0, 2.35, 15.0, 0.76)


def passes_gap_fill_gate(
    c: WindowCandidate,
    min_final: float,
    max_profile: float,
    max_silence: float,
    max_hf: float,
) -> bool:
    return (
        c.final_score >= min_final
        and c.profile_distance <= max_profile
        and c.silence_pct <= max_silence
        and c.hf_to_body_ratio <= max_hf
    )


def fill_large_gaps(
    selected: list[tuple[WindowCandidate, int]],
    candidates: list[WindowCandidate],
    min_gap_sec: float,
    gap_threshold_sec: float,
    max_additions: int,
    profile_mode: str,
    fill_strength: str,
) -> tuple[list[tuple[WindowCandidate, int]], dict[str, object]]:
    out = sorted(selected, key=lambda x: x[0].start_sec)
    used = {(w.start_sec, w.end_sec) for w, _ in out}
    min_final, max_profile, max_silence, max_hf = gap_fill_thresholds(
        profile_mode=profile_mode,
        strength=fill_strength,
    )

    before = compute_large_gaps(out, gap_threshold_sec=gap_threshold_sec)
    additions: list[WindowCandidate] = []

    for _ in range(max_additions):
        gaps = compute_large_gaps(out, gap_threshold_sec=gap_threshold_sec)
        if not gaps:
            break

        inserted = False
        for gap in gaps:
            gs = gap["start_sec"]
            ge = gap["end_sec"]
            center = (gs + ge) * 0.5

            pool = [
                c
                for c in candidates
                if (c.start_sec, c.end_sec) not in used
                and passes_gap_fill_gate(
                    c,
                    min_final=min_final,
                    max_profile=max_profile,
                    max_silence=max_silence,
                    max_hf=max_hf,
                )
                and c.start_sec >= gs - 0.75
                and c.end_sec <= ge + 0.75
                and not any(overlap_or_too_close(c, s[0], min_gap_sec) for s in out)
            ]

            if not pool:
                pool = [
                    c
                    for c in candidates
                    if (c.start_sec, c.end_sec) not in used
                    and passes_gap_fill_gate(
                        c,
                        min_final=min_final,
                        max_profile=max_profile,
                        max_silence=max_silence,
                        max_hf=max_hf,
                    )
                    and c.start_sec < ge
                    and c.end_sec > gs
                    and not any(overlap_or_too_close(c, s[0], min_gap_sec) for s in out)
                ]

            if not pool:
                continue

            pool.sort(
                key=lambda c: (
                    abs(((c.start_sec + c.end_sec) * 0.5) - center),
                    -c.final_score,
                    c.profile_distance,
                )
            )
            chosen = pool[0]
            out.append((chosen, 8))
            out.sort(key=lambda x: x[0].start_sec)
            used.add((chosen.start_sec, chosen.end_sec))
            additions.append(chosen)
            inserted = True
            break

        if not inserted:
            break

    after = compute_large_gaps(out, gap_threshold_sec=gap_threshold_sec)
    diag = {
        "enabled": True,
        "fill_strength": fill_strength,
        "min_final": min_final,
        "max_profile_distance": max_profile,
        "max_silence_pct": max_silence,
        "max_hf_to_body_ratio": max_hf,
        "gap_threshold_sec": gap_threshold_sec,
        "max_additions": max_additions,
        "added_count": len(additions),
        "large_gaps_before_count": len(before),
        "large_gaps_after_count": len(after),
        "largest_gap_before_sec": (before[0]["duration_sec"] if before else 0.0),
        "largest_gap_after_sec": (after[0]["duration_sec"] if after else 0.0),
    }
    return out, diag


def smooth_abs(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1:
        return np.abs(x)
    kernel = np.ones(win, dtype=np.float32) / float(win)
    return np.convolve(np.abs(x), kernel, mode="same")


def refine_boundary(x: np.ndarray, sr: int, idx: int, search_ms: float = 120.0) -> int:
    radius = int(sr * (search_ms / 1000.0))
    lo = max(0, idx - radius)
    hi = min(len(x) - 1, idx + radius)
    if hi <= lo:
        return idx

    env = smooth_abs(x[lo : hi + 1], win=max(3, int(sr * 0.004)))
    rel = int(np.argmin(env))
    return lo + rel


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


def apply_fade(seg: np.ndarray, sr: int, fade_ms: float) -> np.ndarray:
    n = int(sr * (fade_ms / 1000.0))
    if n <= 1 or len(seg) < 2 * n:
        return seg
    out = seg.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    out[:n] *= ramp
    out[-n:] *= ramp[::-1]
    return out


def normalize(
    seg: np.ndarray,
    target_rms_db: float,
    peak_ceiling_db: float,
    forced_gain_db: float | None = None,
) -> tuple[np.ndarray, float, float, float]:
    in_rms = math.sqrt(float(np.mean(seg * seg)))
    if in_rms < DB_EPS:
        return seg, 0.0, float(to_db(DB_EPS)), float(to_db(np.max(np.abs(seg)) + DB_EPS))

    if forced_gain_db is None:
        target_rms = 10 ** (target_rms_db / 20.0)
        gain = target_rms / in_rms
    else:
        gain = 10 ** (forced_gain_db / 20.0)
    out = seg * gain

    peak = float(np.max(np.abs(out)))
    ceiling = 10 ** (peak_ceiling_db / 20.0)
    if peak > ceiling:
        out = out * (ceiling / peak)

    out_rms = math.sqrt(float(np.mean(out * out)))
    out_peak = float(np.max(np.abs(out)))
    gain_db = float(to_db(out_rms / max(in_rms, DB_EPS)))
    return out, gain_db, float(to_db(out_rms)), float(to_db(out_peak + DB_EPS))


def compute_source_gain_baseline(selected: list[tuple[WindowCandidate, int]], target_rms_db: float) -> float:
    if not selected:
        return 0.0
    med_rms = float(np.median(np.array([w.rms_dbfs for w, _ in selected], dtype=np.float64)))
    return float(target_rms_db - med_rms)


def continuity_forced_gain(
    window: WindowCandidate,
    target_rms_db: float,
    base_gain_db: float,
    clamp_db: float,
) -> float:
    per_clip_gain = target_rms_db - window.rms_dbfs
    lo = base_gain_db - clamp_db
    hi = base_gain_db + clamp_db
    return float(min(max(per_clip_gain, lo), hi))


def assign_continuity_blocks(exports: list[ExportedClip], gap_sec: float) -> None:
    if not exports:
        return

    exports.sort(key=lambda e: (e.source, e.refined_start_sec))
    current_source = None
    block_id = 0
    seq = 0
    prev_end = 0.0

    for e in exports:
        if e.source != current_source:
            current_source = e.source
            block_id = 1
            seq = 1
        else:
            if e.refined_start_sec - prev_end > gap_sec:
                block_id += 1
                seq = 1
            else:
                seq += 1

        e.continuity_block = block_id
        e.continuity_seq_in_block = seq
        prev_end = e.refined_end_sec


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deep clean clip extraction with profile matching.")
    p.add_argument("--study-dir", type=Path, default=Path("/Users/dankerbadge/Documents/J.A.R.V.I.S/analysis/jarvis_study"))
    p.add_argument("--out-dir", type=Path, default=None)
    p.add_argument("--window-sec", type=float, default=6.0)
    p.add_argument("--hop-sec", type=float, default=3.0)
    p.add_argument("--min-gap-sec", type=float, default=0.12)
    p.add_argument("--target-rms", type=float, default=-23.0)
    p.add_argument("--peak-ceiling", type=float, default=-1.0)
    p.add_argument("--fade-ms", type=float, default=12.0)
    p.add_argument("--continuity-gain-clamp-db", type=float, default=2.0)
    p.add_argument("--continuity-block-gap-sec", type=float, default=12.0)
    p.add_argument("--gap-threshold-sec", type=float, default=9.0)
    p.add_argument("--gap-fill-max-additions", type=int, default=28)
    p.add_argument(
        "--gap-fill-strength",
        choices=["conservative", "moderate", "aggressive"],
        default="moderate",
    )
    p.add_argument("--fill-gaps", dest="fill_gaps", action="store_true")
    p.add_argument("--no-fill-gaps", dest="fill_gaps", action="store_false")
    p.add_argument("--profile-mode", choices=["balanced", "strict"], default="balanced")
    p.set_defaults(fill_gaps=True)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    study_dir = args.study_dir
    out_dir = args.out_dir or (study_dir / "clean_training_clips_deep")
    clear_output_dir(out_dir)

    sources = [
        SourceConfig(
            name="JARVIS_1",
            path=study_dir / "JARVIS_1.wav",
            max_count=52,
            min_count=36,
            avoid_first_n_sec=0.0,
        ),
        SourceConfig(
            name="JARVIS_II",
            path=study_dir / "JARVIS_II.wav",
            max_count=1,
            min_count=1,
            avoid_first_n_sec=3.0,
        ),
    ]

    raw_audio: dict[str, tuple[np.ndarray, int]] = {}
    for s in sources:
        raw_audio[s.name] = read_wav_mono(s.path)

    base_candidates_primary = extract_candidates(
        mono=raw_audio["JARVIS_1"][0],
        sr=raw_audio["JARVIS_1"][1],
        source="JARVIS_1",
        profile=None,
        win_sec=args.window_sec,
        hop_sec=args.hop_sec,
    )
    profile = build_reference_profile(base_candidates_primary)

    diagnostics: dict[str, object] = {
        "profile": {
            "centroid_median_hz": profile.centroid_median,
            "centroid_mad": profile.centroid_mad,
            "hf_median": profile.hf_median,
            "hf_mad": profile.hf_mad,
            "low_mid_median": profile.low_mid_median,
            "low_mid_mad": profile.low_mid_mad,
            "pitch_median_hz": profile.pitch_median,
            "pitch_mad": profile.pitch_mad,
        },
        "config": {
            "window_sec": args.window_sec,
            "hop_sec": args.hop_sec,
            "min_gap_sec": args.min_gap_sec,
            "target_rms_dbfs": args.target_rms,
            "peak_ceiling_dbfs": args.peak_ceiling,
            "fade_ms": args.fade_ms,
            "continuity_gain_clamp_db": args.continuity_gain_clamp_db,
            "continuity_block_gap_sec": args.continuity_block_gap_sec,
            "gap_threshold_sec": args.gap_threshold_sec,
            "gap_fill_max_additions": args.gap_fill_max_additions,
            "gap_fill_strength": args.gap_fill_strength,
            "fill_gaps": args.fill_gaps,
            "profile_mode": args.profile_mode,
        },
        "sources": {},
    }

    exports: list[ExportedClip] = []

    for src in sources:
        mono, sr = raw_audio[src.name]
        candidates = extract_candidates(
            mono=mono,
            sr=sr,
            source=src.name,
            profile=profile,
            win_sec=args.window_sec,
            hop_sec=args.hop_sec,
        )

        selected, sel_diag = pick_with_relaxation(
            windows=candidates,
            cfg=src,
            min_gap_sec=args.min_gap_sec,
            profile_mode=args.profile_mode,
        )
        if args.fill_gaps:
            selected, gap_diag = fill_large_gaps(
                selected=selected,
                candidates=candidates,
                min_gap_sec=args.min_gap_sec,
                gap_threshold_sec=args.gap_threshold_sec,
                max_additions=args.gap_fill_max_additions,
                profile_mode=args.profile_mode,
                fill_strength=args.gap_fill_strength,
            )
        else:
            before = compute_large_gaps(selected, gap_threshold_sec=args.gap_threshold_sec)
            gap_diag = {
                "enabled": False,
                "gap_threshold_sec": args.gap_threshold_sec,
                "max_additions": 0,
                "added_count": 0,
                "large_gaps_before_count": len(before),
                "large_gaps_after_count": len(before),
                "largest_gap_before_sec": (before[0]["duration_sec"] if before else 0.0),
                "largest_gap_after_sec": (before[0]["duration_sec"] if before else 0.0),
            }
        base_gain_db = compute_source_gain_baseline(selected, target_rms_db=args.target_rms)

        diagnostics["sources"][src.name] = {
            "source_audio": str(src.path),
            "window_count": len(candidates),
            "continuity_base_gain_db": base_gain_db,
            "selection": sel_diag,
            "gap_fill": gap_diag,
            "top_final_scores": [round(c.final_score, 3) for c in sorted(candidates, key=lambda x: x.final_score, reverse=True)[:8]],
        }

        for idx, (c, pass_idx) in enumerate(selected, start=1):
            start_i = int(round(c.start_sec * sr))
            end_i = int(round(c.end_sec * sr))

            ref_start = refine_boundary(mono, sr, start_i, search_ms=120.0)
            ref_end = refine_boundary(mono, sr, end_i, search_ms=120.0)
            if ref_end <= ref_start + int(sr * 3.5):
                ref_start, ref_end = start_i, end_i

            seg = mono[ref_start:ref_end]
            seg = seg - float(np.mean(seg))
            seg = highpass_first_order(seg, sr=sr, cutoff_hz=65.0)
            forced_gain_db = continuity_forced_gain(
                window=c,
                target_rms_db=args.target_rms,
                base_gain_db=base_gain_db,
                clamp_db=args.continuity_gain_clamp_db,
            )
            seg, gain_db, post_rms, post_peak = normalize(
                seg,
                target_rms_db=args.target_rms,
                peak_ceiling_db=args.peak_ceiling,
                forced_gain_db=forced_gain_db,
            )
            seg = apply_fade(seg, sr=sr, fade_ms=args.fade_ms)

            out_name = f"{src.name.lower()}_{idx:03d}_{int(c.start_sec*1000):07d}_{int(c.end_sec*1000):07d}.wav"
            out_path = out_dir / out_name
            write_wav_mono(out_path, seg, sr)

            exports.append(
                ExportedClip(
                    clip_filename=out_name,
                    source=src.name,
                    source_audio=str(src.path),
                    start_sec=c.start_sec,
                    end_sec=c.end_sec,
                    refined_start_sec=ref_start / sr,
                    refined_end_sec=ref_end / sr,
                    duration_sec=(ref_end - ref_start) / sr,
                    continuity_block=0,
                    continuity_seq_in_block=0,
                    selection_pass=pass_idx,
                    quality_score=c.quality_score,
                    profile_distance=c.profile_distance,
                    final_score=c.final_score,
                    raw_rms_dbfs=c.rms_dbfs,
                    raw_silence_pct=c.silence_pct,
                    hf_to_body_ratio=c.hf_to_body_ratio,
                    low_mid_ratio=c.low_mid_ratio,
                    spectral_centroid_hz=c.spectral_centroid_hz,
                    pitch_hz=c.pitch_hz,
                    spectral_cosine=c.spectral_cosine,
                    gain_db=gain_db,
                    post_rms_dbfs=post_rms,
                    post_peak_dbfs=post_peak,
                )
            )

    assign_continuity_blocks(exports, gap_sec=args.continuity_block_gap_sec)
    exports.sort(key=lambda e: (e.source, e.refined_start_sec))

    manifest = out_dir / "clip_manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as f:
        fieldnames = list(asdict(exports[0]).keys()) if exports else []
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for e in exports:
            writer.writerow(asdict(e))

    diag_path = out_dir / "selection_diagnostics.json"
    diag_path.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")

    playlist_path = out_dir / "continuity_playlist.m3u"
    playlist_lines = ["#EXTM3U"]
    for e in exports:
        playlist_lines.append(
            f"#EXTINF:-1,{e.source} block={e.continuity_block} seq={e.continuity_seq_in_block}"
        )
        playlist_lines.append(e.clip_filename)
    playlist_path.write_text("\n".join(playlist_lines) + "\n", encoding="utf-8")

    total_sec = sum(e.duration_sec for e in exports)
    by_src: dict[str, int] = {}
    by_src_blocks: dict[str, set[int]] = {}
    for e in exports:
        by_src[e.source] = by_src.get(e.source, 0) + 1
        by_src_blocks.setdefault(e.source, set()).add(e.continuity_block)

    lines = [
        "# Deep Clean Training Clips",
        "",
        "High-consistency extraction with profile matching to the strongest reference windows.",
        "",
        "## Result",
        "",
        f"- Profile mode: {args.profile_mode}",
        f"- Total clips: {len(exports)}",
        f"- Total duration: {total_sec:.1f}s ({total_sec/60.0:.2f} min)",
        f"- Continuity gain clamp: +/-{args.continuity_gain_clamp_db:.2f} dB around source baseline",
        f"- Continuity block gap threshold: {args.continuity_block_gap_sec:.2f}s",
    ]
    for k in sorted(by_src):
        lines.append(f"- {k}: {by_src[k]} clips across {len(by_src_blocks[k])} continuity blocks")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- Voice-profile consistency scoring applied (pitch, centroid, low-mid, HF/body).")
    lines.append("- Boundary refinement and short fades used to reduce edge clicks and partial-word cuts.")
    lines.append("- DC removal and gentle 65Hz high-pass used to remove rumble without forcing reverb/noise coloration.")

    readme = out_dir / "README.md"
    readme.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {len(exports)} clips to {out_dir}")
    print(f"Manifest: {manifest}")
    print(f"Diagnostics: {diag_path}")
    print(f"Playlist: {playlist_path}")
    print(f"Summary: {readme}")


if __name__ == "__main__":
    main()
