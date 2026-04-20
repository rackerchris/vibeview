"""Grounded Kubernetes/control-plane assistant answers."""
from __future__ import annotations

from typing import Any

from ..operations import k8s_ops


def _node_actions(node_names: list[str] | tuple[str, ...], limit: int = 3) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_name in list(node_names or []):
        name = str(raw_name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        actions.append({"type": "open_node", "node": name, "label": f"Open {name}"})
        if len(actions) >= max(1, int(limit or 1)):
            break
    return actions


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
        "actions": _node_actions(etcd_names),
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
    priority_nodes = [
        str(item.get("name") or "")
        for item in nodes
        if item.get("ready") is not True or item.get("cordoned") is True
    ] or [str(item.get("name") or "") for item in nodes]
    answer = f"{ready} of {total} Kubernetes nodes are Ready."
    if cordoned:
        answer += f" {cordoned} are currently cordoned."
    return {
        "intent": "node_ready_summary",
        "answer": answer,
        "confidence": 0.94,
        "entities": [{"kind": "cluster", "id": "nodes", "label": "nodes"}],
        "actions": _node_actions(priority_nodes),
        "needs_clarification": False,
        "scope": {"node_total": total, "node_ready": ready, "node_cordoned": cordoned},
        "answer_source": "local",
        "mode": "grounded_cluster_health",
        "provenance": "Grounded Kubernetes data",
    }


def answer_mariadb_node_summary(k8s_auth=None) -> dict[str, Any]:
    names = sorted(k8s_ops.get_mariadb_node_names(auth=k8s_auth))
    total = len(names)
    if not total:
        answer = "I could not find any Kubernetes nodes currently hosting MariaDB cluster pods."
        confidence = 0.55
    else:
        answer = f"There are {total} Kubernetes nodes currently hosting MariaDB cluster pods."
        confidence = 0.9
    return {
        "intent": "mariadb_node_summary",
        "answer": answer,
        "confidence": confidence,
        "entities": [{"kind": "cluster", "id": "mariadb", "label": "MariaDB"}],
        "actions": _node_actions(names),
        "needs_clarification": False,
        "scope": {"mariadb_node_total": total},
        "answer_source": "local",
        "mode": "grounded_cluster_health",
        "provenance": "Grounded Kubernetes data",
    }


def answer_cluster_health_overview(k8s_auth=None) -> dict[str, Any]:
    summary = k8s_ops.get_k8s_node_health_density_summary(auth=k8s_auth)
    nodes = list(summary.get("nodes") or [])
    total = len(nodes)
    ready = sum(1 for item in nodes if item.get("ready") is True)
    not_ready = total - ready
    cordoned = int((summary.get("condition_counts") or {}).get("Cordoned", 0) or 0)
    pressure_counts = {
        key: int(value or 0)
        for key, value in (summary.get("condition_counts") or {}).items()
        if key not in {"NotReady", "Cordoned"}
    }
    pressure_findings = [f"{count} nodes reporting {name}" for name, count in pressure_counts.items() if count > 0]
    findings: list[str] = []
    if not_ready:
        findings.append(f"{not_ready} nodes are NotReady.")
    if cordoned:
        findings.append(f"{cordoned} nodes are cordoned.")
    findings.extend(pressure_findings[:3])
    if not findings:
        findings.append("No obvious node-readiness or pressure issues were detected.")
    answer = f"{ready} of {total} Kubernetes nodes are Ready."
    if not_ready or cordoned or pressure_findings:
        answer += " The main health signals to review are: " + "; ".join(findings[:3])
    return {
        "intent": "cluster_health_overview",
        "answer": answer,
        "confidence": 0.9 if total else 0.55,
        "entities": [{"kind": "cluster", "id": "cluster", "label": "cluster"}],
        "actions": _node_actions([
            str(item.get("name") or "")
            for item in nodes
            if item.get("ready") is not True
        ] + [
            str(item.get("name") or "")
            for item in nodes
            if item.get("cordoned") is True
        ] + [
            str(item.get("name") or "")
            for item in nodes
        ]),
        "needs_clarification": False,
        "findings": findings[:3],
        "scope": {"node_total": total, "node_ready": ready, "node_not_ready": not_ready, "node_cordoned": cordoned},
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
