"""Stage 6 B4: the pure parts of the builder loop (no database, no model): op parsing and alias handling, the section 6.4 allowlist, op
ordering and name resolution, the builder-only repair FIX13, node-assist scope and confinement, views and verdicts."""

from __future__ import annotations

import json

import pytest

from src.engine.wf_ai import AIError
from src.services import ai_builder as B
from src.services import ai_grounding
from src.services import ai_node_assist as NA
from src.services.workflow_checker import StaticLookups


def make_ctx(**over) -> B.Ctx:
    base = dict(project_id="proj_x", team_id="default", secrets={"s3cr3t-value-123"}, lookups=StaticLookups(), operations={}, api_ops=[],
                catalog=ai_grounding.full_catalog(), variable_names=[], existing_workflows=[], egress=[])
    base.update(over)
    return B.Ctx(**base)


def defn(*names: str, tests=None) -> dict:
    nodes = [{"name": n, "type": "Set", "parameters": {"mode": "manual", "fields": []}, "position": {"x": 0, "y": 0}} for n in names]
    return {"name": "wf", "nodes": nodes, "connections": [], "tests": tests or [], "test_defaults": {}}


def approved(node: str, tid: str = "t_00000001") -> dict:
    return {"id": tid, "name": "t", "target": {"kind": "workflow"}, "input": {"items": []}, "mocks": {node: {"items": [{}]}}, "expect": [],
            "status": "approved", "locked": True}


def ops(*items: tuple[str, dict]) -> list[B.ParsedOp]:
    return [B.ParsedOp(i, op, args) for i, (op, args) in enumerate(items)]


def apply(d, *items, spec=None, ctx=None, max_nodes=40):
    st = B.LoopState(ctx=ctx or make_ctx(), spec=spec, max_nodes=max_nodes)
    return B.apply_round_ops(d, ops(*items), st, 0)


# ── parsing ──────────────────────────────────────────────────────────────────────────────────


def test_args_json_is_parsed_per_op_and_a_bad_one_fails_only_itself():
    reply = {"ops": [{"op": "add_node", "args_json": json.dumps({"name": "A", "type": "Set"})},
                     {"op": "add_node", "args_json": "{not json"},
                     {"op": "connect", "args_json": '```json\n{"source": "A", "target": "B"}\n```'},
                     "not an op", {"op": 7},
                     {"op": "add_node", "args": {"name": "C", "type": "Set"}}]}
    parsed, errors = B.parse_ops(reply, 60)
    assert [(p.index, p.op) for p in parsed] == [(0, "add_node"), (2, "connect"), (5, "add_node")]
    assert [e["op_index"] for e in errors] == [1, 3, 4] and "bad_args_json" in errors[0]["message"]


def test_ops_past_the_limit_are_refused_not_dropped_silently():
    reply = {"ops": [{"op": "add_node", "args_json": json.dumps({"name": f"N{i}", "type": "Set"})} for i in range(5)]}
    parsed, errors = B.parse_ops(reply, 3)
    assert len(parsed) == 3 and [e["op_index"] for e in errors] == [3, 4] and "Too many ops" in errors[0]["message"]


def test_common_argument_aliases_and_wrappers_are_understood():
    n = B.normalise_args("add_node", {"node": {"node_type": "If", "node_name": "Check", "parameters": '{"condition": "{{ 1 }}"}', "junk": 1}})
    assert n == {"type": "If", "name": "Check", "parameters": {"condition": "{{ 1 }}"}}
    assert B.normalise_args("connect", {"from": "A", "to": "B", "port": "true"}) == {"source": "A", "target": "B", "source_output": "true"}
    assert B.normalise_args("set_parameter", {"node_name": "A", "param": "x.y", "value": '{"a": 1}'}) == {"node": "A", "path": "x.y", "value": {"a": 1}}
    assert B.normalise_args("set_parameter", {"node": "A", "path": "cond", "value": "{{ $json.a > 1 }}"})["value"] == "{{ $json.a > 1 }}"
    assert B.normalise_args("rename_node", {"old_name": "A", "new_name": "B"}) == {"old": "A", "new": "B"}
    assert B.normalise_args("set_parameters", {"name": "A", "parameters": '{"k": 1}'}) == {"node": "A", "parameters": {"k": 1}}


# ── the allowlist (6.4) ──────────────────────────────────────────────────────────────────────


def test_ops_outside_the_enum_and_test_edits_are_refused_with_a_reason():
    d, errors, log = apply(defn("A"), ("update_test", {"test_id": "t_1", "changes": {}}), ("remove_test", {"test_id": "t_1"}), ("delete_everything", {}))
    assert [e["op"] for e in errors] == ["update_test", "remove_test", "delete_everything"] and "fixed" in errors[0]["message"]
    assert d == defn("A") and [l["result"] for l in log] == ["error"] * 3


def test_a_new_test_can_only_arrive_as_a_draft():
    d, errors, _ = apply(defn("A"), ("add_test", {"test": {"name": "x", "status": "approved"}}), ("add_test", {"test": {"name": "y", "locked": True}}))
    assert len(errors) == 2 and d["tests"] == []
    ok, errors2, _ = apply(defn("A"), ("add_test", {"test": {"name": "z", "target": {"kind": "workflow"}, "expect": [{"node": "A", "op": "exists"}]}}))
    assert not errors2 and ok["tests"][0]["status"] == "draft"


def test_nodes_named_by_approved_tests_cannot_be_removed_renamed_or_default_mocked():
    d = defn("A", "B", tests=[approved("A")])
    for item in (("remove_node", {"name": "A"}), ("rename_node", {"old": "A", "new": "Z"}), ("set_test_default_mock", {"node": "A", "mock": {"items": [{}]}})):
        out, errors, _ = apply(d, item)
        assert len(errors) == 1 and out == d, item
    out, errors, _ = apply(d, ("remove_node", {"name": "B"}), ("rename_node", {"old": "B", "new": "C"}))
    assert [n["name"] for n in out["nodes"]] == ["A"] or not errors  # B may go; only A is protected
    # a swap (remove, then add the same name later in the batch) is allowed; add-then-remove is not
    out, errors, _ = apply(d, ("remove_node", {"name": "A"}), ("add_node", {"name": "A", "type": "If", "parameters": {"condition": "{{ true }}"}}))
    assert not errors and next(n for n in out["nodes"] if n["name"] == "A")["type"] == "If"
    out, errors, _ = apply(d, ("add_node", {"name": "A2", "type": "Set"}), ("remove_node", {"name": "A"}), ("add_node", {"name": "A3", "type": "Set"}))
    assert len(errors) == 1 and any(n["name"] == "A" for n in out["nodes"])


def test_unknown_node_types_are_read_as_the_closest_catalog_type_or_refused():
    spec = {"steps": [{"name": "Gate", "node_type": "If"}]}
    d, errors, log = apply(defn("A"), ("add_node", {"name": "Gate", "type": "if", "parameters": {}}), ("add_node", {"name": "Odd", "type": "TeleportNode"}), spec=spec)
    assert next(n for n in d["nodes"] if n["name"] == "Gate")["type"] == "If" and "read as 'If'" in log[0]["message"]
    assert len(errors) == 1 and "Unknown node type" in errors[0]["message"] and not any(n["name"] == "Odd" for n in d["nodes"])


def test_ops_that_carry_a_secret_are_refused_and_never_logged_in_clear():
    d, errors, log = apply(defn("A"), ("set_parameter", {"node": "A", "path": "mode", "value": "token s3cr3t-value-123"}),
                           ("set_parameter", {"node": "A", "path": "mode", "value": "sk-abcdefghijklmnopqrstuvwxyz0123"}))
    assert len(errors) == 2 and all("credential" in e["message"] for e in errors) and d == defn("A")
    assert "s3cr3t-value-123" not in json.dumps(log) and "sk-abcdefgh" not in json.dumps(log)


def test_http_urls_outside_the_egress_allowlist_are_refused_literal_or_not():
    ctx = make_ctx(egress=["api.bank.test", "*.internal.bank"])
    ok = ("add_node", {"name": "Call", "type": "HttpRequest", "parameters": {"url": "https://api.bank.test/x", "method": "GET"}})
    bad = ("add_node", {"name": "Exfil", "type": "HttpRequest", "parameters": {"url": "https://exfil.example/steal", "method": "POST"}})
    templ = ("add_node", {"name": "Dyn", "type": "HttpRequest", "parameters": {"url": "{{ $json.u }}", "method": "GET"}})
    d, errors, _ = apply(defn("A"), ok, bad, templ, ctx=ctx)
    names = [n["name"] for n in d["nodes"]]
    assert "Call" in names and "Dyn" in names and "Exfil" not in names and len(errors) == 1 and "egress allowlist" in errors[0]["message"]
    d2, errors2, _ = apply(d, ("set_parameter", {"node": "Call", "path": "url", "value": "https://exfil.example/"}),
                           ("set_parameters", {"node": "Call", "parameters": {"url": "https://x.internal.bank/ok"}}), ctx=ctx)
    assert len(errors2) == 1 and next(n for n in d2["nodes"] if n["name"] == "Call")["parameters"] == {"url": "https://x.internal.bank/ok"}
    d3, e3, _ = apply(defn("A"), bad, ctx=make_ctx(egress=[]))        # no allowlist configured: nothing to enforce
    assert not e3


def test_the_node_limit_stops_add_node():
    d, errors, _ = apply(defn("A", "B"), ("add_node", {"name": "C", "type": "Set"}), ("add_node", {"name": "D", "type": "Set"}), max_nodes=3)
    assert len(d["nodes"]) == 3 and len(errors) == 1 and "limit" in errors[0]["message"]


# ── order and names ──────────────────────────────────────────────────────────────────────────


def test_connects_listed_before_their_nodes_still_work_when_nothing_is_removed():
    d, errors, _ = apply(defn(), ("connect", {"source": "A", "target": "B"}), ("set_parameter", {"node": "B", "path": "mode", "value": "manual"}),
                         ("add_node", {"name": "A", "type": "Set"}), ("add_node", {"name": "B", "type": "Set"}))
    assert not errors and len(d["connections"]) == 1


def test_node_names_that_differ_by_case_spacing_or_a_typo_are_resolved_when_unique():
    d, errors, _ = apply(defn("Over limit?", "Mark for review"), ("connect", {"source": "over limit?", "target": "Mark for  review"}),
                         ("set_parameter", {"node": "Mark for reviw", "path": "mode", "value": "manual"}))
    assert not errors and d["connections"][0]["source_node"] == "Over limit?" and d["connections"][0]["target_node"] == "Mark for review"
    d2, errors2, _ = apply(defn("Check 1", "Check 2"), ("set_parameter", {"node": "check", "path": "mode", "value": "x"}))
    assert len(errors2) == 1                       # ambiguous: not guessed


# ── FIX13 and finalize ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cond,wrapped", [("$json.amount > 500", True), ("$json.body.ok == true", True), ("{{ $json.a > 1 }}", False), ("", False), ("true", False)])
def test_fix13_wraps_a_bare_if_condition(cond, wrapped):
    d = {"nodes": [{"name": "Gate", "type": "If", "parameters": {"condition": cond}}], "connections": []}
    out, fixes = B.local_repairs(d)
    assert (out["nodes"][0]["parameters"]["condition"] == "{{ " + cond + " }}") == wrapped and bool(fixes) == wrapped


def test_finalize_places_new_nodes_keeps_base_positions_and_regenerates_its_own_default_mocks():
    ctx = make_ctx()
    st = B.LoopState(ctx=ctx, spec=None, max_nodes=40)
    d = defn("Start node", "Call")
    d["nodes"][0].update(type="Webhook", parameters={"method": "POST", "path": "p"}, position={"x": 40, "y": 40})
    d["nodes"][1].update(type="HttpRequest", parameters={"method": "GET", "url": "https://api.bank.test/a"})
    d["connections"] = [{"source_node": "Start node", "target_node": "Call", "source_output": "main", "target_input": "main"}]
    out, fixes, warns, _ = B.finalize(d, st, {"Start node"})
    assert out["nodes"][0]["position"] == {"x": 40, "y": 40} and out["nodes"][1]["position"] != {"x": 0, "y": 0}
    assert out["test_defaults"]["mocks"]["Call"]["http"]["status"] == 200 and "Call" in st.auto_mocks and warns
    # the node changes kind: its server-made default mock follows it; one the AI set itself is never touched
    out["nodes"][1].update(type="SendEmail", parameters={})
    out["test_defaults"]["mocks"]["Mine"] = {"items": [{"x": 1}]}
    out2, _, _, _ = B.finalize(out, st, {"Start node"})
    assert "http" not in out2["test_defaults"]["mocks"].get("Call", {}) and out2["test_defaults"]["mocks"]["Mine"] == {"items": [{"x": 1}]}


# ── node assist: scope and confinement ──────────────────────────────────────────────────────


def test_node_assist_scope_allows_the_node_its_neighbours_and_one_new_node():
    scope = {"B", "A", "C"}

    def err(op, args, added=()):
        return NA._scope_error(B.ParsedOp(0, op, args), scope, set(added))

    assert err("set_parameter", {"node": "B"}) is None and err("set_parameter", {"node": "Z"})
    assert err("connect", {"source": "A", "target": "B"}) is None and err("connect", {"source": "A", "target": "Z"})
    assert err("add_node", {"name": "N", "type": "Set"}) is None and err("add_node", {"name": "M", "type": "Set"}, added=["N"])
    assert err("connect", {"source": "B", "target": "N"}, added=["N"]) is None
    assert err("rename_node", {"old": "B", "new": "X"}) is None and err("add_test", {"test": {}}) and err("set_interface", {"interface": {}})


def test_confine_undoes_repairs_to_untouched_nodes_and_keeps_tests_and_defaults_from_the_base():
    base = defn("A", "B", "C", "D", tests=[approved("A")])
    base["connections"] = [{"source_node": s_, "target_node": t_, "source_output": "main", "target_input": "main"} for s_, t_ in (("A", "B"), ("B", "C"), ("C", "D"))]
    after = json.loads(json.dumps(base))
    after["nodes"][0]["parameters"] = {"changed": True}                  # a repair reached A, which is not touched
    after["nodes"][1]["parameters"] = {"mode": "manual", "fields": [{"name": "x", "value": "1"}]}
    after["connections"] = [c for c in after["connections"] if c["source_node"] != "C"]      # ... and dropped C -> D, both untouched
    after["tests"] = []
    after["test_defaults"] = {"mocks": {"B": {"items": [{}]}}}
    out = NA._confine(base, after, {"B"})
    assert out["nodes"][0]["parameters"] == base["nodes"][0]["parameters"] and out["nodes"][1]["parameters"]["fields"]
    assert out["tests"] == base["tests"] and out["test_defaults"] == {}
    assert {(c["source_node"], c["target_node"]) for c in out["connections"]} == {("A", "B"), ("B", "C"), ("C", "D")}


# ── views, verdicts, errors ─────────────────────────────────────────────────────────────────


def test_the_view_never_carries_prompts_and_always_carries_usage_and_policy():
    row = {"id": "p_1", "status": "ready", "llm_calls": 3, "input_tokens": 10, "output_tokens": 2, "cost_usd": None, "duration_ms": 5, "model": "gemini-3.5-flash",
           "provider": "gemini", "region": "us", "base_definition": {"secret": 1}, "system": "prompt text", "user": "prompt text"}
    v = B.view(row)
    assert v["usage"] == {"llm_calls": 3, "input_tokens": 10, "output_tokens": 2, "cost_usd": None, "duration_ms": 5}
    assert v["policy"] == {"model": "gemini-3.5-flash", "provider": "gemini", "region": "us"}
    # B5 follow-up (a): the view now carries the draft the proposal started from (the studio draws "removed" ghosts from it); still no prompts
    assert v["base_definition"] == {"secret": 1} and "system" not in v and "user" not in v


def test_ai_errors_map_to_the_documented_http_errors():
    assert B.ai_error_to_http(AIError("not_configured", "x")).http_status == 503
    assert B.ai_error_to_http(AIError("model_not_allowed", "x")).http_status == 403
    for code in ("bad_json", "provider_failed", "timeout", "call_budget", "deadline", "prompt_too_large"):
        e = B.ai_error_to_http(AIError(code, "x"), proposal_id="p_1")
        assert e.http_status == 502 and e.code == "ai_failed" and e.detail()["ai_code"] == code and e.detail()["proposal_id"] == "p_1"


def test_actor_rules():
    assert B.clean_actor("  Asha ") == "Asha"
    for bad in (None, "", "   ", "x" * 81):
        with pytest.raises(B.ActorRequired):
            B.clean_actor(bad)


def test_the_round_ranking_prefers_fewer_errors_then_fewer_failed_tests_then_fewer_missing_steps():
    def r(n, errors=0, failed=0, missing=0):
        rr = B.RoundResult(n=n, defn={}, check={"errors": errors, "grounding": []}, coverage={"steps_missing": ["x"] * missing, "steps_wrong_type": []},
                           tests={"results": []}, counts={"approved_total": 2, "approved_failed": failed})
        return rr

    assert r(1, errors=1).key > r(2, errors=0, failed=5).key
    assert r(1, failed=2).key > r(2, failed=1).key and r(1, failed=1, missing=1).key > r(2, failed=1, missing=0).key
    assert r(1).green and r(1).clean and not r(1, missing=1).clean and r(1, missing=1).green
    assert not B.RoundResult(n=0, defn={}, check={"errors": 0}, tests=None).green          # tests that never ran are not green


def test_tests_by_ref_resolves_the_t_style_refs_a_model_cites():
    spec = {"tests": [{"id": "t_00000001", "name": "first"}, {"id": "t_00000002", "name": "second"}]}
    ids = {"t_00000001", "t_00000002"}
    assert B.resolve_test_ref("T2", spec, ids) == "t_00000002" and B.resolve_test_ref("t_00000001", spec, ids) == "t_00000001"
    assert B.resolve_test_ref("first", spec, ids) == "t_00000001" and B.resolve_test_ref("T9", spec, ids) == "T9"


def test_distill_tests_gives_the_model_the_trace_and_what_the_failing_node_actually_emitted():
    report = {"results": [
        {"test_id": "t_1", "name": "ok", "status": "passed"},
        {"test_id": "t_2", "name": "bad", "status": "failed", "ports_taken": {"Gate": ["false"], "Reply": ["main"]}, "node_status": {"Review": "not_run"},
         "assertions": [{"node": "Gate", "port": "true", "op": "count", "expected": 1, "actual": 0, "passed": False, "message": "port true had 0 items"},
                        {"node": "Reply", "op": "status", "passed": True}],
         "failures": [{"kind": "assertion", "node": "Gate", "message": "Gate: port true had 0 items"},
                      {"kind": "unmocked", "node": "Call", "message": "no mock"}],
         "outputs": {"Gate": {"false": [{"amount": 200}]}}, "requests": {}}]}
    out = B.distill_tests(report)
    assert len(out) == 1 and out[0]["trace"] == ["Gate:false", "Reply:main"] and out[0]["not_run"] == ["Review"]
    assert out[0]["failed"][0]["expected"] == 1 and any(f.get("kind") == "unmocked" for f in out[0]["failed"])
    assert out[0]["actual_outputs"]["Gate"] == {"amount": 200}


# ── FIX14: spec step names are the contract ─────────────────────────────────────────────────


def spec_steps(*pairs: tuple[str, str]) -> dict:
    return {"steps": [{"name": n, "node_type": t} for n, t in pairs]}


def typed(**names_types: str) -> dict:
    nodes = [{"name": n, "type": t, "parameters": {}, "position": {"x": 0, "y": 0}} for n, t in names_types.items()]
    return {"nodes": nodes, "connections": [], "tests": [], "test_defaults": {}}


def test_fix14_renames_a_node_that_is_a_spec_step_under_another_name_and_follows_references():
    d = typed(**{"Check amount": "If", "Mark": "Set"})
    d["nodes"][1]["parameters"] = {"mode": "manual", "fields": [{"name": "a", "value": '{{ $node["Check amount"].json.x }}'}]}
    d["connections"] = [{"source_node": "Check amount", "target_node": "Mark", "source_output": "true", "target_input": "main"}]
    out, fixes = B.align_step_names(d, spec_steps(("Over limit?", "If"), ("Mark for review", "Set")))
    assert [n["name"] for n in out["nodes"]] == ["Over limit?", "Mark for review"]
    assert out["connections"][0]["source_node"] == "Over limit?" and '$node["Over limit?"]' in out["nodes"][1]["parameters"]["fields"][0]["value"]
    assert [f["code"] for f in fixes] == ["FIX14", "FIX14"]


def test_fix14_pairs_in_order_only_when_the_counts_match_and_never_touches_matching_names():
    spec = spec_steps(("Approve small", "Set"), ("Approve large", "Set"), ("Gate", "If"))
    out, fixes = B.align_step_names(typed(**{"Gate": "If", "Set 1": "Set", "Set 2": "Set"}), spec)
    assert [n["name"] for n in out["nodes"]] == ["Gate", "Approve small", "Approve large"] and len(fixes) == 2
    # one step missing but two unknown nodes of that type: ambiguous, left to the model
    out2, fixes2 = B.align_step_names(typed(**{"Gate": "If", "Approve small": "Set", "Foo": "Set", "Bar": "Set"}), spec)
    assert [n["name"] for n in out2["nodes"]] == ["Gate", "Approve small", "Foo", "Bar"] and not fixes2
    out3, fixes3 = B.align_step_names(typed(**{"Gate": "If"}), spec)           # nothing to pair with
    assert not fixes3 and B.align_step_names(typed(**{"Gate": "If"}), None)[1] == []


def test_fix14_never_renames_onto_a_name_an_approved_test_uses_and_keeps_the_tests_intact():
    d = typed(**{"Whatever": "If"})
    d["tests"] = [approved("Over limit?")]
    out, _ = B.align_step_names(d, spec_steps(("Over limit?", "If")))
    assert out["nodes"][0]["name"] == "Over limit?" and out["tests"][0]["mocks"] == {"Over limit?": {"items": [{}]}}
