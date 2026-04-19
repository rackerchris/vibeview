"""Grounded Kubernetes/control-plane assistant answers."""
from __future__ import annotations

from typing import Any

from ..operations import k8s_ops


def _ready_fraction(pod: dict[str, Any]) -> tuple[int, int]:
    text = str(pod.get("ready") or "").strip()
    if "/" not in text:
      return (0, 0)
    left, right = text.split("/", 1)
    try:
        return int(left), int(right)
    except Exception:
        return (0, 0)


def _is_pod_healthy(pod: dict[str, Any]) -> bool:
    ready, total = _ready_fraction(pod)
    phase = str(pod.get("phase") or "").strip().lower()
    return phase == "running" and total > 0 and ready >= total


def answer_etcd_health_summary(k8s_auth=None) -> dict[str, Any]:
    nodes = k8s_ops.get_nodes(auth=k8s_auth)
    node_map = {str(item.get("name") or ""): item for item in nodes}
    etcd_names = sorted(k8s_ops.get_etcd_node_names(auth=k8s_auth))
    if not etcd_names:
        return {
            "intent": "etcd_health_summary",
            "answer": "I could not find any etcd-role nodes in the current Kubernetes inventory.",
            "confidence": 0.5,
            "entities": [],
            "actions": [],
            "needs_clarification": False,
            "answer_source": "local",
            "mode": "grounded_cluster_health",
            "provenance": "Grounded Kubernetes and node-agent data",
        }
    healthy = 0
    unknown = 0
    errors: list[str] = []
    for name in etcd_names:
        hostname = str((node_map.get(name) or {}).get("hostname") or "")
        status = k8s_ops.get_etcd_service_status(name, hostname or None)
        if status.get("active") is True:
            healthy += 1
        elif status.get("active") is None:
            unknown += 1
            error = str(status.get("error") or "").strip()
            if error:
                errors.append(error)
    total = len(etcd_names)
    if unknown:
        answer = f"There are {total} etcd nodes. I could validate {healthy} as alive, and {unknown} could not be validated."
    else:
        answer = f"There are {total} etcd nodes, and {healthy} are currently alive."
    findings = []
    if errors:
        findings.append(errors[0])
    return {
        "intent": "etcd_health_summary",
        "answer": answer,
        "confidence": 0.92 if not unknown else 0.72,
        "entities": [{"kind": "cluster", "id": "etcd", "label": "etcd"}],
        "actions": [],
        "needs_clarification": False,
        "findings": findings,
        "scope": {"etcd_total": total, "etcd_alive": healthy, "etcd_unknown": unknown},
        "answer_source": "local",
        "mode": "grounded_cluster_health",
        "provenance": "Grounded Kubernetes and node-agent data",
    }


def answer_node_ready_summary(k8s_auth=None) -> dict[str, Any]:
    nodes = k8s_ops.get_nodes(auth=k8s_auth)
    total = len(nodes)
    ready = sum(1 for item in nodes if item.get("ready") is True)
    cordoned = sum(1 for item in nodes if item.get("cordoned") is True)
    answer = f"{ready} of {total} Kubernetes nodes are Ready."
    if cordoned:
        answer += f" {cordoned} are currently cordoned."
    return {
        "intent": "node_ready_summary",
        "answer": answer,
        "confidence": 0.94,
        "entities": [{"kind": "cluster", "id": "nodes", "label": "nodes"}],
        "actions": [],
        "needs_clarification": False,
        "scope": {"node_total": total, "node_ready": ready, "node_cordoned": cordoned},
        "answer_source": "local",
        "mode": "grounded_cluster_health",
        "provenance": "Grounded Kubernetes data",
    }


def answer_control_plane_component_summary(component: str, k8s_auth=None) -> dict[str, Any]:
    pods = k8s_ops.list_k8s_pods("kube-system", auth=k8s_auth)
    prefixes = {
        "apiserver": ("kube-apiserver",),
        "scheduler": ("kube-scheduler",),
        "controller_manager": ("kube-controller-manager",),
        "control_plane": ("kube-apiserver", "kube-scheduler", "kube-controller-manager", "etcd"),
    }
    wanted = prefixes.get(component, prefixes["control_plane"])
    matching = [pod for pod in pods if any(str(pod.get("name") or "").startswith(prefix) for prefix in wanted)]
    healthy = sum(1 for pod in matching if _is_pod_healthy(pod))
    total = len(matching)
    label_map = {
        "apiserver": "API server",
        "scheduler": "scheduler",
        "controller_manager": "controller manager",
        "control_plane": "control-plane component",
    }
    label = label_map.get(component, "control-plane component")
    if component == "control_plane":
        answer = f"{healthy} of {total} tracked control-plane pods are healthy in kube-system."
    else:
        answer = f"{healthy} of {total} {label} pods are healthy in kube-system."
    findings = []
    unhealthy = [pod for pod in matching if not _is_pod_healthy(pod)]
    if unhealthy:
        pod = unhealthy[0]
        findings.append(f"{pod.get('name')}: phase {pod.get('phase')}, ready {pod.get('ready')}")
    return {
        "intent": "control_plane_component_summary",
        "answer": answer,
        "confidence": 0.9,
        "entities": [{"kind": "cluster", "id": component, "label": label}],
        "actions": [],
        "needs_clarification": False,
        "findings": findings,
        "scope": {"component": component, "pod_total": total, "pod_healthy": healthy},
        "answer_source": "local",
        "mode": "grounded_cluster_health",
        "provenance": "Grounded Kubernetes data",
    }
