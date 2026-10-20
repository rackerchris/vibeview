"""Node-focused API routes."""
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ...operations import k8s_ops, openstack_ops
from .api_issues import build_api_issue
from ..latency import measure_latency

router = APIRouter()
_RESOURCE_DETAIL_TIMEOUT_SECONDS = float(os.getenv("DRAINO_RESOURCE_DETAIL_TIMEOUT_SECONDS", "10"))

_get_session_record_getter: Callable[[], Callable[[Request], object]] | None = None
_get_network_detail_getter: Callable[[], Callable[[str, openstack_ops.OpenStackAuth | None], dict]] | None = None


def configure(
    *,
    get_session_record: Callable[[], Callable[[Request], object]],
    get_network_detail: Callable[[], Callable[[str, openstack_ops.OpenStackAuth | None], dict]],
) -> None:
    global _get_session_record_getter, _get_network_detail_getter
    _get_session_record_getter = get_session_record
    _get_network_detail_getter = get_network_detail


def _require_session_record() -> Callable[[Request], object]:
    if _get_session_record_getter is None:
        raise RuntimeError("node routes are not configured")
    return _get_session_record_getter()


def _require_network_detail() -> Callable[[str, openstack_ops.OpenStackAuth | None], dict]:
    if _get_network_detail_getter is None:
        raise RuntimeError("node routes are not configured")
    return _get_network_detail_getter()


async def _run_with_timeout(func, *args):
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, func, *args),
        timeout=_RESOURCE_DETAIL_TIMEOUT_SECONDS,
    )


class AnnotationPatch(BaseModel):
    key: str
    value: str | None = None


class ManagedNoSchedulePatch(BaseModel):
    enabled: bool


class InstancePortStatsRequest(BaseModel):
    port_ids: list[str]


def _resident_state_map(state) -> dict[str, dict]:
    if state is None:
        return {}
    result: dict[str, dict] = {}
    for item in (state.instances or []):
        result[item.id] = {
            "migration_status": item.migration_status,
            "failover_status": item.failover_status,
            "lb_id": item.lb_id,
            "status": item.status,
            "is_amphora": bool(item.is_amphora),
        }
    return result


def _merge_evacuation_residents(items: list[dict], state) -> dict:
    state_map = _resident_state_map(state)
    merged: list[dict] = []
    for item in items:
        workflow = state_map.get(item.get("id"), {})
        is_amphora = bool(item.get("is_amphora") or workflow.get("is_amphora"))
        merged.append({
            **item,
            "is_amphora": is_amphora,
            "migration_status": workflow.get("migration_status"),
            "failover_status": workflow.get("failover_status"),
            "lb_id": workflow.get("lb_id"),
        })

    regular_vms = [item for item in merged if not item.get("is_amphora")]
    amphora = [item for item in merged if item.get("is_amphora")]
    active = [item for item in regular_vms if str(item.get("status") or "").upper() == "ACTIVE"]
    migrating = [item for item in regular_vms if item.get("migration_status") not in (None, "", "complete", "failed")]
    failed = [item for item in regular_vms if item.get("migration_status") == "failed"]
    failover_pending = [item for item in amphora if item.get("failover_status") not in (None, "", "complete", "failed")]
    failover_failed = [item for item in amphora if item.get("failover_status") == "failed"]

    return {
        "items": merged,
        "summary": {
            "instances": len(merged),
            "regular_vms": len(regular_vms),
            "amphora": len(amphora),
            "active": len(active),
            "migrating": len(migrating),
            "failed": len(failed),
            "failover_pending": len(failover_pending),
            "failover_failed": len(failover_failed),
        },
    }


@router.get("/api/ovn/lsp/{port_id}")
async def api_ovn_port_detail(port_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(k8s_ops.get_ovn_port_detail, port_id, session.server.k8s_auth)
        return {"port": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {"port": None, "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading OVN port detail", "api_issue": None}
    except Exception as exc:
        return {"port": None, "error": str(exc), "api_issue": None}


@router.get("/api/networks/{network_id}/ovn")
async def api_network_ovn(network_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(k8s_ops.get_ovn_logical_switch, network_id, session.server.k8s_auth)
        return {"ovn": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {"ovn": None, "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading OVN network detail", "api_issue": None}
    except Exception as exc:
        return {"ovn": None, "error": str(exc), "api_issue": None}


@router.get("/api/nodes/{node_name}/detail")
async def api_node_detail(node_name: str, request: Request):
    with measure_latency("node_detail"):
        session = _require_session_record()(request)
        server = session.server
        force_refresh = request.query_params.get("refresh", "").strip().lower() in {"1", "true", "yes"}
        if force_refresh:
            server.invalidate_node_detail(node_name)
        cached = server.get_cached_node_detail(node_name)
        if cached is not None:
            return cached
        loop = asyncio.get_running_loop()
        state = server.node_states.get(node_name)
        k8s_future = loop.run_in_executor(None, k8s_ops.get_node_k8s_detail, node_name, server.k8s_auth)
        hw_future = loop.run_in_executor(
            None,
            k8s_ops.get_node_hardware_info,
            node_name,
            state.hypervisor if state else None,
        )

        nova: dict = {}
        placement: dict = {}
        api_issue = None
        if state and state.is_compute and server.openstack_auth is not None:
            try:
                nova = await asyncio.wait_for(
                    loop.run_in_executor(None, openstack_ops.get_hypervisor_detail, state.hypervisor, server.openstack_auth),
                    timeout=_RESOURCE_DETAIL_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                api_issue = build_api_issue("Nova", f"GET /os-hypervisors/{state.hypervisor}", exc)
                nova = {}
            try:
                placement = await asyncio.wait_for(
                    loop.run_in_executor(None, openstack_ops.get_hypervisor_placement_detail, state.hypervisor, server.openstack_auth),
                    timeout=_RESOURCE_DETAIL_TIMEOUT_SECONDS,
                )
            except Exception:
                placement = {}

        try:
            k8s = await asyncio.wait_for(k8s_future, timeout=_RESOURCE_DETAIL_TIMEOUT_SECONDS)
        except Exception as exc:
            k8s = {"error": str(exc)}
        try:
            hw = await asyncio.wait_for(hw_future, timeout=_RESOURCE_DETAIL_TIMEOUT_SECONDS)
        except Exception as exc:
            hw = {"error": f"Hardware info timed out or failed: {exc}"}
        payload = {"k8s": k8s, "nova": nova, "placement": placement, "hw": hw, "error": None, "api_issue": api_issue}
        server.set_cached_node_detail(node_name, payload)
        return payload


@router.get("/api/nodes/{node_name}/metrics")
async def api_node_metrics(node_name: str, request: Request):
    with measure_latency("node_metrics"):
        session = _require_session_record()(request)
        server = session.server
        force_refresh = request.query_params.get("refresh", "").strip().lower() in {"1", "true", "yes"}
        if force_refresh:
            server.invalidate_node_metrics(node_name)
        cached = server.get_cached_node_metrics(node_name)
        if cached is not None:
            return cached
        loop = asyncio.get_running_loop()
        state = server.node_states.get(node_name)
        payload = await loop.run_in_executor(
            None,
            k8s_ops.get_node_monitor_metrics,
            node_name,
            state.hypervisor if state else None,
        )
        server.set_cached_node_metrics(node_name, payload)
        return payload


@router.get("/api/nodes/{node_name}/drain-preflight")
async def api_node_drain_preflight(node_name: str, request: Request):
    session = _require_session_record()(request)
    try:
        payload = await _run_with_timeout(k8s_ops.get_drain_preflight, node_name, session.server.k8s_auth)
        return payload
    except TimeoutError:
        return {
            "node": node_name,
            "evictable": [],
            "skipped": [],
            "blockers": [],
            "summary": {"evictable": 0, "skipped": 0, "blocking": 0, "state": "error"},
            "warning": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading drain preflight",
        }
    except Exception as exc:
        return {
            "node": node_name,
            "evictable": [],
            "skipped": [],
            "blockers": [],
            "summary": {"evictable": 0, "skipped": 0, "blocking": 0, "state": "error"},
            "warning": None,
            "error": str(exc),
        }


@router.get("/api/nodes/{node_name}/evacuate-preflight")
async def api_node_evacuate_preflight(node_name: str, request: Request):
    session = _require_session_record()(request)
    state = session.server.node_states.get(node_name)
    if state is None:
        return {"node": node_name, "items": [], "summary": {}, "warning": None, "error": f"Unknown node '{node_name}'"}
    if not state.is_compute:
        return {
            "node": node_name,
            "items": [],
            "summary": {"instances": 0, "regular_vms": 0, "amphora": 0, "volume_backed": 0, "ephemeral": 0},
            "warning": "Node is not a compute host; Nova evacuation is not required.",
            "error": None,
        }
    if session.server.openstack_auth is None:
        return {
            "node": node_name,
            "items": [],
            "summary": {"instances": 0, "regular_vms": 0, "amphora": 0, "volume_backed": 0, "ephemeral": 0},
            "warning": None,
            "error": "OpenStack credentials are required for evacuation preflight.",
        }
    try:
        items = await _run_with_timeout(openstack_ops.get_instances_preflight, state.hypervisor, session.server.openstack_auth)
    except TimeoutError:
        return {
            "node": node_name,
            "items": [],
            "summary": {"instances": 0, "regular_vms": 0, "amphora": 0, "volume_backed": 0, "ephemeral": 0},
            "warning": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading evacuation preflight",
        }
    except Exception as exc:
        return {
            "node": node_name,
            "items": [],
            "summary": {"instances": 0, "regular_vms": 0, "amphora": 0, "volume_backed": 0, "ephemeral": 0},
            "warning": None,
            "error": str(exc),
        }

    regular_vms = [item for item in items if not item.get("is_amphora")]
    amphora = [item for item in items if item.get("is_amphora")]
    volume_backed = [item for item in regular_vms if item.get("is_volume_backed")]
    ephemeral = [item for item in regular_vms if not item.get("is_volume_backed")]
    active = [item for item in regular_vms if str(item.get("status") or "").upper() == "ACTIVE"]
    warning: str | None = None
    if amphora:
        warning = f"{len(amphora)} Amphora instance(s) will require load-balancer failover handling."
    elif ephemeral:
        warning = f"{len(ephemeral)} ephemeral VM(s) may require migration fallback depending on hypervisor policy."
    elif regular_vms:
        warning = f"{len(active)} active VM(s) look like live-migration candidates."

    return {
        "node": node_name,
        "items": items,
        "summary": {
            "instances": len(items),
            "regular_vms": len(regular_vms),
            "amphora": len(amphora),
            "volume_backed": len(volume_backed),
            "ephemeral": len(ephemeral),
            "active": len(active),
        },
        "warning": warning,
        "error": None,
    }


@router.get("/api/nodes/{node_name}/evacuation-residents")
async def api_node_evacuation_residents(node_name: str, request: Request):
    session = _require_session_record()(request)
    state = session.server.node_states.get(node_name)
    if state is None:
        return {"node": node_name, "items": [], "summary": {}, "warning": None, "error": f"Unknown node '{node_name}'"}
    if not state.is_compute:
        return {
            "node": node_name,
            "items": [],
            "summary": {"instances": 0, "regular_vms": 0, "amphora": 0, "active": 0, "migrating": 0, "failed": 0, "failover_pending": 0, "failover_failed": 0},
            "warning": "Node is not a compute host; no Nova residents apply.",
            "error": None,
        }
    if session.server.openstack_auth is None:
        return {
            "node": node_name,
            "items": [],
            "summary": {"instances": 0, "regular_vms": 0, "amphora": 0, "active": 0, "migrating": 0, "failed": 0, "failover_pending": 0, "failover_failed": 0},
            "warning": None,
            "error": "OpenStack credentials are required to inspect current Nova residents.",
        }
    try:
        current = await _run_with_timeout(openstack_ops.get_instances_preflight, state.hypervisor, session.server.openstack_auth)
        merged = _merge_evacuation_residents(current, state)
        warning = None
        if merged["summary"]["instances"]:
            warning = f"{merged['summary']['instances']} instance(s) currently remain on this host."
        return {
            "node": node_name,
            "items": merged["items"],
            "summary": merged["summary"],
            "warning": warning,
            "error": None,
        }
    except TimeoutError:
        return {
            "node": node_name,
            "items": [],
            "summary": {"instances": 0, "regular_vms": 0, "amphora": 0, "active": 0, "migrating": 0, "failed": 0, "failover_pending": 0, "failover_failed": 0},
            "warning": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading current evacuation residents",
        }
    except Exception as exc:
        return {
            "node": node_name,
            "items": [],
            "summary": {"instances": 0, "regular_vms": 0, "amphora": 0, "active": 0, "migrating": 0, "failed": 0, "failover_pending": 0, "failover_failed": 0},
            "warning": None,
            "error": str(exc),
        }


@router.get("/api/nodes/{node_name}/network-stats")
async def api_node_network_stats(node_name: str, request: Request):
    with measure_latency("node_network_stats"):
        session = _require_session_record()(request)
        loop = asyncio.get_running_loop()
        state = session.server.node_states.get(node_name)
        return await loop.run_in_executor(
            None,
            k8s_ops.get_node_network_stats,
            node_name,
            state.hypervisor if state else None,
        )


@router.get("/api/nodes/{node_name}/irq-balance")
async def api_node_irq_balance(node_name: str, request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    state = session.server.node_states.get(node_name)
    return await loop.run_in_executor(
        None,
        k8s_ops.get_node_irq_balance,
        node_name,
        state.hypervisor if state else None,
    )


@router.get("/api/nodes/{node_name}/sar-trends")
async def api_node_sar_trends(node_name: str, request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    state = session.server.node_states.get(node_name)
    return await loop.run_in_executor(
        None,
        k8s_ops.get_node_sar_trends,
        node_name,
        state.hypervisor if state else None,
    )


@router.post("/api/nodes/{node_name}/instance-port-stats")
async def api_node_instance_port_stats(node_name: str, payload: InstancePortStatsRequest, request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    state = session.server.node_states.get(node_name)
    return await loop.run_in_executor(
        None,
        k8s_ops.get_node_instance_port_stats,
        node_name,
        payload.port_ids,
        session.server.k8s_auth,
        state.hypervisor if state else None,
    )


@router.get("/api/nodes/{node_name}/ovn-annotations")
async def api_node_ovn_annotations(node_name: str, request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, k8s_ops.get_node_ovn_annotations, node_name, session.server.k8s_auth)


@router.post("/api/nodes/{node_name}/ovn-annotations")
async def api_patch_ovn_annotation(node_name: str, payload: AnnotationPatch, request: Request):
    session = _require_session_record()(request)
    if payload.key not in k8s_ops.OVN_ANNOTATION_KEYS:
        return {"ok": False, "error": f"Key {payload.key!r} not in allowed OVN annotation keys"}
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None, k8s_ops.patch_node_annotation, node_name, payload.key, payload.value, session.server.k8s_auth
        )
        return {"ok": True, "error": None}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/api/nodes/{node_name}/taints/noschedule")
async def api_patch_managed_noschedule_taint(node_name: str, payload: ManagedNoSchedulePatch, request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None,
            k8s_ops.set_managed_noschedule_taint,
            node_name,
            payload.enabled,
            session.server.k8s_auth,
        )
        session.server.start_refresh(silent=True)
        return {"ok": True, "error": None}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/nodes/{node_name}/network-interfaces")
async def api_node_network_interfaces(node_name: str, request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    state = session.server.node_states.get(node_name)
    return await loop.run_in_executor(
        None, k8s_ops.get_node_network_interfaces, node_name, state.hypervisor if state else None
    )


@router.get("/api/nodes/{node_name}/instances/{instance_id}")
async def api_node_instance_detail(node_name: str, instance_id: str, request: Request):
    with measure_latency("vm_detail"):
        session = _require_session_record()(request)
        server = session.server
        loop = asyncio.get_running_loop()

        if server.openstack_auth is None:
            return {"instance": None, "error": "OpenStack credentials not provided for this session.", "api_issue": None}

        try:
            instance = await loop.run_in_executor(
                None,
                openstack_ops.get_instance_network_detail,
                instance_id,
                server.openstack_auth,
            )
        except Exception as exc:
            return {"instance": None, "error": str(exc), "api_issue": build_api_issue("Nova", f"GET /servers/{instance_id}", exc)}

        enriched_ports = []
        for port in instance.get("ports", []):
            payload = dict(port)
            ovn = None
            ovn_error = None
            if port.get("id") and port.get("network_id"):
                try:
                    ovn = await loop.run_in_executor(
                        None,
                        k8s_ops.get_ovn_port_logical_switch,
                        port["id"],
                        port["network_id"],
                        server.k8s_auth,
                    )
                except Exception as exc:
                    ovn_error = str(exc)
            payload["ovn"] = ovn
            payload["ovn_error"] = ovn_error
            enriched_ports.append(payload)
        instance["ports"] = enriched_ports

        state = server.node_states.get(node_name)
        if state and state.hypervisor and instance.get("compute_host") and instance["compute_host"] != state.hypervisor:
            instance["node_mismatch"] = {
                "requested_node": node_name,
                "requested_hypervisor": state.hypervisor,
                "actual_hypervisor": instance["compute_host"],
            }

        return {"instance": instance, "error": None, "api_issue": None}


@router.get("/api/networks/{network_id}")
async def api_network_detail(network_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(_require_network_detail(), network_id, session.server.openstack_auth)
        return {"network": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "network": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading network details",
            "api_issue": None,
        }
    except Exception as exc:
        return {"network": None, "error": str(exc), "api_issue": build_api_issue("Neutron", f"GET /v2.0/networks/{network_id}", exc)}
