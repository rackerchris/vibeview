from __future__ import annotations

import json
from types import SimpleNamespace

from draino.models import NodePhase, NodeState
from draino.web import inventory_actions
from draino.web import server as web_server


def test_refresh_marks_missing_node_agents(monkeypatch, tmp_path):
    server = web_server.DrainoServer(
        k8s_auth=None,
        openstack_auth=None,
        role_names=["admin"],
        audit_log=str(tmp_path / "audit.log"),
    )
    nodes = [
        {"name": "node-a", "hostname": "hv-a", "ready": True, "cordoned": False, "taints": [], "kernel_version": None},
        {"name": "node-b", "hostname": "hv-b", "ready": True, "cordoned": False, "taints": [], "kernel_version": None},
    ]

    monkeypatch.setattr(web_server.node_agent_client, "get_ready_node_names", lambda: {"node-a"})
    monkeypatch.setattr(web_server.k8s_ops, "get_etcd_node_names", lambda auth=None: set())
    monkeypatch.setattr(web_server.k8s_ops, "get_node_host_signals", lambda node_name, hostname=None: {"kernel_version": None, "latest_kernel_version": None, "reboot_required": False})
    monkeypatch.setattr(web_server.k8s_ops, "get_ovn_edge_nodes", lambda auth=None: set())
    monkeypatch.setattr(web_server.openstack_ops, "get_all_host_summaries", lambda log_cb=None, auth=None: {})
    monkeypatch.setattr(server, "_push", lambda message: None)

    server._load_nodes_bg(cached_nodes=nodes, silent=True)

    assert server.node_states["node-a"].node_agent_ready is True
    assert server.node_states["node-b"].node_agent_ready is False


def test_reboot_request_requires_admin_role(tmp_path):
    server = web_server.DrainoServer(
        role_names=["member"],
        audit_log=str(tmp_path / "audit.log"),
    )
    state = NodeState(k8s_name="node-1", hypervisor="hv-1")
    server.node_states["node-1"] = state

    pushed: list[dict] = []
    server._push = pushed.append

    server.action_reboot_request("node-1")

    assert pushed == [{
        "type": "log",
        "node": "node-1",
        "message": "Reboot requires the OpenStack 'admin' role.",
        "color": "warn",
    }]


def test_reboot_request_requires_node_to_be_drained(tmp_path):
    server = web_server.DrainoServer(
        role_names=["admin"],
        audit_log=str(tmp_path / "audit.log"),
    )
    state = NodeState(
        k8s_name="node-1",
        hypervisor="hv-1",
        is_compute=True,
        k8s_cordoned=True,
        compute_status="disabled",
        vm_count=2,
        amphora_count=0,
    )
    state.k8s_drain_status = "drained"
    server.node_states["node-1"] = state

    pushed: list[dict] = []
    server._push = pushed.append

    server.action_reboot_request("node-1")

    assert pushed == [{
        "type": "log",
        "node": "node-1",
        "message": "Nova evacuation must be completed before reboot.",
        "color": "warn",
    }]


def test_enable_nova_reopens_compute_service_and_clears_evacuation_state(monkeypatch, tmp_path):
    server = web_server.DrainoServer(
        openstack_auth=object(),
        role_names=["admin"],
        audit_log=str(tmp_path / "audit.log"),
    )
    state = NodeState(
        k8s_name="node-1",
        hypervisor="hv-1",
        is_compute=True,
        compute_status="disabled",
        vm_count=2,
        amphora_count=1,
    )
    state.nova_evacuation_status = "blocked"
    state.nova_evacuation_detail = "nova disabled but not evacuated"
    state.instances = [object()]
    state.preflight_instances = [{"id": "vm-1"}]
    server.node_states["node-1"] = state

    pushed: list[dict] = []
    server._push = pushed.append
    class _ImmediateThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            if self._target:
                self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(inventory_actions.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(web_server.openstack_ops, "enable_compute_service", lambda hypervisor, log, auth=None: log(f"enabled {hypervisor}"))

    server.action_enable_nova("node-1")

    assert state.compute_status == "up"
    assert state.nova_evacuation_status == "clear"
    assert state.nova_evacuation_detail == "nova compute service enabled"
    assert state.instances == []
    assert state.preflight_instances == []
    assert any(msg["type"] == "state_update" and msg["node"] == "node-1" for msg in pushed)
    assert any(msg["type"] == "log" and msg["message"] == "Nova compute service re-enabled on node-1." for msg in pushed)


def test_enable_nova_logs_when_service_is_already_enabled(tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", is_compute=True, compute_status="up")
    server.node_states["node-1"] = state

    pushed: list[dict] = []
    server._push = pushed.append

    server.action_enable_nova("node-1")

    assert pushed == [{
        "type": "log",
        "node": "node-1",
        "message": "Nova compute service is already enabled.",
        "color": "dim",
    }]


def test_drain_logs_when_node_is_already_drained(tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", k8s_cordoned=True)
    state.k8s_drain_status = "drained"
    server.node_states["node-1"] = state

    pushed: list[dict] = []
    server._push = pushed.append

    server.action_drain_quick("node-1")

    assert pushed == [{
        "type": "log",
        "node": "node-1",
        "message": "Kubernetes drain already complete on node-1.",
        "color": "dim",
    }]


def test_undrain_logs_when_node_is_already_schedulable(tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", k8s_cordoned=False)
    state.k8s_drain_status = "clear"
    server.node_states["node-1"] = state

    pushed: list[dict] = []
    server._push = pushed.append

    server.action_undrain("node-1")

    assert pushed == [{
        "type": "log",
        "node": "node-1",
        "message": "Kubernetes scheduling is already enabled on node-1.",
        "color": "dim",
    }]


def test_evacuate_marks_host_complete_when_already_empty(tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    state = NodeState(
        k8s_name="node-1",
        hypervisor="hv-1",
        is_compute=True,
        compute_status="disabled",
        vm_count=0,
        amphora_count=0,
    )
    server.node_states["node-1"] = state

    pushed: list[dict] = []
    server._push = pushed.append

    server.action_evacuate("node-1")

    assert state.phase == NodePhase.IDLE
    assert state.nova_evacuation_status == "evacuated"
    assert state.nova_evacuation_detail == "compute service disabled and hypervisor already empty"
    assert pushed[0]["type"] == "state_update"
    assert pushed[1] == {
        "type": "log",
        "node": "node-1",
        "message": "Nova evacuation already complete on node-1.",
        "color": "dim",
    }


def test_load_nodes_bg_skips_host_signals_during_silent_refresh_until_ttl(monkeypatch, tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    nodes = [{"name": "node-1", "hostname": "hv-1", "ready": True, "cordoned": False}]

    signal_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(web_server.openstack_ops, "get_all_host_summaries", lambda log_cb=None, auth=None: {})
    monkeypatch.setattr(web_server.k8s_ops, "get_etcd_node_names", lambda auth=None: set())
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: signal_calls.append((node_name, hostname)) or {
            "kernel_version": "6.8.0",
            "latest_kernel_version": "6.8.12",
            "reboot_required": True,
        },
    )
    monkeypatch.setattr(server, "_push", lambda msg: None)

    server._load_nodes_bg(cached_nodes=nodes, silent=False)
    server._load_nodes_bg(cached_nodes=nodes, silent=True)

    assert signal_calls == [("node-1", "hv-1")]


def test_load_nodes_bg_refreshes_host_signals_again_after_ttl(monkeypatch, tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    nodes = [{"name": "node-1", "hostname": "hv-1", "ready": True, "cordoned": False}]

    signal_calls: list[tuple[str, str]] = []
    now_values = iter([1000.0, 1000.0 + web_server._HOST_SIGNALS_TTL + 1])

    monkeypatch.setattr(web_server.time, "time", lambda: next(now_values))
    monkeypatch.setattr(web_server.openstack_ops, "get_all_host_summaries", lambda log_cb=None, auth=None: {})
    monkeypatch.setattr(web_server.k8s_ops, "get_etcd_node_names", lambda auth=None: set())
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: signal_calls.append((node_name, hostname)) or {
            "kernel_version": "6.8.0",
            "latest_kernel_version": "6.8.12",
            "reboot_required": True,
        },
    )
    monkeypatch.setattr(server, "_push", lambda msg: None)

    server._load_nodes_bg(cached_nodes=nodes, silent=True)
    server._load_nodes_bg(cached_nodes=nodes, silent=True)

    assert signal_calls == [("node-1", "hv-1"), ("node-1", "hv-1")]


def test_load_nodes_bg_uses_state_updates_when_membership_is_unchanged(monkeypatch, tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    server.node_states["node-1"] = NodeState(k8s_name="node-1", hypervisor="hv-1")
    nodes = [{"name": "node-1", "hostname": "hv-1", "ready": True, "cordoned": False}]

    pushed: list[dict] = []

    monkeypatch.setattr(web_server.openstack_ops, "get_all_host_summaries", lambda log_cb=None, auth=None: {})
    monkeypatch.setattr(web_server.k8s_ops, "get_etcd_node_names", lambda auth=None: set())
    monkeypatch.setattr(web_server.k8s_ops, "get_mariadb_node_names", lambda auth=None: {"node-1"})
    monkeypatch.setattr(web_server.k8s_ops, "get_ovn_edge_nodes", lambda auth=None: {"node-1"})
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: {
            "kernel_version": "6.8.0",
            "latest_kernel_version": "6.8.12",
            "reboot_required": True,
        },
    )
    monkeypatch.setattr(server, "_push", pushed.append)

    server._load_nodes_bg(cached_nodes=nodes, silent=True)

    assert [msg["type"] for msg in pushed] == ["state_update", "state_update"]
    assert all(msg["node"] == "node-1" for msg in pushed)
    assert server.node_states["node-1"].is_edge is True
    assert server.node_states["node-1"].hosts_mariadb is True


def test_load_nodes_bg_uses_full_state_when_membership_changes(monkeypatch, tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    server.node_states["node-old"] = NodeState(k8s_name="node-old", hypervisor="hv-old")
    nodes = [{"name": "node-1", "hostname": "hv-1", "ready": True, "cordoned": False}]

    pushed: list[dict] = []

    monkeypatch.setattr(web_server.openstack_ops, "get_all_host_summaries", lambda log_cb=None, auth=None: {})
    monkeypatch.setattr(web_server.k8s_ops, "get_etcd_node_names", lambda auth=None: set())
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: {
            "kernel_version": "6.8.0",
            "latest_kernel_version": "6.8.12",
            "reboot_required": True,
        },
    )
    monkeypatch.setattr(server, "_push", pushed.append)

    server._load_nodes_bg(cached_nodes=nodes, silent=True)

    assert [msg["type"] for msg in pushed] == ["full_state", "full_state"]
    assert "node-old" not in server.node_states
    assert "node-1" in server.node_states


def test_load_nodes_bg_reuses_cached_openstack_summaries_on_silent_refresh(monkeypatch, tmp_path):
    server = web_server.DrainoServer(openstack_auth=object(), audit_log=str(tmp_path / "audit.log"))
    nodes = [{"name": "node-1", "hostname": "hv-1", "ready": True, "cordoned": False}]

    summary_calls: list[str] = []
    now_values = iter([1000.0, 1005.0])

    monkeypatch.setattr(web_server.time, "time", lambda: next(now_values))
    monkeypatch.setattr(web_server.k8s_ops, "get_etcd_node_names", lambda auth=None: set())
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: {
            "kernel_version": "6.8.0",
            "latest_kernel_version": "6.8.12",
            "reboot_required": True,
        },
    )
    monkeypatch.setattr(
        web_server.openstack_ops,
        "get_all_host_summaries",
        lambda log_cb=None, auth=None: summary_calls.append("called") or {"hv-1": {"is_compute": True, "compute_status": "up", "vm_count": 0, "amphora_count": 0}},
    )
    monkeypatch.setattr(server, "_push", lambda msg: None)

    server._load_nodes_bg(cached_nodes=nodes, silent=True)
    server._load_nodes_bg(cached_nodes=nodes, silent=True)

    assert summary_calls == ["called"]


def test_load_nodes_bg_manual_refresh_bypasses_cached_openstack_summaries(monkeypatch, tmp_path):
    server = web_server.DrainoServer(openstack_auth=object(), audit_log=str(tmp_path / "audit.log"))
    nodes = [{"name": "node-1", "hostname": "hv-1", "ready": True, "cordoned": False}]

    summary_calls: list[str] = []
    now_values = iter([1000.0, 1005.0])

    monkeypatch.setattr(web_server.time, "time", lambda: next(now_values))
    monkeypatch.setattr(web_server.k8s_ops, "get_etcd_node_names", lambda auth=None: set())
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: {
            "kernel_version": "6.8.0",
            "latest_kernel_version": "6.8.12",
            "reboot_required": True,
        },
    )
    monkeypatch.setattr(
        web_server.openstack_ops,
        "get_all_host_summaries",
        lambda log_cb=None, auth=None: summary_calls.append("called") or {"hv-1": {"is_compute": True, "compute_status": "up", "vm_count": 0, "amphora_count": 0}},
    )
    monkeypatch.setattr(server, "_push", lambda msg: None)

    server._load_nodes_bg(cached_nodes=nodes, silent=True)
    server._load_nodes_bg(cached_nodes=nodes, silent=False)

    assert summary_calls == ["called", "called"]


def _drained_compute_state(name="node-1"):
    state = NodeState(k8s_name=name, hypervisor="hv-1", is_compute=True, k8s_cordoned=True, compute_status="disabled", vm_count=0, amphora_count=0)
    state.k8s_drain_status = "drained"
    state.nova_evacuation_status = "evacuated"
    return state


def test_reboot_confirm_audits_operator_identity(monkeypatch, tmp_path):
    audit_path = tmp_path / "audit.log"
    server = web_server.DrainoServer(
        role_names=["admin"],
        audit_log=str(audit_path),
        operator_username="alice",
    )
    server.session_id = "sess-abc"
    server.node_states["node-1"] = _drained_compute_state()

    class _NoStartThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            raise AssertionError("thread must not be started")

    monkeypatch.setattr(inventory_actions.threading, "Thread", _NoStartThread)
    try:
        server.action_reboot_confirm("node-1")
    except AssertionError:
        pass  # the worker thread's start() was reached, as expected

    import json
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    started = [e for e in entries if e["action"] == "reboot" and e["event"] == "started"]
    assert started and started[0]["user"] == "alice"
    assert started[0]["session_id"] == "sess-abc"


def test_reboot_confirm_claims_phase_synchronously(monkeypatch, tmp_path):
    server = web_server.DrainoServer(role_names=["admin"], audit_log=str(tmp_path / "audit.log"))
    state = _drained_compute_state()
    server.node_states["node-1"] = state

    started: list[bool] = []
    monkeypatch.setattr(inventory_actions.worker, "run_reboot", lambda *a, **k: started.append(True))
    monkeypatch.setattr(inventory_actions.threading, "Thread", lambda target=None, args=(), kwargs=None, daemon=None: SimpleNamespace(
        start=lambda: None,
    ))

    server.action_reboot_confirm("node-1")
    assert state.phase == NodePhase.REBOOTING
    assert state.reboot_downtime is None
    assert state.get_step("issue_reboot") is not None

    pushed: list[dict] = []
    server._push = pushed.append
    server.action_reboot_confirm("node-1")
    assert any("operation is in progress" in str(m.get("message")) for m in pushed if m["type"] == "log")


def test_reboot_cancel_is_honest(monkeypatch, tmp_path):
    import json

    audit_path = tmp_path / "audit.log"
    server = web_server.DrainoServer(role_names=["admin"], audit_log=str(audit_path))
    state = _drained_compute_state()
    server.node_states["node-1"] = state

    pushed: list[dict] = []
    server._push = pushed.append

    server.action_reboot_cancel("node-1")
    assert any("no reboot was issued" in str(m.get("message")) for m in pushed)

    state.phase = NodePhase.REBOOTING
    pushed.clear()
    server.action_reboot_cancel("node-1")
    assert any("cannot be cancelled" in str(m.get("message")) for m in pushed)

    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert entries[0]["event"] == "cancelled"
    assert entries[1]["event"] == "failed"
    assert "cancel refused" in entries[1]["detail"]


def test_etcd_reboot_blocks_unlabeled_member(monkeypatch, tmp_path):
    server = web_server.DrainoServer(role_names=["admin"], audit_log=str(tmp_path / "audit.log"))
    server.node_states["node-x"] = _drained_compute_state("node-x")

    monkeypatch.setattr(web_server.k8s_ops, "get_etcd_member_node_names", lambda auth=None: {"node-x"})
    pushed: list[dict] = []
    server._push = pushed.append

    server._etcd_reboot_preflight_bg("node-x")

    blocked = [m for m in pushed if m["type"] == "reboot_blocked"]
    assert blocked and "lacks the node-role.kubernetes.io/etcd label" in blocked[0]["detail"]


def test_etcd_reboot_fails_closed_on_unknown_membership(monkeypatch, tmp_path):
    server = web_server.DrainoServer(role_names=["admin"], audit_log=str(tmp_path / "audit.log"))
    server.node_states["node-x"] = _drained_compute_state("node-x")

    def _boom(auth=None):
        raise RuntimeError("etcd unreachable")

    monkeypatch.setattr(web_server.k8s_ops, "get_etcd_member_node_names", _boom)
    pushed: list[dict] = []
    server._push = pushed.append

    server._etcd_reboot_preflight_bg("node-x")

    blocked = [m for m in pushed if m["type"] == "reboot_blocked"]
    assert blocked and "could not be determined" in blocked[0]["detail"]
