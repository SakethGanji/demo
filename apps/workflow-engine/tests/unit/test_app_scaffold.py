"""App scaffold: the plan's app brief + the project's resource routes become a complete app with no model call; the model is
called only for screens no template fits. Fake routes and a scripted fake LLM; the output is syntax-checked with esbuild and
type-checked with tsc together with the kit and the generated client."""
import asyncio
import copy
import json
import time
from types import SimpleNamespace

import pytest

from src.engine.llm_provider import LLMResponse, ToolCall
from src.services import app_builder_ai_service as builder
from src.services import app_scaffold
from src.services.api_route_service import path_shape
from src.services.app_validate import unknown_route_calls, unused_planned_routes
from src.services.client_codegen import generate_client
from tests.unit.builder_plan_fixture import BODY
from tests.unit.test_app_builder_plan_brief import _service

TABLE = {"name": "supply_requests", "owner": "requests", "writers": [], "audit": True, "columns": [
    {"name": "item", "type": "string", "required": True}, {"name": "quantity", "type": "integer", "required": True},
    {"name": "reason", "type": "string"}, {"name": "status", "type": "string", "enum": ["pending", "approved", "rejected"],
                                           "default": "pending"},
    {"name": "decision_comment", "type": "string"}, {"name": "approved_at", "type": "datetime"}, {"name": "approved_by", "type": "string"}],
    "state_machine": {"field": "status", "initial": "pending", "final": ["approved", "rejected"], "transitions": [
        {"from": "pending", "to": "approved", "route": "POST /supply_requests/{id}/approve"},
        {"from": "pending", "to": "rejected", "route": "POST /supply_requests/{id}/reject"}]}}

ROUTES = [("GET", "/supply_requests", "listSupplyRequests"), ("POST", "/supply_requests", "createSupplyRequest"),
          ("GET", "/supply_requests/{id}", "getSupplyRequest"), ("PATCH", "/supply_requests/{id}", "updateSupplyRequest"),
          ("POST", "/supply_requests/{id}/approve", "approveSupplyRequest"),
          ("POST", "/supply_requests/{id}/reject", "rejectSupplyRequest")]


def _plan(extra_screens=()):
    key = lambda m, p: f"{m} {p}"  # noqa: E731
    body = copy.deepcopy(BODY)
    body["data"]["tables"] = [TABLE]
    body["modules"] = [{"key": "requests", "kind": "routes", "owns": ["supply_requests"]}]
    body["routes"] = [{"key": key(m, p), "module": "requests", "purpose": n} for m, p, n in ROUTES]
    body["build_order"] = ["requests", "app:office"]
    body["requirements"] = {"answers": [], "acceptance": [{"id": "ac1", "text": "A manager can approve or reject a request", "covers": ["app:office"]}]}
    body["apps"] = [{"key": "office", "name": "Office Supplies", "screens": [
        {"name": "Request supplies", "purpose": "ask for an item", "routes": ["POST /supply_requests"], "states": {}, "journeys": ["fill the form"]},
        {"name": "My requests", "purpose": "see what was asked and its status", "routes": ["GET /supply_requests", "GET /supply_requests/{id}"],
         "states": {"empty": "No requests yet. Use Request supplies."}, "journeys": ["open a request"]},
        {"name": "Approvals", "purpose": "managers approve or reject", "routes": ["POST /supply_requests/{id}/approve", "POST /supply_requests/{id}/reject"],
         "states": {}, "journeys": ["approve one"]},
        {"name": "Dashboard", "purpose": "counts by status", "routes": [], "states": {}, "journeys": ["look at the counts"]},
        *extra_screens]}]
    return body


def _routes():
    return [SimpleNamespace(method=m, path=p, path_shape=path_shape(p), name=n, handler_type="python", auth="app_token",
                            response_schema={"type": "object"}) for m, p, n in ROUTES]


class _Store:
    def __init__(self):
        self.items = _routes()

    async def rows(self, project_id):
        return list(self.items)


def _run(svc, files):
    svc._started = time.perf_counter()
    async def go():
        return [json.loads(e["data"]) async for e in svc._run_agent_loop(files, [], "Build the office supplies app. " * 25, None)]
    return asyncio.run(go())


def _client_file():
    routes = [{"name": n, "method": m, "path": p, "auth": "app_token", "description": "",
               "request_schema": {}, "response_schema": {"type": "object"}} for m, p, n in ROUTES]
    return {"path": "api/client.ts", "content": generate_client("office", routes, 1, "h")}


def test_scaffold_binds_pages_to_resource_routes_without_a_model_call(monkeypatch):
    svc, call, _ = _service(monkeypatch, [], plan_body=_plan(), project_routes=_Store())
    files: list = []
    builder.inject_kit(files)
    events = _run(svc, files)
    assert call.await_count == 0                                    # nothing for the model to do
    by = {f["path"]: f["content"] for f in files}
    assert {"App.tsx", "index.css", "api/resources.ts", "views/RequestSupplies.tsx", "views/MyRequests.tsx", "views/Approvals.tsx",
            "views/Dashboard.tsx"} <= set(by)
    assert "<FormPage" in by["views/RequestSupplies.tsx"] and "<ListDetailLayout" in by["views/MyRequests.tsx"]
    assert "<InboxLayout" in by["views/Approvals.tsx"] and "<DashboardLayout" in by["views/Dashboard.tsx"]
    assert "No requests yet" in by["views/MyRequests.tsx"] and "go(\"my-requests\")" in by["views/RequestSupplies.tsx"]
    res = by["api/resources.ts"]
    for op in ("listSupplyRequests", "createSupplyRequest", "getSupplyRequest", "updateSupplyRequest", "approveSupplyRequest",
               "rejectSupplyRequest"):
        assert f"api.{op}(" in res
    assert 'case "approved"' in res and 'case "rejected"' in res and "registerResources" in res
    assert "--primary:" in by["index.css"]
    for p in ("views/MyRequests.tsx", "views/Approvals.tsx", "views/Dashboard.tsx"):          # no plumbing in app code
        assert "fetch(" not in by[p] and "useEffect" not in by[p] and "useState" not in by[p]
    assert len(by["views/Approvals.tsx"].splitlines()) < 12
    stats = next(e for e in events if e["type"] == "stats")
    assert stats["model_calls"] == 0 and stats["scaffolded"] is True and stats["wall_s"] >= 0
    assert any(e["type"] == "scaffold" for e in events)
    print("model calls in the office-supply scaffold build:", stats["model_calls"], "wall_s", stats["wall_s"])


def test_scaffold_validates(monkeypatch):
    svc, call, _ = _service(monkeypatch, [], plan_body=_plan(), project_routes=_Store())
    files: list = [_client_file()]
    builder.inject_kit(files)
    _run(svc, files)
    plain = [{"path": f["path"], "content": f["content"]} for f in files]
    assert asyncio.run(builder._generated_syntax_errors(plain)) is None
    assert unknown_route_calls(plain) == []
    brief = svc._brief_data
    assert unused_planned_routes(brief["routes"], plain) == []
    assert builder.screen_gaps(brief["screens"], plain) == [] if hasattr(builder, "screen_gaps") else True
    monkeypatch.setattr(builder, "KIT_PREFIX", "never/")           # type-check the kit and the scaffold, not only the pages
    errors = asyncio.run(builder.typecheck_errors(plain))
    if builder._typecheck_toolchain() is None:
        pytest.skip("no tsc toolchain")
    assert errors is None, errors


def test_model_is_called_only_for_screens_no_template_fits(monkeypatch):
    extra = [{"name": "Supplier directory", "purpose": "contact details of suppliers", "routes": [], "states": {}, "journeys": ["find a supplier"]}]
    page = ("/** Supplier directory */\nimport { Page, Panel } from '../kit'\nexport interface Props { go: (screen: string) => void }\n"
            "export default function SupplierDirectory({ go }: Props) { return <Page title=\"Suppliers\"><Panel>Acme</Panel></Page> }\n")
    write = LLMResponse(tool_calls=[ToolCall(id="w", name="write_files", args={"files": [{"path": "views/SupplierDirectory.tsx", "content": page}]})])
    svc, call, _ = _service(monkeypatch, [write], plan_body=_plan(extra), project_routes=_Store())
    files: list = []
    builder.inject_kit(files)
    events = _run(svc, files)
    assert call.await_count == 1                                     # one call, for the one custom screen
    by = {f["path"]: f["content"] for f in files}
    assert "Acme" in by["views/SupplierDirectory.tsx"] and "is not built yet" not in by["views/SupplierDirectory.tsx"]
    assert "ListDetailLayout" in by["views/MyRequests.tsx"]
    assert next(e for e in events if e["type"] == "stats")["model_calls"] == 1
    print("model calls with one custom screen:", call.await_count)
