from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .base import BackendHypothesis, CognitionBackend
from .heuristic import HeuristicCognitionBackend
from .llama_cpp_backend import LlamaCppCognitionBackend
from .ollama_backend import OllamaCognitionBackend


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def cognition_enabled_from_env() -> bool:
    return _env_bool("JARVIS_COGNITION_ENABLED", True)


def _ollama_tags_endpoint(ollama_endpoint: str) -> str:
    parsed = urllib.parse.urlparse(str(ollama_endpoint or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return "http://127.0.0.1:11434/api/tags"
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/api/tags", "", "", ""))


def _fetch_ollama_models(*, ollama_endpoint: str, timeout_seconds: float = 0.45) -> list[str]:
    req = urllib.request.Request(
        _ollama_tags_endpoint(ollama_endpoint),
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=max(0.1, float(timeout_seconds))) as resp:  # noqa: S310 - local endpoint probe only
            raw = resp.read().decode("utf-8")
        payload = json.loads(raw)
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


def _auto_select_ollama_model(*, ollama_endpoint: str, requested_model: str) -> str | None:
    requested = str(requested_model or "").strip()
    names = _fetch_ollama_models(ollama_endpoint=ollama_endpoint)
    if not names:
        return None
    lowered = {name.lower(): name for name in names}
    if requested:
        if requested.lower() in lowered:
            return lowered[requested.lower()]
        return None
    preferred_env = str(os.getenv("JARVIS_COGNITION_AUTO_PREFER") or "").strip()
    if preferred_env:
        preferred = tuple(
            item.strip()
            for item in preferred_env.split(",")
            if item.strip()
        )
    else:
        preferred = (
            "qwen3:14b",
            "qwen3:30b",
            "gemma3:27b",
            "qwen3:8b",
            "llama3.2:3b-instruct",
            "llama3.2:3b",
            "qwen2.5:3b-instruct",
            "qwen2.5:3b",
            "mistral:7b-instruct",
        )
    for candidate in preferred:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return names[0]


def build_backend(
    *,
    backend_name: str = "heuristic",
    model_name: str = "",
    local_only: bool = True,
    ollama_endpoint: str | None = None,
    llama_cpp_endpoint: str | None = None,
) -> CognitionBackend:
    resolved_backend = str(backend_name or "heuristic").strip().lower()
    resolved_model = str(model_name or "").strip()
    if resolved_backend == "ollama":
        return OllamaCognitionBackend(
            model=resolved_model or "llama3.2:3b-instruct",
            endpoint=str(ollama_endpoint or "http://127.0.0.1:11434/api/generate"),
            local_only=local_only,
        )
    if resolved_backend == "llama_cpp":
        return LlamaCppCognitionBackend(
            model=resolved_model or "local-llama-cpp",
            endpoint=str(llama_cpp_endpoint or "http://127.0.0.1:8080/v1/chat/completions"),
            local_only=local_only,
        )
    return HeuristicCognitionBackend(local_only=local_only)


def build_backend_from_env() -> CognitionBackend:
    backend_name = str(os.getenv("JARVIS_COGNITION_BACKEND") or "auto").strip().lower()
    model_name = str(os.getenv("JARVIS_COGNITION_MODEL") or "").strip()
    local_only = _env_bool("JARVIS_COGNITION_LOCAL_ONLY", True)
    ollama_endpoint = str(os.getenv("JARVIS_OLLAMA_ENDPOINT") or "http://127.0.0.1:11434/api/generate")
    llama_cpp_endpoint = str(
        os.getenv("JARVIS_LLAMACPP_ENDPOINT")
        or "http://127.0.0.1:8080/v1/chat/completions"
    )
    if backend_name in {"", "auto"}:
        selected = _auto_select_ollama_model(
            ollama_endpoint=ollama_endpoint,
            requested_model=model_name,
        )
        if selected:
            return build_backend(
                backend_name="ollama",
                model_name=selected,
                local_only=local_only,
                ollama_endpoint=ollama_endpoint,
                llama_cpp_endpoint=llama_cpp_endpoint,
            )
        backend_name = "heuristic"
    return build_backend(
        backend_name=backend_name,
        model_name=model_name,
        local_only=local_only,
        ollama_endpoint=ollama_endpoint,
        llama_cpp_endpoint=llama_cpp_endpoint,
    )


__all__ = [
    "BackendHypothesis",
    "CognitionBackend",
    "HeuristicCognitionBackend",
    "OllamaCognitionBackend",
    "LlamaCppCognitionBackend",
    "build_backend",
    "build_backend_from_env",
    "cognition_enabled_from_env",
]
