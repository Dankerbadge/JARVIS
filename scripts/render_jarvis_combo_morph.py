#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf


def _rms_normalize(audio: np.ndarray, target_rms: float = 0.08) -> np.ndarray:
    rms = float(np.sqrt(np.mean(np.square(audio)) + 1e-12))
    if rms <= 1e-9:
        return audio
    gain = target_rms / rms
    return audio * gain


def _trim(audio: np.ndarray, *, top_db: float = 40.0) -> np.ndarray:
    trimmed, _ = librosa.effects.trim(audio, top_db=top_db)
    if trimmed.size == 0:
        return audio
    return trimmed


def _dtw_time_map(
    source: np.ndarray,
    target: np.ndarray,
    *,
    sr: int,
    n_fft: int,
    hop: int,
) -> tuple[np.ndarray, np.ndarray]:
    src_mfcc = librosa.feature.mfcc(y=source, sr=sr, n_mfcc=20, n_fft=n_fft, hop_length=hop)
    tgt_mfcc = librosa.feature.mfcc(y=target, sr=sr, n_mfcc=20, n_fft=n_fft, hop_length=hop)

    _, wp = librosa.sequence.dtw(X=src_mfcc, Y=tgt_mfcc, metric="cosine")
    # librosa returns warping path in reverse order.
    wp = np.asarray(wp[::-1], dtype=np.int64)
    src_idx = wp[:, 0]
    tgt_idx = wp[:, 1]

    # Build monotonic frame map: target frame -> source frame
    tgt_frames = int(np.ceil(len(target) / hop))
    src_for_tgt = np.zeros(tgt_frames, dtype=np.float64)
    for j in range(tgt_frames):
        match = src_idx[tgt_idx == j]
        if match.size:
            src_for_tgt[j] = float(np.median(match))
        else:
            src_for_tgt[j] = np.nan

    # Fill gaps by interpolation, keeping map monotonic.
    valid = np.flatnonzero(~np.isnan(src_for_tgt))
    if valid.size < 2:
        # Fallback linear duration map.
        src_for_tgt = np.linspace(0, max(1, len(source) / hop - 1), num=tgt_frames)
    else:
        src_for_tgt = np.interp(np.arange(tgt_frames), valid, src_for_tgt[valid])
    src_for_tgt = np.maximum.accumulate(src_for_tgt)

    t_tgt = (np.arange(tgt_frames) * hop) / float(sr)
    t_src = (src_for_tgt * hop) / float(sr)
    return t_tgt, t_src


def _time_warp_to_target(
    source: np.ndarray,
    target: np.ndarray,
    *,
    sr: int,
    n_fft: int = 1024,
    hop: int = 256,
) -> np.ndarray:
    t_tgt, t_src = _dtw_time_map(source, target, sr=sr, n_fft=n_fft, hop=hop)
    tgt_len = len(target)
    t_tgt_samples = np.arange(tgt_len) / float(sr)
    src_positions = np.interp(
        t_tgt_samples,
        t_tgt,
        t_src,
        left=0.0,
        right=(len(source) - 1) / float(sr),
    )
    src_x = np.arange(len(source), dtype=np.float64) / float(sr)
    warped = np.interp(src_positions, src_x, source)
    return warped.astype(np.float32)


def _spectral_envelope_transfer(
    source: np.ndarray,
    target: np.ndarray,
    *,
    sr: int,
    n_fft: int = 2048,
    hop: int = 256,
    strength: float = 0.45,
) -> np.ndarray:
    src_stft = librosa.stft(source, n_fft=n_fft, hop_length=hop)
    tgt_stft = librosa.stft(target, n_fft=n_fft, hop_length=hop)

    src_mag = np.abs(src_stft)
    src_phase = np.angle(src_stft)
    tgt_mag = np.abs(tgt_stft)

    # Global spectral envelope match keeps clarity while transferring tone color.
    src_env = np.mean(src_mag, axis=1) + 1e-8
    tgt_env = np.mean(tgt_mag, axis=1) + 1e-8
    ratio = np.exp(strength * (np.log(tgt_env) - np.log(src_env)))
    ratio = np.clip(ratio, 0.55, 1.75)

    out_mag = src_mag * ratio[:, None]
    out_stft = out_mag * np.exp(1j * src_phase)
    out = librosa.istft(out_stft, hop_length=hop, length=len(source))
    return out.astype(np.float32)


def _soft_saturate(audio: np.ndarray, drive: float) -> np.ndarray:
    drive = max(1.0, float(drive))
    return np.tanh(audio * drive) / np.tanh(drive)


def render_variant(
    source: np.ndarray,
    target: np.ndarray,
    *,
    sr: int,
    strength: float,
    drive: float,
    target_rms: float,
) -> np.ndarray:
    warped = _time_warp_to_target(source, target, sr=sr)
    transferred = _spectral_envelope_transfer(warped, target, sr=sr, strength=strength)
    saturated = _soft_saturate(transferred, drive=drive)
    normalized = _rms_normalize(saturated, target_rms=target_rms)
    peak = np.max(np.abs(normalized)) + 1e-9
    if peak > 0.98:
        normalized = normalized * (0.98 / peak)
    return normalized.astype(np.float32)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render single-voice JARVIS combo morph (no layering).")
    parser.add_argument("--locked", required=True, help="Locked reference clip (tone/cadence target).")
    parser.add_argument("--piper", required=True, help="Piper clip (clean source voice).")
    parser.add_argument("--out-dir", required=True, help="Output directory.")
    parser.add_argument("--date-tag", default="2026-04-17")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    sr = 44100
    locked, _ = librosa.load(str(Path(args.locked).expanduser().resolve()), sr=sr, mono=True)
    piper, _ = librosa.load(str(Path(args.piper).expanduser().resolve()), sr=sr, mono=True)

    locked = _rms_normalize(_trim(locked), target_rms=0.09)
    piper = _rms_normalize(_trim(piper), target_rms=0.09)

    variants = [
        ("MORPH_CORE", 0.36, 1.04, 0.095),
        ("MORPH_FILM", 0.46, 1.08, 0.096),
        ("MORPH_DEEP", 0.54, 1.12, 0.097),
    ]

    for name, strength, drive, rms in variants:
        rendered = render_variant(
            piper,
            locked,
            sr=sr,
            strength=strength,
            drive=drive,
            target_rms=rms,
        )
        out_path = out_dir / f"JARVIS_COMBO_{name}_{args.date_tag}.wav"
        sf.write(str(out_path), rendered, sr, subtype="PCM_16")
        print(out_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
