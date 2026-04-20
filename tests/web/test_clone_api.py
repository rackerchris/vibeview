from __future__ import annotations

from types import SimpleNamespace

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

    def fake_clone_instance(instance_id, *, new_name, clone_data_volumes=True, create_floating_ip=True, power_on=True, keep_metadata=True, progress_cb=None, auth=None):
        captured.update({
            "instance_id": instance_id,
            "new_name": new_name,
            "clone_data_volumes": clone_data_volumes,
            "create_floating_ip": create_floating_ip,
            "power_on": power_on,
            "keep_metadata": keep_metadata,
        })
        if progress_cb:
            progress_cb("create_server", "Create Nova Server", "Creating the cloned server.")
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

    class ImmediateThread:
        def __init__(self, *, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr(resource_api, "threading", SimpleNamespace(Thread=ImmediateThread))
    resource_api._clone_status_by_instance.clear()

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
    assert body["result"]["accepted"] is True
    assert body["result"]["state"] == "running"
    assert body["result"]["step_key"] == "queued"
    assert captured == {
        "instance_id": "vm-1",
        "new_name": "vm-1-clone",
        "clone_data_volumes": True,
        "create_floating_ip": False,
        "power_on": False,
        "keep_metadata": True,
    }


def test_instance_clone_status_endpoint_reports_last_clone_state(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])

    def fake_clone_instance(instance_id, *, new_name, clone_data_volumes=True, create_floating_ip=True, power_on=True, keep_metadata=True, progress_cb=None, auth=None):
        if progress_cb:
            progress_cb("create_ports", "Clone Neutron Ports", "Created 1 port.")
        return {
            "source_instance_id": instance_id,
            "server_id": "vm-3",
            "server_name": new_name,
            "status": "created",
            "ports": ["port-3"],
            "volumes": [],
            "floating_ip_id": None,
            "warnings": [],
        }

    monkeypatch.setattr(resource_api, "clone_instance", fake_clone_instance)

    class ImmediateThread:
        def __init__(self, *, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr(resource_api, "threading", SimpleNamespace(Thread=ImmediateThread))
    resource_api._clone_status_by_instance.clear()

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
        resp = client.post("/api/instances/vm-1/clone", json={"new_name": "vm-1-clone"})
        assert resp.status_code == 200
        status_resp = client.get("/api/instances/vm-1/clone-status")

    body = status_resp.json()
    assert status_resp.status_code == 200
    assert body["error"] is None
    assert body["status"]["state"] == "completed"
    assert body["status"]["step_key"] == "completed"
    assert body["status"]["result"]["server_id"] == "vm-3"


def test_instance_clone_endpoint_rejects_second_running_clone(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    resource_api._clone_status_by_instance.clear()
    resource_api._set_clone_status("vm-1", {
        "state": "running",
        "source_instance_id": "vm-1",
        "new_name": "vm-1-clone",
        "step_key": "create_server",
        "step_label": "Create Nova Server",
        "detail": "Creating the clone server.",
        "result": None,
        "error": None,
    })

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
        resp = client.post("/api/instances/vm-1/clone", json={"new_name": "vm-1-clone-2"})

    body = resp.json()
    assert resp.status_code == 200
    assert body["result"] is None
    assert body["error"] == "A clone is already running for this source instance."


def test_instance_clone_status_reports_cleanup_details_on_failure(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])

    def fake_clone_instance(instance_id, *, new_name, clone_data_volumes=True, create_floating_ip=True, power_on=True, keep_metadata=True, progress_cb=None, auth=None):
        if progress_cb:
            progress_cb("create_server", "Create Nova Server", "Nova created the clone server record.")
        raise resource_api.CloneWorkflowError(
            "Nova build failed",
            failed_step_key="wait_server",
            failed_step_label="Wait For Server Build",
            failed_detail="Clone server vm-9 entered ERROR.",
            created_resources={"ports": [{"id": "port-9"}], "servers": [{"id": "vm-9"}]},
            cleanup_actions=[{"resource_type": "server", "resource_id": "vm-9", "state": "deleted"}],
            residual_resources=[{"resource_type": "port", "resource_id": "port-9"}],
        )

    monkeypatch.setattr(resource_api, "clone_instance", fake_clone_instance)

    class ImmediateThread:
        def __init__(self, *, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr(resource_api, "threading", SimpleNamespace(Thread=ImmediateThread))
    resource_api._clone_status_by_instance.clear()

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
        resp = client.post("/api/instances/vm-1/clone", json={"new_name": "vm-1-clone"})
        assert resp.status_code == 200
        status_resp = client.get("/api/instances/vm-1/clone-status")

    body = status_resp.json()
    assert body["status"]["state"] == "failed"
    assert body["status"]["step_key"] == "wait_server"
    assert body["status"]["cleanup_actions"] == [{"resource_type": "server", "resource_id": "vm-9", "state": "deleted"}]
    assert body["status"]["residual_resources"] == [{"resource_type": "port", "resource_id": "port-9"}]
    assert len(body["status"]["timeline"]) >= 2


def test_instance_clone_cleanup_endpoint_deletes_leftover_resource(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])
    deleted_ports = []

    class FakeNetwork:
        @staticmethod
        def delete_port(resource_id, ignore_missing=True):
            deleted_ports.append((resource_id, ignore_missing))

    class FakeConn:
        network = FakeNetwork()

        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    resource_api._clone_status_by_instance.clear()
    resource_api._set_clone_status("vm-1", {
        "state": "failed",
        "source_instance_id": "vm-1",
        "new_name": "vm-1-clone",
        "step_key": "cleanup",
        "step_label": "Rollback Clone Resources",
        "detail": "Rollback left one port behind.",
        "result": None,
        "error": "Nova build failed",
        "timeline": [],
        "cleanup_actions": [],
        "residual_resources": [{"resource_type": "port", "resource_id": "port-9"}],
    })

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
        resp = client.post("/api/instances/vm-1/clone-cleanup", json={"resource_type": "port", "resource_id": "port-9"})
        assert resp.status_code == 200
        status_resp = client.get("/api/instances/vm-1/clone-status")

    body = resp.json()
    status = status_resp.json()["status"]
    assert body["error"] is None
    assert deleted_ports == [("port-9", True)]
    assert status["residual_resources"] == []
    assert status["cleanup_actions"][-1]["state"] == "deleted_by_operator"
