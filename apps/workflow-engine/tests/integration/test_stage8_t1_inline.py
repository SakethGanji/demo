"""Stage 8 T1 (tester): inline-child tests (DESIGN-STAGE8-REUSE 6.3) with a grandchild, nested mocks, version pins, a failing
child and ``mode: each``; plus "used by" after the writes B10 did not try (template install, delete of the parent)."""

from __future__ import annotations

import uuid

import pytest

from ._stage8_t1_support import C, N, call_node, check, child_body, make, parent_body, publish, run, save, setf, trigger, uniq

pytestmark = pytest.mark.integration


def tid() -> str:
    return "t_" + uuid.uuid4().hex[:8]


def inner(tag: str) -> dict:
    """A mock for the middle's call to the grandchild: it must fit the grandchild's derived output (the mail step's fields + tag)."""
    return {"items": [{"amount": 5, "success": True, "to": "ops@bank.test", "subject": "s", "tag": tag}]}


def grandchild() -> dict:
    """Inputs -> Notify (SendEmail, an outside step) -> Mark."""
    return {"name": uniq("s8t1-grand"), "settings": {},
            "nodes": [trigger(("amount", "number", False)),
                      N("Notify", "SendEmail", {"toEmail": "ops@bank.test", "subject": "s", "body": "b"}, 200),
                      setf("Mark", 400, tag="grand")],
            "connections": [C("Inputs", "Notify"), C("Notify", "Mark")]}


def middle(grand_id: str, *, version="draft", defaults: dict | None = None) -> dict:
    """Inputs -> Inner (calls the grandchild) -> Wrap."""
    body = {"name": uniq("s8t1-mid"), "settings": {},
            "nodes": [trigger(("amount", "number", False)), call_node("Inner", grand_id, version), setf("Wrap", 600, via="middle")],
            "connections": [C("Inputs", "Inner"), C("Inner", "Wrap")]}
    if defaults is not None:
        body["test_defaults"] = defaults
    return body


def outer(mid_id: str, test: dict, version="draft") -> dict:
    body = parent_body(mid_id, version)
    body["tests"] = [{"id": tid(), "name": "nested", "target": {"kind": "workflow"}, "input": {"items": [{"amount": 5}]},
                      "subworkflows": {"call": "inline"}, "mocks": {}, "expect": [], "category": "normal", **test}]
    return body


def only(report: dict) -> dict:
    assert len(report["results"]) == 1, report
    return report["results"][0]


def run_tests(api, wid: str, **body) -> dict:
    r = api.post(f"/workflows/{wid}/tests/run", body)
    assert r.status_code == 200, r.text
    return r.json()


def failing(res: dict) -> list:
    return [(f["kind"], f.get("node"), f["message"]) for f in res["failures"]]


def test_a_parent_test_can_mock_the_grandchild_call_by_its_path_and_assert_on_it(api):
    g = make(api, grandchild())
    m = make(api, middle(g))
    body = outer(m, {"mocks": {"call/Inner": inner("mocked-grand")},
                     "expect": [{"node": "call/Inner", "path": "tag", "op": "eq", "value": "mocked-grand"},
                                {"node": "call/Wrap", "path": "via", "op": "eq", "value": "middle"},
                                {"node": "call/Wrap", "path": "tag", "op": "eq", "value": "mocked-grand"}]})
    res = only(run_tests(api, make(api, body)))
    assert res["status"] == "passed", failing(res)
    assert res["node_status"]["call/Inner"] == "success"


def test_an_unmocked_grandchild_call_fails_naming_the_path_of_the_inner_node(api):
    g = make(api, grandchild())
    m = make(api, middle(g))
    res = only(run_tests(api, make(api, outer(m, {"expect": [{"node": "call/Wrap", "op": "status", "value": "success"}]}))))
    assert res["status"] == "error"
    (f,) = [f for f in res["failures"] if f["kind"] == "unmocked"]
    assert "call/Inner" in f["message"]


def test_the_middles_own_default_mock_answers_the_grandchild_call(api):
    g = make(api, grandchild())
    m = make(api, middle(g, defaults={"mocks": {"Inner": inner("from-defaults")}}))
    res = only(run_tests(api, make(api, outer(m, {"expect": [{"node": "call/Wrap", "path": "tag", "op": "eq", "value": "from-defaults"}]}))))
    assert res["status"] == "passed", failing(res)


def test_a_parent_path_mock_beats_the_middles_default_for_the_grandchild_call(api):
    g = make(api, grandchild())
    m = make(api, middle(g, defaults={"mocks": {"Inner": inner("from-defaults")}}))
    body = outer(m, {"mocks": {"call/Inner": inner("from-parent")},
                     "expect": [{"node": "call/Wrap", "path": "tag", "op": "eq", "value": "from-parent"}]})
    res = only(run_tests(api, make(api, body)))
    assert res["status"] == "passed", failing(res)


def test_the_inline_child_runs_the_version_the_pin_names_not_the_draft_that_moved_on(api):
    kid = child_body("one")
    kid["test_defaults"] = {}
    kid_id = make(api, kid)
    publish(api, kid_id)
    save(api, kid_id, child_body("two"))                                              # draft is 'two', published is 'one'
    for version, want in (("latest", "one"), (1, "one"), ("draft", "two")):
        body = outer(kid_id, {"expect": [{"node": "call/Mark", "path": "tag", "op": "eq", "value": want}]}, version=version)
        res = only(run_tests(api, make(api, body)))
        assert res["status"] == "passed", (version, failing(res))


def test_an_inline_child_whose_pin_cannot_resolve_is_a_test_error_not_a_server_error(api):
    kid_id = make(api, child_body("never-published"))
    body = outer(kid_id, {"expect": [{"node": "call", "op": "status", "value": "success"}]}, version="latest")
    res = only(run_tests(api, make(api, body)))
    assert res["status"] in ("error", "failed")
    assert any("published" in f["message"] or "SUBWORKFLOW" in f["message"] or "version" in f["message"] for f in res["failures"]), failing(res)


def test_a_failing_inline_child_fails_the_test_with_the_childs_reason(api):
    kid = {"name": uniq("s8t1-boom"), "settings": {}, "nodes": [
        trigger(("n", "number", False)), N("Boom", "StopAndError", {"errorType": "error", "message": "child said no"}, 200)],
        "connections": [C("Inputs", "Boom")]}
    kid_id = make(api, kid)
    body = outer(kid_id, {"expect": [{"node": "call", "op": "status", "value": "success"}]})
    res = only(run_tests(api, make(api, body)))
    assert res["status"] in ("failed", "error")
    assert "child said no" in str(res["failures"]) + str(res.get("error", ""))


def test_each_mode_over_two_items_gives_the_assertions_both_children(api):
    g = make(api, grandchild())
    m = make(api, middle(g, defaults={"mocks": {"Inner": inner("g")}}))
    body = parent_body(m, "draft")
    body["nodes"][1]["parameters"]["mode"] = "each"
    body["tests"] = [{"id": tid(), "name": "two items", "target": {"kind": "workflow"}, "input": {"items": [{"amount": 1}, {"amount": 2}]},
                      "subworkflows": {"call": "inline"}, "mocks": {}, "category": "normal",
                      "expect": [{"node": "call", "op": "status", "value": "success"}]}]
    res = only(run_tests(api, make(api, body)))
    assert res["status"] == "passed", failing(res)
    assert len(res["outputs"]["call"]["main"]) == 2


def test_a_persisted_test_run_writes_only_test_rows_for_inline_children(api, test_db):
    g = make(api, grandchild())
    m = make(api, middle(g, defaults={"mocks": {"Inner": inner("g")}}))
    wid = make(api, outer(m, {"expect": [{"node": "call/Wrap", "path": "via", "op": "eq", "value": "middle"}]}))
    res = only(run_tests(api, wid))
    assert res["status"] == "passed", failing(res)
    # the default test run persists children as mode 'test' rows (B10); no row it wrote may be a real sub_workflow run
    modes = {r[0] for r in test_db.query("SELECT mode FROM executions WHERE workflow_id = ANY(%s)", ([m, g],))}
    assert modes <= {"test"}


# ── used-by after more kinds of write ───────────────────────────────────────────────────────────────────────────────


def used_by(api, child: str):
    return sorted((u["parent_workflow_id"], u["node"], u["where"]) for u in api.get(f"/workflows/{child}/used-by").json())


def test_used_by_follows_a_template_install_a_parent_delete_and_a_node_rename(api):
    P = api.post("/projects", {"name": uniq("s8t1ub")}).json()["id"]
    body = api.post(f"/projects/{P}/templates/refund-triage/install", {"run_tests": False}).json()
    ids = {w["key"]: w["id"] for w in body["workflows"]}
    elig = ids["eligibility"]
    rows = api.get(f"/workflows/{elig}/used-by").json()
    assert [(r["parent_workflow_id"], r["node"], r["where"]) for r in rows] == [(ids["main"], "Check eligibility", "draft")]
    # renaming the calling node in the parent moves the row (same parent, new node name)
    d = api.get(f"/workflows/{ids['main']}").json()["definition"]
    nodes = [{**n, "name": "Eligibility check"} if n["name"] == "Check eligibility" else n for n in d["nodes"]]
    conns = [{**c, "source_node": "Eligibility check" if c["source_node"] == "Check eligibility" else c["source_node"],
              "target_node": "Eligibility check" if c["target_node"] == "Check eligibility" else c["target_node"]} for c in d["connections"]]
    r = api.put(f"/workflows/{ids['main']}", {"nodes": nodes, "connections": conns})
    assert r.status_code == 200, r.text
    assert [u[1] for u in used_by(api, elig)] == ["Eligibility check"]
    # deleting the parent removes its rows, and then the published child can be deleted
    assert api.delete(f"/workflows/{ids['main']}").status_code == 200
    assert used_by(api, elig) == []
    assert api.delete(f"/workflows/{elig}").status_code == 200


def test_a_disabled_call_still_counts_as_a_user_of_the_child(api):
    kid = make(api, child_body("x"))
    publish(api, kid)
    body = parent_body(kid, "latest")
    body["nodes"][1]["disabled"] = True
    parent = make(api, body)
    publish(api, parent)
    # a disabled node never runs, but its published version still names the child: deleting the child must not be silently allowed
    r = api.delete(f"/workflows/{kid}")
    assert r.status_code == 409, "a published parent that still lists the child (even disabled) must protect it"
