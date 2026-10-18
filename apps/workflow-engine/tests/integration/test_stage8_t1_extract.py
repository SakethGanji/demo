"""Stage 8 T1 (tester): convert selection to sub-workflow, judged by what the workflow *does* before and after the move
(DESIGN-STAGE8-REUSE 7.1-7.3, R9) over HTTP on real Postgres. The unit tests prove the plan's shape; these prove that the
rewritten parent plus the new child compute the same answer, and that every refusal is a 422 that writes nothing."""

from __future__ import annotations

import pytest

from ._stage8_t1_support import C, N, check, codes, get, make, publish, run, setf, uniq

pytestmark = pytest.mark.integration


def hook() -> dict:
    """A Webhook trigger named Start: its payload shape is declared accurately, unlike the manual Start node (see DEFECT S8-2)."""
    return N("Start", "Webhook", {"method": "POST", "path": uniq("s8t1")})


def wf(name: str | None, nodes: list[dict], conns: list[dict]) -> dict:
    return {"name": name or uniq("s8t1-ex"), "nodes": nodes, "connections": conns, "settings": {}}


def extract(api, wid: str, selection: list[str], *, dry_run: bool = False, name: str = "Moved steps", expect: int = 200, **extra):
    r = api.post(f"/workflows/{wid}/extract-subworkflow", {"nodes": selection, "name": uniq(name), "dry_run": dry_run, **extra})
    assert r.status_code == expect, r.text
    return r.json() if expect == 200 else r.json()["detail"]


def business(item: dict) -> dict:
    """An item without the sub-workflow trigger's ``_`` stamps and clock fields."""
    return {k: v for k, v in item.items() if not k.startswith("_") and k not in ("triggeredAt",)}


def finals(out: dict, node: str) -> list[dict]:
    return [business(i["json"]) for i in (out["data"].get(node) or [])]


def before_after(api, body: dict, selection: list[str], last: str, input_data: dict, *, call_alias: str | None = None):
    """Run ``body`` as it is, extract ``selection`` for real, run again; return (before, after, extract response)."""
    wid = make(api, body)
    before = run(api, wid, input_data)
    assert before["status"] == "success", before["errors"]
    res = extract(api, wid, selection)
    after = run(api, wid, input_data)
    assert after["status"] == "success", (after["errors"], res["parent_definition"])
    return before, after, res, wid


def test_an_outside_reference_inside_the_selection_becomes_a_typed_input_and_the_answer_is_unchanged(api):
    body = wf(None, [
        hook(),
        setf("Enrich", a="{{ $json.body.x }}"),
        setf("Score", b="{{ $json.a * 2 }}", c='{{ $node["Start"].json.body.x + 100 }}'),     # reaches back past the entry source
        setf("Decide", d="{{ $json.b + $json.c }}"),
    ], [C("Start", "Enrich"), C("Enrich", "Score"), C("Score", "Decide")])
    before, after, res, wid = before_after(api, body, ["Score"], "Decide", {"x": 7})
    assert finals(before, "Decide")[0]["d"] == finals(after, "Decide")[0]["d"] == 14 + 107
    kid = get(api, res["child_workflow_id"])["definition"]
    trigger = next(n for n in kid["nodes"] if n["type"] == "ExecuteWorkflowTrigger")
    fields = {f["name"]: f for f in trigger["parameters"].get("fields", [])}
    assert any("Start" in name for name in fields), fields                        # the reach-back became a declared input
    assert all(f.get("required") for name, f in fields.items() if "Start" in name)


def test_a_node_after_the_selection_that_read_the_exit_by_name_follows_the_call(api):
    body = wf(None, [
        hook(),
        setf("Prep", xv="{{ $json.body.x }}"),
        setf("A", a="{{ $json.xv }}"),
        setf("B", b="{{ $json.a + 1 }}"),
        setf("C", c='{{ $node["B"].json.b * 10 }}'),
    ], [C("Start", "Prep"), C("Prep", "A"), C("A", "B"), C("B", "C")])
    before, after, res, wid = before_after(api, body, ["A", "B"], "C", {"x": 4})
    assert finals(before, "C")[0]["c"] == finals(after, "C")[0]["c"] == 50
    call = next(n for n in get(api, wid)["definition"]["nodes"] if n["type"] == "ExecuteWorkflow")
    c_node = next(n for n in get(api, wid)["definition"]["nodes"] if n["name"] == "C")
    assert call["name"] in str(c_node["parameters"]) and '"B"' not in str(c_node["parameters"])


def test_a_selection_at_the_end_of_the_graph_returns_the_last_node(api):
    body = wf(None, [hook(), setf("A", a="{{ $json.body.x }}"), setf("B", b="{{ $json.a * 3 }}"), setf("Z", z="{{ $json.b }}")],
              [C("Start", "A"), C("A", "B"), C("B", "Z")])
    before, after, res, wid = before_after(api, body, ["B", "Z"], "Z", {"x": 2})
    call = next(n["name"] for n in get(api, wid)["definition"]["nodes"] if n["type"] == "ExecuteWorkflow")
    assert finals(before, "Z")[0]["z"] == finals(after, call)[0]["z"] == 6


def test_a_fan_in_to_the_entry_node_works_whichever_branch_fed_it(api):
    body = wf(None, [
        hook(),
        N("Fork", "If", {"condition": "{{ $json.body.big }}"}, 100),
        setf("P", via="p"), setf("Q", via="q"),
        setf("Y", y="{{ $json.via }}-y"), setf("Z", z="{{ $json.y }}-z"),
    ], [C("Start", "Fork"), C("Fork", "P", "true"), C("Fork", "Q", "false"), C("P", "Y"), C("Q", "Y"), C("Y", "Z")])
    wid = make(api, body)
    before = [run(api, wid, {"big": b}) for b in (True, False)]
    res = extract(api, wid, ["Y", "Z"])
    call = next(n["name"] for n in get(api, wid)["definition"]["nodes"] if n["type"] == "ExecuteWorkflow")
    after = [run(api, wid, {"big": b}) for b in (True, False)]
    assert [finals(r, "Z")[0]["z"] for r in before] == ["p-y-z", "q-y-z"]
    assert [finals(r, call)[0]["z"] for r in after] == ["p-y-z", "q-y-z"]
    assert len(get(api, wid)["definition"]["nodes"]) == 5                         # Start, Fork, P, Q + the call


def test_exit_through_a_branch_port_returns_only_the_items_that_took_that_port(api):
    body = wf(None, [
        hook(),
        setf("Prep", amount="{{ $json.body.amount }}"),
        N("Gate", "If", {"condition": "{{ $json.amount > 100 }}"}, 300),
        setf("Review", verdict="review"),
    ], [C("Start", "Prep"), C("Prep", "Gate"), C("Gate", "Review", "true")])
    wid = make(api, body)
    big, small = run(api, wid, {"amount": 500}), run(api, wid, {"amount": 5})
    assert finals(big, "Review")[0]["verdict"] == "review" and "Review" not in small["data"]
    res = extract(api, wid, ["Gate"])                                            # exits through Gate's true port only
    kid = get(api, res["child_workflow_id"])["definition"]
    assert any(n["name"] == "Return" and n["type"] == "NoOp" for n in kid["nodes"])
    big2, small2 = run(api, wid, {"amount": 500}), run(api, wid, {"amount": 5})
    assert finals(big2, "Review")[0]["verdict"] == "review"
    assert "Review" not in small2["data"], "an item that took the false port must not reach the node after the call"


def test_several_items_go_through_the_call_once_and_keep_their_number(api):
    body = wf(None, [
        hook(),
        N("Split", "ItemLists", {"operation": "splitOut", "arrayField": "body.rows", "includeOther": False}, 100),
        setf("Double", twice="{{ $json.n * 2 }}"),
        setf("Tag", tag="t-{{ $json.twice }}"),
        setf("Out", seen="{{ $json.tag }}"),
    ], [C("Start", "Split"), C("Split", "Double"), C("Double", "Tag"), C("Tag", "Out")])
    rows = {"rows": [{"n": 1}, {"n": 2}, {"n": 3}]}
    before, after, res, wid = before_after(api, body, ["Double", "Tag"], "Out", rows)
    assert [i["seen"] for i in finals(after, "Out")] == [i["seen"] for i in finals(before, "Out")] == ["t-2", "t-4", "t-6"]


# ── refusals over HTTP: a 422 with the code and the nodes, nothing written ──────────────────────────────────────────


def snapshot(api, wid: str):
    d = get(api, wid)
    return d["definition"], len(api.get("/workflows").json())


@pytest.mark.parametrize("name,nodes,conns,selection,code", [
    ("two_starts", [N("Start", "Start"), setf("A", a="1"), setf("B", b="1"), setf("C", c="1")],
     [C("Start", "A"), C("Start", "B"), C("A", "C")], ["A", "B"], "CS03"),
    ("two_exits", [N("Start", "Start"), N("Gate", "If", {"condition": "{{ true }}"}), setf("Yes", y="1"), setf("No", n="1")],
     [C("Start", "Gate"), C("Gate", "Yes", "true"), C("Gate", "No", "false")], ["Gate"], "CS04"),
    ("approval", [N("Start", "Start"), setf("A", a="1"), N("Ask", "Approval", {"title": "ok?"}), setf("B", b="1")],
     [C("Start", "A"), C("A", "Ask"), C("Ask", "B", "approved")], ["Ask"], "CS06"),
    ("whole_json", [N("Start", "Start"), setf("A", a="1"), setf("B", b='{{ JSON.stringify($node["Start"].json) }}'), setf("C", c="1")],
     [C("Start", "A"), C("A", "B"), C("B", "C")], ["B"], "CS07"),
    ("reads_inner", [N("Start", "Start"), setf("A", a="1"), setf("B", b="2"), setf("C", c='{{ $node["A"].json.a }}')],
     [C("Start", "A"), C("A", "B"), C("B", "C")], ["A", "B"], "CS08"),
    ("hole", [N("Start", "Start"), setf("A", a="1"), setf("Mid", m="2"), setf("B", b="3")],
     [C("Start", "A"), C("A", "Mid"), C("Mid", "B")], ["A", "B"], "CS05"),
    ("trigger", [N("Start", "Start"), setf("A", a="1")], [C("Start", "A")], ["Start", "A"], "CS02"),
    ("nothing", [N("Start", "Start"), setf("A", a="1")], [C("Start", "A")], [], "CS01"),
    ("unknown", [N("Start", "Start"), setf("A", a="1")], [C("Start", "A")], ["Ghost"], "CS01"),
])
def test_a_refusal_is_a_422_with_its_code_and_writes_nothing(api, name, nodes, conns, selection, code):
    wid = make(api, wf(None, nodes, conns))
    before = snapshot(api, wid)
    for dry in (True, False):
        detail = extract(api, wid, selection, dry_run=dry, expect=422)
        assert detail["code"] == code and detail["message"] and isinstance(detail["nodes"], list), detail
    assert snapshot(api, wid) == before


def test_a_loop_cut_in_half_and_an_agent_without_its_tools_are_refused(api):
    loop = wf(None, [N("Start", "Start"), N("Each", "Loop", {}, 100), setf("Body", b="1"), setf("After", a="1")],
              [C("Start", "Each"), C("Each", "Body", "loop"), C("Body", "Each"), C("Each", "After", "done")])
    wid = make(api, loop)
    d = extract(api, wid, ["Body"], expect=422)
    assert d["code"] in ("CS09", "CS03", "CS04", "CS05"), d                        # a half loop is never accepted
    assert d["code"] == "CS09", "a loop's back-edge crossing the boundary must be named CS09 (section 7.3)"


def test_a_child_that_already_calls_the_parent_would_make_a_cycle_and_is_refused(api):
    # the parent P holds a call to workflow Q; Q is published; now we try to extract a selection that contains the call to Q
    q = make(api, {"name": uniq("s8t1-q"), "settings": {}, "nodes": [N("Inputs", "ExecuteWorkflowTrigger", {"inputSource": "passthrough"}),
                                                                  setf("Mark", m="1")],
                   "connections": [C("Inputs", "Mark")]})
    publish(api, q)
    p = make(api, wf(None, [N("Start", "Start"), N("CallQ", "ExecuteWorkflow", {"workflowId": q, "version": "latest", "inputMode": "passThrough"}),
                            setf("After", a="1")], [C("Start", "CallQ"), C("CallQ", "After")]))
    # now Q is made to call P (a draft-only edit of Q); extracting CallQ into a new child R is harmless, but extracting a node
    # that makes R call P-then-Q cycles back: Q -> P -> R -> Q
    api.put(f"/workflows/{q}", {"nodes": [N("Inputs", "ExecuteWorkflowTrigger", {"inputSource": "passthrough"}),
                                          N("CallP", "ExecuteWorkflow", {"workflowId": p, "version": "draft", "inputMode": "passThrough"})],
                                "connections": [C("Inputs", "CallP")]})
    d = extract(api, p, ["CallQ"], expect=422)
    assert d["code"] == "CS10", d


def test_dry_run_reports_the_same_plan_a_real_run_then_performs(api):
    body = wf(None, [N("Start", "Start"), setf("A", a="{{ $json.body.x }}"), setf("B", b="{{ $json.a + 1 }}"), setf("C", c="{{ $json.b }}")],
              [C("Start", "A"), C("A", "B"), C("B", "C")])
    wid = make(api, body)
    name = uniq("same-plan")
    plan = api.post(f"/workflows/{wid}/extract-subworkflow", {"nodes": ["A", "B"], "name": name, "dry_run": True}).json()
    real = api.post(f"/workflows/{wid}/extract-subworkflow", {"nodes": ["A", "B"], "name": name, "dry_run": False}).json()
    assert plan["plan"]["call_node"] == real["plan"]["call_node"] and plan["plan"]["trigger_node"] == real["plan"]["trigger_node"]
    assert [n["name"] for n in plan["child_definition"]["nodes"]] == [n["name"] for n in real["child_definition"]["nodes"]]
    assert plan["warnings"] == real["warnings"]


def test_extracting_twice_in_a_row_does_not_leave_a_dangling_reference(api):
    body = wf(None, [hook(), setf("A", a="{{ $json.body.x }}"), setf("B", b="{{ $json.a + 1 }}"), setf("C", c="{{ $json.b }}")],
              [C("Start", "A"), C("A", "B"), C("B", "C")])
    wid = make(api, body)
    extract(api, wid, ["A", "B"])
    d = extract(api, wid, ["A", "B"], expect=422)                                 # the nodes are gone now
    assert d["code"] == "CS01"
    assert "WF006" not in codes(check(api, wid))
    assert run(api, wid, {"x": 1})["status"] == "success"


def test_the_unsaved_definition_in_the_request_is_what_gets_extracted_and_saved(api):
    stored = wf(None, [N("Start", "Start"), setf("Old", o="1")], [C("Start", "Old")])
    wid = make(api, stored)
    unsaved = wf(None, [N("Start", "Start"), setf("A", a="{{ $json.body.x }}"), setf("B", b="{{ $json.a + 1 }}"), setf("C", c="{{ $json.b }}")],
                 [C("Start", "A"), C("A", "B"), C("B", "C")])
    r = extract(api, wid, ["A", "B"], definition={"nodes": unsaved["nodes"], "connections": unsaved["connections"]})
    names = {n["name"] for n in get(api, wid)["definition"]["nodes"]}
    assert "Old" not in names and "C" in names and r["plan"]["call_node"] in names


# ── DEFECT S8-2: the child's inputs are copied, as required, from a static shape that the manual Start node over-promises ──


def test_converting_the_steps_after_a_manual_start_keeps_the_workflow_runnable(api):
    body = wf(None, [N("Start", "Start"), setf("A", a="{{ $json.body.x }}"), setf("B", b="{{ $json.a + 1 }}"), setf("C", c="{{ $json.b }}")],
              [C("Start", "A"), C("A", "B"), C("B", "C")])
    wid = make(api, body)
    assert run(api, wid, {"x": 1})["status"] == "success"
    extract(api, wid, ["A", "B"])
    after = run(api, wid, {"x": 1})
    assert after["status"] == "success", after["errors"][0]["error"]


# ── DEFECT S8-9: assertions about the moved step's outgoing request cannot hold on the call node, and are not flagged ──────


def test_a_test_that_checked_the_moved_steps_outgoing_request_is_flagged_not_left_to_fail(api):
    tid = "t_5a8b1c01"
    body = wf(None, [
        N("Start", "Start"), setf("Prep", amount="{{ $json.amount }}"),
        N("Pay", "HttpRequest", {"method": "POST", "url": "http://pay.bank.example/pay", "responseType": "json",
                                 "body": {"amount": "{{ $json.amount }}"}}, 400),
        setf("Done", ok="yes")], [C("Start", "Prep"), C("Prep", "Pay"), C("Pay", "Done")])
    body["tests"] = [{"id": tid, "name": "pays the amount", "target": {"kind": "workflow"}, "input": {"items": [{"amount": 5}]},
                      "mocks": {"Pay": {"http": {"status": 201, "body": {"ok": True}}}}, "category": "normal",
                      "expect": [{"node": "Pay", "subject": "request", "item": "any", "path": "body.amount", "op": "eq", "value": 5}]}]
    wid = make(api, body)
    before = api.post(f"/workflows/{wid}/tests/run", {}).json()["results"][0]
    assert before["status"] == "passed", before["failures"]
    res = extract(api, wid, ["Pay"])
    after = api.post(f"/workflows/{wid}/tests/run", {}).json()["results"][0]
    stored = next(t for t in get(api, wid)["definition"]["tests"] if t["id"] == tid)
    flagged = tid in {a["id"] for a in res["affected_tests"]} or bool(stored.get("stale"))
    assert after["status"] == "passed" or flagged, "the test now fails and nothing told the author the behaviour moved"
