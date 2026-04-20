"""Provider-backed synthesis for assistant tasks."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

_LOGGER = logging.getLogger("draino.web")


def _safe_json_parse(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _coerce_confidence(value: Any, default: float) -> float:
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


def _coerce_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_provider_result(data: dict[str, Any], default_confidence: float) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    normalized = dict(data)
    normalized["answer"] = str(data.get("answer") or "").strip()
    normalized["confidence"] = _coerce_confidence(data.get("confidence"), default_confidence)
    if "findings" in data:
        normalized["findings"] = _coerce_string_list(data.get("findings"))
    if "recommendations" in data:
        normalized["recommendations"] = _coerce_string_list(data.get("recommendations"))
    return normalized


def _provider_chat(provider: dict[str, Any], system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
    from openai import OpenAI

    timeout_seconds = float(provider.get("timeout") or 10.0)
    client = OpenAI(
        base_url=str(provider.get("base_url") or ""),
        api_key=str(provider.get("api_key") or ""),
        timeout=httpx.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds), read=timeout_seconds, write=min(3.0, timeout_seconds)),
        max_retries=0,
        http_client=httpx.Client(
            timeout=httpx.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds), read=timeout_seconds, write=min(3.0, timeout_seconds)),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        ),
    )
    try:
        response = client.chat.completions.create(
            model=str(provider.get("model") or ""),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0.2,
        )
        content = response.choices[0].message.content if response.choices else ""
        return _safe_json_parse(str(content or ""))
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _provider_stream_text(provider: dict[str, Any], system_prompt: str, payload: dict[str, Any]):
    from openai import OpenAI

    timeout_seconds = float(provider.get("timeout") or 10.0)
    started = time.perf_counter()
    first_chunk_logged = False
    client = OpenAI(
        base_url=str(provider.get("base_url") or ""),
        api_key=str(provider.get("api_key") or ""),
        timeout=httpx.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds), read=timeout_seconds, write=min(3.0, timeout_seconds)),
        max_retries=0,
        http_client=httpx.Client(
            timeout=httpx.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds), read=timeout_seconds, write=min(3.0, timeout_seconds)),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        ),
    )
    try:
        stream = client.chat.completions.create(
            model=str(provider.get("model") or ""),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0.2,
            stream=True,
        )
        for event in stream:
            if not getattr(event, "choices", None):
                continue
            delta = getattr(event.choices[0], "delta", None)
            content = getattr(delta, "content", None) if delta is not None else None
            if content:
                if not first_chunk_logged:
                    first_chunk_logged = True
                    _LOGGER.info(
                        "assistant provider first text chunk query=%r elapsed_ms=%.1f",
                        str(payload.get("query") or "")[:120],
                        (time.perf_counter() - started) * 1000,
                    )
                yield str(content)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def synthesize_network_diagnosis(query: str, evidence: dict[str, Any], provider: dict[str, Any]) -> dict[str, Any]:
    system_prompt = (
        "You are an OpenStack operations assistant. "
        "Answer only from the provided evidence. "
        "Return JSON with keys: answer, findings, recommendations, confidence. "
        "findings and recommendations must be short arrays of strings. "
        "If the evidence is limited, say so explicitly in answer."
    )
    return _normalize_provider_result(
        _provider_chat(provider, system_prompt, {"query": query, "evidence": evidence}),
        0.6,
    )


def answer_platform_knowledge(query: str, provider: dict[str, Any]) -> dict[str, Any]:
    system_prompt = (
        "You are an OpenStack assistant. "
        "Answer as general OpenStack knowledge, not as environment-specific fact. "
        "Return JSON with keys: answer, confidence. "
        "Format answer for readability: start with a short summary paragraph, then use short bullet points or numbered steps when helpful. "
        "For how-to questions, prefer a brief 'Steps' style answer. "
        "For definition questions, prefer a short definition followed by 2-4 concise bullets. "
        "Do not use markdown headers, but plain bullets and short paragraphs are encouraged."
    )
    return _normalize_provider_result(
        _provider_chat(provider, system_prompt, {"query": query}),
        0.45,
    )


def stream_platform_knowledge(query: str, provider: dict[str, Any]):
    system_prompt = (
        "You are an OpenStack assistant. "
        "Answer as general OpenStack knowledge, not as environment-specific fact. "
        "Respond as plain text only, not JSON. "
        "Start with a short summary paragraph, then use short bullet points or numbered steps when helpful. "
        "For how-to questions, prefer a brief steps-oriented answer. "
        "For definition questions, prefer a short definition followed by 2-4 concise bullets. "
        "Do not use markdown headers."
    )
    yield from _provider_stream_text(provider, system_prompt, {"query": query})


def answer_domain_chat(query: str, provider: dict[str, Any]) -> dict[str, Any]:
    system_prompt = (
        "You are an infrastructure-focused assistant. "
        "Only answer questions about cloud platforms, OpenStack, Kubernetes, Rackspace, Linux, Windows administration, GPUs, servers, networking, storage, virtualization, datacenter operations, or related platform engineering topics. "
        "If the question is outside those domains, return JSON with a short refusal answer explaining that you are scoped to infrastructure/platform topics only. "
        "Return JSON with keys: answer, confidence. "
        "Format answer cleanly with short paragraphs, and use bullets only if they make the answer easier to scan. "
        "Avoid one long dense block of text."
    )
    return _normalize_provider_result(
        _provider_chat(provider, system_prompt, {"query": query}),
        0.35,
    )


def stream_domain_chat(query: str, provider: dict[str, Any]):
    system_prompt = (
        "You are an infrastructure-focused assistant. "
        "Only answer questions about cloud platforms, OpenStack, Kubernetes, Rackspace, Linux, Windows administration, GPUs, servers, networking, storage, virtualization, datacenter operations, or related platform engineering topics. "
        "If the question is outside those domains, respond with a short refusal explaining that you are scoped to infrastructure/platform topics only. "
        "Respond as plain text only, not JSON. "
        "Format answer cleanly with short paragraphs, and use bullets only if they make the answer easier to scan. "
        "Avoid one long dense block of text."
    )
    yield from _provider_stream_text(provider, system_prompt, {"query": query})
