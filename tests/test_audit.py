from __future__ import annotations

import json

from draino.audit import AuditLogger


def test_audit_logger_writes_jsonl_entry(tmp_path):
    log_path = tmp_path / "audit.log"
    logger = AuditLogger(path=log_path)

    logger.log("evacuation", "node-1", "started", user="alice", session_id="sess-1")
    logger.log("reboot", "node-1", "completed", "downtime=42s", user="alice", session_id="sess-1")

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    second = json.loads(lines[1])

    assert first["action"] == "evacuation"
    assert first["node"] == "node-1"
    assert first["event"] == "started"
    assert "detail" not in first
    assert second["detail"] == "downtime=42s"
    assert first["session_id"] == second["session_id"]


def test_audit_logger_records_operator_identity_per_entry(tmp_path):
    log_path = tmp_path / "audit.log"
    logger = AuditLogger(path=log_path)

    logger.log("reboot", "node-1", "started", user="alice", session_id="sess-a")
    logger.log("reboot", "node-1", "started", user="bob", session_id="sess-b")

    first, second = (json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines())

    assert first["user"] == "alice"
    assert second["user"] == "bob"
    assert first["session_id"] != second["session_id"]


def test_audit_logger_falls_back_to_unknown_for_blank_identity(tmp_path):
    log_path = tmp_path / "audit.log"
    logger = AuditLogger(path=log_path)

    logger.log("session", "-", "started", user="", session_id="")

    entry = json.loads(log_path.read_text(encoding="utf-8"))
    assert entry["user"] == "unknown"
    assert entry["session_id"] == "unknown"
