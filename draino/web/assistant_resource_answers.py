"""Focused assistant answers for first-class infrastructure resources."""
from __future__ import annotations

from typing import Any

from .assistant_resolution import resolve_assistant_resource
from .resource_helpers import (
    get_floating_ips,
    get_port_detail,
    get_router_detail,
    get_security_group_detail,
    get_volume_detail,
)


def _choice_from_candidate(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    label = str(item.get("label") or item.get("name") or item.get("id") or "unknown")
    parts: list[str] = []
    project_name = str(item.get("project_name") or "").strip()
    project_id = str(item.get("project_id") or "").strip()
    compute_host = str(item.get("compute_host") or "").strip()
    if project_name:
        parts.append(project_name)
    elif project_id:
        parts.append(project_id)
    if compute_host:
        parts.append(compute_host)
    return {
        "kind": kind,
        "id": str(item.get("id") or ""),
        "label": label,
        "subtext": " • ".join(parts),
    }


def _clarification_response(intent: str, answer: str, question: str, kind: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "intent": intent,
        "answer": answer,
        "confidence": 0.4,
        "entities": candidates[:3],
        "actions": [],
        "needs_clarification": True,
        "clarifying_question": question,
        "choices": [_choice_from_candidate(kind, item) for item in candidates[:5]],
    }


def _resolve_with_locked_id(intent: str, kind: str, name: str, auth, project_id: str = "", resolved_id: str = "") -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
    candidates: list[dict[str, Any]] = []
    match = None
    if resolved_id:
        _match, resolved_candidates = resolve_assistant_resource(kind, name or resolved_id, auth, project_id=project_id)
        candidates = resolved_candidates
        if _match is not None and str(_match.get("id") or "") == resolved_id:
            match = _match
        else:
            match = next((item for item in resolved_candidates if str(item.get("id") or "") == resolved_id), None)
        if match is not None:
            return match, [match], None
    match, candidates = resolve_assistant_resource(kind, name, auth, project_id=project_id)
    if match is not None:
        return match, candidates, None
    if candidates:
        labels = ", ".join(item.get("label") or item.get("id") or "unknown" for item in candidates[:3])
        noun = {
            "port": "port",
            "router": "router",
            "floatingip": "floating IP",
            "volume": "volume",
        }.get(kind, kind or "resource")
        return None, candidates, _clarification_response(
            intent,
            f"I found multiple matching {noun}s: {labels}.",
            f"Which {noun} did you mean?",
            kind,
            candidates,
        )
    return None, candidates, {
        "intent": intent,
        "answer": f"I could not find a {kind or 'resource'} named {name}.",
        "confidence": 0.2,
        "entities": [],
        "actions": [],
        "needs_clarification": True,
    }


def answer_port_attachment_status(name: str, auth, project_context: dict[str, str] | None = None, resolved_id: str = "") -> dict[str, Any]:
    context = project_context or {}
    match, _candidates, failure = _resolve_with_locked_id("port_attachment_status", "port", name, auth, project_id=context.get("project_id", ""), resolved_id=resolved_id)
    if failure is not None:
        return failure
    detail = get_port_detail(str(match.get("id") or ""), auth)
    attached_kind = str(detail.get("attached_kind") or "")
    attached_name = detail.get("attached_name") or detail.get("attached_id") or "nothing obvious"
    network_name = detail.get("network_name") or detail.get("network_id") or "unknown network"
    if attached_kind == "instance":
        answer = f"Port {detail.get('name') or detail.get('id')} is attached to instance {attached_name} on {detail.get('compute_host') or 'unknown host'} on network {network_name}."
    elif attached_kind == "router":
        answer = f"Port {detail.get('name') or detail.get('id')} is attached to router {attached_name} on network {network_name}."
    elif attached_kind == "loadbalancer":
        answer = f"Port {detail.get('name') or detail.get('id')} is attached to load balancer {attached_name} on network {network_name}."
    else:
        answer = f"Port {detail.get('name') or detail.get('id')} is not attached to a VM, router, or load balancer. It is on network {network_name}."
    actions = [{"type": "open_port", "id": detail.get("id", ""), "label": "Open Port"}]
    if attached_kind == "instance" and detail.get("attached_id") and detail.get("compute_host"):
        actions.append({"type": "open_instance", "id": detail.get("attached_id", ""), "compute_host": detail.get("compute_host", ""), "label": "Open VM"})
    elif attached_kind == "router" and detail.get("attached_id"):
        actions.append({"type": "open_router", "id": detail.get("attached_id", ""), "label": "Open Router"})
    elif attached_kind == "loadbalancer" and detail.get("attached_id"):
        actions.append({"type": "open_load_balancer", "id": detail.get("attached_id", ""), "label": "Open Load Balancer"})
    return {
        "intent": "port_attachment_status",
        "answer": answer,
        "confidence": 0.92,
        "entities": [{"kind": "port", "id": detail.get("id", ""), "label": detail.get("name") or detail.get("id") or name}],
        "actions": actions,
        "needs_clarification": False,
        "scope": {"project_id": detail.get("project_id", ""), "project_name": detail.get("project_name", "")},
    }


def answer_router_external_status(name: str, auth, project_context: dict[str, str] | None = None, resolved_id: str = "") -> dict[str, Any]:
    context = project_context or {}
    match, _candidates, failure = _resolve_with_locked_id("router_external_status", "router", name, auth, project_id=context.get("project_id", ""), resolved_id=resolved_id)
    if failure is not None:
        return failure
    detail = get_router_detail(str(match.get("id") or ""), auth)
    gateway = detail.get("external_gateway") or {}
    external_network = gateway.get("network_name") or gateway.get("network_id") or ""
    enable_snat = gateway.get("enable_snat")
    if external_network:
        snat_text = " with SNAT enabled" if enable_snat else " with SNAT disabled" if enable_snat is not None else ""
        answer = f"Router {detail.get('name') or detail.get('id')} is external via {external_network}{snat_text}."
    else:
        answer = f"Router {detail.get('name') or detail.get('id')} does not have an external gateway configured."
    actions = [{"type": "open_router", "id": detail.get("id", ""), "label": "Open Router"}]
    if gateway.get("network_id"):
        actions.append({"type": "open_network", "id": gateway.get("network_id", ""), "label": "Open External Network"})
    return {
        "intent": "router_external_status",
        "answer": answer,
        "confidence": 0.93,
        "entities": [{"kind": "router", "id": detail.get("id", ""), "label": detail.get("name") or detail.get("id") or name}],
        "actions": actions,
        "needs_clarification": False,
        "scope": {"project_id": detail.get("project_id", ""), "project_name": detail.get("project_name", "")},
    }


def answer_volume_attachment_summary(name: str, auth, project_context: dict[str, str] | None = None, resolved_id: str = "") -> dict[str, Any]:
    context = project_context or {}
    match, _candidates, failure = _resolve_with_locked_id("volume_attachment_summary", "volume", name, auth, project_id=context.get("project_id", ""), resolved_id=resolved_id)
    if failure is not None:
        return failure
    detail = get_volume_detail(str(match.get("id") or ""), auth)
    attachments = list(detail.get("attachments") or [])
    if attachments:
        labels = ", ".join(
            item.get("server_name") or item.get("server_id") or item.get("host_name") or "unknown attachment"
            for item in attachments[:3]
        )
        answer = f"Volume {detail.get('name') or detail.get('id')} is attached to {labels}."
    else:
        answer = f"Volume {detail.get('name') or detail.get('id')} is not currently attached to any instance."
    return {
        "intent": "volume_attachment_summary",
        "answer": answer,
        "confidence": 0.92,
        "entities": [{"kind": "volume", "id": detail.get("id", ""), "label": detail.get("name") or detail.get("id") or name}],
        "actions": [{"type": "open_volume", "id": detail.get("id", ""), "label": "Open Volume"}],
        "needs_clarification": False,
        "scope": {"project_id": detail.get("project_id", ""), "project_name": detail.get("project_name", "")},
    }


def answer_floating_ip_instance_status(name: str, auth, project_context: dict[str, str] | None = None, resolved_id: str = "") -> dict[str, Any]:
    context = project_context or {}
    match, _candidates, failure = _resolve_with_locked_id("floating_ip_instance_status", "floatingip", name, auth, project_id=context.get("project_id", ""), resolved_id=resolved_id)
    if failure is not None:
        return failure
    project_id = str(match.get("project_id") or context.get("project_id") or "")
    floating_ips = get_floating_ips(auth, project_id=project_id) if project_id else get_floating_ips(auth)
    detail = next((item for item in floating_ips if item.get("id") == match.get("id")), None) or match
    if detail.get("instance_id") and detail.get("compute_host"):
        answer = f"Floating IP {detail.get('floating_ip_address') or detail.get('id')} belongs to VM {detail.get('instance_name') or detail.get('instance_id')} on {detail.get('compute_host')}."
    elif detail.get("instance_name") or detail.get("fixed_ip_address") or detail.get("port_id"):
        answer = f"Floating IP {detail.get('floating_ip_address') or detail.get('id')} is associated with {detail.get('instance_name') or detail.get('fixed_ip_address') or detail.get('port_id')} but not a fully resolved VM."
    else:
        answer = f"Floating IP {detail.get('floating_ip_address') or detail.get('id')} is not currently associated with a VM."
    actions = [{"type": "open_project_network_family", "project_id": detail.get("project_id", ""), "family": "floatingips", "label": "Open Floating IPs"}]
    if detail.get("instance_id") and detail.get("compute_host"):
        actions.append({"type": "open_instance", "id": detail.get("instance_id", ""), "compute_host": detail.get("compute_host", ""), "label": "Open VM"})
    return {
        "intent": "floating_ip_instance_status",
        "answer": answer,
        "confidence": 0.92,
        "entities": [{"kind": "floatingip", "id": detail.get("id", ""), "label": detail.get("floating_ip_address") or detail.get("id") or name}],
        "actions": actions,
        "needs_clarification": False,
        "scope": {"project_id": detail.get("project_id", ""), "project_name": detail.get("project_name", "")},
    }


def answer_floating_ip_lookup_by_fixed_ip(fixed_ip: str, auth, project_context: dict[str, str] | None = None) -> dict[str, Any]:
    context = project_context or {}
    project_id = str(context.get("project_id") or "")
    floating_ips = get_floating_ips(auth, project_id=project_id) if project_id else get_floating_ips(auth)
    matches = [item for item in floating_ips if str(item.get("fixed_ip_address") or "").strip() == str(fixed_ip or "").strip()]
    if not matches:
        return {
            "intent": "floating_ip_lookup_by_fixed_ip",
            "answer": f"I could not find a floating IP associated with fixed IP {fixed_ip}.",
            "confidence": 0.3,
            "entities": [],
            "actions": [{"type": "open_project_network_family", "project_id": project_id, "family": "floatingips", "label": "Open Floating IPs"}] if project_id else [],
            "needs_clarification": True,
        }
    if len(matches) > 1:
        labels = ", ".join(item.get("floating_ip_address") or item.get("id") or "unknown" for item in matches[:3])
        return {
            "intent": "floating_ip_lookup_by_fixed_ip",
            "answer": f"I found multiple floating IPs for fixed IP {fixed_ip}: {labels}.",
            "confidence": 0.5,
            "entities": matches[:3],
            "actions": [{"type": "open_project_network_family", "project_id": matches[0].get("project_id", ""), "family": "floatingips", "label": "Open Floating IPs"}],
            "needs_clarification": True,
        }
    detail = matches[0]
    answer = (
        f"Fixed IP {fixed_ip} is associated with floating IP {detail.get('floating_ip_address') or detail.get('id')}."
        if detail.get("floating_ip_address")
        else f"Fixed IP {fixed_ip} has a matching floating-IP record, but the floating IP address is unavailable."
    )
    actions = [{"type": "open_project_network_family", "project_id": detail.get("project_id", ""), "family": "floatingips", "label": "Open Floating IPs"}]
    if detail.get("instance_id") and detail.get("compute_host"):
        actions.append({"type": "open_instance", "id": detail.get("instance_id", ""), "compute_host": detail.get("compute_host", ""), "label": "Open VM"})
    return {
        "intent": "floating_ip_lookup_by_fixed_ip",
        "answer": answer,
        "confidence": 0.94,
        "entities": [{"kind": "floatingip", "id": detail.get("id", ""), "label": detail.get("floating_ip_address") or detail.get("id") or fixed_ip}],
        "actions": actions,
        "needs_clarification": False,
        "scope": {"project_id": detail.get("project_id", ""), "project_name": detail.get("project_name", "")},
    }


def answer_port_public_exposure(name: str, auth, project_context: dict[str, str] | None = None, resolved_id: str = "") -> dict[str, Any]:
    context = project_context or {}
    match, _candidates, failure = _resolve_with_locked_id("port_public_exposure", "port", name, auth, project_id=context.get("project_id", ""), resolved_id=resolved_id)
    if failure is not None:
        return failure
    detail = get_port_detail(str(match.get("id") or ""), auth)
    floating_ips = list(detail.get("floating_ips") or [])
    public_findings: list[str] = []
    for group in detail.get("security_groups") or []:
        group_id = str(group.get("id") or "")
        if not group_id:
            continue
        try:
            sg_detail = get_security_group_detail(group_id, auth)
        except Exception:
            continue
        severity = str((sg_detail.get("audit") or {}).get("severity") or "").lower()
        if severity in {"critical", "high"}:
            public_findings.append(group.get("name") or group_id)
    if floating_ips and public_findings:
        answer = f"Port {detail.get('name') or detail.get('id')} looks publicly exposed: it has floating IPs and attached high-risk security groups {', '.join(public_findings[:3])}."
    elif floating_ips:
        answer = f"Port {detail.get('name') or detail.get('id')} is reachable through floating IPs, but I did not detect a high-risk security-group audit finding on its attached groups."
    elif public_findings:
        answer = f"Port {detail.get('name') or detail.get('id')} does not have a floating IP, but attached security groups {', '.join(public_findings[:3])} have high-risk audit findings."
    else:
        answer = f"Port {detail.get('name') or detail.get('id')} does not currently look publicly exposed based on floating IPs and attached security-group audit findings."
    actions = [{"type": "open_port", "id": detail.get("id", ""), "label": "Open Port"}]
    for group in (detail.get("security_groups") or [])[:1]:
        if group.get("id"):
            actions.append({"type": "open_networking_security_group", "id": group.get("id", ""), "label": "Open Security Group"})
    return {
        "intent": "port_public_exposure",
        "answer": answer,
        "confidence": 0.9,
        "entities": [{"kind": "port", "id": detail.get("id", ""), "label": detail.get("name") or detail.get("id") or name}],
        "actions": actions,
        "needs_clarification": False,
        "scope": {"project_id": detail.get("project_id", ""), "project_name": detail.get("project_name", "")},
    }


def answer_router_connectivity_summary(name: str, auth, project_context: dict[str, str] | None = None, resolved_id: str = "") -> dict[str, Any]:
    context = project_context or {}
    match, _candidates, failure = _resolve_with_locked_id("router_connectivity_summary", "router", name, auth, project_id=context.get("project_id", ""), resolved_id=resolved_id)
    if failure is not None:
        return failure
    detail = get_router_detail(str(match.get("id") or ""), auth)
    joins = detail.get("joins") or {}
    networks = int(joins.get("attached_network_count", 0) or 0)
    instances = int(joins.get("routed_instance_count", 0) or 0)
    lbs = int(joins.get("routed_load_balancer_count", 0) or 0)
    if networks >= 8 or instances >= 20 or lbs >= 5:
        answer = (
            f"Router {detail.get('name') or detail.get('id')} is fairly central: {networks} attached networks, "
            f"{instances} routed instances, and {lbs} routed load balancers."
        )
    else:
        answer = (
            f"Router {detail.get('name') or detail.get('id')} is not especially dense: {networks} attached networks, "
            f"{instances} routed instances, and {lbs} routed load balancers."
        )
    return {
        "intent": "router_connectivity_summary",
        "answer": answer,
        "confidence": 0.91,
        "entities": [{"kind": "router", "id": detail.get("id", ""), "label": detail.get("name") or detail.get("id") or name}],
        "actions": [{"type": "open_router", "id": detail.get("id", ""), "label": "Open Router"}],
        "needs_clarification": False,
        "scope": {"project_id": detail.get("project_id", ""), "project_name": detail.get("project_name", "")},
    }


def answer_volume_posture(name: str, auth, project_context: dict[str, str] | None = None, resolved_id: str = "") -> dict[str, Any]:
    context = project_context or {}
    match, _candidates, failure = _resolve_with_locked_id("volume_posture", "volume", name, auth, project_id=context.get("project_id", ""), resolved_id=resolved_id)
    if failure is not None:
        return failure
    detail = get_volume_detail(str(match.get("id") or ""), auth)
    answer = (
        f"Volume {detail.get('name') or detail.get('id')} is "
        f"{'bootable' if detail.get('bootable') else 'not bootable'}, "
        f"{'encrypted' if detail.get('encrypted') else 'not encrypted'}, and "
        f"{'multiattach-enabled' if detail.get('multiattach') else 'not multiattach'}."
    )
    return {
        "intent": "volume_posture",
        "answer": answer,
        "confidence": 0.94,
        "entities": [{"kind": "volume", "id": detail.get("id", ""), "label": detail.get("name") or detail.get("id") or name}],
        "actions": [{"type": "open_volume", "id": detail.get("id", ""), "label": "Open Volume"}],
        "needs_clarification": False,
        "scope": {"project_id": detail.get("project_id", ""), "project_name": detail.get("project_name", "")},
    }


def answer_floating_ip_idle_status(name: str, auth, project_context: dict[str, str] | None = None, resolved_id: str = "") -> dict[str, Any]:
    context = project_context or {}
    match, _candidates, failure = _resolve_with_locked_id("floating_ip_idle_status", "floatingip", name, auth, project_id=context.get("project_id", ""), resolved_id=resolved_id)
    if failure is not None:
        return failure
    project_id = str(match.get("project_id") or context.get("project_id") or "")
    floating_ips = get_floating_ips(auth, project_id=project_id) if project_id else get_floating_ips(auth)
    detail = next((item for item in floating_ips if item.get("id") == match.get("id")), None) or match
    is_idle = not (detail.get("instance_id") or detail.get("port_id") or detail.get("fixed_ip_address"))
    if is_idle:
        answer = f"Floating IP {detail.get('floating_ip_address') or detail.get('id')} appears idle; it is not associated with a port or VM."
    else:
        answer = f"Floating IP {detail.get('floating_ip_address') or detail.get('id')} is in use and associated with {detail.get('instance_name') or detail.get('fixed_ip_address') or detail.get('port_id') or 'a resource'}."
    return {
        "intent": "floating_ip_idle_status",
        "answer": answer,
        "confidence": 0.93,
        "entities": [{"kind": "floatingip", "id": detail.get("id", ""), "label": detail.get("floating_ip_address") or detail.get("id") or name}],
        "actions": [{"type": "open_project_network_family", "project_id": detail.get("project_id", ""), "family": "floatingips", "label": "Open Floating IPs"}],
        "needs_clarification": False,
        "scope": {"project_id": detail.get("project_id", ""), "project_name": detail.get("project_name", "")},
    }
