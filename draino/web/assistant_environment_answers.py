"""Grounded environment and fleet summary assistant answers."""
from __future__ import annotations

import re
from typing import Any, Callable

from .. import node_agent_client
from ..operations import k8s_ops, openstack_ops


def _host_aliases(value: str | None) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    aliases: list[str] = []
    short = text.split(".", 1)[0]
    for candidate in (text, text.lower(), short, short.lower()):
        if candidate and candidate not in aliases:
            aliases.append(candidate)
    return aliases


def _lookup_host_summary(summaries: dict[str, dict], *candidates: str | None) -> dict[str, Any]:
    for candidate in candidates:
        for alias in _host_aliases(candidate):
            summary = summaries.get(alias)
            if summary:
                return summary
    return {}


def _version_key(value: str | None) -> list[Any]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"([0-9]+)", text.lower())
    key: list[Any] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part)
    return key


def _join_nodes(nodes: list[str], limit: int = 8) -> str:
    if not nodes:
        return "none"
    if len(nodes) <= limit:
        return ", ".join(nodes)
    remainder = len(nodes) - limit
    return f"{', '.join(nodes[:limit])}, and {remainder} more"


def _collect_kernel_facts(k8s_auth=None) -> tuple[list[dict[str, Any]], list[str]]:
    nodes = k8s_ops.get_nodes(auth=k8s_auth)
    facts: list[dict[str, Any]] = []
    errors: list[str] = []
    for node in nodes:
        name = str(node.get("name") or "")
        hostname = str(node.get("hostname") or name)
        signals = k8s_ops.get_node_host_signals(name, hostname)
        if signals.get("error"):
            errors.append(str(signals.get("error")))
        facts.append({
            "name": name,
            "hostname": hostname,
            "kernel_version": str(signals.get("kernel_version") or node.get("kernel_version") or "").strip(),
            "latest_kernel_version": str(signals.get("latest_kernel_version") or "").strip(),
            "reboot_required": bool(signals.get("reboot_required", False)),
        })
    return facts, errors


def _collect_k8s_node_version_facts(k8s_auth=None) -> tuple[list[dict[str, Any]], list[str]]:
    nodes = k8s_ops.get_nodes(auth=k8s_auth)
    facts: list[dict[str, Any]] = []
    for node in nodes:
        facts.append({
            "name": str(node.get("name") or ""),
            "hostname": str(node.get("hostname") or node.get("name") or ""),
            "kubelet_version": str(node.get("kubelet_version") or "").strip(),
            "os_image": str(node.get("os_image") or "").strip(),
            "container_runtime": str(node.get("container_runtime") or "").strip(),
        })
    return facts, []


def _collect_node_agent_facts(k8s_auth=None) -> tuple[list[dict[str, Any]], str]:
    nodes = k8s_ops.get_nodes(auth=k8s_auth)
    error = ""
    try:
        ready_node_agents = set(node_agent_client.get_ready_node_names())
    except Exception as exc:
        ready_node_agents = set()
        error = str(exc)
    facts = [{
        "name": str(node.get("name") or ""),
        "node_agent_ready": str(node.get("name") or "") in ready_node_agents,
    } for node in nodes]
    return facts, error


def _collect_compute_facts(openstack_auth=None, k8s_auth=None, include_signals: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    nodes = k8s_ops.get_nodes(auth=k8s_auth)
    errors: list[str] = []
    try:
        summaries = openstack_ops.get_all_host_summaries(auth=openstack_auth)
    except Exception as exc:
        summaries = {}
        errors.append(str(exc))
    facts: list[dict[str, Any]] = []
    for node in nodes:
        if not node.get("is_compute"):
            continue
        name = str(node.get("name") or "")
        hostname = str(node.get("hostname") or name)
        summary = _lookup_host_summary(summaries, hostname, name)
        signals: dict[str, Any] = {}
        if include_signals:
            signals = k8s_ops.get_node_host_signals(name, hostname)
            if signals.get("error"):
                errors.append(str(signals.get("error")))
        facts.append({
            "name": name,
            "hostname": hostname,
            "vm_count": int(summary.get("vm_count", 0) or 0),
            "compute_status": str(summary.get("compute_status") or "").strip(),
            "kernel_version": str(signals.get("kernel_version") or node.get("kernel_version") or "").strip(),
            "latest_kernel_version": str(signals.get("latest_kernel_version") or "").strip(),
            "reboot_required": bool(signals.get("reboot_required", False)),
        })
    return facts, errors


def _collect_node_attention_facts(openstack_auth=None, k8s_auth=None) -> tuple[list[dict[str, Any]], list[str]]:
    nodes = k8s_ops.get_nodes(auth=k8s_auth)
    errors: list[str] = []
    try:
        summaries = openstack_ops.get_all_host_summaries(auth=openstack_auth)
    except Exception as exc:
        summaries = {}
        errors.append(str(exc))
    try:
        ready_node_agents = set(node_agent_client.get_ready_node_names())
    except Exception as exc:
        ready_node_agents = set()
        errors.append(str(exc))
    facts: list[dict[str, Any]] = []
    for node in nodes:
        name = str(node.get("name") or "")
        hostname = str(node.get("hostname") or name)
        signals = k8s_ops.get_node_host_signals(name, hostname)
        if signals.get("error"):
            errors.append(str(signals.get("error")))
        summary = _lookup_host_summary(summaries, hostname, name)
        kernel_version = str(signals.get("kernel_version") or node.get("kernel_version") or "").strip()
        latest_kernel_version = str(signals.get("latest_kernel_version") or "").strip()
        reboot_required = bool(signals.get("reboot_required", False))
        node_agent_ready = name in ready_node_agents
        kernel_behind = bool(latest_kernel_version and kernel_version and latest_kernel_version != kernel_version)
        risk_flags: list[str] = []
        if kernel_behind:
            risk_flags.append("kernel behind latest")
        if reboot_required:
            risk_flags.append("reboot required")
        if not node_agent_ready:
            risk_flags.append("node agent missing")
        facts.append({
            "name": name,
            "hostname": hostname,
            "is_compute": bool(node.get("is_compute")),
            "vm_count": int(summary.get("vm_count", 0) or 0),
            "kernel_version": kernel_version,
            "latest_kernel_version": latest_kernel_version,
            "reboot_required": reboot_required,
            "node_agent_ready": node_agent_ready,
            "kernel_behind": kernel_behind,
            "risk_flags": risk_flags,
            "risk_count": len(risk_flags),
        })
    return facts, errors


def _collect_hypervisor_detail_facts(openstack_auth=None, k8s_auth=None) -> tuple[list[dict[str, Any]], list[str]]:
    nodes = k8s_ops.get_nodes(auth=k8s_auth)
    errors: list[str] = []
    facts: list[dict[str, Any]] = []
    for node in nodes:
        if not node.get("is_compute"):
            continue
        name = str(node.get("name") or "")
        hostname = str(node.get("hostname") or name)
        try:
            detail = openstack_ops.get_hypervisor_detail(hostname, auth=openstack_auth)
        except Exception as exc:
            detail = {}
            errors.append(str(exc))
        facts.append({
            "name": name,
            "hostname": hostname,
            "running_vms": int(detail.get("running_vms", 0) or 0),
            "memory_mb_used": int(detail.get("memory_mb_used", 0) or 0),
            "memory_mb": int(detail.get("memory_mb", 0) or 0),
            "vcpus_used": int(detail.get("vcpus_used", 0) or 0),
            "vcpus": int(detail.get("vcpus", 0) or 0),
            "local_disk_gb_used": int(detail.get("local_disk_gb_used", 0) or 0),
            "local_disk_gb": int(detail.get("local_disk_gb", 0) or 0),
        })
    return facts, errors


def _collect_hypervisor_vm_facts(openstack_auth=None, k8s_auth=None) -> tuple[list[dict[str, Any]], list[str]]:
    facts, errors = _collect_compute_facts(openstack_auth=openstack_auth, k8s_auth=k8s_auth, include_signals=False)
    return [
        {
            "name": item["name"],
            "hostname": item["hostname"],
            "running_vms": int(item.get("vm_count", 0) or 0),
        }
        for item in facts
    ], errors


def _format_metric_value(metric: str, item: dict[str, Any]) -> str:
    if metric == "running_vms":
        return f"{int(item.get(metric) or 0)}"
    if metric == "memory_mb_used":
        return f"{float(int(item.get(metric) or 0)) / 1024.0:.1f} GiB"
    if metric == "vcpus_used":
        return f"{int(item.get(metric) or 0)}"
    if metric == "local_disk_gb_used":
        return f"{int(item.get(metric) or 0)} GB"
    return str(item.get(metric) or 0)


def _format_metric_capacity(metric: str, item: dict[str, Any]) -> str:
    if metric == "memory_mb_used" and int(item.get("memory_mb") or 0) > 0:
        return f"{float(int(item.get('memory_mb') or 0)) / 1024.0:.1f} GiB total capacity"
    if metric == "vcpus_used" and int(item.get("vcpus") or 0) > 0:
        return f"{int(item.get('vcpus') or 0)} total vCPUs"
    if metric == "local_disk_gb_used" and int(item.get("local_disk_gb") or 0) > 0:
        return f"{int(item.get('local_disk_gb') or 0)} GB total local disk"
    return ""


HYPERVISOR_METRIC_SUMMARIES: dict[str, dict[str, Any]] = {
    "busiest_hypervisor_summary": {
        "metric": "running_vms",
        "operator": "max",
        "answer_label": "VM load",
        "answer_verb": "highest",
        "answer_suffix": "running VMs",
        "entity_label": "hypervisor load",
        "findings_label": "Top hypervisors by running VMs",
        "collector": "summary",
        "choice_label": "VM load",
        "choice_subtext": "Which hypervisor is running the most VMs",
    },
    "emptiest_hypervisor_summary": {
        "metric": "running_vms",
        "operator": "min",
        "answer_label": "VM load",
        "answer_verb": "lowest",
        "answer_suffix": "running VMs",
        "entity_label": "hypervisor load",
        "findings_label": "Least-loaded hypervisors by running VMs",
        "collector": "summary",
        "choice_label": "Fewest VMs",
        "choice_subtext": "Which hypervisor is running the fewest VMs",
    },
    "highest_ram_hypervisor_summary": {
        "metric": "memory_mb_used",
        "operator": "max",
        "answer_label": "RAM usage",
        "answer_verb": "highest",
        "answer_suffix": "RAM in use",
        "entity_label": "hypervisor RAM usage",
        "findings_label": "Top hypervisors by RAM in use",
        "include_suffix_in_value": False,
        "choice_label": "RAM usage",
        "choice_subtext": "Which hypervisor is using the most memory",
    },
    "highest_cpu_hypervisor_summary": {
        "metric": "vcpus_used",
        "operator": "max",
        "answer_label": "CPU usage",
        "answer_verb": "highest",
        "answer_suffix": "vCPUs in use",
        "entity_label": "hypervisor CPU usage",
        "findings_label": "Top hypervisors by vCPU use",
        "include_suffix_in_value": True,
        "choice_label": "CPU usage",
        "choice_subtext": "Which hypervisor is using the most vCPUs",
    },
    "highest_disk_hypervisor_summary": {
        "metric": "local_disk_gb_used",
        "operator": "max",
        "answer_label": "local disk usage",
        "answer_verb": "highest",
        "answer_suffix": "local disk in use",
        "entity_label": "hypervisor disk usage",
        "findings_label": "Top hypervisors by local disk use",
        "include_suffix_in_value": True,
        "choice_label": "Disk usage",
        "choice_subtext": "Which hypervisor is using the most local disk",
    },
}

HYPERVISOR_METRIC_KEY_TO_INTENT = {
    "hypervisor_load": "busiest_hypervisor_summary",
    "hypervisor_empty": "emptiest_hypervisor_summary",
    "hypervisor_ram": "highest_ram_hypervisor_summary",
    "hypervisor_cpu": "highest_cpu_hypervisor_summary",
    "hypervisor_disk": "highest_disk_hypervisor_summary",
}


def answer_hypervisor_metric_ranking(
    metric: str,
    *,
    openstack_auth=None,
    k8s_auth=None,
    intent: str,
    operator: str = "max",
    answer_label: str,
    answer_verb: str = "highest",
    answer_suffix: str,
    entity_label: str,
    findings_label: str,
    collector: str = "detail",
    include_suffix_in_value: bool = False,
) -> dict[str, Any]:
    if collector == "summary":
        facts, errors = _collect_hypervisor_vm_facts(openstack_auth=openstack_auth, k8s_auth=k8s_auth)
    else:
        facts, errors = _collect_hypervisor_detail_facts(openstack_auth=openstack_auth, k8s_auth=k8s_auth)
    reverse = operator != "min"
    ranked = sorted(
        facts,
        key=lambda item: (int(item.get(metric) or 0), str(item.get("name") or "")),
        reverse=reverse,
    )
    if not ranked:
        return {
            "intent": intent,
            "answer": "I could not identify any compute hypervisors in the current environment.",
            "confidence": 0.45,
            "entities": [{"kind": "environment", "id": metric, "label": entity_label}],
            "actions": [],
            "needs_clarification": False,
            "answer_source": "local",
            "mode": "grounded_environment_summary",
            "provenance": "Grounded Kubernetes and OpenStack data",
        }
    busiest = ranked[0]
    metric_value = _format_metric_value(metric, busiest)
    display_value = f"{metric_value} {answer_suffix}" if include_suffix_in_value else metric_value
    answer_lines = [
        f"The hypervisor with the {answer_verb} {answer_label} is `{busiest.get('name')}` with `{display_value}`."
    ]
    capacity_text = _format_metric_capacity(metric, busiest)
    if capacity_text:
        answer_lines.append(f"That host has `{capacity_text}`.")
    top_three = ranked[:3]
    findings = [
        f"{findings_label}\n" + "\n".join(
            f"- {item.get('name')} ({_format_metric_value(metric, item)})" for item in top_three
        )
    ]
    if errors:
        findings.append(errors[0])
    return {
        "intent": intent,
        "answer": "\n".join(answer_lines),
        "confidence": 0.88 if not errors else 0.68,
        "entities": [{"kind": "environment", "id": metric, "label": entity_label}],
        "actions": [],
        "needs_clarification": False,
        "findings": findings,
        "scope": {
            "compute_total": len(facts),
            "top_hypervisor": busiest.get("name"),
            "metric": metric,
            "metric_value": int(busiest.get(metric) or 0),
        },
        "answer_source": "local",
        "mode": "grounded_environment_summary",
        "provenance": "Grounded Kubernetes and OpenStack data",
    }


def answer_kernel_version_summary(k8s_auth=None) -> dict[str, Any]:
    facts, errors = _collect_kernel_facts(k8s_auth=k8s_auth)
    observed_versions = sorted({fact["latest_kernel_version"] or fact["kernel_version"] for fact in facts if fact["latest_kernel_version"] or fact["kernel_version"]}, key=_version_key)
    if not observed_versions:
        return {
            "intent": "kernel_version_summary",
            "answer": "I could not determine any kernel versions from the current node inventory.",
            "confidence": 0.45,
            "entities": [{"kind": "environment", "id": "kernel", "label": "kernel"}],
            "actions": [],
            "needs_clarification": False,
            "answer_source": "local",
            "mode": "grounded_environment_summary",
            "provenance": "Grounded Kubernetes and node-agent data",
        }
    latest = observed_versions[-1]
    running_latest = sorted(fact["name"] for fact in facts if fact["kernel_version"] == latest)
    installed_latest_not_running = sorted(
        fact["name"] for fact in facts
        if fact["latest_kernel_version"] == latest and fact["kernel_version"] != latest
    )
    behind = sorted(
        fact["name"] for fact in facts
        if fact["kernel_version"] and fact["kernel_version"] != latest
    )
    answer_lines = [
        f"The most recent kernel observed in this environment is `{latest}`.",
        "",
        f"- Running on: {_join_nodes(running_latest)}",
        f"- Installed but not running: {_join_nodes(installed_latest_not_running)}",
        f"- Nodes behind the newest kernel: {len(behind)}",
    ]
    findings = []
    if behind:
        findings.append(f"Nodes behind latest kernel: {_join_nodes(behind)}")
    if errors:
        findings.append(errors[0])
    return {
        "intent": "kernel_version_summary",
        "answer": "\n".join(answer_lines),
        "confidence": 0.9 if not errors else 0.72,
        "entities": [{"kind": "environment", "id": "kernel", "label": "kernel"}],
        "actions": [],
        "needs_clarification": False,
        "findings": findings,
        "scope": {
            "latest_kernel": latest,
            "node_total": len(facts),
            "running_latest": len(running_latest),
            "behind_count": len(behind),
        },
        "answer_source": "local",
        "mode": "grounded_environment_summary",
        "provenance": "Grounded Kubernetes and node-agent data",
    }


def answer_missing_node_agent_summary(k8s_auth=None) -> dict[str, Any]:
    facts, error = _collect_node_agent_facts(k8s_auth=k8s_auth)
    missing = sorted(fact["name"] for fact in facts if not fact["node_agent_ready"])
    answer_lines = [f"Nodes missing a ready node agent: {len(missing)}"]
    if missing:
        answer_lines.extend(["", *[f"- {name}" for name in missing[:12]]])
        if len(missing) > 12:
            answer_lines.append(f"- and {len(missing) - 12} more")
    findings = [error] if error else []
    return {
        "intent": "missing_node_agent_summary",
        "answer": "\n".join(answer_lines),
        "confidence": 0.92 if not error else 0.65,
        "entities": [{"kind": "environment", "id": "node_agent", "label": "node agent"}],
        "actions": [],
        "needs_clarification": False,
        "findings": findings,
        "scope": {"missing_count": len(missing), "node_total": len(facts)},
        "answer_source": "local",
        "mode": "grounded_environment_summary",
        "provenance": "Grounded Kubernetes and node-agent readiness data",
    }


def answer_reboot_needed_summary(openstack_auth=None, k8s_auth=None) -> dict[str, Any]:
    facts, errors = _collect_compute_facts(openstack_auth=openstack_auth, k8s_auth=k8s_auth, include_signals=True)
    reboot_nodes = sorted(fact["name"] for fact in facts if fact["reboot_required"])
    answer_lines = [f"Compute nodes that appear to need reboot: {len(reboot_nodes)}"]
    if reboot_nodes:
        answer_lines.extend(["", *[f"- {name}" for name in reboot_nodes[:12]]])
        if len(reboot_nodes) > 12:
            answer_lines.append(f"- and {len(reboot_nodes) - 12} more")
    findings = []
    if reboot_nodes:
        findings.append(f"Reboot-required nodes: {_join_nodes(reboot_nodes)}")
    if errors:
        findings.append(errors[0])
    return {
        "intent": "reboot_needed_summary",
        "answer": "\n".join(answer_lines),
        "confidence": 0.9 if not errors else 0.7,
        "entities": [{"kind": "environment", "id": "reboot", "label": "reboot"}],
        "actions": [],
        "needs_clarification": False,
        "findings": findings,
        "scope": {"compute_total": len(facts), "reboot_required_count": len(reboot_nodes)},
        "answer_source": "local",
        "mode": "grounded_environment_summary",
        "provenance": "Grounded Kubernetes, OpenStack, and node-agent data",
    }


def answer_empty_compute_nodes_summary(openstack_auth=None, k8s_auth=None) -> dict[str, Any]:
    facts, _errors = _collect_compute_facts(openstack_auth=openstack_auth, k8s_auth=k8s_auth, include_signals=False)
    empty_nodes = sorted(fact["name"] for fact in facts if int(fact["vm_count"] or 0) == 0)
    answer_lines = [f"Compute nodes with no running VMs: {len(empty_nodes)}"]
    if empty_nodes:
        answer_lines.extend(["", *[f"- {name}" for name in empty_nodes[:12]]])
        if len(empty_nodes) > 12:
            answer_lines.append(f"- and {len(empty_nodes) - 12} more")
    findings = [f"Empty compute nodes: {_join_nodes(empty_nodes)}"] if empty_nodes else []
    return {
        "intent": "empty_compute_nodes_summary",
        "answer": "\n".join(answer_lines),
        "confidence": 0.9,
        "entities": [{"kind": "environment", "id": "compute_empty", "label": "empty compute nodes"}],
        "actions": [],
        "needs_clarification": False,
        "findings": findings,
        "scope": {"compute_total": len(facts), "empty_count": len(empty_nodes)},
        "answer_source": "local",
        "mode": "grounded_environment_summary",
        "provenance": "Grounded Kubernetes and OpenStack data",
    }


def answer_kernel_drift_summary(k8s_auth=None) -> dict[str, Any]:
    facts, errors = _collect_kernel_facts(k8s_auth=k8s_auth)
    behind = sorted(
        (
            fact for fact in facts
            if fact["latest_kernel_version"] and fact["kernel_version"] and fact["latest_kernel_version"] != fact["kernel_version"]
        ),
        key=lambda fact: fact["name"],
    )
    answer_lines = [f"Nodes behind the latest observed kernel: {len(behind)}"]
    if behind:
        answer_lines.append("")
        for fact in behind[:10]:
            answer_lines.append(
                f"- `{fact['name']}`: running `{fact['kernel_version']}`, latest seen `{fact['latest_kernel_version']}`"
            )
        if len(behind) > 10:
            answer_lines.append(f"- and {len(behind) - 10} more")
    findings = []
    if behind:
        findings.append("Kernel drift nodes\n" + "\n".join(f"- {fact['name']}" for fact in behind[:10]))
    if errors:
        findings.append(errors[0])
    return {
        "intent": "kernel_drift_summary",
        "answer": "\n".join(answer_lines),
        "confidence": 0.88 if not errors else 0.68,
        "entities": [{"kind": "environment", "id": "kernel_drift", "label": "kernel drift"}],
        "actions": [],
        "needs_clarification": False,
        "findings": findings,
        "scope": {"behind_count": len(behind), "node_total": len(facts)},
        "answer_source": "local",
        "mode": "grounded_environment_summary",
        "provenance": "Grounded Kubernetes and node-agent data",
    }


def answer_kubelet_drift_summary(k8s_auth=None) -> dict[str, Any]:
    facts, errors = _collect_k8s_node_version_facts(k8s_auth=k8s_auth)
    versions = sorted({fact["kubelet_version"] for fact in facts if fact["kubelet_version"]}, key=_version_key)
    if not versions:
        return {
            "intent": "kubelet_drift_summary",
            "answer": "I could not determine kubelet versions from the current node inventory.",
            "confidence": 0.45,
            "entities": [{"kind": "environment", "id": "kubelet_drift", "label": "kubelet drift"}],
            "actions": [],
            "needs_clarification": False,
            "answer_source": "local",
            "mode": "grounded_environment_summary",
            "provenance": "Grounded Kubernetes node inventory",
        }
    latest = versions[-1]
    behind = sorted(
        (fact for fact in facts if fact["kubelet_version"] and fact["kubelet_version"] != latest),
        key=lambda fact: fact["name"],
    )
    answer_lines = [f"Nodes behind the latest observed kubelet version: {len(behind)}"]
    answer_lines.append("")
    answer_lines.append(f"- Latest observed kubelet: `{latest}`")
    if behind:
        for fact in behind[:10]:
            answer_lines.append(f"- `{fact['name']}`: `{fact['kubelet_version']}`")
        if len(behind) > 10:
            answer_lines.append(f"- and {len(behind) - 10} more")
    findings = []
    if behind:
        findings.append("Kubelet drift nodes\n" + "\n".join(f"- {fact['name']} ({fact['kubelet_version']})" for fact in behind[:10]))
    if errors:
        findings.append(errors[0])
    return {
        "intent": "kubelet_drift_summary",
        "answer": "\n".join(answer_lines),
        "confidence": 0.88 if not errors else 0.68,
        "entities": [{"kind": "environment", "id": "kubelet_drift", "label": "kubelet drift"}],
        "actions": [],
        "needs_clarification": False,
        "findings": findings,
        "scope": {"latest_kubelet": latest, "behind_count": len(behind), "node_total": len(facts)},
        "answer_source": "local",
        "mode": "grounded_environment_summary",
        "provenance": "Grounded Kubernetes node inventory",
    }


def answer_os_image_drift_summary(k8s_auth=None) -> dict[str, Any]:
    facts, errors = _collect_k8s_node_version_facts(k8s_auth=k8s_auth)
    images = sorted({fact["os_image"] for fact in facts if fact["os_image"]})
    if not images:
        return {
            "intent": "os_image_drift_summary",
            "answer": "I could not determine node OS images from the current inventory.",
            "confidence": 0.45,
            "entities": [{"kind": "environment", "id": "os_image_drift", "label": "OS image drift"}],
            "actions": [],
            "needs_clarification": False,
            "answer_source": "local",
            "mode": "grounded_environment_summary",
            "provenance": "Grounded Kubernetes node inventory",
        }
    counts: dict[str, int] = {}
    for fact in facts:
        image = fact["os_image"] or "unknown"
        counts[image] = counts.get(image, 0) + 1
    majority_image = max(counts.items(), key=lambda item: (item[1], item[0]))[0]
    drift = sorted(
        (fact for fact in facts if fact["os_image"] and fact["os_image"] != majority_image),
        key=lambda fact: fact["name"],
    )
    answer_lines = [f"Nodes on a non-majority OS image: {len(drift)}"]
    answer_lines.append("")
    answer_lines.append(f"- Majority OS image: `{majority_image}`")
    if drift:
        for fact in drift[:10]:
            answer_lines.append(f"- `{fact['name']}`: `{fact['os_image']}`")
        if len(drift) > 10:
            answer_lines.append(f"- and {len(drift) - 10} more")
    findings = []
    if drift:
        findings.append("OS image drift nodes\n" + "\n".join(f"- {fact['name']} ({fact['os_image']})" for fact in drift[:10]))
    if errors:
        findings.append(errors[0])
    return {
        "intent": "os_image_drift_summary",
        "answer": "\n".join(answer_lines),
        "confidence": 0.86 if not errors else 0.66,
        "entities": [{"kind": "environment", "id": "os_image_drift", "label": "OS image drift"}],
        "actions": [],
        "needs_clarification": False,
        "findings": findings,
        "scope": {"majority_os_image": majority_image, "drift_count": len(drift), "node_total": len(facts)},
        "answer_source": "local",
        "mode": "grounded_environment_summary",
        "provenance": "Grounded Kubernetes node inventory",
    }


def answer_node_attention_summary(openstack_auth=None, k8s_auth=None) -> dict[str, Any]:
    facts, errors = _collect_node_attention_facts(openstack_auth=openstack_auth, k8s_auth=k8s_auth)
    ranked = sorted(
        (fact for fact in facts if fact["risk_count"] > 0),
        key=lambda fact: (-int(fact["risk_count"]), str(fact["name"])),
    )
    answer_lines = [f"Nodes needing the most attention: {len(ranked)}"]
    if ranked:
        answer_lines.append("")
        for fact in ranked[:8]:
            answer_lines.append(f"- `{fact['name']}`: {', '.join(fact['risk_flags'])}")
    findings = []
    if ranked:
        findings.append("Highest-risk nodes\n" + "\n".join(f"- {fact['name']} ({fact['risk_count']} signals)" for fact in ranked[:8]))
    if errors:
        findings.append(errors[0])
    return {
        "intent": "node_attention_summary",
        "answer": "\n".join(answer_lines),
        "confidence": 0.84 if not errors else 0.64,
        "entities": [{"kind": "environment", "id": "node_attention", "label": "node attention"}],
        "actions": [],
        "needs_clarification": False,
        "findings": findings,
        "scope": {"flagged_count": len(ranked), "node_total": len(facts)},
        "answer_source": "local",
        "mode": "grounded_environment_summary",
        "provenance": "Grounded Kubernetes, OpenStack, and node-agent data",
    }


def answer_hypervisor_metric_summary(intent: str, openstack_auth=None, k8s_auth=None) -> dict[str, Any] | None:
    config = HYPERVISOR_METRIC_SUMMARIES.get(str(intent or "").strip())
    if config is None:
        return None
    return answer_hypervisor_metric_ranking(
        config["metric"],
        openstack_auth=openstack_auth,
        k8s_auth=k8s_auth,
        intent=intent,
        operator=config.get("operator", "max"),
        answer_label=config["answer_label"],
        answer_verb=config.get("answer_verb", "highest"),
        answer_suffix=config["answer_suffix"],
        entity_label=config["entity_label"],
        findings_label=config["findings_label"],
        collector=config.get("collector", "detail"),
        include_suffix_in_value=bool(config.get("include_suffix_in_value", False)),
    )


def answer_hypervisor_metric_top_n(
    metric_key: str,
    limit: int,
    openstack_auth=None,
    k8s_auth=None,
) -> dict[str, Any] | None:
    summary_intent = HYPERVISOR_METRIC_KEY_TO_INTENT.get(str(metric_key or "").strip())
    if not summary_intent:
        return None
    config = HYPERVISOR_METRIC_SUMMARIES.get(summary_intent)
    if config is None:
        return None
    metric = config["metric"]
    collector = config.get("collector", "detail")
    if collector == "summary":
        facts, errors = _collect_hypervisor_vm_facts(openstack_auth=openstack_auth, k8s_auth=k8s_auth)
    else:
        facts, errors = _collect_hypervisor_detail_facts(openstack_auth=openstack_auth, k8s_auth=k8s_auth)
    reverse = config.get("operator", "max") != "min"
    ranked = sorted(
        facts,
        key=lambda item: (int(item.get(metric) or 0), str(item.get("name") or "")),
        reverse=reverse,
    )
    if not ranked:
        return None
    top_n = max(1, min(int(limit or 3), 10))
    selected = ranked[:top_n]
    title = f"Top {top_n} hypervisors by {config['answer_label']}" if reverse else f"Bottom {top_n} hypervisors by {config['answer_label']}"
    lines = [title, ""]
    for item in selected:
        value = _format_metric_value(metric, item)
        if config.get("include_suffix_in_value", False):
            value = f"{value} {config['answer_suffix']}"
        lines.append(f"- `{item.get('name')}`: `{value}`")
    findings = [
        f"{config['findings_label']}\n" + "\n".join(
            f"- {item.get('name')} ({_format_metric_value(metric, item)})" for item in selected
        )
    ]
    if errors:
        findings.append(errors[0])
    return {
        "intent": "hypervisor_metric_top_n",
        "answer": "\n".join(lines),
        "confidence": 0.86 if not errors else 0.66,
        "entities": [{"kind": "environment", "id": metric_key, "label": config["entity_label"]}],
        "actions": [],
        "needs_clarification": False,
        "findings": findings,
        "scope": {
            "metric_key": metric_key,
            "metric": metric,
            "count": top_n,
        },
        "answer_source": "local",
        "mode": "grounded_environment_summary",
        "provenance": "Grounded Kubernetes and OpenStack data",
    }


def hypervisor_metric_choices() -> list[dict[str, Any]]:
    order = [
        "busiest_hypervisor_summary",
        "highest_ram_hypervisor_summary",
        "highest_cpu_hypervisor_summary",
        "highest_disk_hypervisor_summary",
        "emptiest_hypervisor_summary",
    ]
    return [
        {
            "kind": "environment_metric",
            "id": intent,
            "label": HYPERVISOR_METRIC_SUMMARIES[intent]["choice_label"],
            "subtext": HYPERVISOR_METRIC_SUMMARIES[intent]["choice_subtext"],
        }
        for intent in order
        if intent in HYPERVISOR_METRIC_SUMMARIES
    ]


def answer_busiest_hypervisor_summary(openstack_auth=None, k8s_auth=None) -> dict[str, Any]:
    return answer_hypervisor_metric_summary("busiest_hypervisor_summary", openstack_auth, k8s_auth) or {}


def answer_highest_ram_hypervisor_summary(openstack_auth=None, k8s_auth=None) -> dict[str, Any]:
    return answer_hypervisor_metric_summary("highest_ram_hypervisor_summary", openstack_auth, k8s_auth) or {}


def answer_highest_cpu_hypervisor_summary(openstack_auth=None, k8s_auth=None) -> dict[str, Any]:
    return answer_hypervisor_metric_summary("highest_cpu_hypervisor_summary", openstack_auth, k8s_auth) or {}


def answer_highest_disk_hypervisor_summary(openstack_auth=None, k8s_auth=None) -> dict[str, Any]:
    return answer_hypervisor_metric_summary("highest_disk_hypervisor_summary", openstack_auth, k8s_auth) or {}


def answer_emptiest_hypervisor_summary(openstack_auth=None, k8s_auth=None) -> dict[str, Any]:
    return answer_hypervisor_metric_summary("emptiest_hypervisor_summary", openstack_auth, k8s_auth) or {}


ENVIRONMENT_SUMMARY_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "kernel_version_summary": answer_kernel_version_summary,
    "kernel_drift_summary": answer_kernel_drift_summary,
    "kubelet_drift_summary": answer_kubelet_drift_summary,
    "missing_node_agent_summary": answer_missing_node_agent_summary,
    "reboot_needed_summary": answer_reboot_needed_summary,
    "node_attention_summary": answer_node_attention_summary,
    "os_image_drift_summary": answer_os_image_drift_summary,
    "empty_compute_nodes_summary": answer_empty_compute_nodes_summary,
    **{intent: answer_hypervisor_metric_summary for intent in HYPERVISOR_METRIC_SUMMARIES},
}

ENVIRONMENT_SUMMARY_INTENTS = frozenset(ENVIRONMENT_SUMMARY_HANDLERS.keys())


def answer_environment_summary(intent: str, openstack_auth=None, k8s_auth=None) -> dict[str, Any] | None:
    intent = str(intent or "").strip()
    handler = ENVIRONMENT_SUMMARY_HANDLERS.get(intent)
    if handler is None:
        return None
    if intent in HYPERVISOR_METRIC_SUMMARIES:
        return handler(intent, openstack_auth=openstack_auth, k8s_auth=k8s_auth)
    if intent in {"kernel_version_summary", "kernel_drift_summary", "kubelet_drift_summary", "missing_node_agent_summary", "os_image_drift_summary"}:
        return handler(k8s_auth=k8s_auth)
    return handler(openstack_auth=openstack_auth, k8s_auth=k8s_auth)
