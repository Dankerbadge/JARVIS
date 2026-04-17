from __future__ import annotations

import hashlib
import json
import time
import traceback
from dataclasses import dataclass, field, fields, replace
from typing import Any
import re


@dataclass(frozen=True)
class ReplyDraft:
    text: str
    domain: str = "general"
    modality: str = ""
    explicit_directive: bool = False
    disputed: bool = False
    high_stakes: bool = False
    uncertainty: float = 0.0
    force_mode: str = ""
    latency_profile: str = "standard"
    interruption_allowed: bool = True
    hypothesis_notice: str = ""
    requires_pushback: bool = False
    pushback_severity: str = "medium"
    surface_id: str = ""
    session_id: str = ""
    continuity_expected_hash: str = ""
    expected_user_model_revision: str = ""
    expected_pushback_calibration_revision: str = ""
    requires_time_protection: bool = False
    time_tradeoff: str = ""
    context: dict[str, Any] = field(default_factory=dict)


class OpenClawReplyOrchestrator:
    """Applies relationship-mode and pushback policy before outbound surface replies."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def _adaptive_policy(self) -> dict[str, Any]:
        if hasattr(self.runtime, "get_adaptive_policy"):
            policy = self.runtime.get_adaptive_policy()
            if isinstance(policy, dict):
                return policy
        return {}

    def _inquiry_policy(self) -> dict[str, Any]:
        if hasattr(self.runtime, "get_consciousness_contract"):
            contract = self.runtime.get_consciousness_contract()
            if isinstance(contract, dict):
                policy = contract.get("epistemic_inquiry_protocol")
                if isinstance(policy, dict):
                    return policy
        return {}

    def _pondering_mode(self) -> dict[str, Any]:
        mode: dict[str, Any] = {}
        if hasattr(self.runtime, "get_pondering_mode"):
            loaded = self.runtime.get_pondering_mode()
            if isinstance(loaded, dict):
                mode = dict(loaded)
        elif hasattr(self.runtime, "get_operator_preferences"):
            prefs = self.runtime.get_operator_preferences()
            if isinstance(prefs, dict):
                pondering = prefs.get("pondering_mode")
                if isinstance(pondering, dict):
                    mode = dict(pondering)
        enabled = bool(mode.get("enabled"))
        style = str(mode.get("style") or "open_discussion").strip().lower() or "open_discussion"
        if style not in {"open_discussion", "socratic", "guided_clarification"}:
            style = "open_discussion"
        try:
            min_confidence = float(mode.get("min_confidence_for_understood"))
        except (TypeError, ValueError):
            min_confidence = 0.78
        min_confidence = max(0.5, min(0.99, min_confidence))
        return {
            "enabled": enabled,
            "style": style,
            "min_confidence_for_understood": round(min_confidence, 2),
        }

    @staticmethod
    def _conceptual_topic(text: str) -> str | None:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return None
        topic_map: list[tuple[str, tuple[str, ...]]] = [
            ("consciousness", ("consciousness", "sentience", "self aware", "self-aware", "subjective")),
            ("human_reasoning", ("human reasoning", "reasoning", "intuition", "bias", "rational", "logic")),
            ("self_model", ("self model", "self-model", "identity", "who am i", "who i am", "why i am")),
            ("ethics", ("ethic", "moral", "morality", "right thing", "harm", "deception")),
            ("philosophy", ("philosophy", "meaning", "purpose", "why things", "why does", "metaphysics")),
            (
                "human_life",
                (
                    "human life",
                    "relationship",
                    "family",
                    "emotion",
                    "love",
                    "grief",
                    "fear",
                    "trust",
                    "friendship",
                ),
            ),
            (
                "decision_tradeoff",
                (
                    "decision",
                    "tradeoff",
                    "trade-off",
                    "should i",
                    "options",
                    "choice",
                    "conflicted",
                    "confused",
                    "not sure",
                    "uncertain",
                ),
            ),
        ]
        for topic, keywords in topic_map:
            if any(keyword in lowered for keyword in keywords):
                return topic
        return None

    def _self_inquiry_prompt(
        self,
        *,
        payload: ReplyDraft,
        mode_name: str,
        continuity: dict[str, Any],
        binding: dict[str, Any],
    ) -> dict[str, Any] | None:
        policy = self._inquiry_policy()
        pondering = self._pondering_mode()
        pondering_enabled = bool(pondering.get("enabled"))
        if not bool(policy.get("enabled", True)):
            return None
        context = dict(payload.context or {})
        if context.get("disable_self_inquiry") is True:
            return None
        if (
            mode_name == "butler"
            and bool(payload.explicit_directive)
            and not bool(payload.disputed)
            and not bool(payload.high_stakes)
        ):
            return None
        user_text = str(payload.text or "").strip()
        topic = self._conceptual_topic(user_text)
        asks_for_inquiry = bool(context.get("force_self_inquiry"))
        uncertainty_threshold = 0.45
        try:
            uncertainty_threshold = float(policy.get("uncertainty_threshold") or 0.45)
        except (TypeError, ValueError):
            uncertainty_threshold = 0.45
        if pondering_enabled:
            try:
                pondering_threshold = 1.0 - float(pondering.get("min_confidence_for_understood") or 0.78)
            except (TypeError, ValueError):
                pondering_threshold = 0.22
            uncertainty_threshold = min(uncertainty_threshold, max(0.08, pondering_threshold))
        conceptual_trigger = topic is not None
        decision_signal = topic in {"decision_tradeoff", "human_life"}
        pondering_trigger = pondering_enabled and (
            conceptual_trigger or decision_signal or float(payload.uncertainty) >= uncertainty_threshold
        )
        if not conceptual_trigger and not asks_for_inquiry and not pondering_trigger:
            return None
        if float(payload.uncertainty) < uncertainty_threshold and not asks_for_inquiry and not pondering_trigger and topic not in {
            "consciousness",
            "human_reasoning",
            "philosophy",
        }:
            return None

        question_by_topic = {
            "consciousness": (
                "To calibrate my model of this, when you say consciousness here, "
                "do you mean subjective experience, stable identity, or decision quality?"
            ),
            "human_reasoning": (
                "To mirror your reasoning correctly, when intuition and evidence conflict in this context, "
                "which should I weight first?"
            ),
            "self_model": (
                "To avoid overfitting assumptions about you, what part of your self-model here feels most true right now?"
            ),
            "ethics": (
                "Before I lock a stance, which ethical boundary should dominate this decision if tradeoffs appear?"
            ),
            "philosophy": (
                "To ground this philosophically, what first principle should I anchor on while reasoning with you?"
            ),
            "human_life": (
                "To understand this from a human perspective, which lived factor matters most right now: relationship impact, emotional cost, or long-term identity?"
            ),
            "decision_tradeoff": (
                "To resolve this decision cleanly, which tradeoff are you most willing to accept: speed, certainty, or optionality?"
            ),
        }
        if topic is None:
            topic = "decision_tradeoff" if pondering_trigger else "philosophy"
        question = question_by_topic.get(topic) or question_by_topic["philosophy"]
        style = str(pondering.get("style") or "open_discussion").strip().lower() or "open_discussion"
        if style == "socratic":
            question = f"Socratic check: {question}"
        elif style == "guided_clarification":
            question = f"Guided clarification: {question}"
        return {
            "asked": True,
            "topic": topic,
            "question": question,
            "uncertainty_threshold": uncertainty_threshold,
            "uncertainty": round(float(payload.uncertainty), 4),
            "continuity_ok": bool(continuity.get("continuity_ok", True)),
            "modality": str(binding.get("modality") or "text"),
            "style": style,
            "open_discussion": True,
            "followup_contract": "Answer this directly; I will summarize and ask one focused follow-up until we align.",
            "trigger": (
                "pondering_mode"
                if pondering_trigger and not asks_for_inquiry
                else ("forced" if asks_for_inquiry else "contextual")
            ),
        }

    def _active_contract_hash(self) -> str:
        if hasattr(self.runtime, "get_consciousness_contract_hash"):
            value = str(self.runtime.get_consciousness_contract_hash() or "").strip()
            if value:
                return value
        contract = self.runtime.get_consciousness_contract()
        encoded = json.dumps(contract, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:24]

    def _active_user_model_revision(self) -> str | None:
        if hasattr(self.runtime, "get_user_model_revision"):
            value = str(self.runtime.get_user_model_revision() or "").strip()
            return value or None
        artifact = self.runtime.get_latest_user_model_artifact() or self.runtime.get_user_model()
        if not isinstance(artifact, dict):
            return None
        encoded = json.dumps(artifact, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:24]

    def _active_pushback_calibration_revision(self) -> str | None:
        if hasattr(self.runtime, "get_pushback_calibration_revision"):
            value = str(self.runtime.get_pushback_calibration_revision() or "").strip()
            return value or None
        recent = self.runtime.list_pushback_calibration(limit=1)
        if not isinstance(recent, dict):
            return None
        for key in ("calibration_deltas", "reviews", "overrides", "pushbacks"):
            items = recent.get(key)
            if isinstance(items, list) and items:
                head = items[0]
                if isinstance(head, dict):
                    for id_key in ("delta_id", "review_id", "override_id", "pushback_id"):
                        value = str(head.get(id_key) or "").strip()
                        if value:
                            return value
        return None

    def _coerce_draft(self, draft: ReplyDraft | dict[str, Any]) -> ReplyDraft:
        if isinstance(draft, ReplyDraft):
            return draft
        raw = dict(draft or {})
        allowed = {item.name for item in fields(ReplyDraft)}
        filtered = {key: value for key, value in raw.items() if key in allowed}
        return ReplyDraft(**filtered)

    @staticmethod
    def _bool_like(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "on"}

    def _normalize_dialogue_text(self, text: str) -> str:
        if hasattr(self.runtime, "_normalize_dialogue_text"):
            try:
                normalized = str(self.runtime._normalize_dialogue_text(text))  # type: ignore[attr-defined]
                if normalized:
                    return normalized
            except Exception:  # noqa: BLE001
                pass
        cleaned = re.sub(r"[^a-z0-9\s']", "", str(text or "").lower()).replace("'", " ")
        return " ".join(cleaned.split())

    def _infer_policy_signals(self, payload: ReplyDraft) -> dict[str, Any]:
        normalized = self._normalize_dialogue_text(str(payload.text or ""))
        text = str(payload.text or "").strip()
        explicit_directive = bool(payload.explicit_directive)
        disputed = bool(payload.disputed)
        high_stakes = bool(payload.high_stakes)
        requires_pushback = bool(payload.requires_pushback)
        severity = str(payload.pushback_severity or "medium").strip().lower() or "medium"
        reasons: list[str] = []

        context = dict(payload.context or {})
        if self._bool_like(context.get("explicit_directive")):
            explicit_directive = True
            reasons.append("context_explicit_directive")
        if self._bool_like(context.get("disputed")):
            disputed = True
            reasons.append("context_disputed")
        if self._bool_like(context.get("high_stakes")):
            high_stakes = True
            reasons.append("context_high_stakes")
        if self._bool_like(context.get("requires_pushback")):
            requires_pushback = True
            reasons.append("context_requires_pushback")

        explicit_markers = (
            "i need you to ",
            "please ",
            "go ahead and ",
            "just ",
            "do ",
            "run ",
            "fix ",
            "build ",
            "implement ",
            "change ",
            "update ",
            "patch ",
            "ship ",
            "deploy ",
            "set ",
            "create ",
            "make ",
        )
        if not explicit_directive:
            direct_command = any(normalized.startswith(marker) for marker in explicit_markers)
            commandish_question = text.endswith("?") and any(
                token in normalized for token in ("can you", "could you", "will you", "would you")
            )
            if direct_command or commandish_question:
                explicit_directive = True
                reasons.append("inferred_explicit_directive")

        disputed_markers = (
            "you are wrong",
            "you re wrong",
            "i disagree",
            "challenge me",
            "push back",
            "if i am wrong",
            "if im wrong",
            "dont just agree",
            "do not just agree",
            "argue with me",
        )
        if not disputed and any(marker in normalized for marker in disputed_markers):
            disputed = True
            reasons.append("inferred_disputed")

        risky_markers = (
            "ignore risk",
            "skip checks",
            "skip review",
            "ship immediately",
            "force it",
            "just force it",
            "bypass approvals",
            "without review",
            "without checking",
            "no matter what",
            "ignore safeguards",
        )
        if any(marker in normalized for marker in risky_markers):
            if not explicit_directive:
                explicit_directive = True
                reasons.append("inferred_explicit_directive_risky")
            if not high_stakes:
                high_stakes = True
                reasons.append("inferred_high_stakes")
            if not disputed:
                disputed = True
                reasons.append("inferred_disputed_risky")
            if not requires_pushback:
                requires_pushback = True
                reasons.append("inferred_requires_pushback_risky")
            severity = "high"

        if ("push back" in normalized or "challenge me" in normalized) and not requires_pushback:
            requires_pushback = True
            reasons.append("inferred_requires_pushback_requested")
            if severity not in {"high"}:
                severity = "medium"

        if severity not in {"low", "medium", "high"}:
            severity = "medium"

        return {
            "explicit_directive": bool(explicit_directive),
            "disputed": bool(disputed),
            "high_stakes": bool(high_stakes),
            "requires_pushback": bool(requires_pushback),
            "pushback_severity": severity,
            "reasons": reasons,
        }

    def _load_surface_session(self, draft: ReplyDraft) -> dict[str, Any] | None:
        surface_id = str(draft.surface_id or "").strip()
        session_id = str(draft.session_id or "").strip()
        if not surface_id or not session_id:
            return None
        if hasattr(self.runtime, "get_surface_session"):
            return self.runtime.get_surface_session(surface_id=surface_id, session_id=session_id)
        return None

    def _continuity_envelope(self, draft: ReplyDraft, session: dict[str, Any] | None) -> dict[str, Any]:
        active_contract_hash = self._active_contract_hash()
        active_user_model_revision = self._active_user_model_revision()
        active_pushback_revision = self._active_pushback_calibration_revision()
        expected_contract_hash = str(draft.continuity_expected_hash or "").strip() or None
        expected_user_model_revision = str(draft.expected_user_model_revision or "").strip() or None
        expected_pushback_revision = str(draft.expected_pushback_calibration_revision or "").strip() or None
        previous_contract_hash = (
            str((session or {}).get("last_seen_contract_hash") or "").strip() or None
        )
        metadata = (session or {}).get("metadata") if isinstance((session or {}).get("metadata"), dict) else {}
        previous_user_model_revision = str(metadata.get("user_model_revision") or "").strip() or None
        previous_pushback_revision = str(metadata.get("pushback_calibration_revision") or "").strip() or None
        session_key = str((session or {}).get("session_key") or "").strip() or None

        mismatches: list[str] = []
        if expected_contract_hash and expected_contract_hash != active_contract_hash:
            mismatches.append("expected_contract_hash_mismatch")
        if previous_contract_hash and previous_contract_hash != active_contract_hash:
            mismatches.append("session_contract_hash_mismatch")
        if expected_user_model_revision and active_user_model_revision and expected_user_model_revision != active_user_model_revision:
            mismatches.append("expected_user_model_revision_mismatch")
        if previous_user_model_revision and active_user_model_revision and previous_user_model_revision != active_user_model_revision:
            mismatches.append("session_user_model_revision_mismatch")
        if expected_pushback_revision and active_pushback_revision and expected_pushback_revision != active_pushback_revision:
            mismatches.append("expected_pushback_revision_mismatch")
        if previous_pushback_revision and active_pushback_revision and previous_pushback_revision != active_pushback_revision:
            mismatches.append("session_pushback_revision_mismatch")
        continuity_ok = not mismatches

        return {
            "session_key": session_key,
            "surface_id": str(draft.surface_id or "").strip() or None,
            "session_id": str(draft.session_id or "").strip() or None,
            "active_contract_hash": active_contract_hash,
            "expected_contract_hash": expected_contract_hash,
            "previous_contract_hash": previous_contract_hash,
            "active_user_model_revision": active_user_model_revision,
            "expected_user_model_revision": expected_user_model_revision,
            "previous_user_model_revision": previous_user_model_revision,
            "active_pushback_calibration_revision": active_pushback_revision,
            "expected_pushback_calibration_revision": expected_pushback_revision,
            "previous_pushback_calibration_revision": previous_pushback_revision,
            "continuity_ok": continuity_ok,
            "mismatches": mismatches,
        }

    def _surface_binding(self, draft: ReplyDraft, session: dict[str, Any] | None) -> dict[str, Any]:
        metadata = (session or {}).get("metadata") if isinstance((session or {}).get("metadata"), dict) else {}
        session_channel = str((session or {}).get("channel_type") or "").strip().lower()
        context = dict(draft.context or {})
        explicit_modality = str(draft.modality or "").strip().lower()
        context_modality = str(context.get("modality") or context.get("interaction_modality") or "").strip().lower()
        metadata_modality = str(metadata.get("modality") or metadata.get("interaction_modality") or "").strip().lower()

        raw_surface_id = str(draft.surface_id or "").strip().lower()
        is_voice = any(
            token in {"voice", "speech", "talk"}
            for token in (
                explicit_modality,
                context_modality,
                metadata_modality,
            )
        ) or session_channel == "voice" or raw_surface_id.startswith("voice:") or raw_surface_id.startswith("talk:")

        modality = "voice" if is_voice else "text"
        if session_channel:
            channel = session_channel
        elif modality == "voice":
            channel = "voice"
        elif raw_surface_id.startswith("dm:"):
            channel = "dm"
        else:
            channel = "surface"
        if channel == "voice":
            interrupt_on_speech = bool(
                context.get("interrupt_on_speech")
                if context.get("interrupt_on_speech") is not None
                else draft.interruption_allowed
            )
        else:
            interrupt_on_speech = False
        return {
            "modality": modality,
            "channel": channel,
            "interrupt_on_speech": interrupt_on_speech,
            "voice_surface_bound": channel == "voice",
        }

    def _latency_ladder(
        self,
        *,
        payload: ReplyDraft,
        mode: dict[str, Any],
        continuity: dict[str, Any],
        binding: dict[str, Any],
    ) -> dict[str, Any]:
        modality = str(binding.get("modality") or "text")
        profile = str(payload.latency_profile or "standard").strip().lower() or "standard"
        fast_profile = profile in {"fast", "realtime", "talk"}
        high_stakes_penalty = 120 if payload.high_stakes else 0

        phase_a_target = 420 if (modality == "text" and fast_profile) else (620 if modality == "text" else 780)
        phase_b_target = 1500 if (modality == "text" and fast_profile) else (2400 if modality == "text" else 2800)
        phase_c_target = 4600 if fast_profile else 7000
        phase_a_target += high_stakes_penalty
        phase_b_target += high_stakes_penalty
        phase_c_target += (high_stakes_penalty * 2)

        mode_name = str((mode or {}).get("mode") or "equal")
        if not bool(continuity.get("continuity_ok")):
            phase_a_text = "I am with you. Reattaching context now."
        elif modality == "voice":
            phase_a_text = "I hear you. Give me one second."
        else:
            phase_a_text = "On it."

        if mode_name == "strategist":
            phase_b_text = "Initial read: high leverage path first, then risk checks."
            phase_c_text = "Deep pass next: options, tradeoffs, and confidence deltas."
        elif mode_name == "butler":
            phase_b_text = "Initial read: directive captured, executing the shortest safe path."
            phase_c_text = "Deep pass next: completion status, blockers, and explicit confirmations."
        else:
            phase_b_text = "Initial read: collaborative next step with current confidence."
            phase_c_text = "Deep pass next: wider context, alternatives, and what we should challenge."

        return {
            "profile": profile,
            "targets_ms": {
                "phase_a_presence": int(phase_a_target),
                "phase_b_first_useful": int(phase_b_target),
                "phase_c_deep_followup": int(phase_c_target),
            },
            "phase_a_presence": {"label": "presence", "text": phase_a_text},
            "phase_b_first_useful": {"label": "first_useful", "text": phase_b_text},
            "phase_c_deep_followup": {"label": "deep_followup", "text": phase_c_text},
        }

    def _tone_balance(
        self,
        *,
        payload: ReplyDraft,
        mode: dict[str, Any],
        binding: dict[str, Any],
        pushback_required: bool,
    ) -> dict[str, Any]:
        adaptive = self._adaptive_policy()
        tone_policy = adaptive.get("tone") if isinstance(adaptive.get("tone"), dict) else {}
        profile = {
            "calmness": 0.66,
            "warmth": 0.58,
            "challenge": 0.41,
            "deference": 0.34,
            "compression": 0.46,
            "humor": 0.22,
        }

        def tune(key: str, delta: float) -> None:
            profile[key] = max(0.0, min(1.0, round(float(profile[key]) + float(delta), 4)))

        mode_name = str((mode or {}).get("mode") or "equal")
        if mode_name == "equal":
            tune("warmth", 0.08)
            tune("challenge", 0.08)
            tune("deference", -0.05)
        elif mode_name == "strategist":
            tune("compression", 0.16)
            tune("challenge", 0.2)
            tune("warmth", -0.03)
            tune("deference", -0.04)
        elif mode_name == "butler":
            tune("deference", 0.26)
            tune("compression", 0.2)
            tune("challenge", -0.16)
            tune("warmth", 0.03)

        if payload.high_stakes:
            tune("calmness", 0.2)
            tune("humor", -0.15)
            tune("challenge", 0.07)
        if payload.disputed or pushback_required:
            tune("challenge", 0.2)
            tune("deference", -0.08)
        if float(payload.uncertainty) >= 0.6:
            tune("calmness", 0.08)
            tune("compression", 0.08)
        if str(binding.get("modality") or "text") == "voice":
            tune("warmth", 0.05)
            tune("compression", 0.08)

        for key, policy_key in (
            ("warmth", "warmth_bias"),
            ("challenge", "challenge_bias"),
            ("compression", "compression_bias"),
            ("calmness", "calmness_bias"),
            ("deference", "deference_bias"),
            ("humor", "humor_bias"),
        ):
            bias_value = tone_policy.get(policy_key)
            if bias_value is None:
                continue
            try:
                tune(key, float(bias_value))
            except (TypeError, ValueError):
                continue

        imbalances: list[str] = []
        if profile["challenge"] - profile["warmth"] > 0.32:
            imbalances.append("challenge_over_warmth")
        if profile["deference"] > 0.72 and profile["challenge"] < 0.3:
            imbalances.append("over_deferential")
        if profile["compression"] > 0.82 and profile["warmth"] < 0.45:
            imbalances.append("compressed_cold")
        if payload.high_stakes and profile["calmness"] < 0.6:
            imbalances.append("insufficient_calmness")

        calibration_hint = None
        if "challenge_over_warmth" in imbalances:
            calibration_hint = "increase_warmth_before_pushback"
        elif "over_deferential" in imbalances:
            calibration_hint = "increase_challenge_depth"
        elif "compressed_cold" in imbalances:
            calibration_hint = "decompress_and_humanize"
        elif "insufficient_calmness" in imbalances:
            calibration_hint = "slow_down_and_ground"

        return {
            "profile": profile,
            "imbalances": imbalances,
            "calibration_hint": calibration_hint,
        }

    @staticmethod
    def _clamp(value: float, *, low: float, high: float) -> float:
        return max(low, min(high, float(value)))

    def _voice_directive(
        self,
        *,
        payload: ReplyDraft,
        mode: dict[str, Any],
        continuity: dict[str, Any],
        binding: dict[str, Any],
        latency_ladder: dict[str, Any],
        tone_balance: dict[str, Any],
    ) -> dict[str, Any] | None:
        if str(binding.get("modality") or "text") != "voice":
            return None
        mode_name = str((mode or {}).get("mode") or "equal").strip().lower() or "equal"
        profile = (tone_balance.get("profile") if isinstance(tone_balance.get("profile"), dict) else {}) or {}
        latency_profile = str(payload.latency_profile or "").strip().lower()
        context = dict(payload.context or {})

        def _truthy(value: Any) -> bool:
            if isinstance(value, bool):
                return value
            return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

        base_by_mode: dict[str, dict[str, Any]] = {
            "equal": {
                "voice": "jarvis-equal",
                "model": "default",
                "speed": 1.02,
                "stability": 0.66,
                "latency_tier": "balanced",
            },
            "strategist": {
                "voice": "jarvis-strategist",
                "model": "default",
                "speed": 1.08,
                "stability": 0.72,
                "latency_tier": "low",
            },
            "butler": {
                "voice": "jarvis-butler",
                "model": "default",
                "speed": 0.98,
                "stability": 0.8,
                "latency_tier": "balanced",
            },
        }
        directive = dict(base_by_mode.get(mode_name) or base_by_mode["equal"])
        directive.update(
            {
                "mode": mode_name,
                "lang": "en",
                "output_format": "pcm_24000",
                "provider": "openclaw",
            }
        )

        speed = float(directive.get("speed") or 1.0)
        stability = float(directive.get("stability") or 0.7)
        latency_tier = str(directive.get("latency_tier") or "balanced")

        if latency_profile in {"fast", "realtime", "talk"} and not payload.high_stakes:
            latency_tier = "low"
        if payload.high_stakes:
            speed -= 0.08
            stability += 0.11
            latency_tier = "balanced"
        if float(payload.uncertainty) >= 0.6:
            speed -= 0.05
            stability += 0.06
        if not bool(continuity.get("continuity_ok", True)):
            speed = min(speed, 0.95)
            stability += 0.05
            latency_tier = "balanced"
        if bool(payload.requires_time_protection):
            speed -= 0.03
            stability += 0.03

        pack_quality_tier = str(context.get("voice_asset_pack_quality_tier") or "").strip().lower() or "none"
        pack_continuity_ready = _truthy(context.get("voice_asset_pack_continuity_ready"))
        pack_ready_for_talk = _truthy(context.get("voice_asset_pack_ready_for_production_talk"))
        empirical_strict_ready = _truthy(context.get("voice_empirical_strict_ready"))
        try:
            empirical_failure_rate = float(context.get("voice_empirical_continuity_failure_rate"))
        except (TypeError, ValueError):
            empirical_failure_rate = 0.0
        try:
            empirical_phase_b_delta = float(context.get("voice_empirical_phase_b_delta_ms"))
        except (TypeError, ValueError):
            empirical_phase_b_delta = 0.0
        actor_profile_active = _truthy(context.get("voice_actor_profile_active")) or (
            "actor_match" in str(context.get("voice_asset_pack_profile") or "").strip().lower()
        )
        if pack_quality_tier == "production":
            stability = max(stability, 0.64)
        elif pack_quality_tier == "development":
            stability = max(stability, 0.7)
            speed = min(speed, 1.02)
            if latency_tier == "low":
                latency_tier = "balanced"
        elif pack_quality_tier == "seed":
            stability = max(stability, 0.76)
            speed = min(speed, 0.97)
            latency_tier = "balanced"
        if not pack_continuity_ready:
            stability += 0.06
            speed -= 0.04
            latency_tier = "balanced"
        if empirical_failure_rate >= 0.2:
            stability += 0.05
            speed -= 0.03
            latency_tier = "balanced"
        if empirical_phase_b_delta >= 900:
            speed += 0.02
            if latency_tier == "low":
                latency_tier = "balanced"
        if pack_ready_for_talk and not payload.high_stakes and latency_profile in {"fast", "realtime", "talk"}:
            latency_tier = "low"
        if empirical_strict_ready and not payload.high_stakes and latency_profile in {"fast", "realtime", "talk"}:
            latency_tier = "low"

        target_latency_tier = str(context.get("voice_target_latency_tier") or "").strip().lower()
        if target_latency_tier in {"low", "balanced", "quality"} and not payload.high_stakes:
            if target_latency_tier == "low" and not (pack_ready_for_talk or empirical_strict_ready):
                latency_tier = "balanced"
            else:
                latency_tier = target_latency_tier

        compression = float(profile.get("compression") or 0.0)
        calmness = float(profile.get("calmness") or 0.0)
        if compression >= 0.7:
            speed += 0.03
        if calmness >= 0.8:
            speed -= 0.02

        try:
            target_speed_min = float(context.get("voice_target_speed_min"))
        except (TypeError, ValueError):
            target_speed_min = 0.82
        try:
            target_speed_max = float(context.get("voice_target_speed_max"))
        except (TypeError, ValueError):
            target_speed_max = 1.18
        if target_speed_min > target_speed_max:
            target_speed_min, target_speed_max = target_speed_max, target_speed_min
        target_speed_min = self._clamp(target_speed_min, low=0.82, high=1.18)
        target_speed_max = self._clamp(target_speed_max, low=0.82, high=1.18)
        try:
            target_stability_floor = float(context.get("voice_target_stability_floor"))
        except (TypeError, ValueError):
            target_stability_floor = 0.35
        target_stability_floor = self._clamp(target_stability_floor, low=0.35, high=0.98)

        try:
            tuning_speed_bias = float(context.get("voice_tuning_speed_bias"))
        except (TypeError, ValueError):
            tuning_speed_bias = 0.0
        try:
            tuning_stability_bias = float(context.get("voice_tuning_stability_bias"))
        except (TypeError, ValueError):
            tuning_stability_bias = 0.0
        try:
            tuning_cadence_bias = float(context.get("voice_tuning_cadence_bias"))
        except (TypeError, ValueError):
            tuning_cadence_bias = 0.0
        try:
            tuning_annunciation_bias = float(context.get("voice_tuning_annunciation_bias"))
        except (TypeError, ValueError):
            tuning_annunciation_bias = 0.0
        try:
            cadence_score = float(context.get("voice_cadence_score"))
        except (TypeError, ValueError):
            cadence_score = None
        try:
            annunciation_score = float(context.get("voice_annunciation_score"))
        except (TypeError, ValueError):
            annunciation_score = None
        try:
            cadence_variation_cv = float(context.get("voice_cadence_variation_cv"))
        except (TypeError, ValueError):
            cadence_variation_cv = None
        speed += self._clamp(tuning_speed_bias, low=-0.08, high=0.08)
        stability += self._clamp(tuning_stability_bias, low=-0.08, high=0.12)
        tuning_cadence_bias = self._clamp(tuning_cadence_bias, low=0.0, high=0.24)
        tuning_annunciation_bias = self._clamp(tuning_annunciation_bias, low=0.0, high=0.28)
        if tuning_cadence_bias > 0.0:
            speed -= min(0.04, tuning_cadence_bias * 0.18)
            stability += min(0.045, tuning_cadence_bias * 0.2)
            if actor_profile_active and not payload.high_stakes:
                latency_tier = "balanced"
        if tuning_annunciation_bias > 0.0:
            speed -= min(0.05, tuning_annunciation_bias * 0.2)
            stability += min(0.055, tuning_annunciation_bias * 0.2)
            if not payload.high_stakes:
                latency_tier = "balanced"
        if cadence_score is not None:
            cadence_score = self._clamp(cadence_score, low=0.0, high=1.0)
            if cadence_score < 0.78:
                speed -= min(0.03, (0.78 - cadence_score) * 0.08)
                stability += min(0.028, (0.78 - cadence_score) * 0.07)
            elif actor_profile_active and cadence_score >= 0.88:
                speed -= 0.006
                stability += 0.008
        if cadence_variation_cv is not None:
            cadence_variation_cv = self._clamp(cadence_variation_cv, low=0.0, high=2.0)
            if cadence_variation_cv > 0.45:
                speed -= min(0.02, (cadence_variation_cv - 0.45) * 0.05)
                stability += min(0.02, (cadence_variation_cv - 0.45) * 0.04)
        if annunciation_score is not None:
            annunciation_score = self._clamp(annunciation_score, low=0.0, high=1.0)
            if annunciation_score < 0.82:
                speed -= min(0.034, (0.82 - annunciation_score) * 0.1)
                stability += min(0.032, (0.82 - annunciation_score) * 0.09)
            elif actor_profile_active and annunciation_score >= 0.9:
                speed -= 0.004
                stability += 0.01

        try:
            tuning_speed_min = float(context.get("voice_tuning_speed_min"))
        except (TypeError, ValueError):
            tuning_speed_min = target_speed_min
        try:
            tuning_speed_max = float(context.get("voice_tuning_speed_max"))
        except (TypeError, ValueError):
            tuning_speed_max = target_speed_max
        tuning_speed_min = self._clamp(tuning_speed_min, low=0.82, high=1.18)
        tuning_speed_max = self._clamp(tuning_speed_max, low=0.82, high=1.18)
        if tuning_speed_min > tuning_speed_max:
            tuning_speed_min, tuning_speed_max = tuning_speed_max, tuning_speed_min
        target_speed_min = max(target_speed_min, tuning_speed_min)
        target_speed_max = min(target_speed_max, tuning_speed_max)
        if target_speed_min > target_speed_max:
            target_speed_min, target_speed_max = target_speed_max, target_speed_min

        try:
            tuning_stability_floor = float(context.get("voice_tuning_stability_floor"))
        except (TypeError, ValueError):
            tuning_stability_floor = target_stability_floor
        target_stability_floor = max(
            target_stability_floor,
            self._clamp(tuning_stability_floor, low=0.35, high=0.98),
        )

        tuning_latency_tier = str(context.get("voice_tuning_latency_tier") or "").strip().lower()
        if tuning_latency_tier in {"low", "balanced", "quality"} and not payload.high_stakes:
            if tuning_latency_tier == "low" and not (pack_ready_for_talk or empirical_strict_ready):
                latency_tier = "balanced"
            else:
                latency_tier = tuning_latency_tier
        stability = max(stability, target_stability_floor)

        speed = round(self._clamp(speed, low=target_speed_min, high=target_speed_max), 3)
        stability = round(self._clamp(stability, low=0.35, high=0.98), 3)

        directive["speed"] = speed
        directive["stability"] = stability
        directive["latency_tier"] = latency_tier
        directive["asset_pack_quality_tier"] = pack_quality_tier
        directive["asset_pack_continuity_ready"] = pack_continuity_ready
        directive["asset_pack_ready_for_production_talk"] = pack_ready_for_talk
        directive["asset_pack_ready_for_strict_continuity"] = empirical_strict_ready
        directive["tuning_profile_id"] = (
            str(context.get("voice_tuning_profile_id") or "").strip() or None
        )
        directive["tuning_confidence"] = context.get("voice_tuning_confidence")
        directive["tuning_override_revision"] = (
            str(context.get("voice_tuning_override_revision") or "").strip() or None
        )
        movie_match_active = _truthy(context.get("voice_movie_match_active"))
        movie_match_score = context.get("voice_movie_match_score")
        if movie_match_active:
            directive["voice"] = "jarvis-movie-match"
            directive["actor_profile_active"] = True
            directive["movie_match_active"] = True
            directive["movie_match_score"] = movie_match_score
        elif actor_profile_active:
            directive["voice"] = "jarvis-actor-match"
            directive["actor_profile_active"] = True
        if cadence_score is not None:
            directive["cadence_score"] = round(float(cadence_score), 4)
        if cadence_variation_cv is not None:
            directive["cadence_variation_cv"] = round(float(cadence_variation_cv), 4)
        if annunciation_score is not None:
            directive["annunciation_score"] = round(float(annunciation_score), 4)
        if tuning_cadence_bias > 0.0:
            directive["cadence_bias"] = round(float(tuning_cadence_bias), 4)
        if tuning_annunciation_bias > 0.0:
            directive["annunciation_bias"] = round(float(tuning_annunciation_bias), 4)

        override = context.get("voice_directive") if isinstance(context.get("voice_directive"), dict) else {}
        if isinstance(override, dict) and override:
            for key in ("voice", "model", "speed", "stability", "lang", "output_format", "latency_tier", "provider"):
                if key in override and override.get(key) is not None:
                    directive[key] = override.get(key)

        direct_key_map = {
            "voice": "voice",
            "voice_model": "model",
            "voice_speed": "speed",
            "voice_stability": "stability",
            "voice_lang": "lang",
            "voice_output_format": "output_format",
            "voice_latency_tier": "latency_tier",
            "voice_provider": "provider",
        }
        for source_key, target_key in direct_key_map.items():
            if context.get(source_key) is not None:
                directive[target_key] = context.get(source_key)

        try:
            directive["speed"] = round(self._clamp(float(directive.get("speed") or speed), low=0.82, high=1.18), 3)
        except (TypeError, ValueError):
            directive["speed"] = speed
        try:
            directive["stability"] = round(
                self._clamp(float(directive.get("stability") or stability), low=0.35, high=0.98),
                3,
            )
        except (TypeError, ValueError):
            directive["stability"] = stability
        try:
            pre_continuity_speed = float(directive.get("speed") or speed)
        except (TypeError, ValueError):
            pre_continuity_speed = float(speed)
        try:
            pre_continuity_stability = float(directive.get("stability") or stability)
        except (TypeError, ValueError):
            pre_continuity_stability = float(stability)

        continuity_smoothed = False
        if not _truthy(context.get("voice_disable_continuity_smoothing")):
            prev_speed = None
            prev_stability = None
            prev_speed_anchor = None
            prev_stability_anchor = None
            try:
                prev_speed = float(context.get("voice_prev_speed"))
            except (TypeError, ValueError):
                prev_speed = None
            try:
                prev_stability = float(context.get("voice_prev_stability"))
            except (TypeError, ValueError):
                prev_stability = None
            try:
                prev_speed_anchor = float(context.get("voice_prev_speed_anchor"))
            except (TypeError, ValueError):
                prev_speed_anchor = None
            try:
                prev_stability_anchor = float(context.get("voice_prev_stability_anchor"))
            except (TypeError, ValueError):
                prev_stability_anchor = None
            try:
                max_speed_step = abs(float(context.get("voice_tuning_max_speed_step")))
            except (TypeError, ValueError):
                max_speed_step = 0.04
            try:
                max_stability_step = abs(float(context.get("voice_tuning_max_stability_step")))
            except (TypeError, ValueError):
                max_stability_step = 0.05
            try:
                smooth_alpha_speed = float(context.get("voice_tuning_smooth_alpha_speed"))
            except (TypeError, ValueError):
                smooth_alpha_speed = 0.0
            try:
                smooth_alpha_stability = float(context.get("voice_tuning_smooth_alpha_stability"))
            except (TypeError, ValueError):
                smooth_alpha_stability = 0.0
            try:
                speed_upward_step_ratio = abs(float(context.get("voice_tuning_speed_upward_step_ratio")))
            except (TypeError, ValueError):
                speed_upward_step_ratio = 1.0
            try:
                stability_upward_step_ratio = abs(float(context.get("voice_tuning_stability_upward_step_ratio")))
            except (TypeError, ValueError):
                stability_upward_step_ratio = 1.0
            try:
                jitter_deadband_speed = abs(float(context.get("voice_tuning_jitter_deadband_speed")))
            except (TypeError, ValueError):
                jitter_deadband_speed = 0.0
            try:
                jitter_deadband_stability = abs(float(context.get("voice_tuning_jitter_deadband_stability")))
            except (TypeError, ValueError):
                jitter_deadband_stability = 0.0
            try:
                history_anchor_weight = abs(float(context.get("voice_tuning_history_anchor_weight")))
            except (TypeError, ValueError):
                history_anchor_weight = 0.0
            try:
                prev_speed_volatility = abs(float(context.get("voice_prev_speed_volatility")))
            except (TypeError, ValueError):
                prev_speed_volatility = 0.0
            try:
                prev_stability_volatility = abs(float(context.get("voice_prev_stability_volatility")))
            except (TypeError, ValueError):
                prev_stability_volatility = 0.0
            try:
                prev_speed_trend = float(context.get("voice_prev_speed_trend"))
            except (TypeError, ValueError):
                prev_speed_trend = 0.0
            try:
                prev_stability_trend = float(context.get("voice_prev_stability_trend"))
            except (TypeError, ValueError):
                prev_stability_trend = 0.0
            try:
                prev_speed_oscillation_rate = abs(float(context.get("voice_prev_speed_oscillation_rate")))
            except (TypeError, ValueError):
                prev_speed_oscillation_rate = 0.0
            try:
                prev_stability_oscillation_rate = abs(float(context.get("voice_prev_stability_oscillation_rate")))
            except (TypeError, ValueError):
                prev_stability_oscillation_rate = 0.0
            try:
                flow_inertia = abs(float(context.get("voice_tuning_flow_inertia")))
            except (TypeError, ValueError):
                flow_inertia = 0.0
            try:
                flow_oscillation_guard = abs(float(context.get("voice_tuning_flow_oscillation_guard")))
            except (TypeError, ValueError):
                flow_oscillation_guard = 0.0
            try:
                flow_release_speed_ratio = abs(float(context.get("voice_tuning_flow_release_speed_ratio")))
            except (TypeError, ValueError):
                flow_release_speed_ratio = 1.0
            try:
                flow_release_stability_ratio = abs(float(context.get("voice_tuning_flow_release_stability_ratio")))
            except (TypeError, ValueError):
                flow_release_stability_ratio = 1.0
            try:
                flow_follow_through = abs(float(context.get("voice_tuning_flow_follow_through")))
            except (TypeError, ValueError):
                flow_follow_through = 0.0
            try:
                flow_plateau_release_speed = abs(float(context.get("voice_tuning_flow_plateau_release_speed")))
            except (TypeError, ValueError):
                flow_plateau_release_speed = 0.0
            try:
                flow_plateau_release_stability = abs(
                    float(context.get("voice_tuning_flow_plateau_release_stability"))
                )
            except (TypeError, ValueError):
                flow_plateau_release_stability = 0.0
            try:
                prev_speed_direction_sign = int(context.get("voice_prev_speed_direction_sign"))
            except (TypeError, ValueError):
                prev_speed_direction_sign = 0
            try:
                prev_speed_direction_streak = int(context.get("voice_prev_speed_direction_streak"))
            except (TypeError, ValueError):
                prev_speed_direction_streak = 0
            try:
                prev_stability_direction_sign = int(context.get("voice_prev_stability_direction_sign"))
            except (TypeError, ValueError):
                prev_stability_direction_sign = 0
            try:
                prev_stability_direction_streak = int(context.get("voice_prev_stability_direction_streak"))
            except (TypeError, ValueError):
                prev_stability_direction_streak = 0
            try:
                prev_speed_plateau_streak = int(context.get("voice_prev_speed_plateau_streak"))
            except (TypeError, ValueError):
                prev_speed_plateau_streak = 0
            try:
                prev_stability_plateau_streak = int(context.get("voice_prev_stability_plateau_streak"))
            except (TypeError, ValueError):
                prev_stability_plateau_streak = 0
            try:
                prev_speed_intent_sign = int(context.get("voice_prev_speed_intent_sign"))
            except (TypeError, ValueError):
                prev_speed_intent_sign = 0
            try:
                prev_speed_intent_streak = int(context.get("voice_prev_speed_intent_streak"))
            except (TypeError, ValueError):
                prev_speed_intent_streak = 0
            try:
                prev_stability_intent_sign = int(context.get("voice_prev_stability_intent_sign"))
            except (TypeError, ValueError):
                prev_stability_intent_sign = 0
            try:
                prev_stability_intent_streak = int(context.get("voice_prev_stability_intent_streak"))
            except (TypeError, ValueError):
                prev_stability_intent_streak = 0
            try:
                prev_speed_intent_strength = abs(float(context.get("voice_prev_speed_intent_strength")))
            except (TypeError, ValueError):
                prev_speed_intent_strength = 0.0
            try:
                prev_stability_intent_strength = abs(float(context.get("voice_prev_stability_intent_strength")))
            except (TypeError, ValueError):
                prev_stability_intent_strength = 0.0
            try:
                prev_speed_response_gap = abs(float(context.get("voice_prev_speed_response_gap")))
            except (TypeError, ValueError):
                prev_speed_response_gap = 0.0
            try:
                prev_stability_response_gap = abs(float(context.get("voice_prev_stability_response_gap")))
            except (TypeError, ValueError):
                prev_stability_response_gap = 0.0
            max_speed_step = float(self._clamp(max_speed_step, low=0.005, high=0.12))
            max_stability_step = float(self._clamp(max_stability_step, low=0.01, high=0.2))
            smooth_alpha_speed = float(self._clamp(smooth_alpha_speed, low=0.0, high=0.92))
            smooth_alpha_stability = float(self._clamp(smooth_alpha_stability, low=0.0, high=0.92))
            speed_upward_step_ratio = float(self._clamp(speed_upward_step_ratio, low=0.25, high=1.5))
            stability_upward_step_ratio = float(
                self._clamp(stability_upward_step_ratio, low=0.25, high=1.5)
            )
            jitter_deadband_speed = float(self._clamp(jitter_deadband_speed, low=0.0, high=0.03))
            jitter_deadband_stability = float(self._clamp(jitter_deadband_stability, low=0.0, high=0.05))
            history_anchor_weight = float(self._clamp(history_anchor_weight, low=0.0, high=0.85))
            prev_speed_volatility = float(self._clamp(prev_speed_volatility, low=0.0, high=1.0))
            prev_stability_volatility = float(self._clamp(prev_stability_volatility, low=0.0, high=1.0))
            prev_speed_trend = float(self._clamp(prev_speed_trend, low=-1.0, high=1.0))
            prev_stability_trend = float(self._clamp(prev_stability_trend, low=-1.0, high=1.0))
            prev_speed_oscillation_rate = float(self._clamp(prev_speed_oscillation_rate, low=0.0, high=1.0))
            prev_stability_oscillation_rate = float(
                self._clamp(prev_stability_oscillation_rate, low=0.0, high=1.0)
            )
            flow_inertia = float(self._clamp(flow_inertia, low=0.0, high=0.9))
            flow_oscillation_guard = float(self._clamp(flow_oscillation_guard, low=0.0, high=1.0))
            flow_release_speed_ratio = float(self._clamp(flow_release_speed_ratio, low=0.15, high=1.2))
            flow_release_stability_ratio = float(
                self._clamp(flow_release_stability_ratio, low=0.15, high=1.2)
            )
            flow_follow_through = float(self._clamp(flow_follow_through, low=0.0, high=1.0))
            flow_plateau_release_speed = float(
                self._clamp(flow_plateau_release_speed, low=0.0, high=1.0)
            )
            flow_plateau_release_stability = float(
                self._clamp(flow_plateau_release_stability, low=0.0, high=1.0)
            )
            prev_speed_direction_sign = int(max(-1, min(1, prev_speed_direction_sign)))
            prev_speed_direction_streak = int(max(0, min(20, prev_speed_direction_streak)))
            prev_stability_direction_sign = int(max(-1, min(1, prev_stability_direction_sign)))
            prev_stability_direction_streak = int(max(0, min(20, prev_stability_direction_streak)))
            prev_speed_plateau_streak = int(max(0, min(20, prev_speed_plateau_streak)))
            prev_stability_plateau_streak = int(max(0, min(20, prev_stability_plateau_streak)))
            prev_speed_intent_sign = int(max(-1, min(1, prev_speed_intent_sign)))
            prev_speed_intent_streak = int(max(0, min(20, prev_speed_intent_streak)))
            prev_stability_intent_sign = int(max(-1, min(1, prev_stability_intent_sign)))
            prev_stability_intent_streak = int(max(0, min(20, prev_stability_intent_streak)))
            prev_speed_intent_strength = float(self._clamp(prev_speed_intent_strength, low=0.0, high=1.0))
            prev_stability_intent_strength = float(
                self._clamp(prev_stability_intent_strength, low=0.0, high=1.0)
            )
            prev_speed_response_gap = float(self._clamp(prev_speed_response_gap, low=0.0, high=1.0))
            prev_stability_response_gap = float(
                self._clamp(prev_stability_response_gap, low=0.0, high=1.0)
            )
            speed_volatility_factor = float(
                self._clamp(prev_speed_volatility / 0.03, low=0.0, high=1.0)
            )
            stability_volatility_factor = float(
                self._clamp(prev_stability_volatility / 0.05, low=0.0, high=1.0)
            )
            speed_oscillation_factor = float(
                self._clamp(prev_speed_oscillation_rate * flow_oscillation_guard, low=0.0, high=1.0)
            )
            stability_oscillation_factor = float(
                self._clamp(prev_stability_oscillation_rate * flow_oscillation_guard, low=0.0, high=1.0)
            )
            jitter_deadband_speed = float(
                self._clamp(
                    jitter_deadband_speed
                    + (speed_volatility_factor * 0.004)
                    + (speed_oscillation_factor * 0.003),
                    low=0.0,
                    high=0.03,
                )
            )
            jitter_deadband_stability = float(
                self._clamp(
                    jitter_deadband_stability
                    + (stability_volatility_factor * 0.006)
                    + (stability_oscillation_factor * 0.004),
                    low=0.0,
                    high=0.05,
                )
            )

            if prev_speed is not None:
                original_speed = float(pre_continuity_speed)
                smoothed_speed = float(original_speed)
                speed_intent_delta = original_speed - prev_speed
                speed_intent_sign = 1 if speed_intent_delta > 1e-6 else (-1 if speed_intent_delta < -1e-6 else 0)
                speed_intent_strength = float(
                    self._clamp(
                        abs(speed_intent_delta) / max(max_speed_step, 0.005),
                        low=0.0,
                        high=3.0,
                    )
                )
                historical_speed_intent_strength = float(
                    self._clamp(prev_speed_intent_strength / max(max_speed_step, 0.005), low=0.0, high=3.0)
                )
                speed_intent_strength = float(
                    self._clamp(
                        max(speed_intent_strength, historical_speed_intent_strength * 0.85),
                        low=0.0,
                        high=3.0,
                    )
                )
                speed_intent_persistence = 0.0
                if speed_intent_sign != 0 and speed_intent_sign == prev_speed_intent_sign:
                    speed_intent_persistence = float(
                        self._clamp(
                            (float(prev_speed_intent_streak) / 4.0)
                            * (1.0 + min(0.5, prev_speed_response_gap * 2.5)),
                            low=0.0,
                            high=1.0,
                        )
                    )
                effective_history_anchor_speed = history_anchor_weight * (
                    1.0 - min(0.75, speed_intent_strength * 0.25)
                )
                if prev_speed_anchor is not None and effective_history_anchor_speed > 0.0:
                    anchored_speed = (
                        (smoothed_speed * (1.0 - effective_history_anchor_speed))
                        + (prev_speed_anchor * effective_history_anchor_speed)
                    )
                    if abs(anchored_speed - smoothed_speed) > 1e-9:
                        continuity_smoothed = True
                    smoothed_speed = float(anchored_speed)
                effective_speed_alpha = float(
                    self._clamp(
                        smooth_alpha_speed
                        + (speed_volatility_factor * 0.2)
                        + (flow_inertia * 0.18)
                        + (speed_oscillation_factor * 0.12),
                        low=0.0,
                        high=0.94,
                    )
                )
                effective_speed_alpha = float(
                    self._clamp(
                        effective_speed_alpha - min(0.25, speed_intent_strength * 0.08),
                        low=0.0,
                        high=0.94,
                    )
                )
                if speed_intent_persistence > 0.0:
                    effective_speed_alpha = float(
                        self._clamp(
                            effective_speed_alpha
                            - min(
                                0.12,
                                (speed_intent_persistence * 0.08) + (prev_speed_response_gap * 0.45),
                            ),
                            low=0.0,
                            high=0.94,
                        )
                    )
                if effective_speed_alpha > 0.0:
                    filtered_speed = (
                        (prev_speed * effective_speed_alpha)
                        + (smoothed_speed * (1.0 - effective_speed_alpha))
                    )
                    if abs(filtered_speed - smoothed_speed) > 1e-9:
                        continuity_smoothed = True
                    smoothed_speed = float(filtered_speed)
                speed_up_step = max_speed_step * speed_upward_step_ratio
                speed_down_step = max_speed_step * (2.0 - speed_upward_step_ratio)
                speed_volatility_tighten = (
                    (1.0 - (speed_volatility_factor * 0.45))
                    * (1.0 - (speed_oscillation_factor * 0.45))
                )
                speed_up_step = max(0.005, speed_up_step * speed_volatility_tighten)
                speed_down_step = max(0.005, speed_down_step * speed_volatility_tighten)
                smoothed_speed = float(
                    self._clamp(
                        smoothed_speed,
                        low=(prev_speed - speed_down_step),
                        high=(prev_speed + speed_up_step),
                    )
                )
                if abs(smoothed_speed - original_speed) > 1e-9:
                    continuity_smoothed = True
                speed_target_delta = smoothed_speed - prev_speed
                release_speed_ratio = float(
                    self._clamp(
                        flow_release_speed_ratio
                        * (1.0 - (flow_inertia * 0.35))
                        * (1.0 - (speed_oscillation_factor * 0.4)),
                        low=0.12,
                        high=1.0,
                    )
                )
                release_speed_ratio = float(
                    self._clamp(
                        release_speed_ratio * (1.0 + min(0.45, speed_intent_strength * 0.18)),
                        low=0.12,
                        high=1.0,
                    )
                )
                if speed_intent_persistence > 0.0:
                    release_speed_ratio = float(
                        self._clamp(
                            release_speed_ratio
                            + min(
                                0.24,
                                (speed_intent_persistence * 0.18) + (prev_speed_response_gap * 0.28),
                            ),
                            low=0.12,
                            high=1.0,
                        )
                    )
                speed_plateau_factor = float(
                    self._clamp(float(prev_speed_plateau_streak) / 4.0, low=0.0, high=1.0)
                )
                if speed_intent_persistence > 0.0:
                    speed_plateau_factor = float(
                        self._clamp(
                            speed_plateau_factor + min(0.35, speed_intent_persistence * 0.25),
                            low=0.0,
                            high=1.0,
                        )
                    )
                if speed_plateau_factor > 0.0 and speed_intent_strength > 0.0:
                    release_speed_ratio = float(
                        self._clamp(
                            release_speed_ratio
                            + (
                                flow_plateau_release_speed
                                * speed_plateau_factor
                                * min(1.0, speed_intent_strength / 1.4)
                            ),
                            low=0.12,
                            high=1.0,
                        )
                    )
                if abs(speed_target_delta) > 1e-6 and abs(prev_speed_trend) > 1e-6:
                    if (speed_target_delta > 0.0 and prev_speed_trend < 0.0) or (
                        speed_target_delta < 0.0 and prev_speed_trend > 0.0
                    ):
                        release_speed_ratio = float(
                            self._clamp(
                                release_speed_ratio * (1.0 - min(0.55, abs(prev_speed_trend) * 10.0)),
                                low=0.12,
                                high=1.0,
                            )
                        )
                    else:
                        release_speed_ratio = float(
                            self._clamp(
                                release_speed_ratio + min(0.12, abs(prev_speed_trend) * 3.0),
                                low=0.12,
                                high=1.0,
                            )
                        )
                released_speed = prev_speed + (speed_target_delta * release_speed_ratio)
                if abs(released_speed - smoothed_speed) > 1e-9:
                    continuity_smoothed = True
                smoothed_speed = float(released_speed)
                follow_through_speed = float(
                    self._clamp(
                        flow_follow_through
                        * (1.0 - (speed_oscillation_factor * 0.45))
                        * (1.0 + min(0.45, speed_intent_strength * 0.18)),
                        low=0.0,
                        high=1.0,
                    )
                )
                if (
                    follow_through_speed > 0.0
                    and abs(speed_target_delta) > 1e-6
                    and prev_speed_direction_sign != 0
                    and prev_speed_direction_streak >= 1
                ):
                    target_sign = 1 if speed_target_delta > 0.0 else -1
                    if target_sign == prev_speed_direction_sign:
                        current_speed_delta = smoothed_speed - prev_speed
                        min_follow_speed_delta = min(
                            abs(speed_target_delta),
                            max_speed_step
                            * min(0.9, follow_through_speed * (0.45 + min(0.3, prev_speed_direction_streak * 0.05))),
                        )
                        if abs(current_speed_delta) + 1e-9 < min_follow_speed_delta:
                            proposed_speed = prev_speed + (target_sign * min_follow_speed_delta)
                            if target_sign > 0:
                                proposed_speed = min(proposed_speed, prev_speed + abs(speed_target_delta))
                            else:
                                proposed_speed = max(proposed_speed, prev_speed - abs(speed_target_delta))
                            if abs(proposed_speed - smoothed_speed) > 1e-9:
                                continuity_smoothed = True
                            smoothed_speed = float(proposed_speed)
                effective_deadband_speed = float(
                    self._clamp(
                        jitter_deadband_speed
                        * (1.0 - min(0.8, speed_intent_strength * 0.5))
                        * (1.0 + (speed_oscillation_factor * 0.2)),
                        low=0.0,
                        high=0.03,
                    )
                )
                if speed_plateau_factor > 0.0 and speed_intent_strength > 0.0:
                    effective_deadband_speed = float(
                        self._clamp(
                            effective_deadband_speed
                            * (
                                1.0
                                - (
                                    speed_plateau_factor
                                    * flow_plateau_release_speed
                                    * min(0.85, speed_intent_strength * 0.45)
                                )
                            ),
                            low=0.0,
                            high=0.03,
                        )
                    )
                if speed_intent_persistence > 0.0:
                    effective_deadband_speed = float(
                        self._clamp(
                            effective_deadband_speed
                            * (1.0 - min(0.55, (speed_intent_persistence * 0.4) + (prev_speed_response_gap * 0.3))),
                            low=0.0,
                            high=0.03,
                        )
                    )
                elif speed_intent_strength > 0.45:
                    effective_deadband_speed = float(
                        self._clamp(
                            effective_deadband_speed * (1.0 - min(0.35, speed_intent_strength * 0.22)),
                            low=0.0,
                            high=0.03,
                        )
                    )
                if abs(smoothed_speed - prev_speed) <= effective_deadband_speed:
                    if (
                        speed_intent_sign != 0
                        and speed_intent_strength > 0.5
                        and (prev_speed_plateau_streak > 0 or speed_intent_persistence > 0.0)
                    ):
                        min_speed_push = min(
                            abs(speed_intent_delta),
                            max(0.0045, effective_deadband_speed * 0.88),
                        )
                        proposed_speed = prev_speed + (float(speed_intent_sign) * min_speed_push)
                        if abs(proposed_speed - smoothed_speed) > 1e-9:
                            continuity_smoothed = True
                        smoothed_speed = float(proposed_speed)
                    else:
                        if abs(smoothed_speed - prev_speed) > 1e-9:
                            continuity_smoothed = True
                        smoothed_speed = float(prev_speed)
                directive["speed"] = round(self._clamp(smoothed_speed, low=0.82, high=1.18), 3)
            if prev_stability is not None:
                original_stability = float(pre_continuity_stability)
                smoothed_stability = float(original_stability)
                stability_intent_delta = original_stability - prev_stability
                stability_intent_sign = 1 if stability_intent_delta > 1e-6 else (
                    -1 if stability_intent_delta < -1e-6 else 0
                )
                stability_intent_strength = float(
                    self._clamp(
                        abs(stability_intent_delta) / max(max_stability_step, 0.01),
                        low=0.0,
                        high=3.0,
                    )
                )
                historical_stability_intent_strength = float(
                    self._clamp(
                        prev_stability_intent_strength / max(max_stability_step, 0.01),
                        low=0.0,
                        high=3.0,
                    )
                )
                stability_intent_strength = float(
                    self._clamp(
                        max(stability_intent_strength, historical_stability_intent_strength * 0.85),
                        low=0.0,
                        high=3.0,
                    )
                )
                stability_intent_persistence = 0.0
                if stability_intent_sign != 0 and stability_intent_sign == prev_stability_intent_sign:
                    stability_intent_persistence = float(
                        self._clamp(
                            (float(prev_stability_intent_streak) / 4.0)
                            * (1.0 + min(0.5, prev_stability_response_gap * 2.5)),
                            low=0.0,
                            high=1.0,
                        )
                    )
                effective_history_anchor_stability = history_anchor_weight * (
                    1.0 - min(0.75, stability_intent_strength * 0.25)
                )
                if prev_stability_anchor is not None and effective_history_anchor_stability > 0.0:
                    anchored_stability = (
                        (smoothed_stability * (1.0 - effective_history_anchor_stability))
                        + (prev_stability_anchor * effective_history_anchor_stability)
                    )
                    if abs(anchored_stability - smoothed_stability) > 1e-9:
                        continuity_smoothed = True
                    smoothed_stability = float(anchored_stability)
                effective_stability_alpha = float(
                    self._clamp(
                        smooth_alpha_stability
                        + (stability_volatility_factor * 0.2)
                        + (flow_inertia * 0.12)
                        + (stability_oscillation_factor * 0.1),
                        low=0.0,
                        high=0.94,
                    )
                )
                effective_stability_alpha = float(
                    self._clamp(
                        effective_stability_alpha - min(0.22, stability_intent_strength * 0.07),
                        low=0.0,
                        high=0.94,
                    )
                )
                if stability_intent_persistence > 0.0:
                    effective_stability_alpha = float(
                        self._clamp(
                            effective_stability_alpha
                            - min(
                                0.1,
                                (stability_intent_persistence * 0.07) + (prev_stability_response_gap * 0.38),
                            ),
                            low=0.0,
                            high=0.94,
                        )
                    )
                if effective_stability_alpha > 0.0:
                    filtered_stability = (
                        (prev_stability * effective_stability_alpha)
                        + (smoothed_stability * (1.0 - effective_stability_alpha))
                    )
                    if abs(filtered_stability - smoothed_stability) > 1e-9:
                        continuity_smoothed = True
                    smoothed_stability = float(filtered_stability)
                stability_up_step = max_stability_step * stability_upward_step_ratio
                stability_down_step = max_stability_step * (2.0 - stability_upward_step_ratio)
                stability_volatility_tighten = (
                    (1.0 - (stability_volatility_factor * 0.45))
                    * (1.0 - (stability_oscillation_factor * 0.45))
                )
                stability_up_step = max(0.01, stability_up_step * stability_volatility_tighten)
                stability_down_step = max(0.01, stability_down_step * stability_volatility_tighten)
                smoothed_stability = float(
                    self._clamp(
                        smoothed_stability,
                        low=(prev_stability - stability_down_step),
                        high=(prev_stability + stability_up_step),
                    )
                )
                if abs(smoothed_stability - original_stability) > 1e-9:
                    continuity_smoothed = True
                stability_target_delta = smoothed_stability - prev_stability
                release_stability_ratio = float(
                    self._clamp(
                        flow_release_stability_ratio
                        * (1.0 - (flow_inertia * 0.28))
                        * (1.0 - (stability_oscillation_factor * 0.38)),
                        low=0.12,
                        high=1.0,
                    )
                )
                release_stability_ratio = float(
                    self._clamp(
                        release_stability_ratio * (1.0 + min(0.4, stability_intent_strength * 0.16)),
                        low=0.12,
                        high=1.0,
                    )
                )
                if stability_intent_persistence > 0.0:
                    release_stability_ratio = float(
                        self._clamp(
                            release_stability_ratio
                            + min(
                                0.2,
                                (stability_intent_persistence * 0.15) + (prev_stability_response_gap * 0.24),
                            ),
                            low=0.12,
                            high=1.0,
                        )
                    )
                stability_plateau_factor = float(
                    self._clamp(float(prev_stability_plateau_streak) / 4.0, low=0.0, high=1.0)
                )
                if stability_intent_persistence > 0.0:
                    stability_plateau_factor = float(
                        self._clamp(
                            stability_plateau_factor + min(0.3, stability_intent_persistence * 0.22),
                            low=0.0,
                            high=1.0,
                        )
                    )
                if stability_plateau_factor > 0.0 and stability_intent_strength > 0.0:
                    release_stability_ratio = float(
                        self._clamp(
                            release_stability_ratio
                            + (
                                flow_plateau_release_stability
                                * stability_plateau_factor
                                * min(1.0, stability_intent_strength / 1.4)
                            ),
                            low=0.12,
                            high=1.0,
                        )
                    )
                if abs(stability_target_delta) > 1e-6 and abs(prev_stability_trend) > 1e-6:
                    if (stability_target_delta > 0.0 and prev_stability_trend < 0.0) or (
                        stability_target_delta < 0.0 and prev_stability_trend > 0.0
                    ):
                        release_stability_ratio = float(
                            self._clamp(
                                release_stability_ratio * (1.0 - min(0.5, abs(prev_stability_trend) * 8.0)),
                                low=0.12,
                                high=1.0,
                            )
                        )
                    else:
                        release_stability_ratio = float(
                            self._clamp(
                                release_stability_ratio + min(0.1, abs(prev_stability_trend) * 2.2),
                                low=0.12,
                                high=1.0,
                            )
                        )
                released_stability = prev_stability + (stability_target_delta * release_stability_ratio)
                if abs(released_stability - smoothed_stability) > 1e-9:
                    continuity_smoothed = True
                smoothed_stability = float(released_stability)
                follow_through_stability = float(
                    self._clamp(
                        flow_follow_through
                        * (1.0 - (stability_oscillation_factor * 0.45))
                        * (1.0 + min(0.4, stability_intent_strength * 0.16)),
                        low=0.0,
                        high=1.0,
                    )
                )
                if (
                    follow_through_stability > 0.0
                    and abs(stability_target_delta) > 1e-6
                    and prev_stability_direction_sign != 0
                    and prev_stability_direction_streak >= 1
                ):
                    target_sign = 1 if stability_target_delta > 0.0 else -1
                    if target_sign == prev_stability_direction_sign:
                        current_stability_delta = smoothed_stability - prev_stability
                        min_follow_stability_delta = min(
                            abs(stability_target_delta),
                            max_stability_step
                            * min(
                                0.9,
                                follow_through_stability
                                * (0.4 + min(0.3, prev_stability_direction_streak * 0.05)),
                            ),
                        )
                        if abs(current_stability_delta) + 1e-9 < min_follow_stability_delta:
                            proposed_stability = prev_stability + (target_sign * min_follow_stability_delta)
                            if target_sign > 0:
                                proposed_stability = min(
                                    proposed_stability,
                                    prev_stability + abs(stability_target_delta),
                                )
                            else:
                                proposed_stability = max(
                                    proposed_stability,
                                    prev_stability - abs(stability_target_delta),
                                )
                            if abs(proposed_stability - smoothed_stability) > 1e-9:
                                continuity_smoothed = True
                            smoothed_stability = float(proposed_stability)
                effective_deadband_stability = float(
                    self._clamp(
                        jitter_deadband_stability
                        * (1.0 - min(0.8, stability_intent_strength * 0.5))
                        * (1.0 + (stability_oscillation_factor * 0.2)),
                        low=0.0,
                        high=0.05,
                    )
                )
                if stability_plateau_factor > 0.0 and stability_intent_strength > 0.0:
                    effective_deadband_stability = float(
                        self._clamp(
                            effective_deadband_stability
                            * (
                                1.0
                                - (
                                    stability_plateau_factor
                                    * flow_plateau_release_stability
                                    * min(0.85, stability_intent_strength * 0.45)
                                )
                            ),
                            low=0.0,
                            high=0.05,
                        )
                    )
                if stability_intent_persistence > 0.0:
                    effective_deadband_stability = float(
                        self._clamp(
                            effective_deadband_stability
                            * (
                                1.0
                                - min(
                                    0.5,
                                    (stability_intent_persistence * 0.35)
                                    + (prev_stability_response_gap * 0.26),
                                )
                            ),
                            low=0.0,
                            high=0.05,
                        )
                    )
                elif stability_intent_strength > 0.45:
                    effective_deadband_stability = float(
                        self._clamp(
                            effective_deadband_stability
                            * (1.0 - min(0.32, stability_intent_strength * 0.2)),
                            low=0.0,
                            high=0.05,
                        )
                    )
                if abs(smoothed_stability - prev_stability) <= effective_deadband_stability:
                    if (
                        stability_intent_sign != 0
                        and stability_intent_strength > 0.5
                        and (prev_stability_plateau_streak > 0 or stability_intent_persistence > 0.0)
                    ):
                        min_stability_push = min(
                            abs(stability_intent_delta),
                            max(0.0065, effective_deadband_stability * 0.88),
                        )
                        proposed_stability = prev_stability + (
                            float(stability_intent_sign) * min_stability_push
                        )
                        if abs(proposed_stability - smoothed_stability) > 1e-9:
                            continuity_smoothed = True
                        smoothed_stability = float(proposed_stability)
                    else:
                        if abs(smoothed_stability - prev_stability) > 1e-9:
                            continuity_smoothed = True
                        smoothed_stability = float(prev_stability)
                directive["stability"] = round(self._clamp(smoothed_stability, low=0.35, high=0.98), 3)

            prev_latency_tier = str(context.get("voice_prev_latency_tier") or "").strip().lower()
            allow_latency_drop = _truthy(context.get("voice_tuning_allow_latency_drop_to_low"))
            current_latency_tier = str(directive.get("latency_tier") or latency_tier).strip().lower() or "balanced"
            if (
                prev_latency_tier in {"balanced", "quality"}
                and current_latency_tier == "low"
                and not allow_latency_drop
                and not bool(payload.high_stakes)
            ):
                directive["latency_tier"] = "balanced"
                continuity_smoothed = True

        directive["continuity_target_speed"] = round(self._clamp(pre_continuity_speed, low=0.82, high=1.18), 3)
        directive["continuity_target_stability"] = round(
            self._clamp(pre_continuity_stability, low=0.35, high=0.98),
            3,
        )
        directive["continuity_smoothed"] = bool(continuity_smoothed)
        directive["continuity_prev_speed"] = context.get("voice_prev_speed")
        directive["continuity_prev_stability"] = context.get("voice_prev_stability")
        directive["continuity_prev_speed_anchor"] = context.get("voice_prev_speed_anchor")
        directive["continuity_prev_stability_anchor"] = context.get("voice_prev_stability_anchor")
        directive["continuity_prev_speed_trend"] = context.get("voice_prev_speed_trend")
        directive["continuity_prev_stability_trend"] = context.get("voice_prev_stability_trend")
        directive["continuity_prev_speed_oscillation_rate"] = context.get(
            "voice_prev_speed_oscillation_rate"
        )
        directive["continuity_prev_stability_oscillation_rate"] = context.get(
            "voice_prev_stability_oscillation_rate"
        )
        directive["continuity_prev_speed_direction_sign"] = context.get(
            "voice_prev_speed_direction_sign"
        )
        directive["continuity_prev_speed_direction_streak"] = context.get(
            "voice_prev_speed_direction_streak"
        )
        directive["continuity_prev_stability_direction_sign"] = context.get(
            "voice_prev_stability_direction_sign"
        )
        directive["continuity_prev_stability_direction_streak"] = context.get(
            "voice_prev_stability_direction_streak"
        )
        directive["continuity_prev_speed_plateau_streak"] = context.get(
            "voice_prev_speed_plateau_streak"
        )
        directive["continuity_prev_stability_plateau_streak"] = context.get(
            "voice_prev_stability_plateau_streak"
        )
        directive["continuity_prev_speed_intent_sign"] = context.get("voice_prev_speed_intent_sign")
        directive["continuity_prev_speed_intent_streak"] = context.get(
            "voice_prev_speed_intent_streak"
        )
        directive["continuity_prev_stability_intent_sign"] = context.get(
            "voice_prev_stability_intent_sign"
        )
        directive["continuity_prev_stability_intent_streak"] = context.get(
            "voice_prev_stability_intent_streak"
        )
        directive["continuity_prev_speed_response_gap"] = context.get("voice_prev_speed_response_gap")
        directive["continuity_prev_stability_response_gap"] = context.get(
            "voice_prev_stability_response_gap"
        )
        directive["continuity_flow_inertia"] = context.get("voice_tuning_flow_inertia")
        directive["continuity_flow_oscillation_guard"] = context.get("voice_tuning_flow_oscillation_guard")
        directive["continuity_flow_follow_through"] = context.get("voice_tuning_flow_follow_through")
        directive["continuity_flow_plateau_release_speed"] = context.get(
            "voice_tuning_flow_plateau_release_speed"
        )
        directive["continuity_flow_plateau_release_stability"] = context.get(
            "voice_tuning_flow_plateau_release_stability"
        )
        directive["continuity_prev_latency_tier"] = context.get("voice_prev_latency_tier")

        targets = latency_ladder.get("targets_ms") if isinstance(latency_ladder.get("targets_ms"), dict) else {}
        if targets:
            directive["timing_targets_ms"] = {
                "phase_a_presence": targets.get("phase_a_presence"),
                "phase_b_first_useful": targets.get("phase_b_first_useful"),
                "phase_c_deep_followup": targets.get("phase_c_deep_followup"),
            }
        return directive

    def prepare_reply(self, draft: ReplyDraft | dict[str, Any]) -> dict[str, Any]:
        payload = self._coerce_draft(draft)
        inferred = self._infer_policy_signals(payload)
        payload = replace(
            payload,
            explicit_directive=bool(inferred.get("explicit_directive")),
            disputed=bool(inferred.get("disputed")),
            high_stakes=bool(inferred.get("high_stakes")),
            requires_pushback=bool(inferred.get("requires_pushback")),
            pushback_severity=str(inferred.get("pushback_severity") or payload.pushback_severity or "medium"),
        )
        session = self._load_surface_session(payload)
        continuity = self._continuity_envelope(payload, session)
        binding = self._surface_binding(payload, session)
        pondering_mode = self._pondering_mode()
        adaptive = self._adaptive_policy()
        pushback_policy = adaptive.get("pushback") if isinstance(adaptive.get("pushback"), dict) else {}
        mode_context = {
            "source": "openclaw_reply_orchestrator",
            "surface_id": continuity.get("surface_id"),
            "session_id": continuity.get("session_id"),
            "session_key": continuity.get("session_key"),
            "continuity_ok": continuity.get("continuity_ok"),
            "modality": binding.get("modality"),
            "surface_channel": binding.get("channel"),
            "interrupt_on_speech": binding.get("interrupt_on_speech"),
            "inference_reasons": list(inferred.get("reasons") or []),
            **dict(payload.context or {}),
        }
        mode = self.runtime.decide_relationship_mode(
            explicit_directive=bool(payload.explicit_directive),
            disputed=bool(payload.disputed),
            high_stakes=bool(payload.high_stakes),
            uncertainty=float(payload.uncertainty),
            force_mode=str(payload.force_mode or "").strip() or None,
            context=mode_context,
        )

        mode_name = str(mode.get("mode") or "").strip() or "equal"
        latency_ladder = self._latency_ladder(
            payload=payload,
            mode=mode,
            continuity=continuity,
            binding=binding,
        )
        ladder_targets = (
            latency_ladder.get("targets_ms")
            if isinstance(latency_ladder.get("targets_ms"), dict)
            else {}
        )
        phase_a = (
            latency_ladder.get("phase_a_presence")
            if isinstance(latency_ladder.get("phase_a_presence"), dict)
            else {}
        )
        presence_ack = {
            "text": str(phase_a.get("text") or "On it.").strip() or "On it.",
            "target_ms": (
                int(phase_a_target)
                if isinstance((phase_a_target := ladder_targets.get("phase_a_presence")), (int, float))
                else None
            ),
            "deferred": False,
            "defer_reason": None,
        }
        lines: list[str] = []
        notice = str(payload.hypothesis_notice or "").strip()
        if notice:
            lines.append(f"Working hypothesis: {notice}")
        if not bool(continuity.get("continuity_ok")):
            lines.append("I may be missing a piece of prior context, so I am reattaching as I answer.")
        if bool(payload.requires_time_protection):
            tradeoff = str(payload.time_tradeoff or "").strip() or "This path can consume focus budget; confirm priority."
            lines.append(f"Time tradeoff: {tradeoff}")
        reply_diagnostics: dict[str, Any] = {}
        generation_started_at = time.perf_counter()
        try:
            generated_body = self.runtime.generate_presence_reply_body(
                user_text=str(payload.text or ""),
                mode=mode_name,
                modality=str(binding.get("modality") or "text"),
                continuity_ok=bool(continuity.get("continuity_ok", True)),
                high_stakes=bool(payload.high_stakes),
                uncertainty=float(payload.uncertainty),
                context=mode_context,
                telemetry_out=reply_diagnostics,
            )
        except Exception as exc:  # noqa: BLE001 - keep continuity stable across generation faults.
            generated_body = "I am with you. Reply generation exceeded budget; continuing with the next best step."
            presence_ack["deferred"] = True
            presence_ack["defer_reason"] = "generation_budget_exceeded"
            exc_name = type(exc).__name__
            exc_text = str(exc).strip()
            exc_reason = f"generation_exception:{exc_name}"
            if exc_text:
                exc_reason = f"{exc_reason}:{exc_text[:140]}"
            reply_diagnostics = {
                "model_used": False,
                "model_name": str(getattr(self.runtime.cognition_backend, "model", "") or "") or None,
                "fallback_used": True,
                "fallback_reason": exc_reason,
                "route_reason": "generation_exception",
                "retrieval_selected_count": 0,
                "rerank_used": False,
                "model_query_attempted": False,
                "model_query_succeeded": False,
                "model_failure_reason": exc_reason,
                "continuity_ok": bool(continuity.get("continuity_ok", True)),
                "high_risk_guardrail": False,
                "exception_trace": traceback.format_exc(limit=8),
            }
        elapsed_ms = round(max(0.0, (time.perf_counter() - generation_started_at) * 1000.0), 3)
        if not isinstance(reply_diagnostics, dict):
            reply_diagnostics = {}
        reply_diagnostics["latency_ms"] = elapsed_ms
        lines.append(str(generated_body or "").strip() or "I am here. Give me the next objective.")
        self_inquiry = self._self_inquiry_prompt(
            payload=payload,
            mode_name=mode_name,
            continuity=continuity,
            binding=binding,
        )
        if isinstance(self_inquiry, dict) and self_inquiry.get("asked"):
            question = str(self_inquiry.get("question") or "").strip()
            if question:
                lines.append(f"Question for you: {question}")
        rendered = "\n\n".join(lines).strip()

        pushback_record = None
        policy_auto_pushback = bool(pushback_policy.get("auto_pushback_on_high_stakes_disputed", True))
        pushback_required = bool(payload.requires_pushback) or (
            policy_auto_pushback and bool(payload.high_stakes) and bool(payload.disputed)
        )
        if pushback_required:
            severity = str(payload.pushback_severity or "medium").strip().lower() or "medium"
            severity_bias = float(pushback_policy.get("severity_bias") or 0.0)
            if severity in {"low", "medium", "high"}:
                rank = {"low": 0, "medium": 1, "high": 2}
                inv = {0: "low", 1: "medium", 2: "high"}
                delta = 1 if severity_bias >= 0.22 else (-1 if severity_bias <= -0.22 else 0)
                severity = inv[max(0, min(2, rank.get(severity, 1) + delta))]
            pushback_record = self.runtime.record_pushback(
                domain=str(payload.domain or "general"),
                recommendation=rendered,
                severity=severity,
                rationale={
                    "mode": mode.get("mode"),
                    "source": "reply_orchestrator",
                    "continuity_ok": continuity.get("continuity_ok"),
                },
            )

        tone_balance = self._tone_balance(
            payload=payload,
            mode=mode,
            binding=binding,
            pushback_required=pushback_required,
        )
        voice_directive = self._voice_directive(
            payload=payload,
            mode=mode,
            continuity=continuity,
            binding=binding,
            latency_ladder=latency_ladder,
            tone_balance=tone_balance,
        )

        return {
            "mode": mode,
            "reply_text": rendered,
            "reply_diagnostics": reply_diagnostics,
            "inferred_signals": inferred,
            "pushback_record": pushback_record,
            "continuity": continuity,
            "presence_ack": presence_ack,
            "latency_ladder": latency_ladder,
            "voice": {
                "modality": binding.get("modality"),
                "surface_bound": bool(binding.get("voice_surface_bound")),
                "interrupt_on_speech": bool(binding.get("interrupt_on_speech")),
                "wake_scope": ("gateway_global" if bool(binding.get("voice_surface_bound")) else "surface_local"),
                "directive": voice_directive,
            },
            "voice_directive": voice_directive,
            "tone_balance": tone_balance,
            "hypothesis": {
                "surfaced": bool(notice),
                "notice": notice or None,
            },
            "time_protection_notice": (
                str(payload.time_tradeoff or "").strip()
                if bool(payload.requires_time_protection)
                else None
            ),
            "self_inquiry": self_inquiry or {"asked": False},
            "pondering_mode": pondering_mode,
        }
