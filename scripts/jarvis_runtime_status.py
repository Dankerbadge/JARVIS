#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from jarvis.runtime import JarvisRuntime


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _boolish(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _endpoint_to_tags_url(endpoint: str) -> str:
    parsed = urllib.parse.urlparse(str(endpoint or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return "http://127.0.0.1:11434/api/tags"
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/api/tags", "", "", ""))


def _fetch_ollama_models(endpoint: str, timeout: float = 1.2) -> list[str]:
    req = urllib.request.Request(
        _endpoint_to_tags_url(endpoint),
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=max(0.2, float(timeout))) as resp:  # noqa: S310 local endpoint probe
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return []
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _server_get_json(url: str, timeout: float = 1.6) -> dict[str, Any] | None:
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=max(0.2, float(timeout))) as resp:  # noqa: S310 local endpoint probe
            parsed = json.loads(resp.read().decode("utf-8"))
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _server_post_json(url: str, payload: dict[str, Any], timeout: float = 8.0) -> dict[str, Any] | None:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
        data=body,
    )
    try:
        with urllib.request.urlopen(req, timeout=max(0.2, float(timeout))) as resp:  # noqa: S310 local endpoint probe
            parsed = json.loads(resp.read().decode("utf-8"))
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _server_get_json_retry(
    url: str,
    *,
    attempts: int = 8,
    timeout: float = 1.6,
    delay_seconds: float = 0.4,
) -> dict[str, Any] | None:
    for idx in range(max(1, int(attempts))):
        parsed = _server_get_json(url, timeout=timeout)
        if isinstance(parsed, dict):
            return parsed
        if idx < int(attempts) - 1:
            time.sleep(max(0.05, float(delay_seconds)))
    return None


def _warm_probe_ready(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    reply_text = str(payload.get("reply_text") or "").strip()
    if not reply_text:
        return False
    diagnostics = payload.get("reply_diagnostics")
    if not isinstance(diagnostics, dict):
        return False
    model_used = bool(diagnostics.get("model_used"))
    model_query_succeeded = bool(diagnostics.get("model_query_succeeded"))
    fallback_used = bool(diagnostics.get("fallback_used"))
    return model_used and (not fallback_used or model_query_succeeded)


def _model_present(model_names: list[str], target: str) -> bool:
    target_lower = str(target or "").strip().lower()
    if not target_lower:
        return False
    lowered = {name.lower() for name in model_names}
    if target_lower in lowered:
        return True
    # Accept target without explicit tag if a tagged variant exists.
    prefix = target_lower + ":"
    return any(name.startswith(prefix) for name in lowered)


def main() -> int:
    parser = argparse.ArgumentParser(description="Print frozen JARVIS daily runtime status and drift checks.")
    parser.add_argument("--repo-path", type=Path, default=ROOT_DIR)
    parser.add_argument("--db-path", type=Path, default=ROOT_DIR / ".jarvis" / "jarvis.db")
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--check-server", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    repo_path = args.repo_path.expanduser().resolve()
    db_path = args.db_path.expanduser().resolve()
    env_file = args.env_file.expanduser().resolve() if args.env_file else (repo_path / ".env")
    env_values = _load_env_file(env_file)
    for key, value in env_values.items():
        os.environ.setdefault(str(key), str(value))

    runtime = JarvisRuntime(db_path=db_path, repo_path=repo_path)
    try:
        cognition = runtime.get_cognition_config()
        retrieval = runtime.get_dialogue_retrieval_config()
    finally:
        runtime.close()

    endpoint = str(os.getenv("JARVIS_OLLAMA_ENDPOINT") or env_values.get("JARVIS_OLLAMA_ENDPOINT") or "http://127.0.0.1:11434/api/generate")
    models = _fetch_ollama_models(endpoint)
    configured_primary = str(os.getenv("JARVIS_COGNITION_MODEL") or env_values.get("JARVIS_COGNITION_MODEL") or "").strip()
    configured_backend = str(os.getenv("JARVIS_COGNITION_BACKEND") or env_values.get("JARVIS_COGNITION_BACKEND") or "").strip().lower()
    configured_embed_model = str(
        os.getenv("JARVIS_DIALOGUE_EMBED_MODEL")
        or env_values.get("JARVIS_DIALOGUE_EMBED_MODEL")
        or "mxbai-embed-large"
    ).strip()
    rerank_embed_enabled = _boolish(
        os.getenv("JARVIS_DIALOGUE_EMBED_RERANK_ENABLED")
        or env_values.get("JARVIS_DIALOGUE_EMBED_RERANK_ENABLED")
        or "true"
    )
    rerank_flag_enabled = _boolish(
        os.getenv("JARVIS_DIALOGUE_FLAG_RERANK_ENABLED")
        or env_values.get("JARVIS_DIALOGUE_FLAG_RERANK_ENABLED")
        or "false"
    )
    rerank_model = str(
        os.getenv("JARVIS_DIALOGUE_FLAG_RERANK_MODEL")
        or env_values.get("JARVIS_DIALOGUE_FLAG_RERANK_MODEL")
        or "BAAI/bge-reranker-v2-m3"
    ).strip()

    server = None
    if args.check_server:
        base = f"http://{args.host}:{int(args.port)}"
        health = _server_get_json_retry(base + "/api/health", attempts=10, timeout=1.2, delay_seconds=0.35)
        cfg = _server_get_json_retry(base + "/api/cognition/config", attempts=8, timeout=1.4, delay_seconds=0.35)
        retrieval_live = _server_get_json_retry(
            base + "/api/presence/dialogue/retrieval",
            attempts=8,
            timeout=1.6,
            delay_seconds=0.35,
        )
        warm_probe = None
        if isinstance(health, dict) and str(health.get("status") or "").strip().lower() == "ok":
            warm_probe_text = str(
                os.getenv("JARVIS_RUNTIME_STATUS_WARM_PROBE_TEXT")
                or "warm probe: reply in one short sentence confirming you are online"
            ).strip()
            try:
                warm_probe_attempts = max(1, int(os.getenv("JARVIS_RUNTIME_STATUS_WARM_PROBE_ATTEMPTS", "3")))
            except ValueError:
                warm_probe_attempts = 3
            try:
                warm_probe_timeout = max(8.0, float(os.getenv("JARVIS_RUNTIME_STATUS_WARM_PROBE_TIMEOUT_SECONDS", "30")))
            except ValueError:
                warm_probe_timeout = 30.0
            try:
                warm_probe_delay = max(0.2, float(os.getenv("JARVIS_RUNTIME_STATUS_WARM_PROBE_DELAY_SECONDS", "1.0")))
            except ValueError:
                warm_probe_delay = 1.0
            for idx in range(warm_probe_attempts):
                warm_probe = _server_post_json(
                    base + "/api/presence/reply/prepare",
                    {
                        "text": warm_probe_text,
                        "surface_id": "dm:owner",
                        "session_id": f"runtime-status-warm-probe-{idx + 1}",
                        "context": {
                            "source": "runtime_status_probe",
                            "force_model_presence_reply": True,
                            "disable_codex_auto_delegate": True,
                            "disable_self_inquiry": True,
                        },
                    },
                    timeout=warm_probe_timeout,
                )
                if _warm_probe_ready(warm_probe):
                    break
                if idx < warm_probe_attempts - 1:
                    time.sleep(warm_probe_delay)
        server = {
            "base_url": base,
            "health": health,
            "cognition_config": cfg,
            "retrieval_config": retrieval_live,
            "warm_probe": warm_probe,
        }

    status: dict[str, Any] = {
        "repo_path": str(repo_path),
        "db_path": str(db_path),
        "python_executable": sys.executable,
        "env_file": str(env_file),
        "frozen_env": {
            "JARVIS_COGNITION_BACKEND": configured_backend,
            "JARVIS_COGNITION_MODEL": configured_primary,
            "JARVIS_DIALOGUE_EMBED_MODEL": configured_embed_model,
            "JARVIS_DIALOGUE_EMBED_RERANK_ENABLED": rerank_embed_enabled,
            "JARVIS_DIALOGUE_FLAG_RERANK_ENABLED": rerank_flag_enabled,
            "JARVIS_DIALOGUE_FLAG_RERANK_MODEL": rerank_model,
            "JARVIS_PRESENCE_MODEL_TIMEOUT_SECONDS": str(
                os.getenv("JARVIS_PRESENCE_MODEL_TIMEOUT_SECONDS")
                or env_values.get("JARVIS_PRESENCE_MODEL_TIMEOUT_SECONDS")
                or "20"
            ),
        },
        "runtime": {
            "cognition": cognition,
            "dialogue_retrieval": retrieval,
        },
        "ollama": {
            "endpoint": endpoint,
            "model_count": len(models),
            "models": models,
        },
        "dependencies": {
            "FlagEmbedding_importable": _has_module("FlagEmbedding"),
            "transformers_importable": _has_module("transformers"),
        },
        "server": server,
    }

    checks = {
        "backend_ollama": configured_backend == "ollama",
        "primary_qwen3_14b": configured_primary == "qwen3:14b",
        "runtime_model_qwen3_14b": str(cognition.get("model") or "").strip().lower() == "qwen3:14b",
        "qwen3_14b_available": (_model_present(models, "qwen3:14b") if models else True),
        "mxbai_embed_available": (_model_present(models, configured_embed_model) if models else True),
        "embed_rerank_enabled": bool(rerank_embed_enabled),
        "at_least_one_reranker_enabled": bool(rerank_embed_enabled or rerank_flag_enabled),
        "flagembedding_available_or_disabled": (
            (not bool(rerank_flag_enabled))
            or bool(status["dependencies"]["FlagEmbedding_importable"])
        ),
    }
    if server is not None:
        checks["server_healthy"] = bool((server.get("health") or {}).get("status") == "ok")
        server_cfg = server.get("cognition_config") if isinstance(server.get("cognition_config"), dict) else {}
        server_retrieval = (
            server.get("retrieval_config") if isinstance(server.get("retrieval_config"), dict) else {}
        )
        server_warm_probe = server.get("warm_probe") if isinstance(server.get("warm_probe"), dict) else {}
        warm_diag = (
            server_warm_probe.get("reply_diagnostics")
            if isinstance(server_warm_probe.get("reply_diagnostics"), dict)
            else {}
        )
        server_backend = str(server_cfg.get("backend") or "").strip().lower()
        server_model = str(server_cfg.get("model") or "").strip().lower()
        latest_backend_mode = str(server_cfg.get("latest_backend_mode") or "").strip().lower()
        checks["server_model_assisted"] = bool(server_cfg.get("model_assisted"))
        checks["server_backend_matches_env"] = (not configured_backend) or server_backend == configured_backend
        checks["server_model_matches_env"] = (not configured_primary) or server_model == configured_primary.lower()
        embed_live = (
            server_retrieval.get("embed_rerank")
            if isinstance(server_retrieval.get("embed_rerank"), dict)
            else {}
        )
        flag_live = (
            server_retrieval.get("flag_rerank")
            if isinstance(server_retrieval.get("flag_rerank"), dict)
            else {}
        )
        embed_enabled_live = bool(embed_live.get("enabled"))
        flag_enabled_live = bool(flag_live.get("enabled"))
        if not models:
            # If local tags inventory is temporarily unavailable, use live server truth.
            checks["qwen3_14b_available"] = bool(checks["server_model_matches_env"])
            embed_live_model = str(embed_live.get("model") or "").strip()
            checks["mxbai_embed_available"] = (
                (not configured_embed_model)
                or embed_live_model.lower() == configured_embed_model.lower()
            )
        checks["server_latest_backend_not_heuristic"] = (
            latest_backend_mode != "heuristic" or bool(warm_diag.get("model_used"))
        )

        checks["server_embed_rerank_matches_env"] = embed_enabled_live == bool(rerank_embed_enabled)
        checks["server_flag_rerank_matches_env"] = flag_enabled_live == bool(rerank_flag_enabled)
        checks["server_embed_rerank_available_when_enabled"] = (
            True if not embed_enabled_live else bool(embed_live.get("available"))
        )
        checks["server_flag_rerank_available_when_enabled"] = (
            True if not flag_enabled_live else bool(flag_live.get("available"))
        )
        checks["warm_probe_has_reply"] = bool(str(server_warm_probe.get("reply_text") or "").strip())
        checks["warm_probe_not_fallback"] = (
            (not bool(warm_diag.get("fallback_used")))
            or (
                bool(warm_diag.get("model_used"))
                and bool(warm_diag.get("model_query_succeeded"))
            )
        )
        checks["warm_probe_model_path_active"] = bool(warm_diag.get("model_used"))
        checks["warm_probe_continuity_ok"] = bool(warm_diag.get("continuity_ok", True))
    status["checks"] = checks
    status["ok"] = all(bool(value) for value in checks.values())

    print(json.dumps(status, indent=2, sort_keys=True))
    if args.strict and not bool(status["ok"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
