"""Data models shared across the application."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class StepStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    SUCCESS = auto()
    FAILED  = auto()
    SKIPPED = auto()


class NodePhase(Enum):
    IDLE       = auto()
    RUNNING    = auto()
    COMPLETE   = auto()
    ERROR      = auto()
    UNDRAINING = auto()
    REBOOTING  = auto()


@dataclass
class InstanceInfo:
    id: str
    name: str
    status: str                              # Nova power/VM status
    is_amphora: bool = False
    migration_status: Optional[str] = None  # queued | migrating | complete | failed
    lb_id: Optional[str] = None             # Octavia LB ID (amphora only)
    failover_status: Optional[str] = None   # failing_over | complete | failed


@dataclass
class WorkflowStep:
    key: str
    label: str
    status: StepStatus = StepStatus.PENDING
    detail: str = ""


@dataclass
class NodeState:
    k8s_name: str
    hypervisor: str                          # Nova hypervisor hostname

    # Draino workflow phase
    phase: NodePhase = NodePhase.IDLE

    # ── K8s node attributes (from get_nodes()) ───────────────────────────
    k8s_ready:    bool = True
    k8s_cordoned: bool = False
    k8s_taints:   list[dict] = field(default_factory=list)

    # ── K8s node system info (populated by background refresh) ───────────
    kernel_version:        Optional[str] = None
    latest_kernel_version: Optional[str] = None
    uptime:                Optional[str] = None
    reboot_required:       bool = False

    # ── Node roles ────────────────────────────────────────────────────────
    node_agent_ready: bool = False
    is_edge:        bool = False           # True if OVN chassis is marked as gateway/edge
    is_network:     bool = False           # True if node carries the OpenStack network label
    is_etcd:        bool = False            # True if node carries the etcd role label
    hosts_mariadb:  bool = False            # True if a MariaDB/Galera pod is scheduled here
    etcd_healthy:   Optional[bool] = None  # None=unchecked | True=active | False=down
    etcd_checking:  bool = False           # True while etcd health check is in flight
    etcd_error:     Optional[str] = None   # Probe error when etcd status could not be validated

    # ── OpenStack summary (populated by background refresh) ──────────────
    # compute_status: None=loading | "up" | "disabled" | "down"
    is_compute:        bool = False
    compute_status:    Optional[str] = None
    compute_missing_from_openstack: bool = False
    amphora_count:     Optional[int] = None
    vm_count:          Optional[int] = None
    availability_zone: Optional[str] = None   # Nova AZ (from host aggregate metadata)
    aggregates:        list[str] = field(default_factory=list)  # host aggregate names

    # ── Pre-flight instance preview (populated on node selection) ────────────
    preflight_instances: list[dict] = field(default_factory=list)
    preflight_loading:   bool = False

    # ── Reboot tracking ───────────────────────────────────────────────────
    reboot_start:    Optional[float] = None   # Unix timestamp when reboot issued
    reboot_downtime: Optional[float] = None   # Seconds of downtime (set on recovery)

    # ── Workflow detail ───────────────────────────────────────────────────
    steps: list[WorkflowStep] = field(default_factory=list)
    instances: list[InstanceInfo] = field(default_factory=list)
    log_buffer: list[str] = field(default_factory=list)
    k8s_drain_status: str = "clear"
    k8s_drain_detail: str = ""
    nova_evacuation_status: str = "clear"
    nova_evacuation_detail: str = ""

    def add_log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log_buffer.append(f"[{ts}] {msg}")

    def get_step(self, key: str) -> Optional[WorkflowStep]:
        return next((s for s in self.steps if s.key == key), None)

    def init_steps(self) -> None:
        self.steps = [
            WorkflowStep("disable_nova",   "Disable Nova compute service"),
            WorkflowStep("list_instances", "Enumerate instances on hypervisor"),
            WorkflowStep("migrate_vms",    "Live-migrate non-Amphora instances"),
            WorkflowStep("failover_lbs",   "Failover Amphora load balancers"),
            WorkflowStep("await_empty",    "Wait for hypervisor to empty"),
        ]

    def init_quick_drain_steps(self, is_compute: bool) -> None:
        del is_compute
        self.steps = [
            WorkflowStep("cordon", "Cordon K8s node"),
            WorkflowStep("drain_k8s", "Drain K8s node (evict drain-safe pods)"),
        ]

    def init_undrain_steps(self, is_compute: bool) -> None:
        del is_compute
        self.steps = [WorkflowStep("uncordon", "Uncordon K8s node")]

    def init_reboot_steps(self) -> None:
        self.steps = [
            WorkflowStep("issue_reboot",  "Issue reboot command"),
            WorkflowStep("await_offline", "Wait for node to go offline"),
            WorkflowStep("await_online",  "Wait for node to come back online"),
            WorkflowStep("uncordon",      "Uncordon K8s node"),
        ]

    def effective_k8s_drain_status(self) -> str:
        if not self.k8s_cordoned:
            return "clear"
        if self.phase == NodePhase.RUNNING and self.get_step("drain_k8s"):
            return "in_progress"
        if self.k8s_drain_status in {"drained", "blocked", "error"}:
            return self.k8s_drain_status
        return "clear"

    def effective_nova_evacuation_status(self) -> str:
        if not self.is_compute:
            return "clear"
        if self.phase == NodePhase.RUNNING and self.get_step("disable_nova"):
            return "in_progress"
        if self.nova_evacuation_status in {"blocked", "error"}:
            return self.nova_evacuation_status
        if self.compute_status != "disabled":
            return "clear"
        if (self.vm_count or 0) > 0 or (self.amphora_count or 0) > 0:
            return "clear"
        if self.nova_evacuation_status == "evacuated":
            return "evacuated"
        return "clear"

    def maintenance_ready(self) -> bool:
        k8s_ready = self.effective_k8s_drain_status() == "drained"
        nova_ready = self.effective_nova_evacuation_status() == "evacuated"
        if self.is_compute:
            return k8s_ready and nova_ready
        return k8s_ready
