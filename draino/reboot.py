"""Reboot helpers."""
from __future__ import annotations

from typing import Callable

from . import node_agent_client
from .models import NodePhase, NodeState

LogFn = Callable[[str], None]


def is_ready_for_reboot(state: NodeState) -> tuple[bool, str]:
    if state.phase in (NodePhase.RUNNING, NodePhase.REBOOTING, NodePhase.UNDRAINING):
        return False, "Cannot reboot while an operation is in progress."

    if state.effective_k8s_drain_status() != "drained":
        return False, "Kubernetes drain must be completed before reboot."

    if not state.is_compute:
        return True, ""

    if state.effective_nova_evacuation_status() != "evacuated":
        return False, "Nova evacuation must be completed before reboot."

    return True, ""


def issue_reboot(state: NodeState, log: LogFn) -> None:
    body = node_agent_client.post_reboot(state.k8s_name, state.hypervisor)
    if not body.get("accepted"):
        raise RuntimeError("node-agent rejected the reboot request")
    log(f"Reboot command accepted by node-agent for '{state.k8s_name}'")
