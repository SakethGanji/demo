"""Suggested tests and "save as test" (Stage 3A, CONTRACTS-STAGE3A §7). Nothing here saves anything.

* ``suggest_tests`` runs the recipe of every node (or one node) and drops drafts the workflow already has.
* ``replay_from_execution`` turns one past run into a ``replay`` draft: the trigger output as input, the
  recorded outputs of outside steps as mocks, and expectations read from what the run did.
"""

from __future__ import annotations

from typing import Any

from ..core.exceptions import WorkflowNotFoundError
from ..engine import shapes as sh
from ..engine.effects import effect_of
from ..engine.graph import TRIGGER_NODE_TYPES
from ..engine.test_recipes import SuggestContext, stable_id, suggest_for_workflow
from ..repositories.workflow_repository import workflow_to_definition
from ..schemas.workflow import normalize_tests
from .api_catalog import project_operations
from .check_lookups import build_lookups
from .workflow_checker import check_workflow

_HARD_SOURCES = ("declared", "derived", "spec")


class AuthoringError(Exception):
    """A request the authoring routes refuse (``status`` is the HTTP status)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


async def _load(repo: Any, workflow_id: str) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """``(stored, definition, shapes_by_node)`` of the stored draft."""
    stored = await repo.get(workflow_id)
    if not stored:
        raise WorkflowNotFoundError(workflow_id)
    definition = workflow_to_definition(stored.workflow)
    lookups = await build_lookups(repo._session, stored.project_id, None, team_id=getattr(stored, "team_id", None) or "default",
                                  workflow_id=stored.id, definition=definition)
    shapes = check_workflow(definition, lookups=lookups).get("shapes_by_node", {})
    return stored, definition, shapes


async def suggest_tests(repo: Any, workflow_id: str, node: str | None = None) -> list[dict[str, Any]]:
    """Draft tests for the workflow (or one ``node``), without the ones whose id the workflow already has."""
    stored, definition, shapes = await _load(repo, workflow_id)
    if node and not any(n.get("name") == node for n in definition.get("nodes") or []):
        raise AuthoringError(404, f"Node '{node}' not found in workflow")
    ops = await project_operations(repo._session, stored.project_id)
    ctx = SuggestContext(definition, api_operation=lambda spec_id, op_id: (ops.get(spec_id) or {}).get(op_id))
    have = {t["id"] for t in normalize_tests(definition.get("tests"))}
    return [t for t in suggest_for_workflow(definition, shapes, ctx, node) if t["id"] not in have]


# ── save as test ─────────────────────────────────────────────────────────────────────────────


def _recorded_mock(node_type: str, items: list[Any], error: str | None) -> dict[str, Any] | None:
    if error is not None:
        return {"error": {"message": error}}
    if node_type == "HttpRequest" and items and isinstance(items[0], dict) and "statusCode" in items[0]:
        first = items[0]
        http: dict[str, Any] = {"status": int(first["statusCode"])}
        if first.get("body") not in (None, ""):
            http["body"] = first["body"]
        if isinstance(first.get("headers"), dict) and first["headers"]:
            http["headers"] = {str(k): str(v) for k, v in first["headers"].items()}
        return {"http": http}
    if node_type == "Approval" and items and isinstance(items[0], dict):
        # a decided approval leaves on the port of its decision: replay the same one (Stage 5)
        decision = (items[0].get("approval") or {}).get("decision") if isinstance(items[0].get("approval"), dict) else None
        if decision in ("approved", "rejected", "timed_out"):
            return {"items": items, "port": decision}
    return {"items": items}


def _taken_ports(node_type: str, metrics: dict[str, Any]) -> dict[str, int | None]:
    """``{port: item count or None when unknown}`` for the ports an If / Switch sent items to."""
    active = [p for p in metrics.get("activeOutputs") or []]
    if node_type == "If":
        counts = {"true": metrics.get("trueCount"), "false": metrics.get("falseCount")}
        return {p: c for p, c in counts.items() if isinstance(c, int) and c > 0}
    total = metrics.get("outputItemCount")
    if len(active) == 1 and isinstance(total, int):
        return {active[0]: total}
    return {p: 1 if total == len(active) else None for p in active}


def build_replay_test(
    definition: dict[str, Any], shapes: dict[str, Any], record: Any, name: str | None = None,
) -> dict[str, Any]:
    nodes = [n for n in definition.get("nodes") or [] if isinstance(n, dict) and n.get("name")]
    by_name = {n["name"]: n for n in nodes}
    node_data = record.node_data or {}
    metrics = record.node_metrics or {}
    errors = {e.node_name: e.error for e in record.errors or []}

    trigger = next((n["name"] for n in nodes if n.get("type") in TRIGGER_NODE_TYPES and n["name"] in node_data), None)
    if trigger is None:
        raise AuthoringError(409, "This run has no trigger output to replay (it was a partial run)")
    items = [i.json for i in node_data[trigger]]

    mocks: dict[str, Any] = {}
    for n in nodes:
        n_name = n["name"]
        if effect_of(n.get("type", "")) == "none" or n.get("type") in TRIGGER_NODE_TYPES or n.get("disabled"):
            continue
        if n_name not in node_data and n_name not in errors:
            continue
        mock = _recorded_mock(n["type"], [i.json for i in node_data.get(n_name, [])], errors.get(n_name))
        if mock is not None:
            mocks[n_name] = mock

    ran = [n["name"] for n in nodes if n["name"] in node_data or n["name"] in errors]
    sources = {c.get("source_node") for c in definition.get("connections") or [] if isinstance(c, dict)}
    expect: list[dict[str, Any]] = []
    for n in nodes:
        n_name, t = n["name"], n.get("type")
        if n_name not in ran:
            continue
        if t in ("If", "Switch"):
            for port, count in _taken_ports(t, metrics.get(n_name) or {}).items():
                if count is not None:
                    expect.append({"node": n_name, "port": port, "op": "count", "value": count})
    for n_name in ran:
        if n_name in errors:
            expect.append({"node": n_name, "op": "status", "value": "error"})
        elif n_name not in sources:
            expect.append({"node": n_name, "op": "status", "value": "success"})
    for n_name in ran:
        if n_name in errors or n_name == trigger:
            continue
        outputs = (shapes.get(n_name) or {}).get("outputs") or {}
        ports = list((metrics.get(n_name) or {}).get("activeOutputs") or ["main"])
        for port in ports:
            shape = outputs.get(port)
            if isinstance(shape, dict) and not sh.is_unknown(shape) and shape.get("x-source") in _HARD_SOURCES:
                expect.append({"node": n_name, "port": port, "op": "shape"})
    title = (name or "").strip() or f"Replay of run {record.id}"
    return {
        "id": stable_id("replay", record.id, items, mocks, expect),
        "name": title[:120], "description": f"Recorded from execution {record.id}",
        "target": {"kind": "workflow"}, "input": {"items": items}, "mocks": mocks, "expect": expect,
        "category": "ext_error" if errors else "normal", "status": "draft", "locked": False, "source": "replay",
    }


async def replay_from_execution(repo: Any, execution_repo: Any, workflow_id: str, execution_id: str, name: str | None = None) -> dict[str, Any]:
    _, definition, shapes = await _load(repo, workflow_id)
    record = await execution_repo.get(execution_id)
    if record is None or record.workflow_id != workflow_id:
        raise AuthoringError(404, f"Execution not found: {execution_id}")
    return build_replay_test(definition, shapes, record, name)
