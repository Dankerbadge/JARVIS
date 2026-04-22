from __future__ import annotations

import os
import subprocess
from typing import Any

from .providers.github import GitHubReviewClient
from .review_service import ReviewService


def build_default_review_service() -> ReviewService:
    providers: dict[str, Any] = {}
    github_token = str(
        os.getenv("JARVIS_GITHUB_TOKEN")
        or os.getenv("GITHUB_TOKEN")
        or ""
    ).strip()
    if not github_token:
        # Fallback to GitHub CLI auth so local runs work even when shell envs are not loaded.
        try:
            completed = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode == 0:
                github_token = str(completed.stdout or "").strip()
        except OSError:
            github_token = ""
    if github_token:
        providers["github"] = GitHubReviewClient(
            token=github_token,
            api_base=os.getenv("JARVIS_GITHUB_API_BASE", "https://api.github.com"),
        )
    return ReviewService(providers)

