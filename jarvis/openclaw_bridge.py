from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


_DEFAULT_DENY_LIST = {
    "exec",
    "shell",
    "fs_write",
    "apply_patch",
    "session.orchestrate",
    "session.spawn",
}


class OpenClawBridgeError(RuntimeError):
    pass


def _is_private_or_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().lower()
    if not normalized:
        return False
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(normalized)
        return bool(ip.is_private or ip.is_loopback)
    except ValueError:
        return normalized.endswith(".local")


class OpenClawToolsInvokeClient:
    """Minimal /tools/invoke bridge with deny-list and private-network defaults."""

    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str,
        timeout_seconds: float = 10.0,
        allowed_tools: list[str] | None = None,
        deny_list: list[str] | None = None,
        allow_remote: bool = False,
        requester: Any | None = None,
    ) -> None:
        parsed = urllib.parse.urlparse(str(base_url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute http(s) URL.")
        host = str(parsed.hostname or "")
        if not allow_remote and not _is_private_or_loopback_host(host):
            raise ValueError("OpenClaw bridge requires private/loopback host unless allow_remote=True.")
        token = str(bearer_token or "").strip()
        if not token:
            raise ValueError("bearer_token is required.")
        self.base_url = parsed.geturl().rstrip("/")
        self.bearer_token = token
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.allowed_tools = {str(item).strip() for item in (allowed_tools or []) if str(item).strip()}
        deny_base = set(_DEFAULT_DENY_LIST)
        deny_base.update(str(item).strip() for item in (deny_list or []) if str(item).strip())
        self.deny_list = deny_base
        self.requester = requester or urllib.request.urlopen

    def _check_tool(self, tool: str) -> None:
        normalized = str(tool or "").strip()
        if not normalized:
            raise OpenClawBridgeError("tool is required.")
        if normalized in self.deny_list:
            raise OpenClawBridgeError(f"tool denied by policy: {normalized}")
        if self.allowed_tools and normalized not in self.allowed_tools:
            raise OpenClawBridgeError(f"tool is not in allow-list: {normalized}")

    def invoke(
        self,
        *,
        tool: str,
        args: dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> dict[str, Any]:
        self._check_tool(tool)
        body: dict[str, Any] = {
            "tool": str(tool),
            "args": dict(args or {}),
        }
        if session_key:
            body["sessionKey"] = str(session_key)
        encoded = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/tools/invoke",
            data=encoded,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "JARVIS/0.1",
            },
        )
        try:
            with self.requester(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                parsed = json.loads(raw) if raw else {}
                if not isinstance(parsed, dict):
                    raise OpenClawBridgeError("OpenClaw /tools/invoke returned non-object payload.")
                return parsed
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8") if hasattr(exc, "read") else ""
            raise OpenClawBridgeError(f"/tools/invoke failed [{exc.code}]: {detail}") from exc
