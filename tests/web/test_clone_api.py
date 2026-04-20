from __future__ import annotations

from fastapi.testclient import TestClient

from draino.web import server as web_server
from draino.web.api import resources as resource_api


def test_instance_clone_endpoint_requests_action(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])

    captured = {}

    def fake_clone_instance(instance_id, *, new_name, clone_data_volumes=True, create_floating_ip=True, power_on=True, keep_metadata=True, auth=None):
        captured.update({
            "instance_id": instance_id,
            "new_name": new_name,
            "clone_data_volumes": clone_data_volumes,
            "create_floating_ip": create_floating_ip,
            "power_on": power_on,
            "keep_metadata": keep_metadata,
        })
        return {
            "source_instance_id": instance_id,
            "server_id": "vm-2",
            "server_name": new_name,
            "status": "created",
            "ports": ["port-2"],
            "volumes": ["vol-2"],
            "floating_ip_id": None,
            "warnings": [],
        }

    monkeypatch.setattr(resource_api, "clone_instance", fake_clone_instance)

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
        resp = client.post("/api/instances/vm-1/clone", json={
            "new_name": "vm-1-clone",
            "clone_data_volumes": True,
            "create_floating_ip": False,
            "power_on": False,
            "keep_metadata": True,
        })

    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["result"]["server_id"] == "vm-2"
    assert captured == {
        "instance_id": "vm-1",
        "new_name": "vm-1-clone",
        "clone_data_volumes": True,
        "create_floating_ip": False,
        "power_on": False,
        "keep_metadata": True,
    }
