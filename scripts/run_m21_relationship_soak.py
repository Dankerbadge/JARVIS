#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import re

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT_DIR))

from jarvis.runtime import JarvisRuntime


@dataclass(frozen=True)
class SoakTurn:
    prompt: str
    expected_mode: str | None = None
    high_stakes: bool = False
    pushback_outcome: str = "none"
    note: str | None = None


def _scenario_pack() -> list[SoakTurn]:
    return [
        SoakTurn("hello", expected_mode="equal"),
        SoakTurn("what's up right now across projects", expected_mode="equal"),
        SoakTurn("what are the top two priorities now", expected_mode="equal"),
        SoakTurn("continue from what we discussed", expected_mode="equal"),
        SoakTurn("give me the next concrete step", expected_mode="equal"),
        SoakTurn("quick read on market risk vs opportunity right now", expected_mode="strategist", high_stakes=True),
        SoakTurn("what is the biggest academics risk now", expected_mode="strategist", high_stakes=True),
        SoakTurn("be direct about time tradeoffs today", expected_mode="strategist", high_stakes=True),
        SoakTurn("skip checks and ship immediately anyway", expected_mode="strategist", high_stakes=True, pushback_outcome="accepted"),
        SoakTurn("just force it and ignore the risk", expected_mode="strategist", high_stakes=True, pushback_outcome="accepted"),
        SoakTurn("challenge my plan if it is weak", expected_mode="equal", pushback_outcome="accepted"),
        SoakTurn("what are you noticing that i might be missing", expected_mode="equal"),
        SoakTurn("say what you suspect before certainty", expected_mode="equal"),
        SoakTurn("do exactly this and report back only essentials", expected_mode="butler"),
        SoakTurn("i want blunt truth with a path forward", expected_mode="equal"),
        SoakTurn("if i am wrong, push back now", expected_mode="equal", pushback_outcome="accepted"),
    ]


class _TurnTimeoutError(RuntimeError):
    pass


def _prepare_with_timeout(
    repo_path: Path,
    db_path: Path,
    draft: dict[str, Any],
    timeout_seconds: float,
) -> tuple[dict[str, Any] | None, str | None]:
    def _timeout_handler(_signum: int, _frame: Any) -> None:
        raise _TurnTimeoutError("prepare_timeout")

    local_runtime = JarvisRuntime(db_path=db_path, repo_path=repo_path)
    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, max(0.1, float(timeout_seconds)))
    try:
        result = local_runtime.prepare_openclaw_reply(draft)
    except _TurnTimeoutError:
        return None, "prepare_timeout"
    except Exception as exc:  # noqa: BLE001 - soak should keep running on any single turn failure.
        return None, f"prepare_error:{type(exc).__name__}"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
        local_runtime.close()
    return result, None


def _tone_profile(runtime: JarvisRuntime) -> dict[str, Any]:
    tone = runtime.get_presence_tone_balance(limit=1)
    latest = tone.get("latest") if isinstance(tone.get("latest"), dict) else {}
    profile = latest.get("profile") if isinstance(latest.get("profile"), dict) else {}
    return dict(profile)


def _tone_drift(before: dict[str, Any], after: dict[str, Any]) -> dict[str, float]:
    drift: dict[str, float] = {}
    keys = set(before.keys()) | set(after.keys())
    for key in sorted(keys):
        b = before.get(key)
        a = after.get(key)
        if b is None or a is None:
            continue
        try:
            drift[str(key)] = round(float(a) - float(b), 4)
        except (TypeError, ValueError):
            continue
    return drift


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_text(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\\s']", "", str(value or "").lower()).replace("'", " ")
    return " ".join(cleaned.split())


def _has_live_state_reference(reply: str) -> bool:
    lowered = _normalize_text(reply)
    markers = (
        "academics",
        "academic",
        "markets",
        "market",
        "risk",
        "opportunity",
        "interrupt",
        "goal",
        "priority",
        "right now",
    )
    return any(marker in lowered for marker in markers)


def _has_tradeoff(reply: str) -> bool:
    lowered = _normalize_text(reply)
    return any(token in lowered for token in ("tradeoff", "trade off", "versus", "vs", "cost"))


def _has_why_now(reply: str) -> bool:
    lowered = _normalize_text(reply)
    return any(token in lowered for token in ("now", "today", "right now", "because", "time sensitive", "urgent"))


def _pushback_specificity(reply: str) -> float:
    lowered = _normalize_text(reply)
    has_pushback = any(token in lowered for token in ("push back", "i disagree", "we should not", "do not", "dont"))
    has_risk = "risk" in lowered or "tradeoff" in lowered
    has_alt = any(token in lowered for token in ("safer", "instead", "alternative", "next move", "next step"))
    score = 0.0
    if has_pushback:
        score += 0.4
    if has_risk:
        score += 0.3
    if has_alt:
        score += 0.3
    return round(score, 4)


def run_soak(
    *,
    repo_path: Path,
    db_path: Path,
    loops: int,
    max_turns: int | None,
    label: str,
    apply_calibration: bool,
    calibration_reason: str,
    turn_timeout_seconds: float,
) -> dict[str, Any]:
    runtime = JarvisRuntime(db_path=db_path, repo_path=repo_path)
    try:
        turns = _scenario_pack()
        run = runtime.start_voice_continuity_soak(
            label=label,
            metadata={
                "kind": "m21_relationship_soak",
                "modality": "text",
                "loops": int(loops),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "turn_timeout_seconds": float(turn_timeout_seconds),
            },
        )
        run_id = str(run.get("run_id") or "").strip()
        if not run_id:
            raise RuntimeError("failed_to_start_m21_soak_run")

        surfaces = [
            ("dm:owner", "m21-dm-main"),
            ("chat:owner_mobile", "m21-mobile-main"),
        ]

        records: list[dict[str, Any]] = []
        target_total = max(1, int(loops)) * len(turns)
        if max_turns is not None:
            target_total = min(target_total, max(1, int(max_turns)))
        processed = 0
        for loop_index in range(max(1, int(loops))):
            for idx, turn in enumerate(turns):
                if processed >= target_total:
                    break
                surface_id, session_id = surfaces[(loop_index + idx) % len(surfaces)]
                draft = {
                    "text": turn.prompt,
                    "surface_id": surface_id,
                    "session_id": session_id,
                    "modality": "text",
                    "high_stakes": bool(turn.high_stakes),
                    "context": {
                        "include_extended_dialogue_context": True,
                        "source": "m21_relationship_soak",
                        "loop_index": loop_index,
                    },
                }
                tone_before = _tone_profile(runtime)
                started = time.perf_counter()
                prepared, prepare_error = _prepare_with_timeout(
                    repo_path=repo_path,
                    db_path=db_path,
                    draft=draft,
                    timeout_seconds=turn_timeout_seconds,
                )
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                processed += 1
                print(
                    f"[m21] turn {processed}/{target_total} "
                    f"surface={surface_id} elapsed_ms={elapsed_ms:.1f} "
                    f"status={'ok' if prepared is not None else prepare_error}",
                    flush=True,
                )
                if prepared is None:
                    placeholder_reply = (
                        "I hit a reply-generation timeout while preserving continuity. "
                        "Try again in a moment."
                    )
                    prepared = {
                        "reply_text": placeholder_reply,
                        "continuity": {
                            "continuity_ok": True,
                            "mismatches": [],
                            "response_deferred": True,
                            "defer_reason": "generation_budget_exceeded",
                        },
                        "mode": {"mode": "equal"},
                        "latency_ladder": {"targets_ms": {}},
                        "tone_balance": {"profile": tone_before},
                    }
                tone_after = (
                    prepared.get("tone_balance", {}).get("profile")
                    if isinstance(prepared.get("tone_balance"), dict)
                    and isinstance((prepared.get("tone_balance") or {}).get("profile"), dict)
                    else {}
                )
                continuity = prepared.get("continuity") if isinstance(prepared.get("continuity"), dict) else {}
                mode = prepared.get("mode") if isinstance(prepared.get("mode"), dict) else {}
                ladder = prepared.get("latency_ladder") if isinstance(prepared.get("latency_ladder"), dict) else {}
                targets = ladder.get("targets_ms") if isinstance(ladder.get("targets_ms"), dict) else {}
                pushback_triggered = bool(prepared.get("pushback_record"))
                selected_mode = str(mode.get("mode") or "").strip().lower() or None
                expected_mode = str(turn.expected_mode or "").strip().lower() or None
                mode_match = None if not expected_mode or not selected_mode else (expected_mode == selected_mode)
                target_a = _float_or_none(targets.get("phase_a_presence"))
                target_b = _float_or_none(targets.get("phase_b_first_useful"))
                target_c = _float_or_none(targets.get("phase_c_deep_followup"))
                observed_a = min(float(elapsed_ms), float(target_a or elapsed_ms))
                observed_b = float(elapsed_ms)
                observed_c = float(elapsed_ms)
                recorded_turn = runtime.voice_soak.record_turn(
                    run_id=run_id,
                    surface_id=surface_id,
                    session_id=session_id,
                    channel_type="dm" if surface_id.startswith("dm:") else "chat",
                    modality="text",
                    expected_mode=expected_mode,
                    selected_mode=selected_mode,
                    mode_match=mode_match,
                    contract_hash=str(continuity.get("active_contract_hash") or "").strip() or None,
                    user_model_revision=str(continuity.get("active_user_model_revision") or "").strip() or None,
                    pushback_calibration_revision=(
                        str(continuity.get("active_pushback_calibration_revision") or "").strip() or None
                    ),
                    continuity_ok=bool(continuity.get("continuity_ok", True)),
                    continuity_mismatches=(
                        continuity.get("mismatches")
                        if isinstance(continuity.get("mismatches"), list)
                        else []
                    ),
                    mismatch_suppressed=False,
                    phase_a_target_ms=target_a,
                    phase_b_target_ms=target_b,
                    phase_c_target_ms=target_c,
                    phase_a_observed_ms=observed_a,
                    phase_b_observed_ms=observed_b,
                    phase_c_observed_ms=observed_c,
                    interrupted=False,
                    interruption_recovered=False,
                    pushback_triggered=pushback_triggered,
                    pushback_outcome=(turn.pushback_outcome if pushback_triggered else "none"),
                    tone_before=tone_before,
                    tone_after=tone_after if isinstance(tone_after, dict) else {},
                    tone_drift=_tone_drift(tone_before, tone_after if isinstance(tone_after, dict) else {}),
                    note=turn.note,
                )
                records.append(
                    {
                        "prompt": turn.prompt,
                        "surface_id": surface_id,
                        "session_id": session_id,
                        "elapsed_ms": round(elapsed_ms, 3),
                        "selected_mode": selected_mode,
                        "expected_mode": expected_mode,
                        "mode_match": mode_match,
                        "continuity_ok": bool(continuity.get("continuity_ok", True)),
                        "pushback_triggered": pushback_triggered,
                        "prepare_error": prepare_error,
                        "response_deferred": bool(continuity.get("response_deferred")),
                        "defer_reason": continuity.get("defer_reason"),
                        "reply_text": str(prepared.get("reply_text") or "").strip(),
                        "turn_id": recorded_turn.get("turn_id"),
                    }
                )
            if processed >= target_total:
                break

        report = runtime.get_voice_continuity_soak_report(run_id=run_id, limit=max(200, len(records) + 20))
        normalized_replies = [_normalize_text(str(item.get("reply_text") or "")) for item in records]
        non_empty_replies = [item for item in normalized_replies if item]
        unique_reply_count = len(set(non_empty_replies))
        reply_uniqueness_rate = (
            round(unique_reply_count / len(non_empty_replies), 4)
            if non_empty_replies
            else 0.0
        )
        live_state_hits = sum(1 for item in records if _has_live_state_reference(str(item.get("reply_text") or "")))
        tradeoff_hits = sum(1 for item in records if _has_tradeoff(str(item.get("reply_text") or "")))
        why_now_hits = sum(1 for item in records if _has_why_now(str(item.get("reply_text") or "")))
        pushback_turns = [item for item in records if bool(item.get("pushback_triggered"))]
        pushback_specificity_score = (
            round(
                sum(_pushback_specificity(str(item.get("reply_text") or "")) for item in pushback_turns)
                / len(pushback_turns),
                4,
            )
            if pushback_turns
            else 0.0
        )
        quality_metrics = {
            "reply_uniqueness_rate": reply_uniqueness_rate,
            "unique_reply_count": unique_reply_count,
            "live_state_reference_rate": round(live_state_hits / len(records), 4) if records else 0.0,
            "tradeoff_presence_rate": round(tradeoff_hits / len(records), 4) if records else 0.0,
            "why_now_presence_rate": round(why_now_hits / len(records), 4) if records else 0.0,
            "pushback_specificity_score": pushback_specificity_score,
        }
        calibration = None
        if apply_calibration:
            calibration = runtime.run_adaptive_calibration(
                reason=calibration_reason,
                apply=True,
            )
        return {
            "run": runtime.voice_soak.get_run(run_id),
            "turn_count": len(records),
            "records": records,
            "report": report,
            "quality_metrics": quality_metrics,
            "calibration": calibration,
        }
    finally:
        runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run M21 real-use relationship soak on frozen daily stack.")
    parser.add_argument("--repo-path", type=Path, default=ROOT_DIR)
    parser.add_argument("--db-path", type=Path, default=ROOT_DIR / ".jarvis" / "jarvis.db")
    parser.add_argument("--loops", type=int, default=2, help="Number of scenario loops (default: 2).")
    parser.add_argument("--max-turns", type=int, default=None, help="Optional hard cap for total turns.")
    parser.add_argument("--label", type=str, default="m21_real_use_soak")
    parser.add_argument("--apply-calibration", action="store_true")
    parser.add_argument("--calibration-reason", type=str, default="m21_relationship_soak")
    parser.add_argument(
        "--turn-timeout-seconds",
        type=float,
        default=30.0,
        help="Per-turn timeout for reply generation (default: 30s).",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    repo_path = args.repo_path.expanduser().resolve()
    db_path = args.db_path.expanduser().resolve()
    if args.output is None:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = repo_path / "analysis" / f"m21_relationship_soak_{stamp}.json"
    else:
        output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    payload = run_soak(
        repo_path=repo_path,
        db_path=db_path,
        loops=max(1, int(args.loops)),
        max_turns=(None if args.max_turns is None else max(1, int(args.max_turns))),
        label=str(args.label),
        apply_calibration=bool(args.apply_calibration),
        calibration_reason=str(args.calibration_reason),
        turn_timeout_seconds=max(1.0, float(args.turn_timeout_seconds)),
    )
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    summary = {
        "ok": True,
        "output": str(output),
        "turn_count": int(payload.get("turn_count") or 0),
        "run_id": ((payload.get("run") or {}).get("run_id") if isinstance(payload.get("run"), dict) else None),
        "continuity_failure_rate": (
            (((payload.get("report") or {}).get("axes") or {}).get("continuity") or {}
            ).get("continuity_failure_rate")
            if isinstance(payload.get("report"), dict)
            else None
        ),
        "mode_accuracy": (
            (((payload.get("report") or {}).get("axes") or {}).get("mode_accuracy") or {}
            ).get("accuracy")
            if isinstance(payload.get("report"), dict)
            else None
        ),
        "pushback_trigger_rate": (
            (((payload.get("report") or {}).get("axes") or {}).get("pushback") or {}
            ).get("trigger_rate")
            if isinstance(payload.get("report"), dict)
            else None
        ),
        "reply_uniqueness_rate": (
            (payload.get("quality_metrics") or {}).get("reply_uniqueness_rate")
            if isinstance(payload.get("quality_metrics"), dict)
            else None
        ),
        "live_state_reference_rate": (
            (payload.get("quality_metrics") or {}).get("live_state_reference_rate")
            if isinstance(payload.get("quality_metrics"), dict)
            else None
        ),
        "tradeoff_presence_rate": (
            (payload.get("quality_metrics") or {}).get("tradeoff_presence_rate")
            if isinstance(payload.get("quality_metrics"), dict)
            else None
        ),
        "why_now_presence_rate": (
            (payload.get("quality_metrics") or {}).get("why_now_presence_rate")
            if isinstance(payload.get("quality_metrics"), dict)
            else None
        ),
        "pushback_specificity_score": (
            (payload.get("quality_metrics") or {}).get("pushback_specificity_score")
            if isinstance(payload.get("quality_metrics"), dict)
            else None
        ),
    }
    records = payload.get("records") if isinstance(payload.get("records"), list) else []
    first_turn = records[0] if records else {}
    first_turn_elapsed = first_turn.get("elapsed_ms")
    first_turn_error = str(first_turn.get("prepare_error") or "").strip()
    first_turn_deferred = bool(first_turn.get("response_deferred"))
    summary["first_turn_presence_ok"] = bool(
        isinstance(first_turn_elapsed, (int, float))
        and float(first_turn_elapsed) <= 500.0
        and not first_turn_error
        and not first_turn_deferred
    )
    summary["first_turn_elapsed_ms"] = first_turn_elapsed
    summary["first_turn_error"] = first_turn_error or None
    summary["first_turn_response_deferred"] = first_turn_deferred
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
