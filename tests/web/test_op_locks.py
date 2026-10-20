"""Tests for Lease-based distributed operation locks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from draino.operations import k8s_ops
from draino.web import op_locks
from draino.web.inventory_actions import InventoryActionsMixin


class _FakeLeaseSpec:
    def __init__(self, holder, renew=None, seconds=60):
        self.holder_identity = holder
        self.renew_time = renew or datetime.now(timezone.utc)
        self.lease_duration_seconds = seconds


class _FakeLease:
    def __init__(self, spec):
        self.spec = spec


class _FakeApiException(k8s_ops.ApiException):
    def __init__(self, status):
        super().__init__(reason=f"http {status}")
        self.status = status


class FakeCoordinationApi:
    """In-memory stand-in for CoordinationV1Api."""

    def __init__(self):
        self.leases: dict[str, _FakeLease] = {}

    def create_namespaced_lease(self, namespace, body):
        name = body.metadata.name
        if name in self.leases:
            raise _FakeApiException(409)
        spec = body.spec
        if not isinstance(spec, _FakeLeaseSpec):
            spec = _FakeLeaseSpec(spec.holder_identity, renew=spec.renew_time, seconds=spec.lease_duration_seconds)
        self.leases[name] = _FakeLease(spec)

    def read_namespaced_lease(self, name, namespace):
        if name not in self.leases:
            raise _FakeApiException(404)
        return self.leases[name]

    def replace_namespaced_lease(self, name, namespace, body):
        if isinstance(body.spec, _FakeLeaseSpec):
            spec = body.spec
        else:
            spec = _FakeLeaseSpec(body.spec.holder_identity, renew=body.spec.renew_time, seconds=body.spec.lease_duration_seconds)
        self.leases[name] = _FakeLease(spec)

    def delete_namespaced_lease(self, name, namespace):
        if name not in self.leases:
            raise _FakeApiException(404)
        del self.leases[name]


@pytest.fixture()
def fake_api(monkeypatch):
    api = FakeCoordinationApi()
    monkeypatch.setattr(k8s_ops.client, "CoordinationV1Api", lambda _c=None: api)
    monkeypatch.setattr(k8s_ops.client, "ApiClient", lambda _c=None: None)
    monkeypatch.setattr(op_locks, "leases_disabled", lambda: False)
    return api


def test_lock_names_are_dns_safe():
    assert op_locks.node_lock_name("drain", "node-1.example.com") == "vibeview-op-drain-node-1.example.com"
    assert op_locks.node_lock_name("enablenova", "Node A_B") == "vibeview-op-enablenova-node-a-b"
    clone = op_locks.clone_lock_name("01234567-89ab-cdef-0123-456789abcdef")
    assert clone.startswith("vibeview-op-clone-")
    assert len(clone) <= 253


def test_holder_identity_includes_pod_and_user():
    holder = op_locks.holder_identity("alice", "deadbeef-1234")
    assert "alice" in holder and "deadbe" in holder and len(holder) <= 63


def test_acquire_is_exclusive_then_releasable(fake_api):
    ok, existing = k8s_ops.acquire_operation_lease("vibeview-op-drain-x", "pod.a", 60)
    assert ok is True and existing is None
    ok, existing = k8s_ops.acquire_operation_lease("vibeview-op-drain-x", "pod.b", 60)
    assert ok is False and existing["holder"] == "pod.a"
    k8s_ops.release_operation_lease("vibeview-op-drain-x", "pod.a")
    ok, _ = k8s_ops.acquire_operation_lease("vibeview-op-drain-x", "pod.b", 60)
    assert ok is True


def test_release_is_owner_guarded(fake_api):
    k8s_ops.acquire_operation_lease("vibeview-op-reboot-x", "pod.a", 60)
    k8s_ops.release_operation_lease("vibeview-op-reboot-x", "pod.b")
    assert "vibeview-op-reboot-x" in fake_api.leases


def test_expired_lease_takeover(fake_api):
    stale = datetime.now(timezone.utc) - timedelta(seconds=120)
    fake_api.leases["vibeview-op-drain-x"] = _FakeLease(_FakeLeaseSpec("pod.dead", renew=stale))
    assert k8s_ops.operation_lease_expired("vibeview-op-drain-x") is True
    assert k8s_ops.take_over_expired_operation_lease("vibeview-op-drain-x", "pod.live", 60) is True
    fresh = datetime.now(timezone.utc)
    fake_api.leases["vibeview-op-live-x"] = _FakeLease(_FakeLeaseSpec("pod.live", renew=fresh))
    assert k8s_ops.take_over_expired_operation_lease("vibeview-op-live-x", "pod.other", 60) is False


def test_renew_detects_lost_lease(fake_api):
    k8s_ops.acquire_operation_lease("vibeview-op-undrain-x", "pod.a", 60)
    assert k8s_ops.renew_operation_lease("vibeview-op-undrain-x", "pod.a", 60) is True
    assert k8s_ops.renew_operation_lease("vibeview-op-undrain-x", "pod.b", 60) is False


def test_manager_guard_release_and_loss_tracking(fake_api):
    manager = op_locks.OperationLockManager()
    assert manager.try_acquire("vibeview-op-drain-x", "pod.a", action="drain", target="x").acquired is True
    other = manager.try_acquire("vibeview-op-drain-x", "pod.b", action="drain", target="x")
    assert other.acquired is False and other.held_by == "pod.a"
    with manager.guard("vibeview-op-drain-x", "pod.a", action="drain", target="x"):
        assert manager.lost("vibeview-op-drain-x") is False
    assert "vibeview-op-drain-x" not in fake_api.leases


def test_manager_release_releases_renewal(monkeypatch, fake_api):
    manager = op_locks.OperationLockManager()
    monkeypatch.setattr(op_locks, "RENEW_INTERVAL_SECONDS", 0.01)
    manager.try_acquire("vibeview-op-drain-x", "pod.a")
    manager.release("vibeview-op-drain-x", "pod.a")
    manager._renew_threads["vibeview-op-drain-x"].join(timeout=2)
    assert not manager._renew_threads["vibeview-op-drain-x"].is_alive()


class _StubServer(InventoryActionsMixin):
    def __init__(self, manager):
        import threading

        self._action_lock = threading.Lock()
        self._op_locks = manager
        self.operator_username = "alice"
        self.session_id = "s-1234"
        self.node_states = {}
        self.pushes: list[dict] = []
        self.audits: list[tuple] = []

    def _push(self, message):
        self.pushes.append(message)

    def log_audit(self, action, node, event, detail=""):
        self.audits.append((action, event, detail))


def test_entrypoint_gate_reports_held_elsewhere(monkeypatch, fake_api):
    manager = op_locks.OperationLockManager()
    server = _StubServer(manager)
    assert server._acquire_op_lock("vibeview-op-drain-node-1", "drain", "node-1") is True
    other = _StubServer(op_locks.OperationLockManager())
    # Simulate a different replica: same fake API, different holder identity.
    monkeypatch.setattr(op_locks, "_POD_NAME", "web-pod-2")
    assert other._acquire_op_lock("vibeview-op-drain-node-1", "drain", "node-1") is False
    assert any("already running elsewhere" in p.get("message", "") for p in other.pushes if p["type"] == "log")
    assert any(p[1] == "blocked" and "held by" in p[2] for p in other.audits)
