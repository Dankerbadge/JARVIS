#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error, parse, request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish a bridge markdown briefing to an optional webhook target.",
    )
    parser.add_argument(
        "--markdown-path",
        type=Path,
        required=True,
        help="Path to markdown briefing file.",
    )
    parser.add_argument(
        "--webhook-url",
        type=str,
        default="",
        help="Optional webhook URL. If omitted, script returns a skipped result.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate payload and report publish intent without sending network requests.",
    )
    parser.add_argument(
        "--dry-run-output-mode",
        type=str,
        default="full",
        choices=("full", "preview_only"),
        help="Dry-run output verbosity: full payload or preview_only compact payload.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=20.0,
        help="Webhook request timeout in seconds when not in --dry-run mode.",
    )
    parser.add_argument(
        "--retry-attempts",
        type=int,
        default=0,
        help="Number of retry attempts after the initial publish attempt.",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=1.0,
        help="Initial retry backoff duration in seconds.",
    )
    parser.add_argument(
        "--retry-backoff-multiplier",
        type=float,
        default=2.0,
        help="Retry backoff multiplier applied per retry attempt.",
    )
    parser.add_argument(
        "--retry-max-backoff-seconds",
        type=float,
        default=30.0,
        help="Upper bound for retry backoff delay in seconds.",
    )
    parser.add_argument(
        "--retry-jitter-seconds",
        type=float,
        default=0.0,
        help="Optional max random jitter in seconds added to each retry backoff.",
    )
    parser.add_argument(
        "--retry-jitter-seed",
        type=int,
        default=None,
        help="Optional random seed for deterministic retry jitter generation.",
    )
    parser.add_argument(
        "--retry-on-http-status",
        action="append",
        default=None,
        help=(
            "HTTP status codes that should trigger retries. "
            "Can be repeated or passed as comma-separated values."
        ),
    )
    parser.add_argument(
        "--error-body-preview-chars",
        type=int,
        default=0,
        help="Optional max chars of failed HTTP response body to capture in diagnostics (0 disables).",
    )
    parser.add_argument(
        "--retry-diagnostics-mode",
        type=str,
        default="full",
        choices=("full", "minimal"),
        help="Retry diagnostics verbosity mode for attempt metadata payloads.",
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=240,
        help="Max markdown chars to include in payload preview.",
    )
    parser.add_argument(
        "--json-compact",
        action="store_true",
        help="Emit compact JSON.",
    )
    return parser


def _sanitize_webhook_target(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    parsed = parse.urlsplit(raw)
    if not parsed.scheme:
        return "(invalid_url)"
    host = parsed.netloc or "(no_host)"
    path = parsed.path or "/"
    if len(path) > 48:
        path = f"{path[:48]}..."
    return f"{parsed.scheme}://{host}{path}"


def _build_preview(markdown: str, *, preview_chars: int) -> str:
    limit = max(0, int(preview_chars))
    normalized = str(markdown).replace("\r\n", "\n")
    if limit == 0:
        return ""
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def _emit(payload: dict, *, compact: bool) -> None:
    if compact:
        print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    else:
        print(json.dumps(payload, indent=2))


def _parse_retry_http_statuses(raw_values: list[str] | None) -> list[int]:
    if not raw_values:
        return [429, 500, 502, 503, 504]
    statuses: set[int] = set()
    for raw_entry in list(raw_values):
        parts = [segment.strip() for segment in str(raw_entry or "").split(",") if segment.strip()]
        for part in parts:
            value = int(part)
            if value < 100 or value > 599:
                raise ValueError(f"invalid_retry_http_status:{value}")
            statuses.add(value)
    return sorted(statuses)


def _compute_retry_backoff(
    *,
    retry_index: int,
    base_seconds: float,
    multiplier: float,
    max_seconds: float,
) -> float:
    base = max(0.0, float(base_seconds))
    mult = max(0.000001, float(multiplier))
    cap = max(0.0, float(max_seconds))
    delay = base * (mult ** max(0, int(retry_index)))
    if cap > 0.0:
        delay = min(delay, cap)
    return max(0.0, float(delay))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    markdown_path = args.markdown_path.expanduser().resolve()
    if not markdown_path.exists():
        raise SystemExit(f"bridge_markdown_not_found:{markdown_path}")
    if not markdown_path.is_file():
        raise SystemExit(f"bridge_markdown_not_file:{markdown_path}")

    markdown_text = markdown_path.read_text(encoding="utf-8")
    payload_body = {"text": markdown_text}
    request_body = json.dumps(payload_body).encode("utf-8")

    webhook_url = str(args.webhook_url or "").strip()
    has_webhook = bool(webhook_url)
    dry_run = bool(args.dry_run)
    dry_run_output_mode = str(args.dry_run_output_mode or "full")
    skipped = bool(not has_webhook)
    posted = False
    http_status: int | None = None
    retry_attempts = max(0, int(args.retry_attempts))
    max_attempts = 1 + retry_attempts
    timeout_seconds = max(0.1, float(args.timeout_seconds))
    retry_backoff_seconds = max(0.0, float(args.retry_backoff_seconds))
    retry_backoff_multiplier = max(0.000001, float(args.retry_backoff_multiplier))
    retry_max_backoff_seconds = max(0.0, float(args.retry_max_backoff_seconds))
    retry_jitter_seconds = max(0.0, float(args.retry_jitter_seconds))
    retry_jitter_seed = (
        None if args.retry_jitter_seed is None else int(args.retry_jitter_seed)
    )
    jitter_rng = random.Random(retry_jitter_seed)
    retry_on_http_status = _parse_retry_http_statuses(
        list(args.retry_on_http_status or [])
    )
    error_body_preview_chars = max(0, int(args.error_body_preview_chars))
    retry_diagnostics_mode = str(args.retry_diagnostics_mode or "full")
    attempts: list[dict] = []

    result = {
        "status": "ok",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "dry_run_output_mode": dry_run_output_mode,
        "markdown_path": str(markdown_path),
        "markdown_chars": int(len(markdown_text)),
        "payload_bytes": int(len(request_body)),
        "payload_preview": _build_preview(markdown_text, preview_chars=int(args.preview_chars)),
        "webhook_target": _sanitize_webhook_target(webhook_url),
        "would_post": bool(has_webhook),
        "posted": False,
        "skipped": skipped,
        "skip_reason": "webhook_url_missing" if skipped else None,
        "http_status": None,
        "retry_policy": {
            "retry_attempts": retry_attempts,
            "max_attempts": max_attempts,
            "backoff_seconds": retry_backoff_seconds,
            "backoff_multiplier": retry_backoff_multiplier,
            "max_backoff_seconds": retry_max_backoff_seconds,
            "jitter_seconds": retry_jitter_seconds,
            "jitter_seed": retry_jitter_seed,
            "retry_on_http_status": retry_on_http_status,
        },
        "retry_diagnostics_mode": retry_diagnostics_mode,
        "error_body_preview_chars": error_body_preview_chars,
        "attempt_count": 0,
        "retries_attempted": 0,
        "retry_scheduled_count": 0,
        "attempts": attempts,
        "first_attempt_started_at": None,
        "last_attempt_finished_at": None,
        "last_error_body_preview": None,
    }

    if not skipped and not dry_run:
        last_error: str | None = None
        last_error_body_preview: str | None = None
        for attempt_index in range(max_attempts):
            attempt_number = int(attempt_index + 1)
            started_dt = datetime.now(timezone.utc)
            started_at = started_dt.isoformat()
            started_monotonic = time.perf_counter()
            req = request.Request(
                webhook_url,
                data=request_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            attempt_http_status: int | None = None
            attempt_error: str | None = None
            attempt_error_body_preview: str | None = None
            retryable = False
            success = False
            try:
                with request.urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310
                    attempt_http_status = int(getattr(resp, "status", 200))
                if attempt_http_status is not None and attempt_http_status >= 400:
                    attempt_error = f"webhook_http_error:{attempt_http_status}"
                    retryable = bool(attempt_http_status in retry_on_http_status)
                else:
                    success = True
            except error.HTTPError as exc:
                attempt_http_status = int(getattr(exc, "code", 500) or 500)
                attempt_error = f"webhook_http_error:{attempt_http_status}"
                retryable = bool(attempt_http_status in retry_on_http_status)
                if error_body_preview_chars > 0:
                    try:
                        raw_body = exc.read()
                    except Exception:  # pragma: no cover - defensive surface
                        raw_body = b""
                    if raw_body:
                        decoded = raw_body.decode("utf-8", errors="replace")
                        attempt_error_body_preview = _build_preview(
                            decoded,
                            preview_chars=error_body_preview_chars,
                        )
            except Exception as exc:  # pragma: no cover - defensive surface
                attempt_error = f"webhook_request_failed:{exc}"
                retryable = True

            should_retry = bool((not success) and retryable and attempt_number < max_attempts)
            base_backoff_seconds = 0.0
            jitter_seconds = 0.0
            backoff_seconds = 0.0
            if should_retry:
                base_backoff_seconds = _compute_retry_backoff(
                    retry_index=attempt_index,
                    base_seconds=retry_backoff_seconds,
                    multiplier=retry_backoff_multiplier,
                    max_seconds=retry_max_backoff_seconds,
                )
                if retry_jitter_seconds > 0.0:
                    jitter_seconds = float(
                        jitter_rng.uniform(0.0, retry_jitter_seconds)
                    )
                backoff_seconds = max(
                    0.0, float(base_backoff_seconds + jitter_seconds)
                )
            finished_dt = datetime.now(timezone.utc)
            finished_at = finished_dt.isoformat()
            elapsed_ms = max(0.0, float((time.perf_counter() - started_monotonic) * 1000.0))
            next_attempt_at = None
            if should_retry and backoff_seconds > 0.0:
                next_attempt_at = (
                    finished_dt + timedelta(seconds=float(backoff_seconds))
                ).isoformat()

            attempts.append(
                {
                    "attempt_number": attempt_number,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "elapsed_ms": elapsed_ms,
                    "next_attempt_at": next_attempt_at,
                    "http_status": attempt_http_status,
                    "error": attempt_error,
                    "error_body_preview": attempt_error_body_preview,
                    "success": success,
                    "retryable": retryable,
                    "will_retry": should_retry,
                    "base_backoff_seconds": base_backoff_seconds,
                    "jitter_seconds": jitter_seconds,
                    "backoff_seconds": backoff_seconds,
                }
            )

            if success:
                posted = True
                http_status = attempt_http_status
                break

            last_error = attempt_error
            last_error_body_preview = attempt_error_body_preview
            http_status = attempt_http_status
            if not should_retry:
                result["status"] = "error"
                result["posted"] = False
                result["http_status"] = http_status
                result["error"] = str(last_error or "webhook_request_failed:unknown")
                result["last_error_body_preview"] = last_error_body_preview
                break
            if backoff_seconds > 0.0:
                time.sleep(backoff_seconds)

    result["posted"] = posted
    result["http_status"] = http_status
    result["attempt_count"] = int(len(attempts))
    result["retries_attempted"] = max(0, int(len(attempts) - 1))
    result["retry_scheduled_count"] = int(
        sum(1 for attempt in attempts if bool(attempt.get("will_retry")))
    )
    if attempts:
        result["first_attempt_started_at"] = str(attempts[0].get("started_at") or "")
        result["last_attempt_finished_at"] = str(attempts[-1].get("finished_at") or "")
    if retry_diagnostics_mode == "minimal":
        minimal_attempts: list[dict] = []
        for attempt in attempts:
            minimal_attempts.append(
                {
                    "attempt_number": int(attempt.get("attempt_number") or 0),
                    "http_status": attempt.get("http_status"),
                    "error": attempt.get("error"),
                    "success": bool(attempt.get("success")),
                    "will_retry": bool(attempt.get("will_retry")),
                }
            )
        result["attempts"] = minimal_attempts
    if dry_run and dry_run_output_mode == "preview_only":
        preview_result: dict[str, object] = {
            "status": str(result.get("status") or "ok"),
            "generated_at": str(result.get("generated_at") or ""),
            "dry_run": bool(result.get("dry_run")),
            "dry_run_output_mode": str(result.get("dry_run_output_mode") or "preview_only"),
            "markdown_path": str(result.get("markdown_path") or ""),
            "markdown_chars": int(result.get("markdown_chars") or 0),
            "payload_preview": str(result.get("payload_preview") or ""),
            "webhook_target": str(result.get("webhook_target") or ""),
            "would_post": bool(result.get("would_post")),
            "posted": bool(result.get("posted")),
            "skipped": bool(result.get("skipped")),
            "skip_reason": result.get("skip_reason"),
        }
        if "error" in result:
            preview_result["error"] = str(result.get("error") or "")
        _emit(preview_result, compact=bool(args.json_compact))
        return 1 if str(result.get("status") or "ok") == "error" else 0
    if bool(result.get("status") == "error"):
        _emit(result, compact=bool(args.json_compact))
        return 1
    _emit(result, compact=bool(args.json_compact))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
