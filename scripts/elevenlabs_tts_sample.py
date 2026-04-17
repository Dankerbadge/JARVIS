#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs

MOVIE_FX_PRESETS: dict[str, str] = {
    "movie_strict": (
        "afftdn=nf=-22,"
        "highpass=f=85,"
        "lowpass=f=7600,"
        "deesser=i=0.24:m=0.6:f=0.55,"
        "equalizer=f=220:t=q:w=1.2:g=-3,"
        "equalizer=f=480:t=q:w=1.0:g=-2,"
        "equalizer=f=1800:t=q:w=1.1:g=2.0,"
        "equalizer=f=3000:t=q:w=1.0:g=2.8,"
        "equalizer=f=6200:t=q:w=1.2:g=-1.8,"
        "acompressor=threshold=0.11:ratio=2.8:attack=7:release=70:makeup=2.2,"
        "aecho=0.78:0.18:34:0.05,"
        "alimiter=limit=0.90:attack=5:release=50:level=false"
    ),
    "movie_ultracold": (
        "afftdn=nf=-24,"
        "highpass=f=95,"
        "lowpass=f=6900,"
        "deesser=i=0.30:m=0.65:f=0.58,"
        "equalizer=f=200:t=q:w=1.1:g=-3.5,"
        "equalizer=f=430:t=q:w=1.1:g=-2.6,"
        "equalizer=f=1700:t=q:w=1.1:g=2.2,"
        "equalizer=f=2850:t=q:w=1.0:g=3.2,"
        "equalizer=f=5600:t=q:w=1.3:g=-2.4,"
        "acompressor=threshold=0.10:ratio=3.1:attack=6:release=65:makeup=2.5,"
        "chorus=0.58:0.26:16|23:0.08|0.07:0.24|0.18:0.7|0.62,"
        "aecho=0.80:0.16:28:0.04,"
        "alimiter=limit=0.88:attack=5:release=55:level=false"
    ),
    "movie_depth": (
        "afftdn=nf=-23,"
        "highpass=f=78,"
        "lowpass=f=7400,"
        "asetrate=44100*0.962,atempo=1.0395,"
        "deesser=i=0.27:m=0.64:f=0.56,"
        "equalizer=f=120:t=q:w=0.9:g=3.2,"
        "equalizer=f=220:t=q:w=1.1:g=2.1,"
        "equalizer=f=360:t=q:w=1.1:g=-2.5,"
        "equalizer=f=820:t=q:w=1.2:g=-1.2,"
        "equalizer=f=1900:t=q:w=1.2:g=1.3,"
        "equalizer=f=3000:t=q:w=1.0:g=2.1,"
        "equalizer=f=5600:t=q:w=1.2:g=-2.2,"
        "acompressor=threshold=0.095:ratio=3.4:attack=5:release=85:makeup=3.2,"
        "chorus=0.60:0.22:17|23:0.06|0.05:0.20|0.15:0.8|0.7,"
        "aecho=0.82:0.14:38:0.07,"
        "alimiter=limit=0.87:attack=5:release=60:level=false"
    ),
    "movie_depth_extreme": (
        "afftdn=nf=-25,"
        "highpass=f=85,"
        "lowpass=f=7000,"
        "asetrate=44100*0.952,atempo=1.0504,"
        "deesser=i=0.30:m=0.70:f=0.58,"
        "equalizer=f=110:t=q:w=0.85:g=3.8,"
        "equalizer=f=210:t=q:w=1.0:g=2.6,"
        "equalizer=f=340:t=q:w=1.1:g=-3.0,"
        "equalizer=f=760:t=q:w=1.1:g=-1.6,"
        "equalizer=f=1750:t=q:w=1.1:g=1.5,"
        "equalizer=f=2850:t=q:w=1.0:g=2.5,"
        "equalizer=f=5200:t=q:w=1.3:g=-2.6,"
        "acompressor=threshold=0.088:ratio=3.8:attack=4:release=95:makeup=3.8,"
        "chorus=0.58:0.24:18|25:0.07|0.06:0.24|0.18:0.78|0.68,"
        "aecho=0.84:0.12:44:0.09,"
        "alimiter=limit=0.86:attack=5:release=65:level=false"
    ),
    "movie_depth_dry": (
        "afftdn=nf=-20,"
        "highpass=f=72,"
        "lowpass=f=9200,"
        "asetrate=44100*0.972,atempo=1.0288,"
        "deesser=i=0.20:m=0.50:f=0.52,"
        "equalizer=f=105:t=q:w=0.9:g=3.6,"
        "equalizer=f=180:t=q:w=1.0:g=2.4,"
        "equalizer=f=320:t=q:w=1.1:g=-1.9,"
        "equalizer=f=760:t=q:w=1.2:g=-0.9,"
        "equalizer=f=1700:t=q:w=1.1:g=1.2,"
        "equalizer=f=2900:t=q:w=1.0:g=1.8,"
        "equalizer=f=6200:t=q:w=1.2:g=-1.2,"
        "acompressor=threshold=0.105:ratio=3.0:attack=5:release=80:makeup=3.0,"
        "alimiter=limit=0.88:attack=4:release=55:level=false"
    ),
    "movie_depth_dry_extreme": (
        "afftdn=nf=-21,"
        "highpass=f=75,"
        "lowpass=f=9000,"
        "asetrate=44100*0.958,atempo=1.0438,"
        "deesser=i=0.22:m=0.54:f=0.53,"
        "equalizer=f=95:t=q:w=0.85:g=4.2,"
        "equalizer=f=170:t=q:w=0.95:g=2.8,"
        "equalizer=f=300:t=q:w=1.1:g=-2.2,"
        "equalizer=f=690:t=q:w=1.1:g=-1.1,"
        "equalizer=f=1600:t=q:w=1.1:g=1.2,"
        "equalizer=f=2750:t=q:w=1.0:g=1.9,"
        "equalizer=f=6000:t=q:w=1.2:g=-1.5,"
        "acompressor=threshold=0.098:ratio=3.3:attack=4:release=90:makeup=3.4,"
        "alimiter=limit=0.87:attack=4:release=60:level=false"
    ),
    "movie_match_clean": (
        "highpass=f=62,"
        "lowpass=f=14000,"
        "asetrate=44100*0.928,atempo=1.0776,"
        "deesser=i=0.16:m=0.40:f=0.50,"
        "equalizer=f=92:t=q:w=0.9:g=3.8,"
        "equalizer=f=155:t=q:w=1.0:g=2.2,"
        "equalizer=f=285:t=q:w=1.0:g=-1.1,"
        "equalizer=f=560:t=q:w=1.1:g=-0.5,"
        "equalizer=f=2100:t=q:w=1.0:g=1.8,"
        "equalizer=f=3200:t=q:w=0.95:g=2.4,"
        "equalizer=f=5200:t=q:w=1.0:g=1.2,"
        "equalizer=f=9800:t=q:w=1.1:g=0.6,"
        "acompressor=threshold=0.120:ratio=2.2:attack=8:release=90:makeup=2.6,"
        "alimiter=limit=0.93:attack=4:release=55:level=false,"
        "loudnorm=I=-16:TP=-1.5:LRA=9"
    ),
    "movie_match_deep": (
        "highpass=f=60,"
        "lowpass=f=13500,"
        "asetrate=44100*0.906,atempo=1.1038,"
        "deesser=i=0.16:m=0.40:f=0.50,"
        "equalizer=f=88:t=q:w=0.9:g=4.6,"
        "equalizer=f=145:t=q:w=1.0:g=3.0,"
        "equalizer=f=270:t=q:w=1.0:g=-1.2,"
        "equalizer=f=520:t=q:w=1.1:g=-0.6,"
        "equalizer=f=1800:t=q:w=1.0:g=1.3,"
        "equalizer=f=2800:t=q:w=0.95:g=2.0,"
        "equalizer=f=4300:t=q:w=1.0:g=1.5,"
        "equalizer=f=8500:t=q:w=1.1:g=0.4,"
        "acompressor=threshold=0.114:ratio=2.5:attack=7:release=100:makeup=3.0,"
        "alimiter=limit=0.92:attack=4:release=60:level=false,"
        "loudnorm=I=-16.5:TP=-1.5:LRA=9"
    ),
    "startup_parity": (
        "highpass=f=82,"
        "lowpass=f=14500,"
        "asetrate=44100*0.948,atempo=1.0548,"
        "deesser=i=0.14:m=0.34:f=0.48,"
        "equalizer=f=130:t=q:w=0.9:g=2.0,"
        "equalizer=f=300:t=q:w=1.0:g=-1.2,"
        "equalizer=f=950:t=q:w=1.1:g=-0.8,"
        "equalizer=f=2100:t=q:w=1.0:g=2.5,"
        "equalizer=f=3400:t=q:w=0.9:g=3.3,"
        "equalizer=f=5200:t=q:w=1.0:g=2.1,"
        "equalizer=f=8600:t=q:w=1.1:g=2.6,"
        "acompressor=threshold=0.112:ratio=2.4:attack=7:release=90:makeup=2.8,"
        "aecho=0.72:0.12:26:0.03,"
        "alimiter=limit=0.92:attack=4:release=55:level=false,"
        "loudnorm=I=-17:TP=-1.5:LRA=8"
    ),
    "startup_parity_deep": (
        "highpass=f=78,"
        "lowpass=f=14200,"
        "asetrate=44100*0.928,atempo=1.0776,"
        "deesser=i=0.14:m=0.34:f=0.48,"
        "equalizer=f=115:t=q:w=0.9:g=2.8,"
        "equalizer=f=260:t=q:w=1.0:g=-1.1,"
        "equalizer=f=780:t=q:w=1.1:g=-0.7,"
        "equalizer=f=1950:t=q:w=1.0:g=2.2,"
        "equalizer=f=3200:t=q:w=0.9:g=2.7,"
        "equalizer=f=4900:t=q:w=1.0:g=1.9,"
        "equalizer=f=8200:t=q:w=1.1:g=2.2,"
        "acompressor=threshold=0.106:ratio=2.8:attack=6:release=95:makeup=3.1,"
        "aecho=0.74:0.11:28:0.03,"
        "alimiter=limit=0.91:attack=4:release=58:level=false,"
        "loudnorm=I=-17:TP=-1.5:LRA=8"
    ),
    "startup_parity_extreme": (
        "highpass=f=94,"
        "lowpass=f=16000,"
        "asetrate=44100*0.942,atempo=1.0616,"
        "deesser=i=0.12:m=0.30:f=0.46,"
        "equalizer=f=160:t=q:w=0.9:g=1.4,"
        "equalizer=f=420:t=q:w=1.0:g=-1.4,"
        "equalizer=f=1100:t=q:w=1.0:g=-1.0,"
        "equalizer=f=2400:t=q:w=0.9:g=3.4,"
        "equalizer=f=3800:t=q:w=0.9:g=4.2,"
        "equalizer=f=6200:t=q:w=0.9:g=4.9,"
        "equalizer=f=9800:t=q:w=0.9:g=4.6,"
        "equalizer=f=13200:t=q:w=0.9:g=3.8,"
        "acompressor=threshold=0.116:ratio=2.3:attack=6:release=82:makeup=2.8,"
        "acrusher=bits=11:mode=lin:aa=1:mix=0.10,"
        "aecho=0.70:0.10:24:0.03,"
        "alimiter=limit=0.92:attack=4:release=52:level=false,"
        "loudnorm=I=-17:TP=-1.5:LRA=8"
    ),
    "startup_clean_light": (
        "highpass=f=80,"
        "lowpass=f=14500,"
        "deesser=i=0.15:m=0.35:f=0.48,"
        "equalizer=f=170:t=q:w=1.0:g=1.6,"
        "equalizer=f=420:t=q:w=1.0:g=-1.0,"
        "equalizer=f=2200:t=q:w=1.0:g=1.6,"
        "equalizer=f=3600:t=q:w=0.9:g=2.0,"
        "equalizer=f=6200:t=q:w=0.9:g=2.2,"
        "acompressor=threshold=0.108:ratio=2.2:attack=6:release=85:makeup=2.4,"
        "alimiter=limit=0.93:attack=4:release=55:level=false,"
        "loudnorm=I=-18:TP=-1.5:LRA=7"
    ),
    "startup_clean_deep_dry": (
        "highpass=f=60,"
        "lowpass=f=16000,"
        "deesser=i=0.12:m=0.34:f=0.50,"
        "equalizer=f=95:t=q:w=0.9:g=3.8,"
        "equalizer=f=180:t=q:w=1.0:g=2.4,"
        "equalizer=f=320:t=q:w=1.0:g=-1.5,"
        "equalizer=f=2400:t=q:w=0.95:g=1.8,"
        "equalizer=f=4200:t=q:w=0.95:g=2.2,"
        "equalizer=f=6800:t=q:w=1.0:g=1.5,"
        "acompressor=threshold=0.110:ratio=2.4:attack=8:release=100:makeup=2.8,"
        "alimiter=limit=0.94:attack=4:release=58:level=false,"
        "loudnorm=I=-17:TP=-1.5:LRA=8"
    ),
    "startup_clean_deep_dryer": (
        "highpass=f=58,"
        "lowpass=f=15500,"
        "deesser=i=0.13:m=0.36:f=0.50,"
        "equalizer=f=90:t=q:w=0.9:g=4.8,"
        "equalizer=f=160:t=q:w=1.0:g=3.0,"
        "equalizer=f=300:t=q:w=1.0:g=-2.2,"
        "equalizer=f=700:t=q:w=1.0:g=-1.0,"
        "equalizer=f=2000:t=q:w=0.95:g=1.4,"
        "equalizer=f=3400:t=q:w=0.95:g=1.8,"
        "equalizer=f=5800:t=q:w=1.0:g=1.2,"
        "acompressor=threshold=0.104:ratio=2.7:attack=7:release=105:makeup=3.2,"
        "alimiter=limit=0.93:attack=4:release=60:level=false,"
        "loudnorm=I=-17.5:TP=-1.5:LRA=8"
    ),
    "jarvis_md_subtle": (
        "highpass=f=78,"
        "lowpass=f=14500,"
        "deesser=i=0.13:m=0.34:f=0.49,"
        "equalizer=f=260:t=q:w=1.0:g=-2.2,"
        "equalizer=f=3300:t=q:w=0.95:g=2.0,"
        "equalizer=f=8600:t=q:w=1.1:g=0.9,"
        "acompressor=threshold=0.120:ratio=2.2:attack=14:release=90:makeup=1.8,"
        "alimiter=limit=0.95:attack=4:release=60:level=false,"
        "loudnorm=I=-18:TP=-1.5:LRA=8"
    ),
    "jarvis_md_subtle_deep": (
        "highpass=f=72,"
        "lowpass=f=14200,"
        "deesser=i=0.13:m=0.34:f=0.49,"
        "equalizer=f=110:t=q:w=0.9:g=2.1,"
        "equalizer=f=280:t=q:w=1.0:g=-2.5,"
        "equalizer=f=3200:t=q:w=0.95:g=1.7,"
        "equalizer=f=7800:t=q:w=1.1:g=0.7,"
        "acompressor=threshold=0.118:ratio=2.3:attack=12:release=95:makeup=2.1,"
        "alimiter=limit=0.95:attack=4:release=60:level=false,"
        "loudnorm=I=-18:TP=-1.5:LRA=8"
    ),
}


def _audio_to_bytes(audio: Any) -> bytes:
    if audio is None:
        return b""
    if isinstance(audio, (bytes, bytearray)):
        return bytes(audio)
    if isinstance(audio, str):
        return audio.encode("utf-8")
    if hasattr(audio, "read"):
        return bytes(audio.read())
    if isinstance(audio, Iterable):
        chunks: list[bytes] = []
        for chunk in audio:
            if chunk is None:
                continue
            if isinstance(chunk, str):
                chunks.append(chunk.encode("utf-8"))
            else:
                chunks.append(bytes(chunk))
        return b"".join(chunks)
    return bytes(audio)


def _resolve_voice_id(repo_root: Path, explicit_voice_id: str | None) -> str:
    if explicit_voice_id:
        return explicit_voice_id

    metadata_path = repo_root / ".jarvis" / "voice" / "ELEVENLABS_ACTOR_CLONE.json"
    if metadata_path.exists():
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            vid = str(data.get("voice_id") or "").strip()
            if vid:
                return vid
        except (OSError, json.JSONDecodeError):
            pass

    openclaw_path = Path.home() / ".openclaw" / "openclaw.json"
    if openclaw_path.exists():
        try:
            cfg = json.loads(openclaw_path.read_text(encoding="utf-8"))
            vid = str(
                cfg.get("talk", {})
                .get("providers", {})
                .get("elevenlabs", {})
                .get("voiceId")
                or ""
            ).strip()
            if vid:
                return vid
        except (OSError, json.JSONDecodeError):
            pass

    raise RuntimeError(
        "No voice_id found. Pass --voice-id or run scripts/enable_actor_voice_clone.py first."
    )


def _apply_movie_fx(
    *,
    input_path: Path,
    output_path: Path,
    preset: str,
    ffmpeg_bin: str,
) -> None:
    preset_name = str(preset or "").strip()
    out_ext = output_path.suffix.lower()
    encode_args: list[str] = []
    if out_ext == ".mp3":
        encode_args = [
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            "-minrate",
            "192k",
            "-maxrate",
            "192k",
            "-bufsize",
            "384k",
        ]
    elif out_ext == ".wav":
        encode_args = ["-c:a", "pcm_s16le"]

    if preset_name == "startup_parity_cinematic":
        voice_chain = (
            "highpass=f=92,"
            "lowpass=f=16000,"
            "asetrate=44100*0.944,atempo=1.0593,"
            "deesser=i=0.12:m=0.30:f=0.46,"
            "equalizer=f=170:t=q:w=0.9:g=1.6,"
            "equalizer=f=430:t=q:w=1.0:g=-1.3,"
            "equalizer=f=1180:t=q:w=1.0:g=-0.8,"
            "equalizer=f=2500:t=q:w=0.9:g=3.6,"
            "equalizer=f=3900:t=q:w=0.9:g=4.5,"
            "equalizer=f=6400:t=q:w=0.9:g=5.2,"
            "equalizer=f=9800:t=q:w=0.9:g=5.1,"
            "equalizer=f=13400:t=q:w=0.9:g=4.0,"
            "acompressor=threshold=0.094:ratio=4.0:attack=4:release=82:makeup=3.8,"
            "aecho=0.70:0.10:24:0.03"
        )
        noise_chain = "highpass=f=4300,lowpass=f=15000,volume=0.070"
        mix_chain = (
            "[v][n]amix=inputs=2:weights=1 0.34:normalize=0:duration=shortest,"
            "alimiter=limit=0.92:attack=4:release=52:level=false,"
            "loudnorm=I=-19:TP=-1.5:LRA=6"
        )
        cmd = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=color=white:amplitude=0.0038:r=44100:d=30",
            "-filter_complex",
            f"[0:a]{voice_chain}[v];[1:a]{noise_chain}[n];{mix_chain}",
            "-shortest",
            "-ar",
            "44100",
            "-ac",
            "1",
            *encode_args,
            str(output_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"ffmpeg movie-fx failed: {detail}")
        return

    filter_chain = MOVIE_FX_PRESETS.get(preset_name)
    if not filter_chain:
        raise RuntimeError(f"Unknown movie-fx preset: {preset_name}")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-af",
        filter_chain,
        "-ar",
        "44100",
        "-ac",
        "1",
        *encode_args,
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"ffmpeg movie-fx failed: {detail}")


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Render a sample MP3 from ElevenLabs voice clone.")
    parser.add_argument(
        "--text",
        default=(
            "Good evening. All systems are now operating at peak efficiency. "
            "I am prepared to assist with your next directive."
        ),
    )
    parser.add_argument("--voice-id", default=None)
    parser.add_argument("--model-id", default="eleven_v3")
    parser.add_argument("--output-format", default="mp3_44100_128")
    parser.add_argument("--stability", type=float, default=None)
    parser.add_argument("--similarity-boost", type=float, default=None)
    parser.add_argument("--style", type=float, default=None)
    parser.add_argument("--speed", type=float, default=None)
    parser.add_argument(
        "--use-speaker-boost",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument(
        "--movie-fx-preset",
        choices=[
            "none",
            "movie_strict",
            "movie_ultracold",
            "movie_depth",
            "movie_depth_extreme",
            "movie_depth_dry",
            "movie_depth_dry_extreme",
            "movie_match_clean",
            "movie_match_deep",
            "startup_parity",
            "startup_parity_deep",
            "startup_parity_extreme",
            "startup_parity_cinematic",
            "startup_clean_light",
            "startup_clean_deep_dry",
            "startup_clean_deep_dryer",
            "jarvis_md_subtle",
            "jarvis_md_subtle_deep",
        ],
        default="jarvis_md_subtle",
        help="Optional cinematic post-processing profile applied with ffmpeg.",
    )
    parser.add_argument("--ffmpeg-bin", default="ffmpeg")
    parser.add_argument(
        "--out",
        type=Path,
        default=repo_root / "exports" / "voice_samples" / "jarvis_actor_match_sample_sdk.mp3",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(dotenv_path=repo_root / ".env", override=False)
    load_dotenv(dotenv_path=Path.home() / ".openclaw" / ".env", override=False)

    api_key = str(os.getenv("ELEVENLABS_API_KEY") or "").strip()
    if not api_key:
        print("[error] ELEVENLABS_API_KEY is not set.")
        print("[hint] add it to .env or export it in your shell.")
        return 2

    voice_id = _resolve_voice_id(repo_root, args.voice_id)
    client = ElevenLabs(api_key=api_key)
    voice_settings: dict[str, Any] | None = None
    if any(
        value is not None
        for value in (
            args.stability,
            args.similarity_boost,
            args.style,
            args.speed,
            args.use_speaker_boost,
        )
    ):
        voice_settings = {}
        if args.stability is not None:
            voice_settings["stability"] = float(args.stability)
        if args.similarity_boost is not None:
            voice_settings["similarity_boost"] = float(args.similarity_boost)
        if args.style is not None:
            voice_settings["style"] = float(args.style)
        if args.speed is not None:
            voice_settings["speed"] = float(args.speed)
        if args.use_speaker_boost is not None:
            voice_settings["use_speaker_boost"] = bool(args.use_speaker_boost)

    # Newer SDK path (matches ElevenLabs docs).
    audio: Any
    try:
        convert_kwargs: dict[str, Any] = {
            "text": str(args.text),
            "voice_id": voice_id,
            "model_id": str(args.model_id),
            "output_format": str(args.output_format),
        }
        if voice_settings:
            convert_kwargs["voice_settings"] = voice_settings
        audio = client.text_to_speech.convert(
            **convert_kwargs,
        )
    except AttributeError:
        # Backward-compatible fallback.
        generate_kwargs: dict[str, Any] = {
            "text": str(args.text),
            "voice": voice_id,
            "model": str(args.model_id),
        }
        if voice_settings:
            generate_kwargs["voice_settings"] = voice_settings
        audio = client.generate(
            **generate_kwargs,
        )

    data = _audio_to_bytes(audio)
    if not data:
        print("[error] ElevenLabs returned empty audio payload.")
        return 3

    out_path = args.out.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    preset = str(args.movie_fx_preset or "none").strip().lower()
    if preset == "none":
        out_path.write_bytes(data)
    else:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".mp3",
            prefix="jarvis_tts_raw_",
            delete=False,
        ) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            _apply_movie_fx(
                input_path=tmp_path,
                output_path=out_path,
                preset=preset,
                ffmpeg_bin=str(args.ffmpeg_bin),
            )
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
    print(f"[ok] sample rendered: {out_path}")
    print(
        f"[ok] bytes={len(data)} voice_id={voice_id} model_id={args.model_id} "
        f"movie_fx={preset} voice_settings={voice_settings or 'provider_default'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
