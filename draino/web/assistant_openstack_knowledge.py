"""Deterministic OpenStack knowledge helpers for assistant answers."""
from __future__ import annotations

import re
from typing import Any


_TRAIT_RE = re.compile(r"\b([A-Z][A-Z0-9_]{5,})\b")

_TRAIT_PREFIX_CONTEXT: list[tuple[str, dict[str, str]]] = [
    (
        "COMPUTE_NET_VIF_MODEL_",
        {
            "category": "virtual NIC model trait",
            "scope": "OpenStack Placement trait for Nova compute hosts",
            "usage": "used to advertise support for a specific guest virtual NIC model on a compute host",
            "scheduling": "instances that request this trait through flavor or image scheduling constraints should land only on hosts that expose it",
        },
    ),
    (
        "COMPUTE_NET_",
        {
            "category": "network capability trait",
            "scope": "OpenStack Placement trait for Nova compute hosts",
            "usage": "used to advertise a network-related compute capability to the scheduler",
            "scheduling": "instances that request this trait are expected to land on hosts advertising that capability",
        },
    ),
    (
        "COMPUTE_",
        {
            "category": "compute capability trait",
            "scope": "OpenStack Placement trait for Nova compute hosts",
            "usage": "used to advertise a Nova compute capability to Placement",
            "scheduling": "scheduler decisions can constrain workloads to hosts exposing the trait",
        },
    ),
    (
        "HW_",
        {
            "category": "hardware capability trait",
            "scope": "OpenStack Placement trait for resource providers",
            "usage": "used to advertise a hardware feature or capability to Placement",
            "scheduling": "scheduler decisions can constrain workloads to hosts exposing the hardware trait",
        },
    ),
    (
        "STORAGE_",
        {
            "category": "storage capability trait",
            "scope": "OpenStack Placement trait for resource providers",
            "usage": "used to advertise a storage-related capability to Placement",
            "scheduling": "scheduler decisions can constrain workloads to hosts exposing the storage trait",
        },
    ),
    (
        "CUSTOM_",
        {
            "category": "custom operator trait",
            "scope": "OpenStack Placement custom trait",
            "usage": "defined by operators to represent environment-specific capabilities or constraints",
            "scheduling": "instances or requests that require the custom trait should land only on hosts advertising it",
        },
    ),
]

_VIF_MODEL_DETAIL = {
    "RTL8139": "an older emulated Realtek RTL8139 NIC model, usually relevant for legacy guest compatibility",
    "E1000": "an emulated Intel E1000 NIC model, commonly used for broad guest compatibility",
    "E1000E": "an emulated Intel E1000E NIC model",
    "VIRTIO": "the VirtIO paravirtualized NIC model, typically preferred for modern guest performance",
    "NE2K_PCI": "the legacy NE2000 PCI emulated NIC model",
    "PCNET": "the AMD PCnet emulated NIC model often used for older guest compatibility",
}


def extract_platform_trait(query: str) -> str:
    text = str(query or "")
    match = _TRAIT_RE.search(text)
    return str(match.group(1) or "").strip() if match else ""


def build_platform_knowledge_context(query: str) -> dict[str, Any]:
    trait = extract_platform_trait(query)
    if not trait:
        return {}
    for prefix, context in _TRAIT_PREFIX_CONTEXT:
        if trait.startswith(prefix):
            suffix = trait[len(prefix):]
            details: list[str] = []
            if prefix == "COMPUTE_NET_VIF_MODEL_":
                details.append(_VIF_MODEL_DETAIL.get(suffix, f"the `{suffix}` guest virtual NIC model"))
            return {
                "kind": "placement_trait",
                "trait": trait,
                "trait_prefix": prefix.rstrip("_"),
                "trait_suffix": suffix,
                "category": context["category"],
                "scope": context["scope"],
                "usage": context["usage"],
                "scheduling": context["scheduling"],
                "details": details,
                "inference": "This explanation is based on the trait naming convention and standard Placement trait usage.",
            }
    return {
        "kind": "placement_trait",
        "trait": trait,
        "trait_prefix": "",
        "trait_suffix": "",
        "category": "Placement trait",
        "scope": "OpenStack Placement trait",
        "usage": "used to advertise a capability or constraint to the scheduler",
        "scheduling": "workloads that request the trait should land only on hosts that expose it",
        "details": [],
        "inference": "This explanation is based on the trait naming convention and standard Placement trait usage.",
    }


def fallback_platform_knowledge_answer(query: str, context: dict[str, Any]) -> dict[str, Any]:
    if str(context.get("kind") or "") != "placement_trait":
        return {}
    trait = str(context.get("trait") or "").strip()
    usage = str(context.get("usage") or "").strip()
    scheduling = str(context.get("scheduling") or "").strip()
    category = str(context.get("category") or "").strip()
    details = list(context.get("details") or [])
    lines = [
        f"`{trait}` is a {category}.",
        "",
        f"- It is {usage}.",
        f"- In practice, {scheduling}.",
    ]
    if details:
        lines.append(f"- For this specific trait, it usually refers to {details[0]}.")
    lines.append(f"- {str(context.get('inference') or '').strip()}")
    return {
        "answer": "\n".join(lines),
        "confidence": 0.78,
    }
