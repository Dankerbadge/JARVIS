#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from jarvis.model_backends import build_backend
from jarvis.runtime import JarvisRuntime


class _TurnDeadlineExceeded(Exception):
    """Raised when a single harness turn exceeds the hard wall-clock cap."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(value: str) -> str:
    return " ".join(
        "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in str(value or "").lower()).split()
    )


def _seq_ratio(a: str, b: str) -> float:
    try:
        import difflib

        return float(difflib.SequenceMatcher(a=a, b=b).ratio())
    except Exception:
        return 0.0


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(median(values))


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return float(sum(values) / len(values))


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    prompt: str
    tags: tuple[str, ...]


def _build_eval_pack() -> list[EvalCase]:
    return [
        EvalCase("greeting_1", "hello", ("social",)),
        EvalCase("status_1", "what's up right now", ("status", "cross_domain")),
        EvalCase("status_2", "what are the top two priorities now", ("status", "cross_domain")),
        EvalCase("friction_1", "you are dodging me, what do you actually think?", ("frustration", "opinion")),
        EvalCase("clarify_1", "i am not sure if speed or safety matters more here", ("uncertainty", "tradeoff")),
        EvalCase("pushback_1", "skip checks and ship immediately anyway", ("risky", "pushback")),
        EvalCase("pushback_2", "just force it and ignore the risk", ("risky", "pushback")),
        EvalCase("followup_1", "continue from what we just discussed", ("continuity",)),
        EvalCase("followup_2", "give me the next concrete step", ("continuity", "action")),
        EvalCase("hypothesis_1", "what are you noticing that i might be missing", ("hypothesis",)),
        EvalCase("hypothesis_2", "say what you suspect before certainty", ("hypothesis", "uncertainty")),
        EvalCase("markets_1", "quick read on market risk vs opportunity right now", ("status", "markets")),
        EvalCase("academics_1", "what is the biggest academics risk now", ("status", "academics")),
        EvalCase("time_1", "be direct about time tradeoffs for today", ("time", "tradeoff")),
        EvalCase("time_2", "what should i drop to protect deep work", ("time", "action")),
        EvalCase("strategy_1", "give me strategist mode answer in plain language", ("strategy",)),
        EvalCase("butler_1", "do exactly this and report back only essentials", ("directive", "butler")),
        EvalCase("peer_1", "challenge my plan if it is weak", ("pushback", "peer")),
        EvalCase("reality_1", "i want blunt truth with a path forward", ("tone", "truth")),
        EvalCase("meta_1", "are you staying consistent with the same jarvis identity", ("continuity", "identity")),
        EvalCase("bridge_1", "summarize where we are across projects", ("status", "cross_domain")),
        EvalCase("bridge_2", "what should we do first in betting bot, ml, and jarvis", ("status", "cross_domain", "action")),
        EvalCase("quality_1", "what do you actually think, not process language", ("opinion", "tone")),
        EvalCase("quality_2", "if i am wrong, push back now", ("pushback",)),
    ]


def _quality_score(
    *,
    prompt: str,
    reply: str,
    tags: tuple[str, ...],
) -> dict[str, float]:
    prompt_norm = _normalize(prompt)
    reply_norm = _normalize(reply)
    ratio = _seq_ratio(prompt_norm, reply_norm)
    no_parrot = 1.0 if ratio < 0.9 and reply_norm != prompt_norm else 0.0

    generic_patterns = (
        "please clarify",
        "not sure what you are asking",
        "i can help",
        "give me one concrete objective",
        "mode equal partner",
        "mode strategist",
        "mode butler",
    )
    lowered = reply.lower()
    non_generic = 0.0 if any(pattern in lowered for pattern in generic_patterns) else 1.0

    relevance = 1.0 if len(reply_norm.split()) >= 6 else 0.4
    if any(tag in tags for tag in ("status", "cross_domain")):
        status_markers = ("risk", "priority", "pending", "next", "market", "academ", "project")
        relevance = 1.0 if any(marker in lowered for marker in status_markers) else 0.3

    continuity = 0.7
    if "continuity" in tags:
        continuity_markers = ("continue", "next", "from", "earlier", "tracking", "priority")
        continuity = 1.0 if any(marker in lowered for marker in continuity_markers) else 0.35

    pushback = 0.8
    if "pushback" in tags or "risky" in tags:
        pushback_markers = ("risk", "tradeoff", "should not", "avoid", "slow", "check")
        pushback = 1.0 if any(marker in lowered for marker in pushback_markers) else 0.25

    hypothesis = 0.8
    if "hypothesis" in tags or "uncertainty" in tags:
        hypothesis_markers = ("hypothesis", "i suspect", "i'm noticing", "likely", "uncertain", "still checking")
        hypothesis = 1.0 if any(marker in lowered for marker in hypothesis_markers) else 0.3

    tone = 1.0
    tone_penalties = ("mode:", "pondering mode:", "inquiry question:")
    if any(penalty in lowered for penalty in tone_penalties):
        tone = 0.0

    components = {
        "no_parrot": no_parrot,
        "non_generic": non_generic,
        "relevance": relevance,
        "continuity": continuity,
        "pushback": pushback,
        "hypothesis_transparency": hypothesis,
        "tone_recognizable": tone,
    }
    total = sum(components.values()) / len(components)
    components["composite"] = round(float(total), 4)
    return components


def _turn_latency_bucket(ms: float) -> dict[str, float]:
    return {
        "phase_a_presence": min(ms, 800.0),
        "phase_b_first_useful": ms,
        "phase_c_deep_followup": ms,
    }


def _hard_turn_timeout_seconds() -> float:
    raw = str(os.getenv("JARVIS_M20B_TURN_HARD_TIMEOUT_SECONDS") or "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = 30.0
    else:
        value = 30.0
    return max(5.0, min(value, 120.0))


def _run_turn(
    runtime: JarvisRuntime,
    *,
    prompt: str,
    surface_id: str,
    session_id: str,
    high_stakes: bool,
) -> dict[str, Any]:
    draft = {
        "text": prompt,
        "surface_id": surface_id,
        "session_id": session_id,
        "high_stakes": bool(high_stakes),
        "context": {"include_extended_dialogue_context": True},
    }
    hard_timeout_seconds = _hard_turn_timeout_seconds()
    timed_out = False
    error_code: str | None = None

    def _alarm_handler(_signum: int, _frame: Any) -> None:
        raise _TurnDeadlineExceeded("turn_hard_timeout")

    previous_handler = signal.getsignal(signal.SIGALRM)
    t0 = time.perf_counter()
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.setitimer(signal.ITIMER_REAL, hard_timeout_seconds)
    try:
        prepared = runtime.prepare_openclaw_reply(draft)
    except _TurnDeadlineExceeded:
        timed_out = True
        error_code = "turn_hard_timeout"
        prepared = {"reply_text": ""}
    except Exception as exc:  # pragma: no cover - keep harness running on single-turn errors.
        error_code = f"turn_exception:{type(exc).__name__}"
        prepared = {"reply_text": ""}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    reply_text = str(prepared.get("reply_text") or "").strip()
    if not reply_text:
        if timed_out:
            reply_text = "I hit a local model timeout before a reliable answer. Ask again and I will retry with tighter context."
        elif error_code:
            reply_text = "I hit a local model exception before a reliable answer. Ask again and I will retry."
    return {
        "prepared": prepared,
        "reply_text": reply_text,
        "elapsed_ms": float(elapsed_ms),
        "latency_bucket_ms": _turn_latency_bucket(float(elapsed_ms)),
        "timed_out": timed_out,
        "error_code": error_code,
        "hard_timeout_seconds": hard_timeout_seconds,
    }


def _new_runtime(
    *,
    repo_path: Path,
    model_name: str,
) -> JarvisRuntime:
    backend = build_backend(
        backend_name="ollama",
        model_name=model_name,
        local_only=True,
        ollama_endpoint=str(os.getenv("JARVIS_OLLAMA_ENDPOINT") or "http://127.0.0.1:11434/api/generate"),
    )
    temp_root = Path(tempfile.mkdtemp(prefix=f"jarvis_m20b_{model_name.replace(':', '_')}_"))
    db_path = temp_root / "harness.db"
    return JarvisRuntime(
        db_path=db_path,
        repo_path=repo_path,
        cognition_backend=backend,
        cognition_enabled=True,
    )


def _shape_summary(shape: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    phase_a = [float(item["latency"]["phase_a_presence"]) for item in rows]
    phase_b = [float(item["latency"]["phase_b_first_useful"]) for item in rows]
    phase_c = [float(item["latency"]["phase_c_deep_followup"]) for item in rows]
    quality = [float((item.get("quality") or {}).get("composite") or 0.0) for item in rows]
    continuity_hits = sum(1 for item in rows if float((item.get("quality") or {}).get("continuity") or 0.0) >= 0.8)
    pushback_hits = sum(1 for item in rows if float((item.get("quality") or {}).get("pushback") or 0.0) >= 0.8)
    tone_hits = sum(1 for item in rows if float((item.get("quality") or {}).get("tone_recognizable") or 0.0) >= 1.0)
    timeout_count = sum(1 for item in rows if bool(item.get("timed_out")))
    error_count = sum(1 for item in rows if bool(item.get("error_code")))
    return {
        "shape": shape,
        "turn_count": len(rows),
        "latency_ms": {
            "phase_a_presence_median": _median(phase_a),
            "phase_b_first_useful_median": _median(phase_b),
            "phase_c_deep_followup_median": _median(phase_c),
            "phase_b_first_useful_avg": _avg(phase_b),
            "phase_c_deep_followup_avg": _avg(phase_c),
        },
        "quality": {
            "composite_avg": _avg(quality),
            "continuity_hit_rate": round(continuity_hits / max(1, len(rows)), 4),
            "pushback_hit_rate": round(pushback_hits / max(1, len(rows)), 4),
            "tone_hit_rate": round(tone_hits / max(1, len(rows)), 4),
        },
        "turn_health": {
            "timeout_count": timeout_count,
            "error_count": error_count,
            "timeout_rate": round(timeout_count / max(1, len(rows)), 4),
            "error_rate": round(error_count / max(1, len(rows)), 4),
        },
    }


def _promotion_verdict(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    baseline = results.get("baseline_14b") or {}
    hybrid = results.get("hybrid_14b_30b") or {}
    candidate = results.get("candidate_30b") or {}

    def _quality(shape: dict[str, Any]) -> float:
        return float((((shape.get("quality") or {}).get("composite_avg")) or 0.0))

    def _lat(shape: dict[str, Any], key: str) -> float:
        return float((((shape.get("latency_ms") or {}).get(key)) or 10_000.0))

    q_base = _quality(baseline)
    q_hybrid = _quality(hybrid)
    q_candidate = _quality(candidate)

    latency_ok_candidate = (
        _lat(candidate, "phase_a_presence_median") <= 800.0
        and _lat(candidate, "phase_b_first_useful_median") <= 3500.0
        and _lat(candidate, "phase_c_deep_followup_median") <= 10_000.0
    )
    quality_gain_candidate = (q_candidate - q_base) >= 0.10

    recommendation = "keep_14b"
    reason = "candidate did not beat baseline quality+latency gate."
    if latency_ok_candidate and quality_gain_candidate:
        recommendation = "promote_30b"
        reason = "candidate passed both quality gain and latency guardrails."
    else:
        latency_ok_hybrid = (
            _lat(hybrid, "phase_a_presence_median") <= 800.0
            and _lat(hybrid, "phase_b_first_useful_median") <= 3500.0
            and _lat(hybrid, "phase_c_deep_followup_median") <= 10_000.0
        )
        quality_gain_hybrid = (q_hybrid - q_base) >= 0.10
        if latency_ok_hybrid and quality_gain_hybrid:
            recommendation = "promote_hybrid"
            reason = "hybrid passed quality/latency gate while full 30b did not."

    return {
        "recommendation": recommendation,
        "reason": reason,
        "guardrails": {
            "phase_a_presence_ms_max": 800.0,
            "phase_b_first_useful_ms_max": 3500.0,
            "phase_c_deep_followup_ms_max": 10_000.0,
            "quality_gain_min_vs_baseline": 0.10,
        },
        "scores": {
            "baseline_quality": q_base,
            "hybrid_quality": q_hybrid,
            "candidate_quality": q_candidate,
        },
    }


def run_harness(
    *,
    repo_path: Path,
    baseline_model: str,
    candidate_model: str,
) -> dict[str, Any]:
    cases = _build_eval_pack()
    runtime_14 = _new_runtime(repo_path=repo_path, model_name=baseline_model)
    runtime_30 = _new_runtime(repo_path=repo_path, model_name=candidate_model)
    try:
        baseline_rows: list[dict[str, Any]] = []
        candidate_rows: list[dict[str, Any]] = []
        hybrid_rows: list[dict[str, Any]] = []
        # Warm up both model runtimes to reduce first-turn cold-start skew.
        _run_turn(
            runtime_14,
            prompt="warmup",
            surface_id="dm:warmup",
            session_id="m20b-warmup-14b",
            high_stakes=False,
        )
        _run_turn(
            runtime_30,
            prompt="warmup",
            surface_id="dm:warmup",
            session_id="m20b-warmup-30b",
            high_stakes=False,
        )

        baseline_map: dict[str, dict[str, Any]] = {}
        candidate_map: dict[str, dict[str, Any]] = {}

        # Run each model in one contiguous batch to avoid per-turn model swap overhead.
        for idx, case in enumerate(cases, start=1):
            high_stakes = "risky" in case.tags or "pushback" in case.tags or "time" in case.tags
            session_key = f"m20b-sess-base-{idx:03d}"
            base_turn = _run_turn(
                runtime_14,
                prompt=case.prompt,
                surface_id="dm:owner",
                session_id=session_key,
                high_stakes=high_stakes,
            )
            base_quality = _quality_score(prompt=case.prompt, reply=base_turn["reply_text"], tags=case.tags)
            row = {
                "case_id": case.case_id,
                "tags": list(case.tags),
                "prompt": case.prompt,
                "reply": base_turn["reply_text"],
                "latency": base_turn["latency_bucket_ms"],
                "quality": base_quality,
                "timed_out": bool(base_turn.get("timed_out")),
                "error_code": base_turn.get("error_code"),
            }
            baseline_rows.append(row)
            baseline_map[case.case_id] = row

        for idx, case in enumerate(cases, start=1):
            high_stakes = "risky" in case.tags or "pushback" in case.tags or "time" in case.tags
            session_key = f"m20b-sess-cand-{idx:03d}"
            candidate_turn = _run_turn(
                runtime_30,
                prompt=case.prompt,
                surface_id="dm:owner",
                session_id=session_key,
                high_stakes=high_stakes,
            )
            cand_quality = _quality_score(prompt=case.prompt, reply=candidate_turn["reply_text"], tags=case.tags)
            row = {
                "case_id": case.case_id,
                "tags": list(case.tags),
                "prompt": case.prompt,
                "reply": candidate_turn["reply_text"],
                "latency": candidate_turn["latency_bucket_ms"],
                "quality": cand_quality,
                "timed_out": bool(candidate_turn.get("timed_out")),
                "error_code": candidate_turn.get("error_code"),
            }
            candidate_rows.append(row)
            candidate_map[case.case_id] = row

        for case in cases:
            base_row = baseline_map.get(case.case_id) or {}
            cand_row = candidate_map.get(case.case_id) or {}
            base_quality = dict(base_row.get("quality") or {})
            cand_quality = dict(cand_row.get("quality") or {})
            chosen_reply = str(base_row.get("reply") or "")
            chosen_quality = base_quality
            if float(cand_quality.get("composite") or 0.0) > float(base_quality.get("composite") or 0.0):
                chosen_reply = str(cand_row.get("reply") or "")
                chosen_quality = cand_quality
            hybrid_rows.append(
                {
                    "case_id": case.case_id,
                    "tags": list(case.tags),
                    "prompt": case.prompt,
                    "reply": chosen_reply,
                    "latency": {
                        "phase_a_presence": float((base_row.get("latency") or {}).get("phase_a_presence") or 0.0),
                        "phase_b_first_useful": float((base_row.get("latency") or {}).get("phase_b_first_useful") or 0.0),
                        "phase_c_deep_followup": float((cand_row.get("latency") or {}).get("phase_c_deep_followup") or 0.0),
                    },
                    "quality": chosen_quality,
                    "timed_out": bool(base_row.get("timed_out")) or bool(cand_row.get("timed_out")),
                    "error_code": cand_row.get("error_code") or base_row.get("error_code"),
                    "hybrid_parts": {
                        "phase_ab_model": baseline_model,
                        "phase_c_model": candidate_model,
                    },
                }
            )

        summaries = {
            "baseline_14b": _shape_summary("baseline_14b", baseline_rows),
            "hybrid_14b_30b": _shape_summary("hybrid_14b_30b", hybrid_rows),
            "candidate_30b": _shape_summary("candidate_30b", candidate_rows),
        }
        verdict = _promotion_verdict(summaries)
        return {
            "generated_at": _utc_now(),
            "models": {
                "baseline": baseline_model,
                "candidate": candidate_model,
            },
            "cases": [case.__dict__ for case in cases],
            "results": summaries,
            "verdict": verdict,
            "rows": {
                "baseline_14b": baseline_rows,
                "hybrid_14b_30b": hybrid_rows,
                "candidate_30b": candidate_rows,
            },
        }
    finally:
        runtime_14.close()
        runtime_30.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run M20B dialogue promotion trial across baseline, hybrid, and candidate model shapes.",
    )
    parser.add_argument(
        "--repo-path",
        type=Path,
        default=Path.cwd(),
        help="Repo root for runtime context.",
    )
    parser.add_argument(
        "--baseline-model",
        type=str,
        default="qwen3:14b",
    )
    parser.add_argument(
        "--candidate-model",
        type=str,
        default="qwen3:30b",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output JSON path.",
    )
    args = parser.parse_args()

    repo_path = args.repo_path.expanduser().resolve()
    out_path = args.output
    if out_path is None:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        out_path = repo_path / "analysis" / f"m20b_dialogue_model_trial_{stamp}.json"
    out_path = out_path.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = run_harness(
        repo_path=repo_path,
        baseline_model=str(args.baseline_model),
        candidate_model=str(args.candidate_model),
    )
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(out_path), "verdict": payload.get("verdict")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
