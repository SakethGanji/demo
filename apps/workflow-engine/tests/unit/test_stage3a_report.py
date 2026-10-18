"""TestRunReport pieces: grid cells, badges, summary, status rules, partial-run target options, declared contract overrides."""

from __future__ import annotations

import pytest

from src.core.exceptions import ValidationError
from src.engine.node_registry import register_all_nodes
from src.engine.types import Connection, NodeData, NodeDefinition, Workflow
from src.schemas.workflow import NodeDefinitionSchema
from src.services.partial_run import target_run_options
from src.services.test_runner import _contract_failures, _covers, applicable_categories, build_grid, build_report

register_all_nodes()


def nd(name, type_):
    return NodeDefinition(name=name, type=type_)


WORKFLOW = Workflow(
    name="w",
    nodes=[nd("Hook", "Webhook"), nd("Check", "If"), nd("Api", "HttpRequest"), nd("Merge", "Merge"), nd("Fmt", "Set")],
    connections=[Connection("Hook", "Check"), Connection("Check", "Api", "true"), Connection("Check", "Merge", "false"),
                 Connection("Api", "Merge"), Connection("Merge", "Fmt")],
)


def test_default_applicable_categories():
    assert applicable_categories("HttpRequest") == ["normal", "ext_success", "ext_error", "ext_timeout", "ext_empty"]
    assert applicable_categories("DataTable")[0] == "normal" and "ext_timeout" in applicable_categories("DataTable")
    assert applicable_categories("If") == ["normal", "boundary", "missing", "wrong_type", "unusual"]
    assert applicable_categories("Set") == ["normal", "missing", "wrong_type", "unusual"]


def test_grid_rows_cells_and_statuses():
    tests = [
        {"id": "t_1", "category": "normal", "target": {"kind": "node", "node": "Check"}, "expect": []},
        {"id": "t_2", "category": "boundary", "target": {"kind": "node", "node": "Check"}, "expect": []},
        {"id": "t_3", "category": "ext_error", "target": {"kind": "workflow"}, "expect": [{"node": "Api", "op": "status", "value": "error"}]},
    ]
    results = {"t_1": {"status": "passed"}, "t_2": {"status": "failed"}, "t_3": {"status": "passed"}}
    grid = build_grid(WORKFLOW, tests, results)
    assert grid["categories"] == ["normal", "boundary", "missing", "wrong_type", "unusual", "ext_success", "ext_error", "ext_timeout", "ext_empty"]
    rows = {r["node"]: r["categories"] for r in grid["rows"]}
    assert "Hook" not in rows  # triggers have no row
    assert rows["Check"]["normal"] == {"status": "pass", "tests": ["t_1"]}
    assert rows["Check"]["boundary"] == {"status": "fail", "tests": ["t_2"]}
    assert rows["Check"]["missing"] == {"status": "none", "tests": []}
    assert rows["Check"]["ext_error"]["status"] == "na"
    assert rows["Api"]["ext_error"] == {"status": "pass", "tests": ["t_3"]}  # covers = nodes named in expect
    assert rows["Api"]["boundary"]["status"] == "na" and rows["Api"]["missing"]["status"] == "na"
    assert rows["Fmt"]["boundary"]["status"] == "na" and rows["Fmt"]["missing"]["status"] == "none"
    # a cell with one failing and one passing covering test fails
    tests.append({"id": "t_4", "category": "normal", "target": {"kind": "node", "node": "Check"}, "expect": []})
    results["t_4"] = {"status": "error"}
    assert build_grid(WORKFLOW, tests, results)["rows"][0]["categories"]["normal"]["status"] == "fail"


def test_covers_default_and_explicit():
    assert _covers({"target": {"kind": "node", "node": "X"}, "expect": []}) == ["X"]
    assert _covers({"target": {"kind": "workflow"}, "expect": [{"node": "A"}, {"node": "B"}, {"node": "A"}]}) == ["A", "B"]
    assert _covers({"target": {"kind": "range", "from": "A", "to": "B"}, "expect": [{"node": "B"}]}) == ["B"]
    assert _covers({"target": {"kind": "node", "node": "X"}, "expect": [], "covers": ["Y"]}) == ["Y"]


def test_report_summary_and_badges():
    tests = [
        {"id": "t_1", "name": "a", "category": "normal", "status": "approved", "target": {"kind": "node", "node": "Check"}, "expect": []},
        {"id": "t_2", "name": "b", "category": "normal", "status": "draft", "target": {"kind": "node", "node": "Fmt"}, "expect": []},
        {"id": "t_3", "name": "c", "category": "normal", "status": "approved", "target": {"kind": "node", "node": "Api"}, "expect": []},
    ]
    res = [
        {"test_id": "t_1", "status": "failed", "failures": [{"kind": "assertion", "node": "Check"}], "warnings": []},
        {"test_id": "t_2", "status": "passed", "failures": [], "warnings": []},
        {"test_id": "t_3", "status": "error", "failures": [{"kind": "unmocked", "node": "Api"}], "warnings": []},
    ]
    report = build_report(WORKFLOW, tests, res)
    assert report["summary"] == {"total": 3, "passed": 1, "failed": 1, "errored": 1, "approved_total": 2, "approved_failed": 2}
    assert report["badges"] == {"Check": "fail", "Fmt": "pass", "Api": "fail"}
    assert set(report) == {"results", "summary", "badges", "grid"}
    empty = build_report(WORKFLOW, [], [])
    assert empty["summary"]["total"] == 0 and empty["badges"] == {}


def test_target_options_for_workflow_node_and_range():
    items = [NodeData(json={"x": 1})]
    o = target_run_options(WORKFLOW, {"kind": "workflow"}, items)
    assert o == {"start_node_name": "Hook", "stop_at": None, "initial_data": items, "pending_inputs": None}
    o = target_run_options(WORKFLOW, {"kind": "node", "node": "Check"}, items)
    assert (o["start_node_name"], o["stop_at"], o["initial_data"], o["pending_inputs"]) == ("Check", "Check", items, None)
    o = target_run_options(WORKFLOW, {"kind": "range", "from": "Check", "to": "Api"}, items)
    assert (o["start_node_name"], o["stop_at"]) == ("Check", "Api")
    # a Merge target is seeded from its connections: the first upstream gets the items, the others are empty
    o = target_run_options(WORKFLOW, {"kind": "node", "node": "Merge"}, items)
    assert o["initial_data"] == [] and set(o["pending_inputs"]["Merge:0"]) == {"Check:false", "Api:main"}
    assert o["pending_inputs"]["Merge:0"]["Check:false"] == items and o["pending_inputs"]["Merge:0"]["Api:main"] == []
    with pytest.raises(ValidationError):
        target_run_options(WORKFLOW, {"kind": "node", "node": "Nope"}, items)
    with pytest.raises(ValidationError):
        target_run_options(WORKFLOW, {"kind": "range", "from": "Check", "to": "Nope"}, items)


def test_contract_failures_only_block_for_trusted_sources():
    shape = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    for source, blocks in [("declared", True), ("derived", True), ("spec", True), ("observed", False)]:
        hard, soft = _contract_failures("N", "main", {**shape, "x-source": source}, [{"a": 1}], "output", None)
        assert (len(hard) == 1, len(soft) == 1) == (blocks, not blocks), source
        f = (hard or soft)[0]
        assert f["kind"] == "contract" and f["node"] == "N" and f["path"] == "a" and f["direction"] == "output"
    assert _contract_failures("N", "main", {"type": "unknown"}, [{"a": 1}], "output", None) == ([], [])
    hard, _ = _contract_failures("N", "main", {**shape, "x-source": "observed"}, [{}], "input", "declared")
    assert hard[0]["direction"] == "input"
    assert _contract_failures("N", "main", {**shape, "x-source": "derived"}, [{"a": "ok"}], "output", None) == ([], [])


def test_contract_override_accepts_declared_source_and_inputs():
    node = NodeDefinitionSchema(name="N", type="Set", contract={
        "source": "declared", "approved_by": "sam", "outputs": {"main": {"type": "object"}},
        "inputs": {"main": {"type": "object", "properties": {"x": {"type": "number"}}}},
    })
    stored = node.to_dataclass().contract
    assert stored["source"] == "declared" and stored["inputs"]["main"]["properties"]["x"]["type"] == "number"
    with pytest.raises(ValueError):
        NodeDefinitionSchema(name="N", type="Set", contract={"source": "wrong", "approved_by": "s", "outputs": {}})
