"""Evidence builders for grounded assistant diagnosis tasks."""
from __future__ import annotations

from typing import Any

from .resource_helpers import (
    get_floating_ips,
    get_load_balancers,
    get_networks,
    get_ports,
    get_routers,
    get_security_groups,
)


def build_project_network_diagnosis_evidence(project_id: str, project_name: str, auth) -> dict[str, Any]:
    networks = get_networks(auth, project_id=project_id)
    routers = get_routers(auth, project_id=project_id)
    ports = get_ports(auth, project_id=project_id)
    floating_ips = get_floating_ips(auth, project_id=project_id)
    load_balancers = get_load_balancers(auth, project_id=project_id)
    security_groups = [item for item in get_security_groups(auth) if item.get("project_id") == project_id]

    ports_with_fips = [item for item in ports if item.get("floating_ips")]
    unattached_ports = [item for item in ports if not item.get("attached_kind")]
    ports_missing_sgs = [item for item in ports if not item.get("security_group_ids")]
    routers_without_external = [item for item in routers if not item.get("external_network_id")]
    idle_fips = [item for item in floating_ips if not (item.get("port_id") or item.get("instance_id") or item.get("fixed_ip_address"))]
    risky_security_groups = [
        item for item in security_groups
        if str((item.get("audit") or {}).get("severity") or "").lower() in {"critical", "high"}
    ]
    degraded_load_balancers = [
        item for item in load_balancers
        if str(item.get("operating_status") or "").upper() not in {"ONLINE", "ACTIVE"}
        or str(item.get("provisioning_status") or "").upper() not in {"ACTIVE"}
    ]

    findings: list[str] = []
    if risky_security_groups:
        findings.append(f"{len(risky_security_groups)} security groups have high-risk audit findings.")
    if idle_fips:
        findings.append(f"{len(idle_fips)} floating IPs appear idle.")
    if routers_without_external:
        findings.append(f"{len(routers_without_external)} routers do not have an external gateway.")
    if degraded_load_balancers:
        findings.append(f"{len(degraded_load_balancers)} load balancers are not fully healthy.")
    if unattached_ports:
        findings.append(f"{len(unattached_ports)} ports are unattached.")
    if ports_missing_sgs:
        findings.append(f"{len(ports_missing_sgs)} ports have no security groups attached.")

    return {
        "scope": {
            "project_id": project_id,
            "project_name": project_name,
        },
        "summary": {
            "network_count": len(networks),
            "router_count": len(routers),
            "port_count": len(ports),
            "floating_ip_count": len(floating_ips),
            "load_balancer_count": len(load_balancers),
            "security_group_count": len(security_groups),
        },
        "signals": {
            "ports_with_fips": len(ports_with_fips),
            "unattached_ports": len(unattached_ports),
            "ports_missing_security_groups": len(ports_missing_sgs),
            "routers_without_external_gateway": len(routers_without_external),
            "idle_floating_ips": len(idle_fips),
            "high_risk_security_groups": len(risky_security_groups),
            "degraded_load_balancers": len(degraded_load_balancers),
        },
        "samples": {
            "risky_security_groups": [item.get("name") or item.get("id") for item in risky_security_groups[:5]],
            "idle_floating_ips": [item.get("floating_ip_address") or item.get("id") for item in idle_fips[:5]],
            "unattached_ports": [item.get("name") or item.get("id") for item in unattached_ports[:5]],
            "degraded_load_balancers": [item.get("name") or item.get("id") for item in degraded_load_balancers[:5]],
        },
        "findings": findings,
    }


def fallback_network_diagnosis_answer(evidence: dict[str, Any]) -> dict[str, Any]:
    scope = evidence.get("scope") or {}
    summary = evidence.get("summary") or {}
    findings = list(evidence.get("findings") or [])
    project_name = scope.get("project_name") or scope.get("project_id") or "this project"
    if not findings:
        findings = ["No immediately obvious networking risk signals were detected from the current evidence bundle."]
    recommendations: list[str] = []
    signals = evidence.get("signals") or {}
    if signals.get("high_risk_security_groups"):
        recommendations.append("Start with high-risk security groups and internet-facing ports.")
    if signals.get("degraded_load_balancers"):
        recommendations.append("Check degraded load balancers and their backend members next.")
    if signals.get("routers_without_external_gateway"):
        recommendations.append("Confirm routers expected to provide egress actually have external gateways.")
    if signals.get("idle_floating_ips"):
        recommendations.append("Review idle floating IPs for cleanup or stale associations.")
    if not recommendations:
        recommendations.append("Start with security groups, floating IP associations, and router external gateways.")
    answer = (
        f"Project {project_name} has {summary.get('network_count', 0)} networks, {summary.get('router_count', 0)} routers, "
        f"{summary.get('port_count', 0)} ports, and {summary.get('floating_ip_count', 0)} floating IPs. "
        f"First thing to inspect: {recommendations[0]}"
    )
    return {
        "answer": answer,
        "findings": findings[:4],
        "recommendations": recommendations[:3],
        "confidence": 0.72,
    }
