"""Remaining HTTP methods on catch-all routes (webhooks, project router) + root/health/credentials."""

from __future__ import annotations

import pytest

from .conftest import uniq, webhook_workflow
from .journeys._helpers import new_project

pytestmark = pytest.mark.integration


def test_root_health_credentials(api):
    assert api.raw("GET", "/").json()["status"] == "running"
    assert api.raw("GET", "/health").json()["status"] == "healthy"
    assert isinstance(api.get("/credentials").json(), list)


@pytest.mark.parametrize("method", ["GET", "PUT", "PATCH", "DELETE"])
def test_webhook_methods_by_id_and_path(api, method):
    path = uniq("m")
    wid = api.post("/workflows", webhook_workflow(path=path, method=method)).json()["id"]
    api.post(f"/workflows/{wid}/publish", {})
    r = api.raw(method, f"/webhook/{wid}", json={"x": 1} if method != "GET" else None)
    assert r.status_code == 200 and r.json()["status"] == "success"
    r = api.raw(method, f"/webhook/p/{path}", json={"x": 2} if method != "GET" else None)
    assert r.status_code == 200 and r.json()["status"] == "success"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_project_router_methods(api, method):
    P, S = new_project(api, "meth")
    api.post(f"/projects/{P}/routes", {"method": method, "path": "/thing/{id}", "handler": {"type": "workflow", "create": True}})
    api.post(f"/projects/{P}/routes", {"method": method, "path": "/", "handler": {"type": "workflow", "create": True}})
    r = api.raw(method, f"/api/p/{S}/thing/5")
    assert r.status_code == 200 and r.json() == {"id": "5"}
    assert api.raw(method, f"/api/p/{S}").status_code == 200
