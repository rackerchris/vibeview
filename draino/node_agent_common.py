"""Shared node-agent auth, shell, and reboot state helpers."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone

from fastapi import HTTPException, status
from pydantic import BaseModel

_state_lock = threading.Lock()
_reboot_in_progress = False
_LOGGER = logging.getLogger("draino.node_agent")
_AUDIT_LOGGER = logging.getLogger("draino.node_agent.audit")

# Replay/duplicate protection: request ids seen (with timestamp) so a retried
# or replayed POST /reboot with the same request_id is not re-issued after the
# first one completed.
_recent_request_ids: dict[str, float] = {}
_REQUEST_ID_TTL_SECONDS = 3600.0


class RebootRequest(BaseModel):
    request_id: str
    expected_node: str | None = None
    hypervisor: str | None = None
    requested_by: str | None = None
    reason: str | None = None


class RebootAuditEvent(BaseModel):
    """Structured, one-line audit record written by the agent for every
    reboot decision.  Correlates with VibeView's workflow audit log via
    request_id and carries the end-user identity forwarded by VibeView."""

    event: str  # accepted | rejected
    request_id: str
    node: str
    requested_by: str = "unknown"
    reason: str = ""
    detail: str = ""


def audit_reboot(event: RebootAuditEvent) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event.event,
        "request_id": event.request_id,
        "node": event.node,
        "requested_by": event.requested_by or "unknown",
        "reason": (event.reason or "")[:200],
        "detail": (event.detail or "")[:200],
    }
    _AUDIT_LOGGER.info("reboot_audit %s", json.dumps(record, sort_keys=True))


def remember_request_id(request_id: str) -> bool:
    """Record a request id; return False if it was already seen inside the TTL."""
    now = time.monotonic()
    with _state_lock:
        expired = [key for key, ts in _recent_request_ids.items() if now - ts > _REQUEST_ID_TTL_SECONDS]
        for key in expired:
            del _recent_request_ids[key]
        if request_id in _recent_request_ids:
            return False
        _recent_request_ids[request_id] = now
        return True


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def _read_token() -> str:
    try:
        with open(_env("DRAINO_NODE_AGENT_TOKEN_FILE"), "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError as exc:
        # A missing/unreadable token file must surface as an auth failure
        # (and an operator-visible 401), not an unhandled 500.
        _LOGGER.error("node-agent token unreadable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="node-agent unauthenticated: token unavailable",
        ) from exc


def _node_name() -> str:
    return _env("DRAINO_NODE_NAME")


def _authorise(authorization: str | None) -> None:
    expected = f"Bearer {_read_token()}"
    if authorization != expected:
        _LOGGER.warning("unauthorised request node=%s", os.getenv("DRAINO_NODE_NAME", "unknown"))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorised",
        )


def _reboot_host() -> None:
    global _reboot_in_progress
    try:
        _LOGGER.info("reboot command starting node=%s", _node_name())
        subprocess.run(
            ["nsenter", "--target", "1", "--mount", "--uts", "--ipc", "--net", "--pid", "reboot"],
            timeout=15,
            capture_output=True,
            check=False,
        )
    finally:
        _LOGGER.info("reboot command finished node=%s", _node_name())
        with _state_lock:
            _reboot_in_progress = False


def _run_host_shell(script: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["nsenter", "--target", "1", "--mount", "--uts", "--ipc", "--net", "--pid", "sh", "-lc", script],
        timeout=timeout,
        capture_output=True,
        text=True,
        check=False,
    )
