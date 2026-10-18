"""The scorecard of a workflow (Stage 3A, CONTRACTS-STAGE3A §7).

``score(workflow_repo, stored, definition=None, *, lookups=None, mutations=True, repeat=0, seed=0) -> Scorecard``

* **checker**: error and warning counts of the mistake checker.
* **tests**: the summary of one test run.
* **coverage**: every output port of If/Switch (and the ``error`` port of ``on_error="error_output"`` nodes) and
  whether some test made it emit; plus the number of empty grid cells.
* **mutation**: planted bugs (``services/mutations.py``). A bug is *caught* when a test that passes on the original
  fails on the mutated copy. Mutants run with ``persist=False`` (nothing is written) and share one runner setup.
* **repeatability**: the tests run ``repeat`` times; results are compared ignoring timings and execution ids.
* **gaps**: grid cells nothing covers.
* **bar**: ``"14/14 passing · 1 gap · 19/20 planted bugs caught"``.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from ..engine.node_registry import node_registry
from ..repositories.workflow_repository import definition_to_workflow, workflow_to_definition
from ..schemas.workflow import normalize_tests
from . import test_runner as tr
from .mutations import generate_mutants


def format_bar(summary: dict[str, Any], gaps: int, mutation: dict[str, Any] | None) -> str:
    """The test bar text."""
    parts = [f"{summary['passed']}/{summary['total']} passing", f"{gaps} {'gap' if gaps == 1 else 'gaps'}"]
    if mutation is not None:
        parts.append(f"{mutation['caught']}/{mutation['total']} planted {'bug' if mutation['total'] == 1 else 'bugs'} caught")
    return " · ".join(parts)


def branches_of(definition: dict[str, Any]) -> list[tuple[str, str]]:
    """Every (node, port) that counts as a branch: If/Switch outputs and ``error`` of ``error_output`` nodes."""
    out: list[tuple[str, str]] = []
    for n in definition.get("nodes") or []:
        if not isinstance(n, dict) or n.get("disabled") or not n.get("name"):
            continue
        ports: list[str] = []
        if n.get("type") in ("If", "Switch") and node_registry.has(n["type"]):
            try:
                ports = list(node_registry.get(n["type"]).output_ports(n.get("parameters") or {}))
            except Exception:
                ports = ["true", "false"] if n["type"] == "If" else []
        if n.get("on_error") == "error_output":
            ports.append("error")
        out.extend((n["name"], p) for p in ports)
    return out


def _coverage(definition: dict[str, Any], report: dict[str, Any], gaps: list[dict[str, Any]]) -> dict[str, Any]:
    taken: set[tuple[str, str]] = set()
    for r in report["results"]:
        for node, ports in (r.get("ports_taken") or {}).items():
            taken.update((node, p) for p in ports)
    branches = branches_of(definition)
    untaken = [{"node": n, "port": p} for n, p in branches if (n, p) not in taken]
    return {
        "branches_total": len(branches), "branches_taken": len(branches) - len(untaken),
        "untaken": untaken, "grid_gaps": len(gaps),
    }


def _gaps(report: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for row in report["grid"]["rows"]:
        for cat, cell in row["categories"].items():
            if cell["status"] == "none":
                out.append({"node": row["node"], "category": cat, "reason": f"No test covers {cat} cases for {row['node']}"})
    return out


def _stable(result: dict[str, Any]) -> str:
    r = {k: v for k, v in result.items() if k not in ("duration_ms", "execution_id")}
    return json.dumps(r, sort_keys=True, default=str)


async def _run_all(
    workflow_repo: Any, stored: Any, definition: dict[str, Any], setup: Any, *, only: set[str] | None = None,
    stop_on_fail: bool = False,
) -> list[dict[str, Any]]:
    """Run tests of ``definition`` with an existing setup, never persisting. ``only`` limits to those ids."""
    workflow = definition_to_workflow(definition, stored.name, stored.id, stored.workflow.description)
    tests = normalize_tests(definition.get("tests"))
    defaults = definition.get("test_defaults") or {}
    results = []
    for t in tests:
        if only is not None and t["id"] not in only:
            continue
        res = await tr._run_one(workflow_repo, stored, workflow, definition, t, defaults, setup, False)
        results.append(res)
        if stop_on_fail and res["status"] != "passed":
            break
    return results


async def failing_approved_tests(workflow_repo: Any, stored: Any, definition: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """``[{test_id, name, failures}]`` of the approved tests that don't pass. Nothing is persisted (publish and release use it)."""
    if definition is None:
        definition = workflow_to_definition(stored.workflow)
    ids = [t["id"] for t in normalize_tests(definition.get("tests")) if t.get("status") == "approved"]
    if not ids:
        return []
    report = await tr.run_tests(workflow_repo, stored, definition, ids=ids, persist=False)
    return [{"test_id": r["test_id"], "name": r["name"], "failures": r["failures"]}
            for r in report["results"] if r["status"] != "passed"]


async def score(
    workflow_repo: Any, stored: Any, definition: dict[str, Any] | None = None, *, lookups: Any = None,
    mutations: bool = True, repeat: int = 0, seed: int = 0,
) -> dict[str, Any]:
    """The scorecard of ``stored`` (or of the unsaved ``definition``). Never writes to the database."""
    from .workflow_checker import check_workflow

    if definition is None:
        definition = workflow_to_definition(stored.workflow)
    else:
        definition = copy.deepcopy(definition)
    repeat = max(0, min(int(repeat or 0), 20))

    if lookups is None:
        from .check_lookups import build_lookups
        lookups = await build_lookups(workflow_repo._session, stored.project_id, None,
                                      team_id=getattr(stored, "team_id", None) or "default")
    findings = check_workflow(definition, lookups=lookups).get("findings", [])
    checker = {
        "errors": sum(1 for f in findings if f.get("severity") == "error"),
        "warnings": sum(1 for f in findings if f.get("severity") == "warning"),
    }

    workflow = definition_to_workflow(definition, stored.name, stored.id, stored.workflow.description)
    tests = normalize_tests(definition.get("tests"))
    setup = await tr._setup(workflow_repo, stored, definition, workflow) if tests else None
    results = await _run_all(workflow_repo, stored, definition, setup) if tests else []
    report = tr.build_report(workflow, tests, results)
    gaps = _gaps(report)

    mutation = None
    if mutations:
        passing = {r["test_id"] for r in results if r["status"] == "passed"}
        mutants = generate_mutants(definition, seed=seed)
        survived = []
        caught = 0
        for m in mutants:
            res = await _run_all(workflow_repo, stored, m["definition"], setup, only=passing, stop_on_fail=True) if passing else []
            if any(r["status"] != "passed" for r in res):
                caught += 1
            else:
                survived.append({k: m[k] for k in ("id", "operator", "node", "description")})
        mutation = {"total": len(mutants), "caught": caught, "survived": survived}

    repeatability = None
    if repeat:
        first = {r["test_id"]: _stable(r) for r in results}
        differing: set[str] = set()
        for _ in range(repeat - 1):
            again = await _run_all(workflow_repo, stored, definition, setup) if tests else []
            differing.update(r["test_id"] for r in again if first.get(r["test_id"]) != _stable(r))
        repeatability = {"runs": repeat, "identical": not differing,
                         "differing_tests": [t["id"] for t in tests if t["id"] in differing]}

    return {
        "checker": checker, "tests": report["summary"], "coverage": _coverage(definition, report, gaps),
        "mutation": mutation, "repeatability": repeatability, "gaps": gaps,
        "bar": format_bar(report["summary"], len(gaps), mutation),
    }
