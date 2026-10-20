from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from draino import node_agent_common
from draino.node_agent_app import node_agent_app


@pytest.fixture()
def agent_client(monkeypatch, tmp_path):
    token = tmp_path / "token"
    token.write_text("sekret\n", encoding="utf-8")
    monkeypatch.setenv("DRAINO_NODE_AGENT_TOKEN_FILE", str(token))
    monkeypatch.setenv("DRAINO_NODE_NAME", "node-1")
    monkeypatch.setattr(node_agent_common, "_reboot_in_progress", False)
    node_agent_common._recent_request_ids.clear()
    return TestClient(node_agent_app)


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer sekret"}


def _start_reboot(client, monkeypatch, request_id="req-1"):
    monkeypatch.setattr(node_agent_common.threading, "Thread", lambda target=None, daemon=None: SimpleNamespaceStart())
    return client.post(
        "/reboot",
        json={"request_id": request_id, "expected_node": "node-1", "requested_by": "alice", "reason": "kernel patch"},
        headers=_headers(),
    )


class SimpleNamespaceStart:
    def start(self):
        pass


def test_reboot_accepts_and_audits_operator_identity(agent_client, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="draino.node_agent.audit")

    response = _start_reboot(agent_client, monkeypatch)

    assert response.status_code == 202
    assert response.json()["accepted"] is True
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("reboot_audit")]
    assert len(lines) == 1
    record = json.loads(lines[0].split(" ", 1)[1])
    assert record["event"] == "accepted"
    assert record["requested_by"] == "alice"
    assert record["reason"] == "kernel patch"
    assert record["node"] == "node-1"
    assert record["request_id"] == "req-1"


def test_reboot_rejects_replayed_request_id(agent_client, monkeypatch):
    first = _start_reboot(agent_client, monkeypatch, request_id="req-dup")
    assert first.status_code == 202
    node_agent_common._reboot_in_progress = False

    second = _start_reboot(agent_client, monkeypatch, request_id="req-dup")

    assert second.status_code == 409
    assert "already processed" in second.json()["detail"]


def test_reboot_unauthorised_is_audited(agent_client, caplog):
    caplog.set_level(logging.INFO, logger="draino.node_agent.audit")

    response = agent_client.post("/reboot", json={"request_id": "r"}, headers={"Authorization": "Bearer wrong"})

    assert response.status_code == 401
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("reboot_audit")]
    record = json.loads(lines[0].split(" ", 1)[1])
    assert record["event"] == "rejected"
    assert record["detail"] == "unauthorised"


def test_reboot_expected_node_mismatch_is_audited(agent_client, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="draino.node_agent.audit")

    response = agent_client.post(
        "/reboot",
        json={"request_id": "r", "expected_node": "other-node"},
        headers=_headers(),
    )

    assert response.status_code == 409
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("reboot_audit")]
    record = json.loads(lines[0].split(" ", 1)[1])
    assert record["event"] == "rejected"
    assert "mismatch" in record["detail"]


def test_authorise_survives_unreadable_token_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DRAINO_NODE_AGENT_TOKEN_FILE", str(tmp_path / "missing-token"))
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        node_agent_common._authorise("Bearer whatever")
    assert exc_info.value.status_code == 503
