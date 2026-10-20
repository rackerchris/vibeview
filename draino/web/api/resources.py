"""OpenStack resource routes."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from functools import partial

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ...operations import k8s_ops, openstack_ops
from ..assistant_service import answer_assistant_query, assistant_provider_status, stream_assistant_query, validate_assistant_provider
from .api_issues import build_api_issue
from ..op_locks import RENEW_INTERVAL_SECONDS, clone_lock_name, holder_identity, leases_disabled
from ..resource_helpers import (
    CloneWorkflowError,
    clone_instance,
    get_floating_ips,
    get_flavors,
    get_keypairs,
    get_server_groups,
    get_port_detail,
    get_ports,
    get_project_instances,
    get_project_inventory,
    get_projects,
    get_load_balancer_detail,
    get_load_balancers,
    get_network_detail,
    get_networks,
    retype_volume,
    get_security_group_detail,
    get_security_groups,
    get_router_detail,
    get_routers,
    get_swift_containers,
    get_volume_backups,
    get_volume_detail,
    get_volume_snapshots,
    get_volumes,
    repair_subnet_metadata_port,
    search_resources,
    update_project_quota_limit,
)

router = APIRouter()
_LOGGER = logging.getLogger("draino.web")
_RESOURCE_DETAIL_TIMEOUT_SECONDS = float(os.getenv("DRAINO_RESOURCE_DETAIL_TIMEOUT_SECONDS", "10"))
_ASSISTANT_QUERY_TIMEOUT_SECONDS = float(os.getenv("DRAINO_ASSISTANT_QUERY_TIMEOUT_SECONDS", "20"))
_clone_status_lock = threading.Lock()
_clone_status_by_instance: dict[str, dict] = {}
_EVENT_FACTORY = threading.Event

_get_session_record_getter: Callable[[], Callable[[Request], object]] | None = None


def configure(*, get_session_record: Callable[[], Callable[[Request], object]]) -> None:
    global _get_session_record_getter
    _get_session_record_getter = get_session_record


def _require_session_record() -> Callable[[Request], object]:
    if _get_session_record_getter is None:
        raise RuntimeError("resource routes are not configured")
    return _get_session_record_getter()


def _set_clone_status(instance_id: str, payload: dict) -> None:
    with _clone_status_lock:
        _clone_status_by_instance[instance_id] = {
            **payload,
            "updated_at": time.time(),
        }


def _clone_status(instance_id: str) -> dict:
    with _clone_status_lock:
        return dict(_clone_status_by_instance.get(instance_id) or {})


def _clone_status_append_timeline(instance_id: str, entry: dict) -> dict:
    with _clone_status_lock:
        current = dict(_clone_status_by_instance.get(instance_id) or {})
        timeline = list(current.get("timeline") or [])
        timeline.append({
            **entry,
            "updated_at": time.time(),
        })
        current["timeline"] = timeline
        current["updated_at"] = time.time()
        _clone_status_by_instance[instance_id] = current
        return dict(current)


def _clone_progress_status(
    instance_id: str,
    *,
    new_name: str,
    step_key: str,
    step_label: str,
    detail: str,
    state: str = "running",
    error: str | None = None,
    extra: dict | None = None,
) -> None:
    current = _clone_status(instance_id)
    payload = {
        **current,
        "state": state,
        "source_instance_id": instance_id,
        "new_name": new_name,
        "step_key": step_key,
        "step_label": step_label,
        "detail": detail,
        "started_at": current.get("started_at") or time.time(),
        "error": error,
    }
    if extra:
        payload.update(extra)
    _set_clone_status(instance_id, payload)
    _clone_status_append_timeline(instance_id, {
        "step_key": step_key,
        "step_label": step_label,
        "detail": detail,
        "state": state,
        "error": error,
        **(extra or {}),
    })


async def _run_with_timeout(func, *args):
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, func, *args),
        timeout=_RESOURCE_DETAIL_TIMEOUT_SECONDS,
    )


async def _run_assistant_with_timeout(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func, *args)


@router.get("/api/networks")
async def api_networks(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_networks, session.server.openstack_auth)
        return {"networks": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"networks": [], "error": str(exc), "api_issue": build_api_issue("Neutron", "GET /v2.0/networks", exc)}


@router.get("/api/volumes")
async def api_volumes(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data, all_projects = await loop.run_in_executor(None, get_volumes, session.server.openstack_auth)
        return {"volumes": data, "all_projects": all_projects, "error": None, "api_issue": None}
    except Exception as exc:
        return {"volumes": [], "all_projects": False, "error": str(exc), "api_issue": None}


@router.get("/api/projects")
async def api_projects(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    search = str(request.query_params.get("search") or "")
    try:
        data = await loop.run_in_executor(None, get_projects, session.server.openstack_auth, search)
        return {"projects": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"projects": [], "error": str(exc), "api_issue": build_api_issue("OpenStack", "GET aggregated project inventory", exc)}


@router.get("/api/search")
async def api_search(request: Request):
    session = _require_session_record()(request)
    query = str(request.query_params.get("q") or "").strip()
    try:
        limit = int(request.query_params.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    if not query:
        return {"results": [], "error": None, "api_issue": None}
    try:
        data = await _run_with_timeout(search_resources, session.server.openstack_auth, query, max(1, min(limit, 50)), session.server.k8s_auth)
        return {"results": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "results": [],
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while searching resources",
            "api_issue": None,
        }
    except Exception as exc:
        return {"results": [], "error": str(exc), "api_issue": build_api_issue("OpenStack", f"GET aggregated search for query {query}", exc)}


@router.post("/api/assistant/query")
async def api_assistant_query(request: Request):
    session = _require_session_record()(request)
    payload = await request.json()
    query = str(payload.get("query") or "").strip()
    selected_project_id = str(payload.get("selected_project_id") or "").strip()
    selected_project_name = str(payload.get("selected_project_name") or "").strip()
    active_view = str(payload.get("active_view") or "").strip()
    resolved_kind = str(payload.get("resolved_kind") or "").strip()
    resolved_id = str(payload.get("resolved_id") or "").strip()
    resolved_label = str(payload.get("resolved_label") or "").strip()
    previous_result = payload.get("previous_result") if isinstance(payload.get("previous_result"), dict) else None
    if not query:
        return {"result": None, "error": "Query is required.", "api_issue": None}
    try:
        if previous_result is not None:
            runner = partial(
                answer_assistant_query,
                query,
                session.server.openstack_auth,
                session.server.k8s_auth,
                selected_project_id,
                selected_project_name,
                active_view,
                resolved_kind,
                resolved_id,
                resolved_label,
                previous_result=previous_result,
            )
            data = await _run_assistant_with_timeout(runner)
        else:
            data = await _run_assistant_with_timeout(
                answer_assistant_query,
                query,
                session.server.openstack_auth,
                session.server.k8s_auth,
                selected_project_id,
                selected_project_name,
                active_view,
                resolved_kind,
                resolved_id,
                resolved_label,
            )
        return {"result": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "result": None,
            "error": f"Timed out after {_ASSISTANT_QUERY_TIMEOUT_SECONDS:.0f}s while answering assistant query",
            "api_issue": None,
        }
    except Exception as exc:
        return {"result": None, "error": str(exc), "api_issue": build_api_issue("Assistant", "POST /api/assistant/query", exc)}


@router.post("/api/assistant/query/stream")
async def api_assistant_query_stream(request: Request):
    session = _require_session_record()(request)
    payload = await request.json()
    query = str(payload.get("query") or "").strip()
    selected_project_id = str(payload.get("selected_project_id") or "").strip()
    selected_project_name = str(payload.get("selected_project_name") or "").strip()
    active_view = str(payload.get("active_view") or "").strip()
    resolved_kind = str(payload.get("resolved_kind") or "").strip()
    resolved_id = str(payload.get("resolved_id") or "").strip()
    resolved_label = str(payload.get("resolved_label") or "").strip()
    previous_result = payload.get("previous_result") if isinstance(payload.get("previous_result"), dict) else None
    if not query:
        return JSONResponse(status_code=400, content={"error": "Query is required.", "api_issue": None})
    _LOGGER.info("assistant stream start query=%r", query[:120])

    async def event_stream():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict] = asyncio.Queue()
        stream_started = loop.time()
        first_event_logged = False

        def push(event: dict):
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def worker():
            try:
                for event in stream_assistant_query(
                    query,
                    session.server.openstack_auth,
                    session.server.k8s_auth,
                    selected_project_id,
                    selected_project_name,
                    active_view,
                    resolved_kind,
                    resolved_id,
                    resolved_label,
                    **({"previous_result": previous_result} if previous_result is not None else {}),
                ):
                    push(event)
            except Exception as exc:
                push({"type": "error", "error": str(exc), "api_issue": build_api_issue("Assistant", "POST /api/assistant/query/stream", exc)})
            finally:
                push({"type": "done"})

        task = loop.run_in_executor(None, worker)
        try:
            while True:
                event = await queue.get()
                nonlocal_first = False
                if not first_event_logged and event.get("type") in {"meta", "delta", "final", "error"}:
                    first_event_logged = True
                    nonlocal_first = True
                if nonlocal_first:
                    _LOGGER.info(
                        "assistant stream first event query=%r type=%s elapsed_ms=%.1f",
                        query[:120],
                        event.get("type"),
                        (loop.time() - stream_started) * 1000,
                    )
                yield (json.dumps(event) + "\n").encode("utf-8")
                if event.get("type") in {"done", "error"}:
                    break
        finally:
            await task

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.get("/api/assistant/status")
async def api_assistant_status(request: Request):
    _require_session_record()(request)
    try:
        return {"status": assistant_provider_status(), "error": None, "api_issue": None}
    except Exception as exc:
        return {"status": None, "error": str(exc), "api_issue": build_api_issue("Assistant", "GET /api/assistant/status", exc)}


@router.post("/api/assistant/validate")
async def api_assistant_validate(request: Request):
    _require_session_record()(request)
    try:
        data = await _run_with_timeout(validate_assistant_provider)
        return {"validation": data, "error": None if data.get("ok") else data.get("error") or None, "api_issue": None}
    except TimeoutError:
        return {
            "validation": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while validating assistant provider",
            "api_issue": None,
        }
    except Exception as exc:
        return {"validation": None, "error": str(exc), "api_issue": build_api_issue("Assistant", "POST /api/assistant/validate", exc)}


@router.get("/api/projects/{project_id}/inventory")
async def api_project_inventory(project_id: str, request: Request):
    session = _require_session_record()(request)
    section = str(request.query_params.get("section") or "instances")
    family = str(request.query_params.get("family") or "")
    try:
        data = await _run_with_timeout(get_project_inventory, project_id, session.server.openstack_auth, section, family)
        return {"inventory": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "inventory": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading project inventory",
            "api_issue": None,
        }
    except Exception as exc:
        return {"inventory": None, "error": str(exc), "api_issue": build_api_issue("OpenStack", f"GET aggregated inventory for project {project_id}", exc)}


@router.post("/api/projects/{project_id}/quota")
async def api_project_quota_update(project_id: str, request: Request):
    session = _require_session_record()(request)
    if not session.is_admin:
        return JSONResponse(
            status_code=403,
            content={
                "inventory": None,
                "error": "Quota modification requires the OpenStack 'admin' role.",
                "api_issue": None,
            },
        )
    payload = await request.json()
    section = str(payload.get("section") or "").strip().lower()
    resource = str(payload.get("resource") or "").strip().lower()
    limit = payload.get("limit")
    service = {
        "compute": "Nova",
        "network": "Neutron",
        "block_storage": "Cinder",
    }.get(section, "OpenStack")
    try:
        data = await _run_with_timeout(
            update_project_quota_limit,
            project_id,
            section,
            resource,
            limit,
            session.server.openstack_auth,
        )
        return {"inventory": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "inventory": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while updating project quota",
            "api_issue": None,
        }
    except Exception as exc:
        return {
            "inventory": None,
            "error": str(exc),
            "api_issue": build_api_issue(service, f"PUT quota {section}.{resource} for project {project_id}", exc),
        }


@router.get("/api/instances/{instance_id}")
async def api_instance_detail(instance_id: str, request: Request):
    session = _require_session_record()(request)
    skip_ovn = str(request.query_params.get("skip_ovn") or "").strip().lower() in {"1", "true", "yes", "on"}
    try:
        data = await _run_with_timeout(openstack_ops.get_instance_network_detail, instance_id, session.server.openstack_auth)
        loop = asyncio.get_running_loop()
        enriched_ports = []
        for port in data.get("ports", []):
            payload = dict(port)
            ovn = None
            ovn_error = None
            if not skip_ovn and session.server.k8s_auth and port.get("id") and port.get("network_id"):
                try:
                    ovn = await loop.run_in_executor(
                        None,
                        k8s_ops.get_ovn_port_logical_switch,
                        port["id"],
                        port["network_id"],
                        session.server.k8s_auth,
                    )
                except Exception as exc:
                    ovn_error = str(exc)
            payload["ovn"] = ovn
            payload["ovn_error"] = ovn_error
            enriched_ports.append(payload)
        data["ports"] = enriched_ports
        return {"instance": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "instance": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading instance details",
            "api_issue": None,
        }
    except Exception as exc:
        return {"instance": None, "error": str(exc), "api_issue": build_api_issue("Nova", f"GET /servers/{instance_id}", exc)}


@router.get("/api/instances/{instance_id}/clone-source")
async def api_instance_clone_source_detail(instance_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(openstack_ops.get_instance_network_detail, instance_id, session.server.openstack_auth)
        return {"instance": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "instance": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading clone source details",
            "api_issue": None,
        }
    except Exception as exc:
        return {"instance": None, "error": str(exc), "api_issue": build_api_issue("Nova", f"GET clone source /servers/{instance_id}", exc)}


@router.get("/api/floating-ips")
async def api_floating_ips(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_floating_ips, session.server.openstack_auth)
        return {"floating_ips": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"floating_ips": [], "error": str(exc), "api_issue": build_api_issue("Neutron", "GET /v2.0/floatingips", exc)}


@router.get("/api/flavors")
async def api_flavors(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_flavors, session.server.openstack_auth)
        return {"flavors": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"flavors": [], "error": str(exc), "api_issue": build_api_issue("Nova", "GET /flavors/detail", exc)}


@router.get("/api/keypairs")
async def api_keypairs(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_keypairs, session.server.openstack_auth)
        return {"keypairs": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"keypairs": [], "error": str(exc), "api_issue": build_api_issue("Nova", "GET /os-keypairs", exc)}


@router.get("/api/server-groups")
async def api_server_groups(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_server_groups, session.server.openstack_auth)
        return {"server_groups": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"server_groups": [], "error": str(exc), "api_issue": build_api_issue("Nova", "GET /os-server-groups", exc)}


@router.get("/api/ports")
async def api_ports(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_ports, session.server.openstack_auth)
        return {"ports": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"ports": [], "error": str(exc), "api_issue": build_api_issue("Neutron", "GET /v2.0/ports", exc)}


@router.get("/api/ports/{port_id}")
async def api_port_detail(port_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(get_port_detail, port_id, session.server.openstack_auth)
        return {"port": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "port": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading port details",
            "api_issue": None,
        }
    except Exception as exc:
        return {"port": None, "error": str(exc), "api_issue": build_api_issue("Neutron", f"GET /v2.0/ports/{port_id}", exc)}


@router.get("/api/volumes/{volume_id}")
async def api_volume_detail(volume_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(get_volume_detail, volume_id, session.server.openstack_auth)
        return {"volume": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "volume": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading volume details",
            "api_issue": None,
        }
    except Exception as exc:
        return {"volume": None, "error": str(exc), "api_issue": build_api_issue("Cinder", f"GET /v3/volumes/{volume_id}", exc)}


@router.post("/api/volumes/{volume_id}/retype")
async def api_volume_retype(volume_id: str, request: Request):
    session = _require_session_record()(request)
    payload = await request.json()
    target_type = str(payload.get("target_type") or "").strip()
    migration_policy = str(payload.get("migration_policy") or "on-demand").strip().lower()
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(
            None,
            retype_volume,
            volume_id,
            target_type,
            migration_policy,
            session.server.openstack_auth,
        )
        return {"result": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {
            "result": None,
            "error": str(exc),
            "api_issue": build_api_issue("Cinder", f"POST /v3/volumes/{volume_id}/action os-retype", exc),
        }


@router.post("/api/instances/{instance_id}/clone")
async def api_instance_clone(instance_id: str, request: Request):
    session = _require_session_record()(request)
    payload = await request.json()
    new_name = str(payload.get("new_name") or "").strip()
    flavor_id = str(payload.get("flavor_id") or "").strip()
    raw_key_name = payload.get("key_name", None)
    key_name = None if raw_key_name is None else str(raw_key_name)
    raw_server_group_id = payload.get("server_group_id", None)
    server_group_id = None if raw_server_group_id is None else str(raw_server_group_id)
    clone_data_volumes = bool(payload.get("clone_data_volumes", True))
    create_floating_ip = bool(payload.get("create_floating_ip", True))
    power_on = bool(payload.get("power_on", True))
    keep_metadata = bool(payload.get("keep_metadata", True))
    user_data_mode = str(payload.get("user_data_mode") or "preserve").strip().lower() or "preserve"
    user_data_text = str(payload.get("user_data_text") or "")
    raw_port_overrides = payload.get("port_overrides")
    port_overrides = raw_port_overrides if isinstance(raw_port_overrides, list) else []
    _LOGGER.info(
        "clone api request instance_id=%s new_name=%s flavor_id=%s key_name=%s server_group_id=%s clone_data_volumes=%s create_floating_ip=%s power_on=%s keep_metadata=%s user_data_mode=%s port_override_count=%s",
        instance_id,
        new_name,
        flavor_id,
        key_name,
        server_group_id,
        clone_data_volumes,
        create_floating_ip,
        power_on,
        keep_metadata,
        user_data_mode,
        len(port_overrides),
    )
    existing = _clone_status(instance_id)
    if existing and str(existing.get("state") or "") == "running":
        return {
            "result": None,
            "error": "A clone is already running for this source instance.",
            "api_issue": None,
        }
    # Cluster-wide exclusivity: one in-flight clone per source instance across
    # all replicas/sessions (the in-process status above is a convenience layer).
    lock_name = clone_lock_name(instance_id)
    lock_holder = holder_identity(session.server.operator_username, session.server.session_id)
    auth = session.server.k8s_auth
    if leases_disabled():
        acquired, existing_lease = True, None
    else:
        acquired, existing_lease = k8s_ops.acquire_operation_lease(lock_name, lock_holder, auth=auth)
    if not acquired:
        expired = k8s_ops.operation_lease_expired(lock_name, auth=auth)
        if expired is None:
            return {
                "result": None,
                "error": "Cannot verify the cluster-wide clone lock (Kubernetes API unreachable); refusing to start.",
                "api_issue": None,
            }
        if expired and k8s_ops.take_over_expired_operation_lease(lock_name, lock_holder, auth=auth):
            acquired = True
    if not acquired:
        held_by = (existing_lease or {}).get("holder") or "another operator"
        return {
            "result": None,
            "error": f"A clone is already running for this source instance (held by {held_by}).",
            "api_issue": None,
        }
    _set_clone_status(instance_id, {
        "state": "running",
        "source_instance_id": instance_id,
        "new_name": new_name,
        "step_key": "queued",
        "step_label": "Queued",
        "detail": "Clone request accepted and waiting to start.",
        "started_at": time.time(),
        "result": None,
        "error": None,
        "timeline": [{
            "step_key": "queued",
            "step_label": "Queued",
            "detail": "Clone request accepted and waiting to start.",
            "state": "running",
            "updated_at": time.time(),
        }],
        "created_resources": {},
        "cleanup_actions": [],
        "residual_resources": [],
    })

    def progress_cb(step_key: str, label: str, detail: str, state: str = "running", extra: dict | None = None) -> None:
        _clone_progress_status(
            instance_id,
            new_name=new_name,
            step_key=step_key,
            step_label=label,
            detail=detail,
            state=state,
            error=None,
            extra=extra,
        )

    def _release_clone_lease() -> None:
        stop_renewal.set()
        if leases_disabled():
            return
        try:
            k8s_ops.release_operation_lease(lock_name, lock_holder, auth=auth)
        except Exception as exc:
            _LOGGER.warning("clone lease release failed name=%s error=%s", lock_name, exc)

    stop_renewal = _EVENT_FACTORY()

    def _renew_clone_lease() -> None:
        while not stop_renewal.wait(RENEW_INTERVAL_SECONDS):
            if leases_disabled():
                return
            try:
                ok = k8s_ops.renew_operation_lease(lock_name, lock_holder, auth=auth)
            except Exception as exc:
                _LOGGER.warning("clone lease renew errored name=%s error=%s", lock_name, exc)
                continue
            if not ok:
                _LOGGER.error("clone lease lost name=%s holder=%s", lock_name, lock_holder)
                return

    def _run_clone() -> None:
        try:
            data = clone_instance(
                instance_id,
                new_name=new_name,
                flavor_id=flavor_id,
                key_name=key_name,
                server_group_id=server_group_id,
                clone_data_volumes=clone_data_volumes,
                create_floating_ip=create_floating_ip,
                power_on=power_on,
                keep_metadata=keep_metadata,
                user_data_mode=user_data_mode,
                user_data_text=user_data_text,
                port_overrides=port_overrides,
                progress_cb=progress_cb,
                auth=session.server.openstack_auth,
            )
            _clone_progress_status(
                instance_id,
                new_name=new_name,
                step_key="completed",
                step_label="Clone Complete",
                detail=f"Clone created as {(data or {}).get('server_name') or new_name}.",
                state="completed",
                error=None,
                extra={"result": data},
            )
            _LOGGER.info("clone api success instance_id=%s server_id=%s", instance_id, (data or {}).get("server_id"))
        except Exception as exc:
            previous = _clone_status(instance_id)
            failure_detail = previous.get("detail") or "Clone failed."
            failure_extra = {"result": None}
            if isinstance(exc, CloneWorkflowError):
                failure_detail = exc.failed_detail or failure_detail
                failure_extra.update({
                    "created_resources": exc.created_resources,
                    "cleanup_actions": exc.cleanup_actions,
                    "residual_resources": exc.residual_resources,
                })
            _clone_progress_status(
                instance_id,
                new_name=new_name,
                step_key=(getattr(exc, "failed_step_key", "") or previous.get("step_key") or "failed"),
                step_label=(getattr(exc, "failed_step_label", "") or previous.get("step_label") or "Clone Failed"),
                detail=failure_detail,
                state="failed",
                error=str(exc),
                extra=failure_extra,
            )
            _LOGGER.exception("clone api failed instance_id=%s", instance_id)
        finally:
            _release_clone_lease()

    threading.Thread(target=_run_clone, daemon=True).start()
    threading.Thread(target=_renew_clone_lease, daemon=True).start()
    return {
        "result": {
            "accepted": True,
            "source_instance_id": instance_id,
            "new_name": new_name,
            "state": "running",
            "step_key": "queued",
            "step_label": "Queued",
        },
        "error": None,
        "api_issue": None,
    }


@router.get("/api/instances/{instance_id}/clone-status")
async def api_instance_clone_status(instance_id: str, request: Request):
    _require_session_record()(request)
    status = _clone_status(instance_id)
    return {"status": status or None, "error": None, "api_issue": None}


@router.post("/api/instances/{instance_id}/clone-cleanup")
async def api_instance_clone_cleanup(instance_id: str, request: Request):
    session = _require_session_record()(request)
    payload = await request.json()
    resource_type = str(payload.get("resource_type") or "").strip()
    resource_id = str(payload.get("resource_id") or "").strip()
    if not resource_type or not resource_id:
        return {"result": None, "error": "resource_type and resource_id are required.", "api_issue": None}
    conn = openstack_ops._conn(auth=session.server.openstack_auth)
    volume_proxy = getattr(conn, "block_storage", None) or getattr(conn, "volume", None)
    try:
        if resource_type == "port":
            conn.network.delete_port(resource_id, ignore_missing=True)
        elif resource_type == "volume":
            if volume_proxy is None:
                raise RuntimeError("Block storage is not available from the current SDK connection")
            volume_proxy.delete_volume(resource_id, ignore_missing=True)
        elif resource_type == "floating_ip":
            conn.network.delete_ip(resource_id, ignore_missing=True)
        elif resource_type == "server":
            conn.compute.delete_server(resource_id, ignore_missing=True)
        elif resource_type == "snapshot_image":
            conn.image.delete_image(resource_id, ignore_missing=True)
        else:
            raise ValueError(f"Unsupported clone cleanup resource type: {resource_type}")
        status = _clone_status(instance_id)
        residual_resources = [
            item for item in list(status.get("residual_resources") or [])
            if not (str(item.get("resource_type") or "") == resource_type and str(item.get("resource_id") or "") == resource_id)
        ]
        cleanup_actions = list(status.get("cleanup_actions") or []) + [{
            "resource_type": resource_type,
            "resource_id": resource_id,
            "state": "deleted_by_operator",
        }]
        _set_clone_status(instance_id, {
            **status,
            "cleanup_actions": cleanup_actions,
            "residual_resources": residual_resources,
        })
        _clone_status_append_timeline(instance_id, {
            "step_key": "cleanup_manual",
            "step_label": "Manual Cleanup",
            "detail": f"Operator deleted leftover {resource_type} {resource_id}.",
            "state": "completed",
            "resource_type": resource_type,
            "resource_id": resource_id,
        })
        return {
            "result": {
                "resource_type": resource_type,
                "resource_id": resource_id,
                "residual_resources": residual_resources,
            },
            "error": None,
            "api_issue": None,
        }
    except Exception as exc:
        return {"result": None, "error": str(exc), "api_issue": build_api_issue("OpenStack", f"POST clone cleanup {resource_type} {resource_id}", exc)}


@router.get("/api/volume-snapshots")
async def api_volume_snapshots(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_volume_snapshots, session.server.openstack_auth)
        return {"snapshots": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"snapshots": [], "error": str(exc), "api_issue": build_api_issue("Cinder", "GET /v3/snapshots", exc)}


@router.get("/api/volume-backups")
async def api_volume_backups(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_volume_backups, session.server.openstack_auth)
        return {"backups": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"backups": [], "error": str(exc), "api_issue": build_api_issue("Cinder", "GET /v3/backups", exc)}


@router.get("/api/swift-containers")
async def api_swift_containers(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_swift_containers, session.server.openstack_auth)
        return {"containers": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"containers": [], "error": str(exc), "api_issue": None}


@router.get("/api/load-balancers")
async def api_load_balancers(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_load_balancers, session.server.openstack_auth)
        return {"load_balancers": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"load_balancers": [], "error": str(exc), "api_issue": build_api_issue("Octavia", "GET /v2/lbaas/loadbalancers", exc)}


@router.get("/api/load-balancers/{lb_id}")
async def api_load_balancer_detail(lb_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(get_load_balancer_detail, lb_id, session.server.openstack_auth)
        return {"load_balancer": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "load_balancer": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading load balancer details",
            "api_issue": None,
        }
    except Exception as exc:
        return {"load_balancer": None, "error": str(exc), "api_issue": build_api_issue("Octavia", f"GET /v2/lbaas/loadbalancers/{lb_id}", exc)}


@router.get("/api/security-groups")
async def api_security_groups(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_security_groups, session.server.openstack_auth)
        return {"security_groups": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"security_groups": [], "error": str(exc), "api_issue": build_api_issue("Neutron", "GET /v2.0/security-groups", exc)}


@router.get("/api/security-groups/{group_id}")
async def api_security_group_detail(group_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(get_security_group_detail, group_id, session.server.openstack_auth)
        return {"security_group": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "security_group": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading security group details",
            "api_issue": None,
        }
    except Exception as exc:
        return {"security_group": None, "error": str(exc), "api_issue": build_api_issue("Neutron", f"GET /v2.0/security-groups/{group_id}", exc)}


@router.post("/api/networks/{network_id}/subnets/{subnet_id}/repair-metadata-port")
async def api_repair_subnet_metadata_port(network_id: str, subnet_id: str, request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(
            None,
            repair_subnet_metadata_port,
            network_id,
            subnet_id,
            session.server.openstack_auth,
        )
        return {"metadata_port": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {
            "metadata_port": None,
            "error": str(exc),
            "api_issue": build_api_issue("Neutron", f"POST /v2.0/ports repair metadata port for subnet {subnet_id}", exc),
        }


@router.get("/api/routers")
async def api_routers(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_routers, session.server.openstack_auth)
        return {"routers": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"routers": [], "error": str(exc), "api_issue": build_api_issue("Neutron", "GET /v2.0/routers", exc)}


@router.get("/api/routers/{router_id}")
async def api_router_detail(router_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(get_router_detail, router_id, session.server.openstack_auth)
        return {"router": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "router": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading router details",
            "api_issue": None,
        }
    except Exception as exc:
        return {"router": None, "error": str(exc), "api_issue": build_api_issue("Neutron", f"GET /v2.0/routers/{router_id}", exc)}


@router.get("/api/routers/{router_id}/ovn")
async def api_router_ovn(router_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(k8s_ops.get_ovn_logical_router, router_id, session.server.k8s_auth)
        return {"ovn": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {"ovn": None, "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading OVN router detail", "api_issue": None}
    except Exception as exc:
        return {"ovn": None, "error": str(exc), "api_issue": None}
