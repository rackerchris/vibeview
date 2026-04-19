"""Targeted entity resolution for assistant queries."""
from __future__ import annotations

from typing import Any

from .resource_helpers import (
    _search_score,
    get_floating_ips,
    get_load_balancers,
    get_networks,
    get_ports,
    get_project_instances,
    get_projects,
    get_routers,
    get_security_groups,
    get_volumes,
)


def _candidate(kind: str, query: str, label: str, fields: list[object], *, item_id: str = "", subtext: str = "", **extra) -> dict[str, Any] | None:
    score = _search_score(query, fields)
    if score <= 0:
        return None
    return {
        "kind": kind,
        "id": item_id,
        "label": label,
        "subtext": subtext,
        "score": score,
        **extra,
    }


def _rank_candidates(query: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    needle = str(query or "").strip().lower()
    ranked = [item for item in items if item]
    exact = [item for item in ranked if str(item.get("label") or "").strip().lower() == needle]
    rest = [item for item in ranked if item not in exact]
    rest.sort(key=lambda item: (-int(item.get("score", 0)), str(item.get("label") or "").lower(), str(item.get("id") or "")))
    return exact + rest


def _project_candidates(auth, query: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in get_projects(auth, search=query):
        candidate = _candidate(
            "project",
            query,
            item.get("project_name") or item.get("project_id") or "Project",
            [item.get("project_name"), item.get("project_id"), item.get("description")],
            item_id=item.get("project_id", ""),
            subtext=item.get("project_id", ""),
            project_id=item.get("project_id", ""),
            project_name=item.get("project_name", ""),
        )
        if candidate:
            items.append(candidate)
    return _rank_candidates(query, items)


def _instance_candidates(auth, query: str, project_id: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in get_project_instances(auth, project_id=project_id):
        candidate = _candidate(
            "instance",
            query,
            item.get("name") or item.get("id") or "Instance",
            [
                item.get("name"),
                item.get("id"),
                item.get("project_name"),
                item.get("project_id"),
                item.get("compute_host"),
                item.get("status"),
                *(item.get("fixed_ips") or []),
                *(item.get("floating_ips") or []),
            ],
            item_id=item.get("id", ""),
            subtext=f"{item.get('project_name') or item.get('project_id') or 'Project'} • {item.get('compute_host') or item.get('status') or 'UNKNOWN'}",
            project_id=item.get("project_id", ""),
            project_name=item.get("project_name", ""),
            compute_host=item.get("compute_host", ""),
        )
        if candidate:
            items.append(candidate)
    return _rank_candidates(query, items)


def _network_candidates(auth, query: str, project_id: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in get_networks(auth, project_id=project_id):
        candidate = _candidate(
            "network",
            query,
            item.get("name") or item.get("id") or "Network",
            [item.get("name"), item.get("id"), item.get("network_type"), item.get("status"), item.get("project_id")],
            item_id=item.get("id", ""),
            subtext=f"{item.get('network_type') or 'Network'} • {item.get('status') or 'UNKNOWN'}",
            project_id=item.get("project_id", ""),
        )
        if candidate:
            items.append(candidate)
    return _rank_candidates(query, items)


def _router_candidates(auth, query: str, project_id: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in get_routers(auth, project_id=project_id):
        candidate = _candidate(
            "router",
            query,
            item.get("name") or item.get("id") or "Router",
            [item.get("name"), item.get("id"), item.get("status"), item.get("external_network_name"), item.get("external_network_id"), item.get("project_id")],
            item_id=item.get("id", ""),
            subtext=f"{item.get('status') or 'UNKNOWN'} • {item.get('external_network_name') or item.get('external_network_id') or 'no external network'}",
            project_id=item.get("project_id", ""),
        )
        if candidate:
            items.append(candidate)
    return _rank_candidates(query, items)


def _port_candidates(auth, query: str, project_id: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in get_ports(auth, project_id=project_id):
        candidate = _candidate(
            "port",
            query,
            item.get("name") or item.get("id") or "Port",
            [
                item.get("name"),
                item.get("id"),
                item.get("network_name"),
                item.get("network_id"),
                item.get("attached_name"),
                item.get("attached_id"),
                item.get("device_owner"),
                item.get("project_id"),
                item.get("mac_address"),
                *(item.get("fixed_ip_addresses") or []),
                *[ip.get("address") for ip in (item.get("floating_ips") or [])],
            ],
            item_id=item.get("id", ""),
            subtext=f"{item.get('network_name') or item.get('network_id') or 'Port'} • {item.get('attached_name') or item.get('device_owner') or item.get('status') or 'UNKNOWN'}",
            project_id=item.get("project_id", ""),
            project_name=item.get("project_name", ""),
            compute_host=item.get("compute_host", ""),
        )
        if candidate:
            items.append(candidate)
    return _rank_candidates(query, items)


def _floating_ip_candidates(auth, query: str, project_id: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in get_floating_ips(auth, project_id=project_id):
        candidate = _candidate(
            "floatingip",
            query,
            item.get("floating_ip_address") or item.get("id") or "Floating IP",
            [
                item.get("id"),
                item.get("floating_ip_address"),
                item.get("fixed_ip_address"),
                item.get("instance_name"),
                item.get("instance_id"),
                item.get("port_id"),
                item.get("status"),
                item.get("project_id"),
            ],
            item_id=item.get("id", ""),
            subtext=f"{item.get('project_name') or item.get('project_id') or 'Project'} • {item.get('instance_name') or item.get('fixed_ip_address') or item.get('status') or 'Floating IP'}",
            project_id=item.get("project_id", ""),
            project_name=item.get("project_name", ""),
            compute_host=item.get("compute_host", ""),
        )
        if candidate:
            items.append(candidate)
    return _rank_candidates(query, items)


def _load_balancer_candidates(auth, query: str, project_id: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in get_load_balancers(auth, project_id=project_id):
        candidate = _candidate(
            "loadbalancer",
            query,
            item.get("name") or item.get("id") or "Load Balancer",
            [item.get("name"), item.get("id"), item.get("vip_address"), item.get("floating_ip"), item.get("project_id"), item.get("operating_status"), item.get("provisioning_status")],
            item_id=item.get("id", ""),
            subtext=f"{item.get('vip_address') or 'no VIP'} • {item.get('operating_status') or 'UNKNOWN'}",
            project_id=item.get("project_id", ""),
        )
        if candidate:
            items.append(candidate)
    return _rank_candidates(query, items)


def _volume_candidates(auth, query: str, project_id: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    volumes, _all_projects = get_volumes(auth)
    for item in volumes:
        if project_id and item.get("project_id") != project_id:
            continue
        candidate = _candidate(
            "volume",
            query,
            item.get("name") or item.get("id") or "Volume",
            [item.get("name"), item.get("id"), item.get("volume_type"), item.get("status"), item.get("backend_name"), item.get("project_id")],
            item_id=item.get("id", ""),
            subtext=f"{item.get('volume_type') or 'Volume'} • {item.get('status') or 'UNKNOWN'} • {item.get('size_gb') or 0} GB",
            project_id=item.get("project_id", ""),
        )
        if candidate:
            items.append(candidate)
    return _rank_candidates(query, items)


def _security_group_candidates(auth, query: str, project_id: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in get_security_groups(auth):
        if project_id and item.get("project_id") != project_id:
            continue
        candidate = _candidate(
            "securitygroup",
            query,
            item.get("name") or item.get("id") or "Security Group",
            [item.get("name"), item.get("id"), item.get("project_name"), item.get("project_id"), item.get("description"), (item.get("audit") or {}).get("severity")],
            item_id=item.get("id", ""),
            subtext=f"{item.get('project_name') or item.get('project_id') or 'Security Group'} • {(item.get('audit') or {}).get('severity') or 'unknown'}",
            project_id=item.get("project_id", ""),
            project_name=item.get("project_name", ""),
        )
        if candidate:
            items.append(candidate)
    return _rank_candidates(query, items)


_KIND_BUILDERS = {
    "project": _project_candidates,
    "instance": _instance_candidates,
    "network": _network_candidates,
    "router": _router_candidates,
    "port": _port_candidates,
    "floatingip": _floating_ip_candidates,
    "loadbalancer": _load_balancer_candidates,
    "volume": _volume_candidates,
    "securitygroup": _security_group_candidates,
}


def assistant_candidates(kind: str, query: str, auth, project_id: str = "") -> list[dict[str, Any]]:
    builder = _KIND_BUILDERS.get(str(kind or "").strip().lower())
    if builder is None:
        return []
    if str(kind or "").strip().lower() == "project":
        return builder(auth, query)
    return builder(auth, query, project_id=project_id)


def resolve_assistant_resource(kind: str, query: str, auth, project_id: str = "") -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    candidates = assistant_candidates(kind, query, auth, project_id=project_id)
    if not candidates:
        return None, []
    needle = str(query or "").strip().lower()
    exact = [item for item in candidates if str(item.get("label") or "").strip().lower() == needle]
    if len(exact) == 1:
        return exact[0], candidates
    if len(candidates) == 1:
        return candidates[0], candidates
    top_score = candidates[0].get("score", 0)
    top = [item for item in candidates if item.get("score", 0) == top_score]
    if len(top) == 1:
        return top[0], candidates
    return None, candidates
