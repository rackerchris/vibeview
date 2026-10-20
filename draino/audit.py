"""Compliance audit logger — writes structured JSONL entries to a file."""
from __future__ import annotations

import json
import logging
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DEFAULT_PATH = Path.home() / ".draino" / "audit.log"
_UNKNOWN = "unknown"
_LOGGER = logging.getLogger("draino.audit")


class AuditLogger:
    """Thread-safe compliance audit logger.

    Writes one JSON object per line (JSONL) to *path*.  Each entry has:

        timestamp   ISO-8601 UTC time of the event
        user        authenticated operator identity (required per entry)
        hostname    Machine running draino
        session_id  caller-supplied session identifier (required per entry)
        action      evacuation | enable_nova | drain_quick | undrain | reboot | session
        node        Kubernetes node name
        event       started | completed | failed | blocked | cancelled | abandoned
        detail      Free-form context string (step detail, error text, etc.)
    """

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self._path = Path(path) if path else _DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._machine = socket.gethostname()

    # ── Public API ────────────────────────────────────────────────────────────

    def log(
        self,
        action: str,
        node: str,
        event: str,
        detail: str = "",
        *,
        user: str,
        session_id: str,
    ) -> None:
        """Append one audit entry to the log file (thread-safe).

        ``user`` and ``session_id`` are mandatory: every entry must carry the
        authenticated operator identity and the login session that performed
        the action, otherwise the log cannot attribute operator actions.
        """
        entry: dict = {
            "timestamp":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "user":       user or _UNKNOWN,
            "hostname":   self._machine,
            "session_id": session_id or _UNKNOWN,
            "action":     action,
            "node":       node,
            "event":      event,
        }
        if detail:
            entry["detail"] = detail
        line = json.dumps(entry, separators=(",", ":")) + "\n"
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        _LOGGER.info(line.rstrip("\n"))

    @property
    def path(self) -> Path:
        return self._path
