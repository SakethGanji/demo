"""A small approved plan and the seam the builders read it through, for the plan-consuming builder tests.

``install(monkeypatch, body)`` makes ``project_plan.load_approved`` return the plan. When the plan service is not importable
(the package lands separately) a minimal stand-in with the same consumer helpers is registered instead."""

from __future__ import annotations

import sys
import types
from typing import Any

BODY: dict[str, Any] = {
    "v": 1, "title": "QA Workbench",
    "requirements": {"answers": [{"id": "a1", "topic": "credentials", "question": "Secrets?", "answer": "BANK_USER, BANK_PASS"}],
                     "acceptance": [{"id": "ac1", "text": "Run creates a job", "covers": ["route:POST /runs"]}]},
    "data": {"tables": [
        {"name": "tests", "owner": "tests_library", "writers": [],
         "columns": [{"name": "title", "type": "string", "required": True}, {"name": "status", "type": "string"}]},
        {"name": "runs", "owner": "runs", "writers": ["triage"],
         "columns": [{"name": "test_id", "type": "integer", "required": True, "ref": "tests.id"},
                     {"name": "status", "type": "string", "enum": ["queued", "done"]},
                     {"name": "job_id", "type": "string"}]},
        {"name": "audit_log", "owner": "common", "writers": ["runs"],
         "columns": [{"name": "event", "type": "string", "required": True}]},
    ]},
    "modules": [
        {"key": "common", "kind": "shared", "helpers": [
            {"name": "audit", "signature": "audit(event, entity, entity_id, caller, detail=None) -> dict", "purpose": "audit row"}]},
        {"key": "tests_library", "kind": "routes", "depends_on": ["common"], "owns": ["tests"]},
        {"key": "runs", "kind": "routes", "depends_on": ["common", "tests_library"], "owns": ["runs"], "reads": ["tests"]},
        {"key": "triage", "kind": "routes", "depends_on": ["common", "runs"], "owns": []},
    ],
    "routes": [
        {"key": "GET /tests", "module": "tests_library", "purpose": "list tests", "pagination": True},
        {"key": "POST /runs", "module": "runs", "purpose": "start a run",
         "request": {"fields": [{"name": "test_ids", "type": "json", "required": True}]},
         "response": {"fields": [{"name": "id", "type": "integer"}, {"name": "job_id", "type": "string"}]},
         "errors": [{"status": 409, "code": "illegal_transition"}], "idempotency": "none"},
        {"key": "GET /runs", "module": "runs", "purpose": "list runs", "pagination": True},
        {"key": "GET /runs/{id}", "module": "runs", "purpose": "one run"},
    ],
    "apps": [{"key": "qa_workbench", "name": "QA Workbench", "app_id": None,
              "screens": [{"name": "Library", "purpose": "pick tests", "routes": ["GET /tests", "POST /runs"],
                           "states": {"empty": "no tests", "loading": "spinner", "error": "banner", "live": "polls"},
                           "journeys": ["select tests, Run"]}]}],
    "nfr": {"pagination": {"shape": "{rows,total,limit,offset}"}, "errors": {"shape": "{error, message}"}},
    "build_order": ["common", "tests_library", "runs", "triage", "app:qa_workbench"],
}


def _stand_in() -> types.ModuleType:
    """The consumer helpers of the plan service, reduced to what the builders call."""
    m = types.ModuleType("src.services.project_plan")

    async def load_approved(session, project_id):
        return None

    def module_spec(body, key):
        tables = {t["name"]: t for t in body["data"]["tables"]}
        mod = next(x for x in body["modules"] if x["key"] == key)
        shared = next((x for x in body["modules"] if x.get("kind") == "shared"), {})
        return {"summary": body.get("title", ""),
                "entities": [{"name": n, "fields": [{k: v for k, v in c.items()} for c in tables[n]["columns"]]}
                             for n in mod.get("owns") or [] if n in tables],
                "workflows": [], "roles": [], "external_systems": [], "acceptance_criteria": [], "limitations": [],
                "answered": [{"id": a["id"], "question": a["question"], "answer": a["answer"]}
                             for a in body["requirements"]["answers"]],
                "routes": [r for r in body["routes"] if r["module"] == key],
                "helpers": shared.get("helpers") if shared.get("key") != key else []}

    def build_order(body):
        return list(body["build_order"])

    def route_contract(body, key):
        return next((r for r in body["routes"] if r["key"] == key), None)

    def app_brief(body, key):
        app = next(a for a in body["apps"] if a["key"] == key)
        scr = app["screens"]
        routes = []
        for s in scr:
            for r in s["routes"]:
                meth, path = r.split(" ", 1)
                if not any(x["method"] == meth and x["path"] == path for x in routes):
                    c = route_contract(body, r) or {}
                    routes.append({"method": meth, "path": path, "purpose": c.get("purpose", "")})
        return {"users": "QA engineers", "screens": [{"name": s["name"], "purpose": s["purpose"]} for s in scr],
                "journeys": [j for s in scr for j in s["journeys"]], "routes": routes,
                "states": scr[0]["states"], "acceptance_criteria": ["Run creates a job"], "assumptions": []}

    async def bind_app(session, project_id, app_key, app_id):
        return None

    m.__dict__.update(load_approved=load_approved, module_spec=module_spec, build_order=build_order,
                      route_contract=route_contract, app_brief=app_brief, bind_app=bind_app)
    return m


def install(monkeypatch, body: dict[str, Any] | None = None, version: int = 3) -> Any:
    """Make the project's approved plan ``body``; returns the plan module."""
    import importlib

    try:
        pp = importlib.import_module("src.services.project_plan")
    except ImportError:
        pp = _stand_in()
        monkeypatch.setitem(sys.modules, "src.services.project_plan", pp)
        import src.services as pkg
        monkeypatch.setattr(pkg, "project_plan", pp, raising=False)

    async def load_approved(session, project_id):
        return {"version": version, "body": body or BODY}

    async def bind_app(session, project_id, app_key, app_id):
        return None

    monkeypatch.setattr(pp, "load_approved", load_approved, raising=False)
    monkeypatch.setattr(pp, "bind_app", bind_app, raising=False)
    return pp
