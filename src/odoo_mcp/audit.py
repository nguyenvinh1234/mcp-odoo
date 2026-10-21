"""Append-only JSONL audit trail for write-path events.

Opt-in via ODOO_MCP_AUDIT_LOG=<path>. One line per event so operators can
answer "what did the agent change, when, and was it approved?" without
trusting chat transcripts. Tokens are stored as truncated SHA-256 digests,
never in clear text.

The writer profile checks the return value before prepare/execute and therefore
fails closed when this sink is unavailable. Other profiles remain fail-open so
a read query is not converted into an outage. Failures emit a structured
``SECURITY_ALERT`` error for external monitoring.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

AUDIT_LOG_ENV = "ODOO_MCP_AUDIT_LOG"
AUDIT_MAX_BYTES_ENV = "ODOO_MCP_AUDIT_MAX_BYTES"
AUDIT_BACKUP_COUNT_ENV = "ODOO_MCP_AUDIT_BACKUP_COUNT"
DEFAULT_AUDIT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_AUDIT_BACKUP_COUNT = 14
_write_lock = threading.Lock()


def audit_log_path() -> str | None:
    """Return the configured audit log path, or None when disabled."""
    path = os.environ.get(AUDIT_LOG_ENV, "").strip()
    return path or None


def audit_posture() -> dict[str, Any]:
    """Non-secret audit posture for health_check / runtime_security_report."""
    path = audit_log_path()
    return {
        "enabled": path is not None,
        "path": path,
        "env": AUDIT_LOG_ENV,
        "max_bytes": _positive_int_env(AUDIT_MAX_BYTES_ENV, DEFAULT_AUDIT_MAX_BYTES),
        "backup_count": _positive_int_env(
            AUDIT_BACKUP_COUNT_ENV, DEFAULT_AUDIT_BACKUP_COUNT
        ),
        "file_mode": "0600",
    }


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def _rotate_if_needed(path: str, incoming_bytes: int) -> None:
    max_bytes = _positive_int_env(AUDIT_MAX_BYTES_ENV, DEFAULT_AUDIT_MAX_BYTES)
    backup_count = _positive_int_env(
        AUDIT_BACKUP_COUNT_ENV, DEFAULT_AUDIT_BACKUP_COUNT
    )
    try:
        current_size = os.path.getsize(path)
    except FileNotFoundError:
        current_size = 0
    if current_size + incoming_bytes <= max_bytes:
        return
    oldest = f"{path}.{backup_count}"
    if os.path.exists(oldest):
        os.unlink(oldest)
    for index in range(backup_count - 1, 0, -1):
        source = f"{path}.{index}"
        if os.path.exists(source):
            os.replace(source, f"{path}.{index + 1}")
    if os.path.exists(path):
        os.replace(path, f"{path}.1")


def _append_line(path: str, line: str) -> bool:
    encoded_size = len(line.encode("utf-8")) + 1
    try:
        with _write_lock:
            _rotate_if_needed(path, encoded_size)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            os.chmod(path, 0o600)
        return True
    except OSError as exc:
        logger.error(
            "SECURITY_ALERT audit log unavailable (%s): %s",
            path,
            exc,
            extra={"security_event": "audit_unavailable"},
        )
        return False


def _token_digest(token: str | None) -> str | None:
    if not token:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def record_write_event(
    event: str,
    *,
    outcome: str,
    model: str | None = None,
    operation: str | None = None,
    record_ids: list[int] | None = None,
    instance: str | None = None,
    token: str | None = None,
    detail: str | None = None,
) -> bool:
    """Append one audit line; returns True when a line was written."""
    path = audit_log_path()
    if path is None:
        return False
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "outcome": outcome,
        "model": model,
        "operation": operation,
        "record_ids": list(record_ids or []),
        "instance": instance or "default",
        "token_sha256": _token_digest(token),
        "detail": detail,
    }
    line = json.dumps(entry, sort_keys=True, default=str)
    return _append_line(path, line)


def record_gateway_event(
    event: str,
    *,
    outcome: str,
    client_id: str | None,
    tool: str,
    request_id: str | None = None,
    instance: str | None = None,
    record_ids: list[int] | None = None,
    result_count: int | None = None,
    duration_ms: int | None = None,
    error_code: str | None = None,
) -> bool:
    """Append redacted query metadata without arguments or business payloads."""
    path = audit_log_path()
    if path is None:
        return False
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "outcome": outcome,
        "client_id": client_id,
        "tool": tool,
        "request_id": request_id,
        "instance": instance,
        "record_ids": [int(value) for value in (record_ids or [])[:200]],
        "result_count": result_count,
        "duration_ms": duration_ms,
        "error_code": error_code,
    }
    line = json.dumps(entry, sort_keys=True, default=str)
    return _append_line(path, line)
