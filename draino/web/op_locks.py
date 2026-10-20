"""Distributed operation locks backed by Kubernetes Leases.

Web replicas must not be the only place where action exclusivity lives
(docs/multi-user-no-db-scaling-design.md, Layer 3).  Every mutating operation
on shared infrastructure acquires a short-named Lease before it starts, renews
it while the workflow runs, and releases it at the end.  Lease loss aborts the
workflow so a partitioned pod never keeps mutating infrastructure that
another replica has taken over.

In-process phase claims (``_claim_phase``) stay as the cheap per-replica layer;
the Lease is the cluster-wide layer on top.
"""
from __future__ import annotations

import hashlib
import logging
import os
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

from ..operations import k8s_ops

_LOGGER = logging.getLogger("draino.web")

LEASE_SECONDS = int(os.getenv("DRAINO_LEASE_SECONDS", "60"))
RENEW_INTERVAL_SECONDS = max(5, LEASE_SECONDS // 3)

_POD_NAME = os.getenv("DRAINO_POD_NAME") or os.getenv("HOSTNAME") or socket.gethostname()
_LOCK_PREFIX = "vibeview-op"
def leases_disabled() -> bool:
    """Tests disable real Lease calls; production never sets this."""
    return os.getenv("DRAINO_DISABLE_OP_LOCKS") == "1"


def _safe_slug(value: str) -> str:
    slug = "".join(ch if ch.isalnum() or ch in "-." else "-" for ch in value.lower())
    slug = "-".join(part for part in slug.split("-") if part)
    return slug.strip("-.") or "x"


def node_lock_name(kind: str, node_name: str) -> str:
    return f"{_LOCK_PREFIX}-{_safe_slug(kind)}-{_safe_slug(node_name)}"[:253].rstrip("-_.")


def clone_lock_name(instance_id: str) -> str:
    digest = hashlib.sha256(instance_id.encode("utf-8")).hexdigest()[:16]
    return f"{_LOCK_PREFIX}-clone-{digest}"


def holder_identity(username: str = "", session_id: str = "") -> str:
    parts = [_POD_NAME]
    if username:
        parts.append(username)
    if session_id:
        parts.append(session_id[:8])
    return ".".join(parts)[:63].strip(".")


@dataclass
class LockResult:
    acquired: bool
    held_by: str | None = None       # holder identity of the current owner, if known
    lease_error: bool = False        # lock API unreachable → operation must not run


class OperationLockError(RuntimeError):
    """Raised inside a guarded workflow when the Lease was lost or renewal failed."""


class OperationLockManager:
    """Acquire/renew/release cluster-wide Lease locks for one server instance."""

    def __init__(self, k8s_auth: k8s_ops.K8sAuth | None = None) -> None:
        self._k8s_auth = k8s_auth
        self._renew_stop: dict[str, threading.Event] = {}
        self._renew_threads: dict[str, threading.Thread] = {}
        self._holders: dict[str, str] = {}
        self._lost: set[str] = set()
        self._state_lock = threading.Lock()

    def held_names(self, holder: str) -> list[str]:
        with self._state_lock:
            return [name for name, owner in self._holders.items() if owner == holder]

    def try_acquire(self, name: str, holder: str, *, action: str = "", target: str = "") -> LockResult:
        if leases_disabled():
            with self._state_lock:
                self._holders[name] = holder
            return LockResult(acquired=True)
        acquired, existing = k8s_ops.acquire_operation_lease(name, holder, LEASE_SECONDS, auth=self._k8s_auth)
        if not acquired and existing is None:
            # Distinguish "held by someone else" (409 readable) from API failure.
            expired = k8s_ops.operation_lease_expired(name, auth=self._k8s_auth)
            if expired is None:
                return LockResult(acquired=False, lease_error=True)
            if expired and k8s_ops.take_over_expired_operation_lease(name, holder, LEASE_SECONDS, auth=self._k8s_auth):
                acquired = True
        if not acquired:
            held_by = (existing or {}).get("holder")
            return LockResult(acquired=False, held_by=held_by)
        _LOGGER.info("lock acquired name=%s holder=%s action=%s target=%s", name, holder, action, target)
        with self._state_lock:
            self._holders[name] = holder
        self._start_renewal(name, holder)
        return LockResult(acquired=True)

    def release(self, name: str, holder: str, *, action: str = "", target: str = "") -> None:
        with self._state_lock:
            if self._holders.get(name) != holder:
                return
            self._holders.pop(name, None)
        self._stop_renewal(name)
        if not leases_disabled():
            try:
                k8s_ops.release_operation_lease(name, holder, auth=self._k8s_auth)
            except Exception as exc:
                _LOGGER.warning("lock release failed name=%s holder=%s error=%s", name, holder, exc)
                return
        _LOGGER.info("lock released name=%s holder=%s action=%s target=%s", name, holder, action, target)

    def lost(self, name: str) -> bool:
        with self._state_lock:
            return name in self._lost

    @contextmanager
    def guard(self, name: str, holder: str, *, action: str = "", target: str = ""):
        """Context manager raising OperationLockError when the lock is lost mid-run."""
        with self._state_lock:
            self._lost.discard(name)
        try:
            yield
        finally:
            self.release(name, holder, action=action, target=target)

    def _start_renewal(self, name: str, holder: str) -> None:
        if leases_disabled():
            return
        stop = threading.Event()
        with self._state_lock:
            self._renew_stop[name] = stop

        def _loop() -> None:
            while not stop.wait(RENEW_INTERVAL_SECONDS):
                try:
                    ok = k8s_ops.renew_operation_lease(name, holder, LEASE_SECONDS, auth=self._k8s_auth)
                except Exception as exc:
                    _LOGGER.warning("lock renew errored name=%s error=%s; keeping lock", name, exc)
                    continue
                if not ok:
                    _LOGGER.error("lock lost name=%s holder=%s", name, holder)
                    with self._state_lock:
                        self._lost.add(name)
                    return

        thread = threading.Thread(target=_loop, daemon=True, name=f"lease-renew-{name[:40]}")
        self._renew_threads[name] = thread
        thread.start()

    def _stop_renewal(self, name: str) -> None:
        with self._state_lock:
            stop = self._renew_stop.pop(name, None)
        if stop is not None:
            stop.set()
