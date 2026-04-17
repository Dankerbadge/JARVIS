from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .heuristic import HeuristicCognitionBackend

Transport = Callable[[str, dict[str, Any], float], dict[str, Any]]


def _is_local_endpoint(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


class LlamaCppCognitionBackend(HeuristicCognitionBackend):
    name = "llama_cpp"
    model_assisted = True
    supports_model_assisted_skepticism = True
    supports_model_assisted_synthesis = True
    fallback_backend = "heuristic"
    retry_attempts = 0

    def __init__(
        self,
        *,
        model: str = "local-llama-cpp",
        endpoint: str = "http://127.0.0.1:8080/v1/chat/completions",
        timeout_seconds: float = 8.0,
        local_only: bool = True,
        transport: Transport | None = None,
    ) -> None:
        super().__init__(local_only=local_only)
        self.model = model
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self._transport = transport or self._default_transport
        self.reset_cycle_metrics()

    def reset_cycle_metrics(self) -> None:
        self._cycle_metrics = {
            "assist_used": False,
            "fallback_triggered": False,
            "query_count": 0,
            "successful_query_count": 0,
            "average_latency_ms": None,
            "errors": [],
            "endpoint": self.endpoint,
        }

    def _record_query(
        self,
        *,
        success: bool,
        latency_ms: float | None,
        fallback: bool,
        error: str | None = None,
    ) -> None:
        metrics = self._cycle_metrics
        metrics["query_count"] = int(metrics.get("query_count", 0)) + 1
        if success:
            metrics["successful_query_count"] = int(metrics.get("successful_query_count", 0)) + 1
            metrics["assist_used"] = True
        if fallback:
            metrics["fallback_triggered"] = True
        if latency_ms is not None:
            successful = int(metrics.get("successful_query_count", 0))
            prev_avg = metrics.get("average_latency_ms")
            if successful <= 1 or prev_avg is None:
                metrics["average_latency_ms"] = float(latency_ms)
            else:
                metrics["average_latency_ms"] = ((float(prev_avg) * (successful - 1)) + float(latency_ms)) / successful
        if error:
            errors = list(metrics.get("errors", []))
            errors.append(error)
            metrics["errors"] = errors[:5]

    def _default_transport(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - local endpoint by policy
            raw = resp.read().decode("utf-8")
        return json.loads(raw)

    def _chat(self, prompt: str) -> str | None:
        start = time.perf_counter()
        if self.local_only and not _is_local_endpoint(self.endpoint):
            self._record_query(
                success=False,
                latency_ms=None,
                fallback=True,
                error="non_local_endpoint_blocked",
            )
            return None
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a local cognition assistant."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        }
        try:
            response = self._transport(self.endpoint, payload, self.timeout_seconds)
        except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            self._record_query(
                success=False,
                latency_ms=None,
                fallback=True,
                error=f"transport_error:{type(exc).__name__}",
            )
            return None
        choices = response.get("choices", [])
        if not isinstance(choices, list) or not choices:
            self._record_query(
                success=False,
                latency_ms=None,
                fallback=True,
                error="missing_choices",
            )
            return None
        message = choices[0].get("message", {})
        content = str(message.get("content") or "").strip()
        if not content:
            self._record_query(
                success=False,
                latency_ms=None,
                fallback=True,
                error="empty_response",
            )
            return None
        latency_ms = (time.perf_counter() - start) * 1000.0
        self._record_query(
            success=True,
            latency_ms=latency_ms,
            fallback=False,
        )
        return content

    def draft_synthesis(
        self,
        *,
        kind: str,
        structured: dict[str, Any],
        context: dict[str, Any],
    ) -> str | None:
        prompt = (
            "Write a concise synthesis for a personal operator. "
            "Include tradeoff rationale and uncertainty in <=2 sentences. "
            f"kind={kind}; structured={json.dumps(structured, sort_keys=True)}"
        )
        content = self._chat(prompt)
        if content:
            return content
        return super().draft_synthesis(kind=kind, structured=structured, context=context)

    def draft_interrupt_rationale(
        self,
        *,
        candidate: dict[str, Any],
        decision: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[str, str] | None:
        prompt = (
            "Return JSON with why_now and why_not_later for an interrupt decision. "
            f"candidate={json.dumps(candidate, sort_keys=True)} decision={json.dumps(decision, sort_keys=True)}"
        )
        content = self._chat(prompt)
        if content:
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                why_now = str(parsed.get("why_now") or "").strip()
                why_not = str(parsed.get("why_not_later") or "").strip()
                if why_now and why_not:
                    return (why_now, why_not)
        return super().draft_interrupt_rationale(
            candidate=candidate,
            decision=decision,
            context=context,
        )
