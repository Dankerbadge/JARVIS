#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

DB_EPS = 1e-12


@dataclass
class AudioData:
    samples: np.ndarray  # shape: (n_samples, n_channels), float32 in [-1, 1]
    mono: np.ndarray  # shape: (n_samples,), float32 in [-1, 1]
    sample_rate: int
    channels: int
    duration_sec: float


def load_wav(path: Path) -> AudioData:
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sample_rate = wf.getframerate()
        sample_width = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sample_width != 2:
        raise ValueError(f"Expected 16-bit WAV for {path}, got {sample_width * 8}-bit")

    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels)
        mono = data.mean(axis=1)
    else:
        data = data.reshape(-1, 1)
        mono = data[:, 0]

    duration_sec = len(mono) / float(sample_rate)
    return AudioData(
        samples=data,
        mono=mono,
        sample_rate=sample_rate,
        channels=channels,
        duration_sec=duration_sec,
    )


def to_db(v: np.ndarray | float) -> np.ndarray | float:
    return 20.0 * np.log10(np.maximum(v, DB_EPS))


def frame_rms_db(x: np.ndarray, sr: int, frame_sec: float = 0.05, hop_sec: float = 0.025) -> tuple[np.ndarray, float]:
    frame = max(1, int(sr * frame_sec))
    hop = max(1, int(sr * hop_sec))
    if len(x) < frame:
        rms = np.array([math.sqrt(float(np.mean(x * x)))], dtype=np.float64)
        return to_db(rms).astype(np.float64), hop_sec

    out: list[float] = []
    for i in range(0, len(x) - frame + 1, hop):
        chunk = x[i : i + frame]
        out.append(math.sqrt(float(np.mean(chunk * chunk))))
    return to_db(np.array(out, dtype=np.float64)).astype(np.float64), hop_sec


def percentile(v: np.ndarray, p: float) -> float:
    return float(np.percentile(v, p))


def pause_stats(rms_db: np.ndarray, hop_sec: float, silence_thr_db: float = -45.0) -> dict[str, float]:
    silent = rms_db < silence_thr_db

    short = 0
    medium = 0
    long = 0
    total_pause_time = 0.0

    run = 0
    for s in silent:
        if s:
            run += 1
        elif run > 0:
            dur = run * hop_sec
            total_pause_time += dur
            if 0.2 <= dur < 0.7:
                short += 1
            elif 0.7 <= dur < 2.0:
                medium += 1
            elif dur >= 2.0:
                long += 1
            run = 0
    if run > 0:
        dur = run * hop_sec
        total_pause_time += dur
        if 0.2 <= dur < 0.7:
            short += 1
        elif 0.7 <= dur < 2.0:
            medium += 1
        elif dur >= 2.0:
            long += 1

    return {
        "short_pauses": float(short),
        "medium_pauses": float(medium),
        "long_pauses": float(long),
        "total_pause_time_sec": float(total_pause_time),
    }


def spectral_profile(x: np.ndarray, sr: int) -> dict[str, float]:
    n_fft = 2048
    hop = 2048  # downsample analysis to keep runtime reasonable
    if len(x) < n_fft:
        return {
            "spectral_centroid_hz": float("nan"),
            "rolloff_85_hz": float("nan"),
            "band_80_250": float("nan"),
            "band_250_1000": float("nan"),
            "band_1000_4000": float("nan"),
            "band_4000_8000": float("nan"),
            "band_8000_16000": float("nan"),
        }

    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    window = np.hanning(n_fft).astype(np.float32)
    accum = np.zeros(len(freqs), dtype=np.float64)
    used = 0

    for i in range(0, len(x) - n_fft + 1, hop):
        frame = x[i : i + n_fft]
        rms = math.sqrt(float(np.mean(frame * frame)))
        if to_db(rms) < -45.0:
            continue
        mag = np.abs(np.fft.rfft(frame * window))
        accum += mag
        used += 1

    if used == 0:
        return {
            "spectral_centroid_hz": float("nan"),
            "rolloff_85_hz": float("nan"),
            "band_80_250": float("nan"),
            "band_250_1000": float("nan"),
            "band_1000_4000": float("nan"),
            "band_4000_8000": float("nan"),
            "band_8000_16000": float("nan"),
        }

    spec = accum / used
    total = float(np.sum(spec) + DB_EPS)
    centroid = float(np.sum(freqs * spec) / total)

    csum = np.cumsum(spec)
    roll_idx = int(np.searchsorted(csum, 0.85 * csum[-1]))
    rolloff = float(freqs[min(roll_idx, len(freqs) - 1)])

    def band(lo: float, hi: float) -> float:
        m = (freqs >= lo) & (freqs < hi)
        return float(np.sum(spec[m]) / total)

    return {
        "spectral_centroid_hz": centroid,
        "rolloff_85_hz": rolloff,
        "band_80_250": band(80, 250),
        "band_250_1000": band(250, 1000),
        "band_1000_4000": band(1000, 4000),
        "band_4000_8000": band(4000, 8000),
        "band_8000_16000": band(8000, 16000),
    }


def pitch_proxy_hz(x: np.ndarray, sr: int) -> dict[str, float]:
    frame = 2048
    hop = 1024
    if len(x) < frame:
        return {"f0_median_hz": float("nan"), "f0_mean_hz": float("nan"), "f0_std_hz": float("nan")}

    window = np.hanning(frame).astype(np.float32)
    lag_min = int(sr / 300.0)
    lag_max = int(sr / 70.0)
    if lag_max >= frame:
        lag_max = frame - 1

    voiced_starts: list[int] = []
    for i in range(0, len(x) - frame + 1, hop):
        chunk = x[i : i + frame]
        rms = math.sqrt(float(np.mean(chunk * chunk)))
        if to_db(rms) > -35.0:
            voiced_starts.append(i)

    if not voiced_starts:
        return {"f0_median_hz": float("nan"), "f0_mean_hz": float("nan"), "f0_std_hz": float("nan")}

    # Subsample for predictable runtime on long files.
    max_frames = 800
    if len(voiced_starts) > max_frames:
        step = len(voiced_starts) / max_frames
        voiced_starts = [voiced_starts[int(i * step)] for i in range(max_frames)]

    vals: list[float] = []
    nfft = 4096
    for start in voiced_starts:
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
        return {"f0_median_hz": float("nan"), "f0_mean_hz": float("nan"), "f0_std_hz": float("nan")}

    arr = np.array(vals, dtype=np.float64)
    return {
        "f0_median_hz": float(np.median(arr)),
        "f0_mean_hz": float(np.mean(arr)),
        "f0_std_hz": float(np.std(arr)),
    }


def segment_quality_windows(
    x: np.ndarray, sr: int, win_sec: float = 6.0, hop_sec: float = 3.0
) -> dict[str, list[dict[str, float]]]:
    win = int(sr * win_sec)
    hop = int(sr * hop_sec)
    if len(x) < win:
        return {"top_windows": [], "bottom_windows": []}

    n_fft = 4096
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)

    def band_ratio(spec: np.ndarray, lo: float, hi: float) -> float:
        m = (freqs >= lo) & (freqs < hi)
        return float(np.sum(spec[m]) / (np.sum(spec) + DB_EPS))

    scored: list[dict[str, float]] = []
    for start in range(0, len(x) - win + 1, hop):
        segment = x[start : start + win]
        rms = math.sqrt(float(np.mean(segment * segment)))
        rms_db = float(to_db(rms))

        # Internal silence estimate (50 ms frames / 25 ms hop)
        seg_rms_db, _ = frame_rms_db(segment, sr, frame_sec=0.05, hop_sec=0.025)
        silence_pct = float(np.mean(seg_rms_db < -45.0) * 100.0)

        # Coarse spectral profile from averaged short FFTs.
        mags = []
        local_hop = n_fft // 2
        window = np.hanning(n_fft).astype(np.float32)
        for i in range(0, len(segment) - n_fft + 1, local_hop):
            frame = segment[i : i + n_fft]
            if to_db(math.sqrt(float(np.mean(frame * frame)))) < -45.0:
                continue
            mags.append(np.abs(np.fft.rfft(frame * window)))
        if not mags:
            continue
        spec = np.mean(np.vstack(mags), axis=0)

        low_mid = band_ratio(spec, 80.0, 1000.0)
        body = band_ratio(spec, 80.0, 4000.0)
        high = band_ratio(spec, 4000.0, 16000.0)
        hf_ratio = high / max(body, DB_EPS)

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
            {
                "start_sec": float(start / sr),
                "end_sec": float((start + win) / sr),
                "score": float(score),
                "rms_dbfs": rms_db,
                "silence_pct": silence_pct,
                "hf_to_body_ratio": float(hf_ratio),
                "low_mid_ratio": float(low_mid),
            }
        )

    scored.sort(key=lambda d: d["score"], reverse=True)
    top = scored[:8]
    bottom = sorted(scored[-8:], key=lambda d: d["score"])
    return {"top_windows": top, "bottom_windows": bottom}


def analyze_file(path: Path) -> dict[str, Any]:
    audio = load_wav(path)
    mono = audio.mono

    peak = float(np.max(np.abs(mono)))
    rms = float(math.sqrt(float(np.mean(mono * mono))))

    rms_db, hop_sec = frame_rms_db(mono, audio.sample_rate, frame_sec=0.05, hop_sec=0.025)

    clip_ratio = float(np.mean(np.abs(mono) >= 0.999) * 100.0)

    data: dict[str, Any] = {
        "path": str(path),
        "sample_rate_hz": audio.sample_rate,
        "channels": audio.channels,
        "duration_sec": audio.duration_sec,
        "peak_dbfs": float(to_db(peak)),
        "rms_dbfs": float(to_db(rms)),
        "crest_factor_db": float(to_db(peak / max(rms, DB_EPS))),
        "dc_offset": float(np.mean(mono)),
        "clip_percent": clip_ratio,
        "silence_under_minus50db_percent": float(np.mean(rms_db < -50.0) * 100.0),
        "silence_under_minus45db_percent": float(np.mean(rms_db < -45.0) * 100.0),
        "silence_under_minus40db_percent": float(np.mean(rms_db < -40.0) * 100.0),
        "frame_rms_p10_dbfs": percentile(rms_db, 10),
        "frame_rms_p50_dbfs": percentile(rms_db, 50),
        "frame_rms_p90_dbfs": percentile(rms_db, 90),
        "frame_rms_dynamic_span_db": percentile(rms_db, 90) - percentile(rms_db, 10),
    }

    if audio.channels == 2:
        left = audio.samples[:, 0]
        right = audio.samples[:, 1]
        lr = float(np.corrcoef(left, right)[0, 1])
        lr_rms_l = float(math.sqrt(float(np.mean(left * left))))
        lr_rms_r = float(math.sqrt(float(np.mean(right * right))))
        lr_delta = float(to_db((lr_rms_l + DB_EPS) / (lr_rms_r + DB_EPS)))
        data["stereo_lr_correlation"] = lr
        data["stereo_lr_level_delta_db"] = lr_delta

    data.update(pause_stats(rms_db, hop_sec, silence_thr_db=-45.0))
    data.update(spectral_profile(mono, audio.sample_rate))
    data.update(pitch_proxy_hz(mono, audio.sample_rate))
    data.update(segment_quality_windows(mono, audio.sample_rate))

    return data


def format_report(results: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# JARVIS Voice Study")
    lines.append("")
    lines.append("This report analyzes recording quality and voice characteristics to support clean, artifact-aware voice model preparation.")
    lines.append("")

    for r in results:
        name = Path(r["path"]).name
        lines.append(f"## {name}")
        lines.append("")
        lines.append(f"- Duration: {r['duration_sec']:.2f} s")
        lines.append(f"- Format: {r['sample_rate_hz']} Hz, {r['channels']} ch")
        lines.append(f"- Peak: {r['peak_dbfs']:.2f} dBFS")
        lines.append(f"- RMS: {r['rms_dbfs']:.2f} dBFS")
        lines.append(f"- Crest Factor: {r['crest_factor_db']:.2f} dB")
        lines.append(f"- Clipping: {r['clip_percent']:.4f}%")
        lines.append(f"- Silence (< -45 dBFS): {r['silence_under_minus45db_percent']:.2f}%")
        lines.append(f"- Dynamic Span (P90-P10): {r['frame_rms_dynamic_span_db']:.2f} dB")
        lines.append(f"- Estimated Pitch Median: {r['f0_median_hz']:.1f} Hz")
        lines.append(f"- Spectral Centroid: {r['spectral_centroid_hz']:.1f} Hz")
        lines.append(f"- 85% Roll-off: {r['rolloff_85_hz']:.1f} Hz")
        if "stereo_lr_correlation" in r:
            lines.append(f"- Stereo L/R Correlation: {r['stereo_lr_correlation']:.4f}")
            lines.append(f"- Stereo L/R Level Delta: {r['stereo_lr_level_delta_db']:.2f} dB")
        lines.append(f"- Pause Counts: short={int(r['short_pauses'])}, medium={int(r['medium_pauses'])}, long={int(r['long_pauses'])}")
        if r.get("top_windows"):
            top = r["top_windows"][0]
            lines.append(
                "- Best 6s Window: "
                f"{top['start_sec']:.1f}s-{top['end_sec']:.1f}s "
                f"(score={top['score']:.1f}, hf/body={top['hf_to_body_ratio']:.2f})"
            )
        if r.get("bottom_windows"):
            worst = r["bottom_windows"][0]
            lines.append(
                "- Worst 6s Window: "
                f"{worst['start_sec']:.1f}s-{worst['end_sec']:.1f}s "
                f"(score={worst['score']:.1f}, hf/body={worst['hf_to_body_ratio']:.2f})"
            )
        lines.append("")

    if len(results) >= 2:
        a, b = results[0], results[1]
        lines.append("## Cross-File Consistency")
        lines.append("")
        lines.append(f"- Pitch median delta: {abs(a['f0_median_hz'] - b['f0_median_hz']):.1f} Hz")
        lines.append(f"- RMS delta: {abs(a['rms_dbfs'] - b['rms_dbfs']):.2f} dB")
        lines.append(f"- Spectral centroid delta: {abs(a['spectral_centroid_hz'] - b['spectral_centroid_hz']):.1f} Hz")
        lines.append("")

    lines.append("## Recommended Preprocessing Targets")
    lines.append("")
    lines.append("- Keep source at 48 kHz while editing; export training-ready WAV as mono 48 kHz, 16-bit PCM.")
    lines.append("- Remove only non-stationary background noise; avoid aggressive denoise that smears consonants.")
    lines.append("- Use light de-reverb only if room tail is obvious in pauses; prioritize preserving natural timbre.")
    lines.append("- Loudness-normalize segments to a consistent speech RMS window before model ingestion.")
    lines.append("- Exclude long pauses and heavily noisy segments from training clips.")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    study_dir = Path("/Users/dankerbadge/Documents/J.A.R.V.I.S/analysis/jarvis_study")
    files = [
        study_dir / "JARVIS_1.wav",
        study_dir / "JARVIS_II.wav",
    ]

    results = [analyze_file(p) for p in files]

    out_json = study_dir / "voice_study_metrics.json"
    out_md = study_dir / "voice_study_report.md"

    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    out_md.write_text(format_report(results), encoding="utf-8")

    print(f"Wrote: {out_json}")
    print(f"Wrote: {out_md}")


if __name__ == "__main__":
    main()
