from __future__ import annotations

from draino import worker
from draino.models import NodePhase, NodeState, StepStatus
from draino.operations.k8s_ops import K8sAuth


def _fake_clock(*values: float):
    remaining = list(values)
    last = values[-1]

    def _time() -> float:
        nonlocal last
        if remaining:
            last = remaining.pop(0)
        return last

    return _time


def _fake_nodes(*payloads: list[dict]):
    remaining = list(payloads)
    last = payloads[-1]

    def _get_nodes(auth=None):
        nonlocal last
        if remaining:
            last = remaining.pop(0)
        return last

    return _get_nodes


def test_run_drain_quick_propagates_session_auth(monkeypatch):
    events: list[tuple[str, object]] = []
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", is_compute=True)
    state.init_quick_drain_steps(is_compute=True)

    k8s_auth = K8sAuth(server="https://cluster.example", token="token-1")
    monkeypatch.setattr(
        worker.k8s_ops,
        "cordon_node",
        lambda name, log, auth=None: events.append(("cordon", auth)),
    )
    monkeypatch.setattr(
        worker.k8s_ops,
        "drain_node",
        lambda name, log, auth=None: events.append(("drain", auth)),
    )

    worker.run_drain_quick(
        state,
        update_cb=lambda: None,
        log_cb=lambda msg: None,
        k8s_auth=k8s_auth,
    )

    assert events == [("cordon", k8s_auth), ("drain", k8s_auth)]
    assert state.phase == NodePhase.IDLE
    assert state.k8s_cordoned is True
    assert state.k8s_drain_status == "drained"
    assert state.compute_status is None


def test_run_undrain_propagates_session_auth(monkeypatch):
    events: list[tuple[str, object]] = []
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", is_compute=True, k8s_cordoned=True)
    state.k8s_drain_status = "drained"
    state.init_undrain_steps(is_compute=True)

    k8s_auth = K8sAuth(server="https://cluster.example", token="token-1")
    monkeypatch.setattr(
        worker.k8s_ops,
        "uncordon_node",
        lambda name, log, auth=None: events.append(("uncordon", auth)),
    )

    worker.run_undrain(
        state,
        update_cb=lambda: None,
        log_cb=lambda msg: None,
        k8s_auth=k8s_auth,
    )

    assert events == [("uncordon", k8s_auth)]
    assert state.phase == NodePhase.IDLE
    assert state.k8s_cordoned is False
    assert state.k8s_drain_status == "clear"


def test_run_reboot_uncordons_node_after_return(monkeypatch):
    events: list[tuple[str, object]] = []
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", k8s_cordoned=True)

    k8s_auth = K8sAuth(server="https://cluster.example", token="token-1")
    clock = _realistic_clock()

    monkeypatch.setattr(worker, "issue_reboot", lambda state, log, requested_by=None: events.append(("reboot", requested_by)))
    monkeypatch.setattr(worker.time, "time", clock)
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        worker.k8s_ops,
        "get_nodes",
        _fake_nodes(
            [{"name": "node-1", "ready": True, "ready_since": "before-reboot"}],
            [{"name": "node-1", "ready": False, "ready_since": "before-reboot"}],
            [{"name": "node-1", "ready": True, "ready_since": "after-reboot"}],
        ),
    )
    monkeypatch.setattr(
        worker.k8s_ops,
        "uncordon_node",
        lambda name, log, auth=None: events.append(("uncordon", auth)),
    )

    worker.run_reboot(
        state,
        update_cb=lambda: None,
        log_cb=lambda msg: None,
        k8s_auth=k8s_auth,
    )

    assert events == [("reboot", None), ("uncordon", k8s_auth)]
    assert state.phase == NodePhase.IDLE
    assert state.k8s_cordoned is False
    assert state.get_step("uncordon").status == StepStatus.SUCCESS
    assert state.get_step("await_offline").status == StepStatus.SUCCESS


def test_run_reboot_fails_if_auto_uncordon_fails(monkeypatch):
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", k8s_cordoned=True)
    clock = _realistic_clock()

    monkeypatch.setattr(worker, "issue_reboot", lambda state, log, requested_by=None: None)
    monkeypatch.setattr(worker.time, "time", clock)
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        worker.k8s_ops,
        "get_nodes",
        _fake_nodes(
            [{"name": "node-1", "ready": True, "ready_since": "before-reboot"}],
            [{"name": "node-1", "ready": False, "ready_since": "before-reboot"}],
            [{"name": "node-1", "ready": True, "ready_since": "after-reboot"}],
        ),
    )
    monkeypatch.setattr(
        worker.k8s_ops,
        "uncordon_node",
        lambda name, log, auth=None: (_ for _ in ()).throw(RuntimeError("uncordon failed")),
    )

    worker.run_reboot(
        state,
        update_cb=lambda: None,
        log_cb=lambda msg: None,
    )

    assert state.phase == NodePhase.ERROR
    assert state.k8s_cordoned is True
    assert state.get_step("uncordon").status == StepStatus.FAILED


def test_run_workflow_evacuates_nova_only(monkeypatch):
    events: list[tuple[str, object]] = []
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", is_compute=True, k8s_cordoned=True)

    monkeypatch.setattr(
        worker.openstack_ops,
        "disable_compute_service",
        lambda hypervisor, log, auth=None: events.append(("disable", auth)),
    )
    monkeypatch.setattr(
        worker.openstack_ops,
        "list_servers_on_host",
        lambda hypervisor, log, auth=None: [],
    )
    monkeypatch.setattr(
        worker.openstack_ops,
        "get_amphora_lb_mapping",
        lambda log, auth=None: {},
    )
    monkeypatch.setattr(
        worker.openstack_ops,
        "count_servers_on_host",
        lambda hypervisor, auth=None: 0,
    )
    monkeypatch.setattr(
        worker.k8s_ops,
        "cordon_node",
        lambda name, log, auth=None: (_ for _ in ()).throw(AssertionError("evacuate should not cordon kubernetes")),
    )
    monkeypatch.setattr(
        worker.k8s_ops,
        "drain_node",
        lambda name, log, auth=None: (_ for _ in ()).throw(AssertionError("evacuate should not drain kubernetes")),
    )

    worker.run_workflow(
        state,
        update_cb=lambda: None,
        log_cb=lambda msg: None,
    )

    assert events == [("disable", None)]
    assert state.phase == NodePhase.COMPLETE
    assert state.k8s_cordoned is True
    assert state.nova_evacuation_status == "evacuated"


def test_run_workflow_reports_remaining_instance_names_on_timeout(monkeypatch):
    state = NodeState(k8s_name="node-2", hypervisor="hv-2", is_compute=True)
    ticks = iter([1000.0, 1000.0, 1001.0, 1002.0, 1905.0])

    monkeypatch.setattr(worker.time, "time", lambda: next(ticks))
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        worker.openstack_ops,
        "disable_compute_service",
        lambda hypervisor, log, auth=None: None,
    )
    monkeypatch.setattr(
        worker.openstack_ops,
        "list_servers_on_host",
        lambda hypervisor, log, auth=None: [
            {"id": "vm-1", "name": "web-1", "status": "ACTIVE", "is_amphora": False},
            {"id": "vm-2", "name": "db-1", "status": "ACTIVE", "is_amphora": False},
        ],
    )
    monkeypatch.setattr(
        worker.openstack_ops,
        "get_amphora_lb_mapping",
        lambda log, auth=None: {},
    )
    monkeypatch.setattr(
        worker.openstack_ops,
        "live_migrate_server",
        lambda instance_id, log, auth=None: None,
    )
    monkeypatch.setattr(
        worker.openstack_ops,
        "get_server_status",
        lambda instance_id, auth=None: "ACTIVE",
    )
    monkeypatch.setattr(
        worker.openstack_ops,
        "get_server_migrations",
        lambda instance_id, auth=None: [{"status": "completed"}],
    )
    monkeypatch.setattr(
        worker.openstack_ops,
        "count_servers_on_host",
        lambda hypervisor, auth=None: 2,
    )

    worker.run_workflow(
        state,
        update_cb=lambda: None,
        log_cb=lambda msg: None,
    )

    assert state.phase == NodePhase.ERROR
    assert state.nova_evacuation_status == "blocked"
    assert "web-1" in state.nova_evacuation_detail
    assert "db-1" in state.nova_evacuation_detail


def test_run_drain_quick_is_noop_when_already_drained(monkeypatch):
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", k8s_cordoned=True)
    state.k8s_drain_status = "drained"

    monkeypatch.setattr(
        worker.k8s_ops,
        "cordon_node",
        lambda name, log, auth=None: (_ for _ in ()).throw(AssertionError("cordon should not run")),
    )
    monkeypatch.setattr(
        worker.k8s_ops,
        "drain_node",
        lambda name, log, auth=None: (_ for _ in ()).throw(AssertionError("drain should not run")),
    )

    logs: list[str] = []
    worker.run_drain_quick(
        state,
        update_cb=lambda: None,
        log_cb=logs.append,
    )

    assert state.phase == NodePhase.IDLE
    assert logs == ["'node-1' already drained — no Kubernetes action needed"]


def test_run_undrain_is_noop_when_already_schedulable(monkeypatch):
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", k8s_cordoned=False)
    state.k8s_drain_status = "clear"

    monkeypatch.setattr(
        worker.k8s_ops,
        "uncordon_node",
        lambda name, log, auth=None: (_ for _ in ()).throw(AssertionError("uncordon should not run")),
    )

    logs: list[str] = []
    worker.run_undrain(
        state,
        update_cb=lambda: None,
        log_cb=logs.append,
    )

    assert state.phase == NodePhase.IDLE
    assert logs == ["'node-1' already schedulable — no Kubernetes action needed"]


def test_run_workflow_is_noop_when_already_evacuated(monkeypatch):
    state = NodeState(
        k8s_name="node-1",
        hypervisor="hv-1",
        is_compute=True,
        compute_status="disabled",
        vm_count=0,
        amphora_count=0,
    )

    monkeypatch.setattr(
        worker.openstack_ops,
        "disable_compute_service",
        lambda hypervisor, log, auth=None: (_ for _ in ()).throw(AssertionError("disable should not run")),
    )

    logs: list[str] = []
    worker.run_workflow(
        state,
        update_cb=lambda: None,
        log_cb=logs.append,
    )

    assert state.phase == NodePhase.COMPLETE
    assert state.nova_evacuation_status == "evacuated"
    assert state.nova_evacuation_detail == "compute service disabled and hypervisor already empty"
    assert logs == ["'node-1' already evacuated — no Nova action needed"]


def _realistic_clock(start: float = 1000.0, step: float = 100.0):
    now = {"value": start}

    def _time() -> float:
        value = now["value"]
        now["value"] += step
        return value

    return _time


def test_run_reboot_refuses_without_valid_ready_baseline(monkeypatch):
    events: list[tuple[str, object]] = []
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", k8s_cordoned=True)

    def _boom(auth=None):
        raise RuntimeError("api down")

    monkeypatch.setattr(worker, "issue_reboot", lambda state, log, requested_by=None: events.append(("reboot", requested_by)))
    monkeypatch.setattr(worker.time, "time", _realistic_clock())
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(worker.k8s_ops, "get_nodes", _boom)

    audits: list[tuple[str, str]] = []
    worker.run_reboot(
        state,
        update_cb=lambda: None,
        log_cb=lambda msg: None,
        audit_cb=lambda event, detail="": audits.append((event, detail)),
    )

    assert events == []
    assert state.phase == NodePhase.ERROR
    assert state.get_step("issue_reboot").status == StepStatus.FAILED
    assert ("failed", "step=issue_reboot reason=Cannot capture Ready baseline — refusing to reboot without a valid recovery baseline") in audits


def test_run_reboot_does_not_report_recovery_when_node_never_left_ready(monkeypatch):
    events: list[tuple[str, object]] = []
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", k8s_cordoned=True)

    monkeypatch.setattr(worker, "issue_reboot", lambda state, log, requested_by=None: events.append(("reboot", requested_by)))
    monkeypatch.setattr(worker.time, "time", _realistic_clock(step=500.0))
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        worker.k8s_ops,
        "get_nodes",
        _fake_nodes([{"name": "node-1", "ready": True, "ready_since": "unchanged"}]),
    )
    monkeypatch.setattr(
        worker.k8s_ops,
        "uncordon_node",
        lambda name, log, auth=None: events.append(("uncordon", auth)),
    )

    worker.run_reboot(
        state,
        update_cb=lambda: None,
        log_cb=lambda msg: None,
    )

    assert events == [("reboot", None)]
    assert state.phase == NodePhase.ERROR
    assert state.get_step("await_online").status == StepStatus.FAILED


def test_run_reboot_forwards_operator_to_issue_reboot(monkeypatch):
    events: list[tuple[str, object]] = []
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", k8s_cordoned=True)

    monkeypatch.setattr(worker, "issue_reboot", lambda state, log, requested_by=None: events.append(("reboot", requested_by)))
    monkeypatch.setattr(worker.time, "time", _realistic_clock())
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        worker.k8s_ops,
        "get_nodes",
        _fake_nodes(
            [{"name": "node-1", "ready": True, "ready_since": "before-reboot"}],
            [{"name": "node-1", "ready": False, "ready_since": "before-reboot"}],
            [{"name": "node-1", "ready": True, "ready_since": "after-reboot"}],
        ),
    )
    monkeypatch.setattr(worker.k8s_ops, "uncordon_node", lambda name, log, auth=None: None)

    worker.run_reboot(state, update_cb=lambda: None, log_cb=lambda msg: None, operator="alice")

    assert events[0] == ("reboot", "alice")
