from __future__ import annotations

from draino import node_agent_client, reboot
from draino.models import NodeState


def test_compute_node_requires_full_evacuation_before_reboot():
    state = NodeState(
        k8s_name="node-1",
        hypervisor="hv-1",
        is_compute=True,
        k8s_cordoned=True,
        compute_status="disabled",
        vm_count=1,
        amphora_count=0,
    )
    state.k8s_drain_status = "drained"

    ready, detail = reboot.is_ready_for_reboot(state)

    assert ready is False
    assert "Nova evacuation" in detail


def test_non_compute_node_can_reboot_after_cordon():
    state = NodeState(
        k8s_name="node-2",
        hypervisor="hv-2",
        is_compute=False,
        k8s_cordoned=True,
    )
    state.k8s_drain_status = "drained"

    ready, detail = reboot.is_ready_for_reboot(state)

    assert ready is True
    assert detail == ""


def test_blocked_nova_evacuation_status_is_preserved_with_instances_remaining():
    state = NodeState(
        k8s_name="node-3",
        hypervisor="hv-3",
        is_compute=True,
        compute_status="disabled",
        vm_count=2,
        amphora_count=0,
    )
    state.nova_evacuation_status = "blocked"

    assert state.effective_nova_evacuation_status() == "blocked"


def test_disabled_nova_evacuation_status_is_explicit_when_instances_remain():
    state = NodeState(
        k8s_name="node-4",
        hypervisor="hv-4",
        is_compute=True,
        compute_status="disabled",
        vm_count=3,
        amphora_count=1,
    )

    assert state.effective_nova_evacuation_status() == "disabled"


def test_issue_reboot_uses_node_agent(monkeypatch):
    monkeypatch.setattr(node_agent_client, "enabled", lambda: True)
    monkeypatch.setattr(
        node_agent_client,
        "post_reboot",
        lambda node_name, hypervisor, requested_by=None, reason=None: {"accepted": True, "node": node_name, "hypervisor": hypervisor, "requested_by": requested_by},
    )

    state = NodeState(k8s_name="node-1", hypervisor="hv-1")
    log_messages: list[str] = []
    reboot.issue_reboot(state, log_messages.append)

    assert "accepted by node-agent" in log_messages[0]


def test_issue_reboot_forwards_operator_identity(monkeypatch):
    monkeypatch.setattr(node_agent_client, "enabled", lambda: True)
    captured: dict = {}

    def _capture(node_name, hypervisor, requested_by=None, reason=None):
        captured.update(node=node_name, requested_by=requested_by, reason=reason)
        return {"accepted": True}

    monkeypatch.setattr(node_agent_client, "post_reboot", _capture)
    state = NodeState(k8s_name="node-1", hypervisor="hv-1")

    reboot.issue_reboot(state, lambda msg: None, requested_by="alice", reason="kernel patch")

    assert captured == {"node": "node-1", "requested_by": "alice", "reason": "kernel patch"}
