"""Stage 6 B1: pure helpers: canonical hashing and the test fingerprint, auto-layout (golden), and the workflow diff."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.engine.node_registry import register_all_nodes
from src.services import auto_layout as al
from src.services import proposal_sandbox as sb
from src.services import workflow_diff as wd

register_all_nodes()
GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "ai_builder" / "layout_golden.json"


def T(i: str, status: str = "draft", **kw) -> dict:
    return {"id": f"t_{i:0>8}", "name": f"test {i}", "target": {"kind": "workflow"}, "input": {"items": []}, "mocks": {},
            "expect": [], "category": "normal", "status": status, **kw}


# ── hashing and fingerprints ─────────────────────────────────────────────────


def test_canonical_hash_ignores_key_order_and_spacing_but_not_values():
    a = {"b": [1, {"y": 1, "x": 2}], "a": "é"}
    b = {"a": "é", "b": [1, {"x": 2, "y": 1}]}
    assert sb.canonical_hash(a) == sb.canonical_hash(b) and len(sb.canonical_hash(a)) == 64
    assert sb.canonical_hash(a) != sb.canonical_hash({"a": "é", "b": [1, {"x": 2, "y": 2}]})
    assert sb.canonical_hash([1, 2]) != sb.canonical_hash([2, 1])           # list order matters


def test_canonical_hash_survives_values_json_cannot_hold():
    from datetime import datetime

    assert sb.canonical_hash({"at": datetime(2026, 1, 1)}) == sb.canonical_hash({"at": datetime(2026, 1, 1)})


def test_fingerprint_counts_only_approved_tests_and_ignores_who_and_when():
    drafts = [T("1"), T("2")]
    approved = T("3", "approved", approved_by="Asha", approved_at="2026-10-18T10:00:00")
    base = sb.fingerprint([*drafts, approved])
    assert base == sb.fingerprint([approved])
    assert base == sb.fingerprint([T("3", "approved", approved_by="Someone", approved_at="2030-01-01T00:00:00")])
    assert base == sb.fingerprint([approved, *reversed(drafts)])            # order does not matter
    assert sb.fingerprint([]) == sb.fingerprint([T("1")])


def test_fingerprint_changes_when_an_approved_test_changes_in_any_way():
    t = T("3", "approved", expect=[{"node": "A", "op": "count", "value": 1}])
    fp = sb.fingerprint([t])
    changed = [
        {**t, "name": "renamed"},
        {**t, "expect": [{"node": "A", "op": "count", "value": 2}]},
        {**t, "input": {"items": [{"x": 1}]}},
        {**t, "mocks": {"A": {"items": []}}},
        {**t, "category": "boundary"},
        {**t, "now": "2026-01-01T00:00:00Z"},
    ]
    assert all(sb.fingerprint([c]) != fp for c in changed)
    assert sb.fingerprint([t, T("4", "approved")]) != fp                    # an extra approved test
    assert sb.fingerprint([]) != fp                                         # a removed one


def test_tampered_tests_names_missing_and_changed_approved_tests():
    a, b = T("a", "approved"), T("b", "approved")
    expected = [a, b, T("c")]                                               # the draft is not protected
    assert sb.tampered_tests(expected, {"tests": [a, b]}) == []
    assert sb.tampered_tests(expected, {"tests": [a, b, T("d")]}) == []    # extra tests are fine
    assert sb.tampered_tests(expected, {"tests": [a]}) == [b["id"]]
    assert sb.tampered_tests(expected, {"tests": [{**a, "name": "x"}, b]}) == [a["id"]]
    assert sb.tampered_tests(expected, {"tests": [{**a, "status": "draft"}, b]}) == [a["id"]]   # demoted to a draft
    assert sb.tampered_tests(expected, None) == sorted([a["id"], b["id"]])
    assert sb.tampered_tests([], {"tests": []}) == []


def test_draft_hash_is_stable_across_the_stored_round_trip():
    from src.repositories.workflow_repository import definition_to_workflow, workflow_to_definition
    from src.engine.types import StoredWorkflow
    from datetime import datetime

    d = {"nodes": [{"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 1, "y": 2}}], "connections": [],
         "settings": {}, "tests": [], "test_defaults": {}}
    wf = definition_to_workflow(d, "n", "wf_1")
    stored = StoredWorkflow(id="wf_1", name="n", workflow=wf, active=False, created_at=datetime.now(), updated_at=datetime.now())
    again = StoredWorkflow(id="wf_1", name="n", workflow=definition_to_workflow(workflow_to_definition(wf), "n", "wf_1"),
                           active=False, created_at=datetime.now(), updated_at=datetime.now())
    assert sb.draft_hash(stored) == sb.draft_hash(again)
    defn, h = sb.draft_of(stored)
    assert h == sb.draft_hash(stored) == sb.canonical_hash(defn)


def test_proposal_stored_is_what_the_test_runner_reads():
    p = {"id": "p_abc", "project_id": "proj_default", "team_id": None, "workflow_id": None, "spec": {"title": "Spec"}}
    s = sb.proposal_stored(p, {"nodes": [], "description": "d"})
    assert (s.id, s.project_id, s.team_id, s.name, s.workflow.description) == ("p_abc", "proj_default", None, "Spec", "d")
    assert sb.proposal_stored({**p, "workflow_id": "wf_9"}, {}).id == "wf_9"
    assert sb.proposal_stored(p, {"name": "N" * 400}).name == "N" * 255


def test_error_envelopes():
    assert sb.BadState("kept").detail() == {"code": "bad_state", "message": sb.BadState("kept").message, "status": "kept"}
    e = sb.LockedTests(["t_1"])
    assert (e.http_status, e.detail()["code"], e.detail()["test_ids"]) == (409, "locked_tests", ["t_1"])
    assert sb.ProposalNotFound("x").http_status == 404 and sb.DraftMoved("x").detail()["code"] == "draft_moved"
    assert sb.TestsFailing("x").detail()["code"] == "tests_failing"


# ── auto layout ──────────────────────────────────────────────────────────────


def N(name: str, type_: str = "Set", x=0, y=0) -> dict:
    return {"name": name, "type": type_, "parameters": {}, "position": {"x": x, "y": y}}


def C(s: str, t: str, port: str = "main") -> dict:
    return {"source_node": s, "target_node": t, "source_output": port, "target_input": "main"}


def pos(defn: dict) -> dict:
    return {n["name"]: (n["position"]["x"], n["position"]["y"]) for n in defn["nodes"]}


def branching() -> dict:
    return {"nodes": [N("Form", "FormTrigger"), N("Over?", "If"), N("Manager", "Approval"), N("Auto"), N("Notify"), N("Show", "Respond")],
            "connections": [C("Form", "Over?"), C("Over?", "Manager", "true"), C("Over?", "Auto", "false"),
                            C("Manager", "Notify", "approved"), C("Auto", "Notify"), C("Notify", "Show")]}


def test_layout_matches_the_golden_file():
    out = al.auto(branching())
    golden = json.loads(GOLDEN.read_text())
    assert {k: list(v) for k, v in pos(out).items()} == golden["branching"]


def test_layout_is_deterministic_and_does_not_mutate_the_input():
    d = branching()
    before = copy.deepcopy(d)
    assert al.auto(d) == al.auto(copy.deepcopy(d)) and d == before


def test_layout_layers_by_longest_path_and_orders_branches_by_port():
    p = pos(al.auto(branching()))
    assert p["Form"][0] == 0 and p["Over?"][0] == al.X_STEP and p["Manager"][0] == 2 * al.X_STEP and p["Auto"][0] == 2 * al.X_STEP
    assert p["Notify"][0] == 3 * al.X_STEP and p["Show"][0] == 4 * al.X_STEP                  # Notify is after both branches
    assert p["Manager"][1] < p["Auto"][1]                                   # the true port above the false port
    assert all(y % al.Y_STEP == 0 for _, y in p.values())


def test_layout_keeps_existing_nodes_and_places_only_new_ones_in_free_slots():
    d = {"nodes": [N("Start", "Start", 10, 20), N("Old", "Set", 500, 500), N("New 1"), N("New 2")],
         "connections": [C("Start", "Old"), C("Start", "New 1"), C("Start", "New 2")]}
    out = al.auto(d, keep=["Start", "Old"])
    p = pos(out)
    assert p["Start"] == (10, 20) and p["Old"] == (500, 500)
    assert p["New 1"][0] == al.X_STEP and p["New 2"][0] == al.X_STEP and p["New 1"] != p["New 2"]


def test_layout_does_not_stack_a_new_node_on_a_kept_one():
    d = {"nodes": [N("Start", "Start", 0, 0), N("Kept", "Set", al.X_STEP, 0), N("New")], "connections": [C("Start", "Kept"), C("Start", "New")]}
    p = pos(al.auto(d, keep=["Start", "Kept"]))
    assert p["Kept"] == (al.X_STEP, 0) and p["New"][0] == al.X_STEP and p["New"][1] != 0


def test_layout_default_keep_is_every_node_with_a_real_position():
    d = {"nodes": [N("Start", "Start", 7, 9), N("Unplaced")], "connections": [C("Start", "Unplaced")]}
    p = pos(al.auto(d))
    assert p["Start"] == (7, 9) and p["Unplaced"] == (al.X_STEP, 0)


def test_layout_handles_missing_positions_cycles_dangling_edges_and_empty():
    d = {"nodes": [{"name": "A", "type": "Set", "parameters": {}}, N("B"), N("C")],
         "connections": [C("A", "B"), C("B", "C"), C("C", "B"), C("A", "Ghost"), C("Ghost", "A"), C("A", "A")]}
    p = pos(al.auto(d))
    assert p["A"][0] == 0 and p["B"][0] == al.X_STEP and p["C"][0] >= al.X_STEP
    assert al.auto({"nodes": [], "connections": []}) == {"nodes": [], "connections": []}
    assert al.auto({}) == {}


def test_layout_triggers_come_first_in_the_first_column():
    d = {"nodes": [N("Loose"), N("Hook", "Webhook")], "connections": []}
    p = pos(al.auto(d))
    assert p["Hook"] == (0, 0) and p["Loose"] == (0, al.Y_STEP)


def test_layout_survives_an_unknown_node_type():
    d = {"nodes": [N("Start", "Start"), N("Odd", "NoSuchNode")], "connections": [C("Start", "Odd")]}
    assert pos(al.auto(d))["Odd"] == (al.X_STEP, 0)


def test_layout_wraps_a_long_chain_into_a_second_band():
    names = [f"S{i}" for i in range(al.MAX_COLS + 2)]
    d = {"nodes": [N(names[0], "Start")] + [N(n) for n in names[1:]], "connections": [C(a, b) for a, b in zip(names, names[1:])]}
    p = pos(al.auto(d))
    assert p[names[al.MAX_COLS]][0] == 0 and p[names[al.MAX_COLS]][1] > p[names[0]][1]
    assert p[names[al.MAX_COLS - 1]][0] == (al.MAX_COLS - 1) * al.X_STEP and p[names[0]][1] == p[names[al.MAX_COLS - 1]][1]


# ── workflow diff ────────────────────────────────────────────────────────────


def n(name: str, parameters: dict | None = None, x: int = 0, **extra) -> dict:
    return {"name": name, "type": "Set", "parameters": parameters or {}, "position": {"x": x, "y": 0}, **extra}


def wf(*nodes: dict, **kw) -> dict:
    return {"nodes": list(nodes), "connections": [], **kw}


# the cases of the studio's workflowDiff.test.ts: the two ports must agree
def test_parity_added_and_removed_by_name():
    assert wd.diff_nodes(wf(n("a"), n("b")), wf(n("b"), n("c"))) == {"added": ["c"], "removed": ["a"], "changed": []}


def test_parity_parameter_changes_ignore_key_order():
    base = wf(n("a", {"x": 1, "y": {"p": 1, "q": 2}}), n("b", {"k": 1}))
    target = wf(n("a", {"y": {"q": 2, "p": 1}, "x": 1}), n("b", {"k": 2}))
    assert wd.diff_nodes(base, target)["changed"] == ["b"]


def test_parity_position_only_changes_are_ignored():
    assert wd.diff_nodes(wf(n("a", {}, 0)), wf(n("a", {}, 500))) == {"added": [], "removed": [], "changed": []}


def test_parity_type_disabled_on_error_and_label_changes():
    base = wf(n("t"), n("d"), n("e"), n("l"))
    target = wf({**n("t"), "type": "If"}, n("d", disabled=True), n("e", on_error="continue"), n("l", label="Nice"))
    assert wd.diff_nodes(base, target)["changed"] == ["t", "d", "e", "l"]


def test_parity_legacy_continue_on_fail_is_on_error_continue():
    assert wd.diff_nodes(wf(n("a", continue_on_fail=True)), wf(n("a", on_error="continue")))["changed"] == []


def test_diff_sections_for_a_modified_workflow():
    base = wf(n("Start"), n("Issue", {"url": "https://old.bank.internal/x"}), tests=[T("1"), T("2")])
    base["connections"] = [C("Start", "Issue")]
    target = wf(n("Start"), n("Issue", {"url": "https://refunds.bank.internal/x", "body": {"a": 1}}, on_error="continue"),
                n("Over?", {"c": 1}), tests=[T("1"), {**T("2"), "name": "renamed"}, T("3")], interface={"slug": "x"})
    target["connections"] = [C("Start", "Over?"), C("Over?", "Issue", "true")]
    for nd in (base, target):
        for node in nd["nodes"]:
            if node["name"] == "Issue":
                node["type"] = "HttpRequest"
    d = wd.diff_definitions(base, target)
    assert d["nodes"] == {"added": ["Over?"], "removed": [], "changed": [{"name": "Issue", "fields": ["parameters.body", "parameters.url", "on_error"]}]}
    assert d["connections"] == {"added": [["Start", "main", "Over?", "main"], ["Over?", "true", "Issue", "main"]],
                                "removed": [["Start", "main", "Issue", "main"]]}
    assert d["tests"] == {"added": [T("3")["id"]], "changed": [T("2")["id"]], "removed": []}
    assert d["interface"] == {"changed": True}
    assert d["new_destinations"] == ["https://refunds.bank.internal"]
    assert d["counts"] == {"added": 1, "removed": 0, "changed": 1}


def test_diff_of_a_new_workflow_marks_everything_added():
    target = wf(n("Start"), n("Call", {"url": "https://x.bank.internal:8443/p?q=1"}), tests=[T("1")])
    target["nodes"][1]["type"] = "HttpRequest"
    d = wd.diff_definitions(None, target)
    assert d["nodes"]["added"] == ["Start", "Call"] and d["counts"] == {"added": 2, "removed": 0, "changed": 0}
    assert d["tests"]["added"] == [T("1")["id"]] and d["new_destinations"] == ["https://x.bank.internal:8443"]
    assert d["interface"] == {"changed": False}


def test_diff_flags_new_and_changed_code_only():
    base = wf({**n("Calc", {"code": "return 1"}), "type": "Code"}, {**n("Same", {"code": "return 2"}), "type": "Code"})
    target = wf({**n("Calc", {"code": "return 99"}), "type": "Code"}, {**n("Same", {"code": "return 2"}), "type": "Code"},
                {**n("Fresh", {"code": "return 3"}), "type": "Code"}, n("Plain"))
    assert wd.diff_definitions(base, target)["new_code"] == ["Calc", "Fresh"]


def test_destinations_ignore_expressions_relative_urls_and_other_node_types():
    assert wd.origin_of("https://Api.Bank.internal/v1") == "https://api.bank.internal"
    assert wd.origin_of("http://h:81/x") == "http://h:81"
    for bad in ("{{ $vars.URL }}/x", "/relative", "ftp://h/x", "", None, 5, "https://{{ $json.h }}/x"):
        assert wd.origin_of(bad) is None
    d = wf({**n("A", {"url": "https://a.test/x"}), "type": "Set"})
    assert wd.destinations_of(d) == set()                                   # only HttpRequest calls out


def test_diff_with_identical_or_empty_inputs_is_empty():
    d = wd.diff_definitions(wf(n("a")), wf(n("a", {}, 40)))
    assert d["counts"] == {"added": 0, "removed": 0, "changed": 0} and d["connections"] == {"added": [], "removed": []}
    e = wd.diff_definitions(None, None)
    assert e["nodes"] == {"added": [], "removed": [], "changed": []} and e["new_code"] == [] and e["new_destinations"] == []


def test_diff_ignores_duplicate_and_reordered_connections():
    a = wf(n("a"), n("b")); a["connections"] = [C("a", "b"), C("a", "b")]
    b = wf(n("a"), n("b")); b["connections"] = [C("a", "b")]
    assert wd.diff_definitions(a, b)["connections"] == {"added": [], "removed": []}
