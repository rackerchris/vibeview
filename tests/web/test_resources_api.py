from __future__ import annotations

import time

from fastapi.testclient import TestClient

from draino.web import server as web_server
from draino.web.api import resources as resource_api
from draino.web import assistant_evidence
from draino.web import assistant_cluster_answers
from draino.web import assistant_environment_answers
from draino.web import assistant_followups
from draino.web import assistant_openstack_knowledge
from draino.web import assistant_service
from draino.web import assistant_resource_answers
from draino.web import resource_helpers


def test_networks_endpoint_returns_api_issue_on_neutron_failure(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    class FakeExc(Exception):
        status_code = 504
        request_id = "req-neutron-1"

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(resource_api, "get_networks", lambda auth=None: (_ for _ in ()).throw(FakeExc("upstream request timeout")))

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.get("/api/networks")

    body = resp.json()
    assert resp.status_code == 200
    assert body["api_issue"]["service"] == "Neutron"
    assert body["api_issue"]["status"] == 504
    assert body["api_issue"]["request_id"] == "req-neutron-1"


def test_networks_endpoint_reads_request_id_from_response_headers(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    class FakeResponse:
        headers = {"X-Openstack-Request-Id": "req-neutron-header-1"}

    class FakeExc(Exception):
        status_code = 500
        response = FakeResponse()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(resource_api, "get_networks", lambda auth=None: (_ for _ in ()).throw(FakeExc("neutron internal error")))

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.get("/api/networks")

    body = resp.json()
    assert resp.status_code == 200
    assert body["api_issue"]["request_id"] == "req-neutron-header-1"


def test_repair_metadata_port_endpoint_returns_created_port(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "repair_subnet_metadata_port",
        lambda network_id, subnet_id, auth=None: {
            "port_id": "port-1",
            "ip_address": "10.0.0.2",
            "status": "ok",
        },
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.post("/api/networks/net-1/subnets/subnet-1/repair-metadata-port")

    body = resp.json()
    assert resp.status_code == 200
    assert body["metadata_port"]["status"] == "ok"
    assert body["metadata_port"]["port_id"] == "port-1"


def test_load_balancers_endpoint_returns_items(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "get_load_balancers",
        lambda auth=None: [{
            "id": "lb-1",
            "name": "public-lb",
            "operating_status": "ONLINE",
            "provisioning_status": "ACTIVE",
            "vip_address": "10.10.0.5",
            "floating_ip": "198.51.100.25",
            "vip_port_id": "vip-port-1",
            "project_id": "proj-1",
            "listener_count": 1,
            "pool_count": 1,
            "amphora_count": 2,
        }],
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.get("/api/load-balancers")

    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["load_balancers"][0]["floating_ip"] == "198.51.100.25"
    assert body["load_balancers"][0]["amphora_count"] == 2


def test_assistant_query_endpoint_returns_answer(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "answer_assistant_query",
        lambda query, auth=None, k8s_auth=None, selected_project_id="", selected_project_name="", active_view="", resolved_kind="", resolved_id="", resolved_label="": {
            "intent": "quota_usage",
            "answer": "In project admin, you are using 13 floating IPs out of 50.",
            "confidence": 0.93,
            "needs_clarification": False,
            "actions": [{"type": "open_project_tab", "project_id": selected_project_id, "tab": "quota"}],
        },
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.post("/api/assistant/query", json={
            "query": "how many floating ips am I using?",
            "selected_project_id": "proj-1",
            "selected_project_name": "admin",
            "active_view": "projects",
        })

    body = resp.json()
    assert resp.status_code == 200
    assert body["result"]["intent"] == "quota_usage"
    assert "13 floating IPs" in body["result"]["answer"]


def test_assistant_query_endpoint_forwards_resolved_choice(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    captured: dict[str, str] = {}

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])

    def fake_answer(query, auth=None, k8s_auth=None, selected_project_id="", selected_project_name="", active_view="", resolved_kind="", resolved_id="", resolved_label=""):
        captured["resolved_kind"] = resolved_kind
        captured["resolved_id"] = resolved_id
        captured["resolved_label"] = resolved_label
        return {
            "intent": "instance_status",
            "answer": "james-vm is ACTIVE on host cmp-1.",
            "confidence": 0.97,
            "needs_clarification": False,
            "actions": [],
        }

    monkeypatch.setattr(resource_api, "answer_assistant_query", fake_answer)

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.post("/api/assistant/query", json={
            "query": "what is the status of vm james-vm?",
            "selected_project_id": "proj-1",
            "selected_project_name": "admin",
            "active_view": "projects",
            "resolved_kind": "instance",
            "resolved_id": "vm-1",
            "resolved_label": "james-vm",
        })

    assert resp.status_code == 200
    assert captured == {
        "resolved_kind": "instance",
        "resolved_id": "vm-1",
        "resolved_label": "james-vm",
    }


def test_assistant_query_stream_endpoint_returns_events(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "stream_assistant_query",
        lambda query, auth=None, k8s_auth=None, selected_project_id="", selected_project_name="", active_view="", resolved_kind="", resolved_id="", resolved_label="": iter([
            {"type": "meta", "meta": {"intent": "platform_knowledge", "parser_source": "task-router", "answer_source": "xerotier"}},
            {"type": "delta", "text": "A router connects networks. "},
            {"type": "delta", "text": "It can also provide external gateway access."},
            {"type": "final", "result": {"intent": "platform_knowledge", "answer": "A router connects networks. It can also provide external gateway access.", "confidence": 0.45}},
        ]),
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        with client.stream("POST", "/api/assistant/query/stream", json={"query": "what is a router?"}) as resp:
            assert resp.status_code == 200
            lines = [line for line in resp.iter_lines() if line]

    assert any('"type": "meta"' in line for line in lines)
    assert any('"type": "delta"' in line for line in lines)
    assert any('"type": "final"' in line for line in lines)


def test_assistant_status_endpoint_returns_status(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "assistant_provider_status",
        lambda: {
            "configured": True,
            "base_url": "https://xerotier.example/v1",
            "base_host": "xerotier.example",
            "model": "ops-model",
            "timeout_seconds": 10.0,
        },
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.get("/api/assistant/status")

    body = resp.json()
    assert resp.status_code == 200
    assert body["status"]["configured"] is True
    assert body["status"]["base_host"] == "xerotier.example"


def test_assistant_validate_endpoint_returns_validation(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "validate_assistant_provider",
        lambda: {
            "configured": True,
            "base_url": "https://xerotier.example/v1",
            "base_host": "xerotier.example",
            "model": "ops-model",
            "timeout_seconds": 10.0,
            "ok": True,
            "error": "",
            "latency_ms": 142.5,
            "response_text": "OK",
        },
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.post("/api/assistant/validate", json={})

    body = resp.json()
    assert resp.status_code == 200
    assert body["validation"]["ok"] is True
    assert body["validation"]["response_text"] == "OK"


def test_assistant_service_answers_instance_ips(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "instance_ips",
            "resource_kind": "instance",
            "resource_name": "james-vm",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_service,
        "_resolve_instance",
        lambda query, auth, k8s_auth=None, project_id="": (
            {"id": "vm-1", "label": "james-vm", "project_id": "proj-1", "compute_host": "cmp-1"},
            [{"id": "vm-1", "label": "james-vm", "project_id": "proj-1", "compute_host": "cmp-1"}],
        ),
    )
    monkeypatch.setattr(
        assistant_service,
        "get_project_instances",
        lambda auth=None, project_id="": [{
            "id": "vm-1",
            "name": "james-vm",
            "project_id": "proj-1",
            "project_name": "admin",
            "compute_host": "cmp-1",
            "status": "ACTIVE",
            "fixed_ips": ["10.0.0.5"],
            "floating_ips": ["198.51.100.10"],
            "networks": [{"name": "private"}],
        }],
    )

    result = assistant_service.answer_assistant_query("what ips does vm james-vm have?", auth=None)

    assert result["intent"] == "instance_ips"
    assert "10.0.0.5" in result["answer"]
    assert "198.51.100.10" in result["answer"]


def test_assistant_service_answers_quota_limit(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "quota_limit",
            "resource_kind": "quota",
            "resource_name": "floating_ips",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_service,
        "get_project_quota_summary",
        lambda project_id, auth=None: {
            "compute": {},
            "network": {"floatingip": {"used": 13, "limit": 50}},
            "block_storage": {},
        },
    )

    result = assistant_service.answer_assistant_query("what is my floating ip limit?", auth=None)

    assert result["intent"] == "quota_limit"
    assert "50" in result["answer"]


def test_assistant_service_skips_provider_for_simple_fallback_query(monkeypatch):
    monkeypatch.setattr(assistant_service, "_ASSISTANT_BASE_URL", "https://xerotier.example/v1")
    monkeypatch.setattr(assistant_service, "_ASSISTANT_API_KEY", "token")
    monkeypatch.setattr(assistant_service, "_ASSISTANT_MODEL", "ops-model")

    def fail_openai_import():
        raise AssertionError("provider path should not be used for simple fallback queries")
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_service,
        "get_project_quota_summary",
        lambda project_id, auth=None: {
            "compute": {},
            "network": {"floatingip": {"used": 13, "limit": 50}},
            "block_storage": {},
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_backfill_compute_quota_usage",
        lambda project_id, auth, quotas: quotas,
    )

    import builtins
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openai":
            fail_openai_import()
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    result = assistant_service.answer_assistant_query("how many floating ips do i have?", auth=None)

    assert result["intent"] == "quota_usage"
    assert result["parser_source"] == "fallback"


def test_assistant_service_answers_security_group_lockdown(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "security_group_lockdown_suggestions",
            "resource_kind": "security_group",
            "resource_name": "web-tier",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_service,
        "_resolve_security_group",
        lambda query, auth, k8s_auth=None, project_id="": (
            {"id": "sg-1", "label": "web-tier", "project_id": "proj-1"},
            [{"id": "sg-1", "label": "web-tier", "project_id": "proj-1"}],
        ),
    )
    monkeypatch.setattr(
        assistant_service,
        "get_security_group_detail",
        lambda group_id, auth=None: {
            "id": "sg-1",
            "name": "web-tier",
            "project_id": "proj-1",
            "project_name": "admin",
            "rule_count": 3,
            "attachment_port_count": 1,
            "audit": {
                "severity": "high",
                "findings": [{"summary": "tcp:22 0.0.0.0/0"}],
            },
            "rules": [{
                "audit": {
                    "category": "public-admin-port",
                },
            }],
        },
    )

    result = assistant_service.answer_assistant_query("audit security group web-tier and suggest how to lock it down", auth=None)

    assert result["intent"] == "security_group_lockdown_suggestions"
    assert "Recommended next steps" in result["answer"]
    assert result["recommendations"]


def test_assistant_service_returns_instance_choices_when_ambiguous(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "instance_status",
            "resource_kind": "instance",
            "resource_name": "james-vm",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_service,
        "_resolve_instance",
        lambda query, auth, k8s_auth=None, project_id="": (
            None,
            [
                {"id": "vm-1", "label": "james-vm", "project_id": "proj-1", "project_name": "admin", "compute_host": "cmp-1"},
                {"id": "vm-2", "label": "james-vm", "project_id": "proj-2", "project_name": "prod", "compute_host": "cmp-2"},
            ],
        ),
    )

    result = assistant_service.answer_assistant_query("what is the status of vm james-vm?", auth=None)

    assert result["needs_clarification"] is True
    assert result["clarifying_question"] == "Which instance did you mean?"
    assert len(result["choices"]) == 2
    assert result["choices"][0]["kind"] == "instance"
    assert "cmp-1" in result["choices"][0]["subtext"]


def test_assistant_service_returns_security_group_choices_when_ambiguous(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "security_group_audit",
            "resource_kind": "security_group",
            "resource_name": "web-tier",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_service,
        "_resolve_security_group",
        lambda query, auth, k8s_auth=None, project_id="": (
            None,
            [
                {"id": "sg-1", "label": "web-tier", "project_id": "proj-1", "project_name": "admin"},
                {"id": "sg-2", "label": "web-tier", "project_id": "proj-2", "project_name": "prod"},
            ],
        ),
    )

    result = assistant_service.answer_assistant_query("audit security group web-tier", auth=None)

    assert result["needs_clarification"] is True
    assert result["clarifying_question"] == "Which security group did you mean?"
    assert len(result["choices"]) == 2
    assert result["choices"][0]["kind"] == "securitygroup"


def test_assistant_service_resolves_project_hint_for_quota(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "quota_usage",
            "resource_kind": "quota",
            "resource_name": "cores",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "", "project_name": ""},
    )
    monkeypatch.setattr(
        assistant_service,
        "_resolve_project",
        lambda query, auth, k8s_auth=None: (
            {"id": "proj-2", "label": "prod"},
            [{"id": "proj-2", "label": "prod"}],
        ),
    )
    monkeypatch.setattr(
        assistant_service,
        "get_project_quota_summary",
        lambda project_id, auth=None: {
            "compute": {"cores": {"used": 24, "limit": 64}},
            "network": {},
            "block_storage": {},
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_backfill_compute_quota_usage",
        lambda project_id, auth, quotas: quotas,
    )

    result = assistant_service.answer_assistant_query("how many cores is project prod using?", auth=None)

    assert result["intent"] == "quota_usage"
    assert "project prod" in result["answer"].lower()
    assert result["scope"]["project_id"] == "proj-2"
    assert result["actions"][0]["label"] == "Open Quota"


def test_assistant_service_returns_project_choices_when_ambiguous(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "project_network_summary",
            "resource_kind": "project",
            "resource_name": "",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "", "project_name": ""},
    )
    monkeypatch.setattr(
        assistant_service,
        "_resolve_project",
        lambda query, auth, k8s_auth=None: (
            None,
            [
                {"id": "proj-1", "label": "prod-east"},
                {"id": "proj-2", "label": "prod-west"},
            ],
        ),
    )

    result = assistant_service.answer_assistant_query("summarize networking for project prod", auth=None)

    assert result["needs_clarification"] is True
    assert result["clarifying_question"] == "Which project did you mean?"
    assert len(result["choices"]) == 2
    assert result["choices"][0]["kind"] == "project"


def test_assistant_service_opens_network_resource(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "open_resource",
            "resource_kind": "network",
            "resource_name": "tenant-net",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_service,
        "_resolve_resource",
        lambda kind, query, auth, k8s_auth=None, project_id="": (
            {"id": "net-1", "label": "tenant-net", "project_id": "proj-1", "project_name": "admin"},
            [{"id": "net-1", "label": "tenant-net", "project_id": "proj-1", "project_name": "admin"}],
        ),
    )
    monkeypatch.setattr(
        assistant_service,
        "get_network_detail",
        lambda network_id, auth=None: {
            "id": network_id,
            "name": "tenant-net",
            "status": "ACTIVE",
            "project_name": "admin",
            "subnets": [{"id": "subnet-1"}],
        },
    )

    result = assistant_service.answer_assistant_query("open network tenant-net", auth=None)

    assert result["intent"] == "open_resource"
    assert "tenant-net" in result["answer"]
    assert result["actions"][0]["type"] == "open_network"


def test_assistant_service_returns_port_choices_when_ambiguous(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "open_resource",
            "resource_kind": "port",
            "resource_name": "api-port",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_service,
        "_resolve_resource",
        lambda kind, query, auth, k8s_auth=None, project_id="": (
            None,
            [
                {"id": "port-1", "label": "api-port", "project_id": "proj-1", "project_name": "admin"},
                {"id": "port-2", "label": "api-port", "project_id": "proj-1", "project_name": "admin"},
            ],
        ),
    )

    result = assistant_service.answer_assistant_query("open port api-port", auth=None)

    assert result["needs_clarification"] is True
    assert result["clarifying_question"] == "Which port did you mean?"
    assert len(result["choices"]) == 2
    assert result["choices"][0]["kind"] == "port"


def test_assistant_service_opens_floating_ip_family(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "open_resource",
            "resource_kind": "floatingip",
            "resource_name": "198.51.100.10",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_service,
        "_resolve_resource",
        lambda kind, query, auth, k8s_auth=None, project_id="": (
            {"id": "fip-1", "label": "198.51.100.10", "project_id": "proj-1", "project_name": "admin"},
            [{"id": "fip-1", "label": "198.51.100.10", "project_id": "proj-1", "project_name": "admin"}],
        ),
    )
    monkeypatch.setattr(
        assistant_service,
        "get_floating_ips",
        lambda auth=None, project_id="": [{
            "id": "fip-1",
            "floating_ip_address": "198.51.100.10",
            "status": "DOWN",
            "instance_name": "",
            "fixed_ip_address": "",
            "project_id": "proj-1",
        }],
    )

    result = assistant_service.answer_assistant_query("open floating ip 198.51.100.10", auth=None)

    assert result["intent"] == "open_resource"
    assert result["actions"][0]["type"] == "open_project_network_family"
    assert result["actions"][0]["family"] == "floatingips"


def test_assistant_service_answers_port_attachment_status(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "port_attachment_status",
            "resource_kind": "port",
            "resource_name": "api-port",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "resolve_assistant_resource",
        lambda kind, query, auth, project_id="": (
            {"id": "port-1", "label": "api-port", "project_id": "proj-1", "project_name": "admin", "compute_host": "cmp-1"},
            [{"id": "port-1", "label": "api-port", "project_id": "proj-1", "project_name": "admin", "compute_host": "cmp-1"}],
        ),
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "get_port_detail",
        lambda port_id, auth=None: {
            "id": port_id,
            "name": "api-port",
            "project_id": "proj-1",
            "network_name": "tenant-net",
            "attached_kind": "instance",
            "attached_id": "vm-1",
            "attached_name": "api-01",
            "compute_host": "cmp-1",
        },
    )

    result = assistant_service.answer_assistant_query("is port api-port attached to anything?", auth=None)

    assert result["intent"] == "port_attachment_status"
    assert "attached to instance api-01" in result["answer"]
    assert any(action["type"] == "open_port" for action in result["actions"])
    assert any(action["type"] == "open_instance" for action in result["actions"])


def test_assistant_service_answers_router_external_status(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "router_external_status",
            "resource_kind": "router",
            "resource_name": "edge-router",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "resolve_assistant_resource",
        lambda kind, query, auth, project_id="": (
            {"id": "router-1", "label": "edge-router", "project_id": "proj-1"},
            [{"id": "router-1", "label": "edge-router", "project_id": "proj-1"}],
        ),
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "get_router_detail",
        lambda router_id, auth=None: {
            "id": router_id,
            "name": "edge-router",
            "project_id": "proj-1",
            "external_gateway": {"network_id": "net-ext", "network_name": "external-net", "enable_snat": True},
        },
    )

    result = assistant_service.answer_assistant_query("is router edge-router external?", auth=None)

    assert result["intent"] == "router_external_status"
    assert "external via external-net" in result["answer"]
    assert any(action["type"] == "open_router" for action in result["actions"])


def test_assistant_service_answers_volume_attachment_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "volume_attachment_summary",
            "resource_kind": "volume",
            "resource_name": "db-volume",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "resolve_assistant_resource",
        lambda kind, query, auth, project_id="": (
            {"id": "vol-1", "label": "db-volume", "project_id": "proj-1"},
            [{"id": "vol-1", "label": "db-volume", "project_id": "proj-1"}],
        ),
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "get_volume_detail",
        lambda volume_id, auth=None: {
            "id": volume_id,
            "name": "db-volume",
            "project_id": "proj-1",
            "attachments": [{"server_id": "vm-1", "server_name": "db-01"}],
        },
    )

    result = assistant_service.answer_assistant_query("what is volume db-volume attached to?", auth=None)

    assert result["intent"] == "volume_attachment_summary"
    assert "attached to db-01" in result["answer"]
    assert result["actions"][0]["type"] == "open_volume"


def test_assistant_service_answers_floating_ip_instance_status(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "floating_ip_instance_status",
            "resource_kind": "floatingip",
            "resource_name": "198.51.100.10",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "resolve_assistant_resource",
        lambda kind, query, auth, project_id="": (
            {"id": "fip-1", "label": "198.51.100.10", "project_id": "proj-1", "project_name": "admin"},
            [{"id": "fip-1", "label": "198.51.100.10", "project_id": "proj-1", "project_name": "admin"}],
        ),
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "get_floating_ips",
        lambda auth=None, project_id="": [{
            "id": "fip-1",
            "floating_ip_address": "198.51.100.10",
            "project_id": "proj-1",
            "project_name": "admin",
            "instance_id": "vm-1",
            "instance_name": "api-01",
            "compute_host": "cmp-1",
        }],
    )

    result = assistant_service.answer_assistant_query("does floating ip 198.51.100.10 belong to a vm?", auth=None)

    assert result["intent"] == "floating_ip_instance_status"
    assert "belongs to VM api-01" in result["answer"]
    assert any(action["type"] == "open_instance" for action in result["actions"])


def test_assistant_service_answers_port_public_exposure(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "port_public_exposure",
            "resource_kind": "port",
            "resource_name": "api-port",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "resolve_assistant_resource",
        lambda kind, query, auth, project_id="": (
            {"id": "port-1", "label": "api-port", "project_id": "proj-1"},
            [{"id": "port-1", "label": "api-port", "project_id": "proj-1"}],
        ),
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "get_port_detail",
        lambda port_id, auth=None: {
            "id": port_id,
            "name": "api-port",
            "project_id": "proj-1",
            "floating_ips": [{"address": "198.51.100.10"}],
            "security_groups": [{"id": "sg-1", "name": "web-open"}],
        },
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "get_security_group_detail",
        lambda group_id, auth=None: {"audit": {"severity": "high"}},
    )

    result = assistant_service.answer_assistant_query("is port api-port exposed publicly?", auth=None)

    assert result["intent"] == "port_public_exposure"
    assert "publicly exposed" in result["answer"]
    assert any(action["type"] == "open_networking_security_group" for action in result["actions"])


def test_assistant_service_answers_router_connectivity_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "router_connectivity_summary",
            "resource_kind": "router",
            "resource_name": "edge-router",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "resolve_assistant_resource",
        lambda kind, query, auth, project_id="": (
            {"id": "router-1", "label": "edge-router", "project_id": "proj-1"},
            [{"id": "router-1", "label": "edge-router", "project_id": "proj-1"}],
        ),
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "get_router_detail",
        lambda router_id, auth=None: {
            "id": router_id,
            "name": "edge-router",
            "project_id": "proj-1",
            "joins": {"attached_network_count": 10, "routed_instance_count": 22, "routed_load_balancer_count": 6},
        },
    )

    result = assistant_service.answer_assistant_query("is router edge-router highly connected?", auth=None)

    assert result["intent"] == "router_connectivity_summary"
    assert "fairly central" in result["answer"]


def test_assistant_service_answers_volume_posture(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "volume_posture",
            "resource_kind": "volume",
            "resource_name": "db-volume",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "resolve_assistant_resource",
        lambda kind, query, auth, project_id="": (
            {"id": "vol-1", "label": "db-volume", "project_id": "proj-1"},
            [{"id": "vol-1", "label": "db-volume", "project_id": "proj-1"}],
        ),
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "get_volume_detail",
        lambda volume_id, auth=None: {
            "id": volume_id,
            "name": "db-volume",
            "project_id": "proj-1",
            "bootable": False,
            "encrypted": True,
            "multiattach": False,
        },
    )

    result = assistant_service.answer_assistant_query("is volume db-volume encrypted?", auth=None)

    assert result["intent"] == "volume_posture"
    assert "not bootable, encrypted, and not multiattach" in result["answer"]


def test_assistant_service_answers_floating_ip_idle_status(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "floating_ip_idle_status",
            "resource_kind": "floatingip",
            "resource_name": "198.51.100.10",
            "needs_clarification": False,
            "parser_source": "fallback",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "resolve_assistant_resource",
        lambda kind, query, auth, project_id="": (
            {"id": "fip-1", "label": "198.51.100.10", "project_id": "proj-1", "project_name": "admin"},
            [{"id": "fip-1", "label": "198.51.100.10", "project_id": "proj-1", "project_name": "admin"}],
        ),
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "get_floating_ips",
        lambda auth=None, project_id="": [{
            "id": "fip-1",
            "floating_ip_address": "198.51.100.10",
            "project_id": "proj-1",
            "project_name": "admin",
            "instance_id": "",
            "port_id": "",
            "fixed_ip_address": "",
        }],
    )

    result = assistant_service.answer_assistant_query("is floating ip 198.51.100.10 idle?", auth=None)

    assert result["intent"] == "floating_ip_idle_status"
    assert "appears idle" in result["answer"]


def test_assistant_service_looks_up_floating_ip_by_fixed_ip(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_resource_answers,
        "get_floating_ips",
        lambda auth=None, project_id="": [{
            "id": "fip-1",
            "floating_ip_address": "198.51.100.10",
            "fixed_ip_address": "10.30.1.188",
            "project_id": "proj-1",
            "project_name": "admin",
            "instance_id": "vm-1",
            "instance_name": "api-01",
            "compute_host": "cmp-1",
        }],
    )

    result = assistant_service.answer_assistant_query("whats the floating ip for 10.30.1.188", auth=None)

    assert result["intent"] == "floating_ip_lookup_by_fixed_ip"
    assert "198.51.100.10" in result["answer"]
    assert result["parser_source"] == "fallback"


def test_assistant_service_answers_etcd_health_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for etcd cluster questions")),
    )
    monkeypatch.setattr(
        assistant_cluster_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "node-1", "hostname": "hv-1"},
            {"name": "node-2", "hostname": "hv-2"},
            {"name": "node-3", "hostname": "hv-3"},
        ],
    )
    monkeypatch.setattr(assistant_cluster_answers.k8s_ops, "get_etcd_node_names", lambda auth=None: {"node-1", "node-2", "node-3"})
    monkeypatch.setattr(
        assistant_cluster_answers.k8s_ops,
        "get_etcd_service_status",
        lambda node_name, hostname=None: {"active": node_name != "node-3", "error": None},
    )

    result = assistant_service.answer_assistant_query("how many etcd nodes are alive?", auth=None, k8s_auth=None)

    assert result["intent"] == "etcd_health_summary"
    assert "3 etcd nodes" in result["answer"]
    assert "2 are currently alive" in result["answer"]
    assert [action["node"] for action in result["actions"]] == ["node-1", "node-2", "node-3"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_mariadb_node_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for mariadb cluster questions")),
    )
    monkeypatch.setattr(
        assistant_cluster_answers.k8s_ops,
        "get_mariadb_node_names",
        lambda auth=None: {"node-2", "node-3"},
    )

    result = assistant_service.answer_assistant_query("how many mariadb nodes do we have?", auth=None, k8s_auth=None)

    assert result["intent"] == "mariadb_node_summary"
    assert "2 Kubernetes nodes currently hosting MariaDB cluster pods" in result["answer"]
    assert [action["node"] for action in result["actions"]] == ["node-2", "node-3"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_cluster_health_overview(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for cluster health overview questions")),
    )
    monkeypatch.setattr(
        assistant_cluster_answers.k8s_ops,
        "get_k8s_node_health_density_summary",
        lambda auth=None: {
            "nodes": [
                {"name": "node-1", "ready": True},
                {"name": "node-2", "ready": True},
                {"name": "node-3", "ready": False},
            ],
            "condition_counts": {"NotReady": 1, "Cordoned": 1, "MemoryPressure": 2},
        },
    )

    result = assistant_service.answer_assistant_query("is the cluster healthy right now?", auth=None, k8s_auth=None)

    assert result["intent"] == "cluster_health_overview"
    assert "2 of 3 Kubernetes nodes are Ready" in result["answer"]
    assert "NotReady" in " ".join(result.get("findings") or [])
    assert result["actions"][0]["node"] == "node-3"
    assert result["answer_source"] == "local"


def test_assistant_service_answers_node_ready_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for cluster node questions")),
    )
    monkeypatch.setattr(
        assistant_cluster_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "node-1", "ready": True, "cordoned": False},
            {"name": "node-2", "ready": True, "cordoned": True},
            {"name": "node-3", "ready": False, "cordoned": False},
        ],
    )

    result = assistant_service.answer_assistant_query("how many kubernetes nodes are ready?", auth=None, k8s_auth=None)

    assert result["intent"] == "node_ready_summary"
    assert "2 of 3 Kubernetes nodes are Ready" in result["answer"]
    assert "1 are currently cordoned" in result["answer"]
    assert [action["node"] for action in result["actions"]] == ["node-2", "node-3"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_apiserver_health_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for control-plane questions")),
    )
    monkeypatch.setattr(
        assistant_cluster_answers.k8s_ops,
        "list_k8s_pods",
        lambda namespace=None, auth=None: [
            {"name": "kube-apiserver-cp-1", "phase": "Running", "ready": "1/1"},
            {"name": "kube-apiserver-cp-2", "phase": "Running", "ready": "1/1"},
            {"name": "kube-apiserver-cp-3", "phase": "Pending", "ready": "0/1"},
        ],
    )

    result = assistant_service.answer_assistant_query("how many api servers are healthy?", auth=None, k8s_auth=None)

    assert result["intent"] == "control_plane_component_summary"
    assert "2 of 3 API server pods are healthy" in result["answer"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_kernel_version_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for environment kernel questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "node-1", "hostname": "hv-1", "kernel_version": "5.14.0-500"},
            {"name": "node-2", "hostname": "hv-2", "kernel_version": "5.14.0-510"},
            {"name": "node-3", "hostname": "hv-3", "kernel_version": "5.14.0-510"},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: {
            "kernel_version": "5.14.0-500" if node_name == "node-1" else "5.14.0-510",
            "latest_kernel_version": "5.14.0-510",
            "reboot_required": node_name == "node-1",
        },
    )

    result = assistant_service.answer_assistant_query("what is the most recent kernel in this environment?", auth=None, k8s_auth=None)

    assert result["intent"] == "kernel_version_summary"
    assert "`5.14.0-510`" in result["answer"]
    assert "Nodes behind latest kernel" in " ".join(result.get("findings") or [])
    assert result["answer_source"] == "local"


def test_assistant_service_answers_missing_node_agent_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for node-agent summary questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "node-1"},
            {"name": "node-2"},
            {"name": "node-3"},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.node_agent_client,
        "get_ready_node_names",
        lambda: {"node-1"},
    )

    result = assistant_service.answer_assistant_query("which nodes are missing the node agent?", auth=None, k8s_auth=None)

    assert result["intent"] == "missing_node_agent_summary"
    assert "Nodes missing a ready node agent: 2" in result["answer"]
    assert "- node-2" in result["answer"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_reboot_needed_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for reboot summary questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_all_host_summaries",
        lambda auth=None: {
            "cmp-1": {"vm_count": 4, "compute_status": "up"},
            "cmp-2": {"vm_count": 0, "compute_status": "up"},
        },
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: {
            "kernel_version": "5.14.0-500",
            "latest_kernel_version": "5.14.0-510",
            "reboot_required": node_name == "cmp-1",
        },
    )

    result = assistant_service.answer_assistant_query("which nodes need to be rebooted?", auth=None, k8s_auth=None)

    assert result["intent"] == "reboot_needed_summary"
    assert "Compute nodes that appear to need reboot: 1" in result["answer"]
    assert "- cmp-1" in result["answer"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_empty_compute_nodes_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for empty compute summary questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
            {"name": "net-1", "hostname": "net-1", "is_compute": False},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_all_host_summaries",
        lambda auth=None: {
            "cmp-1": {"vm_count": 3, "compute_status": "up"},
            "cmp-2": {"vm_count": 0, "compute_status": "up"},
        },
    )

    result = assistant_service.answer_assistant_query("which nodes have no running vms?", auth=None, k8s_auth=None)

    assert result["intent"] == "empty_compute_nodes_summary"
    assert "Compute nodes with no running VMs: 1" in result["answer"]
    assert "- cmp-2" in result["answer"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_busiest_hypervisor_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for hypervisor load summary questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
            {"name": "cmp-3", "hostname": "cmp-3", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_all_host_summaries",
        lambda auth=None: {
            "cmp-1": {"vm_count": 11, "compute_status": "up"},
            "cmp-2": {"vm_count": 27, "compute_status": "up"},
            "cmp-3": {"vm_count": 6, "compute_status": "up"},
        },
    )

    result = assistant_service.answer_assistant_query("what is the busiest hypervisor in this environment?", auth=None, k8s_auth=None)

    assert result["intent"] == "busiest_hypervisor_summary"
    assert "`cmp-2`" in result["answer"]
    assert "`27`" in result["answer"]
    assert "Top hypervisors by running VMs" in " ".join(result.get("findings") or [])
    assert result["actions"][0]["type"] == "open_node"
    assert result["actions"][0]["node"] == "cmp-2"
    assert result["answer_source"] == "local"


def test_assistant_service_answers_emptiest_hypervisor_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for emptiest hypervisor summary questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
            {"name": "cmp-3", "hostname": "cmp-3", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_all_host_summaries",
        lambda auth=None: {
            "cmp-1": {"vm_count": 11, "compute_status": "up"},
            "cmp-2": {"vm_count": 2, "compute_status": "up"},
            "cmp-3": {"vm_count": 6, "compute_status": "up"},
        },
    )

    result = assistant_service.answer_assistant_query("which nodes are emptiest?", auth=None, k8s_auth=None)

    assert result["intent"] == "emptiest_hypervisor_summary"
    assert "`cmp-2`" in result["answer"]
    assert "`2`" in result["answer"]
    assert "Least-loaded hypervisors by running VMs" in " ".join(result.get("findings") or [])
    assert result["answer_source"] == "local"


def test_assistant_service_answers_highest_ram_hypervisor_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for hypervisor RAM summary questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_hypervisor_detail",
        lambda hypervisor, auth=None: {
            "cmp-1": {"memory_mb_used": 65536, "memory_mb": 131072, "running_vms": 11},
            "cmp-2": {"memory_mb_used": 98304, "memory_mb": 131072, "running_vms": 9},
        }[hypervisor],
    )

    result = assistant_service.answer_assistant_query("what hypervisor is using the most ram?", auth=None, k8s_auth=None)

    assert result["intent"] == "highest_ram_hypervisor_summary"
    assert "`cmp-2`" in result["answer"]
    assert "`96.0 GiB`" in result["answer"]
    assert "Top hypervisors by RAM in use" in " ".join(result.get("findings") or [])
    assert result["answer_source"] == "local"


def test_assistant_service_answers_highest_cpu_hypervisor_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for hypervisor CPU summary questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_hypervisor_detail",
        lambda hypervisor, auth=None: {
            "cmp-1": {"vcpus_used": 24, "vcpus": 64, "running_vms": 11},
            "cmp-2": {"vcpus_used": 37, "vcpus": 64, "running_vms": 9},
        }[hypervisor],
    )

    result = assistant_service.answer_assistant_query("which hypervisor is using the most cpu?", auth=None, k8s_auth=None)

    assert result["intent"] == "highest_cpu_hypervisor_summary"
    assert "`cmp-2`" in result["answer"]
    assert "`37 vCPUs in use`" in result["answer"]
    assert "Top hypervisors by vCPU use" in " ".join(result.get("findings") or [])
    assert result["answer_source"] == "local"


def test_assistant_service_answers_highest_disk_hypervisor_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for hypervisor disk summary questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_hypervisor_detail",
        lambda hypervisor, auth=None: {
            "cmp-1": {"local_disk_gb_used": 880, "local_disk_gb": 1200, "running_vms": 11},
            "cmp-2": {"local_disk_gb_used": 640, "local_disk_gb": 1200, "running_vms": 9},
        }[hypervisor],
    )

    result = assistant_service.answer_assistant_query("which hypervisor is using the most disk?", auth=None, k8s_auth=None)

    assert result["intent"] == "highest_disk_hypervisor_summary"
    assert "`cmp-1`" in result["answer"]
    assert "`880 GB local disk in use`" in result["answer"]
    assert "Top hypervisors by local disk use" in " ".join(result.get("findings") or [])
    assert result["answer_source"] == "local"


def test_assistant_service_uses_provider_extraction_for_freeform_environment_query(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for provider-extracted environment questions")),
    )
    monkeypatch.setattr(assistant_service, "assistant_provider_enabled", lambda: True)
    monkeypatch.setattr(
        assistant_service,
        "_parse_environment_query_with_provider",
        lambda query, fallback=None: {
            "intent": "highest_ram_hypervisor_summary",
            "resource_kind": "environment",
            "resource_name": "hypervisor_ram",
            "needs_clarification": False,
            "parser_source": "xerotier",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "answer_environment_summary",
        lambda intent, auth=None, k8s_auth=None: {
            "intent": "highest_ram_hypervisor_summary",
            "answer": "The hypervisor using the most RAM is `cmp-2` with `96.0 GiB` in use.",
            "confidence": 0.88,
            "entities": [],
            "actions": [],
            "needs_clarification": False,
            "findings": ["Top hypervisors by RAM in use\n- cmp-2 (96.0 GiB)\n- cmp-1 (64.0 GiB)"],
            "answer_source": "local",
            "mode": "grounded_environment_summary",
            "provenance": "Grounded Kubernetes and OpenStack data",
        },
    )

    result = assistant_service.answer_assistant_query("which box is carrying the most memory right now?", auth=None, k8s_auth=None)

    assert result["intent"] == "highest_ram_hypervisor_summary"
    assert result["parser_source"] == "xerotier"
    assert result["answer_source"] == "local"


def test_fallback_parser_requests_metric_clarification_for_busiest_node():
    parsed = assistant_service._parse_query_fallback("what is the busiest node?")

    assert parsed["intent"] == "unknown"
    assert parsed["needs_clarification"] is True
    assert parsed["clarifying_question"] == "What metric do you want to rank hypervisors by?"
    assert len(parsed["choices"]) == 5
    assert parsed["choices"][0]["kind"] == "environment_metric"
    assert parsed["choices"][0]["id"] == "busiest_hypervisor_summary"


def test_assistant_service_resolves_environment_metric_choice(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for environment metric choice questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_hypervisor_detail",
        lambda hypervisor, auth=None: {
            "cmp-1": {"vcpus_used": 24, "vcpus": 64, "running_vms": 11},
            "cmp-2": {"vcpus_used": 37, "vcpus": 64, "running_vms": 9},
        }[hypervisor],
    )

    result = assistant_service.answer_assistant_query(
        "what is the busiest node?",
        auth=None,
        k8s_auth=None,
        resolved_kind="environment_metric",
        resolved_id="highest_cpu_hypervisor_summary",
        resolved_label="CPU usage",
    )

    assert result["intent"] == "highest_cpu_hypervisor_summary"
    assert result["parser_source"] == "choice"
    assert result["answer_source"] == "local"
    assert "`cmp-2`" in result["answer"]
    assert "`37 vCPUs in use`" in result["answer"]


def test_assistant_service_resolves_environment_metric_choice_to_emptiest(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for environment metric choice questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_all_host_summaries",
        lambda auth=None: {
            "cmp-1": {"vm_count": 9, "compute_status": "up"},
            "cmp-2": {"vm_count": 1, "compute_status": "up"},
        },
    )

    result = assistant_service.answer_assistant_query(
        "what is the least busy node?",
        auth=None,
        k8s_auth=None,
        resolved_kind="environment_metric",
        resolved_id="emptiest_hypervisor_summary",
        resolved_label="Fewest VMs",
    )

    assert result["intent"] == "emptiest_hypervisor_summary"
    assert result["parser_source"] == "choice"
    assert result["answer_source"] == "local"
    assert "`cmp-2`" in result["answer"]
    assert "`1`" in result["answer"]


def test_assistant_service_answers_top_n_busiest_hypervisors(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for top-n hypervisor summary questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
            {"name": "cmp-3", "hostname": "cmp-3", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_all_host_summaries",
        lambda auth=None: {
            "cmp-1": {"vm_count": 11, "compute_status": "up"},
            "cmp-2": {"vm_count": 27, "compute_status": "up"},
            "cmp-3": {"vm_count": 6, "compute_status": "up"},
        },
    )

    result = assistant_service.answer_assistant_query("top 2 busiest compute nodes", auth=None, k8s_auth=None)

    assert result["intent"] == "hypervisor_metric_top_n"
    assert "Top 2 hypervisors by VM load" in result["answer"]
    assert "- `cmp-2`: `27`" in result["answer"]
    assert "- `cmp-1`: `11`" in result["answer"]
    assert [action["node"] for action in result["actions"]] == ["cmp-2", "cmp-1"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_kernel_drift_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for kernel drift questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "kernel_version": "5.14.0-500"},
            {"name": "cmp-2", "hostname": "cmp-2", "kernel_version": "5.14.0-510"},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: {
            "kernel_version": "5.14.0-500" if node_name == "cmp-1" else "5.14.0-510",
            "latest_kernel_version": "5.14.0-510",
            "reboot_required": node_name == "cmp-1",
        },
    )

    result = assistant_service.answer_assistant_query("which nodes are behind on kernel updates?", auth=None, k8s_auth=None)

    assert result["intent"] == "kernel_drift_summary"
    assert "Nodes behind the latest observed kernel: 1" in result["answer"]
    assert "`cmp-1`" in result["answer"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_kubelet_drift_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for kubelet drift questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "kubelet_version": "v1.30.1"},
            {"name": "cmp-2", "hostname": "cmp-2", "kubelet_version": "v1.30.3"},
            {"name": "cmp-3", "hostname": "cmp-3", "kubelet_version": "v1.30.3"},
        ],
    )

    result = assistant_service.answer_assistant_query("which nodes are behind on kubelet versions?", auth=None, k8s_auth=None)

    assert result["intent"] == "kubelet_drift_summary"
    assert "Nodes behind the latest observed kubelet version: 1" in result["answer"]
    assert "`cmp-1`" in result["answer"]
    assert "`v1.30.1`" in result["answer"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_os_image_drift_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for os image drift questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "os_image": "Ubuntu 22.04.4 LTS"},
            {"name": "cmp-2", "hostname": "cmp-2", "os_image": "Ubuntu 22.04.4 LTS"},
            {"name": "cmp-3", "hostname": "cmp-3", "os_image": "Ubuntu 24.04 LTS"},
        ],
    )

    result = assistant_service.answer_assistant_query("do we have os image drift across the kubernetes nodes?", auth=None, k8s_auth=None)

    assert result["intent"] == "os_image_drift_summary"
    assert "Nodes on a non-majority OS image: 1" in result["answer"]
    assert "`cmp-3`" in result["answer"]
    assert "Ubuntu 24.04 LTS" in result["answer"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_container_runtime_drift_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for container runtime drift questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "container_runtime": "containerd://1.7.20"},
            {"name": "cmp-2", "hostname": "cmp-2", "container_runtime": "containerd://1.7.20"},
            {"name": "cmp-3", "hostname": "cmp-3", "container_runtime": "containerd://2.0.0"},
        ],
    )

    result = assistant_service.answer_assistant_query("do we have container runtime version drift?", auth=None, k8s_auth=None)

    assert result["intent"] == "container_runtime_drift_summary"
    assert "Nodes on a non-majority container runtime: 1" in result["answer"]
    assert "`cmp-3`" in result["answer"]
    assert "containerd://2.0.0" in result["answer"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_control_plane_runtime_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for control plane runtime questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "list_k8s_pods",
        lambda namespace, auth=None: [
            {"name": "kube-apiserver-cp-1", "phase": "Running", "ready": "1/1"},
            {"name": "kube-scheduler-cp-1", "phase": "Running", "ready": "1/1"},
            {"name": "kube-controller-manager-cp-1", "phase": "Running", "ready": "0/1"},
            {"name": "etcd-cp-1", "phase": "Pending", "ready": "0/1"},
        ],
    )

    result = assistant_service.answer_assistant_query("is the control plane healthy right now?", auth=None, k8s_auth=None)

    assert result["intent"] == "control_plane_runtime_summary"
    assert "2 of 4 tracked control-plane pods are fully ready" in result["answer"]
    assert "`1` API server" in result["answer"]
    assert "`1` scheduler" in result["answer"]
    assert "`1` controller-manager" in result["answer"]
    assert "`1` etcd" in result["answer"]
    assert any("kube-controller-manager-cp-1" in item for item in (result.get("findings") or []))
    assert result["answer_source"] == "local"


def test_assistant_service_answers_pod_density_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for pod density questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_k8s_node_health_density_summary",
        lambda auth=None: {
            "nodes": [
                {"node": "cmp-1", "pod_count": 120, "pods_allocatable": 200, "pvc_pod_count": 8, "pvc_claim_count": 10, "namespace_count": 12, "ready": True},
                {"node": "cmp-2", "pod_count": 140, "pods_allocatable": 160, "pvc_pod_count": 4, "pvc_claim_count": 5, "namespace_count": 10, "namespace_names": ["api", "ingress", "metrics"], "ready": True},
                {"node": "cmp-3", "pod_count": 60, "pods_allocatable": 160, "pvc_pod_count": 14, "pvc_claim_count": 18, "namespace_count": 7, "ready": True},
            ],
            "error": None,
        },
    )

    result = assistant_service.answer_assistant_query("which kubernetes node has the highest pod density?", auth=None, k8s_auth=None)

    assert result["intent"] == "pod_density_summary"
    assert "`cmp-2`" in result["answer"]
    assert "`87.5%`" in result["answer"]
    assert "`140`" in result["answer"]
    assert "`10` namespaces" in result["answer"]
    assert result["actions"][0]["node"] == "cmp-2"
    assert result["answer_source"] == "local"


def test_assistant_service_answers_pvc_hotspot_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for pvc hotspot questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_k8s_node_health_density_summary",
        lambda auth=None: {
            "nodes": [
                {"node": "cmp-1", "pod_count": 120, "pods_allocatable": 200, "pvc_pod_count": 8, "pvc_claim_count": 10, "namespace_count": 12, "ready": True},
                {"node": "cmp-2", "pod_count": 140, "pods_allocatable": 160, "pvc_pod_count": 4, "pvc_claim_count": 5, "namespace_count": 10, "ready": True},
                {"node": "cmp-3", "pod_count": 60, "pods_allocatable": 160, "pvc_pod_count": 14, "pvc_claim_count": 18, "namespace_count": 7, "ready": True},
            ],
            "error": None,
        },
    )

    result = assistant_service.answer_assistant_query("which nodes have the most pvc-backed workloads?", auth=None, k8s_auth=None)

    assert result["intent"] == "pvc_hotspot_summary"
    assert "`cmp-3`" in result["answer"]
    assert "`14`" in result["answer"]
    assert "`18`" in result["answer"]
    assert "`60` running pods" in result["answer"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_cpu_request_load_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for cpu request load questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_k8s_node_health_density_summary",
        lambda auth=None: {
            "nodes": [
                {"node": "cmp-1", "pod_count": 120, "pods_allocatable": 200, "cpu_requests_mcpu": 42000, "cpu_allocatable_mcpu": 64000, "pvc_pod_count": 8, "pvc_claim_count": 10, "namespace_count": 12, "ready": True},
                {"node": "cmp-2", "pod_count": 140, "pods_allocatable": 160, "cpu_requests_mcpu": 58000, "cpu_allocatable_mcpu": 64000, "pvc_pod_count": 4, "pvc_claim_count": 5, "namespace_count": 10, "ready": True},
                {"node": "cmp-3", "pod_count": 60, "pods_allocatable": 160, "cpu_requests_mcpu": 21000, "cpu_allocatable_mcpu": 64000, "pvc_pod_count": 14, "pvc_claim_count": 18, "namespace_count": 7, "ready": True},
            ],
            "error": None,
        },
    )

    result = assistant_service.answer_assistant_query("which kubernetes node has the highest cpu request load?", auth=None, k8s_auth=None)

    assert result["intent"] == "cpu_request_load_summary"
    assert "`cmp-2`" in result["answer"]
    assert "`90.6%`" in result["answer"]
    assert "`58000 mCPU`" in result["answer"]
    assert "`140` running pods" in result["answer"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_namespace_spread_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for namespace spread questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_k8s_node_health_density_summary",
        lambda auth=None: {
            "nodes": [
                {"node": "cmp-1", "pod_count": 120, "pods_allocatable": 200, "namespace_count": 12, "pvc_pod_count": 8, "pvc_claim_count": 10, "ready": True},
                {"node": "cmp-2", "pod_count": 140, "pods_allocatable": 160, "namespace_count": 18, "pvc_pod_count": 4, "pvc_claim_count": 5, "ready": True},
                {"node": "cmp-3", "pod_count": 60, "pods_allocatable": 160, "namespace_count": 7, "pvc_pod_count": 14, "pvc_claim_count": 18, "ready": True},
            ],
            "error": None,
        },
    )

    result = assistant_service.answer_assistant_query("which kubernetes node is hosting workloads from the most namespaces?", auth=None, k8s_auth=None)

    assert result["intent"] == "namespace_spread_summary"
    assert "`cmp-2`" in result["answer"]
    assert "`18`" in result["answer"]
    assert "`140`" in result["answer"]
    assert "`4` of those pods are PVC-backed" in result["answer"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_memory_request_load_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for memory request load questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_k8s_node_health_density_summary",
        lambda auth=None: {
            "nodes": [
                {"node": "cmp-1", "pod_count": 120, "pods_allocatable": 200, "memory_requests_mib": 131072, "memory_allocatable_mib": 262144, "namespace_count": 12, "ready": True},
                {"node": "cmp-2", "pod_count": 140, "pods_allocatable": 160, "memory_requests_mib": 245760, "memory_allocatable_mib": 262144, "namespace_count": 10, "namespace_names": ["api", "ingress", "metrics"], "ready": True},
                {"node": "cmp-3", "pod_count": 60, "pods_allocatable": 160, "memory_requests_mib": 65536, "memory_allocatable_mib": 262144, "namespace_count": 7, "ready": True},
            ],
            "error": None,
        },
    )

    result = assistant_service.answer_assistant_query("which kubernetes node has the highest memory request load?", auth=None, k8s_auth=None)

    assert result["intent"] == "memory_request_load_summary"
    assert "`cmp-2`" in result["answer"]
    assert "`93.8%`" in result["answer"]
    assert "`245760 MiB`" in result["answer"]
    assert "`140` running pods" in result["answer"]
    assert result["actions"][0]["node"] == "cmp-2"
    assert result["answer_source"] == "local"


def test_assistant_service_answers_not_ready_node_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for not-ready node questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_k8s_node_health_density_summary",
        lambda auth=None: {
            "nodes": [
                {"node": "cmp-1", "pod_count": 120, "pods_allocatable": 200, "ready": True, "cordoned": False, "conditions": []},
                {"node": "cmp-2", "pod_count": 140, "pods_allocatable": 160, "ready": False, "cordoned": False, "conditions": ["MemoryPressure"]},
            ],
            "error": None,
        },
    )

    result = assistant_service.answer_assistant_query("which kubernetes nodes are not ready?", auth=None, k8s_auth=None)

    assert result["intent"] == "not_ready_node_summary"
    assert "Kubernetes nodes that are NotReady: 1" in result["answer"]
    assert "`cmp-2`" in result["answer"]
    assert "MemoryPressure" in result["answer"]
    assert "`140` running pods" in result["answer"]
    assert result["actions"][0]["node"] == "cmp-2"
    assert result["answer_source"] == "local"


def test_fallback_parser_routes_noschedule_nodes_to_grounded_summary():
    parsed = assistant_service._parse_query_fallback("which nodes have the noschedule label?")

    assert parsed["intent"] == "noschedule_node_summary"
    assert parsed["resource_kind"] == "environment"
    assert parsed["resource_name"] == "noschedule_nodes"


def test_assistant_service_answers_noschedule_node_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for noschedule node questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {
                "name": "cmp-1",
                "hostname": "cmp-1",
                "cordoned": False,
                "taints": [{"key": "draino.openstack.org/maintenance", "value": "true", "effect": "NoSchedule"}],
            },
            {
                "name": "cmp-2",
                "hostname": "cmp-2",
                "cordoned": False,
                "taints": [{"key": "node.kubernetes.io/dedicated", "value": "storage", "effect": "NoSchedule"}],
            },
            {
                "name": "cmp-3",
                "hostname": "cmp-3",
                "cordoned": False,
                "taints": [],
            },
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_k8s_node_health_density_summary",
        lambda auth=None: {
            "nodes": [
                {"node": "cmp-1", "pod_count": 120, "pods_allocatable": 200, "namespace_count": 12, "pvc_pod_count": 8, "pvc_claim_count": 10, "ready": True, "cordoned": False, "conditions": []},
                {"node": "cmp-2", "pod_count": 140, "pods_allocatable": 160, "namespace_count": 10, "pvc_pod_count": 4, "pvc_claim_count": 5, "ready": True, "cordoned": False, "conditions": []},
                {"node": "cmp-3", "pod_count": 60, "pods_allocatable": 160, "namespace_count": 7, "pvc_pod_count": 0, "pvc_claim_count": 0, "ready": True, "cordoned": False, "conditions": []},
            ],
            "error": None,
        },
    )

    result = assistant_service.answer_assistant_query("which nodes have the noschedule label?", auth=None, k8s_auth=None)

    assert result["intent"] == "noschedule_node_summary"
    assert "Kubernetes nodes with a `NoSchedule` taint: 2" in result["answer"]
    assert "not a native node label" in result["answer"]
    assert "`cmp-1`" in result["answer"]
    assert "`cmp-2`" in result["answer"]
    assert "managed by VibeView" in result["answer"]
    assert "`120` running pods" in result["answer"]
    assert [action["node"] for action in result["actions"]] == ["cmp-1", "cmp-2"]
    assert result["parser_source"] == "fallback"
    assert result["answer_source"] == "local"


def test_assistant_service_answers_followup_why(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for grounded follow-up questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_k8s_node_health_density_summary",
        lambda auth=None: {
            "nodes": [
                {"node": "cmp-1", "pod_count": 120, "pods_allocatable": 200, "namespace_count": 12, "pvc_pod_count": 8, "pvc_claim_count": 10, "ready": True, "cordoned": False, "conditions": []},
                {"node": "cmp-2", "pod_count": 140, "pods_allocatable": 160, "namespace_count": 10, "pvc_pod_count": 4, "pvc_claim_count": 5, "ready": True, "cordoned": False, "conditions": []},
                {"node": "cmp-3", "pod_count": 60, "pods_allocatable": 160, "namespace_count": 7, "pvc_pod_count": 14, "pvc_claim_count": 18, "ready": True, "cordoned": False, "conditions": []},
            ],
            "error": None,
        },
    )

    result = assistant_service.answer_assistant_query(
        "why?",
        auth=None,
        k8s_auth=None,
        previous_result={
            "intent": "pod_density_summary",
            "mode": "grounded_environment_summary",
            "scope": {"top_node": "cmp-2"},
            "findings": ["Top nodes by pod density\n- cmp-2 (87.5%, 140 pods)\n- cmp-1 (60.0%, 120 pods)"],
            "actions": [{"type": "open_node", "node": "cmp-2", "label": "Open cmp-2"}],
        },
    )

    assert result["intent"] == "followup_why"
    assert result["parser_source"] == "followup"
    assert "Why:" in result["answer"]
    assert "Top nodes by pod density" in result["answer"]
    assert result["actions"][0]["node"] == "cmp-2"


def test_parse_query_with_provider_prefers_provider_for_environment_queries(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "assistant_provider_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_fallback",
        lambda query: {
            "intent": "reboot_needed_summary",
            "resource_kind": "environment",
            "resource_name": "reboot",
            "needs_clarification": False,
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_parse_environment_query_with_provider",
        lambda query, fallback=None: {
            "intent": "reboot_needed_summary",
            "resource_kind": "environment",
            "resource_name": "reboot",
            "needs_clarification": False,
            "parser_source": "xerotier",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_looks_like_environment_summary_query",
        lambda query: True,
    )

    result = assistant_service._parse_query_with_provider(
        "which nodes should be rebooted first?",
        {"selected_project_id": "", "selected_project_name": "", "active_view": "infrastructure"},
    )

    assert result["intent"] == "reboot_needed_summary"
    assert result["parser_source"] == "xerotier"


def test_assistant_service_answers_followup_why_hot(monkeypatch):
    monkeypatch.setattr(
        assistant_followups,
        "answer_environment_summary",
        lambda intent, auth=None, k8s_auth=None: {
            "intent": "memory_request_load_summary",
            "answer": "The Kubernetes node with the highest memory request load is `cmp-2` at `93.8%`.",
            "confidence": 0.86,
            "entities": [{"kind": "environment", "id": "memory_request_load", "label": "memory request load"}],
            "actions": [{"type": "open_node", "node": "cmp-2", "label": "Open cmp-2"}],
            "needs_clarification": False,
            "findings": ["Top nodes by memory request load\n- cmp-2 (93.8%, 245760 MiB)\n- cmp-1 (50.0%, 131072 MiB)"],
            "scope": {"top_node": "cmp-2", "top_memory_req_pct": 93.8},
            "answer_source": "local",
            "mode": "grounded_environment_summary",
            "provenance": "Grounded Kubernetes node density data",
        },
    )
    result = assistant_service.answer_assistant_query(
        "why is cmp-2 hot?",
        auth=None,
        k8s_auth=None,
        previous_result={
            "intent": "memory_request_load_summary",
            "mode": "grounded_environment_summary",
            "scope": {"top_node": "cmp-2"},
            "actions": [{"type": "open_node", "node": "cmp-2", "label": "Open cmp-2"}],
        },
    )

    assert result["intent"] == "followup_why_hot"
    assert result["parser_source"] == "followup"
    assert "`cmp-2` looks hot" in result["answer"]
    assert "Top nodes by memory request load" in result["answer"]


def test_assistant_service_answers_followup_show_top_n(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for grounded follow-up questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
            {"name": "cmp-3", "hostname": "cmp-3", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_hypervisor_detail",
        lambda hypervisor, auth=None: {
            "cmp-1": {"memory_mb_used": 65536, "memory_mb": 131072, "running_vms": 11},
            "cmp-2": {"memory_mb_used": 98304, "memory_mb": 131072, "running_vms": 9},
            "cmp-3": {"memory_mb_used": 49152, "memory_mb": 131072, "running_vms": 6},
        }[hypervisor],
    )

    result = assistant_service.answer_assistant_query(
        "show top 2",
        auth=None,
        k8s_auth=None,
        previous_result={
            "intent": "highest_ram_hypervisor_summary",
            "mode": "grounded_environment_summary",
            "scope": {"top_hypervisor": "cmp-2"},
            "actions": [{"type": "open_node", "node": "cmp-2", "label": "Open cmp-2"}],
        },
    )

    assert result["intent"] == "followup_top_n"
    assert result["parser_source"] == "followup"
    assert "Top 2 hypervisors by RAM usage" in result["answer"]
    assert "- `cmp-2`: `96.0 GiB`" in result["answer"]
    assert "- `cmp-1`: `64.0 GiB`" in result["answer"]


def test_assistant_service_answers_followup_compare_top_n(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for grounded follow-up questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
            {"name": "cmp-3", "hostname": "cmp-3", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_hypervisor_detail",
        lambda hypervisor, auth=None: {
            "cmp-1": {"memory_mb_used": 65536, "memory_mb": 131072, "running_vms": 11},
            "cmp-2": {"memory_mb_used": 98304, "memory_mb": 131072, "running_vms": 9},
            "cmp-3": {"memory_mb_used": 49152, "memory_mb": 131072, "running_vms": 6},
        }[hypervisor],
    )

    result = assistant_service.answer_assistant_query(
        "compare top 3",
        auth=None,
        k8s_auth=None,
        previous_result={
            "intent": "highest_ram_hypervisor_summary",
            "mode": "grounded_environment_summary",
            "scope": {"top_hypervisor": "cmp-2"},
            "actions": [{"type": "open_node", "node": "cmp-2", "label": "Open cmp-2"}],
        },
    )

    assert result["intent"] == "followup_compare_top_n"
    assert result["parser_source"] == "followup"
    assert "Comparison of top 3:" in result["answer"]
    assert "Top 3 hypervisors by RAM usage" in result["answer"]


def test_assistant_service_answers_followup_problem(monkeypatch):
    monkeypatch.setattr(
        assistant_followups,
        "answer_environment_summary",
        lambda intent, auth=None, k8s_auth=None: {
            "intent": "noschedule_node_summary",
            "answer": "Kubernetes nodes with a `NoSchedule` taint: 2",
            "confidence": 0.9,
            "entities": [{"kind": "environment", "id": "noschedule_nodes", "label": "NoSchedule taints"}],
            "actions": [{"type": "open_node", "node": "cmp-1", "label": "Open cmp-1"}],
            "needs_clarification": False,
            "findings": ["VibeView-managed NoSchedule taints\n- cmp-1\n- cmp-2"],
            "scope": {"noschedule_count": 2, "managed_noschedule_count": 2, "node_total": 3},
            "answer_source": "local",
            "mode": "grounded_environment_summary",
            "provenance": "Grounded Kubernetes node taints",
        },
    )
    result = assistant_service.answer_assistant_query(
        "is that a problem?",
        auth=None,
        k8s_auth=None,
        previous_result={
            "intent": "noschedule_node_summary",
            "mode": "grounded_environment_summary",
            "scope": {"noschedule_count": 2, "managed_noschedule_count": 2, "node_total": 3},
            "actions": [{"type": "open_node", "node": "cmp-1", "label": "Open cmp-1"}],
        },
    )

    assert result["intent"] == "followup_problem"
    assert result["parser_source"] == "followup"
    assert "Not necessarily" in result["answer"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_followup_open_top(monkeypatch):
    result = assistant_service.answer_assistant_query(
        "open the top node",
        auth=None,
        k8s_auth=None,
        previous_result={
            "intent": "memory_request_load_summary",
            "mode": "grounded_environment_summary",
            "scope": {"top_node": "cmp-2"},
            "actions": [{"type": "open_node", "node": "cmp-2", "label": "Open cmp-2"}],
        },
    )

    assert result["intent"] == "followup_open_top"
    assert result["parser_source"] == "followup"
    assert "Opening Open cmp-2." in result["answer"]
    assert result["actions"][0]["node"] == "cmp-2"


def test_assistant_service_answers_followup_open_next(monkeypatch):
    monkeypatch.setattr(
        assistant_followups,
        "answer_environment_summary",
        lambda intent, auth=None, k8s_auth=None: {
            "intent": "memory_request_load_summary",
            "answer": "The Kubernetes node with the highest memory request load is `cmp-2`.",
            "confidence": 0.86,
            "entities": [{"kind": "environment", "id": "memory_request_load", "label": "memory request load"}],
            "actions": [
                {"type": "open_node", "node": "cmp-2", "label": "Open cmp-2"},
                {"type": "open_node", "node": "cmp-1", "label": "Open cmp-1"},
            ],
            "needs_clarification": False,
            "findings": [],
            "scope": {"top_node": "cmp-2"},
            "answer_source": "local",
            "mode": "grounded_environment_summary",
            "provenance": "Grounded Kubernetes node density data",
        },
    )
    result = assistant_service.answer_assistant_query(
        "open the next one",
        auth=None,
        k8s_auth=None,
        previous_result={
            "intent": "memory_request_load_summary",
            "mode": "grounded_environment_summary",
            "scope": {"top_node": "cmp-2"},
            "actions": [{"type": "open_node", "node": "cmp-2", "label": "Open cmp-2"}],
        },
    )

    assert result["intent"] == "followup_open_next"
    assert result["parser_source"] == "followup"
    assert "Opening Open cmp-1." in result["answer"]
    assert result["actions"][0]["node"] == "cmp-1"


def test_assistant_service_answers_provider_driven_followup_compare_top_n(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "assistant_provider_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        assistant_service,
        "_parse_followup_with_provider",
        lambda query, previous_result: {
            "kind": "compare_top_n",
            "limit": 3,
            "parser_source": "xerotier",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for provider-driven grounded follow-up questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
            {"name": "cmp-3", "hostname": "cmp-3", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_hypervisor_detail",
        lambda hypervisor, auth=None: {
            "cmp-1": {"memory_mb_used": 65536, "memory_mb": 131072, "running_vms": 11},
            "cmp-2": {"memory_mb_used": 98304, "memory_mb": 131072, "running_vms": 9},
            "cmp-3": {"memory_mb_used": 49152, "memory_mb": 131072, "running_vms": 6},
        }[hypervisor],
    )

    result = assistant_service.answer_assistant_query(
        "show me the top three side by side",
        auth=None,
        k8s_auth=None,
        previous_result={
            "intent": "highest_ram_hypervisor_summary",
            "mode": "grounded_environment_summary",
            "scope": {"top_hypervisor": "cmp-2"},
            "actions": [{"type": "open_node", "node": "cmp-2", "label": "Open cmp-2"}],
        },
    )

    assert result["intent"] == "followup_compare_top_n"
    assert result["parser_source"] == "xerotier"
    assert "Comparison of top 3:" in result["answer"]
    assert "Top 3 hypervisors by RAM usage" in result["answer"]


def test_assistant_service_answers_followup_compare_next(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for grounded follow-up questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
            {"name": "cmp-3", "hostname": "cmp-3", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_hypervisor_detail",
        lambda hypervisor, auth=None: {
            "cmp-1": {"memory_mb_used": 65536, "memory_mb": 131072, "running_vms": 11},
            "cmp-2": {"memory_mb_used": 98304, "memory_mb": 131072, "running_vms": 9},
            "cmp-3": {"memory_mb_used": 49152, "memory_mb": 131072, "running_vms": 6},
        }[hypervisor],
    )

    result = assistant_service.answer_assistant_query(
        "compare it to the next one",
        auth=None,
        k8s_auth=None,
        previous_result={
            "intent": "highest_ram_hypervisor_summary",
            "mode": "grounded_environment_summary",
            "scope": {"top_hypervisor": "cmp-2"},
            "actions": [{"type": "open_node", "node": "cmp-2", "label": "Open cmp-2"}],
        },
    )

    assert result["intent"] == "followup_compare_next"
    assert result["parser_source"] == "followup"
    assert "Comparison against the next result:" in result["answer"]
    assert "Top 2 hypervisors by RAM usage" in result["answer"]


def test_assistant_service_answers_followup_unhealthy_only(monkeypatch):
    monkeypatch.setattr(
        assistant_followups,
        "answer_environment_summary",
        lambda intent, auth=None, k8s_auth=None: {
            "intent": "not_ready_node_summary",
            "answer": "Kubernetes nodes that are NotReady: 2",
            "confidence": 0.9,
            "entities": [{"kind": "environment", "id": "not_ready_nodes", "label": "NotReady nodes"}],
            "actions": [{"type": "open_node", "node": "cmp-2", "label": "Open cmp-2"}],
            "needs_clarification": False,
            "findings": ["NotReady nodes\n- cmp-2\n- cmp-3"],
            "scope": {"not_ready_count": 2, "node_total": 5},
            "answer_source": "local",
            "mode": "grounded_environment_summary",
            "provenance": "Grounded Kubernetes node density data",
        },
    )
    result = assistant_service.answer_assistant_query(
        "show only the unhealthy ones",
        auth=None,
        k8s_auth=None,
        previous_result={
            "intent": "not_ready_node_summary",
            "mode": "grounded_environment_summary",
            "scope": {"not_ready_count": 2, "node_total": 5},
            "actions": [{"type": "open_node", "node": "cmp-2", "label": "Open cmp-2"}],
        },
    )

    assert result["intent"] == "followup_unhealthy_only"
    assert result["parser_source"] == "followup"
    assert "Only the unhealthy or flagged resources:" in result["answer"]
    assert "NotReady nodes" in result["answer"]


def test_assistant_service_answers_followup_affected_namespaces(monkeypatch):
    monkeypatch.setattr(
        assistant_followups,
        "answer_environment_summary",
        lambda intent, auth=None, k8s_auth=None: {
            "intent": "memory_request_load_summary",
            "answer": "The Kubernetes node with the highest memory request load is `cmp-2`.",
            "confidence": 0.86,
            "entities": [{"kind": "environment", "id": "memory_request_load", "label": "memory request load"}],
            "actions": [{"type": "open_node", "node": "cmp-2", "label": "Open cmp-2"}],
            "needs_clarification": False,
            "findings": [],
            "scope": {"top_node": "cmp-2", "top_namespace_names": ["api", "ingress", "metrics"]},
            "answer_source": "local",
            "mode": "grounded_environment_summary",
            "provenance": "Grounded Kubernetes node density data",
        },
    )
    result = assistant_service.answer_assistant_query(
        "show affected namespaces",
        auth=None,
        k8s_auth=None,
        previous_result={
            "intent": "memory_request_load_summary",
            "mode": "grounded_environment_summary",
            "scope": {"top_node": "cmp-2"},
            "actions": [{"type": "open_node", "node": "cmp-2", "label": "Open cmp-2"}],
        },
    )

    assert result["intent"] == "followup_affected_namespaces"
    assert result["parser_source"] == "followup"
    assert "Affected namespaces on `cmp-2`:" in result["answer"]
    assert "- `api`" in result["answer"]


def test_assistant_service_answers_provider_driven_followup_compare_named_nodes(monkeypatch):
    monkeypatch.setattr(assistant_service, "assistant_provider_enabled", lambda: True)
    monkeypatch.setattr(
        assistant_service,
        "_parse_followup_with_provider",
        lambda query, previous_result: {
            "kind": "compare_named_nodes",
            "left": "cmp-1",
            "right": "cmp-2",
            "parser_source": "xerotier",
        },
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_k8s_node_health_density_summary",
        lambda auth=None: {
            "nodes": [
                {"node": "cmp-1", "pod_count": 80, "pods_allocatable": 200, "namespace_count": 5, "namespace_names": ["api", "metrics"], "pvc_pod_count": 2, "ready": True, "cordoned": False, "conditions": []},
                {"node": "cmp-2", "pod_count": 140, "pods_allocatable": 180, "namespace_count": 9, "namespace_names": ["api", "ingress", "mq"], "pvc_pod_count": 7, "ready": False, "cordoned": True, "conditions": ["MemoryPressure"]},
            ],
            "error": None,
        },
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_all_host_summaries",
        lambda auth=None: {
            "cmp-1": {"vm_count": 4, "compute_status": "up"},
            "cmp-2": {"vm_count": 9, "compute_status": "up"},
        },
    )
    monkeypatch.setattr(assistant_environment_answers.node_agent_client, "get_ready_node_names", lambda: ["cmp-1"])
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: {
            "kernel_version": "5.14.0-500" if node_name == "cmp-1" else "5.14.0-510",
            "latest_kernel_version": "5.14.0-510",
            "reboot_required": node_name == "cmp-2",
        },
    )

    result = assistant_service.answer_assistant_query(
        "show me cmp-1 and cmp-2 side by side",
        auth=None,
        k8s_auth=None,
        previous_result={
            "intent": "memory_request_load_summary",
            "mode": "grounded_environment_summary",
            "scope": {"top_node": "cmp-2"},
            "actions": [{"type": "open_node", "node": "cmp-2", "label": "Open cmp-2"}],
        },
    )

    assert result["intent"] == "followup_compare_named_nodes"
    assert result["parser_source"] == "xerotier"
    assert "Node comparison" in result["answer"]
    assert "`cmp-1`" in result["answer"]
    assert "`cmp-2`" in result["answer"]


def test_assistant_service_answers_provider_driven_followup_managed_only(monkeypatch):
    monkeypatch.setattr(assistant_service, "assistant_provider_enabled", lambda: True)
    monkeypatch.setattr(
        assistant_service,
        "_parse_followup_with_provider",
        lambda query, previous_result: {
            "kind": "managed_only",
            "parser_source": "xerotier",
        },
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "taints": [{"key": "vibeview", "value": "managed", "effect": "NoSchedule"}]},
            {"name": "cmp-2", "hostname": "cmp-2", "taints": [{"key": "custom", "value": "true", "effect": "NoSchedule"}]},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_k8s_node_health_density_summary",
        lambda auth=None: {
            "nodes": [
                {"node": "cmp-1", "pod_count": 10, "pods_allocatable": 100, "namespace_count": 2, "namespace_names": ["api"], "pvc_pod_count": 1, "ready": True, "cordoned": False, "conditions": []},
                {"node": "cmp-2", "pod_count": 0, "pods_allocatable": 100, "namespace_count": 0, "namespace_names": [], "pvc_pod_count": 0, "ready": True, "cordoned": False, "conditions": []},
            ],
            "error": None,
        },
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "has_managed_noschedule_taint",
        lambda taints: any(str(t.get("key")) == "vibeview" for t in taints),
    )

    result = assistant_service.answer_assistant_query(
        "just show the managed ones",
        auth=None,
        k8s_auth=None,
        previous_result={
            "intent": "noschedule_node_summary",
            "mode": "grounded_environment_summary",
            "scope": {"noschedule_count": 2},
            "actions": [{"type": "open_node", "node": "cmp-1", "label": "Open cmp-1"}],
        },
    )

    assert result["intent"] == "followup_managed_only"
    assert result["parser_source"] == "xerotier"
    assert "VibeView-managed `NoSchedule`" in result["answer"]
    assert "`cmp-1`" in result["answer"]


def test_assistant_service_answers_provider_driven_followup_compute_with_workloads(monkeypatch):
    monkeypatch.setattr(assistant_service, "assistant_provider_enabled", lambda: True)
    monkeypatch.setattr(
        assistant_service,
        "_parse_followup_with_provider",
        lambda query, previous_result: {
            "kind": "compute_with_workloads",
            "parser_source": "xerotier",
        },
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
            {"name": "cmp-3", "hostname": "cmp-3", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_all_host_summaries",
        lambda auth=None: {
            "cmp-1": {"vm_count": 0, "compute_status": "up"},
            "cmp-2": {"vm_count": 5, "compute_status": "up"},
            "cmp-3": {"vm_count": 2, "compute_status": "up"},
        },
    )

    result = assistant_service.answer_assistant_query(
        "show only compute nodes with workloads",
        auth=None,
        k8s_auth=None,
        previous_result={
            "intent": "busiest_hypervisor_summary",
            "mode": "grounded_environment_summary",
            "scope": {"top_hypervisor": "cmp-2"},
            "actions": [{"type": "open_node", "node": "cmp-2", "label": "Open cmp-2"}],
        },
    )

    assert result["intent"] == "followup_compute_with_workloads"
    assert result["parser_source"] == "xerotier"
    assert "Compute nodes currently hosting running VMs: 2" in result["answer"]
    assert "`cmp-2` (5 VMs)" in result["answer"]
    assert "`cmp-3` (2 VMs)" in result["answer"]


def test_assistant_service_answers_followup_changed(monkeypatch):
    result = assistant_service.answer_assistant_query(
        "what changed?",
        auth=None,
        k8s_auth=None,
        previous_result={
            "intent": "cluster_health_overview",
            "mode": "grounded_cluster_health",
            "scope": {"node_total": 3, "node_ready": 2},
            "actions": [{"type": "open_node", "node": "node-3", "label": "Open node-3"}],
        },
    )

    assert result["intent"] == "followup_changed"
    assert result["parser_source"] == "followup"
    assert "do not have a historical baseline" in result["answer"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_cordoned_node_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for cordoned node questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_k8s_node_health_density_summary",
        lambda auth=None: {
            "nodes": [
                {"node": "cmp-1", "pod_count": 120, "pods_allocatable": 200, "ready": True, "cordoned": True, "conditions": []},
                {"node": "cmp-2", "pod_count": 140, "pods_allocatable": 160, "ready": False, "cordoned": False, "conditions": ["MemoryPressure"]},
            ],
            "error": None,
        },
    )

    result = assistant_service.answer_assistant_query("which kubernetes nodes are cordoned?", auth=None, k8s_auth=None)

    assert result["intent"] == "cordoned_node_summary"
    assert "Kubernetes nodes that are cordoned: 1" in result["answer"]
    assert "`cmp-1`" in result["answer"]
    assert "`120`" in result["answer"]
    assert result["actions"][0]["node"] == "cmp-1"
    assert result["answer_source"] == "local"


def test_assistant_service_answers_node_attention_summary(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for node attention questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True, "kernel_version": "5.14.0-500"},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True, "kernel_version": "5.14.0-510"},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_all_host_summaries",
        lambda auth=None: {
            "cmp-1": {"vm_count": 11, "compute_status": "up"},
            "cmp-2": {"vm_count": 2, "compute_status": "up"},
        },
    )
    monkeypatch.setattr(
        assistant_environment_answers.node_agent_client,
        "get_ready_node_names",
        lambda: ["cmp-2"],
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: {
            "kernel_version": "5.14.0-500" if node_name == "cmp-1" else "5.14.0-510",
            "latest_kernel_version": "5.14.0-510",
            "reboot_required": node_name == "cmp-1",
        },
    )

    result = assistant_service.answer_assistant_query("which nodes need the most attention?", auth=None, k8s_auth=None)

    assert result["intent"] == "node_attention_summary"
    assert "Nodes needing the most attention: 1" in result["answer"]
    assert "`cmp-1`" in result["answer"]
    assert "reboot required" in result["answer"]
    assert "node agent missing" in result["answer"]
    assert result["actions"][0]["node"] == "cmp-1"
    assert result["answer_source"] == "local"


def test_assistant_service_answers_top_n_hypervisors_by_ram(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for top-n RAM hypervisor summary questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
            {"name": "cmp-3", "hostname": "cmp-3", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_hypervisor_detail",
        lambda hypervisor, auth=None: {
            "cmp-1": {"memory_mb_used": 65536, "memory_mb": 131072, "running_vms": 11},
            "cmp-2": {"memory_mb_used": 98304, "memory_mb": 131072, "running_vms": 9},
            "cmp-3": {"memory_mb_used": 49152, "memory_mb": 131072, "running_vms": 6},
        }[hypervisor],
    )

    result = assistant_service.answer_assistant_query("top 2 hypervisors by ram usage", auth=None, k8s_auth=None)

    assert result["intent"] == "hypervisor_metric_top_n"
    assert "Top 2 hypervisors by RAM usage" in result["answer"]
    assert "- `cmp-2`: `96.0 GiB`" in result["answer"]
    assert "- `cmp-1`: `64.0 GiB`" in result["answer"]
    assert result["answer_source"] == "local"


def test_assistant_service_answers_bottom_n_hypervisors_by_vm_load(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": (_ for _ in ()).throw(AssertionError("project context should be skipped for bottom-n hypervisor summary questions")),
    )
    monkeypatch.setattr(
        assistant_environment_answers.k8s_ops,
        "get_nodes",
        lambda auth=None: [
            {"name": "cmp-1", "hostname": "cmp-1", "is_compute": True},
            {"name": "cmp-2", "hostname": "cmp-2", "is_compute": True},
            {"name": "cmp-3", "hostname": "cmp-3", "is_compute": True},
        ],
    )
    monkeypatch.setattr(
        assistant_environment_answers.openstack_ops,
        "get_all_host_summaries",
        lambda auth=None: {
            "cmp-1": {"vm_count": 11, "compute_status": "up"},
            "cmp-2": {"vm_count": 2, "compute_status": "up"},
            "cmp-3": {"vm_count": 6, "compute_status": "up"},
        },
    )

    result = assistant_service.answer_assistant_query("bottom 2 hypervisors by vm load", auth=None, k8s_auth=None)

    assert result["intent"] == "hypervisor_metric_top_n"
    assert "Bottom 2 hypervisors by VM load" in result["answer"]
    assert "- `cmp-2`: `2`" in result["answer"]
    assert "- `cmp-3`: `6`" in result["answer"]
    assert result["answer_source"] == "local"


def test_fallback_parser_leaves_networking_diagnosis_question_unknown():
    parsed = assistant_service._parse_query_fallback("What should I look at first if networking feels unhealthy for this project?")

    assert parsed["intent"] == "unknown"
    assert parsed["needs_clarification"] is True


def test_fallback_parser_leaves_networking_risk_summary_question_unknown():
    parsed = assistant_service._parse_query_fallback("Summarize the biggest networking risks in this project")

    assert parsed["intent"] == "unknown"
    assert parsed["needs_clarification"] is True


def test_assistant_service_uses_grounded_network_diagnosis(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(assistant_service, "assistant_provider_enabled", lambda: True)
    monkeypatch.setattr(
        assistant_service,
        "build_project_network_diagnosis_evidence",
        lambda project_id, project_name, auth=None: {
            "scope": {"project_id": project_id, "project_name": project_name},
            "summary": {"network_count": 7, "router_count": 1, "port_count": 30, "floating_ip_count": 13},
            "signals": {"high_risk_security_groups": 2},
            "findings": ["2 security groups have high-risk audit findings."],
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "synthesize_network_diagnosis",
        lambda query, evidence, provider: {
            "answer": "Start with the high-risk security groups and internet-facing ports.",
            "findings": ["2 security groups have high-risk audit findings."],
            "recommendations": ["Review those groups first."],
            "confidence": 0.83,
        },
    )

    result = assistant_service.answer_assistant_query(
        "What should I look at first if networking feels unhealthy for this project?",
        auth=None,
    )

    assert result["intent"] == "network_diagnosis"
    assert result["mode"] == "grounded_diagnosis"
    assert result["provenance"] == "Synthesized from project networking evidence"
    assert result["recommendations"]


def test_assistant_service_answers_platform_knowledge(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "", "project_name": ""},
    )
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: (_ for _ in ()).throw(AssertionError("provider parse should be skipped for platform knowledge")),
    )
    monkeypatch.setattr(assistant_service, "assistant_provider_enabled", lambda: True)
    monkeypatch.setattr(
        assistant_service,
        "answer_platform_knowledge",
        lambda query, provider: {
            "answer": "A floating IP is a public address that can be associated with a Neutron port.",
            "confidence": 0.9,
        },
    )

    result = assistant_service.answer_assistant_query("what is a floating ip?", auth=None)

    assert result["intent"] == "platform_knowledge"
    assert result["mode"] == "platform_knowledge"
    assert "public address" in result["answer"]
    assert result["parser_source"] == "task-router"
    assert result["answer_source"] == "xerotier"


def test_openstack_knowledge_context_extracts_placement_trait():
    context = assistant_openstack_knowledge.build_platform_knowledge_context(
        "what is the COMPUTE_NET_VIF_MODEL_RTL8139 trait for?"
    )

    assert context["kind"] == "placement_trait"
    assert context["trait"] == "COMPUTE_NET_VIF_MODEL_RTL8139"
    assert context["category"] == "virtual NIC model trait"
    assert "RTL8139" in context["details"][0]


def test_assistant_service_answers_platform_knowledge_for_placement_trait_with_context(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "", "project_name": ""},
    )
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: (_ for _ in ()).throw(AssertionError("provider parse should be skipped for platform knowledge")),
    )
    monkeypatch.setattr(assistant_service, "assistant_provider_enabled", lambda: True)
    monkeypatch.setattr(
        assistant_service,
        "answer_platform_knowledge_with_context",
        lambda query, provider, context: {
            "answer": "COMPUTE_NET_VIF_MODEL_RTL8139 is a Placement trait used to advertise support for the RTL8139 virtual NIC model on compute hosts so workloads that require that NIC model can be scheduled there.",
            "confidence": 0.88,
        },
    )

    result = assistant_service.answer_assistant_query(
        "what is the COMPUTE_NET_VIF_MODEL_RTL8139 trait for?",
        auth=None,
    )

    assert result["intent"] == "platform_knowledge"
    assert result["mode"] == "platform_knowledge"
    assert "RTL8139 virtual NIC model" in result["answer"]
    assert result["answer_source"] == "xerotier"
    assert result["provenance"] == "General OpenStack knowledge with trait context"


def test_assistant_service_falls_back_locally_for_placement_trait_when_provider_disabled(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "", "project_name": ""},
    )
    monkeypatch.setattr(assistant_service, "assistant_provider_enabled", lambda: False)

    result = assistant_service.answer_assistant_query(
        "what is the COMPUTE_NET_VIF_MODEL_RTL8139 trait for?",
        auth=None,
    )

    assert result["intent"] == "platform_knowledge"
    assert result["answer_source"] == "local"
    assert "Placement" in result["answer"]
    assert "RTL8139" in result["answer"]


def test_assistant_service_answers_platform_knowledge_for_router_definition(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "", "project_name": ""},
    )
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: (_ for _ in ()).throw(AssertionError("provider parse should be skipped for platform knowledge")),
    )
    monkeypatch.setattr(assistant_service, "assistant_provider_enabled", lambda: True)
    monkeypatch.setattr(
        assistant_service,
        "answer_platform_knowledge",
        lambda query, provider: {
            "answer": "A router connects networks and can provide external gateway access.",
            "confidence": 0.9,
        },
    )

    result = assistant_service.answer_assistant_query("what is a router?", auth=None)

    assert result["intent"] == "platform_knowledge"
    assert result["mode"] == "platform_knowledge"
    assert "connects networks" in result["answer"]
    assert result["parser_source"] == "task-router"


def test_assistant_service_answers_platform_knowledge_for_vm_howto(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "", "project_name": ""},
    )
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: (_ for _ in ()).throw(AssertionError("provider parse should be skipped for platform knowledge")),
    )
    monkeypatch.setattr(assistant_service, "assistant_provider_enabled", lambda: True)
    monkeypatch.setattr(
        assistant_service,
        "answer_platform_knowledge",
        lambda query, provider: {
            "answer": "In OpenStack, freezing a VM usually maps to suspend or pause behavior depending on the workflow.",
            "confidence": 0.82,
        },
    )

    result = assistant_service.answer_assistant_query("how do i freeze my vm?", auth=None)

    assert result["intent"] == "platform_knowledge"
    assert result["mode"] == "platform_knowledge"
    assert "suspend or pause" in result["answer"]
    assert result["parser_source"] == "task-router"


def test_assistant_service_coerces_string_confidence_for_platform_knowledge(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "", "project_name": ""},
    )
    monkeypatch.setattr(assistant_service, "assistant_provider_enabled", lambda: True)
    monkeypatch.setattr(
        assistant_service,
        "answer_platform_knowledge",
        lambda query, provider: {
            "answer": "A floating IP is a public address.",
            "confidence": "High",
        },
    )

    result = assistant_service.answer_assistant_query("what is a floating ip?", auth=None)

    assert result["intent"] == "platform_knowledge"
    assert result["confidence"] == 0.85


def test_assistant_service_answers_domain_chat(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "", "project_name": ""},
    )
    monkeypatch.setattr(assistant_service, "assistant_provider_enabled", lambda: True)
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "unknown",
            "resource_kind": "",
            "resource_name": "",
            "needs_clarification": False,
            "parser_source": "xerotier",
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "answer_domain_chat",
        lambda query, provider: {
            "answer": "Linux is usually the better default for Kubernetes workers because ecosystem support is broader.",
            "confidence": 0.7,
        },
    )

    result = assistant_service.answer_assistant_query("should i use linux or windows for kubernetes workers?", auth=None)

    assert result["intent"] == "domain_chat"
    assert result["mode"] == "domain_chat"
    assert "Linux" in result["answer"]
    assert result["parser_source"] == "xerotier"
    assert result["answer_source"] == "xerotier"


def test_assistant_service_overrides_provider_network_summary_for_diagnosis(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "proj-1", "project_name": "admin"},
    )
    monkeypatch.setattr(
        assistant_service,
        "_parse_query_with_provider",
        lambda query, context: {
            "intent": "project_network_summary",
            "resource_kind": "project",
            "resource_name": "",
            "needs_clarification": False,
            "parser_source": "xerotier",
        },
    )
    monkeypatch.setattr(assistant_service, "assistant_provider_enabled", lambda: True)
    monkeypatch.setattr(
        assistant_service,
        "build_project_network_diagnosis_evidence",
        lambda project_id, project_name, auth=None: {
            "scope": {"project_id": project_id, "project_name": project_name},
            "summary": {"network_count": 7, "router_count": 1, "port_count": 30, "floating_ip_count": 13},
            "signals": {"high_risk_security_groups": 2},
            "findings": ["2 security groups have high-risk audit findings."],
        },
    )
    monkeypatch.setattr(
        assistant_service,
        "synthesize_network_diagnosis",
        lambda query, evidence, provider: {
            "answer": "Start with the high-risk security groups.",
            "findings": ["2 security groups have high-risk audit findings."],
            "recommendations": ["Review those groups first."],
            "confidence": 0.81,
        },
    )

    result = assistant_service.answer_assistant_query(
        "What should I look at first if networking feels unhealthy for this project?",
        auth=None,
    )

    assert result["intent"] == "network_diagnosis"
    assert result["mode"] == "grounded_diagnosis"
    assert "high-risk security groups" in result["answer"]


def test_assistant_service_rejects_out_of_scope_query(monkeypatch):
    monkeypatch.setattr(
        assistant_service,
        "_default_project_context",
        lambda auth, selected_project_id="": {"project_id": "", "project_name": ""},
    )

    result = assistant_service.answer_assistant_query("what is the meaning of life?", auth=None)

    assert result["intent"] == "out_of_scope"
    assert result["mode"] == "out_of_scope"
    assert "scoped to infrastructure and platform topics" in result["answer"]
    assert result["answer_source"] == "local"


def test_ports_endpoints_return_items(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "get_ports",
        lambda auth=None: [{
            "id": "port-1",
            "name": "api-port",
            "status": "ACTIVE",
            "network_id": "net-1",
            "network_name": "tenant-net",
            "attached_kind": "instance",
            "attached_id": "vm-1",
            "attached_name": "api-01",
            "project_id": "proj-1",
        }],
    )
    monkeypatch.setattr(
        resource_api,
        "get_port_detail",
        lambda port_id, auth=None: {
            "id": port_id,
            "name": "api-port",
            "status": "ACTIVE",
            "network_id": "net-1",
            "network_name": "tenant-net",
            "security_groups": [{"id": "sg-1", "name": "default"}],
        },
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        ports_resp = client.get("/api/ports")
        port_resp = client.get("/api/ports/port-1")

    assert ports_resp.status_code == 200
    assert ports_resp.json()["ports"][0]["attached_name"] == "api-01"
    assert port_resp.status_code == 200
    assert port_resp.json()["port"]["security_groups"][0]["id"] == "sg-1"


def test_volume_snapshot_and_backup_endpoints_return_items(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(resource_api, "get_volume_snapshots", lambda auth=None: [{
        "id": "snap-1",
        "name": "snap-db",
        "status": "available",
        "size_gb": 120,
        "volume_id": "vol-1",
        "project_id": "proj-1",
        "created_at": "2026-04-18T02:00:00Z",
    }])
    monkeypatch.setattr(resource_api, "get_volume_backups", lambda auth=None: [{
        "id": "backup-1",
        "name": "backup-db",
        "status": "available",
        "size_gb": 120,
        "volume_id": "vol-1",
        "project_id": "proj-1",
        "created_at": "2026-04-18T03:00:00Z",
        "is_incremental": False,
        "container": "",
    }])

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        snapshot_resp = client.get("/api/volume-snapshots")
        backup_resp = client.get("/api/volume-backups")

    assert snapshot_resp.status_code == 200
    assert snapshot_resp.json()["snapshots"][0]["id"] == "snap-1"
    assert backup_resp.status_code == 200
    assert backup_resp.json()["backups"][0]["id"] == "backup-1"


def test_volume_retype_endpoint_requests_action(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "retype_volume",
        lambda volume_id, target_type, migration_policy="on-demand", auth=None: {
            "volume_id": volume_id,
            "target_type": target_type,
            "migration_policy": migration_policy,
            "status": "requested",
        },
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.post("/api/volumes/vol-1/retype", json={"target_type": "gold-backend-b", "migration_policy": "on-demand"})

    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["result"]["volume_id"] == "vol-1"
    assert body["result"]["target_type"] == "gold-backend-b"


def test_projects_endpoints_return_inventory(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "get_projects",
        lambda auth=None, search="": [{
            "project_id": "proj-1",
            "project_name": "production",
            "instance_count": 4,
            "network_count": 2,
            "volume_count": 5,
            "security_group_count": 3,
            "floating_ip_count": 1,
            "load_balancer_count": 1,
            "host_count": 2,
            "top_host": "cmp-12",
            "top_host_pct": 50.0,
        }],
    )
    monkeypatch.setattr(
        resource_api,
        "get_project_inventory",
        lambda project_id, auth=None, section="overview", family="": {
            "summary": {"project_id": project_id, "project_name": "production"},
            "instances": [{"id": "vm-1", "name": "api-01"}] if section == "instances" else [],
            "quotas": {"compute": {"instances": {"used": 4, "limit": 20}}} if section == "quota" else {},
        },
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        projects_resp = client.get("/api/projects")
        inventory_resp = client.get("/api/projects/proj-1/inventory?section=instances")
        quota_resp = client.get("/api/projects/proj-1/inventory?section=quota")

    assert projects_resp.status_code == 200
    assert projects_resp.json()["projects"][0]["project_name"] == "production"
    assert inventory_resp.status_code == 200
    assert inventory_resp.json()["inventory"]["instances"][0]["id"] == "vm-1"
    assert quota_resp.status_code == 200
    assert quota_resp.json()["inventory"]["quotas"]["compute"]["instances"]["limit"] == 20


def test_search_endpoint_returns_normalized_results(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "search_resources",
        lambda auth, query, limit=20, k8s_auth=None: [
            {
                "kind": "instance",
                "id": "vm-1",
                "label": "prod-api-01",
                "subtext": "production • cmp-01",
                "score": 120,
                "project_id": "proj-1",
                "compute_host": "cmp-01",
            },
            {
                "kind": "network",
                "id": "net-1",
                "label": "prod-net",
                "subtext": "vxlan • ACTIVE",
                "score": 95,
                "project_id": "proj-1",
            },
        ],
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.get("/api/search?q=prod&limit=5")

    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["results"][0]["kind"] == "instance"
    assert body["results"][0]["compute_host"] == "cmp-01"


def test_get_project_quota_summary_reads_usage_from_openstack_quota_set_shape(monkeypatch):
    class FakeCompute:
        def get_quota_set(self, project_id, usage=True):
            assert project_id == "proj-1"
            assert usage is True
            return {
                "id": project_id,
                "instances": 100,
                "cores": 200,
                "ram": 102400,
                "server_groups": 50,
                "server_group_members": 20,
                "usage": {
                    "instances": 14,
                    "cores": 17,
                    "ram": 34816,
                    "server_groups": 7,
                    "server_group_members": 0,
                },
            }

    class FakeNetwork:
        def get_quota(self, project_id):
            assert project_id == "proj-1"
            return {}

    class FakeBlockStorage:
        def get_quota_set(self, project_id, usage=True):
            assert project_id == "proj-1"
            assert usage is True
            return {}

    class FakeConn:
        compute = FakeCompute()
        network = FakeNetwork()
        block_storage = FakeBlockStorage()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    quotas = resource_helpers.get_project_quota_summary("proj-1", auth=None)

    assert quotas["compute"]["instances"] == {"used": 14, "limit": 100}
    assert quotas["compute"]["cores"] == {"used": 17, "limit": 200}
    assert quotas["compute"]["ram"] == {"used": 34816, "limit": 102400}


def test_get_project_quota_summary_reads_network_quota_details_shape(monkeypatch):
    class FakeCompute:
        def get_quota_set(self, project_id, usage=True):
            assert project_id == "proj-1"
            return {}

    class FakeNetwork:
        def get_quota(self, project_id, details=True):
            assert project_id == "proj-1"
            assert details is True
            return {
                "networks": {"limit": 10, "used": 3, "reserved": 0},
                "subnets": {"limit": 10, "used": 4, "reserved": 0},
                "ports": {"limit": 50, "used": 18, "reserved": 0},
                "routers": {"limit": 5, "used": 1, "reserved": 0},
                "floating_ips": {"limit": 20, "used": 2, "reserved": 0},
                "security_groups": {"limit": 10, "used": 3, "reserved": 0},
                "security_group_rules": {"limit": 100, "used": 12, "reserved": 0},
                "load_balancers": {"limit": 5, "used": 1, "reserved": 0},
                "listeners": {"limit": 10, "used": 2, "reserved": 0},
                "pools": {"limit": 10, "used": 1, "reserved": 0},
                "members": {"limit": 50, "used": 4, "reserved": 0},
                "health_monitors": {"limit": 10, "used": 1, "reserved": 0},
            }

    class FakeBlockStorage:
        def get_quota_set(self, project_id, usage=True):
            assert project_id == "proj-1"
            return {}

    class FakeConn:
        compute = FakeCompute()
        network = FakeNetwork()
        block_storage = FakeBlockStorage()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    quotas = resource_helpers.get_project_quota_summary("proj-1", auth=None)

    assert quotas["network"]["network"] == {"used": 3, "limit": 10}
    assert quotas["network"]["subnet"] == {"used": 4, "limit": 10}
    assert quotas["network"]["port"] == {"used": 18, "limit": 50}
    assert quotas["network"]["floatingip"] == {"used": 2, "limit": 20}
    assert quotas["network"]["health_monitor"] == {"used": 1, "limit": 10}


def test_get_project_quota_summary_reads_network_quota_details_object_shape(monkeypatch):
    class FakeQuotaDetails:
        def to_dict(self):
            return {
                "trunk": {"limit": -1, "used": 0, "reserved": 0},
                "endpoint_group": {"limit": -1, "used": 0, "reserved": 0},
                "vpnservice": {"limit": -1, "used": 0, "reserved": 0},
                "ipsec_site_connection": {"limit": -1, "used": 0, "reserved": 0},
                "ipsecpolicy": {"limit": -1, "used": 0, "reserved": 0},
                "ikepolicy": {"limit": -1, "used": 0, "reserved": 0},
                "floating_ips": {"limit": 50, "used": 13, "reserved": 0},
                "health_monitors": None,
                "listeners": None,
                "load_balancers": None,
                "l7_policies": None,
                "networks": {"limit": 100, "used": 7, "reserved": 0},
                "pools": None,
                "ports": {"limit": 500, "used": 30, "reserved": 0},
                "project_id": None,
                "rbac_policies": {"limit": 10, "used": 1, "reserved": 0},
                "routers": {"limit": 10, "used": 1, "reserved": 0},
                "subnets": {"limit": 100, "used": 6, "reserved": 0},
                "subnet_pools": {"limit": -1, "used": 0, "reserved": 0},
                "security_group_rules": {"limit": 500, "used": 56, "reserved": 0},
                "security_groups": {"limit": 100, "used": 12, "reserved": 0},
            }

    class FakeCompute:
        def get_quota_set(self, project_id, usage=True):
            assert project_id == "proj-1"
            return {}

    class FakeNetwork:
        def get_quota(self, project_id, details=True):
            assert project_id == "proj-1"
            assert details is True
            return FakeQuotaDetails()

    class FakeBlockStorage:
        def get_quota_set(self, project_id, usage=True):
            assert project_id == "proj-1"
            return {}

    class FakeConn:
        compute = FakeCompute()
        network = FakeNetwork()
        block_storage = FakeBlockStorage()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    quotas = resource_helpers.get_project_quota_summary("proj-1", auth=None)

    assert quotas["network"]["network"] == {"used": 7, "limit": 100}
    assert quotas["network"]["port"] == {"used": 30, "limit": 500}
    assert quotas["network"]["floatingip"] == {"used": 13, "limit": 50}
    assert quotas["network"]["rbac_policy"] == {"used": 1, "limit": 10}
    assert quotas["network"]["subnet_pool"] == {"used": 0, "limit": -1}
    assert quotas["network"]["trunk"] == {"used": 0, "limit": -1}
    assert quotas["network"]["endpoint_group"] == {"used": 0, "limit": -1}
    assert quotas["network"]["vpnservice"] == {"used": 0, "limit": -1}
    assert quotas["network"]["ipsec_site_connection"] == {"used": 0, "limit": -1}
    assert quotas["network"]["ipsecpolicy"] == {"used": 0, "limit": -1}
    assert quotas["network"]["ikepolicy"] == {"used": 0, "limit": -1}
    assert "health_monitor" not in quotas["network"]
    assert "load_balancer" not in quotas["network"]
    assert "listener" not in quotas["network"]
    assert "l7_policy" not in quotas["network"]
    assert "pool" not in quotas["network"]


def test_get_floating_ips_enriches_attached_instance(monkeypatch):
    class FakeServer:
        id = "server-1"
        name = "api-01"

        def to_dict(self):
            return {"id": self.id, "name": self.name}

    class FakeCompute:
        def get_server(self, server_id):
            assert server_id == "server-1"
            return FakeServer()

    class FakeIp:
        id = "fip-1"
        floating_ip_address = "198.51.100.10"
        fixed_ip_address = "10.0.0.7"
        status = "ACTIVE"
        project_id = "proj-1"
        floating_network_id = "ext-net"
        port_id = "port-1"
        router_id = "router-1"
        description = "public"

        def to_dict(self):
            return {
                "id": self.id,
                "floating_ip_address": self.floating_ip_address,
                "fixed_ip_address": self.fixed_ip_address,
                "status": self.status,
                "project_id": self.project_id,
                "floating_network_id": self.floating_network_id,
                "port_id": self.port_id,
                "router_id": self.router_id,
                "description": self.description,
            }

    class FakePort:
        def to_dict(self):
            return {"id": "port-1", "device_owner": "compute:nova", "device_id": "server-1"}

    class FakeNetwork:
        def ips(self):
            return [FakeIp()]

        def get_port(self, port_id):
            assert port_id == "port-1"
            return FakePort()

        def get_network(self, network_id):
            class Network:
                name = "public-net"

                def to_dict(self):
                    return {"id": network_id, "name": "public-net"}
            return Network()

    class FakeIdentity:
        def projects(self):
            class Project:
                id = "proj-1"
                name = "production"
            return [Project()]

    class FakeConn:
        compute = FakeCompute()
        network = FakeNetwork()
        identity = FakeIdentity()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "_server_host", lambda server: "cmp-01")

    items = resource_helpers.get_floating_ips(auth=None)

    assert items[0]["instance_id"] == "server-1"
    assert items[0]["instance_name"] == "api-01"
    assert items[0]["compute_host"] == "cmp-01"


def test_get_project_quota_summary_reads_network_quota_cli_list_shape(monkeypatch):
    class FakeCompute:
        def get_quota_set(self, project_id, usage=True):
            assert project_id == "proj-1"
            return {}

    class FakeNetwork:
        def get_quota(self, project_id, details=True):
            assert project_id == "proj-1"
            return [{
                "Project ID": project_id,
                "Floating IPs": 50,
                "Networks": 100,
                "Ports": 500,
                "RBAC Policies": 10,
                "Routers": 10,
                "Security Groups": 100,
                "Security Group Rules": 500,
                "Subnets": 100,
                "Subnet Pools": -1,
            }]

    class FakeBlockStorage:
        def get_quota_set(self, project_id, usage=True):
            assert project_id == "proj-1"
            return {}

    class FakeConn:
        compute = FakeCompute()
        network = FakeNetwork()
        block_storage = FakeBlockStorage()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    quotas = resource_helpers.get_project_quota_summary("proj-1", auth=None)

    assert quotas["network"]["network"] == {"used": None, "limit": 100}
    assert quotas["network"]["subnet"] == {"used": None, "limit": 100}
    assert quotas["network"]["port"] == {"used": None, "limit": 500}
    assert quotas["network"]["router"] == {"used": None, "limit": 10}
    assert quotas["network"]["floatingip"] == {"used": None, "limit": 50}
    assert quotas["network"]["security_group"] == {"used": None, "limit": 100}
    assert quotas["network"]["security_group_rule"] == {"used": None, "limit": 500}


def test_get_project_inventory_backfills_compute_quota_usage_from_instances(monkeypatch):
    class FakeConn:
        current_project_id = "proj-1"
        current_user_id = None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(resource_helpers, "_project_names", lambda conn: {"proj-1": "production"})
    monkeypatch.setattr(
        resource_helpers,
        "_project_summary_header",
        lambda conn, project_id, project_names: {"project_id": project_id, "project_name": "production"},
    )
    monkeypatch.setattr(
        resource_helpers,
        "get_project_quota_summary",
        lambda project_id, auth=None: {
            "compute": {
                "instances": {"limit": 20, "used": None},
                "cores": {"limit": 80, "used": None},
                "ram": {"limit": 262144, "used": None},
            },
            "network": {},
            "block_storage": {},
        },
    )
    monkeypatch.setattr(
        resource_helpers,
        "get_project_instances",
        lambda auth=None, project_id="": [
            item for item in [
                {"project_id": "proj-1", "id": "vm-1", "vcpus": 4, "ram_mb": 8192},
                {"project_id": "proj-1", "id": "vm-2", "vcpus": 8, "ram_mb": 16384},
                {"project_id": "proj-2", "id": "vm-3", "vcpus": 2, "ram_mb": 4096},
            ] if not project_id or item["project_id"] == project_id
        ],
    )
    monkeypatch.setattr(web_server.openstack_ops, "get_project_vm_distribution", lambda auth=None: [])

    inventory = resource_helpers.get_project_inventory("proj-1", auth=None, section="quota")

    assert inventory["quotas"]["compute"]["instances"] == {"limit": 20, "used": 2}
    assert inventory["quotas"]["compute"]["cores"] == {"limit": 80, "used": 12}
    assert inventory["quotas"]["compute"]["ram"] == {"limit": 262144, "used": 24576}


def test_project_inventory_endpoint_forwards_section(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    captured = {}
    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "get_project_inventory",
        lambda project_id, auth=None, section="overview", family="": captured.update({"project_id": project_id, "section": section, "family": family}) or {"summary": {"project_id": project_id}},
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.get("/api/projects/proj-1/inventory?section=networking&family=ports")

    assert resp.status_code == 200
    assert captured == {"project_id": "proj-1", "section": "networking", "family": "ports"}


def test_project_quota_update_endpoint_requires_admin(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["member"])

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.post("/api/projects/proj-1/quota", json={"section": "compute", "resource": "instances", "limit": "24"})

    assert resp.status_code == 403
    assert resp.json()["error"] == "Quota modification requires the OpenStack 'admin' role."


def test_project_quota_update_endpoint_returns_fresh_inventory(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    captured = {}
    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "update_project_quota_limit",
        lambda project_id, section, resource, limit, auth=None: captured.update({
            "project_id": project_id,
            "section": section,
            "resource": resource,
            "limit": limit,
        }) or {
            "summary": {"project_id": project_id, "project_name": "production"},
            "quotas": {"compute": {"instances": {"used": 4, "limit": 24}}},
            "placement": None,
        },
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.post("/api/projects/proj-1/quota", json={"section": "compute", "resource": "instances", "limit": "24"})

    assert resp.status_code == 200
    assert captured == {"project_id": "proj-1", "section": "compute", "resource": "instances", "limit": "24"}
    assert resp.json()["inventory"]["quotas"]["compute"]["instances"]["limit"] == 24


def test_projects_endpoint_forwards_search(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    captured = {}

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "get_projects",
        lambda auth=None, search="": captured.update({"search": search}) or [],
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.get("/api/projects?search=prod")

    assert resp.status_code == 200
    assert captured["search"] == "prod"


def test_instance_detail_endpoint_returns_enriched_ports(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api.openstack_ops,
        "get_instance_network_detail",
        lambda instance_id, auth=None: {
            "id": instance_id,
            "name": "api-01",
            "compute_host": "cmp-12",
            "ports": [{
                "id": "port-1",
                "network_id": "net-1",
                "fixed_ips": ["10.0.0.5"],
                "floating_ips": ["198.51.100.10"],
            }],
        },
    )
    monkeypatch.setattr(
        resource_api.k8s_ops,
        "get_ovn_port_logical_switch",
        lambda port_id, network_id, auth=None: {"ls_name": "neutron-net-1", "port": {"type": "normal"}},
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.get("/api/instances/vm-1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["instance"]["id"] == "vm-1"
    assert body["instance"]["ports"][0]["ovn"]["ls_name"] == "neutron-net-1"


def test_load_balancer_detail_endpoint_returns_detail(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "get_load_balancer_detail",
        lambda lb_id, auth=None: {
            "id": lb_id,
            "name": "public-lb",
            "operating_status": "ONLINE",
            "provisioning_status": "ACTIVE",
            "vip_address": "10.10.0.5",
            "floating_ip": "198.51.100.25",
            "vip_port_id": "vip-port-1",
            "vip_subnet_id": "subnet-1",
            "vip_port": {
                "id": "vip-port-1",
                "name": "octavia-lb-vip",
                "status": "ACTIVE",
                "network_id": "net-1",
                "subnet_id": "subnet-1",
                "ip_address": "10.10.0.5",
                "mac_address": "fa:16:3e:11:22:33",
                "device_owner": "Octavia",
                "device_id": "lb-1",
                "project_id": "proj-1",
                "admin_state_up": True,
            },
            "project_id": "proj-1",
            "flavor_id": "amphora-small",
            "listeners": [{"id": "listener-1", "name": "https"}],
            "pools": [{"id": "pool-1", "name": "web-pool"}],
            "amphorae": [{"id": "amp-1", "role": "MASTER"}],
            "distinct_host_count": 2,
            "ha_summary": "HA spread OK",
        },
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.get("/api/load-balancers/lb-1")

    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["load_balancer"]["id"] == "lb-1"
    assert body["load_balancer"]["vip_port"]["id"] == "vip-port-1"
    assert body["load_balancer"]["vip_port"]["mac_address"] == "fa:16:3e:11:22:33"
    assert body["load_balancer"]["ha_summary"] == "HA spread OK"
    assert body["load_balancer"]["pools"][0]["id"] == "pool-1"


def test_load_balancer_detail_endpoint_times_out(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(resource_api, "_RESOURCE_DETAIL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(resource_api, "get_load_balancer_detail", lambda lb_id, auth=None: time.sleep(0.05) or {})

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.get("/api/load-balancers/lb-1")

    assert resp.status_code == 200
    assert "Timed out after 0s while loading load balancer details" in resp.json()["error"]


def test_security_groups_endpoint_returns_items(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "get_security_groups",
        lambda auth=None: [{
            "id": "sg-1",
            "name": "allow-all",
            "project_id": "proj-1",
            "project_name": "production",
            "rule_count": 2,
            "flagged_rule_count": 1,
            "attachment_port_count": 3,
            "attachment_instance_count": 2,
            "audit": {
                "severity": "critical",
                "score": 100,
                "findings": [{"severity": "critical", "summary": "any:any 0.0.0.0/0", "count": 1}],
                "has_open_world_ingress": True,
                "has_any_any_open_world": True,
                "has_unused": False,
            },
        }],
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.get("/api/security-groups")

    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["security_groups"][0]["audit"]["severity"] == "critical"
    assert body["security_groups"][0]["project_name"] == "production"


def test_security_group_detail_endpoint_returns_detail(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "get_security_group_detail",
        lambda group_id, auth=None: {
            "id": group_id,
            "name": "web-frontend",
            "project_id": "proj-1",
            "project_name": "production",
            "rule_count": 2,
            "flagged_rule_count": 1,
            "attachment_port_count": 1,
            "attachment_instance_count": 1,
            "audit": {
                "severity": "high",
                "score": 50,
                "findings": [{"severity": "high", "summary": "tcp:22 0.0.0.0/0", "count": 1}],
                "has_open_world_ingress": True,
                "has_any_any_open_world": False,
                "has_unused": False,
            },
            "rules": [{
                "id": "rule-1",
                "direction": "ingress",
                "protocol": "tcp",
                "port_range": "22",
                "remote_ip_prefix": "0.0.0.0/0",
                "audit": {"flagged": True, "severity": "high", "summary": "tcp:22 0.0.0.0/0"},
            }],
            "attachments": [{
                "port_id": "port-1",
                "device_owner": "compute:nova",
                "device_id": "server-1",
            }],
        },
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        resp = client.get("/api/security-groups/sg-1")

    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["security_group"]["id"] == "sg-1"
    assert body["security_group"]["rules"][0]["audit"]["summary"] == "tcp:22 0.0.0.0/0"
    assert body["security_group"]["attachments"][0]["device_id"] == "server-1"
