#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import wave
from array import array
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import noisereduce as nr
import numpy as np
from scipy import signal

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
class Decision:
    clip_filename: str
    source: str
    start_sec: float
    selected: str
    reason: str
    orig_total_score: float
    master_total_score: float
    delta_total_score: float
    orig_clarity: float
    master_clarity: float
    delta_clarity: float
    orig_silence_pct: float
    master_silence_pct: float
    orig_hiss_ratio: float
    master_hiss_ratio: float
    orig_harmonicity: float
    master_harmonicity: float
    orig_peak_dbfs: float
    master_peak_dbfs: float
    orig_rms_dbfs: float
    master_rms_dbfs: float
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
        rms_mad=robust_mad(rms, 1.0),
        hiss_ratio=float(np.median(hiss)),
        hiss_mad=robust_mad(hiss, 0.016),
        presence_ratio=float(np.median(pres)),
        presence_mad=robust_mad(pres, 0.011),
        flatness=float(np.median(flat)),
        flat_mad=robust_mad(flat, 0.007),
        harmonicity=float(np.median(harm)),
        harm_mad=robust_mad(harm, 0.016),
        centroid_hz=float(np.median(cent)),
        centroid_mad=robust_mad(cent, 100.0),
    )


def profile_distance(m: Metrics, p: Profile) -> float:
    vals = np.array(
        [
            abs(m.hiss_ratio - p.hiss_ratio) / max(p.hiss_mad, 0.012),
            abs(m.presence_ratio - p.presence_ratio) / max(p.presence_mad, 0.010),
            abs(m.spectral_flatness - p.flatness) / max(p.flat_mad, 0.006),
            abs(m.harmonicity - p.harmonicity) / max(p.harm_mad, 0.015),
            abs(m.centroid_hz - p.centroid_hz) / max(p.centroid_mad, 90.0),
        ],
        dtype=np.float64,
    )
    return float(np.mean(np.clip(vals, 0.0, 4.0)))


def total_score(m: Metrics, p: Profile) -> float:
    pd = profile_distance(m, p)
    pen = 0.0
    if m.peak_dbfs > -0.70:
        pen += (m.peak_dbfs + 0.70) * 3.0
    if m.clipped_pct > 0.08:
        pen += (m.clipped_pct - 0.08) * 2.0
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


def trim_silence(x: np.ndarray, sr: int, threshold_db: float = -44.0, pad_ms: float = 70.0) -> np.ndarray:
    frame = max(1, int(sr * 0.02))
    hop = max(1, int(sr * 0.01))
    if len(x) < frame:
        return x
    vals = []
    starts = []
    for i in range(0, len(x) - frame + 1, hop):
        f = x[i : i + frame]
        vals.append(float(to_db(math.sqrt(float(np.mean(f * f))) + DB_EPS)))
        starts.append(i)
    vals_np = np.array(vals, dtype=np.float64)
    idx = np.where(vals_np > threshold_db)[0]
    if len(idx) == 0:
        return x
    start = starts[int(idx[0])]
    end = starts[int(idx[-1])] + frame
    pad = int(sr * (pad_ms / 1000.0))
    start = max(0, start - pad)
    end = min(len(x), end + pad)
    return x[start:end]


def highpass(x: np.ndarray, sr: int, cutoff_hz: float = 65.0) -> np.ndarray:
    sos = signal.butter(2, cutoff_hz, btype="highpass", fs=sr, output="sos")
    return signal.sosfiltfilt(sos, x).astype(np.float32)


def dehum(x: np.ndarray, sr: int) -> np.ndarray:
    y = x.astype(np.float32)
    for f0 in (60.0, 120.0, 180.0, 240.0):
        if f0 >= (sr / 2.0 - 100.0):
            continue
        b, a = signal.iirnotch(w0=f0, Q=30.0, fs=sr)
        y = signal.filtfilt(b, a, y).astype(np.float32)
    return y


def deesser(x: np.ndarray, sr: int, threshold_ratio: float = 0.225, max_reduction_db: float = 2.4) -> np.ndarray:
    sos_sib = signal.butter(2, [5200.0, 9200.0], btype="bandpass", fs=sr, output="sos")
    sos_body = signal.butter(2, [120.0, 4200.0], btype="bandpass", fs=sr, output="sos")
    sib = signal.sosfiltfilt(sos_sib, x)
    body = signal.sosfiltfilt(sos_body, x)
    env_s = signal.sosfiltfilt(signal.butter(1, 18.0, btype="lowpass", fs=sr, output="sos"), np.abs(sib))
    env_b = signal.sosfiltfilt(signal.butter(1, 18.0, btype="lowpass", fs=sr, output="sos"), np.abs(body))
    ratio = env_s / np.maximum(env_b, 1e-6)
    over = np.maximum(0.0, ratio - threshold_ratio)
    if float(np.max(over)) <= 0.0:
        return x.astype(np.float32)
    depth = np.clip(over / max(threshold_ratio, 1e-4), 0.0, 1.0)
    max_lin = 10 ** (-max_reduction_db / 20.0)
    gain = np.clip(1.0 - depth * (1.0 - max_lin), max_lin, 1.0)
    y = x - sib + (sib * gain)
    return y.astype(np.float32)


def presence_tilt_eq(x: np.ndarray, sr: int, presence_db: float, air_db: float) -> np.ndarray:
    sos_pres = signal.butter(2, [2200.0, 4800.0], btype="bandpass", fs=sr, output="sos")
    sos_air = signal.butter(2, 9000.0, btype="highpass", fs=sr, output="sos")
    pres = signal.sosfiltfilt(sos_pres, x)
    air = signal.sosfiltfilt(sos_air, x)
    pg = 10 ** (presence_db / 20.0)
    ag = 10 ** (air_db / 20.0)
    y = x + (pg - 1.0) * pres + (ag - 1.0) * air
    return y.astype(np.float32)


def apply_fade(x: np.ndarray, sr: int, ms: float = 8.0) -> np.ndarray:
    n = int(sr * (ms / 1000.0))
    if n <= 1 or len(x) < 2 * n:
        return x
    y = x.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    y[:n] *= ramp
    y[-n:] *= ramp[::-1]
    return y


def soft_limiter(x: np.ndarray, ceiling_dbfs: float = -1.10) -> np.ndarray:
    ceiling = 10 ** (ceiling_dbfs / 20.0)
    return (np.tanh(x / max(ceiling, 1e-6)) * ceiling).astype(np.float32)


def normalize_rms(x: np.ndarray, gain_db: float) -> np.ndarray:
    gain = 10 ** (gain_db / 20.0)
    return (x * gain).astype(np.float32)


def smooth_gains(raw: list[float], clamp_db: float = 0.85, alpha: float = 0.22) -> list[float]:
    if not raw:
        return []
    med = float(np.median(np.array(raw, dtype=np.float64)))
    out = [float(np.clip(raw[0], med - clamp_db, med + clamp_db))]
    for g in raw[1:]:
        v = ((1.0 - alpha) * out[-1]) + (alpha * g)
        out.append(float(np.clip(v, med - clamp_db, med + clamp_db)))
    return out


def guard_reason(orig: Metrics, cand: Metrics, delta_score: float, delta_clarity: float, min_delta: float) -> str | None:
    if cand.silence_pct > (orig.silence_pct + 2.0):
        return "silence_up"
    if cand.harmonicity < (orig.harmonicity - 0.008):
        return "harm_down"
    if cand.spectral_flatness > (orig.spectral_flatness + 0.010):
        return "flat_up"
    if cand.hiss_ratio > (orig.hiss_ratio + 0.016):
        return "hiss_up"
    if cand.peak_dbfs > -0.35:
        return "peak_hot"
    if cand.clipped_pct > max(orig.clipped_pct + 0.015, 0.08):
        return "clip_risk"
    if delta_clarity < -0.10:
        return "clarity_down"
    if delta_score < 0.0 and delta_clarity < 0.22:
        return "not_better"
    if delta_score < min_delta and delta_clarity < 0.10:
        return "not_better"
    return None


def clear_output(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def stitch_preview(clips: list[Path], out_path: Path, count: int, gap_ms: int = 120) -> None:
    if not clips:
        return
    chosen = clips[: max(1, count)]
    with wave.open(str(chosen[0]), "rb") as wf:
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
    if ch != 1 or sw != 2:
        return
    gap_frames = int(sr * (gap_ms / 1000.0))
    gap = array("h", [0] * gap_frames)
    merged = array("h")
    for i, clip in enumerate(chosen):
        with wave.open(str(clip), "rb") as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != sr:
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


def zip_pack(pack_root: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as zf:
        for file in sorted(pack_root.rglob("*")):
            if file.is_file():
                zf.write(file, arcname=str(file.relative_to(pack_root)))


def write_active_pointer(path: Path, pack_root: Path, export_zip: Path) -> None:
    payload = {
        "active_pack": str(pack_root),
        "export_zip": str(export_zip),
        "profile": "master_v2",
        "updated_at": datetime.now().isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    default_input = root / ".jarvis" / "voice" / "training_assets" / "jarvis_actor_isolated_v1" / "clips"
    default_output = root / ".jarvis" / "voice" / "training_assets" / "jarvis_actor_isolated_v2_master"
    default_exports = root / "exports"
    p = argparse.ArgumentParser(description="Build master v2 isolated actor voice pack with stronger denoise/isolation.")
    p.add_argument("--input-clips-dir", type=Path, default=default_input)
    p.add_argument("--output-pack-dir", type=Path, default=default_output)
    p.add_argument("--exports-dir", type=Path, default=default_exports)
    p.add_argument("--target-rms", type=float, default=-23.2)
    p.add_argument("--min-delta-score", type=float, default=0.03)
    p.add_argument("--preview-count", type=int, default=10)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    in_dir = args.input_clips_dir.resolve()
    out_pack = args.output_pack_dir.resolve()
    exports_dir = args.exports_dir.resolve()

    if not in_dir.exists():
        raise FileNotFoundError(f"Input clips dir not found: {in_dir}")

    clips = sorted(in_dir.glob("*.wav"))
    if not clips:
        raise RuntimeError(f"No wav clips found in {in_dir}")

    data = []
    for clip in clips:
        x, sr = read_wav_mono(clip)
        source, start = parse_clip_info(clip)
        data.append({"path": clip, "x": x, "sr": sr, "source": source, "start_sec": start})

    metrics_orig = [analyze(d["x"], d["sr"]) for d in data]
    profile = make_profile(metrics_orig)

    for d, m in zip(data, metrics_orig):
        x = d["x"].astype(np.float32)
        sr = d["sr"]

        y = (x - np.mean(x)).astype(np.float32)
        y = highpass(y, sr, cutoff_hz=65.0)
        y = dehum(y, sr)

        # Conditional denoise only for hiss-heavy clips to avoid over-gating.
        hiss_gate = max(profile.hiss_ratio + 0.012, 0.055)
        if m.hiss_ratio >= hiss_gate:
            y = nr.reduce_noise(
                y=y,
                sr=sr,
                stationary=True,
                prop_decrease=0.35,
                time_mask_smooth_ms=80,
                freq_mask_smooth_hz=320,
                n_std_thresh_stationary=1.6,
            ).astype(np.float32)

        y = deesser(y, sr, threshold_ratio=0.225, max_reduction_db=2.4)

        # Adaptive spectral tilt towards profile centroid.
        current = analyze(y, sr)
        presence_db = float(np.clip((profile.presence_ratio - current.presence_ratio) * 26.0, -0.8, 0.9))
        air_db = float(np.clip((profile.centroid_hz - current.centroid_hz) / 2400.0, -0.5, 0.5))
        y = presence_tilt_eq(y, sr, presence_db=presence_db, air_db=air_db)

        d["y_pre"] = y

    # Continuity-safe gain smoothing.
    for source in sorted({d["source"] for d in data}):
        idx = [i for i, d in enumerate(data) if d["source"] == source]
        idx.sort(key=lambda i: data[i]["start_sec"])
        raw = []
        for i in idx:
            rms = math.sqrt(float(np.mean(data[i]["y_pre"] * data[i]["y_pre"])))
            raw.append(float(args.target_rms - float(to_db(rms + DB_EPS))))
        smooth = smooth_gains(raw, clamp_db=0.85, alpha=0.22)
        for k, i in enumerate(idx):
            y = normalize_rms(data[i]["y_pre"], smooth[k])
            y = soft_limiter(y, ceiling_dbfs=-1.10)
            y = apply_fade(y, data[i]["sr"], ms=8.0)
            data[i]["y_master"] = y
            data[i]["continuity_gain_db"] = smooth[k]

    decisions: list[Decision] = []
    selected_master = 0

    clear_output(out_pack)
    clips_out = out_pack / "clips"
    clips_out.mkdir(parents=True, exist_ok=True)

    for d, mo in zip(data, metrics_orig):
        mm = analyze(d["y_master"], d["sr"])
        so = total_score(mo, profile)
        sm = total_score(mm, profile)
        ds = sm - so
        dc = mm.clarity_score - mo.clarity_score
        why = guard_reason(mo, mm, ds, dc, args.min_delta_score)
        use_master = why is None

        dst = clips_out / d["path"].name
        if use_master:
            write_wav_mono(dst, d["y_master"], d["sr"])
            selected_master += 1
            reason = "master_safe_improvement"
        else:
            shutil.copy2(d["path"], dst)
            reason = f"kept_input_{why}"

        decisions.append(
            Decision(
                clip_filename=d["path"].name,
                source=d["source"],
                start_sec=d["start_sec"],
                selected=("master" if use_master else "input"),
                reason=reason,
                orig_total_score=so,
                master_total_score=sm,
                delta_total_score=ds,
                orig_clarity=mo.clarity_score,
                master_clarity=mm.clarity_score,
                delta_clarity=dc,
                orig_silence_pct=mo.silence_pct,
                master_silence_pct=mm.silence_pct,
                orig_hiss_ratio=mo.hiss_ratio,
                master_hiss_ratio=mm.hiss_ratio,
                orig_harmonicity=mo.harmonicity,
                master_harmonicity=mm.harmonicity,
                orig_peak_dbfs=mo.peak_dbfs,
                master_peak_dbfs=mm.peak_dbfs,
                orig_rms_dbfs=mo.rms_dbfs,
                master_rms_dbfs=mm.rms_dbfs,
                continuity_gain_db=float(d.get("continuity_gain_db", 0.0)),
            )
        )

    decisions.sort(key=lambda r: (r.source, r.start_sec))

    # CSV report
    csv_path = out_pack / "master_selection.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(decisions[0]).keys()))
        writer.writeheader()
        for row in decisions:
            writer.writerow(asdict(row))

    # Playlist
    playlist = out_pack / "playlist.m3u"
    lines = ["#EXTM3U"]
    for r in decisions:
        lines.append(
            f"#EXTINF:-1,{r.source} start={r.start_sec:.3f}s sel={r.selected} "
            f"dScore={r.delta_total_score:+.3f} dClr={r.delta_clarity:+.3f}"
        )
        lines.append(f"clips/{r.clip_filename}")
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Metadata and readme
    total_sec = 0.0
    clip_paths = sorted(clips_out.glob("*.wav"))
    for c in clip_paths:
        with wave.open(str(c), "rb") as wf:
            total_sec += wf.getnframes() / float(wf.getframerate())

    raw_delta = np.array([r.delta_total_score for r in decisions], dtype=np.float64)
    final_gain = np.array([r.delta_total_score if r.selected == "master" else 0.0 for r in decisions], dtype=np.float64)
    final_clarity = np.array([r.delta_clarity if r.selected == "master" else 0.0 for r in decisions], dtype=np.float64)

    readme = out_pack / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# JARVIS Actor Isolated Pack v2 (Master)",
                "",
                "Built from actor clips with stronger isolation/noise cleanup and non-regression A/B selection.",
                "",
                "## Result",
                "",
                f"- Input clips: {len(decisions)}",
                f"- Master selected: {selected_master}",
                f"- Input retained: {len(decisions) - selected_master}",
                f"- Total duration sec: {total_sec:.3f}",
                f"- Mean raw delta score (master-input): {float(np.mean(raw_delta)):+.3f}",
                f"- Median raw delta score (master-input): {float(np.median(raw_delta)):+.3f}",
                f"- Mean final score gain vs input baseline: {float(np.mean(final_gain)):+.3f}",
                f"- Mean final clarity gain vs input baseline: {float(np.mean(final_clarity)):+.3f}",
                "",
                "## Files",
                "",
                "- `clips/*.wav`",
                "- `master_selection.csv`",
                "- `playlist.m3u`",
                "- `preview_reel.wav`",
                "- `metadata.json`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    meta = {
        "version": 2,
        "created_at": datetime.now().isoformat(),
        "profile": "master_v2",
        "input_clips_dir": str(in_dir),
        "clip_count": len(decisions),
        "selected_master": selected_master,
        "retained_input": len(decisions) - selected_master,
        "total_duration_sec": round(total_sec, 6),
        "processing": {
            "trim_silence": True,
            "highpass": "65 Hz",
            "dehum": [60, 120, 180, 240],
            "noisereduce": {
                "stationary": False,
                "prop_decrease": 0.82,
                "time_mask_smooth_ms": 42,
                "freq_mask_smooth_hz": 240,
            },
            "deesser": {"threshold_ratio": 0.225, "max_reduction_db": 2.4},
            "continuity_gain_smoothing": {"clamp_db": 0.85, "alpha": 0.22},
        },
    }
    (out_pack / "metadata.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    stitch_preview(clip_paths, out_pack / "preview_reel.wav", count=args.preview_count)

    # Export and pointer
    today = datetime.now().strftime("%Y-%m-%d")
    export_zip = exports_dir / f"JARVIS_ACTOR_ISOLATED_VOICE_PACK_MASTER_V2_{today}.zip"
    zip_pack(out_pack, export_zip)
    pointer = out_pack.parents[1] / "ACTIVE_VOICE_PACK.json"
    write_active_pointer(pointer, out_pack, export_zip)

    print(f"[ok] output pack: {out_pack}")
    print(f"[ok] master selected: {selected_master}/{len(decisions)}")
    print(f"[ok] total duration sec: {total_sec:.3f}")
    print(f"[ok] export zip: {export_zip}")
    print(f"[ok] active pointer: {pointer}")


if __name__ == "__main__":
    main()
