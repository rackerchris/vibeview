"""Grounded follow-up handling for assistant answers."""
from __future__ import annotations

import re
from typing import Any

from .assistant_cluster_answers import (
    answer_cluster_health_overview,
    answer_control_plane_component_summary,
    answer_etcd_health_summary,
    answer_mariadb_node_summary,
    answer_node_ready_summary,
)
from .assistant_environment_answers import (
    ENVIRONMENT_SUMMARY_INTENTS,
    HYPERVISOR_METRIC_KEY_TO_INTENT,
    answer_compute_nodes_with_workloads_summary,
    answer_environment_summary,
    answer_hypervisor_metric_top_n,
    answer_managed_noschedule_summary,
    answer_node_comparison,
)

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

_CLUSTER_INTENTS = {
    "etcd_health_summary",
    "mariadb_node_summary",
    "cluster_health_overview",
    "node_ready_summary",
    "control_plane_component_summary",
}


def _normalize_text(value: str | None) -> str:
    return str(value or "").strip().lower()


def _extract_top_n(text: str) -> tuple[str, int] | None:
    lowered = _normalize_text(text)
    match = re.search(r"\b(top|bottom)\s+(\d+)\b", lowered)
    if match:
        return match.group(1), max(1, min(int(match.group(2)), 10))
    match = re.search(r"\b(top|bottom)\s+(one|two|three|four|five|six|seven|eight|nine|ten)\b", lowered)
    if match:
        return match.group(1), _NUMBER_WORDS[match.group(2)]
    if re.search(r"\bshow\b", lowered) and re.search(r"\b(top|bottom)\b", lowered):
        return ("top", 5)
    return None


def detect_followup_kind(query: str) -> tuple[str, dict[str, Any]] | None:
    lowered = _normalize_text(query)
    if not lowered:
        return None
    hot_match = re.search(r"\bwhy is\s+([a-zA-Z0-9._:-]+)\s+hot\??$", lowered)
    if hot_match:
        return ("why_hot", {"target": hot_match.group(1)})
    if lowered in {"why", "why?", "how so", "how so?", "why is that", "why is that?"} or lowered.startswith("why "):
        return ("why", {})
    if lowered in {"open the top node", "open top node", "open it", "open that node", "take me there"}:
        return ("open_top", {})
    if lowered in {"open the next one", "open next one", "open the next node", "open next node"}:
        return ("open_next", {})
    compare_top_match = re.search(r"\bcompare top\s+(\d+)\b", lowered)
    if compare_top_match:
        return ("compare_top_n", {"limit": max(2, min(int(compare_top_match.group(1)), 10))})
    if lowered in {"compare it to the next one", "compare to the next one", "compare it with the next one", "compare it to the next node"}:
        return ("compare_next", {})
    if lowered in {"show affected namespaces", "show the affected namespaces", "which namespaces are affected", "what namespaces are affected"}:
        return ("affected_namespaces", {})
    compare_named = re.search(r"\bcompare\s+([a-zA-Z0-9._:-]+)\s+(?:and|to|with|vs\.?)\s+([a-zA-Z0-9._:-]+)\b", lowered)
    if compare_named:
        return ("compare_named_nodes", {"left": compare_named.group(1), "right": compare_named.group(2)})
    if lowered in {"show only the managed ones", "show only managed ones", "only show the managed ones", "show the managed ones"}:
        return ("managed_only", {})
    if lowered in {"show only compute nodes with workloads", "show only the compute nodes with workloads", "show compute nodes with workloads", "which compute nodes have workloads"}:
        return ("compute_with_workloads", {})
    if lowered in {"show only the unhealthy ones", "show only unhealthy ones", "only show the unhealthy ones", "show the unhealthy ones"}:
        return ("unhealthy_only", {})
    if lowered in {"what changed", "what changed?", "what changed since then?", "what changed since then"}:
        return ("changed", {})
    top_n = _extract_top_n(lowered)
    if top_n:
        direction, limit = top_n
        return ("top_n", {"direction": direction, "limit": limit})
    if lowered in {"is that a problem", "is that a problem?", "is this a problem", "is this a problem?", "is that bad", "is that bad?"}:
        return ("problem", {})
    return None


def _followup_base_result(previous_result: dict[str, Any], auth, k8s_auth) -> dict[str, Any] | None:
    previous_intent = str(previous_result.get("intent") or "").strip()
    if previous_intent in ENVIRONMENT_SUMMARY_INTENTS:
        return answer_environment_summary(previous_intent, auth, k8s_auth)
    if previous_intent == "etcd_health_summary":
        return answer_etcd_health_summary(k8s_auth)
    if previous_intent == "mariadb_node_summary":
        return answer_mariadb_node_summary(k8s_auth)
    if previous_intent == "cluster_health_overview":
        return answer_cluster_health_overview(k8s_auth)
    if previous_intent == "node_ready_summary":
        return answer_node_ready_summary(k8s_auth)
    if previous_intent == "control_plane_component_summary":
        component = str((previous_result.get("scope") or {}).get("component") or "control_plane")
        return answer_control_plane_component_summary(component, k8s_auth)
    return None


def _metric_key_from_result(previous_result: dict[str, Any]) -> str:
    scope = previous_result.get("scope") or {}
    metric_key = str(scope.get("metric_key") or "").strip()
    if metric_key:
        return metric_key
    previous_intent = str(previous_result.get("intent") or "").strip()
    for key, intent in HYPERVISOR_METRIC_KEY_TO_INTENT.items():
        if intent == previous_intent:
            return key
    return ""


def _extract_finding_blocks(result: dict[str, Any]) -> list[str]:
    return [str(item or "").strip() for item in list(result.get("findings") or []) if str(item or "").strip()]


def _truncate_finding_block(text: str, limit: int) -> str:
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) <= 1:
        return lines[0]
    heading = lines[0]
    bullets = [line for line in lines[1:] if line.lstrip().startswith("-")]
    if not bullets:
        return "\n".join(lines[: limit + 1])
    return "\n".join([heading, *bullets[:limit]])


def _first_action(base_result: dict[str, Any], previous_result: dict[str, Any]) -> dict[str, Any] | None:
    for source in (base_result, previous_result):
        for action in list(source.get("actions") or []):
            if isinstance(action, dict):
                return action
    return None


def _nth_action(base_result: dict[str, Any], previous_result: dict[str, Any], index: int) -> dict[str, Any] | None:
    actions: list[dict[str, Any]] = []
    for source in (base_result, previous_result):
        for action in list(source.get("actions") or []):
            if isinstance(action, dict) and action not in actions:
                actions.append(action)
    if 0 <= index < len(actions):
        return actions[index]
    return None


def _problem_assessment(previous_intent: str, base_result: dict[str, Any], previous_result: dict[str, Any]) -> str:
    scope = dict(base_result.get("scope") or previous_result.get("scope") or {})
    if previous_intent == "noschedule_node_summary":
        count = int(scope.get("noschedule_count") or 0)
        managed = int(scope.get("managed_noschedule_count") or 0)
        if count == 0:
            return "No. I do not currently see any `NoSchedule` taints."
        if managed == count:
            return "Not necessarily. These look like intentional VibeView-managed `NoSchedule` taints, so they are usually a scheduling control or maintenance posture rather than an outright problem."
        return "Potentially. There are `NoSchedule` taints present that are not all VibeView-managed, so you should confirm they are intentional and that workloads are not being stranded."
    if previous_intent in {"not_ready_node_summary", "cordoned_node_summary", "cluster_health_overview", "control_plane_runtime_summary", "etcd_health_summary"}:
        findings = _extract_finding_blocks(base_result)
        if findings:
            return "Yes, this deserves attention. The current grounded summary shows health or scheduling exceptions that can affect workload placement or cluster resilience."
        return "Not immediately. I do not see grounded evidence of an active health issue in that summary."
    if previous_intent in {"cpu_request_load_summary", "memory_request_load_summary", "pod_density_summary"}:
        pct = None
        for key in ("top_cpu_req_pct", "top_memory_req_pct", "top_pods_pct"):
            if scope.get(key) is not None:
                try:
                    pct = float(scope[key])
                    break
                except Exception:
                    pass
        if pct is not None and pct >= 90:
            return "Yes, potentially. That node is near a high-utilization threshold, so it is worth checking headroom, evictions, and scheduling concentration."
        if pct is not None and pct >= 75:
            return "Not necessarily, but it is worth watching. The node looks relatively hot compared with peers."
        return "Not automatically. This indicates a relative hotspot, but not necessarily a problem without pressure, failures, or scheduling symptoms."
    if previous_intent in {"kernel_drift_summary", "kubelet_drift_summary", "container_runtime_drift_summary", "os_image_drift_summary", "reboot_needed_summary", "node_attention_summary"}:
        count = 0
        for key in ("behind_count", "drift_count", "reboot_required_count", "flagged_count"):
            count = max(count, int(scope.get(key) or 0))
        if count > 0:
            return "It is worth reviewing. This is drift or maintenance debt rather than an immediate outage signal, but it can increase operational risk."
        return "No immediate issue is visible in that summary."
    if previous_intent in ENVIRONMENT_SUMMARY_INTENTS:
        return "This looks like a useful hotspot or drift signal, but whether it is a problem depends on current headroom, pressure, and whether the state is intentional."
    return "I can only assess that follow-up for grounded environment and cluster summaries right now."


def answer_followup_query(
    query: str,
    previous_result: dict[str, Any] | None,
    auth,
    k8s_auth,
    parsed_followup: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(previous_result, dict) or not previous_result:
        return None
    detected = None
    if isinstance(parsed_followup, dict) and parsed_followup:
        kind = _normalize_text(parsed_followup.get("kind"))
        if kind:
            params = {
                "target": str(parsed_followup.get("target") or "").strip(),
                "limit": parsed_followup.get("limit"),
                "direction": str(parsed_followup.get("direction") or "").strip().lower(),
                "left": str(parsed_followup.get("left") or "").strip(),
                "right": str(parsed_followup.get("right") or "").strip(),
            }
            detected = (kind, params)
    if not detected:
        detected = detect_followup_kind(query)
    if not detected:
        return None
    kind, params = detected
    previous_intent = str(previous_result.get("intent") or "").strip()

    if kind == "why_hot":
        base_result = _followup_base_result(previous_result, auth, k8s_auth)
        if base_result is None:
            return None
        target = str(params.get("target") or "").strip()
        findings = _extract_finding_blocks(base_result)
        answer = f"`{target}` looks hot based on the current grounded summary."
        if findings:
            answer += f"\n\n{findings[0]}"
        return {
            "intent": "followup_why_hot",
            "answer": answer,
            "confidence": float(base_result.get("confidence") or previous_result.get("confidence") or 0.75),
            "entities": list(base_result.get("entities") or previous_result.get("entities") or []),
            "actions": list(base_result.get("actions") or previous_result.get("actions") or []),
            "needs_clarification": False,
            "findings": findings[:1],
            "answer_source": "local",
            "mode": "grounded_followup",
            "provenance": "Grounded follow-up from previous result",
        }

    if kind == "why":
        base_result = _followup_base_result(previous_result, auth, k8s_auth)
        if base_result is None:
            return None
        findings = _extract_finding_blocks(base_result)
        answer = "Why:\n" + ("\n\n".join(findings[:2]) if findings else str(base_result.get("answer") or "").strip())
        return {
            "intent": "followup_why",
            "answer": answer.strip(),
            "confidence": float(base_result.get("confidence") or previous_result.get("confidence") or 0.75),
            "entities": list(base_result.get("entities") or previous_result.get("entities") or []),
            "actions": list(base_result.get("actions") or previous_result.get("actions") or []),
            "needs_clarification": False,
            "findings": findings[:2],
            "answer_source": "local",
            "mode": "grounded_followup",
            "provenance": "Grounded follow-up from previous result",
        }

    if kind == "top_n":
        base_result = _followup_base_result(previous_result, auth, k8s_auth)
        if base_result is None:
            return None
        direction = str(params.get("direction") or "top")
        limit = int(params.get("limit") or 5)
        metric_key = _metric_key_from_result(previous_result)
        if metric_key:
            ranking = answer_hypervisor_metric_top_n(metric_key, limit, auth, k8s_auth)
            if ranking:
                ranking["intent"] = "followup_top_n"
                ranking["mode"] = "grounded_followup"
                ranking["provenance"] = "Grounded follow-up from previous result"
                return ranking
        findings = _extract_finding_blocks(base_result)
        if findings:
            selected = _truncate_finding_block(findings[0], limit)
            heading = "Here are the top results:" if direction != "bottom" else "Here are the bottom results:"
            return {
                "intent": "followup_top_n",
                "answer": f"{heading}\n\n{selected}".strip(),
                "confidence": float(base_result.get("confidence") or previous_result.get("confidence") or 0.75),
                "entities": list(base_result.get("entities") or previous_result.get("entities") or []),
                "actions": list(base_result.get("actions") or previous_result.get("actions") or []),
                "needs_clarification": False,
                "findings": [selected],
                "answer_source": "local",
                "mode": "grounded_followup",
                "provenance": "Grounded follow-up from previous result",
            }
        return None

    if kind == "open_top":
        action = _first_action({}, previous_result)
        if not action:
            return None
        label = str(action.get("label") or action.get("node") or "that resource")
        return {
            "intent": "followup_open_top",
            "answer": f"Opening {label}.",
            "confidence": float(previous_result.get("confidence") or 0.85),
            "entities": list(previous_result.get("entities") or []),
            "actions": [action],
            "needs_clarification": False,
            "findings": [],
            "answer_source": "local",
            "mode": "grounded_followup",
            "provenance": "Grounded follow-up from previous result",
        }

    if kind == "open_next":
        base_result = _followup_base_result(previous_result, auth, k8s_auth)
        action = _nth_action(base_result or {}, previous_result, 1)
        if not action:
            return None
        label = str(action.get("label") or action.get("node") or "the next resource")
        return {
            "intent": "followup_open_next",
            "answer": f"Opening {label}.",
            "confidence": float((base_result or {}).get("confidence") or previous_result.get("confidence") or 0.8),
            "entities": list((base_result or {}).get("entities") or previous_result.get("entities") or []),
            "actions": [action],
            "needs_clarification": False,
            "findings": [],
            "answer_source": "local",
            "mode": "grounded_followup",
            "provenance": "Grounded follow-up from previous result",
        }

    if kind == "compare_next":
        base_result = _followup_base_result(previous_result, auth, k8s_auth)
        if base_result is None:
            return None
        metric_key = _metric_key_from_result(previous_result)
        if metric_key:
            comparison = answer_hypervisor_metric_top_n(metric_key, 2, auth, k8s_auth)
            if comparison:
                return {
                    "intent": "followup_compare_next",
                    "answer": f"Comparison against the next result:\n\n{comparison.get('answer') or ''}".strip(),
                    "confidence": float(comparison.get("confidence") or previous_result.get("confidence") or 0.8),
                    "entities": list(comparison.get("entities") or previous_result.get("entities") or []),
                    "actions": list(comparison.get("actions") or previous_result.get("actions") or []),
                    "needs_clarification": False,
                    "findings": list(comparison.get("findings") or []),
                    "answer_source": "local",
                    "mode": "grounded_followup",
                    "provenance": "Grounded follow-up from previous result",
                }
        findings = _extract_finding_blocks(base_result)
        if findings:
            selected = _truncate_finding_block(findings[0], 2)
            return {
                "intent": "followup_compare_next",
                "answer": f"Comparison against the next result:\n\n{selected}".strip(),
                "confidence": float(base_result.get("confidence") or previous_result.get("confidence") or 0.75),
                "entities": list(base_result.get("entities") or previous_result.get("entities") or []),
                "actions": list(base_result.get("actions") or previous_result.get("actions") or []),
                "needs_clarification": False,
                "findings": [selected],
                "answer_source": "local",
                "mode": "grounded_followup",
                "provenance": "Grounded follow-up from previous result",
            }
        return None

    if kind == "compare_top_n":
        base_result = _followup_base_result(previous_result, auth, k8s_auth)
        if base_result is None:
            return None
        limit = int(params.get("limit") or 3)
        metric_key = _metric_key_from_result(previous_result)
        if metric_key:
            ranking = answer_hypervisor_metric_top_n(metric_key, limit, auth, k8s_auth)
            if ranking:
                ranking["intent"] = "followup_compare_top_n"
                ranking["mode"] = "grounded_followup"
                ranking["provenance"] = "Grounded follow-up from previous result"
                ranking["answer"] = f"Comparison of top {limit}:\n\n{ranking.get('answer') or ''}".strip()
                return ranking
        findings = _extract_finding_blocks(base_result)
        if findings:
            selected = _truncate_finding_block(findings[0], limit)
            return {
                "intent": "followup_compare_top_n",
                "answer": f"Comparison of top {limit}:\n\n{selected}".strip(),
                "confidence": float(base_result.get("confidence") or previous_result.get("confidence") or 0.75),
                "entities": list(base_result.get("entities") or previous_result.get("entities") or []),
                "actions": list(base_result.get("actions") or previous_result.get("actions") or []),
                "needs_clarification": False,
                "findings": [selected],
                "answer_source": "local",
                "mode": "grounded_followup",
                "provenance": "Grounded follow-up from previous result",
            }
        return None

    if kind == "affected_namespaces":
        base_result = _followup_base_result(previous_result, auth, k8s_auth)
        scope = dict((base_result or {}).get("scope") or previous_result.get("scope") or {})
        namespaces = list(scope.get("top_namespace_names") or [])
        top_node = str(scope.get("top_node") or "").strip()
        if not namespaces:
            return {
                "intent": "followup_affected_namespaces",
                "answer": "I do not have namespace names available for that grounded summary.",
                "confidence": float((base_result or {}).get("confidence") or previous_result.get("confidence") or 0.6),
                "entities": list((base_result or {}).get("entities") or previous_result.get("entities") or []),
                "actions": list((base_result or {}).get("actions") or previous_result.get("actions") or []),
                "needs_clarification": False,
                "findings": [],
                "answer_source": "local",
                "mode": "grounded_followup",
                "provenance": "Grounded follow-up from previous result",
            }
        lines = [f"Affected namespaces on `{top_node}`:" if top_node else "Affected namespaces:", ""]
        lines.extend(f"- `{name}`" for name in namespaces[:10])
        if len(namespaces) > 10:
            lines.append(f"- and {len(namespaces) - 10} more")
        return {
            "intent": "followup_affected_namespaces",
            "answer": "\n".join(lines),
            "confidence": float((base_result or {}).get("confidence") or previous_result.get("confidence") or 0.8),
            "entities": list((base_result or {}).get("entities") or previous_result.get("entities") or []),
            "actions": list((base_result or {}).get("actions") or previous_result.get("actions") or []),
            "needs_clarification": False,
            "findings": [],
            "answer_source": "local",
            "mode": "grounded_followup",
            "provenance": "Grounded follow-up from previous result",
        }

    if kind == "compare_named_nodes":
        left = str(params.get("left") or "").strip()
        right = str(params.get("right") or "").strip()
        comparison = answer_node_comparison([left, right], openstack_auth=auth, k8s_auth=k8s_auth)
        if not comparison:
            return None
        comparison["intent"] = "followup_compare_named_nodes"
        comparison["mode"] = "grounded_followup"
        comparison["provenance"] = "Grounded follow-up from previous result"
        return comparison

    if kind == "managed_only":
        if previous_intent != "noschedule_node_summary":
            return None
        managed = answer_managed_noschedule_summary(k8s_auth=k8s_auth)
        managed["intent"] = "followup_managed_only"
        managed["mode"] = "grounded_followup"
        managed["provenance"] = "Grounded follow-up from previous result"
        return managed

    if kind == "compute_with_workloads":
        summary = answer_compute_nodes_with_workloads_summary(openstack_auth=auth, k8s_auth=k8s_auth)
        summary["intent"] = "followup_compute_with_workloads"
        summary["mode"] = "grounded_followup"
        summary["provenance"] = "Grounded follow-up from previous result"
        return summary

    if kind == "unhealthy_only":
        base_result = _followup_base_result(previous_result, auth, k8s_auth)
        if base_result is None:
            return None
        findings = _extract_finding_blocks(base_result)
        if findings:
            return {
                "intent": "followup_unhealthy_only",
                "answer": f"Only the unhealthy or flagged resources:\n\n{findings[0]}".strip(),
                "confidence": float(base_result.get("confidence") or previous_result.get("confidence") or 0.8),
                "entities": list(base_result.get("entities") or previous_result.get("entities") or []),
                "actions": list(base_result.get("actions") or previous_result.get("actions") or []),
                "needs_clarification": False,
                "findings": findings[:1],
                "answer_source": "local",
                "mode": "grounded_followup",
                "provenance": "Grounded follow-up from previous result",
            }
        return {
            "intent": "followup_unhealthy_only",
            "answer": "I do not have a separate unhealthy-only subset for that grounded summary.",
            "confidence": float(base_result.get("confidence") or previous_result.get("confidence") or 0.6),
            "entities": list(base_result.get("entities") or previous_result.get("entities") or []),
            "actions": list(base_result.get("actions") or previous_result.get("actions") or []),
            "needs_clarification": False,
            "findings": [],
            "answer_source": "local",
            "mode": "grounded_followup",
            "provenance": "Grounded follow-up from previous result",
        }

    if kind == "changed":
        return {
            "intent": "followup_changed",
            "answer": "I do not have a historical baseline in this conversation to tell you what changed. I can tell you the current grounded state, or you can ask me to compare this against a prior snapshot or report.",
            "confidence": 0.9,
            "entities": list(previous_result.get("entities") or []),
            "actions": list(previous_result.get("actions") or []),
            "needs_clarification": False,
            "findings": [],
            "answer_source": "local",
            "mode": "grounded_followup",
            "provenance": "Grounded follow-up from previous result",
        }

    if kind == "problem":
        base_result = _followup_base_result(previous_result, auth, k8s_auth)
        if base_result is None:
            return None
        return {
            "intent": "followup_problem",
            "answer": _problem_assessment(previous_intent, base_result, previous_result),
            "confidence": float(base_result.get("confidence") or previous_result.get("confidence") or 0.7),
            "entities": list(base_result.get("entities") or previous_result.get("entities") or []),
            "actions": list(base_result.get("actions") or previous_result.get("actions") or []),
            "needs_clarification": False,
            "findings": _extract_finding_blocks(base_result)[:1],
            "answer_source": "local",
            "mode": "grounded_followup",
            "provenance": "Grounded follow-up from previous result",
        }

    return None
