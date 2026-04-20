"""Hybrid assistant service for natural-language ops questions."""
from __future__ import annotations

import json
import os
import re
import time
from urllib.parse import urlparse
from typing import Any

import httpx

from .resource_helpers import (
    _backfill_compute_quota_usage,
    get_floating_ips,
    get_project_instances,
    get_project_quota_summary,
    get_projects,
    get_load_balancer_detail,
    get_network_detail,
    get_port_detail,
    get_router_detail,
    get_security_group_detail,
    get_volume_detail,
    get_volumes,
)
from .assistant_resolution import resolve_assistant_resource
from .assistant_evidence import build_project_network_diagnosis_evidence, fallback_network_diagnosis_answer
from .assistant_resource_answers import (
    answer_floating_ip_lookup_by_fixed_ip,
    answer_floating_ip_instance_status,
    answer_floating_ip_idle_status,
    answer_port_attachment_status,
    answer_port_public_exposure,
    answer_router_connectivity_summary,
    answer_router_external_status,
    answer_volume_attachment_summary,
    answer_volume_posture,
)
from .assistant_cluster_answers import (
    answer_cluster_health_overview,
    answer_control_plane_component_summary,
    answer_etcd_health_summary,
    answer_mariadb_node_summary,
    answer_node_ready_summary,
)
from .assistant_environment_answers import (
    ENVIRONMENT_SUMMARY_INTENTS as REGISTERED_ENVIRONMENT_SUMMARY_INTENTS,
    answer_environment_summary,
    answer_hypervisor_metric_top_n,
    hypervisor_metric_choices,
)
from .assistant_synthesis import (
    answer_domain_chat,
    answer_platform_knowledge,
    stream_domain_chat,
    stream_platform_knowledge,
    synthesize_network_diagnosis,
)
from .assistant_followups import answer_followup_query
from ..operations import openstack_ops

_ASSISTANT_BASE_URL = os.getenv("DRAINO_ASSISTANT_BASE_URL", "").strip()
_ASSISTANT_API_KEY = os.getenv("DRAINO_ASSISTANT_API_KEY", "").strip()
_ASSISTANT_MODEL = os.getenv("DRAINO_ASSISTANT_MODEL", "").strip()
_ASSISTANT_TIMEOUT_SECONDS = float(os.getenv("DRAINO_ASSISTANT_TIMEOUT_SECONDS", "10"))
_ASSISTANT_PARSE_TIMEOUT_SECONDS = float(os.getenv("DRAINO_ASSISTANT_PARSE_TIMEOUT_SECONDS", "4"))

_QUOTA_RESOURCE_MAP = {
    "floating_ips": ("network", "floatingip", "floating IPs"),
    "floatingip": ("network", "floatingip", "floating IPs"),
    "fips": ("network", "floatingip", "floating IPs"),
    "cores": ("compute", "cores", "cores"),
    "instances": ("compute", "instances", "instances"),
    "ram": ("compute", "ram", "RAM"),
    "networks": ("network", "network", "networks"),
    "routers": ("network", "router", "routers"),
    "ports": ("network", "port", "ports"),
    "load_balancers": ("network", "load_balancer", "load balancers"),
}

_INSTANCE_INTENTS = {
    "instance_status",
    "instance_location",
    "instance_project",
    "instance_ips",
    "instance_networks",
}

_PROJECT_SCOPED_INTENTS = {
    "quota_usage",
    "quota_limit",
    "resource_count",
    "project_network_summary",
    "project_storage_summary",
}

_RESOURCE_SUMMARY_INTENTS = {
    "port_attachment_status",
    "port_public_exposure",
    "router_external_status",
    "router_connectivity_summary",
    "volume_attachment_summary",
    "volume_posture",
    "floating_ip_instance_status",
    "floating_ip_lookup_by_fixed_ip",
    "floating_ip_idle_status",
    "etcd_health_summary",
    "mariadb_node_summary",
    "cluster_health_overview",
    "node_ready_summary",
    "control_plane_component_summary",
    "kernel_version_summary",
    "kernel_drift_summary",
    "kubelet_drift_summary",
    "container_runtime_drift_summary",
    "missing_node_agent_summary",
    "reboot_needed_summary",
    "node_attention_summary",
    "os_image_drift_summary",
    "control_plane_runtime_summary",
    "pod_density_summary",
    "pvc_hotspot_summary",
    "cpu_request_load_summary",
    "memory_request_load_summary",
    "namespace_spread_summary",
    "not_ready_node_summary",
    "cordoned_node_summary",
    "noschedule_node_summary",
    "empty_compute_nodes_summary",
    "busiest_hypervisor_summary",
    "emptiest_hypervisor_summary",
    "highest_ram_hypervisor_summary",
    "highest_cpu_hypervisor_summary",
    "highest_disk_hypervisor_summary",
    "hypervisor_metric_top_n",
}

_ENVIRONMENT_SUMMARY_INTENTS = set(REGISTERED_ENVIRONMENT_SUMMARY_INTENTS)

_RESOURCE_KIND_ALIASES = {
    "network": "network",
    "networks": "network",
    "router": "router",
    "routers": "router",
    "port": "port",
    "ports": "port",
    "floating ip": "floatingip",
    "floating ips": "floatingip",
    "floating-ip": "floatingip",
    "floatingips": "floatingip",
    "fip": "floatingip",
    "fips": "floatingip",
    "load balancer": "loadbalancer",
    "load balancers": "loadbalancer",
    "load-balancer": "loadbalancer",
    "loadbalancer": "loadbalancer",
    "lb": "loadbalancer",
    "lbs": "loadbalancer",
    "volume": "volume",
    "volumes": "volume",
    "project": "project",
    "projects": "project",
    "security group": "securitygroup",
    "security groups": "securitygroup",
    "security-group": "securitygroup",
    "securitygroup": "securitygroup",
    "security_group": "securitygroup",
    "vm": "instance",
    "instance": "instance",
    "instances": "instance",
}

_OPENSTACK_KNOWLEDGE_TERMS = (
    "openstack",
    "nova",
    "neutron",
    "cinder",
    "keystone",
    "glance",
    "octavia",
    "floating ip",
    "floating ips",
    "security group",
    "security groups",
    "router",
    "port",
    "network",
    "subnet",
    "interface",
    "volume",
    "snapshot",
    "backup",
    "flavor",
    "hypervisor",
    "availability zone",
    "az",
    "tenant",
    "project",
    "image",
    "boot volume",
    "live migrate",
    "migration",
    "resize",
    "rebuild",
    "reboot",
    "shelve",
    "unshelve",
    "suspend",
    "resume",
    "pause",
    "unpause",
    "freeze",
    "interface",
    "nic",
    "instance",
    "vm",
    "server",
)

_KNOWLEDGE_PHRASES = (
    "how do i",
    "how do you",
    "how to",
    "what is",
    "what are",
    "what's",
    "whats",
    "explain",
    "difference between",
    "can i",
    "when should i",
    "why would i",
    "why is",
    "why does",
    "help me understand",
)

_DOMAIN_CHAT_TERMS = (
    "openstack",
    "kubernetes",
    "k8s",
    "rackspace",
    "cloud",
    "linux",
    "windows",
    "gpu",
    "gpus",
    "cuda",
    "vmware",
    "hypervisor",
    "virtualization",
    "server",
    "servers",
    "datacenter",
    "storage",
    "networking",
    "network",
    "router",
    "firewall",
    "dns",
    "load balancer",
    "container",
    "containers",
    "docker",
    "podman",
    "kernel",
    "filesystem",
    "raid",
    "backup",
    "restore",
    "incident",
    "sre",
    "observability",
    "monitoring",
    "grafana",
    "prometheus",
    "etcd",
    "mariadb",
    "mysql",
    "apiserver",
    "scheduler",
    "controller manager",
    "controller-manager",
)

_ENVIRONMENT_QUERY_TERMS = (
    "environment",
    "fleet",
    "estate",
    "cluster",
    "hypervisor",
    "hypervisors",
    "compute node",
    "compute nodes",
    "host",
    "hosts",
    "node",
    "nodes",
    "kernel",
    "kubelet",
    "runtime",
    "container runtime",
    "containerd",
    "cri-o",
    "docker",
    "ram",
    "memory",
    "cpu",
    "vcpu",
    "vcpus",
    "disk",
    "storage",
    "node agent",
    "node-agent",
    "reboot",
    "reboots",
    "vm",
    "vms",
    "instances",
)

_OUT_OF_SCOPE_TERMS = (
    "sports",
    "nfl",
    "nba",
    "mlb",
    "nhl",
    "soccer",
    "football score",
    "baseball",
    "basketball",
    "tennis",
    "golf",
    "movie",
    "movies",
    "celebrity",
    "song",
    "songs",
    "recipe",
    "travel",
    "vacation",
    "restaurant",
    "stock price",
    "bitcoin price",
    "meaning of life",
)


def assistant_provider_enabled() -> bool:
    return bool(_ASSISTANT_BASE_URL and _ASSISTANT_API_KEY and _ASSISTANT_MODEL)


def assistant_provider_status() -> dict[str, Any]:
    parsed = urlparse(_ASSISTANT_BASE_URL) if _ASSISTANT_BASE_URL else None
    return {
        "configured": assistant_provider_enabled(),
        "base_url": _ASSISTANT_BASE_URL or "",
        "base_host": parsed.netloc if parsed is not None else "",
        "model": _ASSISTANT_MODEL or "",
        "timeout_seconds": _ASSISTANT_TIMEOUT_SECONDS,
    }


def _assistant_provider_payload(timeout: float | None = None) -> dict[str, Any]:
    return {
        "base_url": _ASSISTANT_BASE_URL,
        "api_key": _ASSISTANT_API_KEY,
        "model": _ASSISTANT_MODEL,
        "timeout": timeout if timeout is not None else min(_ASSISTANT_TIMEOUT_SECONDS, _ASSISTANT_PARSE_TIMEOUT_SECONDS),
    }


def validate_assistant_provider() -> dict[str, Any]:
    status = assistant_provider_status()
    if not status["configured"]:
        return {
            **status,
            "ok": False,
            "error": "Assistant provider is not configured.",
            "latency_ms": None,
            "response_text": "",
        }
    try:
        from openai import OpenAI
    except Exception as exc:
        return {
            **status,
            "ok": False,
            "error": f"OpenAI SDK is unavailable: {exc}",
            "latency_ms": None,
            "response_text": "",
        }
    timeout_seconds = min(_ASSISTANT_TIMEOUT_SECONDS, _ASSISTANT_PARSE_TIMEOUT_SECONDS)
    client = OpenAI(
        base_url=_ASSISTANT_BASE_URL,
        api_key=_ASSISTANT_API_KEY,
        timeout=httpx.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds), read=timeout_seconds, write=min(3.0, timeout_seconds)),
        max_retries=0,
        http_client=httpx.Client(
            timeout=httpx.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds), read=timeout_seconds, write=min(3.0, timeout_seconds)),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        ),
    )
    started = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=_ASSISTANT_MODEL,
            messages=[
                {"role": "system", "content": "Reply with exactly OK."},
                {"role": "user", "content": "Connectivity check."},
            ],
            temperature=0,
            max_tokens=8,
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        content = ""
        if response.choices:
            content = str(response.choices[0].message.content or "").strip()
        return {
            **status,
            "ok": True,
            "error": "",
            "latency_ms": latency_ms,
            "response_text": content,
        }
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return {
            **status,
            "ok": False,
            "error": str(exc),
            "latency_ms": latency_ms,
            "response_text": "",
        }
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _default_project_context(auth, selected_project_id: str = "") -> dict[str, str]:
    project_id = str(selected_project_id or "").strip()
    project_name = ""
    conn = openstack_ops._conn(auth=auth)
    if not project_id:
        project_id = str(getattr(conn, "current_project_id", None) or "").strip()
    if project_id:
        try:
            project = conn.identity.get_project(project_id)
            project_name = getattr(project, "name", None) or ""
        except Exception:
            project_name = ""
    if not project_name:
        project_name = str(getattr(auth, "project_name", None) or "").strip()
    return {"project_id": project_id, "project_name": project_name}


def _safe_json_parse(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _provider_json_completion(system_prompt: str, user_payload: dict[str, Any], timeout_seconds: float | None = None) -> dict[str, Any]:
    if not assistant_provider_enabled():
        return {}
    try:
        from openai import OpenAI
    except Exception:
        return {}
    timeout_seconds = float(timeout_seconds if timeout_seconds is not None else min(_ASSISTANT_TIMEOUT_SECONDS, _ASSISTANT_PARSE_TIMEOUT_SECONDS))
    client = OpenAI(
        base_url=_ASSISTANT_BASE_URL,
        api_key=_ASSISTANT_API_KEY,
        timeout=httpx.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds), read=timeout_seconds, write=min(3.0, timeout_seconds)),
        max_retries=0,
        http_client=httpx.Client(
            timeout=httpx.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds), read=timeout_seconds, write=min(3.0, timeout_seconds)),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        ),
    )
    try:
        response = client.chat.completions.create(
            model=_ASSISTANT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            temperature=0,
        )
        content = response.choices[0].message.content if response.choices else ""
        return _safe_json_parse(str(content or ""))
    except Exception:
        return {}
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _safe_confidence(value: Any, default: float) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    lowered = str(value or "").strip().lower()
    if not lowered:
        return default
    scale = {
        "very high": 0.95,
        "high": 0.85,
        "medium": 0.6,
        "moderate": 0.6,
        "low": 0.35,
        "very low": 0.15,
    }
    if lowered in scale:
        return scale[lowered]
    try:
        parsed = float(lowered)
    except Exception:
        return default
    if parsed > 1.0 and parsed <= 100.0:
        parsed = parsed / 100.0
    return max(0.0, min(1.0, parsed))


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(phrase in lowered for phrase in phrases)


def _contains_word(lower: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", str(lower or "").lower()) is not None


def _intent_signals(lower: str) -> dict[str, bool]:
    return {
        "count": _contains_any(lower, ("how many", "count", "total", "number of")),
        "usage": _contains_any(lower, ("using", "used", "usage", "consuming", "consume", "allocated", "have")),
        "knowledge": _contains_any(lower, _KNOWLEDGE_PHRASES),
        "limit": _contains_any(lower, ("limit", "quota", "max", "allowed")),
        "status": _contains_any(lower, ("status", "state", "health")),
        "location": _contains_any(lower, ("where", "host", "located", "running")),
        "project": _contains_any(lower, ("project", "tenant")),
        "addresses": _contains_any(lower, (" ip", " ips", "address", "addresses")),
        "attachment": _contains_any(lower, ("attach", "attached", "attachment", "connected to")),
        "ownership": _contains_any(lower, ("belong", "owner", "owned", "associated", "mapped", "mapping", "for ")),
        "exposure": _contains_any(lower, ("public", "publicly", "exposed", "internet")),
        "external": _contains_any(lower, ("external", "gateway", "internet")),
        "connectivity": _contains_any(lower, ("connected", "highly connected", "busy", "dense", "central")),
        "posture": _contains_any(lower, ("bootable", "encrypted", "multiattach")),
        "idle": _contains_word(lower, "idle") or _contains_word(lower, "unused") or _contains_word(lower, "free") or _contains_word(lower, "unassigned"),
        "ranking": _contains_any(lower, ("busiest", "most busy", "most loaded", "least loaded", "fewest", "bottom ", "top ", "hosts the most", "host the most")),
        "security_audit": _contains_any(lower, ("audit", "insecure", "secure", "risky")),
        "security_lockdown": _contains_any(lower, ("lock", "tighten", "suggest", "recommend")),
        "navigate": _contains_any(lower, ("open ", "show ", "inspect ", "review ", "find ", "go to ", "take me to ")),
        "alive": _contains_any(lower, ("alive", "healthy", "up", "ready")),
    }


def _classify_unhandled_query(query: str, project_context: dict[str, str]) -> str:
    lower = str(query or "").strip().lower()
    if not lower:
        return "unknown"
    signals = _intent_signals(lower)
    if project_context.get("project_id") and _contains_any(lower, ("network", "networking", "router", "port", "floating ip", "security group", "load balancer")) and _contains_any(lower, ("unhealthy", "risk", "problem", "wrong", "look at first", "investigate", "concerning", "issue")):
        return "grounded_diagnosis_network"
    operational_signals = any(
        signals[key]
        for key in (
            "count",
            "usage",
            "limit",
            "status",
            "location",
            "project",
            "attachment",
            "ownership",
            "exposure",
            "external",
            "connectivity",
            "posture",
            "idle",
            "ranking",
            "security_audit",
            "security_lockdown",
            "navigate",
        )
    )
    if (
        _contains_any(lower, _KNOWLEDGE_PHRASES)
        and _contains_any(lower, _OPENSTACK_KNOWLEDGE_TERMS)
        and not operational_signals
    ):
        return "platform_knowledge"
    if _looks_like_environment_summary_query(query):
        return "environment_summary"
    if _contains_any(lower, _OUT_OF_SCOPE_TERMS):
        return "out_of_scope"
    if _contains_any(lower, _DOMAIN_CHAT_TERMS):
        return "domain_chat"
    return "out_of_scope"


def _looks_like_environment_summary_query(query: str) -> bool:
    lower = str(query or "").strip().lower()
    if not lower:
        return False
    signals = _intent_signals(lower)
    environmentish = _contains_any(lower, _ENVIRONMENT_QUERY_TERMS)
    summaryish = any(
        signals[key]
        for key in ("count", "status", "alive", "usage", "idle", "ranking")
    ) or _contains_any(
        lower,
        ("latest", "newest", "oldest", "recent", "behind", "missing", "most", "least", "highest", "lowest", "emptiest"),
    )
    return environmentish and summaryish


def _environment_metric_choices() -> list[dict[str, Any]]:
    return hypervisor_metric_choices()


def _extract_top_n(query: str) -> int | None:
    match = re.search(r"\btop\s+(\d{1,2})\b", str(query or ""), re.IGNORECASE)
    if not match:
        return None
    try:
        value = int(match.group(1))
    except Exception:
        return None
    return max(1, min(value, 10))


def _extract_bottom_n(query: str) -> int | None:
    match = re.search(r"\bbottom\s+(\d{1,2})\b", str(query or ""), re.IGNORECASE)
    if not match:
        return None
    try:
        value = int(match.group(1))
    except Exception:
        return None
    return max(1, min(value, 10))


def _parse_query_fallback(query: str) -> dict[str, Any]:
    text = str(query or "").strip()
    lower = text.lower()
    signals = _intent_signals(lower)
    vm_match = re.search(r"\b(?:vm|instance)\s+([a-zA-Z0-9._:-]+)\b", text, re.IGNORECASE)
    sg_match = re.search(r"\bsecurity group\s+([a-zA-Z0-9._:-]+)\b", text, re.IGNORECASE)
    port_match = re.search(r"\bport\s+([a-zA-Z0-9._:/-]+)\b", text, re.IGNORECASE)
    router_match = re.search(r"\brouter\s+([a-zA-Z0-9._:/-]+)\b", text, re.IGNORECASE)
    volume_match = re.search(r"\bvolume\s+([a-zA-Z0-9._:/-]+)\b", text, re.IGNORECASE)
    fip_match = re.search(r"\b(?:floating ip|floating ips|fip)\s+([a-zA-Z0-9._:/-]+)\b", text, re.IGNORECASE)
    ip_literal_match = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", text)
    operational_signals = any(
        signals[key]
        for key in (
            "count",
            "usage",
            "limit",
            "status",
            "location",
            "project",
            "addresses",
            "attachment",
            "ownership",
            "exposure",
            "external",
            "connectivity",
            "posture",
            "idle",
            "ranking",
            "security_audit",
            "security_lockdown",
            "navigate",
        )
    )
    if signals["knowledge"] and _contains_any(lower, _OPENSTACK_KNOWLEDGE_TERMS) and not operational_signals:
        return {
            "intent": "unknown",
            "resource_kind": "",
            "resource_name": "",
            "needs_clarification": True,
            "clarifying_question": "",
        }
    if _contains_any(lower, ("etcd",)) and (signals["count"] or signals["alive"]):
        return {
            "intent": "etcd_health_summary",
            "resource_kind": "cluster",
            "resource_name": "etcd",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("mariadb", "mysql")) and (signals["count"] or signals["alive"] or signals["status"]):
        return {
            "intent": "mariadb_node_summary",
            "resource_kind": "cluster",
            "resource_name": "mariadb",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("notready", "not ready", "unready")) and _contains_any(lower, ("nodes", "node")):
        return {
            "intent": "not_ready_node_summary",
            "resource_kind": "environment",
            "resource_name": "not_ready_nodes",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("noschedule", "no schedule", "no-schedule")) and _contains_any(lower, ("nodes", "node", "label", "labels", "taint", "taints")):
        return {
            "intent": "noschedule_node_summary",
            "resource_kind": "environment",
            "resource_name": "noschedule_nodes",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("cordoned", "unschedulable")) and _contains_any(lower, ("nodes", "node")):
        return {
            "intent": "cordoned_node_summary",
            "resource_kind": "environment",
            "resource_name": "cordoned_nodes",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("nodes", "node")) and (signals["count"] or signals["alive"]) and _contains_any(lower, ("kubernetes", "cluster", "ready", "alive")):
        return {
            "intent": "node_ready_summary",
            "resource_kind": "cluster",
            "resource_name": "nodes",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("control plane", "control-plane")) and _contains_any(lower, ("healthy", "health", "runtime", "ready", "status")):
        return {
            "intent": "control_plane_runtime_summary",
            "resource_kind": "environment",
            "resource_name": "control_plane_runtime",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("pods", "pod", "pod density", "pod-dense")) and _contains_any(lower, ("density", "densest", "most packed", "most pods", "highest")):
        return {
            "intent": "pod_density_summary",
            "resource_kind": "environment",
            "resource_name": "pod_density",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("cpu requests", "cpu request", "requested cpu", "cpu load")) and _contains_any(lower, ("highest", "most", "load", "pressure", "heaviest")):
        return {
            "intent": "cpu_request_load_summary",
            "resource_kind": "environment",
            "resource_name": "cpu_request_load",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("memory requests", "memory request", "requested memory", "memory load")) and _contains_any(lower, ("highest", "most", "load", "pressure", "heaviest")):
        return {
            "intent": "memory_request_load_summary",
            "resource_kind": "environment",
            "resource_name": "memory_request_load",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("namespaces", "namespace spread", "namespace density")) and _contains_any(lower, ("most", "highest", "widest", "spread")):
        return {
            "intent": "namespace_spread_summary",
            "resource_kind": "environment",
            "resource_name": "namespace_spread",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("pvc", "pvcs", "persistent volume", "persistent volumes", "storage-backed", "pvc-backed")) and _contains_any(lower, ("hotspot", "concentration", "most", "highest", "densest")):
        return {
            "intent": "pvc_hotspot_summary",
            "resource_kind": "environment",
            "resource_name": "pvc_hotspot",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("api server", "apiserver", "api servers")) and (signals["count"] or signals["alive"] or signals["status"]):
        return {
            "intent": "control_plane_component_summary",
            "resource_kind": "cluster",
            "resource_name": "apiserver",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("scheduler", "schedulers")) and (signals["count"] or signals["alive"] or signals["status"]):
        return {
            "intent": "control_plane_component_summary",
            "resource_kind": "cluster",
            "resource_name": "scheduler",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("controller manager", "controller-manager", "controller managers")) and (signals["count"] or signals["alive"] or signals["status"]):
        return {
            "intent": "control_plane_component_summary",
            "resource_kind": "cluster",
            "resource_name": "controller_manager",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("control plane", "control-plane")) and (signals["count"] or signals["alive"] or signals["status"]):
        return {
            "intent": "control_plane_component_summary",
            "resource_kind": "cluster",
            "resource_name": "control_plane",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("cluster", "kubernetes", "k8s")) and (
        signals["status"] or signals["alive"]
    ) and _contains_any(lower, ("health", "healthy", "unhealthy", "status")):
        return {
            "intent": "cluster_health_overview",
            "resource_kind": "cluster",
            "resource_name": "cluster",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("kernel", "kernels")) and _contains_any(lower, ("recent", "newest", "latest", "most recent", "what kernels")):
        return {
            "intent": "kernel_version_summary",
            "resource_kind": "environment",
            "resource_name": "kernel",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("kernel", "kernels")) and _contains_any(lower, ("behind", "outdated", "drift", "updates", "update")):
        return {
            "intent": "kernel_drift_summary",
            "resource_kind": "environment",
            "resource_name": "kernel_drift",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("kubelet", "kubernetes version", "k8s version")) and _contains_any(lower, ("behind", "outdated", "drift", "versions", "version")):
        return {
            "intent": "kubelet_drift_summary",
            "resource_kind": "environment",
            "resource_name": "kubelet_drift",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("container runtime", "runtime", "containerd", "cri-o", "docker")) and _contains_any(lower, ("drift", "different", "mismatch", "versions", "version")):
        return {
            "intent": "container_runtime_drift_summary",
            "resource_kind": "environment",
            "resource_name": "container_runtime_drift",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("node agent", "node-agent", "agent")) and _contains_any(lower, ("missing", "without", "no ", "not reporting", "not ready", "unavailable")):
        return {
            "intent": "missing_node_agent_summary",
            "resource_kind": "environment",
            "resource_name": "node_agent",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("os image", "operating system", "os version", "os", "image")) and _contains_any(lower, ("drift", "different", "mismatch", "versions", "version")):
        return {
            "intent": "os_image_drift_summary",
            "resource_kind": "environment",
            "resource_name": "os_image_drift",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("nodes", "node", "hosts", "host")) and _contains_any(lower, ("attention", "attention first", "highest risk", "most risk", "riskiest", "needs attention", "need attention")):
        return {
            "intent": "node_attention_summary",
            "resource_kind": "environment",
            "resource_name": "node_attention",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("reboot", "reboots", "rebooted", "restarting")) and _contains_any(lower, ("need", "needs", "should", "required", "pending")):
        return {
            "intent": "reboot_needed_summary",
            "resource_kind": "environment",
            "resource_name": "reboot",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("nodes", "compute nodes", "hosts")) and _contains_any(lower, ("no running vms", "no vms", "empty", "without vms", "with no running")):
        return {
            "intent": "empty_compute_nodes_summary",
            "resource_kind": "environment",
            "resource_name": "compute_empty",
            "needs_clarification": False,
        }
    top_n = _extract_top_n(text)
    bottom_n = _extract_bottom_n(text)
    rank_n = top_n or bottom_n
    if rank_n and _contains_any(lower, ("hypervisor", "hypervisors", "compute node", "compute nodes", "host", "hosts", "node", "nodes")):
        if bottom_n and _contains_any(lower, ("vm", "vms", "instance", "instances", "load", "busy", "loaded")):
            return {
                "intent": "hypervisor_metric_top_n",
                "resource_kind": "environment",
                "resource_name": "hypervisor_empty",
                "needs_clarification": False,
                "top_n": rank_n,
            }
        if _contains_any(lower, ("busy", "busiest", "loaded", "most loaded", "vm", "vms", "instance", "instances")):
            return {
                "intent": "hypervisor_metric_top_n",
                "resource_kind": "environment",
                "resource_name": "hypervisor_load",
                "needs_clarification": False,
                "top_n": rank_n,
            }
        if _contains_any(lower, ("ram", "memory")):
            return {
                "intent": "hypervisor_metric_top_n",
                "resource_kind": "environment",
                "resource_name": "hypervisor_ram",
                "needs_clarification": False,
                "top_n": rank_n,
            }
        if _contains_any(lower, ("cpu", "vcpu", "vcpus")):
            return {
                "intent": "hypervisor_metric_top_n",
                "resource_kind": "environment",
                "resource_name": "hypervisor_cpu",
                "needs_clarification": False,
                "top_n": rank_n,
            }
        if _contains_any(lower, ("disk", "storage")):
            return {
                "intent": "hypervisor_metric_top_n",
                "resource_kind": "environment",
                "resource_name": "hypervisor_disk",
                "needs_clarification": False,
                "top_n": rank_n,
            }
    if _contains_any(lower, ("hypervisor", "hypervisors", "compute node", "compute nodes", "hosts", "nodes")) and _contains_any(lower, ("emptiest", "least busy", "least loaded", "fewest vms", "fewest instances", "lightest load")):
        return {
            "intent": "emptiest_hypervisor_summary",
            "resource_kind": "environment",
            "resource_name": "hypervisor_empty",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("compute node", "compute nodes", "host", "hosts", "node", "nodes")) and _contains_any(
        lower,
        ("busiest", "most busy", "most loaded", "highest", "lowest", "top ", "most", "least"),
    ) and not _contains_any(
        lower,
        (
            "vm",
            "vms",
            "instance",
            "instances",
            "ram",
            "memory",
            "cpu",
            "vcpu",
            "vcpus",
            "disk",
            "storage",
        ),
    ):
        return {
            "intent": "unknown",
            "resource_kind": "environment_metric",
            "resource_name": "hypervisor_metric",
            "needs_clarification": True,
            "clarifying_question": "What metric do you want to rank hypervisors by?",
            "choices": _environment_metric_choices(),
        }
    if _contains_any(lower, ("hypervisor", "hypervisors", "compute node", "compute nodes", "hosts")) and _contains_any(lower, ("busiest", "most busy", "most loaded", "hosts the most vms", "host the most vms")):
        return {
            "intent": "busiest_hypervisor_summary",
            "resource_kind": "environment",
            "resource_name": "hypervisor_load",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("hypervisor", "hypervisors", "compute node", "compute nodes", "hosts")) and _contains_any(lower, ("most ram", "most memory", "using the most ram", "using the most memory", "highest ram", "highest memory")):
        return {
            "intent": "highest_ram_hypervisor_summary",
            "resource_kind": "environment",
            "resource_name": "hypervisor_ram",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("hypervisor", "hypervisors", "compute node", "compute nodes", "hosts")) and _contains_any(lower, ("most cpu", "highest cpu", "using the most cpu", "most vcpu", "most vcpus", "highest vcpu", "highest vcpus")):
        return {
            "intent": "highest_cpu_hypervisor_summary",
            "resource_kind": "environment",
            "resource_name": "hypervisor_cpu",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("hypervisor", "hypervisors", "compute node", "compute nodes", "hosts")) and _contains_any(lower, ("most disk", "most storage", "highest disk", "highest storage", "using the most disk", "using the most storage")):
        return {
            "intent": "highest_disk_hypervisor_summary",
            "resource_kind": "environment",
            "resource_name": "hypervisor_disk",
            "needs_clarification": False,
        }
    if sg_match and signals["security_lockdown"]:
        return {
            "intent": "security_group_lockdown_suggestions",
            "resource_kind": "security_group",
            "resource_name": sg_match.group(1),
            "needs_clarification": False,
        }
    if sg_match and signals["security_audit"]:
        return {
            "intent": "security_group_audit" if "audit" in lower else "security_group_insecure_check",
            "resource_kind": "security_group",
            "resource_name": sg_match.group(1),
            "needs_clarification": False,
        }
    if signals["status"] and vm_match:
        return {
            "intent": "instance_status",
            "resource_kind": "instance",
            "resource_name": vm_match.group(1),
            "needs_clarification": False,
        }
    if signals["location"] and vm_match:
        return {
            "intent": "instance_location",
            "resource_kind": "instance",
            "resource_name": vm_match.group(1),
            "needs_clarification": False,
        }
    if signals["project"] and vm_match:
        return {
            "intent": "instance_project",
            "resource_kind": "instance",
            "resource_name": vm_match.group(1),
            "needs_clarification": False,
        }
    if signals["addresses"] and vm_match:
        return {
            "intent": "instance_ips",
            "resource_kind": "instance",
            "resource_name": vm_match.group(1),
            "needs_clarification": False,
        }
    if _contains_any(lower, ("network", "networks")) and vm_match:
        return {
            "intent": "instance_networks",
            "resource_kind": "instance",
            "resource_name": vm_match.group(1),
            "needs_clarification": False,
        }
    if port_match and signals["attachment"]:
        return {
            "intent": "port_attachment_status",
            "resource_kind": "port",
            "resource_name": port_match.group(1),
            "needs_clarification": False,
        }
    if port_match and signals["exposure"]:
        return {
            "intent": "port_public_exposure",
            "resource_kind": "port",
            "resource_name": port_match.group(1),
            "needs_clarification": False,
        }
    if router_match and signals["external"]:
        return {
            "intent": "router_external_status",
            "resource_kind": "router",
            "resource_name": router_match.group(1),
            "needs_clarification": False,
        }
    if router_match and signals["connectivity"]:
        return {
            "intent": "router_connectivity_summary",
            "resource_kind": "router",
            "resource_name": router_match.group(1),
            "needs_clarification": False,
        }
    if volume_match and signals["attachment"]:
        return {
            "intent": "volume_attachment_summary",
            "resource_kind": "volume",
            "resource_name": volume_match.group(1),
            "needs_clarification": False,
        }
    if volume_match and signals["posture"]:
        return {
            "intent": "volume_posture",
            "resource_kind": "volume",
            "resource_name": volume_match.group(1),
            "needs_clarification": False,
        }
    if _contains_any(lower, ("floating ip", "floating ips", "fip")) and ip_literal_match and signals["ownership"] and not signals["count"] and not signals["limit"] and not signals["idle"]:
        return {
            "intent": "floating_ip_lookup_by_fixed_ip",
            "resource_kind": "floatingip",
            "resource_name": ip_literal_match.group(1),
            "needs_clarification": False,
        }
    if fip_match and (signals["ownership"] or signals["attachment"] or vm_match is not None or _contains_any(lower, (" vm", " instance"))):
        return {
            "intent": "floating_ip_instance_status",
            "resource_kind": "floatingip",
            "resource_name": fip_match.group(1),
            "needs_clarification": False,
        }
    if fip_match and signals["idle"]:
        return {
            "intent": "floating_ip_idle_status",
            "resource_kind": "floatingip",
            "resource_name": fip_match.group(1),
            "needs_clarification": False,
        }
    if _contains_any(lower, ("floating ip", "floating ips", "fip")):
        return {
            "intent": "quota_limit" if signals["limit"] and not _contains_any(lower, ("use", "used", "using")) else "quota_usage",
            "resource_kind": "quota",
            "resource_name": "floating_ips",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("core", "cores")):
        return {
            "intent": "quota_limit" if signals["limit"] and not _contains_any(lower, ("use", "used", "using")) else "quota_usage",
            "resource_kind": "quota",
            "resource_name": "cores",
            "needs_clarification": False,
        }
    if _contains_any(lower, ("instance", "instances")) and signals["count"]:
        return {
            "intent": "resource_count",
            "resource_kind": "quota",
            "resource_name": "instances",
            "needs_clarification": False,
        }
    open_match = re.search(
        r"\b(?:open|show|inspect|review|find|go to|take me to)\s+"
        r"(network|router|port|floating(?:\s|-)?ips?|fips?|load(?:\s|-)?balancers?|lbs?|volumes?|projects?|security(?:\s|-)?groups?|vms?|instances?)\s+"
        r"([a-zA-Z0-9._:/-]+(?:[ ][a-zA-Z0-9._:/-]+)*)",
        text,
        re.IGNORECASE,
    )
    if open_match and signals["navigate"]:
        return {
            "intent": "open_resource",
            "resource_kind": _RESOURCE_KIND_ALIASES.get(open_match.group(1).strip().lower(), ""),
            "resource_name": re.sub(r"[?.,!]+$", "", open_match.group(2)).strip(),
            "needs_clarification": False,
        }
    if _contains_any(lower, ("network", "router", "port")) and (signals["count"] or signals["usage"] or signals["limit"]):
        return {
            "intent": "project_network_summary",
            "resource_kind": "project",
            "resource_name": "",
            "needs_clarification": False,
        }
    return {
        "intent": "unknown",
        "resource_kind": "",
        "resource_name": "",
        "needs_clarification": True,
        "clarifying_question": "Try asking about quota usage, instance status, instance location, or project networking counts.",
    }


def _parse_query_with_provider(query: str, context: dict[str, Any]) -> dict[str, Any]:
    fallback = _parse_query_fallback(query)
    if _looks_like_environment_summary_query(query):
        parsed = _parse_environment_query_with_provider(query, fallback)
        if parsed:
            return parsed
    if not fallback.get("needs_clarification") and str(fallback.get("intent") or "").strip().lower() != "unknown":
        fallback["parser_source"] = "fallback"
        return fallback
    if not assistant_provider_enabled():
        fallback["parser_source"] = "fallback"
        return fallback
    system_prompt = (
        "You classify ops questions into JSON only. "
        "Allowed intents: quota_usage, quota_limit, resource_count, instance_status, instance_location, "
        "instance_project, instance_ips, instance_networks, project_network_summary, project_storage_summary, "
        "security_group_audit, security_group_lockdown_suggestions, security_group_insecure_check, "
        "etcd_health_summary, mariadb_node_summary, cluster_health_overview, node_ready_summary, control_plane_component_summary, "
        "kernel_version_summary, kernel_drift_summary, kubelet_drift_summary, container_runtime_drift_summary, missing_node_agent_summary, reboot_needed_summary, node_attention_summary, os_image_drift_summary, control_plane_runtime_summary, pod_density_summary, pvc_hotspot_summary, cpu_request_load_summary, memory_request_load_summary, namespace_spread_summary, not_ready_node_summary, cordoned_node_summary, noschedule_node_summary, empty_compute_nodes_summary, busiest_hypervisor_summary, emptiest_hypervisor_summary, highest_ram_hypervisor_summary, highest_cpu_hypervisor_summary, highest_disk_hypervisor_summary, hypervisor_metric_top_n, "
        "port_attachment_status, port_public_exposure, router_external_status, router_connectivity_summary, "
        "volume_attachment_summary, volume_posture, floating_ip_instance_status, floating_ip_lookup_by_fixed_ip, floating_ip_idle_status, "
        "open_resource, unknown. "
        "Return keys: intent, resource_kind, resource_name, needs_clarification, clarifying_question, top_n. "
        "resource_kind can be instance, quota, project, security_group, network, router, port, floatingip, loadbalancer, volume, cluster, environment, or empty. "
        "resource_name should be a VM name, security group name, resource name, or metric keyword such as floating_ips, cores, networks, routers, ports, instances. "
        "Never include markdown."
    )
    parsed = _provider_json_completion(system_prompt, {
        "query": query,
        "context": {
            "selected_project_id": context.get("selected_project_id", ""),
            "selected_project_name": context.get("selected_project_name", ""),
            "active_view": context.get("active_view", ""),
        },
    })
    if not parsed:
        fallback["parser_source"] = "fallback"
        return fallback
    parsed["parser_source"] = "xerotier"
    return parsed


def _parse_environment_query_with_provider(query: str, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = dict(fallback or _parse_query_fallback(query))
    if not assistant_provider_enabled():
        fallback["parser_source"] = "fallback"
        return fallback
    system_prompt = (
        "Classify environment and fleet questions into JSON only. "
        "Allowed intents: kernel_version_summary, kernel_drift_summary, kubelet_drift_summary, container_runtime_drift_summary, missing_node_agent_summary, reboot_needed_summary, "
        "node_attention_summary, os_image_drift_summary, control_plane_runtime_summary, pod_density_summary, pvc_hotspot_summary, cpu_request_load_summary, memory_request_load_summary, namespace_spread_summary, not_ready_node_summary, cordoned_node_summary, noschedule_node_summary, empty_compute_nodes_summary, busiest_hypervisor_summary, emptiest_hypervisor_summary, highest_ram_hypervisor_summary, highest_cpu_hypervisor_summary, highest_disk_hypervisor_summary, hypervisor_metric_top_n, unknown. "
        "Map freeform wording about environment-wide compute, host, node, hypervisor, kernel, RAM, reboot, VM density, or missing agents "
        "to the closest supported intent. "
        "Return keys: intent, resource_kind, resource_name, needs_clarification, clarifying_question, top_n. "
        "resource_kind must be environment or empty. resource_name should be a short metric keyword such as kernel, kernel_drift, kubelet_drift, container_runtime_drift, node_agent, reboot, node_attention, os_image_drift, control_plane_runtime, pod_density, pvc_hotspot, cpu_request_load, memory_request_load, namespace_spread, not_ready_nodes, cordoned_nodes, noschedule_nodes, compute_empty, hypervisor_load, hypervisor_empty, hypervisor_ram, hypervisor_cpu, or hypervisor_disk. "
        "Never include markdown."
    )
    parsed = _provider_json_completion(system_prompt, {"query": query})
    intent = str(parsed.get("intent") or "").strip().lower()
    if intent not in _ENVIRONMENT_SUMMARY_INTENTS:
        fallback["parser_source"] = "fallback"
        return fallback
    parsed["parser_source"] = "xerotier"
    parsed.setdefault("resource_kind", "environment")
    parsed.setdefault("needs_clarification", False)
    return parsed


def _parse_followup_with_provider(query: str, previous_result: dict[str, Any]) -> dict[str, Any]:
    if not assistant_provider_enabled():
        return {}
    previous_intent = str(previous_result.get("intent") or "").strip()
    previous_mode = str(previous_result.get("mode") or "").strip()
    previous_scope = previous_result.get("scope") or {}
    previous_findings = list(previous_result.get("findings") or [])
    previous_actions = list(previous_result.get("actions") or [])
    system_prompt = (
        "Classify assistant follow-up questions into JSON only. "
        "Allowed kinds: why, why_hot, top_n, open_top, open_next, compare_next, compare_top_n, compare_named_nodes, affected_namespaces, managed_only, compute_with_workloads, unhealthy_only, changed, problem, unknown. "
        "Return keys: kind, target, limit, direction, left, right. "
        "Use why_hot for targeted hotspot questions such as asking why a specific node is hot. "
        "Use compare_top_n for freer comparative wording such as side-by-side or compare the first few. "
        "Use compare_named_nodes when the user names two nodes or hosts to compare directly. "
        "Use managed_only when the user asks to show only managed items from a previous NoSchedule-style result. "
        "Use compute_with_workloads when the user asks to filter down to compute nodes actively hosting workloads. "
        "Use open_next when the user asks to open the second or next result. "
        "Use affected_namespaces when the user asks which namespaces dominate or are affected on the prior result. "
        "Never include markdown."
    )
    parsed = _provider_json_completion(system_prompt, {
        "query": query,
        "previous_result": {
            "intent": previous_intent,
            "mode": previous_mode,
            "scope": previous_scope,
            "findings": previous_findings[:3],
            "actions": previous_actions[:3],
        },
    })
    kind = str(parsed.get("kind") or "").strip().lower()
    if kind not in {
        "why",
        "why_hot",
        "top_n",
        "open_top",
        "open_next",
        "compare_next",
        "compare_top_n",
        "compare_named_nodes",
        "affected_namespaces",
        "managed_only",
        "compute_with_workloads",
        "unhealthy_only",
        "changed",
        "problem",
    }:
        return {}
    normalized = {
        "kind": kind,
        "target": str(parsed.get("target") or "").strip(),
        "direction": str(parsed.get("direction") or "").strip().lower(),
        "left": str(parsed.get("left") or "").strip(),
        "right": str(parsed.get("right") or "").strip(),
    }
    try:
        limit = int(parsed.get("limit") or 0) if str(parsed.get("limit") or "").strip() else 0
    except Exception:
        limit = 0
    if limit:
        normalized["limit"] = max(1, min(limit, 10))
    normalized["parser_source"] = "xerotier"
    return normalized


def _resolve_instance(query: str, auth, k8s_auth=None, project_id: str = "") -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    return resolve_assistant_resource("instance", query, auth, project_id=project_id)


def _resolve_security_group(query: str, auth, k8s_auth=None, project_id: str = "") -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    return resolve_assistant_resource("securitygroup", query, auth, project_id=project_id)


def _resolve_project(query: str, auth, k8s_auth=None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    return resolve_assistant_resource("project", query, auth)


def _extract_project_hint(query: str) -> str:
    match = re.search(r"\bproject\s+([A-Za-z0-9._:-]+(?:[ -][A-Za-z0-9._:-]+)*)", str(query or ""), re.IGNORECASE)
    if not match:
        return ""
    hint = re.sub(r"[?.,!]+$", "", match.group(1)).strip()
    for suffix in (" using", " have", " has", " with", " on", " in", " at", " for", " limit", " quota"):
        if hint.lower().endswith(suffix):
            hint = hint[: -len(suffix)].strip()
    return hint


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


def _project_resolution_response(intent: str, text: str, auth, k8s_auth, project_context: dict[str, str], resolved_kind: str, resolved_id: str, resolved_label: str) -> tuple[dict[str, str], dict[str, Any] | None]:
    if resolved_kind == "project" and resolved_id:
        return {
            "project_id": resolved_id,
            "project_name": resolved_label or resolved_id,
        }, None
    hint = _extract_project_hint(text)
    if hint:
        match, candidates = _resolve_project(hint, auth, k8s_auth)
        if match is None:
            if candidates:
                labels = ", ".join(item.get("label") or item.get("id") or "unknown" for item in candidates[:3])
                return project_context, _clarification_response(
                    intent,
                    f"I found multiple matching projects: {labels}.",
                    "Which project did you mean?",
                    "project",
                    candidates,
                )
            return project_context, {
                "intent": intent,
                "answer": f"I could not find a project named {hint}.",
                "confidence": 0.2,
                "entities": [],
                "actions": [{"type": "open_projects", "label": "Open Projects"}],
                "needs_clarification": True,
            }
        return {
            "project_id": str(match.get("id") or match.get("project_id") or ""),
            "project_name": str(match.get("label") or match.get("project_name") or hint),
        }, None
    return project_context, None


def _resolve_resource(kind: str, query: str, auth, k8s_auth=None, project_id: str = "") -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    normalized = _RESOURCE_KIND_ALIASES.get(str(kind or "").strip().lower(), str(kind or "").strip().lower())
    if not normalized:
        return None, []
    return resolve_assistant_resource(normalized, query, auth, project_id=project_id)


def _resource_action(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    if kind == "network":
        return {"type": "open_network", "id": item.get("id", ""), "label": "Open Network"}
    if kind == "router":
        return {"type": "open_router", "id": item.get("id", ""), "label": "Open Router"}
    if kind == "port":
        return {"type": "open_port", "id": item.get("id", ""), "label": "Open Port"}
    if kind == "loadbalancer":
        return {"type": "open_load_balancer", "id": item.get("id", ""), "label": "Open Load Balancer"}
    if kind == "volume":
        return {"type": "open_volume", "id": item.get("id", ""), "label": "Open Volume"}
    if kind == "project":
        return {"type": "open_project_tab", "project_id": item.get("id") or item.get("project_id") or "", "tab": "overview", "label": "Open Project"}
    if kind == "floatingip":
        return {
            "type": "open_project_network_family",
            "project_id": item.get("project_id", ""),
            "family": "floatingips",
            "label": "Open Floating IPs",
        }
    if kind == "securitygroup":
        return {"type": "open_networking_security_group", "id": item.get("id", ""), "label": "Open Security Group"}
    if kind == "instance":
        return {
            "type": "open_instance",
            "id": item.get("id", ""),
            "compute_host": item.get("compute_host", ""),
            "label": "Open VM",
        }
    return {"type": "open_projects", "label": "Open Projects"}


def _answer_open_resource(kind: str, name: str, auth, k8s_auth=None, project_context: dict[str, str] | None = None, resolved_id: str = "") -> dict[str, Any]:
    normalized = _RESOURCE_KIND_ALIASES.get(str(kind or "").strip().lower(), str(kind or "").strip().lower())
    context = project_context or {}
    project_id = context.get("project_id", "") if normalized != "project" else ""
    match = None
    candidates: list[dict[str, Any]] = []
    if resolved_id:
        _match, resolved_candidates = _resolve_resource(normalized, name or resolved_id, auth, k8s_auth, project_id)
        candidates = resolved_candidates
        if _match is not None and str(_match.get("id") or "") == resolved_id:
            match = _match
        else:
            match = next((item for item in resolved_candidates if str(item.get("id") or "") == resolved_id), None)
        if match is not None:
            candidates = [match]
    if match is None:
        match, candidates = _resolve_resource(normalized, name, auth, k8s_auth, project_id)
    if match is None:
        if candidates:
            labels = ", ".join(item.get("label") or item.get("id") or "unknown" for item in candidates[:3])
            return _clarification_response(
                "open_resource",
                f"I found multiple matching {normalized or 'resources'}: {labels}.",
                f"Which {normalized or 'resource'} did you mean?",
                normalized or "resource",
                candidates,
            )
        return {
            "intent": "open_resource",
            "answer": f"I could not find a {normalized or 'resource'} named {name}.",
            "confidence": 0.2,
            "entities": [],
            "actions": [],
            "needs_clarification": True,
        }
    if normalized == "instance":
        return _answer_instance("instance_status", name, auth, k8s_auth, context, str(match.get("id") or resolved_id or ""))
    if normalized == "securitygroup":
        return _answer_security_group("security_group_audit", name, auth, k8s_auth, context, str(match.get("id") or resolved_id or ""))
    if normalized == "network":
        detail = get_network_detail(str(match.get("id") or ""), auth)
        project_name = detail.get("project_name") or match.get("project_name") or match.get("project_id") or "unknown"
        answer = (
            f"Network {detail.get('name') or detail.get('id')} in project {project_name} is {detail.get('status') or 'UNKNOWN'} "
            f"with {len(detail.get('subnets') or [])} subnets."
        )
    elif normalized == "router":
        detail = get_router_detail(str(match.get("id") or ""), auth)
        project_name = detail.get("project_name") or match.get("project_name") or match.get("project_id") or "unknown"
        answer = (
            f"Router {detail.get('name') or detail.get('id')} in project {project_name} is {detail.get('status') or 'UNKNOWN'} "
            f"with {len(detail.get('interfaces') or [])} interfaces."
        )
    elif normalized == "port":
        detail = get_port_detail(str(match.get("id") or ""), auth)
        attached = detail.get("attached_name") or detail.get("attached_id") or detail.get("device_owner") or "unattached"
        answer = (
            f"Port {detail.get('name') or detail.get('id')} is {detail.get('status') or 'UNKNOWN'} on "
            f"{detail.get('network_name') or detail.get('network_id') or 'unknown network'} and is attached to {attached}."
        )
    elif normalized == "floatingip":
        project_fips = get_floating_ips(auth, project_id=str(match.get("project_id") or "")) if match.get("project_id") else get_floating_ips(auth)
        detail = next((item for item in project_fips if item.get("id") == match.get("id")), None) or match
        answer = (
            f"Floating IP {detail.get('floating_ip_address') or detail.get('id')} is {detail.get('status') or 'UNKNOWN'} "
            f"and is attached to {detail.get('instance_name') or detail.get('fixed_ip_address') or 'no instance'}."
        )
    elif normalized == "loadbalancer":
        detail = get_load_balancer_detail(str(match.get("id") or ""), auth)
        answer = (
            f"Load balancer {detail.get('name') or detail.get('id')} has provisioning status "
            f"{detail.get('provisioning_status') or 'UNKNOWN'} and operating status {detail.get('operating_status') or 'UNKNOWN'}."
        )
    elif normalized == "volume":
        detail = get_volume_detail(str(match.get("id") or ""), auth)
        answer = (
            f"Volume {detail.get('name') or detail.get('id')} is {detail.get('status') or 'UNKNOWN'} at "
            f"{detail.get('size_gb') or 0} GB with {len(detail.get('attachments') or [])} attachments."
        )
    elif normalized == "project":
        project_id_value = str(match.get("id") or match.get("project_id") or "")
        projects = get_projects(auth, search=str(match.get("label") or name))
        detail = next((item for item in projects if item.get("project_id") == project_id_value), None) or {
            "project_id": project_id_value,
            "project_name": match.get("label") or project_id_value,
            "description": "",
        }
        answer = f"Project {detail.get('project_name') or detail.get('project_id')} is available for review."
        match = {
            "id": detail.get("project_id", ""),
            "label": detail.get("project_name") or detail.get("project_id") or name,
            "project_id": detail.get("project_id", ""),
        }
    else:
        return {
            "intent": "open_resource",
            "answer": f"I can’t open resource type {kind} yet.",
            "confidence": 0.2,
            "entities": [],
            "actions": [],
            "needs_clarification": True,
        }
    return {
        "intent": "open_resource",
        "answer": answer,
        "confidence": 0.9,
        "entities": [{"kind": normalized, "id": match.get("id", ""), "label": match.get("label") or name}],
        "actions": [_resource_action(normalized, match)],
        "needs_clarification": False,
        "scope": {"project_id": match.get("project_id", ""), "project_name": match.get("project_name", "")},
    }


def _answer_quota_usage(metric: str, auth, project_context: dict[str, str]) -> dict[str, Any]:
    resource = _QUOTA_RESOURCE_MAP.get(metric)
    if resource is None:
        raise ValueError(f"Unsupported quota metric: {metric}")
    section, key, label = resource
    project_id = project_context.get("project_id", "")
    if not project_id:
        return {
            "intent": "quota_usage",
            "answer": "Select a project first so I know which quota scope to use.",
            "confidence": 0.35,
            "entities": [],
            "actions": [{"type": "open_projects", "label": "Open Projects"}],
            "needs_clarification": True,
        }
    quotas = get_project_quota_summary(project_id, auth)
    quotas = _backfill_compute_quota_usage(project_id, auth, quotas)
    entry = (quotas.get(section) or {}).get(key) or {}
    used = entry.get("used")
    limit = entry.get("limit")
    project_name = project_context.get("project_name") or project_id
    return {
        "intent": "quota_usage",
        "answer": f"In project {project_name}, you are using {used if used is not None else 'unknown'} {label} out of {limit if limit is not None else 'unknown'}.",
        "confidence": 0.92 if used is not None else 0.7,
        "entities": [{"kind": "project", "id": project_id, "label": project_name}],
        "actions": [{"type": "open_project_tab", "project_id": project_id, "tab": "quota", "label": "Open Quota"}],
        "needs_clarification": False,
        "scope": {"project_id": project_id, "project_name": project_name},
    }


def _answer_quota_limit(metric: str, auth, project_context: dict[str, str]) -> dict[str, Any]:
    resource = _QUOTA_RESOURCE_MAP.get(metric)
    if resource is None:
        raise ValueError(f"Unsupported quota metric: {metric}")
    section, key, label = resource
    project_id = project_context.get("project_id", "")
    if not project_id:
        return {
            "intent": "quota_limit",
            "answer": "Select a project first so I know which quota scope to use.",
            "confidence": 0.35,
            "entities": [],
            "actions": [{"type": "open_projects", "label": "Open Projects"}],
            "needs_clarification": True,
        }
    quotas = get_project_quota_summary(project_id, auth)
    entry = (quotas.get(section) or {}).get(key) or {}
    limit = entry.get("limit")
    project_name = project_context.get("project_name") or project_id
    return {
        "intent": "quota_limit",
        "answer": f"In project {project_name}, the quota limit for {label} is {limit if limit is not None else 'unknown'}.",
        "confidence": 0.9 if limit is not None else 0.65,
        "entities": [{"kind": "project", "id": project_id, "label": project_name}],
        "actions": [{"type": "open_project_tab", "project_id": project_id, "tab": "quota", "label": "Open Quota"}],
        "needs_clarification": False,
        "scope": {"project_id": project_id, "project_name": project_name},
    }


def _answer_resource_count(metric: str, auth, project_context: dict[str, str]) -> dict[str, Any]:
    if metric in _QUOTA_RESOURCE_MAP:
        return _answer_quota_usage(metric, auth, project_context)
    if metric != "instances":
        raise ValueError(f"Unsupported resource count metric: {metric}")
    project_id = project_context.get("project_id", "")
    if not project_id:
        return {
            "intent": "resource_count",
            "answer": "Select a project first so I can count its instances.",
            "confidence": 0.35,
            "entities": [],
            "actions": [{"type": "open_projects", "label": "Open Projects"}],
            "needs_clarification": True,
        }
    instances = get_project_instances(auth, project_id=project_id)
    project_name = project_context.get("project_name") or project_id
    return {
        "intent": "resource_count",
        "answer": f"Project {project_name} currently has {len(instances)} instances.",
        "confidence": 0.95,
        "entities": [{"kind": "project", "id": project_id, "label": project_name}],
        "actions": [{"type": "open_project_tab", "project_id": project_id, "tab": "instances", "label": "Open Instances"}],
        "needs_clarification": False,
        "scope": {"project_id": project_id, "project_name": project_name},
    }


def _answer_instance(
    intent: str,
    name: str,
    auth,
    k8s_auth=None,
    project_context: dict[str, str] | None = None,
    resolved_instance_id: str = "",
) -> dict[str, Any]:
    context = project_context or {}
    project_id = context.get("project_id", "")
    match = None
    candidates: list[dict[str, Any]] = []
    if resolved_instance_id:
        _match, resolved_candidates = _resolve_instance(name or resolved_instance_id, auth, k8s_auth, project_id)
        candidates = resolved_candidates
        if _match is not None and str(_match.get("id") or "") == resolved_instance_id:
            match = _match
        else:
            match = next((item for item in resolved_candidates if str(item.get("id") or "") == resolved_instance_id), None)
        if match is not None:
            candidates = [match]
    if match is None:
        match, candidates = _resolve_instance(name, auth, k8s_auth, project_id)
    if match is None:
        if candidates:
            labels = ", ".join(item.get("label") or item.get("id") or "unknown" for item in candidates[:3])
            return _clarification_response(
                intent,
                f"I found multiple matching instances: {labels}.",
                "Which instance did you mean?",
                "instance",
                candidates,
            )
        return {
            "intent": intent,
            "answer": f"I could not find an instance named {name}.",
            "confidence": 0.2,
            "entities": [],
            "actions": [],
            "needs_clarification": True,
        }
    instances = get_project_instances(auth, project_id=match.get("project_id", "")) if match.get("project_id") else get_project_instances(auth)
    detail = next((item for item in instances if item.get("id") == match.get("id")), None) or {}
    vm_name = detail.get("name") or match.get("label") or name
    status = detail.get("status") or "UNKNOWN"
    host = detail.get("compute_host") or match.get("compute_host") or "unknown"
    if intent == "instance_location":
        answer = f"{vm_name} is on host {host}."
    elif intent == "instance_project":
        answer = f"{vm_name} belongs to project {detail.get('project_name') or match.get('project_id') or 'unknown'}."
    elif intent == "instance_ips":
        fixed_ips = list(detail.get("fixed_ips") or [])
        floating_ips = list(detail.get("floating_ips") or [])
        answer = f"{vm_name} has fixed IPs {', '.join(fixed_ips) or 'none'} and floating IPs {', '.join(floating_ips) or 'none'}."
    elif intent == "instance_networks":
        networks = list(detail.get("networks") or [])
        names = [item.get("name") or "" for item in networks if item.get("name")]
        answer = f"{vm_name} is attached to networks {', '.join(names) or 'none'}."
    else:
        answer = f"{vm_name} is {status} on host {host}."
    return {
        "intent": intent,
        "answer": answer,
        "confidence": 0.95,
        "entities": [{"kind": "instance", "id": match.get("id", ""), "label": vm_name}],
        "actions": [
            {
                "type": "open_instance",
                "id": match.get("id", ""),
                "label": "Open VM",
                "compute_host": host,
            },
            {
                "type": "open_project_tab",
                "project_id": match.get("project_id", ""),
                "tab": "instances",
                "label": "Open Project",
            },
        ],
        "needs_clarification": False,
        "scope": {"project_id": match.get("project_id", "")},
    }


def _answer_project_network_summary(auth, project_context: dict[str, str]) -> dict[str, Any]:
    project_id = project_context.get("project_id", "")
    if not project_id:
        return {
            "intent": "project_network_summary",
            "answer": "Select a project first so I can summarize its networking resources.",
            "confidence": 0.35,
            "entities": [],
            "actions": [{"type": "open_projects", "label": "Open Projects"}],
            "needs_clarification": True,
        }
    quotas = get_project_quota_summary(project_id, auth)
    project_name = project_context.get("project_name") or project_id
    network = quotas.get("network") or {}
    def used(key: str) -> str:
        entry = network.get(key) or {}
        value = entry.get("used")
        return str(value) if value is not None else "unknown"
    return {
        "intent": "project_network_summary",
        "answer": (
            f"Project {project_name} is using {used('network')} networks, {used('router')} routers, "
            f"{used('port')} ports, {used('floatingip')} floating IPs, and {used('load_balancer')} load balancers."
        ),
        "confidence": 0.88,
        "entities": [{"kind": "project", "id": project_id, "label": project_name}],
        "actions": [{"type": "open_project_tab", "project_id": project_id, "tab": "networking", "label": "Open Networking"}],
        "needs_clarification": False,
        "scope": {"project_id": project_id, "project_name": project_name},
    }


def _answer_project_storage_summary(auth, project_context: dict[str, str]) -> dict[str, Any]:
    project_id = project_context.get("project_id", "")
    if not project_id:
        return {
            "intent": "project_storage_summary",
            "answer": "Select a project first so I can summarize its storage usage.",
            "confidence": 0.35,
            "entities": [],
            "actions": [{"type": "open_projects", "label": "Open Projects"}],
            "needs_clarification": True,
        }
    quotas = get_project_quota_summary(project_id, auth)
    storage = quotas.get("block_storage") or {}
    project_name = project_context.get("project_name") or project_id
    def used(key: str) -> str:
        entry = storage.get(key) or {}
        value = entry.get("used")
        return str(value) if value is not None else "unknown"
    return {
        "intent": "project_storage_summary",
        "answer": f"Project {project_name} is using {used('volumes')} volumes, {used('gigabytes')} gigabytes, and {used('snapshots')} snapshots.",
        "confidence": 0.86,
        "entities": [{"kind": "project", "id": project_id, "label": project_name}],
        "actions": [{"type": "open_project_tab", "project_id": project_id, "tab": "storage", "label": "Open Storage"}],
        "needs_clarification": False,
        "scope": {"project_id": project_id, "project_name": project_name},
    }


def _security_group_recommendations(detail: dict[str, Any]) -> list[str]:
    recommendations: list[str] = []
    for rule in detail.get("rules", []) or []:
        audit = rule.get("audit") or {}
        category = str(audit.get("category") or "")
        if category == "open-world-any-any":
            recommendations.append("Replace any:any ingress from 0.0.0.0/0 or ::/0 with protocol-specific rules limited to trusted source ranges.")
        elif category == "public-admin-port":
            recommendations.append("Restrict administrative ports exposed to the internet to a small known admin CIDR or VPN range.")
        elif category == "open-world-ingress":
            recommendations.append("Narrow public ingress rules to the smallest required CIDRs and ports.")
    if detail.get("attachment_port_count", 0) == 0:
        recommendations.append("Remove or quarantine this security group if it is unused.")
    if not recommendations:
        recommendations.append("No high-risk patterns were detected; review whether all remaining rules are still needed and minimize broad egress where possible.")
    deduped: list[str] = []
    for item in recommendations:
        if item not in deduped:
            deduped.append(item)
    return deduped[:3]


def _answer_security_group(
    intent: str,
    name: str,
    auth,
    k8s_auth=None,
    project_context: dict[str, str] | None = None,
    resolved_security_group_id: str = "",
) -> dict[str, Any]:
    context = project_context or {}
    match = None
    candidates: list[dict[str, Any]] = []
    if resolved_security_group_id:
        _match, resolved_candidates = _resolve_security_group(name or resolved_security_group_id, auth, k8s_auth, context.get("project_id", ""))
        candidates = resolved_candidates
        if _match is not None and str(_match.get("id") or "") == resolved_security_group_id:
            match = _match
        else:
            match = next((item for item in resolved_candidates if str(item.get("id") or "") == resolved_security_group_id), None)
        if match is not None:
            candidates = [match]
    if match is None:
        match, candidates = _resolve_security_group(name, auth, k8s_auth, context.get("project_id", ""))
    if match is None:
        if candidates:
            labels = ", ".join(item.get("label") or item.get("id") or "unknown" for item in candidates[:3])
            return _clarification_response(
                intent,
                f"I found multiple matching security groups: {labels}.",
                "Which security group did you mean?",
                "securitygroup",
                candidates,
            )
        return {
            "intent": intent,
            "answer": f"I could not find a security group named {name}.",
            "confidence": 0.2,
            "entities": [],
            "actions": [],
            "needs_clarification": True,
        }
    detail = get_security_group_detail(match.get("id", ""), auth)
    severity = ((detail.get("audit") or {}).get("severity") or "unknown").lower()
    findings = (detail.get("audit") or {}).get("findings") or []
    project_name = detail.get("project_name") or detail.get("project_id") or "unknown"
    base_actions = [
        {"type": "open_networking_security_group", "id": detail.get("id", ""), "label": "Open Security Group"},
        {"type": "open_project_tab", "project_id": detail.get("project_id", ""), "tab": "security", "label": "Open Project Security"},
    ]
    if intent == "security_group_insecure_check":
        adjective = "insecure" if severity in {"critical", "high"} else "not obviously insecure"
        answer = f"Security group {detail.get('name') or detail.get('id')} is {adjective}; current severity is {severity}."
    elif intent == "security_group_lockdown_suggestions":
        recs = _security_group_recommendations(detail)
        answer = (
            f"Security group {detail.get('name') or detail.get('id')} in project {project_name} has {detail.get('rule_count', 0)} rules and severity {severity}. "
            f"Recommended next steps: {' '.join(f'{idx + 1}. {item}' for idx, item in enumerate(recs))}"
        )
    else:
        top = findings[0]["summary"] if findings else "No flagged findings"
        answer = (
            f"Security group {detail.get('name') or detail.get('id')} in project {project_name} is rated {severity}. "
            f"It has {detail.get('rule_count', 0)} rules, {detail.get('attachment_port_count', 0)} attached ports, and top finding: {top}."
        )
    return {
        "intent": intent,
        "answer": answer,
        "confidence": 0.9,
        "entities": [{"kind": "securitygroup", "id": detail.get("id", ""), "label": detail.get("name") or detail.get("id") or name}],
        "actions": base_actions,
        "needs_clarification": False,
        "scope": {"project_id": detail.get("project_id", ""), "project_name": project_name},
        "findings": findings[:3],
        "recommendations": _security_group_recommendations(detail) if intent != "security_group_insecure_check" else [],
    }


def answer_assistant_query(
    query: str,
    auth,
    k8s_auth=None,
    selected_project_id: str = "",
    selected_project_name: str = "",
    active_view: str = "",
    resolved_kind: str = "",
    resolved_id: str = "",
    resolved_label: str = "",
    previous_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    text = str(query or "").strip()
    if not text:
        return {
            "intent": "unknown",
            "answer": "Ask a question about quotas, instances, or project networking.",
            "confidence": 0.0,
            "entities": [],
            "actions": [],
            "needs_clarification": True,
            "timings": {"parse_ms": 0.0, "answer_ms": 0.0, "total_ms": 0.0},
        }
    followup_parse_ms = 0.0
    parsed_followup: dict[str, Any] | None = None
    if isinstance(previous_result, dict) and previous_result and assistant_provider_enabled():
        followup_parse_started = time.perf_counter()
        parsed_followup = _parse_followup_with_provider(text, previous_result) or None
        followup_parse_ms = round((time.perf_counter() - followup_parse_started) * 1000, 1) if parsed_followup else 0.0
    followup_answer_started = time.perf_counter()
    followup = answer_followup_query(text, previous_result, auth, k8s_auth, parsed_followup=parsed_followup)
    if followup is not None:
        followup["parser_source"] = str((parsed_followup or {}).get("parser_source") or "followup")
        followup["provider_enabled"] = assistant_provider_enabled()
        followup["timings"] = {
            "parse_ms": followup_parse_ms,
            "answer_ms": round((time.perf_counter() - followup_answer_started) * 1000, 1),
            "total_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        return followup
    project_context = {
        "project_id": str(selected_project_id or "").strip(),
        "project_name": str(selected_project_name or "").strip(),
    }
    fallback_preview = _parse_query_fallback(text)
    preview_intent = str(fallback_preview.get("intent") or "").strip().lower()
    environment_like_query = _looks_like_environment_summary_query(text)
    task = _classify_unhandled_query(text, project_context)
    if task != "platform_knowledge" and not environment_like_query and preview_intent not in {
        "etcd_health_summary",
        "mariadb_node_summary",
        "cluster_health_overview",
        "node_ready_summary",
        "control_plane_component_summary",
        "control_plane_runtime_summary",
        "pod_density_summary",
        "pvc_hotspot_summary",
        "cpu_request_load_summary",
        "memory_request_load_summary",
        "namespace_spread_summary",
        "not_ready_node_summary",
        "cordoned_node_summary",
        "noschedule_node_summary",
        "kernel_version_summary",
        "kernel_drift_summary",
        "kubelet_drift_summary",
        "container_runtime_drift_summary",
        "missing_node_agent_summary",
        "reboot_needed_summary",
        "node_attention_summary",
        "os_image_drift_summary",
        "empty_compute_nodes_summary",
        "busiest_hypervisor_summary",
        "emptiest_hypervisor_summary",
        "highest_ram_hypervisor_summary",
        "highest_cpu_hypervisor_summary",
        "highest_disk_hypervisor_summary",
        "hypervisor_metric_top_n",
    }:
        
        project_context = _default_project_context(auth, selected_project_id)
        if selected_project_name and not project_context.get("project_name"):
            project_context["project_name"] = selected_project_name
        task = _classify_unhandled_query(text, project_context)
    parse_started = time.perf_counter()
    if task == "grounded_diagnosis_network":
        parsed = {
            "intent": "network_diagnosis",
            "resource_kind": "project",
            "resource_name": "",
            "needs_clarification": False,
            "parser_source": "task-router",
        }
    elif task == "platform_knowledge":
        parsed = {
            "intent": "platform_knowledge",
            "resource_kind": "",
            "resource_name": "",
            "needs_clarification": False,
            "parser_source": "task-router",
        }
    else:
        parsed = _parse_query_with_provider(text, {
            "selected_project_id": project_context.get("project_id", ""),
            "selected_project_name": project_context.get("project_name", ""),
            "active_view": active_view,
        })
    parse_ms = round((time.perf_counter() - parse_started) * 1000, 1)
    answer_started = time.perf_counter()
    intent = str(parsed.get("intent") or "unknown").strip().lower()
    resource_name = str(parsed.get("resource_name") or "").strip()
    try:
        top_n = int(parsed.get("top_n") or 0) if str(parsed.get("top_n") or "").strip() else 0
    except Exception:
        top_n = 0
    locked_kind = str(resolved_kind or "").strip().lower()
    locked_id = str(resolved_id or "").strip()
    locked_label = str(resolved_label or "").strip()
    if locked_kind == "environment_metric" and locked_id in _ENVIRONMENT_SUMMARY_INTENTS:
        parsed = {
            "intent": locked_id,
            "resource_kind": "environment",
            "resource_name": locked_label or locked_id,
            "needs_clarification": False,
            "parser_source": "choice",
        }
        intent = locked_id
        resource_name = str(parsed.get("resource_name") or "").strip()
    if task == "grounded_diagnosis_network" and intent in {"unknown", "project_network_summary"}:
        intent = "network_diagnosis"
    elif task == "platform_knowledge" and intent in {"unknown", "quota_usage", "quota_limit", "project_network_summary"}:
        intent = "platform_knowledge"
    elif task == "domain_chat" and intent == "unknown":
        intent = "domain_chat"
    elif task == "out_of_scope" and intent == "unknown":
        intent = "out_of_scope"
    if intent in _PROJECT_SCOPED_INTENTS:
        project_context, project_resolution = _project_resolution_response(
            intent,
            text,
            auth,
            k8s_auth,
            project_context,
            locked_kind,
            locked_id,
            locked_label,
        )
        if project_resolution is not None:
            project_resolution["parser_source"] = parsed.get("parser_source", "fallback")
            project_resolution["answer_source"] = "local"
            project_resolution["provider_enabled"] = assistant_provider_enabled()
            answer_ms = round((time.perf_counter() - answer_started) * 1000, 1)
            project_resolution["timings"] = {
                "parse_ms": parse_ms,
                "answer_ms": answer_ms,
                "total_ms": round((time.perf_counter() - started) * 1000, 1),
            }
            return project_resolution
    if locked_kind and locked_label:
        resource_name = locked_label
    if intent in {"network_diagnosis", "platform_knowledge", "domain_chat", "out_of_scope"} or (parsed.get("needs_clarification") and intent == "unknown"):
        if intent == "network_diagnosis":
            if not project_context.get("project_id"):
                response = {
                    "intent": "network_diagnosis",
                    "answer": "Select a project first so I can gather networking evidence for it.",
                    "confidence": 0.25,
                    "entities": [],
                    "actions": [{"type": "open_projects", "label": "Open Projects"}],
                    "needs_clarification": True,
                    "mode": "grounded_diagnosis",
                    "provenance": "Grounded project evidence",
                    "answer_source": "local",
                }
            else:
                evidence = build_project_network_diagnosis_evidence(
                    project_context.get("project_id", ""),
                    project_context.get("project_name", ""),
                    auth,
                )
                used_provider = False
                if assistant_provider_enabled():
                    try:
                        synthesized = synthesize_network_diagnosis(
                            text,
                            evidence,
                            _assistant_provider_payload(timeout=_ASSISTANT_TIMEOUT_SECONDS),
                        )
                        used_provider = bool(synthesized)
                    except Exception:
                        synthesized = {}
                else:
                    synthesized = {}
                if not synthesized:
                    synthesized = fallback_network_diagnosis_answer(evidence)
                response = {
                    "intent": "network_diagnosis",
                    "answer": synthesized.get("answer") or "I gathered project networking evidence, but could not synthesize a diagnosis.",
                    "confidence": _safe_confidence(synthesized.get("confidence"), 0.6),
                    "entities": [{"kind": "project", "id": project_context.get("project_id", ""), "label": project_context.get("project_name") or project_context.get("project_id") or "project"}],
                    "actions": [{"type": "open_project_tab", "project_id": project_context.get("project_id", ""), "tab": "networking", "label": "Open Networking"}],
                    "needs_clarification": False,
                    "mode": "grounded_diagnosis",
                    "provenance": "Synthesized from project networking evidence" if used_provider else "Grounded project evidence",
                    "answer_source": "xerotier" if used_provider else "local",
                    "evidence": evidence,
                    "findings": list(synthesized.get("findings") or []),
                    "recommendations": list(synthesized.get("recommendations") or []),
                    "scope": project_context,
                }
        elif intent == "platform_knowledge":
            if assistant_provider_enabled():
                try:
                    synthesized = answer_platform_knowledge(text, _assistant_provider_payload(timeout=_ASSISTANT_TIMEOUT_SECONDS))
                except Exception:
                    synthesized = {}
            else:
                synthesized = {}
            response = {
                "intent": "platform_knowledge",
                "answer": synthesized.get("answer") or "I need the provider path enabled to answer that as general OpenStack knowledge.",
                "confidence": _safe_confidence(synthesized.get("confidence"), 0.45),
                "entities": [],
                "actions": [],
                "needs_clarification": False,
                "mode": "platform_knowledge",
                "provenance": "General OpenStack knowledge",
                "answer_source": "xerotier" if synthesized else "local",
            }
        elif intent == "domain_chat":
            if assistant_provider_enabled():
                try:
                    synthesized = answer_domain_chat(text, _assistant_provider_payload(timeout=_ASSISTANT_TIMEOUT_SECONDS))
                except Exception:
                    synthesized = {}
            else:
                synthesized = {}
            response = {
                "intent": "domain_chat",
                "answer": synthesized.get("answer") or "I don’t have a provider answer for that right now.",
                "confidence": _safe_confidence(synthesized.get("confidence"), 0.35),
                "entities": [],
                "actions": [],
                "needs_clarification": False,
                "mode": "domain_chat",
                "provenance": "Infrastructure domain answer",
                "answer_source": "xerotier" if synthesized else "local",
            }
        elif intent == "out_of_scope":
            response = {
                "intent": "out_of_scope",
                "answer": "I’m scoped to infrastructure and platform topics such as OpenStack, Kubernetes, Rackspace, cloud operations, Linux, Windows administration, GPUs, networking, storage, and related systems work.",
                "confidence": 0.95,
                "entities": [],
                "actions": [],
                "needs_clarification": False,
                "mode": "out_of_scope",
                "provenance": "Scope policy",
                "answer_source": "local",
            }
        else:
            response = {
                "intent": intent,
                "answer": parsed.get("clarifying_question") or "Try asking about floating IPs, cores, or a VM status.",
                "confidence": 0.15,
                "entities": [],
                "actions": [],
                "needs_clarification": True,
                "answer_source": "local",
                "choices": list(parsed.get("choices") or []),
                "clarifying_question": parsed.get("clarifying_question") or "",
            }
        response["parser_source"] = parsed.get("parser_source", "fallback")
        response["timings"] = {
            "parse_ms": parse_ms,
            "answer_ms": round((time.perf_counter() - answer_started) * 1000, 1),
            "total_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        response["provider_enabled"] = assistant_provider_enabled()
        return response
    if intent == "quota_usage":
        response = _answer_quota_usage(resource_name, auth, project_context)
    elif intent == "quota_limit":
        response = _answer_quota_limit(resource_name, auth, project_context)
    elif intent == "resource_count":
        response = _answer_resource_count(resource_name, auth, project_context)
    elif intent in _INSTANCE_INTENTS:
        response = _answer_instance(
            intent,
            resource_name,
            auth,
            k8s_auth,
            project_context,
            locked_id if locked_kind == "instance" else "",
        )
    elif intent == "project_network_summary":
        response = _answer_project_network_summary(auth, project_context)
    elif intent == "project_storage_summary":
        response = _answer_project_storage_summary(auth, project_context)
    elif intent in {"security_group_audit", "security_group_lockdown_suggestions", "security_group_insecure_check"}:
        response = _answer_security_group(
            intent,
            resource_name,
            auth,
            k8s_auth,
            project_context,
            locked_id if locked_kind == "securitygroup" else "",
        )
    elif intent == "etcd_health_summary":
        response = answer_etcd_health_summary(k8s_auth)
    elif intent == "mariadb_node_summary":
        response = answer_mariadb_node_summary(k8s_auth)
    elif intent == "cluster_health_overview":
        response = answer_cluster_health_overview(k8s_auth)
    elif intent == "node_ready_summary":
        response = answer_node_ready_summary(k8s_auth)
    elif intent == "control_plane_component_summary":
        response = answer_control_plane_component_summary(resource_name or "control_plane", k8s_auth)
    elif intent in _ENVIRONMENT_SUMMARY_INTENTS:
        response = answer_environment_summary(intent, auth, k8s_auth) or {
            "intent": intent,
            "answer": "I could not answer that environment summary right now.",
            "confidence": 0.2,
            "entities": [],
            "actions": [],
            "needs_clarification": False,
            "answer_source": "local",
        }
    elif intent == "hypervisor_metric_top_n":
        response = answer_hypervisor_metric_top_n(resource_name, top_n or 3, auth, k8s_auth) or {
            "intent": intent,
            "answer": "I could not answer that ranking question right now.",
            "confidence": 0.2,
            "entities": [],
            "actions": [],
            "needs_clarification": False,
            "answer_source": "local",
        }
    elif intent == "port_attachment_status":
        response = answer_port_attachment_status(
            resource_name,
            auth,
            project_context,
            locked_id if locked_kind == "port" else "",
        )
    elif intent == "port_public_exposure":
        response = answer_port_public_exposure(
            resource_name,
            auth,
            project_context,
            locked_id if locked_kind == "port" else "",
        )
    elif intent == "router_external_status":
        response = answer_router_external_status(
            resource_name,
            auth,
            project_context,
            locked_id if locked_kind == "router" else "",
        )
    elif intent == "router_connectivity_summary":
        response = answer_router_connectivity_summary(
            resource_name,
            auth,
            project_context,
            locked_id if locked_kind == "router" else "",
        )
    elif intent == "volume_attachment_summary":
        response = answer_volume_attachment_summary(
            resource_name,
            auth,
            project_context,
            locked_id if locked_kind == "volume" else "",
        )
    elif intent == "volume_posture":
        response = answer_volume_posture(
            resource_name,
            auth,
            project_context,
            locked_id if locked_kind == "volume" else "",
        )
    elif intent == "floating_ip_instance_status":
        response = answer_floating_ip_instance_status(
            resource_name,
            auth,
            project_context,
            locked_id if locked_kind == "floatingip" else "",
        )
    elif intent == "floating_ip_lookup_by_fixed_ip":
        response = answer_floating_ip_lookup_by_fixed_ip(
            resource_name,
            auth,
            project_context,
        )
    elif intent == "floating_ip_idle_status":
        response = answer_floating_ip_idle_status(
            resource_name,
            auth,
            project_context,
            locked_id if locked_kind == "floatingip" else "",
        )
    elif intent == "open_resource":
        response = _answer_open_resource(
            str(parsed.get("resource_kind") or locked_kind or "").strip(),
            resource_name,
            auth,
            k8s_auth,
            project_context,
            locked_id,
        )
    else:
        response = {
            "intent": "unknown",
            "answer": "I can currently answer quota, instance, project summary, security group audit, and first-class resource navigation questions.",
            "confidence": 0.2,
            "entities": [],
            "actions": [],
            "needs_clarification": True,
        }
    response["parser_source"] = parsed.get("parser_source", "fallback")
    response["answer_source"] = response.get("answer_source") or "local"
    response["provider_enabled"] = assistant_provider_enabled()
    response["timings"] = {
        "parse_ms": parse_ms,
        "answer_ms": round((time.perf_counter() - answer_started) * 1000, 1),
        "total_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    return response


def stream_assistant_query(
    query: str,
    auth,
    k8s_auth=None,
    selected_project_id: str = "",
    selected_project_name: str = "",
    active_view: str = "",
    resolved_kind: str = "",
    resolved_id: str = "",
    resolved_label: str = "",
    previous_result: dict[str, Any] | None = None,
):
    started = time.perf_counter()
    text = str(query or "").strip()
    if not text:
        yield {
            "type": "final",
            "result": {
                "intent": "unknown",
                "answer": "Ask a question about quotas, instances, or project networking.",
                "confidence": 0.0,
                "entities": [],
                "actions": [],
                "needs_clarification": True,
                "parser_source": "fallback",
                "answer_source": "local",
                "timings": {"parse_ms": 0.0, "answer_ms": 0.0, "total_ms": 0.0},
            },
        }
        return
    project_context = {
        "project_id": str(selected_project_id or "").strip(),
        "project_name": str(selected_project_name or "").strip(),
    }
    task = _classify_unhandled_query(text, project_context)
    if task == "platform_knowledge":
        parse_ms = 0.0
        meta = {
            "intent": "platform_knowledge",
            "parser_source": "task-router",
            "answer_source": "xerotier" if assistant_provider_enabled() else "local",
            "mode": "platform_knowledge",
            "provenance": "General OpenStack knowledge",
        }
        yield {"type": "meta", "meta": meta}
        if assistant_provider_enabled():
            chunks: list[str] = []
            answer_started = time.perf_counter()
            try:
                for chunk in stream_platform_knowledge(text, _assistant_provider_payload(timeout=_ASSISTANT_TIMEOUT_SECONDS)):
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    yield {"type": "delta", "text": chunk}
                answer = "".join(chunks).strip()
            except Exception:
                fallback = answer_platform_knowledge(text, _assistant_provider_payload(timeout=_ASSISTANT_TIMEOUT_SECONDS))
                answer = str(fallback.get("answer") or "").strip()
            answer_ms = round((time.perf_counter() - answer_started) * 1000, 1)
            yield {
                "type": "final",
                "result": {
                    "intent": "platform_knowledge",
                    "answer": answer or "I need the provider path enabled to answer that as general OpenStack knowledge.",
                    "confidence": 0.45,
                    "entities": [],
                    "actions": [],
                    "needs_clarification": False,
                    "parser_source": "task-router",
                    "answer_source": "xerotier",
                    "mode": "platform_knowledge",
                    "provenance": "General OpenStack knowledge",
                    "provider_enabled": True,
                    "timings": {
                        "parse_ms": parse_ms,
                        "answer_ms": answer_ms,
                        "total_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                },
            }
            return
        yield {
            "type": "final",
            "result": {
                "intent": "platform_knowledge",
                "answer": "I need the provider path enabled to answer that as general OpenStack knowledge.",
                "confidence": 0.45,
                "entities": [],
                "actions": [],
                "needs_clarification": False,
                "parser_source": "task-router",
                "answer_source": "local",
                "mode": "platform_knowledge",
                "provenance": "General OpenStack knowledge",
                "provider_enabled": False,
                "timings": {
                    "parse_ms": parse_ms,
                    "answer_ms": round((time.perf_counter() - started) * 1000, 1),
                    "total_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            },
        }
        return
    if task == "domain_chat" and assistant_provider_enabled():
        parse_started = time.perf_counter()
        parsed = _parse_query_with_provider(text, {
            "selected_project_id": "",
            "selected_project_name": "",
            "active_view": active_view,
        })
        parse_ms = round((time.perf_counter() - parse_started) * 1000, 1)
        if str(parsed.get("intent") or "unknown").strip().lower() == "unknown":
            meta = {
                "intent": "domain_chat",
                "parser_source": parsed.get("parser_source", "fallback"),
                "answer_source": "xerotier",
                "mode": "domain_chat",
                "provenance": "Infrastructure domain answer",
            }
            yield {"type": "meta", "meta": meta}
            chunks: list[str] = []
            answer_started = time.perf_counter()
            try:
                for chunk in stream_domain_chat(text, _assistant_provider_payload(timeout=_ASSISTANT_TIMEOUT_SECONDS)):
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    yield {"type": "delta", "text": chunk}
                answer = "".join(chunks).strip()
            except Exception:
                fallback = answer_domain_chat(text, _assistant_provider_payload(timeout=_ASSISTANT_TIMEOUT_SECONDS))
                answer = str(fallback.get("answer") or "").strip()
            answer_ms = round((time.perf_counter() - answer_started) * 1000, 1)
            yield {
                "type": "final",
                "result": {
                    "intent": "domain_chat",
                    "answer": answer or "I don’t have a provider answer for that right now.",
                    "confidence": 0.35,
                    "entities": [],
                    "actions": [],
                    "needs_clarification": False,
                    "parser_source": parsed.get("parser_source", "fallback"),
                    "answer_source": "xerotier",
                    "mode": "domain_chat",
                    "provenance": "Infrastructure domain answer",
                    "provider_enabled": True,
                    "timings": {
                        "parse_ms": parse_ms,
                        "answer_ms": answer_ms,
                        "total_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                },
            }
            return
    if task == "out_of_scope":
        yield {
            "type": "final",
            "result": {
                "intent": "out_of_scope",
                "answer": "I’m scoped to infrastructure and platform topics such as OpenStack, Kubernetes, Rackspace, cloud operations, Linux, Windows administration, GPUs, networking, storage, and related systems work.",
                "confidence": 0.95,
                "entities": [],
                "actions": [],
                "needs_clarification": False,
                "parser_source": "task-router",
                "answer_source": "local",
                "mode": "out_of_scope",
                "provenance": "Scope policy",
                "provider_enabled": assistant_provider_enabled(),
                "timings": {
                    "parse_ms": 0.0,
                    "answer_ms": round((time.perf_counter() - started) * 1000, 1),
                    "total_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            },
        }
        return
    yield {"type": "final", "result": answer_assistant_query(text, auth, k8s_auth, selected_project_id, selected_project_name, active_view, resolved_kind, resolved_id, resolved_label, previous_result)}
