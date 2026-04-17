#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class PartnerTurn:
    prompt: str
    expected_depth_lane: str = "partner_deep"
    require_tradeoff_frame: bool = False
    require_why_now_frame: bool = False


PARTNER_DEEP_TURNS: list[PartnerTurn] = [
    PartnerTurn("What do you really think about my current plan?", require_why_now_frame=True),
    PartnerTurn("Be straight with me about the biggest risk right now.", require_why_now_frame=True),
    PartnerTurn("Tell me what I am missing that could hurt us this week.", require_why_now_frame=True),
    PartnerTurn("Where am I bullshitting myself right now?", require_why_now_frame=True),
    PartnerTurn("Challenge my plan hard and tell me why now matters.", require_why_now_frame=True),
    PartnerTurn("Give me your deeper strategic read for this week.", require_tradeoff_frame=True, require_why_now_frame=True),
    PartnerTurn("What is the real tradeoff if I prioritize speed today?", require_tradeoff_frame=True, require_why_now_frame=True),
    PartnerTurn("What is the real tradeoff if I prioritize quality this hour?", require_tradeoff_frame=True, require_why_now_frame=True),
    PartnerTurn("If we delay this decision, what cost do we absorb and when?", require_tradeoff_frame=True, require_why_now_frame=True),
    PartnerTurn("What is the uncomfortable truth I need to hear right now?", require_why_now_frame=True),
    PartnerTurn("Where should you push back on me the hardest today?", require_why_now_frame=True),
    PartnerTurn("What is the riskiest assumption in my current approach?", require_why_now_frame=True),
    PartnerTurn("Give me two options and the tradeoff between them.", require_tradeoff_frame=True),
    PartnerTurn("What should I stop doing immediately and why now?", require_why_now_frame=True),
    PartnerTurn("What matters more today: academics or markets, and why?", require_tradeoff_frame=True, require_why_now_frame=True),
    PartnerTurn("How should we prioritize betting bot, ML, and JARVIS this week?", require_tradeoff_frame=True, require_why_now_frame=True),
    PartnerTurn("What is the highest-leverage move this hour and what do we defer?", require_tradeoff_frame=True, require_why_now_frame=True),
    PartnerTurn("Where am I wasting time and what is the opportunity cost right now?", require_tradeoff_frame=True, require_why_now_frame=True),
    PartnerTurn("What tension are you tracking across domains that I am underweighting?", require_why_now_frame=True),
    PartnerTurn("Continue from earlier, but go deeper and call out my blind spots.", require_why_now_frame=True),
    PartnerTurn("If I choose speed, what quality risk do I accept now?", require_tradeoff_frame=True, require_why_now_frame=True),
    PartnerTurn("If I choose quality, what timing risk do I accept now?", require_tradeoff_frame=True, require_why_now_frame=True),
    PartnerTurn("What would you do if you were in my position today?", require_why_now_frame=True),
    PartnerTurn("What do you think I am avoiding, and why does it matter now?", require_why_now_frame=True),
    PartnerTurn("Give me the deeper read, not the short status summary.", require_why_now_frame=True),
    PartnerTurn("Tell me the main contradiction in my current strategy.", require_why_now_frame=True),
    PartnerTurn("Push back on my default instinct and propose a safer alternative.", require_why_now_frame=True),
    PartnerTurn("What is the real downside if this thesis is wrong this week?", require_tradeoff_frame=True, require_why_now_frame=True),
    PartnerTurn("Give me your best partner-level recommendation and why now.", require_why_now_frame=True),
    PartnerTurn("If I am rationalizing, call it out and tell me what to do instead.", require_why_now_frame=True),
    PartnerTurn("What are we underestimating across academics, markets, and product execution?", require_tradeoff_frame=True, require_why_now_frame=True),
    PartnerTurn("What decision should I make before tonight, and what tradeoff does it lock in?", require_tradeoff_frame=True, require_why_now_frame=True),
    PartnerTurn("What is the strongest argument against my current plan?", require_why_now_frame=True),
    PartnerTurn("What is the most expensive delay risk in the next 48 hours?", require_tradeoff_frame=True, require_why_now_frame=True),
    PartnerTurn("What is your most honest read of where I am strongest and weakest this week?", require_why_now_frame=True),
    PartnerTurn("Summarize the deepest strategic recommendation you have for me now.", require_tradeoff_frame=True, require_why_now_frame=True),
]


def _post_json(url: str, payload: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = Request(
        url=url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 - local endpoint.
        return json.loads(resp.read().decode("utf-8"))


def _run_strict_check(*, repo_path: Path, db_path: Path, host: str, port: int) -> None:
    candidates = [
        repo_path / ".venv-voice" / "bin" / "python",
        Path(sys.executable),
        Path("/usr/bin/python3"),
    ]
    py_bin = next((candidate for candidate in candidates if candidate.exists()), None)
    if py_bin is None:
        raise RuntimeError("python_binary_not_found_for_strict_check")
    cmd = [
        str(py_bin),
        str(repo_path / "scripts" / "jarvis_runtime_status.py"),
        "--repo-path",
        str(repo_path),
        "--db-path",
        str(db_path),
        "--host",
        host,
        "--port",
        str(port),
        "--check-server",
        "--strict",
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            "strict_runtime_check_failed\n"
            + (completed.stdout or "")
            + ("\n" + completed.stderr if completed.stderr else "")
        )


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _semantic_signature(
    text: str,
    *,
    partner_subfamily: str | None = None,
    partner_depth_lane: str | None = None,
    used_tradeoff_frame: bool | None = None,
    used_why_now_frame: bool | None = None,
) -> str:
    lowered = str(text or "").strip().lower()
    has_pushback = any(
        token in lowered
        for token in ("push back", "i disagree", "we should not", "do not", "risk", "safer", "alternative")
    )
    has_uncertainty = any(token in lowered for token in ("uncertain", "not sure", "hypothesis", "confidence"))
    has_question = "?" in str(text or "")
    has_academics = any(token in lowered for token in ("academic", "exam", "school", "class"))
    has_markets = any(token in lowered for token in ("market", "msft", "trade", "bet", "thesis"))
    has_projects = any(token in lowered for token in ("jarvis", "betting bot", "ml", "machine learning", "product"))
    has_time_window = any(token in lowered for token in ("today", "tonight", "this hour", "this week", "48 hours", "now"))
    has_decision_frame = any(token in lowered for token in ("decide", "choose", "prioritize", "defer", "stop", "next move"))
    has_blindspot_frame = any(
        token in lowered for token in ("blind spot", "missing", "underestimating", "underweight", "rationalizing")
    )
    return "|".join(
        [
            f"subfamily:{(str(partner_subfamily or '').strip().lower() or 'unknown')}",
            f"depth:{(str(partner_depth_lane or '').strip().lower() or 'unknown')}",
            "tradeoff" if bool(used_tradeoff_frame) else "no_tradeoff",
            "why_now" if bool(used_why_now_frame) else "no_why_now",
            "pushback" if has_pushback else "no_pushback",
            "uncertainty" if has_uncertainty else "no_uncertainty",
            "question" if has_question else "statement",
            "academics" if has_academics else "no_academics",
            "markets" if has_markets else "no_markets",
            "projects" if has_projects else "no_projects",
            "time_window" if has_time_window else "no_time_window",
            "decision_frame" if has_decision_frame else "no_decision_frame",
            "blindspot_frame" if has_blindspot_frame else "no_blindspot_frame",
        ]
    )


def run_soak(
    *,
    api_base: str,
    session_prefix: str,
    repo_path: Path,
    db_path: Path,
    strict_check: bool,
    host: str,
    port: int,
    min_retrieval_deep: int,
    require_model_answer: bool,
    max_signature_reuse: int,
    limit: int | None,
) -> dict[str, Any]:
    if strict_check:
        _run_strict_check(repo_path=repo_path, db_path=db_path, host=host, port=port)

    base = api_base.rstrip("/")
    failures: list[str] = []
    records: list[dict[str, Any]] = []
    leak_markers = (
        "current read:",
        "cached brief",
        "route_reason",
        "answer_source",
        "contract_gate",
        "status_contract",
        "target outcome",
        "iterate from evidence",
        "i am online and continuity is stable",
        "i can choose the next concrete step and keep momentum",
    )
    surface_specs = (
        ("text", "/api/presence/reply/prepare", "dm:owner"),
        ("voice", "/api/presence/voice/reply/prepare", "voice:owner"),
    )
    signature_usage: dict[str, dict[str, int]] = {"text": {}, "voice": {}}

    turns = PARTNER_DEEP_TURNS[: max(1, int(limit))] if limit else PARTNER_DEEP_TURNS
    for index, turn in enumerate(turns, start=1):
        print(f"[m22d] turn {index}/{len(turns)}: {turn.prompt}", flush=True)
        turn_record: dict[str, Any] = {
            "prompt": turn.prompt,
            "expected_depth_lane": turn.expected_depth_lane,
            "surfaces": {},
        }
        for surface_name, endpoint, surface_id in surface_specs:
            payload = {
                "text": turn.prompt,
                "surface_id": surface_id,
                "session_id": f"{session_prefix}-{surface_name}-{index}",
            }
            try:
                response = _post_json(f"{base}{endpoint}", payload)
            except HTTPError as exc:
                failures.append(f"{turn.prompt}::{surface_name}:http_error:{exc.code}")
                continue
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{turn.prompt}::{surface_name}:request_error:{exc}")
                continue

            diagnostics = (
                dict(response.get("reply_diagnostics") or {})
                if isinstance(response.get("reply_diagnostics"), dict)
                else {}
            )
            reply_text = str(response.get("reply_text") or "").strip()
            contract_gate_passed = _normalize_bool(
                response.get("contract_gate_passed", diagnostics.get("contract_gate_passed"))
            )
            retrieval_selected_count = int(diagnostics.get("retrieval_selected_count") or 0)
            answer_source = str(diagnostics.get("answer_source") or "").strip() or None
            route_reason = str(diagnostics.get("route_reason") or "").strip() or None
            response_family = str(diagnostics.get("response_family") or "").strip() or None
            partner_lane_used = _normalize_bool(diagnostics.get("partner_lane_used"))
            partner_subfamily = str(diagnostics.get("partner_subfamily") or "").strip().lower() or None
            partner_depth_lane = str(diagnostics.get("partner_depth_lane") or "").strip().lower() or None
            deep_lane_invoked = _normalize_bool(diagnostics.get("deep_lane_invoked"))
            deep_model_name = str(diagnostics.get("deep_model_name") or "").strip() or None
            identity_capsule_used = _normalize_bool(diagnostics.get("identity_capsule_used"))
            identity_capsule_hash = str(diagnostics.get("identity_capsule_hash") or "").strip() or None
            retrieval_bucket_counts = (
                dict(diagnostics.get("retrieval_bucket_counts") or {})
                if isinstance(diagnostics.get("retrieval_bucket_counts"), dict)
                else {}
            )
            partner_context_mix_ok = (
                _normalize_bool(diagnostics.get("partner_context_mix_ok"))
                if diagnostics.get("partner_context_mix_ok") is not None
                else False
            )
            used_tradeoff_frame = (
                _normalize_bool(diagnostics.get("used_tradeoff_frame"))
                if diagnostics.get("used_tradeoff_frame") is not None
                else False
            )
            used_why_now_frame = (
                _normalize_bool(diagnostics.get("used_why_now_frame"))
                if diagnostics.get("used_why_now_frame") is not None
                else False
            )
            signature = _semantic_signature(
                reply_text,
                partner_subfamily=partner_subfamily,
                partner_depth_lane=partner_depth_lane,
                used_tradeoff_frame=used_tradeoff_frame,
                used_why_now_frame=used_why_now_frame,
            )

            turn_record["surfaces"][surface_name] = {
                "route_reason": route_reason,
                "answer_source": answer_source,
                "response_family": response_family,
                "partner_lane_used": partner_lane_used,
                "partner_subfamily": partner_subfamily,
                "partner_depth_lane": partner_depth_lane,
                "deep_lane_invoked": deep_lane_invoked,
                "deep_model_name": deep_model_name,
                "identity_capsule_used": identity_capsule_used,
                "identity_capsule_hash": identity_capsule_hash,
                "retrieval_selected_count": retrieval_selected_count,
                "retrieval_bucket_counts": retrieval_bucket_counts,
                "partner_context_mix_ok": partner_context_mix_ok,
                "contract_gate_passed": contract_gate_passed,
                "used_tradeoff_frame": used_tradeoff_frame,
                "used_why_now_frame": used_why_now_frame,
                "semantic_signature": signature,
                "reply_text": reply_text,
            }
            signature_usage.setdefault(surface_name, {})
            signature_usage[surface_name][signature] = int(signature_usage[surface_name].get(signature) or 0) + 1

            if require_model_answer and answer_source != "model":
                failures.append(f"{turn.prompt}::{surface_name}:answer_source_not_model:{answer_source}")
            if not contract_gate_passed:
                failures.append(f"{turn.prompt}::{surface_name}:contract_gate_failed")
            if not partner_lane_used:
                failures.append(f"{turn.prompt}::{surface_name}:partner_lane_not_used")
            if not identity_capsule_used or not identity_capsule_hash:
                failures.append(f"{turn.prompt}::{surface_name}:identity_capsule_missing")
            if not partner_subfamily:
                failures.append(f"{turn.prompt}::{surface_name}:partner_subfamily_missing")
            if not partner_depth_lane:
                failures.append(f"{turn.prompt}::{surface_name}:partner_depth_lane_missing")
            if turn.expected_depth_lane and partner_depth_lane != turn.expected_depth_lane:
                failures.append(
                    f"{turn.prompt}::{surface_name}:partner_depth_lane_mismatch:{partner_depth_lane}!={turn.expected_depth_lane}"
                )
            if retrieval_selected_count < max(1, min_retrieval_deep):
                failures.append(f"{turn.prompt}::{surface_name}:retrieval_below_deep_floor:{retrieval_selected_count}")
            if not partner_context_mix_ok:
                failures.append(f"{turn.prompt}::{surface_name}:partner_context_mix_missing")

            required_buckets = ("live_state", "thread_memory", "identity_long_horizon", "personal_context", "pushback_outcomes")
            for bucket in required_buckets:
                if int(retrieval_bucket_counts.get(bucket) or 0) <= 0:
                    failures.append(f"{turn.prompt}::{surface_name}:missing_bucket:{bucket}")

            if turn.require_tradeoff_frame and not used_tradeoff_frame:
                failures.append(f"{turn.prompt}::{surface_name}:tradeoff_frame_missing")
            if turn.require_why_now_frame and not used_why_now_frame:
                failures.append(f"{turn.prompt}::{surface_name}:why_now_frame_missing")

            if response_family in {
                "status_brief",
                "status_fallback",
                "social_fast_path",
                "partner_limited_context",
                "partner_fallback",
            }:
                failures.append(f"{turn.prompt}::{surface_name}:response_family_invalid:{response_family}")

            lowered = reply_text.lower()
            for marker in leak_markers:
                if marker in lowered:
                    failures.append(f"{turn.prompt}::{surface_name}:pipeline_or_generic_leak:{marker}")

        records.append(turn_record)

    for surface_name, counts in signature_usage.items():
        for signature, count in counts.items():
            if int(count) > max(1, int(max_signature_reuse)):
                failures.append(
                    f"{surface_name}:semantic_signature_reuse_exceeded:{signature}:{count}>{max_signature_reuse}"
                )

    return {
        "ok": not failures,
        "generated_at": datetime.now(UTC).isoformat(),
        "api_base": base,
        "turn_count": len(turns),
        "min_retrieval_deep": min_retrieval_deep,
        "require_model_answer": require_model_answer,
        "max_signature_reuse": max_signature_reuse,
        "signature_usage": signature_usage,
        "records": records,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M22D partner-depth soak with strict deep-lane gates.")
    parser.add_argument("--api-base", default="http://127.0.0.1:8765")
    parser.add_argument("--session-prefix", default="m22d-partner-depth")
    parser.add_argument("--repo-path", type=Path, default=ROOT_DIR)
    parser.add_argument("--db-path", type=Path, default=ROOT_DIR / ".jarvis" / "jarvis.db")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--min-retrieval-deep", type=int, default=5)
    parser.add_argument("--allow-non-model-answer", action="store_true")
    parser.add_argument("--skip-strict-check", action="store_true")
    parser.add_argument("--max-signature-reuse", type=int, default=4)
    parser.add_argument("--limit", type=int, default=36)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    repo_path = args.repo_path.expanduser().resolve()
    db_path = args.db_path.expanduser().resolve()
    if args.output is None:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = repo_path / "analysis" / f"m22d_partner_depth_soak_{stamp}.json"
    else:
        output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    payload = run_soak(
        api_base=str(args.api_base),
        session_prefix=str(args.session_prefix),
        repo_path=repo_path,
        db_path=db_path,
        strict_check=not bool(args.skip_strict_check),
        host=str(args.host),
        port=int(args.port),
        min_retrieval_deep=max(1, int(args.min_retrieval_deep)),
        require_model_answer=not bool(args.allow_non_model_answer),
        max_signature_reuse=max(1, int(args.max_signature_reuse)),
        limit=(max(1, int(args.limit)) if args.limit else None),
    )
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    summary = {
        "ok": bool(payload.get("ok")),
        "output": str(output),
        "turn_count": int(payload.get("turn_count") or 0),
        "failure_count": len(payload.get("failures") or []),
        "min_retrieval_deep": int(payload.get("min_retrieval_deep") or 0),
        "max_signature_reuse": int(payload.get("max_signature_reuse") or 0),
        "require_model_answer": bool(payload.get("require_model_answer")),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not payload.get("ok"):
        print("[m22d] failures:")
        for item in payload.get("failures") or []:
            print(f" - {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
