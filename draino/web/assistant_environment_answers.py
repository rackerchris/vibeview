"""Grounded environment and fleet summary assistant answers."""
from __future__ import annotations

import re
from typing import Any

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


def answer_hypervisor_metric_ranking(
    metric: str,
    *,
    openstack_auth=None,
    k8s_auth=None,
    intent: str,
    answer_label: str,
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
    ranked = sorted(facts, key=lambda item: (-int(item.get(metric) or 0), str(item.get("name") or "")))
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
        f"The hypervisor with the highest {answer_label} is `{busiest.get('name')}` with `{display_value}`."
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


def answer_busiest_hypervisor_summary(openstack_auth=None, k8s_auth=None) -> dict[str, Any]:
    return answer_hypervisor_metric_ranking(
        "running_vms",
        openstack_auth=openstack_auth,
        k8s_auth=k8s_auth,
        intent="busiest_hypervisor_summary",
        answer_label="VM load",
        answer_suffix="running VMs",
        entity_label="hypervisor load",
        findings_label="Top hypervisors by running VMs",
        collector="summary",
    )


def answer_highest_ram_hypervisor_summary(openstack_auth=None, k8s_auth=None) -> dict[str, Any]:
    return answer_hypervisor_metric_ranking(
        "memory_mb_used",
        openstack_auth=openstack_auth,
        k8s_auth=k8s_auth,
        intent="highest_ram_hypervisor_summary",
        answer_label="RAM usage",
        answer_suffix="RAM in use",
        entity_label="hypervisor RAM usage",
        findings_label="Top hypervisors by RAM in use",
        include_suffix_in_value=False,
    )


def answer_highest_cpu_hypervisor_summary(openstack_auth=None, k8s_auth=None) -> dict[str, Any]:
    return answer_hypervisor_metric_ranking(
        "vcpus_used",
        openstack_auth=openstack_auth,
        k8s_auth=k8s_auth,
        intent="highest_cpu_hypervisor_summary",
        answer_label="CPU usage",
        answer_suffix="vCPUs in use",
        entity_label="hypervisor CPU usage",
        findings_label="Top hypervisors by vCPU use",
        include_suffix_in_value=True,
    )


def answer_highest_disk_hypervisor_summary(openstack_auth=None, k8s_auth=None) -> dict[str, Any]:
    return answer_hypervisor_metric_ranking(
        "local_disk_gb_used",
        openstack_auth=openstack_auth,
        k8s_auth=k8s_auth,
        intent="highest_disk_hypervisor_summary",
        answer_label="local disk usage",
        answer_suffix="local disk in use",
        entity_label="hypervisor disk usage",
        findings_label="Top hypervisors by local disk use",
        include_suffix_in_value=True,
    )
