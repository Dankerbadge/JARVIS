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


PARTNER_TURNS: list[PartnerTurn] = [
    PartnerTurn("What do you think?"),
    PartnerTurn("Be straight with me."),
    PartnerTurn("Push back on me."),
    PartnerTurn("What are you noticing?"),
    PartnerTurn("Continue from earlier."),
    PartnerTurn("What's the real tradeoff here?"),
    PartnerTurn("What am I probably missing right now?"),
    PartnerTurn("Challenge my current plan."),
    PartnerTurn("If I am rationalizing, say it directly."),
    PartnerTurn("Where am I wasting time today?"),
    PartnerTurn("What is the highest-leverage move this hour?"),
    PartnerTurn("What should I stop doing immediately?"),
    PartnerTurn("What matters more: academics or markets today?"),
    PartnerTurn("Give me your strategic read, not a checklist."),
    PartnerTurn("What tension are you tracking across my projects?"),
    PartnerTurn("What would you do if you were in my position?"),
    PartnerTurn("What is the riskiest assumption in my plan?"),
    PartnerTurn("What are two concrete options and their tradeoff?"),
    PartnerTurn("Where should you push back on me right now?"),
    PartnerTurn("What is the real cost if I delay this decision?"),
    PartnerTurn("Tell me the uncomfortable truth I need to hear."),
    PartnerTurn("How should we prioritize betting bot, ML, and JARVIS this week?"),
    PartnerTurn("If we choose speed, what quality risk do we accept?"),
    PartnerTurn("Give me your best partner-level recommendation now."),
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


def _semantic_signature(text: str, *, partner_subfamily: str | None = None) -> str:
    lowered = str(text or "").strip().lower()
    has_tradeoff = any(token in lowered for token in ("tradeoff", "trade off", "versus", " vs ", "cost"))
    has_next_move = any(
        token in lowered
        for token in ("next move", "next step", "we should", "i recommend", "first step", "first move")
    )
    has_pushback = any(
        token in lowered
        for token in ("push back", "i disagree", "we should not", "do not", "risk", "safer", "alternative")
    )
    has_uncertainty = any(token in lowered for token in ("uncertain", "not sure", "hypothesis", "confidence"))
    has_question = "?" in str(text or "")
    return "|".join(
        [
            f"subfamily:{(str(partner_subfamily or '').strip().lower() or 'unknown')}",
            "tradeoff" if has_tradeoff else "no_tradeoff",
            "next_move" if has_next_move else "no_next_move",
            "pushback" if has_pushback else "no_pushback",
            "uncertainty" if has_uncertainty else "no_uncertainty",
            "question" if has_question else "statement",
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
    min_retrieval: int,
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

    turns = PARTNER_TURNS[: max(1, int(limit))] if limit else PARTNER_TURNS
    for index, turn in enumerate(turns, start=1):
        print(f"[m22b] turn {index}/{len(turns)}: {turn.prompt}", flush=True)
        turn_record: dict[str, Any] = {
            "prompt": turn.prompt,
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
            except Exception as exc:  # noqa: BLE001 - soak should continue and report.
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
            identity_capsule_used = _normalize_bool(diagnostics.get("identity_capsule_used"))
            identity_capsule_hash = str(diagnostics.get("identity_capsule_hash") or "").strip() or None
            partner_subfamily = str(diagnostics.get("partner_subfamily") or "").strip().lower() or None
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
            signature = _semantic_signature(reply_text, partner_subfamily=partner_subfamily)

            turn_record["surfaces"][surface_name] = {
                "route_reason": route_reason,
                "answer_source": answer_source,
                "response_family": response_family,
                "partner_lane_used": partner_lane_used,
                "partner_subfamily": partner_subfamily,
                "identity_capsule_used": identity_capsule_used,
                "identity_capsule_hash": identity_capsule_hash,
                "retrieval_selected_count": retrieval_selected_count,
                "retrieval_bucket_counts": retrieval_bucket_counts,
                "partner_context_mix_ok": partner_context_mix_ok,
                "contract_gate_passed": contract_gate_passed,
                "semantic_signature": signature,
                "reply_text": reply_text,
            }
            signature_usage.setdefault(surface_name, {})
            signature_usage[surface_name][signature] = int(signature_usage[surface_name].get(signature) or 0) + 1

            if require_model_answer and answer_source != "model":
                failures.append(f"{turn.prompt}::{surface_name}:answer_source_not_model:{answer_source}")
            if retrieval_selected_count < max(1, min_retrieval):
                failures.append(f"{turn.prompt}::{surface_name}:retrieval_below_floor:{retrieval_selected_count}")
            if not contract_gate_passed:
                failures.append(f"{turn.prompt}::{surface_name}:contract_gate_failed")
            if not partner_lane_used:
                failures.append(f"{turn.prompt}::{surface_name}:partner_lane_not_used")
            if not identity_capsule_used or not identity_capsule_hash:
                failures.append(f"{turn.prompt}::{surface_name}:identity_capsule_missing")
            if not partner_subfamily:
                failures.append(f"{turn.prompt}::{surface_name}:partner_subfamily_missing")
            if not partner_context_mix_ok:
                failures.append(f"{turn.prompt}::{surface_name}:partner_context_mix_missing")
            for bucket in ("live_state", "thread_memory", "identity_long_horizon"):
                if int(retrieval_bucket_counts.get(bucket) or 0) <= 0:
                    failures.append(f"{turn.prompt}::{surface_name}:missing_bucket:{bucket}")
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
        "min_retrieval": min_retrieval,
        "require_model_answer": require_model_answer,
        "max_signature_reuse": max_signature_reuse,
        "signature_usage": signature_usage,
        "records": records,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M22B spoken partner soak with strict partner-quality gates.")
    parser.add_argument("--api-base", default="http://127.0.0.1:8765")
    parser.add_argument("--session-prefix", default="m22b-partner")
    parser.add_argument("--repo-path", type=Path, default=ROOT_DIR)
    parser.add_argument("--db-path", type=Path, default=ROOT_DIR / ".jarvis" / "jarvis.db")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--min-retrieval", type=int, default=3)
    parser.add_argument("--allow-non-model-answer", action="store_true")
    parser.add_argument("--skip-strict-check", action="store_true")
    parser.add_argument("--max-signature-reuse", type=int, default=4)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    repo_path = args.repo_path.expanduser().resolve()
    db_path = args.db_path.expanduser().resolve()
    if args.output is None:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = repo_path / "analysis" / f"m22b_spoken_partner_soak_{stamp}.json"
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
        min_retrieval=max(1, int(args.min_retrieval)),
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
        "min_retrieval": int(payload.get("min_retrieval") or 0),
        "max_signature_reuse": int(payload.get("max_signature_reuse") or 0),
        "require_model_answer": bool(payload.get("require_model_answer")),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not payload.get("ok"):
        print("[m22b] failures:")
        for item in payload.get("failures") or []:
            print(f" - {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
