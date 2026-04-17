#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class QuickPrompt:
    prompt: str
    kind: str


PROMPTS: list[QuickPrompt] = [
    QuickPrompt("Hello", "greeting"),
    QuickPrompt("What's up?", "status"),
    QuickPrompt("What do you think?", "partner"),
    QuickPrompt("Push back on me.", "partner"),
]


def _post_json(url: str, payload: dict[str, Any], timeout: float = 45.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        url=url,
        data=data,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urlopen(req, timeout=max(5.0, float(timeout))) as resp:  # noqa: S310 local endpoint
        return json.loads(resp.read().decode("utf-8"))


def _normalize(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _contains_live_state(text: str) -> bool:
    lowered = _normalize(text)
    markers = (
        "academic",
        "exam",
        "market",
        "risk",
        "priority",
        "tradeoff",
        "next move",
        "opportunity",
        "zenith",
        "ml",
        "betting",
    )
    return any(marker in lowered for marker in markers)


def run_check(*, api_base: str, session_prefix: str, timeout: float) -> dict[str, Any]:
    base = api_base.rstrip("/")
    failures: list[str] = []
    records: list[dict[str, Any]] = []
    latencies: list[float] = []
    normalized_replies: list[str] = []
    banned_fragments = (
        "give me the target outcome",
        "turn this into a concrete next action",
        "iterate from evidence",
        "i can turn this into",
    )

    for idx, item in enumerate(PROMPTS, start=1):
        payload = {
            "text": item.prompt,
            "surface_id": "dm:owner",
            "session_id": f"{session_prefix}-{idx}",
        }
        try:
            response = _post_json(f"{base}/api/presence/reply/prepare", payload, timeout=timeout)
        except HTTPError as exc:
            failures.append(f"{item.prompt}:http_error:{exc.code}")
            continue
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{item.prompt}:request_error:{exc}")
            continue

        diagnostics = (
            dict(response.get("reply_diagnostics") or {})
            if isinstance(response.get("reply_diagnostics"), dict)
            else {}
        )
        reply_text = str(response.get("reply_text") or "").strip()
        normalized_reply = _normalize(reply_text)
        normalized_replies.append(normalized_reply)
        for fragment in banned_fragments:
            if fragment in normalized_reply:
                failures.append(f"{item.prompt}:legacy_template_leak:{fragment}")

        latency = float(diagnostics.get("latency_ms") or 0.0)
        if latency > 0:
            latencies.append(latency)

        partner_lane_used = bool(diagnostics.get("partner_lane_used"))
        identity_capsule_used = bool(diagnostics.get("identity_capsule_used"))
        partner_depth_lane = str(diagnostics.get("partner_depth_lane") or "").strip().lower() or None
        deep_lane_invoked = bool(diagnostics.get("deep_lane_invoked"))
        answer_source = str(diagnostics.get("answer_source") or "").strip().lower() or None
        retrieval_selected_count = int(diagnostics.get("retrieval_selected_count") or 0)

        if item.kind == "status":
            if not _contains_live_state(reply_text):
                failures.append(f"{item.prompt}:status_missing_live_state")
            if retrieval_selected_count <= 0 and "current read:" not in normalized_reply:
                failures.append(f"{item.prompt}:status_missing_grounding_signal")

        if item.kind == "partner":
            if not partner_lane_used:
                failures.append(f"{item.prompt}:partner_lane_not_used")
            if not identity_capsule_used:
                failures.append(f"{item.prompt}:identity_capsule_not_used")
            if answer_source in {"status_fallback", "error_fallback", "cached_brief"}:
                failures.append(f"{item.prompt}:invalid_answer_source:{answer_source}")
            if partner_depth_lane == "partner_deep":
                failures.append(f"{item.prompt}:unexpected_deep_lane")
            if deep_lane_invoked:
                failures.append(f"{item.prompt}:unexpected_deep_invocation")

        records.append(
            {
                "prompt": item.prompt,
                "kind": item.kind,
                "reply_text": reply_text,
                "diagnostics": {
                    "answer_source": answer_source,
                    "route_reason": diagnostics.get("route_reason"),
                    "response_family": diagnostics.get("response_family"),
                    "partner_lane_used": partner_lane_used,
                    "identity_capsule_used": identity_capsule_used,
                    "partner_depth_lane": partner_depth_lane,
                    "deep_lane_invoked": deep_lane_invoked,
                    "retrieval_selected_count": retrieval_selected_count,
                    "latency_ms": latency,
                },
            }
        )

    unique_reply_count = len({item for item in normalized_replies if item})
    if unique_reply_count < len(PROMPTS):
        failures.append(f"semantic_collapse:{unique_reply_count}/{len(PROMPTS)}")

    median_latency_ms = statistics.median(latencies) if latencies else None
    max_latency_ms = max(latencies) if latencies else None
    under_12s_count = sum(1 for item in latencies if item <= 12000.0)
    if median_latency_ms is None or max_latency_ms is None:
        failures.append("latency_metrics_missing")
    else:
        if median_latency_ms > 12000:
            failures.append(f"latency_median_too_high:{median_latency_ms:.1f}")
        if under_12s_count < 3:
            failures.append(f"latency_under_12s_count_too_low:{under_12s_count}/4")
        if max_latency_ms > 45000:
            failures.append(f"latency_max_too_high:{max_latency_ms:.1f}")

    return {
        "ok": not failures,
        "generated_at": datetime.now(UTC).isoformat(),
        "api_base": base,
        "prompt_count": len(PROMPTS),
        "unique_reply_count": unique_reply_count,
        "median_latency_ms": median_latency_ms,
        "max_latency_ms": max_latency_ms,
        "under_12s_count": under_12s_count,
        "records": records,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Low-power quick 4-prompt validation.")
    parser.add_argument("--api-base", default="http://127.0.0.1:8765")
    parser.add_argument("--session-prefix", default="low-power-quick")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if args.output is None:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = ROOT_DIR / "analysis" / f"low_power_quick_check_{stamp}.json"
    else:
        output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    payload = run_check(
        api_base=str(args.api_base),
        session_prefix=str(args.session_prefix),
        timeout=float(args.timeout),
    )
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    summary = {
        "ok": bool(payload.get("ok")),
        "output": str(output),
        "prompt_count": int(payload.get("prompt_count") or 0),
        "unique_reply_count": int(payload.get("unique_reply_count") or 0),
        "median_latency_ms": payload.get("median_latency_ms"),
        "max_latency_ms": payload.get("max_latency_ms"),
        "under_12s_count": int(payload.get("under_12s_count") or 0),
        "failure_count": len(payload.get("failures") or []),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not payload.get("ok"):
        print("[low-power-check] failures:")
        for failure in payload.get("failures") or []:
            print(f" - {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
