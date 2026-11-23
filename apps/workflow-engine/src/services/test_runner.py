"""Run the tests of a workflow (Stage 3A, CONTRACTS-STAGE3A §5).

``run_tests(workflow_repo, stored, definition=None, *, ids=None, persist=True) -> TestRunReport`` runs the
tests one after another through ``WorkflowRunner.run(test_options=...)``. Nothing here calls an outside
system: outside steps are answered by mocks, and a step without one fails the test with ``TEST_UNMOCKED_EFFECT``.

Besides the test's own assertions, every run checks every item every node emitted against the node's
contract (kind ``contract``), and the items arriving at a node against its declared ``inputs``.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from datetime import datetime
from time import perf_counter
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from ..core.exceptions import ValidationError
from ..engine import shapes as sh
from ..engine.assertions import RunView, evaluate
from ..engine.graph import TRIGGER_NODE_TYPES
from ..engine.test_recipes import recipe_for
from ..engine.test_mode import TestNotFoundError, TestRunOptions, parse_now
from ..engine.types import NodeData
from ..engine.workflow_runner import WorkflowRunner
from ..repositories.workflow_repository import definition_to_workflow, workflow_to_definition
from ..schemas.workflow import normalize_tests
from .partial_run import target_run_options

logger = logging.getLogger(__name__)

CATEGORIES = ["normal", "boundary", "missing", "wrong_type", "unusual",
              "ext_success", "ext_error", "ext_timeout", "ext_empty"]
_NO_ROW_TYPES = TRIGGER_NODE_TYPES | {"StickyNote"}
_MAX_OUTPUT_ITEMS = 20
_MAX_CONTRACT_FAILURES = 20
_RUN_TIMEOUT_S = 60.0
#: Smoke runs hit live data: these assertion ops are evaluated and reported as "info", never failures
VALUE_OPS = frozenset({"eq", "neq", "gt", "gte", "lt", "lte", "contains", "matches", "one_of", "range"})


def applicable_categories(node_type: str) -> list[str]:
    """The grid columns that apply to a node type: the ones its recipe lists (AI/LLM nodes have none)."""
    return list(recipe_for(node_type).categories)


def _json_of(items: list[NodeData]) -> list[Any]:
    return [i.json for i in items]


def _node_status(name: str, context: Any) -> str:
    if any(e.node_name == name for e in context.errors):
        return "error"
    emitted = context.test_outputs.get(name)
    if emitted is not None:
        return "success" if any(emitted.values()) else "no_output"
    if name in context.node_states or name in context.node_metrics:
        return "success"
    return "not_run"


def _contract_failures(
    name: str, port: str, shape: Any, items: list[Any], direction: str, kind_source: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(failures, warnings) of validating ``items`` against ``shape``; observed shapes only warn."""
    if sh.is_unknown(shape):
        return [], []
    source = kind_source or shape.get("x-source")
    hard = source in ("declared", "derived", "spec")
    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for item in items:
        for v in sh.validate(shape, item):
            key = (v["path"], v["message"])
            if key in seen:
                continue
            seen.add(key)
            where = f" at {v['path']}" if v["path"] else ""
            out.append({
                "kind": "contract", "node": name, "port": port, "path": v["path"], "expected": v["expected"],
                "actual": v["actual"], "direction": direction,
                "message": f"{name} {'received' if direction == 'input' else 'emitted'} data that breaks its {source or 'contract'} shape{where}: {v['message']}",
            })
            if len(out) >= _MAX_CONTRACT_FAILURES:
                break
    return (out, []) if hard else ([], out)


def _expects_error(assertions: list[dict[str, Any]], node: str) -> bool:
    return any(a.get("op") == "status" and a.get("node") == node and a.get("value") == "error" for a in assertions)


def _covers(test: dict[str, Any]) -> list[str]:
    if test.get("covers") is not None:
        return list(test["covers"])
    target = test.get("target") or {}
    if target.get("kind") == "node":
        return [target["node"]]
    return list(dict.fromkeys(a["node"] for a in test.get("expect") or [] if a.get("node")))


class _Setup:
    """Everything the tests of one request share."""

    def __init__(self) -> None:
        self.variables: dict[str, str] = {}
        self.secrets: set[str] = set()
        self.extras: dict[str, Any] = {}
        self.project_id: str | None = None
        self.environment: str | None = None
        self.team_id: str = "default"
        self.shapes: dict[str, Any] = {}
        self.definition: dict[str, Any] = {}
        self.lookups: Any = None


async def _setup(workflow_repo: Any, stored: Any, definition: dict[str, Any], workflow: Any) -> _Setup:
    from .api_catalog import load_api_specs
    from .check_lookups import build_lookups
    from .execution_service import _egress_allowlist
    from .project_service import effective_environment, resolve_project
    from .variable_loader import load_run_secrets, load_run_variables
    from .workflow_checker import check_workflow

    session = workflow_repo._session
    project = await resolve_project(session, stored.project_id)
    env = effective_environment(project, "default", strict=True)
    setup = _Setup()
    setup.project_id, setup.environment = stored.project_id, env
    setup.team_id = getattr(stored, "team_id", None) or "default"
    setup.variables = await load_run_variables(environment=env, project_id=stored.project_id)
    setup.secrets = await load_run_secrets(environment=env, project_id=stored.project_id)
    setup.extras = {
        "egress_allowlist": _egress_allowlist(project),
        "api_specs": await load_api_specs(session, workflow.nodes),
    }
    # workflow_id + definition load the call closure, so a mocked ExecuteWorkflow gets its child's typed output contract (section 6.2)
    lookups = await build_lookups(session, stored.project_id, None, team_id=setup.team_id,
                                  workflow_id=stored.id, definition=definition)
    setup.definition, setup.lookups = definition, lookups
    setup.shapes = check_workflow(definition, lookups=lookups).get("shapes_by_node", {})
    return setup


def _result(test: dict[str, Any], status: str, duration_ms: float, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "test_id": test["id"], "name": test["name"], "status": status, "duration_ms": duration_ms,
        "execution_id": None, "assertions": [], "failures": [], "warnings": [], "node_status": {},
        "ports_taken": {}, "requests": {}, "outputs": {},
    }
    out.update(extra)
    return out


async def _run_one(
    workflow_repo: Any, stored: Any, workflow: Any, definition: dict[str, Any], test: dict[str, Any],
    defaults: dict[str, Any], setup: _Setup, persist: bool, *, live_nodes: frozenset[str] = frozenset(),
    smoke: bool = False,
) -> dict[str, Any]:
    from ..db.session import async_session_factory
    from ..repositories.execution_repository import ExecutionRepository
    from ..utils.ids import execution_id as gen_exec_id
    from .subworkflow_wiring import subworkflow_invoker

    started = perf_counter()
    now = parse_now(test.get("now") or defaults.get("now"))
    seed = test.get("seed") if test.get("seed") is not None else defaults.get("seed", 1)
    items = [NodeData(json=copy.deepcopy(i) if isinstance(i, dict) else {"value": i}) for i in (test.get("input") or {}).get("items", [])]
    if not items and (test.get("target") or {}).get("kind") == "workflow":
        items = [NodeData(json={"triggeredAt": now.isoformat(), "mode": "manual"})]
    try:
        opts = target_run_options(workflow, test["target"], items)
    except ValidationError as e:
        return _result(test, "error", 0.0, failures=[{"kind": "setup", "node": None, "message": e.message}])

    inline = set((test.get("subworkflows") or {}).keys())
    exec_nodes = {n.name for n in workflow.nodes if n.type == "ExecuteWorkflow"}
    not_calls = sorted(inline - exec_nodes)
    if not_calls:
        # section 6.3: only an ExecuteWorkflow node can run its child inline; say which names are wrong, don't crash
        return _result(test, "error", 0.0, failures=[{
            "kind": "setup", "node": not_calls[0],
            "message": "subworkflows: " + ", ".join(f"'{n}'" for n in not_calls)
            + (" is not an Execute Workflow node" if len(not_calls) == 1 else " are not Execute Workflow nodes")
            + " in this workflow, so it can't run inline.",
        }])
    options = TestRunOptions(
        mocks=copy.deepcopy(test.get("mocks") or {}), default_mocks=copy.deepcopy(defaults.get("mocks") or {}),
        now=now, seed=int(seed if seed is not None else 1), persist=persist, live_nodes=frozenset(live_nodes),
        inline_subworkflows=frozenset(inline),
    )
    exec_id = gen_exec_id()
    exec_repo = ExecutionRepository(workflow_repo._session) if persist else None
    if exec_repo is not None:
        await exec_repo.start(
            exec_id, stored.id, stored.name, "test", project_id=stored.project_id, environment=setup.environment,
            metadata={"trigger": {"type": "test", "test_id": test["id"], "test_name": test["name"]}},
        )
    runner = WorkflowRunner(db_session_factory=async_session_factory if persist else None)
    try:
        context = await asyncio.wait_for(
            runner.run(
                copy.deepcopy(workflow), opts["start_node_name"], opts["initial_data"], "test",  # type: ignore[arg-type]
                workflow_repository=workflow_repo, execution_id=exec_id, variables=dict(setup.variables),
                secret_values=set(setup.secrets), project_id=setup.project_id, environment=setup.environment,
                team_id=setup.team_id, extras=dict(setup.extras), stop_at=opts["stop_at"],
                pending_inputs=opts["pending_inputs"], test_options=options,
                # a sub-workflow call that is not mocked resolves versions like a real run; with persist=False the invoker
                # writes no child row (it reads test_options.persist), with persist=True the child is a mode='test' row
                subworkflow_invoker=subworkflow_invoker(async_session_factory),
            ),
            timeout=_RUN_TIMEOUT_S,
        )
    except Exception as e:  # the run itself broke: report it as a setup failure, keep going with the next test
        if exec_repo is not None:
            await exec_repo.mark_failed(exec_id, str(e))
        msg = "the run timed out" if isinstance(e, asyncio.TimeoutError) else str(e)
        return _result(test, "error", round((perf_counter() - started) * 1000, 2),
                       execution_id=exec_id if persist else None,
                       failures=[{"kind": "setup", "node": None, "message": f"The test could not run: {msg}"}])
    if exec_repo is not None:
        await exec_repo.complete(context, stored.id, stored.name)

    nodes_raw = {n["name"]: n for n in definition.get("nodes", []) if isinstance(n, dict) and n.get("name")}
    node_status = {n.name: _node_status(n.name, context) for n in workflow.nodes}
    # inline children (section 6.3): their nodes are addressable as "<node>/<child node>"
    node_status.update(context.extras.get("test_inline_status") or {})
    outputs = {
        node: {port: _json_of(its) for port, its in ports.items() if its}
        for node, ports in context.test_outputs.items()
    }
    shapes_by_node = setup.shapes
    if (test.get("target") or {}).get("kind") == "workflow" and setup.lookups is not None and items:
        # the trigger's output is this test's input: derive downstream shapes from it, not from the trigger's default
        from .workflow_checker import check_workflow

        trigger_shape = sh.with_source(sh.from_sample([i.json for i in items]), "derived", deep=True)
        if not sh.is_unknown(trigger_shape):
            try:
                shapes_by_node = check_workflow(
                    setup.definition or definition, lookups=setup.lookups,
                    output_overrides={opts["start_node_name"]: trigger_shape},
                ).get("shapes_by_node", setup.shapes)
            except Exception:
                logger.warning("could not recompute shapes for test %s", test.get("id"), exc_info=True)
    out_shapes = {name: dict((shapes_by_node.get(name) or {}).get("outputs", {})) for name in node_status}
    run_view = RunView(outputs=outputs, requests=context.test_requests, node_status=node_status, contracts=out_shapes)
    assertions = [evaluate(a, run_view) for a in test.get("expect") or []]

    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for a in assertions:
        if not a["passed"] and smoke and a.get("op") in VALUE_OPS:
            # live data changes: a value that moved is news, not a failure
            warnings.append({
                "kind": "info", "node": a["node"], "port": a["port"], "path": a["path"], "expected": a["expected"],
                "actual": a["actual"], "message": f"{a['node']}: {a['message']}",
            })
        elif not a["passed"]:
            failures.append({
                "kind": "assertion", "node": a["node"], "port": a["port"], "path": a["path"], "expected": a["expected"],
                "actual": a["actual"], "message": f"{a['node']}: {a['message']}",
            })
    for e in context.errors:
        if e.error_code == "TEST_UNMOCKED_EFFECT":
            details = e.details or {}
            failures.append({
                "kind": "unmocked", "node": e.node_name, "message": e.error,
                "fixes": list(details.get("fixes") or ["generate_from_spec", "use_last_real_reply"]),
            })
        elif e.error_code == "TEST_BAD_MOCK":
            failures.append({"kind": "setup", "node": e.node_name, "message": e.error})
        elif not _expects_error(test.get("expect") or [], e.node_name):
            failures.append({"kind": "node_error", "node": e.node_name, "message": e.error})

    start_node = opts["start_node_name"] if (test.get("target") or {}).get("kind") == "workflow" else None
    for node, ports in context.test_outputs.items():
        if node == start_node:
            continue  # the injected trigger output is the test's input, not something the node produced
        for port, its in ports.items():
            shape = out_shapes.get(node, {}).get(port)
            # items on_error=continue passed on carry _errorNode: they are error items, not the node's success shape
            checked = [j for j in _json_of(its) if not (isinstance(j, dict) and "_errorNode" in j)]
            hard, soft = _contract_failures(node, port, shape, checked, "output", None)
            failures.extend(hard)
            warnings.extend(soft)
    for node, raw in nodes_raw.items():
        declared = (raw.get("contract") or {}).get("inputs") if isinstance(raw.get("contract"), dict) else None
        if isinstance(declared, dict) and declared.get("main") and node in context.test_inputs:
            shape = sh.with_source(declared["main"], "declared", only_missing=True, deep=True)
            hard, _ = _contract_failures(node, "main", shape, [j for j in _json_of(context.test_inputs[node]) if not (isinstance(j, dict) and "_errorNode" in j)],
                "input", "declared",
            )
            failures.extend(hard)

    status = "passed"
    if failures:
        status = "error" if any(f["kind"] in ("unmocked", "setup", "node_error") for f in failures) else "failed"
    extra: dict[str, Any] = {}
    if live_nodes:
        extra["live_calls"] = [
            {"node": n, "status_code": (context.node_metrics.get(n) or {}).get("responseStatusCode"),
             "ms": (context.node_metrics.get(n) or {}).get("responseTimeMs", (context.node_metrics.get(n) or {}).get("executionTimeMs"))}
            for n in sorted(live_nodes) if n in context.node_metrics
        ]
        await _drift_of_live_replies(workflow_repo, stored, setup.definition or definition, context, live_nodes,
                                     exec_id if persist else None)
    return _result(
        test, status, round((perf_counter() - started) * 1000, 2),
        execution_id=exec_id if persist else None, assertions=assertions, failures=failures, warnings=warnings,
        node_status=node_status,
        ports_taken={n: [p for p, its in ports.items() if its] for n, ports in context.test_outputs.items()},
        requests=copy.deepcopy(context.test_requests),
        outputs={n: {p: its[:_MAX_OUTPUT_ITEMS] for p, its in ports.items()} for n, ports in outputs.items()},
        **extra,
    )


async def _drift_of_live_replies(
    workflow_repo: Any, stored: Any, definition: dict[str, Any], context: Any, live_nodes: frozenset[str],
    execution_id: str | None,
) -> None:
    """Live replies go through drift detection like real runs do. Never raises."""
    from . import drift

    try:
        outs = {n: [i.json for i in ports.get("main", [])] for n, ports in context.test_outputs.items() if n in live_nodes}
        await drift.check_outputs(
            workflow_repo._session, project_id=stored.project_id, workflow_id=stored.id, definition=definition,
            outputs=outs, execution_id=execution_id, only_nodes=set(live_nodes),
        )
    except Exception:
        logger.warning("drift check of live replies failed for %s", stored.id, exc_info=True)
        try:
            await workflow_repo._session.rollback()
        except Exception:
            pass


def build_grid(workflow: Any, tests: list[dict[str, Any]], results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The node x category coverage grid for the tests that were run."""
    rows = []
    for node in workflow.nodes:
        if node.type in _NO_ROW_TYPES:
            continue
        applicable = applicable_categories(node.type)
        cells: dict[str, Any] = {}
        for cat in CATEGORIES:
            if cat not in applicable:
                cells[cat] = {"status": "na", "tests": []}
                continue
            covering = [t for t in tests if t["id"] in results and t.get("category") == cat and node.name in _covers(t)]
            if not covering:
                status = "none"
            elif all(results[t["id"]]["status"] == "passed" for t in covering):
                status = "pass"
            else:
                status = "fail"
            cells[cat] = {"status": status, "tests": [t["id"] for t in covering]}
        rows.append({"node": node.name, "categories": cells})
    return {"categories": list(CATEGORIES), "rows": rows}


def build_report(
    workflow: Any, tests: list[dict[str, Any]], results: list[dict[str, Any]], stale_test_ids: list[str] | None = None,
) -> dict[str, Any]:
    by_id = {r["test_id"]: r for r in results}
    approved = [t for t in tests if t.get("status") == "approved" and t["id"] in by_id]
    badges: dict[str, str] = {}
    for t in tests:
        if t["id"] in by_id:
            for node in _covers(t):
                badges.setdefault(node, "pass")
    for r in results:
        for f in r["failures"]:
            if f.get("node"):
                badges[f["node"]] = "fail"
    return {
        "results": results,
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r["status"] == "passed"),
            "failed": sum(1 for r in results if r["status"] == "failed"),
            "errored": sum(1 for r in results if r["status"] == "error"),
            "approved_total": len(approved),
            "approved_failed": sum(1 for t in approved if by_id[t["id"]]["status"] != "passed"),
        },
        "badges": badges,
        "grid": build_grid(workflow, tests, by_id),
        "stale_test_ids": list(stale_test_ids or []),
    }


async def run_tests(
    workflow_repo: Any, stored: Any, definition: dict[str, Any] | None = None, *,
    ids: list[str] | None = None, persist: bool = True, live_nodes: Any = None, smoke: bool = False,
) -> dict[str, Any]:
    """Run the tests of ``stored`` (or of the unsaved ``definition`` of it); returns a ``TestRunReport`` dict.

    ``live_nodes`` (smoke runs, Stage 3B) names nodes that skip their mock and call the real system; with
    ``smoke`` only structural assertions can fail (value ops are reported as ``info``).

    ``ids`` picks tests (an unknown id raises TestNotFoundError). With ``persist`` each run is saved as an
    execution with ``mode="test"``; without it nothing is written. Test runs older than 7 days are deleted.
    """
    from ..repositories.execution_repository import ExecutionRepository

    if definition is None:
        definition = workflow_to_definition(stored.workflow)
    else:
        definition = copy.deepcopy(definition)
    workflow = definition_to_workflow(definition, stored.name, stored.id, stored.workflow.description)
    try:
        tests = normalize_tests(definition.get("tests"))
    except PydanticValidationError as e:
        raise ValidationError(f"Invalid tests: {e.errors()[0]['msg']} ({'.'.join(str(p) for p in e.errors()[0]['loc'])})", field="tests")
    defaults = definition.get("test_defaults") or {}
    if ids is not None:
        known = {t["id"] for t in tests}
        for i in ids:
            if i not in known:
                raise TestNotFoundError(i)
        tests = [t for t in tests if t["id"] in set(ids)]
    if persist:
        await ExecutionRepository(workflow_repo._session).purge_test_runs(7)
    stale = await _stale_ids(workflow_repo, stored, definition)
    if not tests:
        return build_report(workflow, [], [], stale)
    setup = await _setup(workflow_repo, stored, definition, workflow)
    live = frozenset(live_nodes or ())
    results = []
    for test in tests:
        results.append(await _run_one(workflow_repo, stored, workflow, definition, test, defaults, setup, persist,
                                      live_nodes=live, smoke=smoke))
    return build_report(workflow, tests, results, stale)


async def _stale_ids(workflow_repo: Any, stored: Any, definition: dict[str, Any]) -> list[str]:
    """Tests whose mocks cover a node with open API drift (derived, never stored). Empty on any failure."""
    from . import drift

    try:
        return await drift.stale_ids_for(workflow_repo._session, stored.id, definition)
    except Exception:
        logger.warning("could not compute stale tests for %s", stored.id, exc_info=True)
        return []
