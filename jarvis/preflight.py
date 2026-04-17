from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Iterable, Sequence


def _trim(text: str, max_chars: int = 400) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


@dataclass(frozen=True)
class CheckResult:
    name: str
    command: Sequence[str]
    passed: bool
    return_code: int
    duration_seconds: float
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""


@dataclass(frozen=True)
class PreflightReport:
    working_dir: str
    checks: Sequence[CheckResult] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def summarize(self) -> str:
        if not self.checks:
            return "No checks were run."
        return "; ".join(
            f"{check.name}={'PASS' if check.passed else 'FAIL'}" for check in self.checks
        )


class PreflightRunner:
    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        *,
        working_dir: str,
        checks: Iterable[tuple[str, Sequence[str]]],
    ) -> PreflightReport:
        results: list[CheckResult] = []
        for name, command in checks:
            start = time.monotonic()
            try:
                completed = subprocess.run(
                    command,
                    cwd=working_dir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
                passed = completed.returncode == 0
                stdout_excerpt = _trim((completed.stdout or "").strip())
                stderr_excerpt = _trim((completed.stderr or "").strip())
                return_code = completed.returncode
            except subprocess.TimeoutExpired as exc:
                passed = False
                stdout_excerpt = _trim((exc.stdout or "").strip())
                stderr_excerpt = _trim((exc.stderr or "").strip() or "Timed out.")
                return_code = 124
            duration = time.monotonic() - start
            results.append(
                CheckResult(
                    name=name,
                    command=tuple(command),
                    passed=passed,
                    return_code=return_code,
                    duration_seconds=duration,
                    stdout_excerpt=stdout_excerpt,
                    stderr_excerpt=stderr_excerpt,
                )
            )
        return PreflightReport(working_dir=working_dir, checks=tuple(results))

