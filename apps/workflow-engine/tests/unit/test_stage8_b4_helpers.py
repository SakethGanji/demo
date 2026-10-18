"""Stage 8 B4: the pure helpers (no database): release pins and diff, version parsing, the stale flag, the shared invoker
helper and the new response fields."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.schemas.execution import ExecutionDetailResponse
from src.schemas.workflow import TestCaseSchema, WorkflowPublishResponse, WorkflowPublishResult, normalize_tests
from src.services.release_service import ReleaseService, pins_from_snapshot, release_to_dict
from src.services.route_dispatcher import release_extras
from src.services.subworkflow_api import SubworkflowApi, SubworkflowApiError, io_findings, parse_version
from src.services.subworkflow_service import DbSubworkflowInvoker, pin_key
from src.services.subworkflow_wiring import subworkflow_invoker
from src.services.workflow_io import WorkflowIO


def edge(parent_version=10, node="call", child_version=20, number=2, child="wf_c", parent="wf_p", **kw):
    return {"parent_workflow_id": parent, "parent_version_id": parent_version, "node": node, "child_workflow_id": child,
            "child_version_id": child_version, "child_version_number": number, "spec": "latest", **kw}


# ── pins ─────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_pins_are_keyed_the_way_resolve_child_reads_them():
    pins = pins_from_snapshot({"subworkflows": [edge(10, "call", 20), edge(11, "other", 21)]})
    assert pins == {"10:call": 20, "11:other": 21}
    assert pin_key(10, "call") in pins                                   # one definition of the key, shared with resolve_child


def test_a_snapshot_without_the_key_is_a_legacy_release_and_has_no_pins_at_all():
    assert pins_from_snapshot({"workflows": []}) is None
    assert pins_from_snapshot(None) is None
    assert pins_from_snapshot({"subworkflows": []}) == {}                # pinned, nothing to pin: not the same as legacy


def test_unresolved_and_cyclic_edges_and_dry_run_ids_are_not_pins():
    snap = {"subworkflows": [edge(child_version=None), edge(node="b", child_version=-1), edge(node="c", cycle=True), edge(node="ok")]}
    assert pins_from_snapshot(snap) == {"10:ok": 20}


def test_the_agent_tool_pins_under_its_fixed_name():
    assert pins_from_snapshot({"subworkflows": [edge(node="Ask tool", pin_node="WorkflowTool")]}) == {"10:WorkflowTool": 20}


def test_release_extras_carry_pins_only_for_a_pinned_release():
    legacy = SimpleNamespace(id="rel_1", snapshot={"modules": []})
    assert "release_pins" not in release_extras(legacy) and release_extras(legacy)["release_id"] == "rel_1"
    pinned = SimpleNamespace(id="rel_2", snapshot={"modules": [{"name": "m", "source": "x"}], "subworkflows": [edge()]})
    out = release_extras(pinned)
    assert out["release_pins"] == {"10:call": 20} and out["release_modules"] == {"m": "x"}
    assert release_extras(None) is None


def test_release_to_dict_flags_a_release_that_predates_pinning():
    from datetime import datetime

    def rel(snapshot):
        return SimpleNamespace(id="r", project_id="p", environment="dev", number=1, is_active=False, status="snapshot", checks=[],
                               source_release_id=None, notes=None, created_by=None, created_at=datetime(2026, 1, 1), snapshot=snapshot)

    assert release_to_dict(rel({"subworkflows": []}))["pins_subworkflows"] is True
    assert release_to_dict(rel({"workflows": []}))["pins_subworkflows"] is False


# ── diff ─────────────────────────────────────────────────────────────────────────────────────────────────────────


def snap(*edges, workflows=()):
    return {"apps": [], "workflows": list(workflows), "subworkflows": list(edges), "secret_bindings": [], "spec": {}}


def test_the_diff_lists_a_child_whose_pinned_version_changed_with_its_parents():
    old = snap(edge(number=2), edge(node="b", parent="wf_q", number=2))
    new = snap(edge(number=3), edge(node="b", parent="wf_q", number=3))
    [row] = ReleaseService._diff(old, new)["subworkflows"]
    assert (row["workflow"], row["from_version"], row["to_version"], row["via"]) == ("wf_c", [2], [3], "subworkflow")
    assert row["parents"] == [{"workflow_id": "wf_p", "node": "call"}, {"workflow_id": "wf_q", "node": "b"}]


def test_the_diff_is_empty_when_nothing_moved_and_names_added_and_removed_children():
    assert ReleaseService._diff(snap(edge()), snap(edge()))["subworkflows"] == []
    added = ReleaseService._diff(snap(), snap(edge(number=4)))["subworkflows"]
    assert added[0]["from_version"] == [] and added[0]["to_version"] == [4]
    removed = ReleaseService._diff(snap(edge(number=4)), snap())["subworkflows"]
    assert removed[0]["from_version"] == [4] and removed[0]["to_version"] == []


def test_the_classic_workflow_diff_ignores_child_entries_and_old_snapshots_still_diff():
    roots = [{"workflow_id": "wf_p", "version_id": 10, "via": "route"}]
    kid = {"workflow_id": "wf_c", "version_id": 20, "via": "subworkflow"}
    d = ReleaseService._diff(snap(workflows=roots), snap(workflows=[*roots, kid]))
    assert d["workflows"] == []
    legacy = ReleaseService._diff({"workflows": [{"workflow_id": "w", "version_id": 1}]}, {"workflows": [{"workflow_id": "w", "version_id": 2}]})
    assert legacy["workflows"] == [{"workflow_id": "w", "from_version": 1, "to_version": 2}] and legacy["subworkflows"] == []


# ── endpoint helpers ─────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,want", [(None, None), ("", None), ("draft", "draft"), (" Latest ", "latest"), ("3", 3), ("12", 12)])
def test_parse_version(raw, want):
    assert parse_version(raw) == want


@pytest.mark.parametrize("raw", ["0", "-1", "v2", "1.5", "newest"])
def test_parse_version_refuses_anything_else_with_a_422(raw):
    with pytest.raises(SubworkflowApiError) as e:
        parse_version(raw)
    assert e.value.status == 422 and e.value.detail["code"] == "INVALID_VERSION"


def test_io_findings_name_the_stage_8_codes():
    ok = WorkflowIO(has_trigger=True, input_fields=[{"name": "a"}], input_source="fields")
    assert io_findings("Kid", ok) == []
    codes = lambda io: [f["code"] for f in io_findings("Kid", io)]  # noqa: E731
    assert codes(WorkflowIO(has_trigger=False)) == ["WF021"]
    assert codes(WorkflowIO(has_trigger=True, has_human=True, input_fields=[{"name": "a"}])) == ["WF025"]
    assert codes(WorkflowIO(has_trigger=True, input_fields=[{"name": "a"}], output_note="several shapes")) == ["WF026"]
    assert codes(WorkflowIO(has_trigger=True, input_source="passthrough")) == ["WF029"]
    assert {f["severity"] for f in io_findings("Kid", WorkflowIO(has_trigger=False))} == {"error"}


# ── the stale flag ───────────────────────────────────────────────────────────────────────────────────────────────


def plan(*affected):
    return SimpleNamespace(child_name="Scoring", affected_tests=[{"id": i, "nodes": ["Enrich"]} for i in affected])


def test_affected_tests_are_marked_stale_and_the_input_is_not_mutated():
    parent = {"tests": [{"id": "t_00000001", "name": "a"}, {"id": "t_00000002", "name": "b"}]}
    out = SubworkflowApi._stale_tests(parent, plan("t_00000001"))
    assert out["tests"][0]["stale"] is True and "Enrich" in out["tests"][0]["stale_reason"] and "Scoring" in out["tests"][0]["stale_reason"]
    assert "stale" not in out["tests"][1] and "stale" not in parent["tests"][0]
    assert SubworkflowApi._stale_tests(parent, plan()) is parent


def test_the_stale_flag_survives_normalisation_and_is_absent_by_default():
    t = TestCaseSchema(name="x", stale=True, stale_reason="moved").to_stored()
    assert t["stale"] is True and t["stale_reason"] == "moved"
    assert "stale" not in TestCaseSchema(name="y").to_stored()
    assert normalize_tests([t])[0]["stale"] is True


def test_set_call_version_changes_only_the_named_node():
    d = {"nodes": [{"name": "a", "parameters": {"version": "latest"}}, {"name": "b", "parameters": {"version": "latest"}}, {"name": "c"}]}
    SubworkflowApi._set_call_version(d, "a", "draft")
    SubworkflowApi._set_call_version(d, "c", "draft")
    assert [n.get("parameters", {}).get("version") for n in d["nodes"]] == ["draft", "latest", "draft"]


def test_publish_errors_are_flattened_from_findings_and_failed_tests():
    f = SubworkflowApi._publish_errors(SimpleNamespace(findings=[{"code": "WF006", "message": "gone"}]))
    assert f == [{"kind": "check", "code": "WF006", "message": "gone"}]
    t = SubworkflowApi._publish_errors(SimpleNamespace(failed_tests=[{"test_id": "t_1", "failures": []}]))
    assert t == [{"kind": "test", "test_id": "t_1", "failures": []}]
    assert SubworkflowApi._publish_errors(ValueError("nope")) == [{"kind": "publish", "message": "nope"}]


# ── wiring helper and response fields ────────────────────────────────────────────────────────────────────────────


def test_the_shared_helper_builds_a_db_invoker_with_the_callers_session_factory():
    factory = object()
    inv = subworkflow_invoker(factory)
    assert isinstance(inv, DbSubworkflowInvoker) and inv._factory() is factory
    assert isinstance(subworkflow_invoker(), DbSubworkflowInvoker)        # default: the engine's own factory, looked up per call


def test_response_models_gain_their_fields_without_breaking_old_payloads():
    assert WorkflowPublishResult(id="w", active=True, version_id=1).impact == []
    assert "impact" not in WorkflowPublishResponse(id="w", active=False, version_id=None).model_dump()   # unpublish keeps its shape
    d = ExecutionDetailResponse(id="e", workflow_id="w", workflow_name="n", status="success", mode="manual", start_time="t",
                                end_time=None, errors=[], node_data={})
    assert (d.parent, d.root_execution_id, d.depth, d.children) == (None, None, 0, [])
    d2 = ExecutionDetailResponse(id="e", workflow_id="w", workflow_name="n", status="s", mode="m", start_time="t", end_time=None,
                                 errors=[], node_data={}, parent={"execution_id": "p", "workflow_id": "w", "workflow_name": "n", "node": "call"},
                                 children=[{"execution_id": "c", "node": "call", "workflow_id": "w2", "workflow_name": "k",
                                            "version_number": 2, "status": "success", "duration_ms": 5}])
    assert d2.parent.node == "call" and d2.children[0].version_number == 2
