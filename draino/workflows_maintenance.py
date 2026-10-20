"""Maintenance workflow implementations shared by the worker entrypoints."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .models import NodePhase, NodeState, StepStatus

UpdateFn = Callable[[], None]
LogFn = Callable[[str], None]
AuditCb = Callable[[str, str], None]

REBOOT_OFFLINE_TIMEOUT = 300
REBOOT_ONLINE_TIMEOUT = 1200
# Minimum time a reboot must be in flight before a Ready node with an unchanged
# ready_since may be treated as recovered (covers agents that reboot before
# kubelet can report NotReady).
REBOOT_MIN_INFLIGHT = 120


@dataclass
class WorkflowContext:
    state: NodeState
    update_cb: UpdateFn
    log_cb: LogFn
    audit_cb: Optional[AuditCb]
    abort_prefix: str

    def step_set(self, key: str, status: StepStatus, detail: str = "") -> None:
        step = self.state.get_step(key)
        if step:
            step.status = status
            if detail:
                step.detail = detail
        self.update_cb()

    def log(self, msg: str) -> None:
        self.state.add_log(msg)
        self.log_cb(msg)
        self.update_cb()

    def abort(self, key: str, reason: str) -> None:
        self.step_set(key, StepStatus.FAILED, reason)
        self.state.phase = NodePhase.ERROR
        self.log(f"{self.abort_prefix}: {reason}")
        if self.audit_cb:
            self.audit_cb("failed", f"step={key} reason={reason}")
        self.update_cb()


def run_reboot(
    state: NodeState,
    update_cb: UpdateFn,
    log_cb: LogFn,
    *,
    k8s_ops,
    issue_reboot_fn,
    time_module,
    audit_cb: Optional[AuditCb] = None,
    k8s_auth=None,
    phase_already_claimed: bool = False,
    operator: Optional[str] = None,
) -> None:
    ctx = WorkflowContext(
        state=state,
        update_cb=update_cb,
        log_cb=log_cb,
        audit_cb=audit_cb,
        abort_prefix="Reboot aborted",
    )

    if not phase_already_claimed:
        state.phase = NodePhase.REBOOTING
        state.reboot_start = time_module.time()
        state.reboot_downtime = None
        state.etcd_healthy = False
        state.init_reboot_steps()
    elif state.reboot_start is None:
        state.reboot_start = time_module.time()
    update_cb()

    baseline_ready_since = None
    baseline_valid = False
    try:
        for nd in k8s_ops.get_nodes(auth=k8s_auth):
            if nd["name"] == state.k8s_name:
                baseline_ready_since = nd.get("ready_since")
                baseline_valid = True
                break
    except Exception:
        pass
    if not baseline_valid:
        ctx.abort("issue_reboot", "Cannot capture Ready baseline — refusing to reboot without a valid recovery baseline")
        return

    ctx.step_set("issue_reboot", StepStatus.RUNNING)
    try:
        issue_reboot_fn(state, ctx.log, requested_by=operator)
    except Exception as exc:
        ctx.abort("issue_reboot", str(exc))
        return

    ctx.step_set("issue_reboot", StepStatus.SUCCESS)

    ctx.step_set("await_offline", StepStatus.RUNNING)
    offline_deadline = time_module.time() + REBOOT_OFFLINE_TIMEOUT
    went_offline = False

    while time_module.time() < offline_deadline:
        try:
            for nd in k8s_ops.get_nodes(auth=k8s_auth):
                if nd["name"] == state.k8s_name and not nd["ready"]:
                    went_offline = True
                    break
        except Exception:
            pass

        elapsed = int(time_module.time() - state.reboot_start)
        step = state.get_step("await_offline")
        if step:
            step.detail = f"{elapsed}s elapsed"
        update_cb()

        if went_offline:
            break
        time_module.sleep(5)

    if went_offline:
        ctx.step_set("await_offline", StepStatus.SUCCESS, "Node offline")
    else:
        ctx.step_set("await_offline", StepStatus.SKIPPED, "Not detected — proceeding")

    ctx.step_set("await_online", StepStatus.RUNNING)
    online_deadline = state.reboot_start + REBOOT_ONLINE_TIMEOUT
    came_back = False
    saw_ready_before_offline = not went_offline

    while time_module.time() < online_deadline:
        try:
            nodes = k8s_ops.get_nodes(auth=k8s_auth)
        except Exception:
            nodes = []
        for nd in nodes:
            if nd["name"] != state.k8s_name:
                continue
            if nd["ready"]:
                # Recovery requires a changed ready_since.  An unchanged
                # ready_since counts only while the node is still inside the
                # offline-detection blind window (never went NotReady, reboot
                # in flight past the minimum, and no Ready was seen inside the
                # window) — this covers agents that reboot the host before
                # kubelet can report NotReady, without ever accepting a
                # never-went-down node as recovered.
                changed = (
                    nd.get("ready_since") is not None
                    and nd.get("ready_since") != baseline_ready_since
                )
                in_window = (
                    not went_offline
                    and not saw_ready_before_offline
                    and (time_module.time() - state.reboot_start) >= REBOOT_MIN_INFLIGHT
                )
                saw_ready_before_offline = True
                if changed or in_window:
                    state.reboot_downtime = time_module.time() - state.reboot_start
                    came_back = True
                break
            saw_ready_before_offline = False

        if came_back:
            break

        elapsed = int(time_module.time() - state.reboot_start)
        step = state.get_step("await_online")
        if step:
            step.detail = f"{elapsed}s elapsed"
        update_cb()
        time_module.sleep(5)

    if not came_back:
        ctx.abort("await_online", "Timeout — no offline-to-online transition observed, cannot confirm the reboot completed")
        return

    dt = int(state.reboot_downtime)
    ctx.step_set("await_online", StepStatus.SUCCESS, f"Online — downtime {dt}s")
    ctx.step_set("uncordon", StepStatus.RUNNING)
    try:
        k8s_ops.uncordon_node(state.k8s_name, ctx.log, auth=k8s_auth)
        state.k8s_cordoned = False
        ctx.step_set("uncordon", StepStatus.SUCCESS)
    except Exception as exc:
        ctx.abort("uncordon", str(exc))
        return

    state.phase = NodePhase.IDLE
    state.etcd_healthy = None
    ctx.log(f"✓ '{state.k8s_name}' back online and uncordoned — total downtime: {dt}s")
    if audit_cb:
        audit_cb("completed", f"downtime={dt}s auto_uncordoned=true")
    update_cb()


def run_drain_quick(
    state: NodeState,
    update_cb: UpdateFn,
    log_cb: LogFn,
    *,
    k8s_ops,
    openstack_ops,
    audit_cb: Optional[AuditCb] = None,
    k8s_auth=None,
    openstack_auth=None,
) -> None:
    del openstack_ops, openstack_auth
    if state.k8s_cordoned and state.k8s_drain_status == "drained":
        state.phase = NodePhase.IDLE
        state.k8s_drain_detail = state.k8s_drain_detail or "node already drained"
        log_cb(f"'{state.k8s_name}' already drained — no Kubernetes action needed")
        if audit_cb:
            audit_cb("completed", "already drained")
        update_cb()
        return
    ctx = WorkflowContext(
        state=state,
        update_cb=update_cb,
        log_cb=log_cb,
        audit_cb=audit_cb,
        abort_prefix="Drain aborted",
    )

    ctx.step_set("cordon", StepStatus.RUNNING)
    try:
        k8s_ops.cordon_node(state.k8s_name, ctx.log, auth=k8s_auth)
        state.k8s_cordoned = True
        state.k8s_drain_status = "in_progress"
        state.k8s_drain_detail = "cordoned"
        ctx.step_set("cordon", StepStatus.SUCCESS)
    except Exception as exc:
        state.k8s_drain_status = "error"
        state.k8s_drain_detail = str(exc)
        ctx.abort("cordon", str(exc))
        return

    ctx.step_set("drain_k8s", StepStatus.RUNNING)
    try:
        k8s_ops.drain_node(state.k8s_name, ctx.log, auth=k8s_auth)
        state.k8s_drain_status = "drained"
        state.k8s_drain_detail = "node cordoned and drain-safe pods evicted"
        ctx.step_set("drain_k8s", StepStatus.SUCCESS)
    except Exception as exc:
        state.k8s_drain_status = "blocked"
        state.k8s_drain_detail = str(exc)
        ctx.abort("drain_k8s", str(exc))
        return

    state.phase = NodePhase.IDLE
    ctx.log(f"✓ '{state.k8s_name}' drained — cordoned, drain-safe pods evicted")
    if audit_cb:
        audit_cb("completed", "cordoned, drain-safe pods evicted")
    update_cb()


def run_undrain(
    state: NodeState,
    update_cb: UpdateFn,
    log_cb: LogFn,
    *,
    k8s_ops,
    openstack_ops,
    audit_cb: Optional[AuditCb] = None,
    k8s_auth=None,
    openstack_auth=None,
) -> None:
    del openstack_ops, openstack_auth
    if not state.k8s_cordoned and state.k8s_drain_status == "clear":
        state.phase = NodePhase.IDLE
        state.k8s_drain_detail = ""
        log_cb(f"'{state.k8s_name}' already schedulable — no Kubernetes action needed")
        if audit_cb:
            audit_cb("completed", "already schedulable")
        update_cb()
        return
    ctx = WorkflowContext(
        state=state,
        update_cb=update_cb,
        log_cb=log_cb,
        audit_cb=audit_cb,
        abort_prefix="Undrain aborted",
    )

    ctx.step_set("uncordon", StepStatus.RUNNING)
    try:
        k8s_ops.uncordon_node(state.k8s_name, ctx.log, auth=k8s_auth)
        state.k8s_cordoned = False
        state.k8s_drain_status = "clear"
        state.k8s_drain_detail = ""
        ctx.step_set("uncordon", StepStatus.SUCCESS)
    except Exception as exc:
        state.k8s_drain_status = "error"
        state.k8s_drain_detail = str(exc)
        ctx.abort("uncordon", str(exc))
        return

    state.phase = NodePhase.IDLE
    ctx.log(f"✓ '{state.k8s_name}' undrained — node uncordoned")
    if audit_cb:
        audit_cb("completed", "node uncordoned")
    update_cb()
