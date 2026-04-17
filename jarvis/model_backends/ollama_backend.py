from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import os
from dataclasses import replace
from typing import Any, Callable

from .base import BackendHypothesis
from .heuristic import HeuristicCognitionBackend

Transport = Callable[[str, dict[str, Any], float], dict[str, Any]]


def _is_local_endpoint(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


class OllamaCognitionBackend(HeuristicCognitionBackend):
    name = "ollama"
    model_assisted = True
    supports_model_assisted_skepticism = True
    supports_model_assisted_synthesis = True
    fallback_backend = "heuristic"
    retry_attempts = 0

    def __init__(
        self,
        *,
        model: str = "llama3.2:3b-instruct",
        endpoint: str = "http://127.0.0.1:11434/api/generate",
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

    def _query_text(
        self,
        prompt: str,
        *,
        timeout_seconds: float | None = None,
        num_predict: int | None = None,
        model_override: str | None = None,
    ) -> str | None:
        start = time.perf_counter()
        if self.local_only and not _is_local_endpoint(self.endpoint):
            self._record_query(
                success=False,
                latency_ms=None,
                fallback=True,
                error="non_local_endpoint_blocked",
            )
            return None
        max_tokens = int(num_predict) if num_predict is not None else 120
        keep_alive = str(os.getenv("JARVIS_OLLAMA_KEEP_ALIVE") or "30m").strip() or "30m"
        resolved_model = str(model_override or self.model).strip() or self.model
        payload = {
            "model": resolved_model,
            "stream": False,
            "prompt": prompt,
            "think": False,
            "keep_alive": keep_alive,
            "options": {
                "temperature": 0.15,
                "num_predict": max(16, min(max_tokens, 220)),
            },
        }
        timeout = float(self.timeout_seconds if timeout_seconds is None else timeout_seconds)
        try:
            raw = self._transport(self.endpoint, payload, timeout)
        except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            self._record_query(
                success=False,
                latency_ms=None,
                fallback=True,
                error=f"transport_error:{type(exc).__name__}",
            )
            return None
        error_text = _clean_text(raw.get("error"))
        if error_text:
            self._record_query(
                success=False,
                latency_ms=None,
                fallback=True,
                error=f"model_error:{error_text[:120]}",
            )
            return None
        text = _clean_text(raw.get("response"))
        if not text:
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
        return text

    def _compact_presence_dialogue_context(self, dialogue_context: dict[str, Any]) -> dict[str, Any]:
        context = dict(dialogue_context or {})
        compact: dict[str, Any] = {}

        def _trim(value: Any, *, limit: int = 280) -> str:
            text = _clean_text(value)
            return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."

        identity = context.get("identity") if isinstance(context.get("identity"), dict) else {}
        thinking = context.get("thinking") if isinstance(context.get("thinking"), dict) else {}
        academics = context.get("academics") if isinstance(context.get("academics"), dict) else {}
        markets = context.get("markets") if isinstance(context.get("markets"), dict) else {}
        operations = context.get("operations") if isinstance(context.get("operations"), dict) else {}
        thread = context.get("dialogue_thread") if isinstance(context.get("dialogue_thread"), dict) else {}
        memory = context.get("memory") if isinstance(context.get("memory"), dict) else {}

        identity_name = _trim(identity.get("display_name"), limit=80)
        if identity_name:
            compact["identity_name"] = identity_name
        if identity.get("value_guidance"):
            compact["value_guidance"] = [
                _trim(item, limit=120)
                for item in list(identity.get("value_guidance") or [])[:3]
                if _trim(item, limit=120)
            ]
        if thinking.get("active_priorities"):
            compact["active_priorities"] = [
                _trim(item, limit=120)
                for item in list(thinking.get("active_priorities") or [])[:3]
                if _trim(item, limit=120)
            ]
        if thinking.get("top_hypotheses"):
            compact["top_hypotheses"] = [
                _trim(item, limit=140)
                for item in list(thinking.get("top_hypotheses") or [])[:3]
                if _trim(item, limit=140)
            ]
        compact["status_snapshot"] = _trim(context.get("status_snapshot"), limit=220)

        if academics.get("top_risks"):
            compact["academics_risks"] = [
                _trim(item.get("reason") or item, limit=140)
                for item in list(academics.get("top_risks") or [])[:2]
                if _trim(item.get("reason") if isinstance(item, dict) else item, limit=140)
            ]
        if markets.get("top_risks"):
            compact["markets_risks"] = [
                _trim(item.get("reason") or item, limit=140)
                for item in list(markets.get("top_risks") or [])[:2]
                if _trim(item.get("reason") if isinstance(item, dict) else item, limit=140)
            ]
        if markets.get("top_opportunities"):
            compact["markets_opportunities"] = [
                _trim(item.get("reason") or item, limit=140)
                for item in list(markets.get("top_opportunities") or [])[:2]
                if _trim(item.get("reason") if isinstance(item, dict) else item, limit=140)
            ]
        pending_interrupts = int(operations.get("pending_interrupt_count") or 0)
        if pending_interrupts:
            compact["pending_interrupt_count"] = pending_interrupts
        thread_summary = _trim(thread.get("summary"), limit=200)
        if thread_summary:
            compact["thread_summary"] = thread_summary
        unresolved = list(thread.get("unresolved_questions") or [])
        if unresolved:
            compact["unresolved_questions"] = [
                _trim(item, limit=140)
                for item in unresolved[:3]
                if _trim(item, limit=140)
            ]
        recent_turns = list(thread.get("recent_turns") or [])
        if recent_turns:
            compact_recent: list[dict[str, Any]] = []
            for item in recent_turns[:3]:
                if not isinstance(item, dict):
                    continue
                user_turn = _trim(item.get("user_text"), limit=140)
                assistant_turn = _trim(item.get("final_reply"), limit=180)
                if not user_turn and not assistant_turn:
                    continue
                compact_recent.append(
                    {
                        "user": user_turn or None,
                        "assistant": assistant_turn or None,
                    }
                )
            if compact_recent:
                compact["recent_turns"] = compact_recent

        snippets = list(memory.get("semantic_snippets") or [])
        if snippets:
            compact_snippets: list[dict[str, Any]] = []
            for item in snippets[:3]:
                if not isinstance(item, dict):
                    continue
                snippet_text = _trim(item.get("snippet") or item.get("text"), limit=200)
                if not snippet_text:
                    continue
                compact_snippets.append(
                    {
                        "memory_key": _trim(item.get("memory_key"), limit=90) or None,
                        "score": item.get("score"),
                        "source_bucket": _trim(item.get("source_bucket"), limit=60) or None,
                        "snippet": snippet_text,
                    }
                )
            if compact_snippets:
                compact["memory_snippets"] = compact_snippets
        bucket_counts = memory.get("snippet_bucket_counts") if isinstance(memory.get("snippet_bucket_counts"), dict) else {}
        if bucket_counts:
            compact["memory_bucket_counts"] = {
                key: int(bucket_counts.get(key) or 0)
                for key in ("live_state", "thread_memory", "identity_long_horizon", "general")
            }
        if "partner_context_mix_ok" in memory:
            compact["partner_context_mix_ok"] = bool(memory.get("partner_context_mix_ok"))
        partner_subfamily = _trim(memory.get("partner_subfamily"), limit=40)
        if partner_subfamily:
            compact["partner_subfamily"] = partner_subfamily
        return compact

    def _query_json(
        self,
        prompt: str,
        *,
        timeout_seconds: float | None = None,
        model_override: str | None = None,
    ) -> dict[str, Any] | None:
        start = time.perf_counter()
        if self.local_only and not _is_local_endpoint(self.endpoint):
            self._record_query(
                success=False,
                latency_ms=None,
                fallback=True,
                error="non_local_endpoint_blocked",
            )
            return None
        resolved_model = str(model_override or self.model).strip() or self.model
        payload = {
            "model": resolved_model,
            "stream": False,
            "format": "json",
            "prompt": prompt,
            "think": False,
            "options": {"temperature": 0.1},
        }
        timeout = float(self.timeout_seconds if timeout_seconds is None else timeout_seconds)
        try:
            raw = self._transport(self.endpoint, payload, timeout)
        except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            self._record_query(
                success=False,
                latency_ms=None,
                fallback=True,
                error=f"transport_error:{type(exc).__name__}",
            )
            return None
        error_text = _clean_text(raw.get("error"))
        if error_text:
            self._record_query(
                success=False,
                latency_ms=None,
                fallback=True,
                error=f"model_error:{error_text[:120]}",
            )
            return None
        response_text = str(raw.get("response") or "").strip()
        if not response_text:
            self._record_query(
                success=False,
                latency_ms=None,
                fallback=True,
                error="empty_response",
            )
            return None
        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError:
            self._record_query(
                success=False,
                latency_ms=None,
                fallback=True,
                error="invalid_json_response",
            )
            return None
        if isinstance(parsed, dict):
            latency_ms = (time.perf_counter() - start) * 1000.0
            self._record_query(
                success=True,
                latency_ms=latency_ms,
                fallback=False,
            )
            return parsed
        self._record_query(
            success=False,
            latency_ms=None,
            fallback=True,
            error="non_object_json_response",
        )
        return None

    def _presence_reply_timeout_seconds(self) -> float:
        raw = str(os.getenv("JARVIS_PRESENCE_MODEL_TIMEOUT_SECONDS") or "").strip()
        if raw:
            try:
                value = float(raw)
            except ValueError:
                value = max(float(self.timeout_seconds), 45.0)
        else:
            value = max(float(self.timeout_seconds), 45.0)
        # Keep a sane range while allowing slower local models to return real output.
        return max(1.8, min(value, 45.0))

    def generate_hypotheses(
        self,
        *,
        risks: list[dict[str, Any]],
        recent_outcomes: list[dict[str, Any]],
        max_hypotheses: int,
    ) -> list[BackendHypothesis]:
        base = super().generate_hypotheses(
            risks=risks,
            recent_outcomes=recent_outcomes,
            max_hypotheses=max_hypotheses,
        )
        if not base:
            return base
        prompt = (
            "You are refining ranked risk hypotheses for a local-only personal operator. "
            "Return JSON: {\"updates\":[{\"index\":0,\"claim\":\"...\",\"skepticism_flags\":[\"...\"],"
            "\"counter_refs\":[\"...\"],\"confidence_delta\":0.0,\"expected_value_delta\":0.0}]}. "
            f"Current hypotheses: {json.dumps([item.__dict__ for item in base], sort_keys=True)}"
        )
        model_payload = self._query_json(prompt)
        if not model_payload:
            return base

        updates = model_payload.get("updates", [])
        if not isinstance(updates, list):
            return base
        merged = list(base)
        for item in updates:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("index", -1))
            if idx < 0 or idx >= len(merged):
                continue
            current = merged[idx]
            claim = str(item.get("claim") or current.claim).strip() or current.claim
            skepticism = tuple(
                sorted(
                    {
                        *current.skepticism_flags,
                        *(str(flag).strip().lower() for flag in item.get("skepticism_flags", []) if str(flag).strip()),
                    }
                )
            )
            counter_refs = tuple(
                dict.fromkeys(
                    [
                        *current.counter_refs,
                        *(str(ref).strip() for ref in item.get("counter_refs", []) if str(ref).strip()),
                    ]
                )
            )
            confidence = current.confidence + float(item.get("confidence_delta", 0.0))
            expected_value = current.expected_value + float(item.get("expected_value_delta", 0.0))
            merged[idx] = replace(
                current,
                claim=claim,
                skepticism_flags=skepticism,
                counter_refs=counter_refs,
                confidence=confidence,
                expected_value=expected_value,
            ).normalized()
        return merged

    def draft_synthesis(
        self,
        *,
        kind: str,
        structured: dict[str, Any],
        context: dict[str, Any],
    ) -> str | None:
        if str(kind or "").strip().lower() == "presence_reply":
            dialogue_context = context.get("dialogue_context") if isinstance(context.get("dialogue_context"), dict) else {}
            compact_context = self._compact_presence_dialogue_context(dialogue_context)
            user_text = _clean_text((structured or {}).get("user_text"))
            user_text_lower = user_text.lower()
            token_count = len(user_text_lower.split())
            neutral_voice_mode = bool((context or {}).get("neutral_voice_mode"))
            force_model_presence_reply = bool((context or {}).get("force_model_presence_reply"))
            partner_dialogue_turn = bool((context or {}).get("partner_dialogue_turn")) and not neutral_voice_mode
            presence_model_override = str((context or {}).get("presence_model_override") or "").strip() or None
            timeout_override = None
            timeout_override_raw = str((context or {}).get("presence_model_timeout_override") or "").strip()
            if timeout_override_raw:
                try:
                    timeout_override = max(1.8, float(timeout_override_raw))
                except ValueError:
                    timeout_override = None
            low_signal_social_turns = {
                "hi",
                "hello",
                "hey",
                "yo",
                "sup",
                "whats up",
                "what is up",
                "hows it going",
                "how is it going",
                "how are you",
            }
            is_low_signal_social = user_text_lower in low_signal_social_turns
            is_high_stakes = bool((structured or {}).get("high_stakes"))
            total_timeout = timeout_override if timeout_override is not None else self._presence_reply_timeout_seconds()
            status_turn_tokens = (
                "what's going on",
                "whats going on",
                "what is going on",
                "what's up",
                "whats up",
                "what is up",
                "quick status",
                "status update",
                "what matters",
                "top priorities",
                "what should i focus on",
            )
            light_probe_tokens = (
                "what are you noticing",
                "what do you notice",
                "what are you seeing",
                "what do you think",
                "what's your read",
                "whats your read",
                "your read",
            )
            is_status_probe = any(token in user_text_lower for token in status_turn_tokens)
            is_light_presence_probe = any(token in user_text_lower for token in light_probe_tokens)
            partner_subfamily = str((structured or {}).get("partner_subfamily") or "").strip().lower() or None
            partner_guidance = ""
            if partner_subfamily == "pushback":
                partner_guidance = "For this turn, include the specific risk and a safer alternative."
            elif partner_subfamily == "tradeoff":
                partner_guidance = "For this turn, explicitly name the central tradeoff and the recommended next move."
            elif partner_subfamily == "identity":
                partner_guidance = "For this turn, include continuity/memory language that shows what you are tracking about the user."
            elif partner_subfamily == "truth":
                partner_guidance = "For this turn, be direct about the uncomfortable read and include a corrective next move."
            elif partner_subfamily == "strategic":
                partner_guidance = "For this turn, provide a strategic read with why-now context and one bounded experiment."
            elif partner_subfamily == "reflection":
                partner_guidance = "For this turn, surface one explicit uncertainty or hypothesis before the next move."
            if (is_status_probe or is_light_presence_probe) and not neutral_voice_mode:
                status_snapshot = _clean_text(compact_context.get("status_snapshot"))
                priorities = [
                    _clean_text(item)
                    for item in list(compact_context.get("active_priorities") or [])[:2]
                    if _clean_text(item)
                ]
                request_goal = (
                    "State two live signals, one tradeoff, and one concrete next move."
                    if is_status_probe
                    else "State what you are noticing now, one uncertainty, and one next move."
                )
                status_prompt = (
                    "You are JARVIS. Reply naturally in <=3 sentences. "
                    f"{request_goal} "
                    "Do not repeat the user text or output internal labels. "
                    f"User={json.dumps(user_text)} "
                    f"Status={json.dumps(status_snapshot)} "
                    f"Priorities={json.dumps(priorities)}"
                )
                status_timeout = min(total_timeout, 8.5 if not is_high_stakes else 10.5)
                status_text = self._query_text(
                    status_prompt,
                    timeout_seconds=status_timeout,
                    num_predict=72,
                    model_override=presence_model_override,
                )
                if status_text:
                    cleaned_status = _clean_text(status_text)
                    if cleaned_status:
                        return cleaned_status
            if neutral_voice_mode:
                recent_turns = list(compact_context.get("recent_turns") or [])
                prompt = (
                    "You are JARVIS. Reply naturally and directly to the user in <=2 sentences. "
                    "Answer only what was asked. "
                    "Use recent turns only for short-range conversational continuity and pronoun resolution "
                    "(for example: there/that/it from the previous exchange). "
                    "Do not inject personal history, memory, projects, markets, academics, or internal continuity unless explicitly requested. "
                    "Do not lecture about tone or profanity; stay calm and address the underlying request. "
                    "If realtime data is requested and unavailable, say so plainly and ask one concise follow-up. "
                    "Do not output internal labels. "
                    f"User={json.dumps(user_text)} "
                    f"Recent={json.dumps(recent_turns, sort_keys=True)}"
                )
            else:
                prompt = (
                    "You are JARVIS as a measured, stateful partner. "
                    "Reply directly to the user's latest message using the provided dialogue context. "
                    "Do not mirror, parrot, or repeat the user text. "
                    "Do not output internal labels like Mode or Pondering mode. "
                    "Treat memory.semantic_snippets as ranked evidence; cite details from them only when relevant. "
                    "For partner turns, ground in at least one live-state signal, one longer-horizon identity signal, and one thread-memory signal. "
                    f"{partner_guidance} "
                    "If memory is weak, acknowledge uncertainty briefly and ask one precise clarifier. "
                    "If the user asks for status, use concrete context (risks, pending items, priorities) in plain language. "
                    "If ambiguity remains, include one focused follow-up question. "
                    "Keep narrative natural, specific, and <=3 sentences. "
                    f"Data={json.dumps(structured, sort_keys=True)} Context={json.dumps(compact_context, sort_keys=True)}"
                )
            if force_model_presence_reply:
                first_query_timeout = min(total_timeout, max(8.0, total_timeout))
            elif partner_dialogue_turn:
                first_query_timeout = min(total_timeout, max(10.0, total_timeout))
            elif is_low_signal_social:
                first_query_timeout = min(total_timeout, 2.2)
            else:
                # Keep everyday turns responsive, but give local 14b-class models
                # enough budget to avoid avoidable fallback chatter.
                first_query_timeout = min(total_timeout * 0.78, 11.0 if not is_high_stakes else 13.0)
                first_query_timeout = max(1.8, first_query_timeout)
            text_response = self._query_text(
                prompt,
                timeout_seconds=first_query_timeout,
                num_predict=72,
                model_override=presence_model_override,
            )
            if not text_response and not is_low_signal_social:
                compact_retry_prompt = (
                    "You are JARVIS. Reply in 1-2 concise sentences with one concrete next step. "
                    "Do not repeat the user text. "
                    f"User={json.dumps(user_text)} "
                    f"Status={json.dumps(compact_context.get('status_snapshot') or '')}"
                )
                if force_model_presence_reply:
                    retry_timeout = min(total_timeout, max(first_query_timeout + 2.0, 10.0))
                else:
                    retry_timeout = min(total_timeout, 5.5 if not is_high_stakes else 7.0)
                text_response = self._query_text(
                    compact_retry_prompt,
                    timeout_seconds=retry_timeout,
                    num_predict=48,
                    model_override=presence_model_override,
                )
            if text_response:
                maybe_json = None
                try:
                    maybe_json = json.loads(text_response)
                except json.JSONDecodeError:
                    maybe_json = None
                narrative = self._extract_narrative(maybe_json) if isinstance(maybe_json, dict) else None
                if narrative:
                    return narrative
                cleaned = _clean_text(text_response)
                if neutral_voice_mode and cleaned:
                    refusal_markers = (
                        "can't engage in that kind of language",
                        "cannot engage in that kind of language",
                        "let's keep the conversation respectful and constructive",
                        "lets keep the conversation respectful and constructive",
                        "i don't understand the context of your statement",
                        "i dont understand the context of your statement",
                        "i'm sorry, but i don't understand",
                        "im sorry, but i dont understand",
                    )
                    if any(marker in cleaned.lower() for marker in refusal_markers):
                        cleaned = ""
                if cleaned:
                    return cleaned
            # Fail fast to heuristic fallback when model misses budget; keep phase-B responsive.
            return super().draft_synthesis(kind=kind, structured=structured, context=context)
        else:
            prompt = (
                "Return strict JSON only with this exact schema: "
                "{\"narrative\":\"...\"}. "
                "Narrative must be <=2 sentences, spoken naturally, and include uncertainty when relevant. "
                f"Kind={kind}; Data={json.dumps(structured, sort_keys=True)}"
            )
        query_timeout = min(float(self.timeout_seconds), 2.5)
        payload = self._query_json(prompt, timeout_seconds=query_timeout)
        narrative = self._extract_narrative(payload)
        if narrative:
            return narrative
        return super().draft_synthesis(kind=kind, structured=structured, context=context)

    def draft_interrupt_rationale(
        self,
        *,
        candidate: dict[str, Any],
        decision: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[str, str] | None:
        prompt = (
            "Return JSON {\"why_now\":\"...\",\"why_not_later\":\"...\"}. "
            "Ground in urgency, confidence, and suppression windows. "
            f"Candidate={json.dumps(candidate, sort_keys=True)} Decision={json.dumps(decision, sort_keys=True)}"
        )
        payload = self._query_json(prompt)
        if payload:
            why_now = str(payload.get("why_now") or "").strip()
            why_not = str(payload.get("why_not_later") or "").strip()
            if why_now and why_not:
                return (why_now, why_not)
        return super().draft_interrupt_rationale(
            candidate=candidate,
            decision=decision,
            context=context,
        )

    def _extract_narrative(self, payload: dict[str, Any] | None) -> str | None:
        if not isinstance(payload, dict):
            return None

        def _coerce(value: Any) -> str:
            if isinstance(value, str):
                return _clean_text(value)
            if isinstance(value, dict):
                for key in ("content", "text", "message", "narrative", "description", "output"):
                    if isinstance(value.get(key), str):
                        return _clean_text(value.get(key))
                return ""
            return ""

        for key in (
            "narrative",
            "reply",
            "response",
            "text",
            "message",
            "output",
            "content",
            "description",
            "summary",
        ):
            candidate = _coerce(payload.get(key))
            if candidate:
                return candidate

        operator = _clean_text(payload.get("operator"))
        description = _clean_text(payload.get("description"))
        if description and operator:
            return f"{operator}: {description}"
        if description:
            return description

        # Last resort: stitch short text values so we still get model-assisted output.
        stitched: list[str] = []
        for value in payload.values():
            text = _coerce(value)
            if text:
                stitched.append(text)
            if len(stitched) >= 2:
                break
        if stitched:
            return " ".join(stitched).strip()
        return None
