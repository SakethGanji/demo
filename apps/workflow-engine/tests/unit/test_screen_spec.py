"""ScreenSpec v1: schema validation, the closed component and binding sets, chips, generation (DESIGN-STAGE5-UIS section 6.3)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.schemas import interface as schema

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "workflows"


@pytest.fixture(scope="module")
def refund() -> dict:
    return json.loads((FIXTURES / "refund_triage_ui.json").read_text())


def spec(*components, **kw) -> dict:
    return {"v": 1, "components": list(components), **kw}


def test_a_minimal_spec_validates_and_defaults_are_filled():
    clean, errors = schema.parse_screen_spec(spec({"type": "heading", "text": "Hi"}))
    assert errors == [] and clean == {"v": 1, "title": "", "layout": "card", "components": [{"type": "heading", "text": "Hi"}]}


@pytest.mark.parametrize("component", [
    {"type": "script", "text": "alert(1)"},                       # not in the closed set
    {"type": "heading", "onclick": "x"},                          # unknown key
    {"type": "amount", "size": "huge"},
    {"type": "callout", "tone": "rainbow"},
    {"type": "link", "value": "http://example.com"},               # https only
    {"type": "link", "value": "javascript:alert(1)"},
    {"type": "field", "bind": "secrets.api_key"},                  # not a binding root
    {"type": "field", "bind": "task.password"},
    {"type": "field", "bind": "run.token"},
    {"type": "text", "template": "{{ __import__('os') }}"},
    {"type": "text", "template": "{{ a + b }}"},
])
def test_bad_components_are_refused(component):
    clean, errors = schema.parse_screen_spec(spec(component))
    assert clean is None and errors and errors[0]["field"].startswith("components[0]")


def test_every_component_type_is_accepted():
    comps = [{"type": t} for t in schema.COMPONENT_TYPES]
    assert schema.parse_screen_spec(spec(*comps))[1] == []


def test_a_screen_may_not_be_a_spec_of_the_wrong_version_or_shape():
    assert schema.parse_screen_spec(spec(v=2))[0] is None
    assert schema.parse_screen_spec("nope")[0] is None
    assert schema.parse_screen_spec({"components": "x"})[0] is None


def test_size_caps_are_screen_too_large():
    many = spec(*[{"type": "divider"}] * (schema.MAX_COMPONENTS + 1))
    _, errors = schema.parse_screen_spec(many)
    assert errors and "SCREEN_TOO_LARGE" in errors[0]["message"]
    fat = spec({"type": "text", "text": "x" * 2000}, *[{"type": "text", "text": "y" * 2000}] * 70)
    _, errors = schema.parse_screen_spec(fat)
    assert errors and "SCREEN_TOO_LARGE" in errors[0]["message"] and "bytes" in errors[0]["message"]


def test_the_exact_limit_is_allowed():
    assert schema.parse_screen_spec(spec(*[{"type": "divider"}] * schema.MAX_COMPONENTS))[1] == []


def test_json_schema_lists_the_closed_component_set():
    s = json.dumps(schema.screen_spec_json_schema())
    assert all(t in s for t in schema.COMPONENT_TYPES)


# ── interface ────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("slug,ok", [("refunds", True), ("a1", True), ("refund-triage-2", True), ("a", False), ("-a", False),
                                     ("Refunds", False), ("has space", False), ("x" * 64, False), ("x" * 63, True), ("a/b", False), ("", False)])
def test_slug_rule(slug, ok):
    clean, errors = schema.parse_interface({"kind": "form", "slug": slug})
    assert (clean is not None) is ok and (errors == []) is ok


def test_unknown_interface_keys_and_kinds_are_refused():
    assert schema.parse_interface({"kind": "wizard", "slug": "ab"})[0] is None
    assert schema.parse_interface({"kind": "form", "slug": "ab", "evil": 1})[0] is None
    assert schema.parse_interface({"kind": "form", "slug": "ab", "access": {"mode": "everyone"}})[0] is None
    assert schema.parse_interface([1])[0] is None


def test_screen_ids_are_closed():
    ok = {"kind": "custom", "slug": "ab", "screens": {"start": spec(), "result": spec(), "task:Manager review": spec()}}
    assert schema.parse_interface(ok)[1] == []
    bad = {"kind": "custom", "slug": "ab", "screens": {"admin": spec()}}
    assert schema.parse_interface(bad)[1][0]["field"] == "screens.admin"


def test_errors_carry_a_field_path():
    _, errors = schema.parse_interface({"kind": "custom", "slug": "ab", "screens": {"start": spec({"type": "nope"})}})
    assert errors[0]["field"] == "screens.start.components[0].type"


def test_the_refund_fixture_interface_round_trips(refund):
    clean, errors = schema.parse_interface(refund["interface"])
    assert errors == [] and clean == refund["interface"]


# ── findings ─────────────────────────────────────────────────────────────────────────────────


def codes(findings):
    return sorted((f["code"], f["severity"]) for f in findings)


def test_the_refund_fixture_has_no_interface_findings(refund):
    assert schema.interface_findings(refund) == []


def test_wf017_trigger_missing_or_wrong_type(refund):
    d = copy.deepcopy(refund)
    d["interface"]["trigger_node"] = "Nope"
    assert codes(schema.interface_findings(d)) == [("WF017", "error")]
    d["interface"]["trigger_node"] = "Classify"
    f = schema.interface_findings(d)
    assert codes(f) == [("WF017", "error")] and "FormTrigger" in f[0]["message"]
    d["interface"]["trigger_node"] = None
    assert codes(schema.interface_findings(d)) == [("WF017", "error")]
    d["interface"].update(kind="chat", trigger_node="Refund request")
    assert "ChatInput" in schema.interface_findings(d)[0]["message"]


def test_wf019_no_result_node_and_a_last_node_with_many_items(refund):
    d = copy.deepcopy(refund)
    del d["interface"]["result_node"]
    assert schema.interface_findings(d) == []                       # the last node is a Respond: one item
    d["nodes"].append({"name": "Rows", "type": "DataTable", "parameters": {}})
    d["connections"].append({"source_node": "Show result", "target_node": "Rows"})
    assert codes(schema.interface_findings(d)) == [("WF019", "warning")]
    d["interface"]["result_node"] = "Ghost"
    assert codes(schema.interface_findings(d)) == [("WF019", "warning")]


@pytest.mark.parametrize("bind,fragment", [
    ("input.nope", "isn't a field"),
    ("node.Ghost.x", "no such node"),
    ("node.Refund request.amount", "isn't upstream"),         # the trigger is upstream; "Show result" is not
])
def test_wf018_bindings_that_do_not_match_the_workflow(refund, bind, fragment):
    d = copy.deepcopy(refund)
    if bind == "node.Refund request.amount":
        bind = "node.Show result.status"
    d["interface"]["screens"]["task:Manager review"]["components"].append({"type": "field", "bind": bind})
    found = schema.interface_findings(d)
    assert codes(found) == [("WF018", "warning")] and fragment in found[0]["message"]


def test_wf018_field_missing_from_a_known_output_shape(refund):
    d = copy.deepcopy(refund)
    d["interface"]["screens"]["task:Manager review"]["components"].append({"type": "field", "bind": "node.Classify.verdict"})
    shapes = {"Classify": {"outputs": {"main": {"type": "object", "properties": {
        "response": {"type": "string"}, "structured": {"type": "object", "properties": {"summary": {"type": "string"}}}}}}}}
    assert schema.interface_findings(d) == []                       # nothing known: no alarm
    found = schema.interface_findings(d, shapes)
    assert codes(found) == [("WF018", "warning")] and "'verdict'" in found[0]["message"]


def test_wf018_task_and_result_roots_only_on_their_own_screens(refund):
    d = copy.deepcopy(refund)
    d["interface"]["screens"]["start"]["components"] += [{"type": "field", "bind": "task.title"}, {"type": "field", "bind": "result.status"}]
    assert codes(schema.interface_findings(d)) == [("WF018", "warning")] * 2
    d["interface"]["screens"]["task:Ghost"] = spec()
    assert len(schema.interface_findings(d)) == 3


def test_no_interface_means_no_findings():
    assert schema.interface_findings({"nodes": []}) == [] and schema.interface_findings({"interface": {}}) == []


def test_node_names_with_dots_and_spaces_resolve_longest_first():
    names = ["Lookup", "Lookup history", "A.B"]
    assert schema.split_node_bind("node.Lookup history.orders", names) == ("Lookup history", "orders")
    assert schema.split_node_bind("node.A.B.x.y", names) == ("A.B", "x.y")
    assert schema.split_node_bind("node.Lookup", names) == ("Lookup", "")
    assert schema.split_node_bind("node.Other.x", names) is None
    assert schema.split_node_bind("input.x", names) is None


# ── chips ────────────────────────────────────────────────────────────────────────────────────


def test_wired_chips_of_the_refund_fixture(refund):
    wired = schema.compute_wired(refund, refund["interface"])
    assert wired == {"input": ["order_id", "amount", "reason", "email"], "tasks": ["Manager review"],
                     "context": ["Classify.structured.summary"], "result": "Show result"}


def test_wired_input_follows_the_bindings_when_there_is_no_input_form(refund):
    d = copy.deepcopy(refund)
    del d["interface"]["screens"]["start"]
    d["interface"]["screens"]["task:Manager review"]["components"] = [{"type": "field", "bind": "input.amount"}, {"type": "text", "template": "{{input.email}}"}]
    assert schema.compute_wired(d, d["interface"])["input"] == ["amount", "email"]   # a template reads its fields too
    d["interface"]["kind"] = "form"
    assert schema.compute_wired(d, d["interface"])["input"] == ["order_id", "amount", "reason", "email"]


def test_wired_of_nothing_is_empty():
    assert schema.compute_wired({}, None) == {"input": [], "tasks": [], "context": [], "result": None}


def test_task_screen_bindings_are_what_the_approval_node_must_capture(refund):
    assert schema.task_screen_bindings(refund["interface"], "Manager review") == ["Classify.structured.summary"]
    assert schema.task_screen_bindings(refund["interface"], "Other") == []
    assert schema.task_screen_bindings(None, "x") == []


# ── generation ───────────────────────────────────────────────────────────────────────────────


def test_generate_custom_builds_start_task_and_result_screens(refund):
    definition = {"nodes": refund["nodes"], "connections": refund["connections"]}
    iface = schema.generate_interface(definition, "Refund triage (UI)", "custom", "refunds")
    assert iface["trigger_node"] == "Refund request" and iface["result_node"] == "Show result"
    assert list(iface["screens"]) == ["start", "task:Manager review", "result"]
    task = iface["screens"]["task:Manager review"]["components"]
    assert [c["type"] for c in task][:2] == ["heading", "status"] and task[-2:] == [{"type": "form", "bind": "task.form"}, {"type": "actions", "bind": "task"}]
    assert {"type": "amount", "label": "Amount", "bind": "input.amount", "currency": "USD", "size": "xl"} in task
    assert {"type": "field", "label": "Suggest", "bind": "task.context.Suggest"} in task
    assert schema.parse_interface(iface)[1] == []
    assert schema.generate_interface(definition, "Refund triage (UI)", "custom", "refunds") == iface      # deterministic


def test_generate_form_and_chat_have_no_screens(refund):
    definition = {"nodes": refund["nodes"], "connections": refund["connections"]}
    form = schema.generate_interface(definition, "Refund triage", "form")
    assert form["kind"] == "form" and form["slug"] == "refund-triage" and "screens" not in form
    chat = schema.generate_interface(definition, "Refund triage", "chat")
    assert "trigger_node" not in chat                                  # no ChatInput: WF017 will say so


@pytest.mark.parametrize("name,slug", [("Refund triage (UI)", "refund-triage-ui"), ("  ", "workflow-ui"), ("A", "a-ui"), ("Ünïcode name", "n-code-name")])
def test_slugify_always_gives_a_valid_slug(name, slug):
    assert schema.SLUG_RE.match(schema.slugify(name))
    assert schema.slugify(name) == slug
