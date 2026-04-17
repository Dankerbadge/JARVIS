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
]


def _post_json(url: str, payload: dict[str, Any], timeout: float = 90.0) -> dict[str, Any]:
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


def run_soak(
    *,
    api_base: str,
    session_prefix: str,
    repo_path: Path,
    db_path: Path,
    strict_check: bool,
    host: str,
    port: int,
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
    )
    surface_specs = (
        ("text", "/api/presence/reply/prepare", "dm:owner"),
        ("voice", "/api/presence/voice/reply/prepare", "voice:owner"),
    )

    for index, turn in enumerate(PARTNER_TURNS, start=1):
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

            turn_record["surfaces"][surface_name] = {
                "route_reason": route_reason,
                "answer_source": answer_source,
                "response_family": response_family,
                "partner_lane_used": partner_lane_used,
                "identity_capsule_used": identity_capsule_used,
                "identity_capsule_hash": identity_capsule_hash,
                "retrieval_selected_count": retrieval_selected_count,
                "contract_gate_passed": contract_gate_passed,
                "reply_text": reply_text,
            }

            if answer_source == "cached_brief":
                failures.append(f"{turn.prompt}::{surface_name}:answer_source_cached_brief")
            if retrieval_selected_count <= 0:
                failures.append(f"{turn.prompt}::{surface_name}:retrieval_selected_count_zero")
            if not contract_gate_passed:
                failures.append(f"{turn.prompt}::{surface_name}:contract_gate_failed")
            if not partner_lane_used:
                failures.append(f"{turn.prompt}::{surface_name}:partner_lane_not_used")
            if not identity_capsule_used or not identity_capsule_hash:
                failures.append(f"{turn.prompt}::{surface_name}:identity_capsule_missing")
            if response_family in {"status_brief", "status_fallback", "social_fast_path", "partner_limited_context"}:
                failures.append(f"{turn.prompt}::{surface_name}:response_family_invalid:{response_family}")

            lowered = reply_text.lower()
            for marker in leak_markers:
                if marker in lowered:
                    failures.append(f"{turn.prompt}::{surface_name}:pipeline_leak:{marker}")

        records.append(turn_record)

    return {
        "ok": not failures,
        "generated_at": datetime.now(UTC).isoformat(),
        "api_base": base,
        "turn_count": len(PARTNER_TURNS),
        "records": records,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M22A partner-turn soak with hard fail gates.")
    parser.add_argument("--api-base", default="http://127.0.0.1:8765")
    parser.add_argument("--session-prefix", default="m22a-partner")
    parser.add_argument("--repo-path", type=Path, default=ROOT_DIR)
    parser.add_argument("--db-path", type=Path, default=ROOT_DIR / ".jarvis" / "jarvis.db")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--skip-strict-check", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    repo_path = args.repo_path.expanduser().resolve()
    db_path = args.db_path.expanduser().resolve()
    if args.output is None:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = repo_path / "analysis" / f"m22a_partner_soak_{stamp}.json"
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
    )
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    summary = {
        "ok": bool(payload.get("ok")),
        "output": str(output),
        "turn_count": int(payload.get("turn_count") or 0),
        "failure_count": len(payload.get("failures") or []),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not payload.get("ok"):
        print("[m22a] failures:")
        for item in payload.get("failures") or []:
            print(f" - {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
