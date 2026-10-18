"""Capability graph Wave 0: the seed ontology file loads, validates and maps to graph rows (PLAN-CAPABILITY-GRAPH section 2.1)."""

from __future__ import annotations

import copy

import pytest
import yaml

from src.services import kg_ontology as ko
from src.services.node_catalog import build_node_catalog

CONCEPT_KEYS = {
    "intake", "approval", "four_eyes_approval", "escalation", "assignment", "review", "retry", "reconciliation", "notification",
    "report", "schedule", "document_review", "screening", "refund", "dispute", "payment_handling",
    "exception_case", "customer_request", "sla", "threshold_check", "audit_log", "report_output",
}
ROLE_KEYS = {"ops_analyst", "ops_manager", "ops_head", "compliance_officer", "fraud_analyst", "fraud_lead", "finance_controller",
             "customer_service"}


@pytest.fixture(scope="module")
def raw() -> dict:
    return yaml.safe_load(ko.DEFAULT_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def doc(raw) -> dict:
    return copy.deepcopy(raw)


def codes(d: dict, **kw) -> set[str]:
    return {i.code for i in ko.validate(d, **kw)}


def concept(d: dict, slug: str) -> dict:
    return next(c for c in d["concepts"] if c["key"] == f"concept:{slug}")


def test_the_file_loads_with_22_concepts_and_8_roles():
    ont = ko.load()
    assert {c["key"] for c in ont.concepts} == {f"concept:{k}" for k in CONCEPT_KEYS}
    assert {r["key"] for r in ont.roles} == {f"role:{k}" for k in ROLE_KEYS}
    assert (len(ont.concepts), len(ont.roles)) == (22, 8)
    assert ont.domain == "bank_ops" and ont.version == 1


def test_the_four_concept_kinds_match_the_plan():
    kinds: dict[str, set[str]] = {}
    for c in ko.load().concepts:
        kinds.setdefault(c["kind"], set()).add(ko.short_name(c["key"]))
    assert kinds["entity"] == {"exception_case", "customer_request"}
    assert kinds["control"] == {"sla", "threshold_check", "audit_log"}
    assert kinds["artifact"] == {"report_output"}
    assert len(kinds["process"]) == 16


def test_the_shipped_file_has_no_issues_even_against_the_real_node_catalog():
    node_types = [n["type"] for n in build_node_catalog()]
    assert ko.validate(yaml.safe_load(ko.DEFAULT_PATH.read_text()), node_types=node_types) == []


def test_every_slot_type_is_known_and_every_choice_has_options():
    for c in ko.load().concepts:
        for s in c["slots"]:
            assert s["type"] in ko.SLOT_TYPES, (c["key"], s)
            if s["type"] == "choice":
                assert s["options"], (c["key"], s)


def test_every_relation_target_exists():
    ont = ko.load()
    keys = {c["key"] for c in ont.concepts}
    for c in ont.concepts:
        for rel in ko.CONCEPT_RELATIONS:
            for t in c.get(rel) or []:
                assert t in keys, (c["key"], rel, t)
    assert ko.edge_rows(ont)


def test_aliases_are_lowercase_and_unique_across_concepts_unless_declared_shared():
    ont = ko.load()
    seen: dict[str, str] = {}
    for c in ont.concepts:
        for a in c["aliases"]:
            assert a == a.strip().lower() and a, (c["key"], a)
            if a in seen:
                assert a in (c.get("shared_aliases") or []), f"{a!r} used by {seen[a]} and {c['key']}"
            seen[a] = c["key"]
    roles: set[str] = set()
    for r in ont.roles:
        for a in r["aliases"]:
            assert a == a.lower() and a not in roles, a
            roles.add(a)


def test_the_plan_aliases_are_present():
    exc = ko.load().concept("concept:exception_case")["aliases"]
    for w in ("exception", "case", "incident", "alert", "issue", "break"):
        assert w in exc
    assert "deadline" in ko.load().concept("concept:sla")["aliases"]
    assert "head of operations" in ko.load().role("role:ops_head")["aliases"]


def test_required_slots_match_the_plan_table():
    ont = ko.load()
    required = {ko.short_name(c["key"]): {s["name"]: s["type"] for s in c["slots"] if s["required"]} for c in ont.concepts}
    assert required["approval"] == {"approver_role": "role"}
    assert required["escalation"] == {"target_role": "role", "after": "duration"}
    assert required["assignment"] == {"by_field": "field", "owners": "role"}
    assert required["sla"] == {"due_in": "duration"}
    assert required["exception_case"] == {"entity": "text", "fields": "field"}
    assert required["intake"] == {"channel": "choice"}
    assert required["report"] == {"recipients_role": "role", "schedule": "cron"}
    assert required["schedule"] == {"cron": "cron"}
    assert required["notification"] == {"channel": "api_capability"}


def test_alias_slots_point_at_the_sla_slot_and_are_asked_once():
    ont = ko.load()
    esc = {s["name"]: s for s in ont.concept("concept:escalation")["slots"]}
    appr = {s["name"]: s for s in ont.concept("concept:approval")["slots"]}
    assert esc["after"]["alias_of"] == "sla.due_in" and appr["due_in"]["alias_of"] == "sla.due_in"


def test_part_recipes_follow_the_plan():
    ont = ko.load()
    assert ont.concept("concept:assignment")["part_recipe"]["parts"][0]["key"] == "assign_owner"
    assert ont.concept("concept:exception_case")["part_recipe"]["tables"][0]["key"] == "cases"
    assert [p["key"] for p in ont.concept("concept:report")["part_recipe"]["parts"]] == ["build_report", "weekly_report", "report_view"]
    assert ont.concept("concept:notification")["part_recipe"] == {"reuse_template": "customer-notify"}
    assert ont.concept("concept:approval").get("part_recipe") is None


def test_the_concept_props_have_the_frozen_shape():
    for row in ko.node_rows(ko.load()):
        if row["kind"] != "concept":
            continue
        assert set(row["props"]) >= {"definition", "kind", "slots", "signatures", "test_obligations", "prompt_hint", "part_recipe"}
        assert row["scope"] == "global" and row["source"] == "seed" and row["subkind"] == row["props"]["kind"]
    roles = [r for r in ko.node_rows(ko.load()) if r["kind"] == "role"]
    assert len(roles) == 8 and all(r["key"].startswith("role:") for r in roles)


def test_the_approval_concept_is_exactly_the_plan_example():
    props = ko.concept_props(ko.load().concept("concept:approval"))
    assert props["definition"] == "A person with authority decides approved or rejected before the process continues."
    assert props["slots"][0]["used_as"] == "Approval.assignees" and props["signatures"] == [{"node_type": "Approval"}]


def test_four_eyes_is_a_approval():
    edges = ko.edge_rows(ko.load())
    assert {"src": "concept:four_eyes_approval", "rel": "is_a", "dst": "concept:approval"} in edges
    assert {"src": "concept:escalation", "rel": "requires", "dst": "concept:sla"} in edges


# ── the validator rejects each kind of mistake with its own code ──────────────────────────────────────────────────────


def test_a_missing_root_key_is_kgo001():
    assert codes({"version": 1}) == {"KGO001"}
    assert codes([]) == {"KGO001"}


def test_a_duplicate_key_is_kgo002(doc):
    doc["concepts"].append(copy.deepcopy(doc["concepts"][0]))
    assert "KGO002" in codes(doc)


def test_a_bad_kind_or_missing_field_is_kgo003(doc):
    concept(doc, "sla")["kind"] = "thing"
    del concept(doc, "retry")["prompt_hint"]
    assert codes(doc) == {"KGO003"}


def test_an_unknown_slot_type_is_kgo004(doc):
    concept(doc, "sla")["slots"][0]["type"] = "colour"
    assert "KGO004" in codes(doc)


def test_a_missing_relation_target_is_kgo005(doc):
    concept(doc, "escalation")["requires"].append("concept:nope")
    concept(doc, "report")["renders_with"] = ["table"]
    assert codes(doc) == {"KGO005"}


def test_aliases_must_be_lowercase_and_unique_unless_shared_kgo006(doc):
    concept(doc, "sla")["aliases"].append("Deadline Day")
    assert codes(doc) == {"KGO006"}
    doc = copy.deepcopy(doc)
    concept(doc, "sla")["aliases"].remove("Deadline Day")
    concept(doc, "approval")["aliases"].append("deadline")           # now used by sla and approval
    assert codes(doc) == {"KGO006"}
    concept(doc, "approval")["shared_aliases"] = ["deadline"]
    assert codes(doc) == {"KGO006"}                                    # one side declaring it is not enough
    concept(doc, "sla")["shared_aliases"] = ["deadline"]
    assert codes(doc) == set()                                         # both sides declare it: allowed


@pytest.mark.parametrize("mutate", [
    lambda c: c["slots"][0].pop("question"),                                    # required slot without a question
    lambda c: c["slots"][0].update(name="Bad Name"),
    lambda c: c["slots"][0].update(required="yes"),
])
def test_a_malformed_slot_is_kgo007(doc, mutate):
    mutate(concept(doc, "sla"))
    assert "KGO007" in codes(doc)


def test_alias_of_must_name_an_existing_unaliased_slot_of_the_same_type_kgo007(doc):
    concept(doc, "escalation")["slots"][1]["alias_of"] = "sla.nothing"
    assert codes(doc) == {"KGO007"}
    concept(doc, "escalation")["slots"][1]["alias_of"] = "approval.due_in"       # an alias itself
    assert codes(doc) == {"KGO007"}
    concept(doc, "escalation")["slots"][1]["alias_of"] = "approval.approver_role"  # wrong type
    assert codes(doc) == {"KGO007"}


def test_a_choice_slot_needs_options_and_a_default_among_them(doc):
    concept(doc, "intake")["slots"][0]["default"] = "carrier pigeon"
    assert codes(doc) == {"KGO007"}
    del concept(doc, "intake")["slots"][0]["options"]
    assert codes(doc) == {"KGO007"}


def test_a_malformed_signature_or_unknown_node_type_is_kgo008(doc):
    concept(doc, "approval")["signatures"] = [{"params": ["x"]}]
    assert codes(doc) == {"KGO008"}
    concept(doc, "approval")["signatures"] = [{"node_type": "Approval", "colour": "red"}]
    assert codes(doc) == {"KGO008"}
    concept(doc, "approval")["signatures"] = [{"node_type": "NoSuchNode"}]
    assert codes(doc) == set()                                                   # unknown types only fail with a catalog
    assert codes(doc, node_types=["Approval"]) == {"KGO008"}


def test_a_malformed_part_recipe_is_kgo009(doc):
    concept(doc, "assignment")["part_recipe"] = {"parts": [{"key": "x", "role": "wizard"}]}
    assert codes(doc) == {"KGO009"}
    concept(doc, "assignment")["part_recipe"] = {"bogus": 1}
    assert codes(doc) == {"KGO009"}


def test_a_malformed_role_is_kgo010(doc):
    doc["roles"][0]["key"] = "ops_analyst"
    del doc["roles"][1]["aliases"]
    assert codes(doc) == {"KGO010"}


def test_an_is_a_cycle_is_kgo011(doc):
    concept(doc, "approval")["is_a"] = ["concept:four_eyes_approval"]
    assert "KGO011" in codes(doc)


def test_load_raises_with_every_issue_listed(tmp_path, doc):
    concept(doc, "sla")["kind"] = "thing"
    concept(doc, "retry")["slots"][0]["type"] = "colour"
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(doc))
    with pytest.raises(ko.OntologyInvalid) as e:
        ko.load(p)
    assert {i.code for i in e.value.issues} == {"KGO003", "KGO004"}
    with pytest.raises(ko.OntologyInvalid):
        ko.load(tmp_path / "missing.yaml")
