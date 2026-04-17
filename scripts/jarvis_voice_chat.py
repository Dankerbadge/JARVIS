#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import ctypes
import difflib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_IMPORT_ERROR: Exception | None = None
try:
    import numpy as np
    import requests
    import sounddevice as sd
    import soundfile as sf
    from faster_whisper import WhisperModel
    from piper.config import SynthesisConfig
    from piper.voice import PiperVoice
except Exception as exc:  # pragma: no cover - import guard for CLI ergonomics
    _IMPORT_ERROR = exc
    np = None  # type: ignore[assignment]
    requests = None  # type: ignore[assignment]
    sd = None  # type: ignore[assignment]
    sf = None  # type: ignore[assignment]
    WhisperModel = Any  # type: ignore[assignment]
    SynthesisConfig = Any  # type: ignore[assignment]
    PiperVoice = Any  # type: ignore[assignment]

MERRIAM_COLLEGIATE_API_TEMPLATE = (
    "https://www.dictionaryapi.com/api/v3/references/collegiate/json/{word}?key={api_key}"
)
PRONUNCIATION_MISSPELLINGS = {
    "pronounciation": "pronunciation",
    "pronounciations": "pronunciations",
    "anunciation": "enunciation",
    "annuciation": "enunciation",
    "announciation": "enunciation",
    "definately": "definitely",
    "seperate": "separate",
    "occured": "occurred",
    "recieve": "receive",
}
ACRONYM_WORD_EXCEPTIONS = {
    "NASA",
    "NATO",
    "RADAR",
    "LASER",
    "SCUBA",
    "UNESCO",
    "OPEC",
}


@dataclass
class PronunciationSettings:
    reference_mode: str
    lexicon: dict[str, str]
    merriam_api_key: str | None
    merriam_cache_path: Path
    merriam_cache: dict[str, str]
    max_merriam_lookups: int
    missing_key_warned: bool = False
    cache_dirty: bool = False


def _load_pronunciation_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in payload.items():
        key_str = str(key or "").strip().lower()
        value_str = str(value or "").strip()
        if key_str and value_str:
            out[key_str] = value_str
    return out


def _write_pronunciation_cache(settings: PronunciationSettings) -> None:
    if not settings.cache_dirty:
        return
    try:
        settings.merriam_cache_path.parent.mkdir(parents=True, exist_ok=True)
        settings.merriam_cache_path.write_text(
            json.dumps(settings.merriam_cache, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        settings.cache_dirty = False
    except Exception:
        pass


def _with_source_case(source: str, replacement: str) -> str:
    if source[:1].isupper() and source[1:].islower():
        return replacement.capitalize()
    return replacement


def _replace_word_case_insensitive(text: str, source: str, replacement: str) -> str:
    if not source or not replacement:
        return text
    pattern = re.compile(rf"\b{re.escape(source)}\b", flags=re.IGNORECASE)
    return pattern.sub(lambda match: _with_source_case(match.group(0), replacement), text)


def _strip_spoken_markup(text: str) -> str:
    clean = str(text or "")
    clean = re.sub(r"```.+?```", " ", clean, flags=re.DOTALL)
    clean = re.sub(r"`([^`]+)`", r"\1", clean)
    clean = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", clean)
    clean = re.sub(r"https?://\S+", "that link", clean)
    clean = clean.replace("&", " and ")
    clean = re.sub(r"(?<=\w)/(?!/)(?=\w)", " or ", clean)
    clean = re.sub(r"(?<=\w)-(?=\w)", " ", clean)
    return clean


def _collapse_dotted_initialisms(text: str) -> str:
    def _repl(match: re.Match[str]) -> str:
        token = re.sub(r"[^A-Za-z]", "", match.group(0)).upper()
        if token == "JARVIS":
            return "Jarvis"
        if token in ACRONYM_WORD_EXCEPTIONS:
            return token.title()
        return " ".join(list(token))

    return re.sub(r"\b(?:[A-Za-z]\s*\.){2,}[A-Za-z]?\b\.?", _repl, text)


def _expand_initialisms(text: str) -> str:
    clean = _collapse_dotted_initialisms(text)

    def _repl(match: re.Match[str]) -> str:
        token = match.group(0)
        if token in ACRONYM_WORD_EXCEPTIONS:
            return token.title()
        if token == "JARVIS":
            return "Jarvis"
        if token in {"AM", "PM"}:
            return token.lower()
        return " ".join(list(token))

    return re.sub(r"\b[A-Z]{2,6}\b", _repl, clean)


def _apply_custom_lexicon(text: str, lexicon: dict[str, str]) -> str:
    if not lexicon:
        return text
    clean = text
    for source, replacement in sorted(lexicon.items(), key=lambda item: len(item[0]), reverse=True):
        if not source or not replacement:
            continue
        clean = _replace_word_case_insensitive(clean, source, replacement)
    return clean


def _collect_merriam_candidates(text: str) -> list[str]:
    suspects: set[str] = set()
    for match in re.finditer(r"\b[A-Za-z][A-Za-z']{3,}\b", text):
        word = match.group(0).lower()
        if word in PRONUNCIATION_MISSPELLINGS:
            suspects.add(word)
            continue
        if any(fragment in word for fragment in ("ounci", "anunci", "definately", "seperate", "recieve", "occured")):
            suspects.add(word)
            continue
        if re.search(r"(.)\1\1", word):
            suspects.add(word)
    return sorted(suspects)


def _merriam_lookup_canonical(word: str, settings: PronunciationSettings) -> str | None:
    normalized = str(word or "").strip().lower()
    if not normalized or not settings.merriam_api_key:
        return None
    cached = settings.merriam_cache.get(normalized)
    if isinstance(cached, str):
        return cached or None
    url = MERRIAM_COLLEGIATE_API_TEMPLATE.format(
        word=urllib.parse.quote(normalized),
        api_key=urllib.parse.quote(settings.merriam_api_key),
    )
    candidate: str | None = None
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:  # noqa: S310 - fixed trusted host
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    if isinstance(payload, list) and payload:
        dict_entries = [item for item in payload if isinstance(item, dict)]
        if dict_entries:
            for entry in dict_entries:
                hwi = entry.get("hwi") if isinstance(entry.get("hwi"), dict) else {}
                headword = str(hwi.get("hw") or "").strip().lower()
                if not headword:
                    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
                    headword = str(meta.get("id") or "").split(":")[0].strip().lower()
                headword = re.sub(r"[^a-z' -]", "", headword.replace("*", "")).strip()
                if headword:
                    candidate = headword
                    break
        else:
            suggestions = [
                re.sub(r"[^a-z' -]", "", str(item or "").strip().lower()).strip()
                for item in payload
                if isinstance(item, str)
            ]
            suggestions = [item for item in suggestions if item]
            if suggestions:
                close = difflib.get_close_matches(normalized, suggestions, n=1, cutoff=0.72)
                candidate = close[0] if close else suggestions[0]
    if candidate == normalized:
        candidate = None
    settings.merriam_cache[normalized] = candidate or ""
    settings.cache_dirty = True
    return candidate


def _normalize_word_pronunciation(text: str, settings: PronunciationSettings | None) -> str:
    if settings is None:
        clean = text
        for source, replacement in PRONUNCIATION_MISSPELLINGS.items():
            clean = _replace_word_case_insensitive(clean, source, replacement)
        return clean

    mode = settings.reference_mode
    if mode == "off":
        return text

    clean = text
    for source, replacement in PRONUNCIATION_MISSPELLINGS.items():
        clean = _replace_word_case_insensitive(clean, source, replacement)

    use_merriam = mode in {"auto", "merriam"} and bool(settings.merriam_api_key)
    if mode == "merriam" and not settings.merriam_api_key and not settings.missing_key_warned:
        print("[warn] Merriam reference requested but API key is missing; using builtin normalization.")
        settings.missing_key_warned = True
    if use_merriam:
        remaining = max(0, int(settings.max_merriam_lookups))
        for suspect in _collect_merriam_candidates(clean):
            if remaining <= 0:
                break
            replacement = _merriam_lookup_canonical(suspect, settings)
            if replacement:
                clean = _replace_word_case_insensitive(clean, suspect, replacement)
            remaining -= 1

    clean = _apply_custom_lexicon(clean, settings.lexicon)
    return clean


def _smooth_spoken_pacing(text: str, *, flatten_prosody: bool) -> str:
    clean = str(text or "")
    clean = re.sub(r"\s*[;:]\s*", ", ", clean)
    clean = re.sub(r"\s*[–—]\s*", ", ", clean)
    if flatten_prosody:
        clean = clean.replace("?", ".")
        clean = clean.replace("!", ".")
    clean = re.sub(r",\s*,+", ", ", clean)
    clean = re.sub(r"\.{2,}", ".", clean)
    clean = re.sub(r",\s*\.", ".", clean)
    clean = re.sub(r"\s+([,.;!?])", r"\1", clean)
    clean = re.sub(r"([,.;!?])([A-Za-z])", r"\1 \2", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean.split()) >= 18 and not re.search(r"[,.!?;:]", clean):
        clean = re.sub(
            r"\b(and|but|because|so|while|which)\b",
            r", \1",
            clean,
            count=2,
            flags=re.IGNORECASE,
        )
    if clean and clean[-1] not in ".!?":
        clean += "."
    return clean


def build_pronunciation_settings(args: argparse.Namespace) -> PronunciationSettings:
    reference_mode = str(getattr(args, "pronunciation_reference", "auto") or "auto").strip().lower()
    lexicon_path = Path(str(getattr(args, "pronunciation_lexicon", ""))).expanduser()
    cache_path = Path(str(getattr(args, "merriam_cache", ""))).expanduser()
    key_env = str(getattr(args, "merriam_api_key_env", "JARVIS_MERRIAM_API_KEY") or "").strip()
    key_env = key_env or "JARVIS_MERRIAM_API_KEY"
    api_key = str(os.getenv(key_env) or "").strip() or None
    return PronunciationSettings(
        reference_mode=reference_mode if reference_mode in {"off", "builtin", "auto", "merriam"} else "auto",
        lexicon=_load_pronunciation_map(lexicon_path),
        merriam_api_key=api_key,
        merriam_cache_path=cache_path,
        merriam_cache=_load_pronunciation_map(cache_path),
        max_merriam_lookups=max(0, int(getattr(args, "max_merriam_lookups", 6) or 0)),
    )


def parse_args() -> argparse.Namespace:
    repo_default = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Fallback/local JARVIS voice harness (mic -> Whisper -> JARVIS API -> Piper).",
    )
    parser.add_argument("--api-base", default="http://127.0.0.1:8765")
    parser.add_argument("--surface-id", default="voice:owner")
    parser.add_argument("--session-id", default="live-voice-1")
    parser.add_argument("--record-seconds", type=float, default=3.0)
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument(
        "--input-device",
        default="",
        help="Input device index or name substring (default: system default input).",
    )
    parser.add_argument(
        "--list-input-devices",
        action="store_true",
        help="List available input devices and exit.",
    )
    parser.add_argument("--stt-model", default="base.en")
    parser.add_argument("--stt-device", default="auto")
    parser.add_argument("--stt-compute-type", default="int8")
    parser.add_argument("--language", default="en")
    parser.add_argument(
        "--piper-model",
        default=str(repo_default / ".jarvis" / "voice" / "models" / "en_GB-northern_english_male-medium.onnx"),
    )
    parser.add_argument("--piper-config", default="")
    parser.add_argument(
        "--voice-profile",
        choices=["jarvis", "neutral", "warm"],
        default="jarvis",
        help="Speech profile for TTS cadence and cleanup.",
    )
    parser.add_argument(
        "--flatten-prosody",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reduce upward/question inflection for flatter delivery.",
    )
    parser.add_argument(
        "--audio-backend",
        choices=["auto", "afplay", "sounddevice"],
        default="auto",
        help="TTS playback backend. auto prefers afplay on macOS for smoother playback.",
    )
    parser.add_argument(
        "--model-default-synthesis",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Use the voice model's native synthesis defaults instead of tuned clarity settings.",
    )
    parser.add_argument(
        "--pronunciation-reference",
        choices=["off", "builtin", "auto", "merriam"],
        default="auto",
        help="Word normalization reference mode. auto uses Merriam only when API key is available.",
    )
    parser.add_argument(
        "--pronunciation-lexicon",
        default=str(repo_default / "configs" / "jarvis_pronunciation_lexicon.json"),
        help="Path to optional JSON pronunciation lexicon mapping words to spoken replacements.",
    )
    parser.add_argument(
        "--merriam-api-key-env",
        default="JARVIS_MERRIAM_API_KEY",
        help="Environment variable name containing Merriam-Webster Collegiate API key.",
    )
    parser.add_argument(
        "--merriam-cache",
        default=str(repo_default / ".jarvis" / "voice" / "merriam_collegiate_cache.json"),
        help="Path to JSON cache for Merriam reference lookups.",
    )
    parser.add_argument(
        "--max-merriam-lookups",
        type=int,
        default=6,
        help="Per-utterance cap on Merriam lookups for suspect words.",
    )
    parser.add_argument("--fallback-say", action="store_true")
    parser.add_argument("--text-only", action="store_true")
    parser.add_argument(
        "--push-to-talk",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use hold-to-talk hotkey capture for mic input.",
    )
    parser.add_argument("--ptt-key", default="shift")
    parser.add_argument("--ptt-arm-timeout", type=float, default=0.0)
    parser.add_argument("--ptt-max-seconds", type=float, default=12.0)
    parser.add_argument("--start-server", action="store_true")
    parser.add_argument(
        "--skip-runtime-gate",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Skip strict runtime gate checks after server startup.",
    )
    parser.add_argument(
        "--debug-telemetry",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Print per-turn reply diagnostics (model/fallback/rerank/latency).",
    )
    parser.add_argument("--repo-path", default=str(repo_default))
    parser.add_argument("--db-path", default=str(repo_default / ".jarvis" / "jarvis.db"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--restart-non-model-assisted",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="If an existing server is heuristic-only, restart it automatically when --start-server is set.",
    )
    return parser.parse_args()


def load_repo_env_defaults(repo_path: str | os.PathLike[str]) -> None:
    env_path = Path(repo_path).expanduser().resolve() / ".env"
    if not env_path.exists():
        return
    try:
        raw_lines = env_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    for raw in raw_lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = str(key).strip()
        if not key:
            continue
        os.environ.setdefault(key, str(value).strip())


def _api_url(api_base: str, path: str) -> str:
    return f"{api_base.rstrip('/')}{path}"


def fetch_health_payload(api_base: str, timeout: float = 2.5) -> dict[str, Any] | None:
    try:
        resp = requests.get(_api_url(api_base, "/api/health"), timeout=timeout)
        if not resp.ok:
            return None
        payload = resp.json()
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def check_health(api_base: str, timeout: float = 2.5) -> bool:
    payload = fetch_health_payload(api_base, timeout=timeout)
    return bool(isinstance(payload, dict) and payload.get("status") == "ok")


def fetch_cognition_config(api_base: str, timeout: float = 2.0) -> dict[str, Any] | None:
    try:
        resp = requests.get(_api_url(api_base, "/api/cognition/config"), timeout=timeout)
        if not resp.ok:
            return None
        payload = resp.json()
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def detect_local_ollama_model() -> str | None:
    endpoint = str(os.getenv("JARVIS_OLLAMA_ENDPOINT") or "http://127.0.0.1:11434/api/generate").strip()
    parsed = urllib.parse.urlparse(endpoint)
    if not parsed.scheme or not parsed.netloc:
        return None
    tags_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/api/tags", "", "", ""))
    try:
        resp = requests.get(tags_url, timeout=0.6)
        if not resp.ok:
            return None
        payload = resp.json()
    except Exception:
        return None
    models = payload.get("models")
    if not isinstance(models, list):
        return None
    names = [str(item.get("name") or "").strip() for item in models if isinstance(item, dict)]
    names = [name for name in names if name]
    if not names:
        return None
    lowered = {name.lower(): name for name in names}
    preferred_raw = str(os.getenv("JARVIS_COGNITION_AUTO_PREFER") or "").strip()
    preferred_env = [item.strip() for item in preferred_raw.split(",") if item.strip()]
    preferred = [
        str(os.getenv("JARVIS_COGNITION_MODEL") or "").strip(),
        *preferred_env,
        "qwen3:14b",
        "qwen3:30b",
        "gemma3:27b",
        "qwen3:8b",
        "llama3.2:3b-instruct",
        "llama3.2:3b",
        "qwen2.5:3b-instruct",
        "qwen2.5:3b",
        "mistral:7b-instruct",
    ]
    for candidate in preferred:
        if candidate and candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return names[0]


def _list_listening_pids(port: int) -> list[int]:
    proc = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    pids: list[int] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return sorted(set(pids))


def _command_for_pid(pid: int) -> str:
    proc = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip()


def terminate_existing_jarvis_server(port: int, *, timeout_seconds: float = 5.0) -> bool:
    pids = _list_listening_pids(port)
    if not pids:
        return False
    terminated = False
    for pid in pids:
        cmdline = _command_for_pid(pid)
        if "jarvis.cli serve" not in cmdline:
            raise RuntimeError(
                f"Port {port} is owned by non-JARVIS process pid={pid}: {cmdline or '[unknown]'}"
            )
        try:
            os.kill(pid, signal.SIGTERM)
            terminated = True
        except ProcessLookupError:
            continue
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    while time.monotonic() < deadline:
        if not _list_listening_pids(port):
            return terminated
        time.sleep(0.1)
    return terminated


def _server_env() -> tuple[dict[str, str], str]:
    env = dict(os.environ)
    env.setdefault("JARVIS_CODEX_DELEGATION_ENABLED", "true")
    env.setdefault("JARVIS_CODEX_AUTO_EXECUTE", "true")
    env.setdefault("JARVIS_CODEX_MODEL", "gpt-5.4")
    env.setdefault("JARVIS_COGNITION_BACKEND", "ollama")
    env.setdefault("JARVIS_COGNITION_MODEL", "qwen3:14b")
    env.setdefault(
        "JARVIS_COGNITION_AUTO_PREFER",
        "qwen3:14b,qwen3:30b,gemma3:27b,qwen3:8b,llama3.2:3b-instruct,llama3.2:3b,qwen2.5:3b-instruct,qwen2.5:3b,mistral:7b-instruct",
    )
    backend = str(env.get("JARVIS_COGNITION_BACKEND") or "").strip().lower()
    model = str(env.get("JARVIS_COGNITION_MODEL") or "").strip()
    if backend and backend not in {"auto"}:
        if backend == "ollama":
            selected_model = model or detect_local_ollama_model()
            if not selected_model:
                raise RuntimeError(
                    "JARVIS_COGNITION_BACKEND=ollama but no local model is available. "
                    "Run `open /Applications/Ollama.app` and `ollama pull qwen3:14b`."
                )
            env["JARVIS_COGNITION_MODEL"] = selected_model
            env.setdefault("JARVIS_COGNITION_LOCAL_ONLY", "true")
            return env, f"ollama:{selected_model}"
        label = backend if not model else f"{backend}:{model}"
        return env, label
    detected_model = detect_local_ollama_model()
    if detected_model:
        env["JARVIS_COGNITION_BACKEND"] = "ollama"
        env["JARVIS_COGNITION_MODEL"] = detected_model
        env.setdefault("JARVIS_COGNITION_LOCAL_ONLY", "true")
        return env, f"ollama:{detected_model}"
    allow_heuristic = str(env.get("JARVIS_ALLOW_HEURISTIC_FALLBACK") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if allow_heuristic:
        env["JARVIS_COGNITION_BACKEND"] = "heuristic"
        env.setdefault("JARVIS_COGNITION_LOCAL_ONLY", "true")
        return env, "heuristic"
    raise RuntimeError(
        "No reachable Ollama model found. Start Ollama and pull at least one local model "
        "(recommended: `ollama pull qwen3:14b`). "
        "Set JARVIS_ALLOW_HEURISTIC_FALLBACK=true only if you intentionally want heuristic fallback."
    )


def _download_special_jarvis_voice(*, voice_code: str, download_dir: Path) -> bool:
    normalized = str(voice_code or "").strip().lower()
    if normalized not in {"jarvis-high", "jarvis-medium"}:
        return False
    quality = "high" if normalized.endswith("-high") else "medium"
    base = f"https://huggingface.co/jgkawell/jarvis/resolve/main/en/en_GB/jarvis/{quality}/jarvis-{quality}"
    files = {
        "onnx": download_dir / f"{normalized}.onnx",
        "json": download_dir / f"{normalized}.onnx.json",
    }
    print(f"[init] downloading JARVIS voice '{normalized}' from Hugging Face ...")
    try:
        for key, path in files.items():
            url = f"{base}.onnx{'?download=true' if key == 'onnx' else '.json?download=true'}"
            with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed trusted host
                data = response.read()
            path.write_bytes(data)
        return True
    except Exception as exc:
        for path in files.values():
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass
        print(f"[warn] JARVIS voice download failed ({exc}); falling back to Piper catalog.")
        return False


def start_server_if_needed(args: argparse.Namespace) -> subprocess.Popen[str] | None:
    health = fetch_health_payload(args.api_base)
    if isinstance(health, dict) and health.get("status") == "ok" and not args.start_server:
        boot_id = str(health.get("boot_id") or "").strip() or "unknown"
        model = str(health.get("model") or "").strip() or "unknown"
        pid = health.get("pid")
        print(
            "[init] attached to existing JARVIS server "
            f"(boot_id={boot_id}, pid={pid}, model={model})"
        )
        return None
    if not args.start_server:
        raise RuntimeError(
            "JARVIS server is not healthy. Start it first or rerun with --start-server."
        )
    bootstrap = Path(args.repo_path).expanduser().resolve() / "scripts" / "start_jarvis_daily_production.sh"
    if not bootstrap.exists():
        raise RuntimeError(f"missing frozen production bootstrap: {bootstrap}")
    cmd = [
        "bash",
        str(bootstrap),
        "--repo-path",
        str(Path(args.repo_path).expanduser().resolve()),
        "--db-path",
        str(Path(args.db_path).expanduser().resolve()),
        "--host",
        str(args.host),
        "--port",
        str(args.port),
        "--background",
    ]
    if not bool(args.restart_non_model_assisted):
        cmd.append("--no-restart")
    print("[init] starting JARVIS server via frozen daily bootstrap...")
    launched = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if launched.returncode != 0:
        detail = (launched.stdout or "") + ("\n" if launched.stdout and launched.stderr else "") + (launched.stderr or "")
        raise RuntimeError(
            "Failed to start JARVIS server via frozen bootstrap.\n"
            f"{detail.strip()}"
        )
    for _ in range(40):
        health_ready = fetch_health_payload(args.api_base)
        if isinstance(health_ready, dict) and health_ready.get("status") == "ok":
            cfg = fetch_cognition_config(args.api_base) or {}
            backend = str(cfg.get("backend") or "unknown")
            model = str(cfg.get("model") or "")
            model_assisted = bool(cfg.get("model_assisted"))
            boot_id = str(health_ready.get("boot_id") or "").strip() or "unknown"
            pid = health_ready.get("pid")
            print(
                "[init] JARVIS server ready "
                f"(backend={backend}{':' + model if model else ''}, "
                f"model_assisted={'yes' if model_assisted else 'no'}, "
                f"boot_id={boot_id}, pid={pid})"
            )
            return None
        time.sleep(0.25)
    raise RuntimeError("Frozen bootstrap completed but server did not become healthy in time.")


def load_stt(args: argparse.Namespace) -> WhisperModel:
    return WhisperModel(
        args.stt_model,
        device=args.stt_device,
        compute_type=args.stt_compute_type,
    )


def assert_runtime_gate(args: argparse.Namespace) -> None:
    if bool(args.skip_runtime_gate):
        return
    status_script = Path(args.repo_path).expanduser().resolve() / "scripts" / "jarvis_runtime_status.py"
    if not status_script.exists():
        raise RuntimeError(f"runtime status script missing: {status_script}")
    cmd = [
        sys.executable,
        str(status_script),
        "--repo-path",
        str(Path(args.repo_path).expanduser().resolve()),
        "--db-path",
        str(Path(args.db_path).expanduser().resolve()),
        "--host",
        str(args.host),
        "--port",
        str(args.port),
        "--check-server",
        "--strict",
    ]
    probe = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        detail = (probe.stdout or probe.stderr or "").strip()
        raise RuntimeError(
            "strict runtime gate failed; refusing to run degraded voice loop.\n"
            f"{detail}"
        )


def load_tts(args: argparse.Namespace) -> PiperVoice:
    model_path = Path(args.piper_model).expanduser().resolve()
    if not model_path.exists():
        model_path.parent.mkdir(parents=True, exist_ok=True)
        voice_code = model_path.name.removesuffix(".onnx") if model_path.name.endswith(".onnx") else ""
        if voice_code:
            if not _download_special_jarvis_voice(voice_code=voice_code, download_dir=model_path.parent):
                print(f"[init] downloading Piper voice '{voice_code}' ...")
                proc = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "piper.download_voices",
                        voice_code,
                        "--download-dir",
                        str(model_path.parent),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if proc.returncode != 0:
                    detail = (proc.stderr or proc.stdout or "").strip()
                    raise FileNotFoundError(
                        f"Piper model not found and download failed for '{voice_code}': {detail}"
                    ) from None
        if not model_path.exists():
            raise FileNotFoundError(f"Piper model not found: {model_path}")
    config_path = (
        Path(args.piper_config).expanduser().resolve()
        if str(args.piper_config).strip()
        else model_path.with_suffix(model_path.suffix + ".json")
    )
    return PiperVoice.load(str(model_path), config_path=str(config_path))


def record_to_temp_wav(*, sample_rate: int, seconds: float, input_device: int | None = None) -> Path:
    duration_seconds = max(0.5, float(seconds))
    chunks: list[np.ndarray] = []
    status_messages: list[str] = []

    def _callback(indata: np.ndarray, frames: int, t: Any, status: Any) -> None:
        del frames, t
        if status:
            status_messages.append(str(status))
        chunks.append(indata.copy())

    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            device=input_device,
            callback=_callback,
        ):
            time.sleep(duration_seconds)
    except Exception as exc:
        raise RuntimeError(f"mic capture stream failed: {exc}") from exc
    if not chunks:
        detail = status_messages[-1] if status_messages else "no audio frames captured"
        raise RuntimeError(f"mic capture produced no frames ({detail})")
    recording = np.concatenate(chunks, axis=0)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp_path = Path(tmp.name)
    tmp.close()
    sf.write(str(tmp_path), recording, sample_rate)
    return tmp_path


def _normalize_ptt_token(value: str) -> str:
    token = str(value or "").strip().lower()
    aliases = {
        "control": "ctrl",
        "option": "alt",
        "command": "cmd",
    }
    return aliases.get(token, token)


def _parse_ptt_combo(ptt_key: str) -> set[str]:
    raw = str(ptt_key or "shift").strip().lower()
    if not raw:
        raise ValueError("Empty push-to-talk key.")
    parts = [_normalize_ptt_token(part) for part in re.split(r"\s*\+\s*", raw) if str(part).strip()]
    allowed_special = {
        "space",
        "shift",
        "ctrl",
        "alt",
        "cmd",
        "enter",
        "tab",
    }
    tokens: set[str] = set()
    for token in parts:
        if token in allowed_special or len(token) == 1:
            tokens.add(token)
            continue
        raise ValueError(f"Unsupported ptt key token: {token}")
    if not tokens:
        raise ValueError("No valid push-to-talk key tokens.")
    return tokens


def _key_to_token(key: Any) -> str | None:
    key_name = str(key).strip().lower()
    key_map = {
        "key.space": "space",
        "key.shift": "shift",
        "key.shift_l": "shift",
        "key.shift_r": "shift",
        "key.ctrl": "ctrl",
        "key.ctrl_l": "ctrl",
        "key.ctrl_r": "ctrl",
        "key.alt": "alt",
        "key.alt_l": "alt",
        "key.alt_r": "alt",
        "key.alt_gr": "alt",
        "key.cmd": "cmd",
        "key.cmd_l": "cmd",
        "key.cmd_r": "cmd",
        "key.enter": "enter",
        "key.tab": "tab",
    }
    if key_name in key_map:
        return key_map[key_name]
    char = getattr(key, "char", None)
    if isinstance(char, str) and char:
        return char.lower()
    return None


@dataclass
class PushToTalkState:
    pressed: bool = False


def _macos_accessibility_trusted() -> bool:
    if sys.platform != "darwin":
        return True
    lib_path = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    try:
        app_services = ctypes.cdll.LoadLibrary(lib_path)
        fn = app_services.AXIsProcessTrusted
        fn.restype = ctypes.c_bool
        return bool(fn())
    except Exception:
        # If we cannot determine trust, do not hard fail here.
        return True


def start_push_to_talk_listener(ptt_key: str) -> tuple[PushToTalkState, Any]:
    if not _macos_accessibility_trusted():
        raise RuntimeError(
            "push-to-talk blocked by macOS Accessibility trust. "
            "Add Terminal/iTerm to System Settings -> Privacy & Security -> Accessibility."
        )
    try:
        from pynput import keyboard
    except Exception as exc:
        raise RuntimeError(f"push-to-talk requires pynput ({exc})") from exc
    required_tokens = _parse_ptt_combo(ptt_key)
    state = PushToTalkState(pressed=False)
    pressed_tokens: set[str] = set()

    def _refresh_state() -> None:
        state.pressed = required_tokens.issubset(pressed_tokens)

    def _on_press(key: Any) -> None:
        token = _key_to_token(key)
        if token is None:
            return
        pressed_tokens.add(token)
        _refresh_state()

    def _on_release(key: Any) -> None:
        token = _key_to_token(key)
        if token is None:
            return
        pressed_tokens.discard(token)
        _refresh_state()

    listener = keyboard.Listener(on_press=_on_press, on_release=_on_release)
    listener.start()
    return state, listener


def list_input_devices() -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    try:
        all_devices = sd.query_devices()
    except Exception:
        return devices
    for index, item in enumerate(all_devices):
        if not isinstance(item, dict):
            continue
        if int(item.get("max_input_channels") or 0) <= 0:
            continue
        devices.append(
            {
                "index": index,
                "name": str(item.get("name") or f"input-{index}"),
                "default_samplerate": float(item.get("default_samplerate") or 16000.0),
                "max_input_channels": int(item.get("max_input_channels") or 0),
            }
        )
    return devices


def resolve_input_device(preferred: str) -> int | None:
    choice = str(preferred or "").strip()
    devices = list_input_devices()
    if not devices:
        return None
    if not choice:
        return None
    if choice.isdigit():
        idx = int(choice)
        if any(int(item.get("index") or -1) == idx for item in devices):
            return idx
        raise RuntimeError(f"input device index not found: {idx}")
    lowered_choice = choice.lower()
    for item in devices:
        name = str(item.get("name") or "").lower()
        if lowered_choice in name:
            return int(item.get("index") or 0)
    raise RuntimeError(f"input device name match not found: {choice}")


def resolve_capture_sample_rate(sample_rate: int, device: int | None) -> int:
    requested = int(sample_rate or 0)
    if requested > 0:
        return requested
    try:
        if device is not None:
            details = sd.query_devices(device)
            if isinstance(details, dict):
                candidate = int(float(details.get("default_samplerate") or 0))
                if candidate > 0:
                    return candidate
        default_pair = sd.default.device
        if isinstance(default_pair, (tuple, list)) and len(default_pair) >= 1 and default_pair[0] is not None:
            details = sd.query_devices(int(default_pair[0]))
            if isinstance(details, dict):
                candidate = int(float(details.get("default_samplerate") or 0))
                if candidate > 0:
                    return candidate
    except Exception:
        pass
    return 16000


def record_push_to_talk_to_temp_wav(
    *,
    sample_rate: int,
    input_device: int | None,
    ptt_state: PushToTalkState,
    arm_timeout_seconds: float,
    max_press_seconds: float,
) -> Path | None:
    chunks: list[np.ndarray] = []
    started = False
    finished = False
    started_at = 0.0

    def _audio_callback(indata: np.ndarray, frames: int, t: Any, status: Any) -> None:
        del frames, t
        if status:
            return
        if bool(ptt_state.pressed):
            chunks.append(indata.copy())

    arm_timeout = float(arm_timeout_seconds)
    arm_deadline = None if arm_timeout <= 0 else (time.monotonic() + max(2.0, arm_timeout))
    max_hold = max(1.0, float(max_press_seconds))

    with sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        device=input_device,
        callback=_audio_callback,
    ):
        while True:
            now = time.monotonic()
            is_pressed = bool(ptt_state.pressed)
            if is_pressed and not started:
                started = True
                started_at = now
            if started and (not is_pressed) and chunks:
                finished = True
            if finished:
                break
            if arm_deadline is not None and (not started) and now >= arm_deadline:
                break
            if started and (now - started_at) >= max_hold:
                break
            time.sleep(0.01)

    if not chunks:
        return None

    audio = np.concatenate(chunks, axis=0)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp_path = Path(tmp.name)
    tmp.close()
    sf.write(str(tmp_path), audio, sample_rate)
    return tmp_path


def transcribe_audio(
    model: WhisperModel,
    wav_path: Path,
    *,
    language: str,
) -> str:
    segments, _ = model.transcribe(
        str(wav_path),
        language=language,
        vad_filter=True,
        beam_size=5,
    )
    text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
    return text


def _strip_control(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", str(text or ""))


def prepare_voice_reply(
    *,
    api_base: str,
    text: str,
    surface_id: str,
    session_id: str,
) -> dict:
    force_model_reply = str(os.getenv("JARVIS_VOICE_FORCE_MODEL_REPLY") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    disable_fast_social = str(os.getenv("JARVIS_VOICE_DISABLE_FAST_SOCIAL") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    neutral_voice_mode = str(os.getenv("JARVIS_VOICE_NEUTRAL_MODE") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    disable_partner_lane = str(os.getenv("JARVIS_VOICE_DISABLE_PARTNER_LANE") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    disable_identity_capsule = str(os.getenv("JARVIS_VOICE_DISABLE_IDENTITY_CAPSULE") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    disable_live_state_context = str(os.getenv("JARVIS_VOICE_DISABLE_LIVE_STATE_CONTEXT") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    context: dict[str, Any] = {
        "source": "jarvis_voice_chat",
    }
    if force_model_reply:
        context["force_model_presence_reply"] = True
    if disable_fast_social:
        context["disable_fast_presence_social"] = True
    if neutral_voice_mode:
        context["neutral_voice_mode"] = True
    if disable_partner_lane:
        context["disable_partner_dialogue_turn"] = True
    if disable_identity_capsule:
        context["disable_identity_capsule"] = True
    if disable_live_state_context:
        context["disable_live_state_context"] = True
        context["skip_dialogue_retrieval"] = True
        context["live_briefs"] = {}
    timeout_override_raw = str(os.getenv("JARVIS_VOICE_MODEL_TIMEOUT_SECONDS") or "32").strip()
    try:
        timeout_override = float(timeout_override_raw)
    except ValueError:
        timeout_override = 32.0
    if timeout_override > 0:
        context["presence_model_timeout_override"] = max(8.0, timeout_override)

    payload = {
        "text": text,
        "surface_id": surface_id,
        "session_id": session_id,
        "context": context,
    }
    resp = requests.post(
        _api_url(api_base, "/api/presence/voice/reply/prepare"),
        json=payload,
        timeout=60,
    )
    raw = _strip_control(resp.text)
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"Non-JSON reply from JARVIS API: {raw[:300]}") from None
    if not resp.ok:
        raise RuntimeError(f"JARVIS API error ({resp.status_code}): {data}")
    return data


def split_display_and_spoken(reply_text: str) -> tuple[str | None, str]:
    lines = [line.strip() for line in str(reply_text or "").splitlines() if line.strip()]
    mode_line = None
    spoken_lines: list[str] = []
    for line in lines:
        lowered = line.lower()
        if lowered.startswith("mode:"):
            mode_line = line
            continue
        if lowered.startswith("hypothesis:"):
            continue
        if lowered.startswith("continuity:"):
            continue
        if lowered.startswith("time tradeoff:"):
            continue
        spoken_lines.append(line)
    spoken = " ".join(spoken_lines).strip()
    if not spoken:
        spoken = str(reply_text or "").strip()
    return mode_line, spoken


def style_spoken_text(
    text: str,
    *,
    voice_profile: str,
    flatten_prosody: bool,
    pronunciation_settings: PronunciationSettings | None = None,
) -> str:
    clean = _strip_control(text).strip()
    if not clean:
        return clean
    clean = _strip_spoken_markup(clean)
    clean = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", clean)
    if voice_profile == "jarvis":
        clean = re.sub(
            r"^(i hear you\.?|got you\.?|understood\.?|quick answer:|short answer:|first pass:|short take:)\s*",
            "",
            clean,
            flags=re.IGNORECASE,
        )
        clean = re.sub(r"\bI am\b", "I'm", clean)
        clean = re.sub(r"\bI will\b", "I'll", clean)
    elif voice_profile == "warm":
        clean = re.sub(r"\bwe are\b", "we're", clean, flags=re.IGNORECASE)
    clean = _normalize_word_pronunciation(clean, pronunciation_settings)
    clean = _expand_initialisms(clean)
    clean = _smooth_spoken_pacing(clean, flatten_prosody=flatten_prosody)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def synthesis_config_for_profile(voice_profile: str) -> SynthesisConfig:
    profile = str(voice_profile or "jarvis").strip().lower()
    if profile == "jarvis":
        # Slightly slower and more stable articulation to prevent clipped consonants.
        return SynthesisConfig(
            length_scale=1.08,
            noise_scale=0.5,
            noise_w_scale=0.72,
            normalize_audio=True,
            volume=1.0,
        )
    if profile == "warm":
        return SynthesisConfig(
            length_scale=1.05,
            noise_scale=0.54,
            noise_w_scale=0.76,
            normalize_audio=True,
            volume=1.0,
        )
    return SynthesisConfig(
        length_scale=1.04,
        noise_scale=0.54,
        noise_w_scale=0.74,
        normalize_audio=True,
        volume=1.0,
    )


def _preferred_audio_backend(requested: str) -> str:
    backend = str(requested or "auto").strip().lower()
    if backend in {"afplay", "sounddevice"}:
        return backend
    if sys.platform == "darwin":
        probe = subprocess.run(
            ["which", "afplay"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0 and str(probe.stdout or "").strip():
            return "afplay"
    return "sounddevice"


def _should_use_model_default_synthesis(model_path: str) -> bool:
    name = Path(str(model_path or "")).name.lower()
    return (
        name.startswith("jarvis-high")
        or name.startswith("jarvis-medium")
        or name.startswith("en_gb-northern_english_male-medium")
    )


def speak_with_piper(
    voice: PiperVoice,
    text: str,
    *,
    syn_config: SynthesisConfig | None = None,
    audio_backend: str = "auto",
) -> None:
    clean = _strip_control(text).strip()
    if not clean:
        return
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp_path = Path(tmp.name)
    tmp.close()
    with wave.open(str(tmp_path), "wb") as wav_file:
        voice.synthesize_wav(clean, wav_file, syn_config=syn_config)
    try:
        backend = _preferred_audio_backend(audio_backend)
        if backend == "afplay":
            subprocess.run(
                ["afplay", str(tmp_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        audio, sample_rate = sf.read(str(tmp_path), dtype="float32")
        sd.stop()
        sd.play(audio, sample_rate, blocking=True)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def speak_with_say(text: str) -> None:
    clean = _strip_control(text).strip()
    if clean:
        subprocess.run(["say", clean], check=False)


def main() -> int:
    if _IMPORT_ERROR is not None:
        print(f"[error] voice dependencies are missing: {_IMPORT_ERROR}")
        print("[hint] activate the voice environment first:")
        print("       cd /Users/dankerbadge/Documents/J.A.R.V.I.S")
        print("       source .venv-voice/bin/activate")
        return 1

    args = parse_args()
    load_repo_env_defaults(args.repo_path)
    if bool(args.list_input_devices):
        devices = list_input_devices()
        if not devices:
            print("[info] no input devices detected.")
            return 0
        for item in devices:
            print(
                f"{item['index']}: {item['name']} "
                f"(default_sr={int(float(item['default_samplerate']))}, "
                f"max_input_channels={item['max_input_channels']})"
            )
        return 0
    try:
        input_device = resolve_input_device(args.input_device)
    except Exception as exc:
        print(f"[error] {exc}")
        return 1
    capture_sample_rate = resolve_capture_sample_rate(args.sample_rate, input_device)
    pronunciation_settings = build_pronunciation_settings(args)
    atexit.register(lambda: _write_pronunciation_cache(pronunciation_settings))
    print("[mode] fallback voice harness (production path is OpenClaw Talk Mode).")
    print(
        "[init] pronunciation normalization "
        f"(mode={pronunciation_settings.reference_mode}, "
        f"lexicon_entries={len(pronunciation_settings.lexicon)})"
    )
    if input_device is None:
        print(f"[init] mic input device=default, sample_rate={capture_sample_rate}")
    else:
        print(f"[init] mic input device={input_device}, sample_rate={capture_sample_rate}")
    try:
        start_server_if_needed(args)
    except Exception as exc:
        print(f"[error] {exc}")
        return 1
    try:
        assert_runtime_gate(args)
    except Exception as exc:
        print(f"[error] {exc}")
        return 1

    print("[init] loading Whisper model...")
    try:
        stt = load_stt(args)
    except Exception as exc:
        print(f"[error] failed to load Whisper model: {exc}")
        return 1

    tts = None
    syn_config: SynthesisConfig | None = None
    audio_backend = _preferred_audio_backend(args.audio_backend)
    try:
        print("[init] loading Piper voice...")
        tts = load_tts(args)
        if bool(args.model_default_synthesis):
            syn_config = None
            print("[init] using model-native synthesis defaults.")
        else:
            syn_config = synthesis_config_for_profile(args.voice_profile)
            if _should_use_model_default_synthesis(args.piper_model):
                print("[init] overriding model defaults with tuned clarity synthesis config.")
        print(f"[init] audio playback backend={audio_backend}")
    except Exception as exc:
        if not args.fallback_say:
            print(f"[error] failed to load Piper: {exc}")
            print("[hint] rerun with --fallback-say to use macOS 'say'.")
            return 1
        print(f"[warn] Piper unavailable, falling back to macOS say: {exc}")

    hotkey_available = bool(args.push_to_talk and not args.text_only)
    ptt_state: PushToTalkState | None = None
    ptt_listener: Any | None = None
    if hotkey_available:
        try:
            ptt_state, ptt_listener = start_push_to_talk_listener(args.ptt_key)
            atexit.register(lambda: ptt_listener.stop() if ptt_listener is not None else None)
        except Exception as exc:
            hotkey_available = False
            print(f"[warn] push-to-talk unavailable ({exc}); falling back to timed recording.")

    def handle_user_text(user_text: str) -> None:
        print(f"You: {user_text}")
        try:
            prepared = prepare_voice_reply(
                api_base=args.api_base,
                text=user_text,
                surface_id=args.surface_id,
                session_id=args.session_id,
            )
        except Exception as exc:
            print(f"[error] {exc}")
            return

        reply_text = str(prepared.get("reply_text") or "").strip()
        if not reply_text:
            error_detail = prepared.get("error") or "unknown"
            print(f"JARVIS: [error] {error_detail}")
            return

        mode_line, spoken_text = split_display_and_spoken(reply_text)
        spoken_text = style_spoken_text(
            spoken_text,
            voice_profile=args.voice_profile,
            flatten_prosody=bool(args.flatten_prosody),
            pronunciation_settings=pronunciation_settings,
        )
        _write_pronunciation_cache(pronunciation_settings)
        if mode_line:
            print(f"JARVIS ({mode_line}): {spoken_text}")
        else:
            print(f"JARVIS: {spoken_text}")
        if bool(args.debug_telemetry):
            diagnostics = prepared.get("reply_diagnostics") if isinstance(prepared.get("reply_diagnostics"), dict) else {}
            if diagnostics:
                telemetry = {
                    "boot_id": diagnostics.get("boot_id"),
                    "reply_policy_hash": diagnostics.get("reply_policy_hash"),
                    "model_used": bool(diagnostics.get("model_used")),
                    "model_name": diagnostics.get("model_name"),
                    "fallback_used": bool(diagnostics.get("fallback_used")),
                    "fallback_reason": diagnostics.get("fallback_reason"),
                    "answer_source": diagnostics.get("answer_source"),
                    "route_reason": diagnostics.get("route_reason"),
                    "response_family": diagnostics.get("response_family"),
                    "partner_lane_used": bool(diagnostics.get("partner_lane_used")),
                    "identity_capsule_hash": diagnostics.get("identity_capsule_hash"),
                    "identity_capsule_used": bool(diagnostics.get("identity_capsule_used")),
                    "high_risk_guardrail": bool(diagnostics.get("high_risk_guardrail")),
                    "retrieval_selected_count": diagnostics.get("retrieval_selected_count"),
                    "cached_brief_used": bool(diagnostics.get("cached_brief_used")),
                    "rerank_used": bool(diagnostics.get("rerank_used")),
                    "latency_ms": diagnostics.get("latency_ms"),
                    "contract_gate_passed": bool(
                        prepared.get("contract_gate_passed", diagnostics.get("contract_gate_passed", True))
                    ),
                }
                print(f"[diag] {json.dumps(telemetry, ensure_ascii=True)}")

        try:
            if tts is not None:
                speak_with_piper(
                    tts,
                    spoken_text,
                    syn_config=syn_config,
                    audio_backend=audio_backend,
                )
            elif args.fallback_say:
                speak_with_say(spoken_text)
        except Exception as exc:
            print(f"[warn] audio playback failed: {exc}")
            if args.fallback_say:
                speak_with_say(spoken_text)

    if hotkey_available:
        print(
            f"[ready] hold '{args.ptt_key}' to talk and release to send "
            f"(max {args.ptt_max_seconds:.1f}s). Press Ctrl+C to exit."
        )
        try:
            while True:
                print(f"[listen] waiting for '{args.ptt_key}'...")
                try:
                    wav = record_push_to_talk_to_temp_wav(
                        sample_rate=capture_sample_rate,
                        input_device=input_device,
                        ptt_state=ptt_state or PushToTalkState(),
                        arm_timeout_seconds=args.ptt_arm_timeout,
                        max_press_seconds=args.ptt_max_seconds,
                    )
                except Exception as exc:
                    print(f"[warn] push-to-talk failed ({exc}); switching to timed mode.")
                    hotkey_available = False
                    if ptt_listener is not None:
                        try:
                            ptt_listener.stop()
                        except Exception:
                            pass
                        ptt_listener = None
                    break
                if wav is None:
                    continue
                try:
                    user_text = transcribe_audio(stt, wav, language=args.language)
                finally:
                    try:
                        wav.unlink(missing_ok=True)
                    except Exception:
                        pass
                if not user_text:
                    print("You: [no speech detected]")
                    continue
                handle_user_text(user_text)
        except KeyboardInterrupt:
            print()

    if not hotkey_available:
        print("[ready] press Enter to talk, type /text <message>, or /exit")
        timed_mic_available = not bool(args.text_only)
        while True:
            try:
                raw = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if raw == "/exit":
                break
            if raw.startswith("/text "):
                user_text = raw[len("/text ") :].strip()
            elif args.text_only or (not timed_mic_available):
                user_text = raw.strip()
                if not user_text:
                    continue
            else:
                print(f"[listen] recording {args.record_seconds:.1f}s...")
                try:
                    wav = record_to_temp_wav(
                        sample_rate=capture_sample_rate,
                        seconds=args.record_seconds,
                        input_device=input_device,
                    )
                except Exception as exc:
                    timed_mic_available = False
                    print(
                        "[warn] microphone capture failed "
                        f"({exc}); switching to text input mode for this session."
                    )
                    print("[hint] type your message directly, or use `/text <message>`.")
                    continue
                try:
                    try:
                        user_text = transcribe_audio(stt, wav, language=args.language)
                    except Exception as exc:
                        print(f"[warn] transcription failed ({exc}); try again or use `/text`.")
                        continue
                finally:
                    try:
                        wav.unlink(missing_ok=True)
                    except Exception:
                        pass
                if not user_text:
                    print("You: [no speech detected]")
                    continue
            handle_user_text(user_text)

    if ptt_listener is not None:
        try:
            ptt_listener.stop()
        except Exception:
            pass

    print("[done] voice loop stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
