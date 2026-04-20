"""Inventory action and workflow helpers."""
from __future__ import annotations

import threading
import time

from .. import worker
from ..models import NodePhase
from ..operations import k8s_ops, openstack_ops
from ..reboot import is_ready_for_reboot
from .latency import measure_latency
from .serialise import serialise_state as _serialise


class InventoryActionsMixin:
    def start_preflight(self, node_name: str, silent: bool = False) -> None:
        state = self.node_states.get(node_name)
        if not state or not state.is_compute or state.phase != NodePhase.IDLE:
            return
        if state.preflight_loading:
            return
        state.preflight_loading = True
        if not silent:
            state.preflight_instances = []
            self._push({"type": "state_update", "node": node_name, "data": _serialise(state)})
        threading.Thread(target=self._preflight_bg, args=(node_name,), daemon=True).start()

    def _preflight_bg(self, node_name: str) -> None:
        state = self.node_states.get(node_name)
        if not state:
            return
        try:
            with measure_latency("instance_preflight"):
                state.preflight_instances = openstack_ops.get_instances_preflight(
                    state.hypervisor,
                    auth=self.openstack_auth,
                )
        except Exception as exc:
            state.preflight_instances = []
            self._push({"type": "log", "node": "-", "message": f"Preflight failed for {node_name}: {exc}", "color": "warn"})
        finally:
            state.preflight_loading = False
        self._push({"type": "state_update", "node": node_name, "data": _serialise(state)})

    def start_etcd_check(self) -> None:
        threading.Thread(target=self._etcd_check_bg, daemon=True).start()

    def _etcd_check_bg(self) -> None:
        etcd_states = [state for state in self.node_states.values() if state.is_etcd]
        for state in etcd_states:
            state.etcd_checking = True
            state.etcd_error = None
            self._push({"type": "state_update", "node": state.k8s_name, "data": _serialise(state)})
        for state in etcd_states:
            etcd_status = k8s_ops.get_etcd_service_status(state.k8s_name, state.hypervisor)
            state.etcd_healthy = etcd_status.get("active")
            state.etcd_error = etcd_status.get("error")
            state.etcd_checking = False
            self._push({"type": "state_update", "node": state.k8s_name, "data": _serialise(state)})

    def action_evacuate(self, node_name: str) -> None:
        state = self.node_states.get(node_name)
        if not state:
            return
        if not state.is_compute:
            self._push({"type": "log", "node": node_name, "message": "Not a compute node — no evacuation needed.", "color": "warn"})
            return
        if state.phase == NodePhase.RUNNING:
            self._push({"type": "log", "node": node_name, "message": "Evacuation already in progress.", "color": "warn"})
            return

        state.phase = NodePhase.RUNNING
        state.instances = []
        state.log_buffer = []
        state.preflight_instances = []
        state.preflight_loading = False
        state.nova_evacuation_status = "in_progress"
        state.nova_evacuation_detail = "starting nova evacuation"
        state.init_steps()

        audit_cb = self._audit_cb("evacuation", node_name)
        audit_cb("started")
        self._push({"type": "state_update", "node": node_name, "data": _serialise(state)})
        self._push({"type": "log", "node": node_name, "message": f"Starting Nova evacuation of {node_name}…", "color": "important"})

        threading.Thread(
            target=worker.run_workflow,
            args=(state, self._update_cb(node_name), self._log_cb(node_name), audit_cb, self.k8s_auth, self.openstack_auth),
            daemon=True,
        ).start()

    def action_enable_nova(self, node_name: str) -> None:
        state = self.node_states.get(node_name)
        if not state:
            return
        if not state.is_compute:
            self._push({"type": "log", "node": node_name, "message": "Not a compute node — Nova does not apply here.", "color": "warn"})
            return
        if state.phase == NodePhase.RUNNING:
            self._push({"type": "log", "node": node_name, "message": "Cannot re-enable Nova while evacuation is running.", "color": "warn"})
            return
        if state.compute_status != "disabled":
            self._push({"type": "log", "node": node_name, "message": "Nova compute service is already enabled.", "color": "dim"})
            return

        audit_cb = self._audit_cb("enable_nova", node_name)
        audit_cb("started")
        self._push({"type": "log", "node": node_name, "message": f"Re-enabling Nova compute service on {node_name}…", "color": "important"})

        threading.Thread(
            target=self._enable_nova_bg,
            args=(node_name, audit_cb),
            daemon=True,
        ).start()

    def _enable_nova_bg(self, node_name: str, audit_cb) -> None:
        state = self.node_states.get(node_name)
        if not state:
            return
        log = self._log_cb(node_name)
        try:
            openstack_ops.enable_compute_service(state.hypervisor, log, auth=self.openstack_auth)
        except Exception as exc:
            audit_cb("failed", str(exc))
            self._push({"type": "log", "node": node_name, "message": f"Failed to re-enable Nova on {node_name}: {exc}", "color": "warn"})
            return

        state.compute_status = "up"
        state.phase = NodePhase.IDLE
        state.steps = []
        state.instances = []
        state.preflight_instances = []
        state.preflight_loading = False
        state.nova_evacuation_status = "clear"
        state.nova_evacuation_detail = "nova compute service enabled"
        audit_cb("completed", f"hypervisor={state.hypervisor}")
        self._push({"type": "state_update", "node": node_name, "data": _serialise(state)})
        self._push({"type": "log", "node": node_name, "message": f"Nova compute service re-enabled on {node_name}.", "color": "green"})

    def action_drain_quick(self, node_name: str) -> None:
        state = self.node_states.get(node_name)
        if not state or state.phase == NodePhase.RUNNING:
            return

        state.phase = NodePhase.RUNNING
        state.k8s_drain_status = "in_progress"
        state.k8s_drain_detail = "starting kubernetes drain"
        state.init_quick_drain_steps(state.is_compute)

        audit_cb = self._audit_cb("drain_quick", node_name)
        audit_cb("started")
        self._push({"type": "state_update", "node": node_name, "data": _serialise(state)})
        self._push({"type": "log", "node": node_name, "message": f"Starting Kubernetes drain of {node_name}…", "color": "important"})

        threading.Thread(
            target=worker.run_drain_quick,
            args=(state, self._update_cb(node_name), self._log_cb(node_name), audit_cb, self.k8s_auth, self.openstack_auth),
            daemon=True,
        ).start()

    def action_undrain(self, node_name: str) -> None:
        state = self.node_states.get(node_name)
        if not state or state.phase == NodePhase.RUNNING:
            return

        state.phase = NodePhase.UNDRAINING
        state.k8s_drain_detail = "clearing kubernetes drain posture"
        state.init_undrain_steps(state.is_compute)

        audit_cb = self._audit_cb("undrain", node_name)
        audit_cb("started")
        self._push({"type": "state_update", "node": node_name, "data": _serialise(state)})
        self._push({"type": "log", "node": node_name, "message": f"Clearing Kubernetes drain on {node_name}…", "color": "important"})

        threading.Thread(
            target=worker.run_undrain,
            args=(state, self._update_cb(node_name), self._log_cb(node_name), audit_cb, self.k8s_auth, self.openstack_auth),
            daemon=True,
        ).start()

    def action_reboot_request(self, node_name: str) -> None:
        if not self.is_admin:
            detail = "Reboot requires the OpenStack 'admin' role."
            self._audit.log("reboot", node_name, "blocked", detail)
            self._push({"type": "log", "node": node_name, "message": detail, "color": "warn"})
            return
        state = self.node_states.get(node_name)
        if not state:
            return
        reboot_ready, detail = is_ready_for_reboot(state)
        if not reboot_ready:
            self._push({"type": "log", "node": node_name, "message": detail, "color": "warn"})
            return

        if state.is_etcd:
            self._push({"type": "log", "node": "-", "message": "Checking etcd quorum before reboot…", "color": "dim"})
            threading.Thread(target=self._etcd_reboot_preflight_bg, args=(node_name,), daemon=True).start()
        else:
            self._push({"type": "reboot_confirm_needed", "node": node_name})

    def _etcd_reboot_preflight_bg(self, node_name: str) -> None:
        etcd_states = [state for state in self.node_states.values() if state.is_etcd]
        etcd_total = len(etcd_states)
        quorum_needed = (etcd_total // 2) + 1

        for state in etcd_states:
            etcd_status = k8s_ops.get_etcd_service_status(state.k8s_name, state.hypervisor)
            state.etcd_healthy = etcd_status.get("active")
            state.etcd_error = etcd_status.get("error")
            self._push({"type": "state_update", "node": state.k8s_name, "data": _serialise(state)})

        healthy_count = sum(1 for state in etcd_states if state.etcd_healthy is True)
        this_state = self.node_states.get(node_name)
        this_healthy = this_state is not None and this_state.etcd_healthy is True
        remaining = healthy_count - (1 if this_healthy else 0)

        if remaining < quorum_needed:
            detail = f"{healthy_count}/{etcd_total} healthy, rebooting would leave {remaining} (quorum={quorum_needed})"
            self._audit.log("reboot", node_name, "blocked", detail)
            self._push({"type": "reboot_blocked", "node": node_name, "detail": detail})
        else:
            self._push({"type": "reboot_confirm_needed", "node": node_name})

    def action_reboot_confirm(self, node_name: str) -> None:
        if not self.is_admin:
            detail = "Reboot requires the OpenStack 'admin' role."
            self._audit.log("reboot", node_name, "blocked", detail)
            self._push({"type": "log", "node": node_name, "message": detail, "color": "warn"})
            return
        state = self.node_states.get(node_name)
        if not state:
            return

        audit_cb = self._audit_cb("reboot", node_name)
        audit_cb("started", f"hypervisor={state.hypervisor}")
        self._push({"type": "log", "node": node_name, "message": f"Rebooting {node_name}…", "color": "important"})

        threading.Thread(
            target=worker.run_reboot,
            args=(state, self._update_cb(node_name), self._log_cb(node_name, "magenta"), audit_cb, self.k8s_auth),
            daemon=True,
        ).start()

    def action_reboot_cancel(self, node_name: str) -> None:
        self._audit.log("reboot", node_name, "cancelled")
        self._push({"type": "log", "node": node_name, "message": "Reboot cancelled.", "color": "dim"})

    def action_migrate_instance(self, node_name: str, instance_id: str) -> None:
        state = self.node_states.get(node_name)
        if not state or state.phase.name.lower() != "idle":
            self._push({"type": "log", "node": node_name, "message": "Cannot migrate: node is not idle.", "color": "warn"})
            return
        self._push({
            "type": "instance_migrate_status",
            "node": node_name,
            "instance_id": instance_id,
            "status": "migrating",
        })
        threading.Thread(target=self._migrate_instance_bg, args=(node_name, instance_id), daemon=True).start()

    def _migrate_instance_bg(self, node_name: str, instance_id: str) -> None:
        log = self._log_cb(node_name)
        try:
            openstack_ops.live_migrate_server(instance_id, log, auth=self.openstack_auth)
        except Exception as exc:
            task_state = openstack_ops.get_server_task_state(instance_id, auth=self.openstack_auth) or ""
            if "migrat" in task_state.lower():
                log(f"Live migration for {instance_id} timed out but instance is already migrating")
            else:
                log(f"Live migration failed for {instance_id}: {exc} — trying cold migration")
                try:
                    openstack_ops.cold_migrate_server(instance_id, log, auth=self.openstack_auth)
                except Exception as cold_exc:
                    log(f"Migration trigger failed for {instance_id}: {cold_exc}")
                    self._push({"type": "instance_migrate_status", "node": node_name, "instance_id": instance_id, "status": "error"})
                    return

        deadline = time.time() + 600
        time.sleep(3)
        while time.time() < deadline:
            task_state = openstack_ops.get_server_task_state(instance_id, auth=self.openstack_auth)
            server_status = openstack_ops.get_server_status(instance_id, auth=self.openstack_auth)
            if server_status == "VERIFY_RESIZE":
                try:
                    openstack_ops.confirm_resize_server(instance_id, log, auth=self.openstack_auth)
                except Exception as exc:
                    log(f"Confirm resize failed for {instance_id}: {exc}")
                    self._push({"type": "instance_migrate_status", "node": node_name, "instance_id": instance_id, "status": "error"})
                    return
            if server_status == "ERROR":
                log(f"Instance {instance_id} entered ERROR state during migration")
                self._push({"type": "instance_migrate_status", "node": node_name, "instance_id": instance_id, "status": "error"})
                return
            if task_state is None and server_status == "ACTIVE":
                log(f"Instance {instance_id} migrated successfully")
                self._push({"type": "instance_migrate_status", "node": node_name, "instance_id": instance_id, "status": "complete"})
                self.start_preflight(node_name)
                return
            time.sleep(5)

        log(f"Timeout waiting for instance {instance_id} to finish migrating")
        self._push({"type": "instance_migrate_status", "node": node_name, "instance_id": instance_id, "status": "error"})
