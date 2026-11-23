"""Shared fixtures for the schema-evolution / plan-conformance tests: a fixture plan body, ``load_approved`` patched to return it,
and stand-ins for the pure ``project_plan`` / ``schema_migrations`` helpers when those modules are not on disk yet."""

from __future__ import annotations

import sys
import types
from typing import Any

from src.services.backend_templates import coverage

PLAN_COLUMNS = [
    {"name": "test_id", "type": "integer", "required": True},
    {"name": "status", "type": "string", "required": True, "enum": ["queued", "running", "passed"], "default": "queued"},
    {"name": "run_key", "type": "string", "unique": True},
]


def plan_body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "v": 1, "title": "t",
        "data": {"tables": [{"name": "runs", "owner": "runs", "writers": [], "columns": PLAN_COLUMNS},
                            {"name": "tests", "owner": "tests_library", "writers": ["runs"],
                             "columns": [{"name": "title", "type": "string", "required": True}]},
                            {"name": "audit_log", "owner": "common", "writers": [],
                             "columns": [{"name": "event", "type": "string"}]}]},
        "modules": [{"key": "common", "kind": "shared", "helpers": [{"name": "audit", "signature": "audit(event)"}]},
                    {"key": "runs", "kind": "routes", "owns": ["runs"]},
                    {"key": "tests_library", "kind": "routes", "owns": ["tests"]}],
        "routes": [{"key": "POST /runs", "module": "runs", "request": {"fields": [{"name": "test_ids", "type": "json", "required": True}]},
                    "response": {"fields": [{"name": "id", "type": "integer"}, {"name": "job_id", "type": "string"}]}},
                   {"key": "GET /runs/{id}", "module": "runs", "response": {"fields": [{"name": "id", "type": "integer"}]}},
                   {"key": "GET /tests", "module": "tests_library"}],
        "environments": [{"name": "dev", "base_url": "http://localhost:4590", "live": False}],
        "secrets": [{"name": "BANK_USER", "environments": ["dev"], "allowed_hosts": ["localhost"]}],
        "integrations": [],
        "apps": [],
    }
    body.update(over)
    return body


# ── stand-ins (only used when WP-A's real modules are not importable) ───────────────────────────────────────────────


def _fake_diff_table(current: list[dict[str, Any]], planned: dict[str, Any], row_count: int) -> dict[str, Any]:
    cur = {c["name"]: c for c in current}
    want = {c["name"]: c for c in planned.get("columns") or []}
    ops: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for n, c in want.items():
        if n not in cur:
            if c.get("required") and "default" not in c and row_count:
                refused.append({"op": "required_without_default_on_nonempty", "column": n, "reason": f"{n} is required, no default"})
            else:
                ops.append({"op": "add_column", "column": c})
            continue
        k = cur[n]
        if k["type"] != c["type"]:
            refused.append({"op": "type_change", "column": n, "reason": f"{n}: {k['type']} -> {c['type']}"})
            continue
        add = [v for v in c.get("enum") or [] if v not in (k.get("enum") or [])]
        if add:
            ops.append({"op": "add_enum_values", "column": n, "values": add})
        if c.get("unique") and not k.get("unique"):
            ops.append({"op": "set_unique", "column": n})
        if c.get("index") and not k.get("index"):
            ops.append({"op": "set_index", "column": n})
    for n in cur:
        if n not in want:
            refused.append({"op": "drop_column", "column": n, "reason": f"{n} is not in the target"})
    return {"ops": ops, "refused": refused}


def _fake_table_plan(body: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((t for t in (body.get("data") or {}).get("tables") or [] if t.get("name") == name), None)


def _fake_route_contract(body: dict[str, Any], key: str) -> dict[str, Any] | None:
    return next((r for r in body.get("routes") or [] if r.get("key") == key), None)


def install(monkeypatch, body: dict[str, Any] | None) -> None:
    """Make ``project_plan.load_approved`` return ``{version: 1, body}`` (or None for no plan), importing the real helper modules
    when they exist and substituting minimal stand-ins when they do not."""
    for modname, attrs in (("schema_migrations", {"diff_table": _fake_diff_table}),
                           ("project_plan", {"table_plan": _fake_table_plan, "route_contract": _fake_route_contract})):
        full = f"src.services.{modname}"
        try:
            __import__(full)
        except ImportError:
            mod = types.ModuleType(full)
            for k, v in attrs.items():
                setattr(mod, k, v)
            monkeypatch.setitem(sys.modules, full, mod)
            import src.services as pkg
            monkeypatch.setattr(pkg, modname, mod, raising=False)

    async def load_approved(session, project_id):  # noqa: ANN001
        return {"version": 1, "body": body} if body is not None else None

    monkeypatch.setattr(sys.modules["src.services.project_plan"], "load_approved", load_approved, raising=False)
    assert coverage  # imported for tests that need route keys
