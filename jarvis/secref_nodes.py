from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class SecretRefError(ValueError):
    pass


@dataclass(frozen=True)
class SecretRef:
    kind: str
    value: str

    def as_ref(self) -> str:
        return f"{self.kind}:{self.value}"


def parse_secret_ref(raw: str | dict[str, Any]) -> SecretRef:
    if isinstance(raw, dict):
        kind = str(raw.get("kind") or "").strip().lower()
        value = str(raw.get("value") or "").strip()
    else:
        text = str(raw or "").strip()
        if ":" not in text:
            raise SecretRefError("secret ref must be in '<kind>:<value>' format.")
        kind, value = text.split(":", 1)
        kind = kind.strip().lower()
        value = value.strip()
    if kind not in {"env", "file"}:
        raise SecretRefError(f"unsupported secret ref kind: {kind}")
    if not value:
        raise SecretRefError("secret ref value is required.")
    return SecretRef(kind=kind, value=value)


def validate_secret_ref(
    ref: SecretRef,
    *,
    allowed_file_roots: list[str | Path] | None = None,
) -> None:
    if ref.kind == "env":
        if not _ENV_NAME_RE.match(ref.value):
            raise SecretRefError(f"invalid env var name: {ref.value}")
        return

    if ref.kind == "file":
        path = Path(ref.value).expanduser()
        if not path.is_absolute():
            raise SecretRefError("file secret refs must use absolute paths.")
        if not path.exists() or not path.is_file():
            raise SecretRefError(f"file secret ref does not exist: {path}")
        if allowed_file_roots:
            resolved = path.resolve()
            roots = [Path(item).expanduser().resolve() for item in allowed_file_roots]
            if not any(str(resolved).startswith(str(root) + os.sep) or resolved == root for root in roots):
                raise SecretRefError(f"file secret ref not under allowed roots: {path}")
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & stat.S_IRWXG or mode & stat.S_IRWXO:
            raise SecretRefError(
                f"file secret permissions too broad: {path} (mode={oct(mode)})"
            )
        return

    raise SecretRefError(f"unsupported secret ref kind: {ref.kind}")


def resolve_secret_ref(ref: SecretRef) -> str:
    if ref.kind == "env":
        value = str(os.getenv(ref.value) or "")
        if not value:
            raise SecretRefError(f"env secret is empty or missing: {ref.value}")
        return value
    if ref.kind == "file":
        path = Path(ref.value).expanduser()
        if not path.exists():
            raise SecretRefError(f"file secret ref does not exist: {path}")
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise SecretRefError(f"file secret is empty: {path}")
        return value
    raise SecretRefError(f"unsupported secret ref kind: {ref.kind}")


def validate_node_secret_plan(
    plan: dict[str, Any],
    *,
    allowed_file_roots: list[str | Path] | None = None,
) -> dict[str, Any]:
    node_id = str(plan.get("node_id") or "").strip()
    device_id = str(plan.get("device_id") or "").strip()
    owner_id = str(plan.get("owner_id") or "").strip()
    if not node_id or not device_id or not owner_id:
        raise SecretRefError("node_id, device_id, and owner_id are required.")

    gateway_ref = parse_secret_ref(plan.get("gateway_token_ref") or "")
    node_ref = parse_secret_ref(plan.get("node_token_ref") or "")
    validate_secret_ref(gateway_ref, allowed_file_roots=allowed_file_roots)
    validate_secret_ref(node_ref, allowed_file_roots=allowed_file_roots)
    metadata = plan.get("metadata")
    normalized_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    return {
        "node_id": node_id,
        "device_id": device_id,
        "owner_id": owner_id,
        "gateway_token_ref": gateway_ref.as_ref(),
        "node_token_ref": node_ref.as_ref(),
        "pairing_status": str(plan.get("pairing_status") or "paired").strip().lower() or "paired",
        "metadata": normalized_metadata,
    }
