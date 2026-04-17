#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


def _load_dotenv_file(path: Path) -> int:
    if not path.exists() or not path.is_file():
        return 0
    loaded = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for raw in lines:
        line = str(raw or "").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        loaded += 1
    return loaded


def _bootstrap_env(repo_root: Path) -> None:
    # Load local secret files without overriding already exported shell vars.
    candidates = [
        repo_root / ".env",
        Path.home() / ".openclaw" / ".env",
    ]
    for path in candidates:
        _load_dotenv_file(path)


def _run_curl(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"curl failed ({proc.returncode}): {detail}")
    text = str(proc.stdout or "")
    if "\n" not in text:
        return 0, text.strip()
    body, status_line = text.rsplit("\n", 1)
    try:
        status_code = int(status_line.strip())
    except ValueError:
        status_code = 0
    return status_code, body.strip()


def _api_json(
    *,
    method: str,
    url: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cmd = [
        "curl",
        "-sS",
        "-X",
        method.upper(),
        url,
        "-H",
        f"xi-api-key: {api_key}",
        "-w",
        "\n%{http_code}",
    ]
    if payload is not None:
        cmd.extend(["-H", "Content-Type: application/json", "-d", json.dumps(payload)])
    status_code, body = _run_curl(cmd)
    parsed: dict[str, Any] | None = None
    try:
        loaded = json.loads(body) if body else {}
        parsed = loaded if isinstance(loaded, dict) else {"data": loaded}
    except json.JSONDecodeError:
        parsed = {"raw": body}
    if status_code < 200 or status_code >= 300:
        raise RuntimeError(f"ElevenLabs API error {status_code}: {parsed}")
    return parsed


def _api_create_voice(
    *,
    base_url: str,
    api_key: str,
    name: str,
    description: str,
    files: list[Path],
    remove_background_noise: bool,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/v1/voices/add"
    cmd = [
        "curl",
        "-sS",
        "-X",
        "POST",
        url,
        "-H",
        f"xi-api-key: {api_key}",
        "-F",
        f"name={name}",
        "-F",
        f"description={description}",
        "-F",
        f"remove_background_noise={'true' if remove_background_noise else 'false'}",
        "-w",
        "\n%{http_code}",
    ]
    for file_path in files:
        cmd.extend(["-F", f"files=@{file_path}"])
    status_code, body = _run_curl(cmd)
    try:
        parsed = json.loads(body) if body else {}
    except json.JSONDecodeError:
        parsed = {"raw": body}
    if status_code < 200 or status_code >= 300:
        raise RuntimeError(f"ElevenLabs voice creation failed {status_code}: {parsed}")
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def _api_render_sample(
    *,
    base_url: str,
    api_key: str,
    voice_id: str,
    model_id: str,
    text: str,
    output_format: str,
    out_path: Path,
    voice_settings: dict[str, Any],
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    url = (
        f"{base_url.rstrip('/')}/v1/text-to-speech/{voice_id}"
        f"?output_format={output_format}"
    )
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": voice_settings,
        "apply_text_normalization": "auto",
    }
    cmd = [
        "curl",
        "-sS",
        "-X",
        "POST",
        url,
        "-H",
        f"xi-api-key: {api_key}",
        "-H",
        "Content-Type: application/json",
        "-d",
        json.dumps(payload),
        "-o",
        str(out_path),
        "-w",
        "\n%{http_code}",
    ]
    status_code, body = _run_curl(cmd)
    if status_code < 200 or status_code >= 300:
        detail = body
        try:
            if out_path.exists():
                detail = out_path.read_text(encoding="utf-8")
        except OSError:
            pass
        raise RuntimeError(f"ElevenLabs sample render failed {status_code}: {detail}")


def _load_openclaw_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _save_openclaw_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _find_existing_voice_by_name(
    *,
    base_url: str,
    api_key: str,
    voice_name: str,
) -> str | None:
    listing = _api_json(
        method="GET",
        url=f"{base_url.rstrip('/')}/v1/voices",
        api_key=api_key,
    )
    voices = listing.get("voices") if isinstance(listing.get("voices"), list) else []
    for item in voices:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        voice_id = str(item.get("voice_id") or "").strip()
        if not name or not voice_id:
            continue
        if name.lower() == voice_name.strip().lower():
            return voice_id
    return None


def _list_voices(*, base_url: str, api_key: str) -> list[dict[str, Any]]:
    listing = _api_json(
        method="GET",
        url=f"{base_url.rstrip('/')}/v1/voices",
        api_key=api_key,
    )
    voices = listing.get("voices") if isinstance(listing.get("voices"), list) else []
    out: list[dict[str, Any]] = []
    for item in voices:
        if isinstance(item, dict):
            out.append(item)
    return out


def _pick_best_premade_voice(voices: list[dict[str, Any]]) -> tuple[str, str] | None:
    ranked: list[tuple[float, str, str]] = []
    for item in voices:
        voice_id = str(item.get("voice_id") or "").strip()
        if not voice_id:
            continue
        name = str(item.get("name") or "").strip()
        category = str(item.get("category") or "").strip().lower()
        labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
        accent = str(labels.get("accent") or "").strip().lower()
        gender = str(labels.get("gender") or "").strip().lower()
        age = str(labels.get("age") or "").strip().lower()
        description = str(item.get("description") or "").strip().lower()

        score = 0.0
        if gender == "male":
            score += 0.36
        if accent == "british":
            score += 0.46
        elif accent in {"australian", "irish"}:
            score += 0.12
        if age == "middle_aged":
            score += 0.08
        if category == "premade":
            score += 0.02
        name_norm = name.lower()
        if "daniel" in name_norm:
            score += 0.12
        if "george" in name_norm:
            score += 0.10
        if "broadcast" in description or "professional" in description:
            score += 0.08
        if "storyteller" in description:
            score -= 0.05

        ranked.append((score, voice_id, name))

    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        return None
    _, voice_id, name = ranked[0]
    return voice_id, name


def _collect_top_actor_clips(*, repo_root: Path, max_actor_clips: int) -> list[Path]:
    if int(max_actor_clips) <= 0:
        return []
    manifest_path = (
        repo_root
        / ".jarvis"
        / "voice"
        / "training_assets"
        / "jarvis_actor_isolated_actor_match"
        / "manifest.csv"
    )
    clips_dir = manifest_path.parent / "clips"
    if not manifest_path.exists() or not clips_dir.exists():
        return []
    rows: list[dict[str, str]] = []
    with manifest_path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if isinstance(row, dict):
                rows.append(row)
    candidates: list[tuple[float, Path]] = []
    for row in rows:
        source = str(row.get("source") or "").strip().upper()
        selected = str(row.get("selected") or "").strip().lower()
        clip_name = str(row.get("clip_filename") or "").strip()
        if source in {"JARVIS_REF"}:
            continue
        if selected not in {"polished", "master", "input"}:
            continue
        clip_path = clips_dir / clip_name
        if not clip_path.exists():
            continue
        try:
            actor_score = float(row.get("actor_match_score") or 0.0)
        except (TypeError, ValueError):
            actor_score = 0.0
        try:
            movie_score = float(row.get("movie_match_score") or 0.0)
        except (TypeError, ValueError):
            movie_score = 0.0
        try:
            hiss = float(row.get("hiss_ratio") or 0.0)
        except (TypeError, ValueError):
            hiss = 0.0
        ranking = (0.62 * actor_score) + (0.38 * movie_score) - (0.12 * max(0.0, hiss - 0.08))
        candidates.append((ranking, clip_path))
    candidates.sort(key=lambda item: item[0], reverse=True)
    out: list[Path] = []
    for _, path in candidates:
        if path in out:
            continue
        out.append(path)
        if len(out) >= max(0, int(max_actor_clips)):
            break
    return out


def _collect_top_jarvis_ref_clips(*, repo_root: Path, max_jarvis_ref_clips: int) -> list[Path]:
    if int(max_jarvis_ref_clips) <= 0:
        return []
    manifest_path = (
        repo_root
        / ".jarvis"
        / "voice"
        / "training_assets"
        / "jarvis_actor_isolated_actor_match"
        / "manifest.csv"
    )
    clips_dir = manifest_path.parent / "clips"
    if not manifest_path.exists() or not clips_dir.exists():
        return []
    rows: list[dict[str, str]] = []
    with manifest_path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if isinstance(row, dict):
                rows.append(row)
    candidates: list[tuple[float, Path]] = []
    for row in rows:
        source = str(row.get("source") or "").strip().upper()
        clip_name = str(row.get("clip_filename") or "").strip()
        if source != "JARVIS_REF":
            continue
        if not clip_name:
            continue
        clip_path = clips_dir / clip_name
        if not clip_path.exists():
            continue
        try:
            silence = float(row.get("silence_pct") or 100.0)
        except (TypeError, ValueError):
            silence = 100.0
        try:
            hiss = float(row.get("hiss_ratio") or 1.0)
        except (TypeError, ValueError):
            hiss = 1.0
        try:
            harm = float(row.get("harmonicity") or 0.0)
        except (TypeError, ValueError):
            harm = 0.0
        try:
            duration = float(row.get("duration_sec") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        quality = (
            (0.48 * max(0.0, min(1.0, harm / 0.62)))
            + (0.33 * max(0.0, min(1.0, 1.0 - (hiss / 0.18))))
            + (0.14 * max(0.0, min(1.0, 1.0 - (silence / 20.0))))
            + (0.05 * max(0.0, min(1.0, duration / 5.5)))
        )
        candidates.append((quality, clip_path))
    candidates.sort(key=lambda item: item[0], reverse=True)
    out: list[Path] = []
    for _, path in candidates:
        if path in out:
            continue
        out.append(path)
        if len(out) >= max(0, int(max_jarvis_ref_clips)):
            break
    return out


def _collect_reference_files(
    *,
    repo_root: Path,
    normalized_ref_dir: Path,
    max_total_files: int,
    max_actor_clips: int,
    max_jarvis_ref_clips: int,
) -> list[Path]:
    refs: list[Path] = []
    if normalized_ref_dir.exists():
        for path in sorted(normalized_ref_dir.glob("*.wav")):
            if path.is_file():
                refs.append(path.resolve())
    jarvis_ref_clips = _collect_top_jarvis_ref_clips(
        repo_root=repo_root,
        max_jarvis_ref_clips=max_jarvis_ref_clips,
    )
    for path in jarvis_ref_clips:
        resolved = path.resolve()
        if resolved not in refs:
            refs.append(resolved)
    actor_refs = _collect_top_actor_clips(repo_root=repo_root, max_actor_clips=max_actor_clips)
    for path in actor_refs:
        resolved = path.resolve()
        if resolved not in refs:
            refs.append(resolved)
    capped = refs[: max(1, int(max_total_files))]
    return capped


def _ensure_provider_block(
    *,
    config: dict[str, Any],
    provider_name: str,
    voice_id: str,
    model_id: str,
    api_key_ref: str,
    voice_settings: dict[str, Any],
) -> None:
    talk = config.setdefault("talk", {})
    if not isinstance(talk, dict):
        talk = {}
        config["talk"] = talk
    talk["provider"] = provider_name
    talk_providers = talk.setdefault("providers", {})
    if not isinstance(talk_providers, dict):
        talk_providers = {}
        talk["providers"] = talk_providers
    talk_provider_cfg = talk_providers.setdefault(provider_name, {})
    if not isinstance(talk_provider_cfg, dict):
        talk_provider_cfg = {}
        talk_providers[provider_name] = talk_provider_cfg
    talk_provider_cfg.setdefault("apiKey", api_key_ref)
    talk_provider_cfg["voiceId"] = voice_id
    talk_provider_cfg["modelId"] = model_id
    talk_provider_cfg["applyTextNormalization"] = "auto"
    talk_provider_cfg["voiceSettings"] = dict(voice_settings)
    talk.setdefault("interruptOnSpeech", True)
    talk.setdefault("silenceTimeoutMs", 1500)

    messages = config.setdefault("messages", {})
    if not isinstance(messages, dict):
        messages = {}
        config["messages"] = messages
    tts = messages.setdefault("tts", {})
    if not isinstance(tts, dict):
        tts = {}
        messages["tts"] = tts
    tts["provider"] = provider_name
    tts["enabled"] = True
    tts.setdefault("auto", "inbound")
    tts.setdefault("mode", "final")
    tts.setdefault("timeoutMs", 20000)
    tts.setdefault("maxTextLength", 2200)
    tts_providers = tts.setdefault("providers", {})
    if not isinstance(tts_providers, dict):
        tts_providers = {}
        tts["providers"] = tts_providers
    tts_provider_cfg = tts_providers.setdefault(provider_name, {})
    if not isinstance(tts_provider_cfg, dict):
        tts_provider_cfg = {}
        tts_providers[provider_name] = tts_provider_cfg
    tts_provider_cfg.setdefault("apiKey", api_key_ref)
    tts_provider_cfg["voiceId"] = voice_id
    tts_provider_cfg["modelId"] = model_id
    tts_provider_cfg["applyTextNormalization"] = "auto"
    tts_provider_cfg["voiceSettings"] = dict(voice_settings)


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Create/reuse ElevenLabs actor clone and bind OpenClaw Talk/TTS to it.",
    )
    parser.add_argument("--api-key-env", default="ELEVENLABS_API_KEY")
    parser.add_argument("--base-url", default="https://api.elevenlabs.io")
    parser.add_argument("--voice-name", default="JARVIS Actor Match (Codex)")
    parser.add_argument(
        "--voice-description",
        default="JARVIS actor-match voice cloned from private actor references for local assistant.",
    )
    parser.add_argument("--model-id", default="eleven_v3")
    parser.add_argument(
        "--normalized-ref-dir",
        type=Path,
        default=repo_root / "analysis" / "jarvis_study" / "new_actor_refs" / "normalized",
    )
    parser.add_argument("--max-reference-files", type=int, default=20)
    parser.add_argument("--max-actor-clips", type=int, default=8)
    parser.add_argument("--max-jarvis-ref-clips", type=int, default=24)
    parser.add_argument(
        "--openclaw-config",
        type=Path,
        default=Path.home() / ".openclaw" / "openclaw.json",
    )
    parser.add_argument(
        "--metadata-out",
        type=Path,
        default=repo_root / ".jarvis" / "voice" / "ELEVENLABS_ACTOR_CLONE.json",
    )
    parser.add_argument("--api-key-ref", default="env:ELEVENLABS_API_KEY")
    parser.add_argument("--force-create", action="store_true")
    parser.add_argument("--fallback-voice-id", default="")
    parser.add_argument("--fallback-voice-name", default="")
    parser.add_argument("--voice-stability", type=float, default=0.52)
    parser.add_argument("--voice-similarity-boost", type=float, default=0.92)
    parser.add_argument("--voice-style", type=float, default=0.2)
    parser.add_argument("--voice-speed", type=float, default=0.96)
    parser.add_argument(
        "--voice-use-speaker-boost",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--remove-background-noise",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--sample-text",
        default=(
            "Good evening. All systems are online. Power levels are stable, "
            "and I am ready when you are."
        ),
    )
    parser.add_argument("--sample-output-format", default="mp3_44100_128")
    parser.add_argument(
        "--sample-out",
        type=Path,
        default=repo_root / "exports" / "voice_samples" / "jarvis_actor_match_sample.mp3",
    )
    parser.add_argument("--render-sample", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--restart-gateway", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    _bootstrap_env(repo_root)
    api_key = str(os.getenv(str(args.api_key_env) or "") or "").strip()
    if (not api_key) and (not args.dry_run):
        print(
            f"[error] {args.api_key_env} is not set; cannot create/verify ElevenLabs clone voice."
        )
        print(
            "[hint] export ELEVENLABS_API_KEY='...'"
        )
        print(
            f"[hint] rerun: python {Path(__file__).name}"
        )
        return 2
    if (not api_key) and args.dry_run:
        print(f"[warn] {args.api_key_env} is not set; running dry-run without API calls.")

    references = _collect_reference_files(
        repo_root=repo_root,
        normalized_ref_dir=args.normalized_ref_dir.resolve(),
        max_total_files=max(2, int(args.max_reference_files)),
        max_actor_clips=max(0, int(args.max_actor_clips)),
        max_jarvis_ref_clips=max(0, int(args.max_jarvis_ref_clips)),
    )
    if len(references) < 2:
        print("[error] Not enough reference files to build actor clone (need at least 2).")
        print(f"[hint] expected refs under: {args.normalized_ref_dir.resolve()}")
        return 3

    voice_id: str | None = None
    resolved_voice_name = str(args.voice_name)
    clone_strategy = "instant_clone"
    fallback_reason: str | None = None
    if api_key and (not args.force_create):
        voice_id = _find_existing_voice_by_name(
            base_url=args.base_url,
            api_key=api_key,
            voice_name=str(args.voice_name),
        )
        if voice_id:
            print(f"[ok] reusing existing ElevenLabs voice '{args.voice_name}' id={voice_id}")
            clone_strategy = "reuse_existing"

    if (not voice_id) and (not api_key) and args.dry_run:
        voice_id = "dry_run_voice_id"

    if not voice_id:
        if args.dry_run:
            print(
                f"[dry-run] would create ElevenLabs voice '{args.voice_name}' from {len(references)} files."
            )
            return 0
        try:
            created = _api_create_voice(
                base_url=args.base_url,
                api_key=api_key,
                name=str(args.voice_name),
                description=str(args.voice_description),
                files=references,
                remove_background_noise=bool(args.remove_background_noise),
            )
        except RuntimeError as exc:
            detail = str(exc)
            short_sample_error = (
                "voice_sample_too_short" in detail
                or "audio_too_short" in detail
            )
            if short_sample_error and bool(args.remove_background_noise):
                print(
                    "[warn] sample-length guard triggered with background-noise removal; "
                    "retrying clone creation with remove_background_noise=false."
                )
                try:
                    created = _api_create_voice(
                        base_url=args.base_url,
                        api_key=api_key,
                        name=str(args.voice_name),
                        description=str(args.voice_description),
                        files=references,
                        remove_background_noise=False,
                    )
                except RuntimeError as retry_exc:
                    detail = str(retry_exc)
                    clone_restricted = (
                        "paid_plan_required" in detail
                        or "can_not_use_instant_voice_cloning" in detail
                        or "payment_required" in detail
                    )
                    if not clone_restricted:
                        raise
                    fallback_reason = detail
                    fallback_voice_id = str(args.fallback_voice_id or "").strip()
                    fallback_voice_name = str(args.fallback_voice_name or "").strip()
                    if fallback_voice_id:
                        voice_id = fallback_voice_id
                        resolved_voice_name = fallback_voice_name or fallback_voice_id
                    else:
                        voices = _list_voices(base_url=args.base_url, api_key=api_key)
                        picked = _pick_best_premade_voice(voices)
                        if not picked:
                            raise RuntimeError(
                                "Instant voice clone unavailable and no fallback voices were found."
                            ) from retry_exc
                        voice_id, resolved_voice_name = picked
                    clone_strategy = "premade_fallback"
                    print(
                        f"[warn] instant cloning unavailable on current ElevenLabs plan; "
                        f"using best available premade voice '{resolved_voice_name}' id={voice_id}."
                    )
                    created = {"voice_id": voice_id}
            else:
                clone_restricted = (
                    "paid_plan_required" in detail
                    or "can_not_use_instant_voice_cloning" in detail
                    or "payment_required" in detail
                )
                if not clone_restricted:
                    raise
                fallback_reason = detail
                fallback_voice_id = str(args.fallback_voice_id or "").strip()
                fallback_voice_name = str(args.fallback_voice_name or "").strip()
                if fallback_voice_id:
                    voice_id = fallback_voice_id
                    resolved_voice_name = fallback_voice_name or fallback_voice_id
                else:
                    voices = _list_voices(base_url=args.base_url, api_key=api_key)
                    picked = _pick_best_premade_voice(voices)
                    if not picked:
                        raise RuntimeError(
                            "Instant voice clone unavailable and no fallback voices were found."
                        ) from exc
                    voice_id, resolved_voice_name = picked
                clone_strategy = "premade_fallback"
                print(
                    f"[warn] instant cloning unavailable on current ElevenLabs plan; "
                    f"using best available premade voice '{resolved_voice_name}' id={voice_id}."
                )
                created = {"voice_id": voice_id}
        voice_id = str(created.get("voice_id") or "").strip()
        if not voice_id:
            raise RuntimeError(f"ElevenLabs did not return voice_id: {created}")
        print(f"[ok] created ElevenLabs voice id={voice_id}")

    voice_settings_api = {
        "stability": float(args.voice_stability),
        "similarity_boost": float(args.voice_similarity_boost),
        "style": float(args.voice_style),
        "use_speaker_boost": bool(args.voice_use_speaker_boost),
        "speed": float(args.voice_speed),
    }
    if not args.dry_run:
        try:
            _api_json(
                method="POST",
                url=f"{args.base_url.rstrip('/')}/v1/voices/{voice_id}/settings/edit",
                api_key=api_key,
                payload=voice_settings_api,
            )
            print("[ok] tuned ElevenLabs voice settings.")
        except RuntimeError as exc:
            print(f"[warn] could not persist voice settings on provider: {exc}")

    config_path = args.openclaw_config.expanduser().resolve()
    config = _load_openclaw_config(config_path)
    voice_settings_openclaw = {
        "stability": voice_settings_api["stability"],
        "similarityBoost": voice_settings_api["similarity_boost"],
        "style": voice_settings_api["style"],
        "useSpeakerBoost": voice_settings_api["use_speaker_boost"],
        "speed": voice_settings_api["speed"],
    }
    _ensure_provider_block(
        config=config,
        provider_name="elevenlabs",
        voice_id=voice_id,
        model_id=str(args.model_id),
        api_key_ref=str(args.api_key_ref),
        voice_settings=voice_settings_openclaw,
    )

    if args.dry_run:
        print(f"[dry-run] would update OpenClaw config at {config_path}")
    else:
        backup = config_path.with_suffix(config_path.suffix + ".bak")
        if config_path.exists():
            backup.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
        _save_openclaw_config(config_path, config)
        print(f"[ok] updated OpenClaw config: {config_path}")

        validate = subprocess.run(
            ["openclaw", "config", "validate"],
            capture_output=True,
            text=True,
            check=False,
        )
        if validate.returncode != 0:
            if backup.exists():
                _save_openclaw_config(config_path, _load_openclaw_config(backup))
            raise RuntimeError(
                "openclaw config validate failed after update: "
                + (validate.stderr or validate.stdout or "").strip()
            )
        print("[ok] openclaw config validate passed.")

    metadata = {
        "voice_id": voice_id,
        "voice_name": str(resolved_voice_name),
        "model_id": str(args.model_id),
        "base_url": str(args.base_url),
        "configured_at": datetime.now().isoformat(),
        "reference_file_count": len(references),
        "reference_files": [str(path) for path in references],
        "voice_settings": voice_settings_api,
        "openclaw_config_path": str(config_path),
        "clone_strategy": clone_strategy,
        "fallback_reason": fallback_reason,
        "dry_run": bool(args.dry_run),
    }
    metadata_out = args.metadata_out.expanduser().resolve()
    if args.dry_run:
        print(f"[dry-run] would write clone metadata: {metadata_out}")
    else:
        metadata_out.parent.mkdir(parents=True, exist_ok=True)
        metadata_out.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[ok] wrote clone metadata: {metadata_out}")

    if (not args.dry_run) and bool(args.render_sample):
        sample_out = args.sample_out.expanduser().resolve()
        _api_render_sample(
            base_url=args.base_url,
            api_key=api_key,
            voice_id=voice_id,
            model_id=str(args.model_id),
            text=str(args.sample_text),
            output_format=str(args.sample_output_format),
            out_path=sample_out,
            voice_settings=voice_settings_api,
        )
        print(f"[ok] rendered sample clip: {sample_out}")

    if args.dry_run:
        return 0

    if bool(args.restart_gateway):
        restart = subprocess.run(
            ["openclaw", "gateway", "restart"],
            capture_output=True,
            text=True,
            check=False,
        )
        if restart.returncode != 0:
            raise RuntimeError(
                "openclaw gateway restart failed: "
                + (restart.stderr or restart.stdout or "").strip()
            )
        print("[ok] OpenClaw gateway restarted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
