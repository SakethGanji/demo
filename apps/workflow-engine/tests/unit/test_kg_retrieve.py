"""Capability graph: retrieval over a loaded slice (``services/kg_retrieve``). POC rules: no scoring tiers, no fit bands; a plain order."""

from __future__ import annotations

from src.services import kg_retrieve as R
from tests.unit._kg_support import PROJECT, make_slice, node, pattern, subworkflow


def hits(*keys, confirmed=True):
    return [{"key": k, "label": k, "score": 0.9, "source": "lexical", "evidence": "", "confirmed": confirmed} for k in keys]


def test_nothing_known_is_an_empty_retrieval():
    assert R.retrieve(make_slice(), [], "x") == R.empty_retrieval()
    assert R.retrieve(make_slice(), hits("concept:does_not_exist"), "x") == R.empty_retrieval()
    assert R.similarity("refund the customer", "customer refund") > 0.5 and R.similarity("abc", "xyz") == 0.0 and R.similarity("", "x") == 0.0


def test_node_types_come_from_the_signatures_and_node_capabilities():
    r = R.retrieve(make_slice(), hits("concept:approval", "concept:schedule"), "x")
    assert "Approval" in r["node_types"] and "Cron" in r["node_types"] and r["node_types"] == sorted(r["node_types"])


def test_order_is_most_concepts_covered_then_validated_then_label():
    a = subworkflow("wf_a", "Zeta assign")                 # implements 2 concepts
    b = subworkflow("wf_b", "Alpha assign")                # implements 1
    c = subworkflow("wf_c", "Beta assign")                 # implements 1
    sl = make_slice(extra=[a, b, c], extra_edges=[(a, "implements", "concept:assignment"), (a, "implements", "concept:report"),
                                                  (b, "implements", "concept:assignment"), (c, "implements", "concept:assignment")])
    r = R.retrieve(sl, hits("concept:assignment", "concept:report"), "x")
    assert [s["label"] for s in r["subworkflows"]] == ["Zeta assign", "Alpha assign", "Beta assign"]
    first = r["subworkflows"][0]
    assert first["key"] == "wf:wf_a" and first["why"] == "implements Assignment, Report" and first["concepts"] == ["concept:assignment", "concept:report"]
    assert first["score"] == 1.0 and r["subworkflows"][1]["score"] == 0.5


def test_only_callable_published_subworkflows_of_this_project_are_offered():
    ok = subworkflow("wf_ok", "Assign owner")
    draft = subworkflow("wf_draft", "Draft", published=None)
    private = subworkflow("wf_private", "Not callable", callable_=False)
    foreign = subworkflow("wf_foreign", "Other project", project_id="proj_other")   # WF027: never across projects
    sl = make_slice(extra=[ok, draft, private, foreign], extra_edges=[(n, "implements", "concept:assignment") for n in (ok, draft, private, foreign)])
    assert [s["key"] for s in R.retrieve(sl, hits("concept:assignment"), "x")["subworkflows"]] == ["wf:wf_ok"]


def test_installed_templates_are_not_offered_again_and_apis_must_be_in_the_project():
    t1 = node("tpl:customer-notify", "capability", "Customer notify", subkind="template")
    t2 = node("tpl:refund-triage", "capability", "Refund triage", subkind="template")
    api = node("api:notify.send", "capability", "send", subkind="api_operation", scope="project", project_id=PROJECT)
    foreign_api = node("api:other.send", "capability", "send", subkind="api_operation", scope="project", project_id="proj_other")
    sl = make_slice(extra=[t1, t2, api, foreign_api], extra_edges=[(n, "implements", "concept:notification") for n in (t1, t2, api, foreign_api)])
    r = R.retrieve(sl, hits("concept:notification"), "x", installed_templates={"customer-notify"})
    assert [t["key"] for t in r["templates"]] == ["tpl:refund-triage"]
    assert r["apis"] == [{"key": "api:notify.send", "why": "implements Notification"}]
    assert len(R.retrieve(sl, hits("concept:notification"), "x")["templates"]) == 2


def three_concept_pattern(key, label, *, shape="workflow", validated=1, extra=()):
    return pattern(key, label, ["concept:approval", "concept:escalation", *extra], shape=shape, validated=validated)


def test_a_pattern_matches_when_it_covers_two_of_the_confirmed_concepts():
    p_two = three_concept_pattern("pattern:two", "Two shared")
    p_one = pattern("pattern:one", "One shared", ["concept:approval"])
    sl = make_slice(extra=[p_two, p_one], extra_edges=[(p_two, "implements", "concept:approval"), (p_two, "implements", "concept:escalation"),
                                                       (p_one, "implements", "concept:approval")])
    want = hits("concept:approval", "concept:escalation", "concept:refund")
    r = R.retrieve(sl, want, "x")
    assert [p["key"] for p in r["patterns"]] == ["pattern:two"]
    assert r["patterns"][0]["adapt"] is True and r["patterns"][0]["source_project"] == "Payments Ops" and r["patterns"][0]["validated_count"] == 1
    # a request with a single confirmed concept is matched by one shared concept; more shared concepts come first
    assert [p["key"] for p in R.retrieve(sl, hits("concept:approval"), "x")["patterns"]] == ["pattern:one", "pattern:two"]   # a tie: by label


def test_implied_concepts_do_not_make_a_pattern_match():
    p = three_concept_pattern("pattern:p", "P")
    sl = make_slice(extra=[p], extra_edges=[(p, "implements", "concept:approval"), (p, "implements", "concept:escalation")])
    mixed = hits("concept:approval") + hits("concept:escalation", confirmed=False) + hits("concept:refund")
    assert R.retrieve(sl, mixed, "x")["patterns"] == []


def test_patterns_are_ordered_by_shared_concepts_then_validated_and_only_the_right_shape_is_the_seed():
    best = three_concept_pattern("pattern:best", "Best", shape="app", validated=1, extra=["concept:report"])
    older = three_concept_pattern("pattern:older", "Older", shape="workflow", validated=5)
    newer = three_concept_pattern("pattern:newer", "Newer", shape="workflow", validated=2)
    edges = [(p, "implements", k) for p in (older, newer) for k in ("concept:approval", "concept:escalation")] + \
            [(best, "implements", k) for k in ("concept:approval", "concept:escalation", "concept:report")]
    sl = make_slice(extra=[best, older, newer], extra_edges=edges)
    want = hits("concept:approval", "concept:escalation", "concept:report")
    r = R.retrieve(sl, want, "x", shape="workflow")
    assert [p["key"] for p in r["patterns"]] == ["pattern:best", "pattern:older", "pattern:newer"]
    assert [p["adapt"] for p in r["patterns"]] == [False, True, False]      # the first pattern OF THE REQUEST'S SHAPE
    assert [p["adapt"] for p in R.retrieve(sl, want, "x", shape="app")["patterns"]] == [True, False, False]
    assert all(set(p) == {"key", "label", "fit", "validated_count", "source_project", "adapt"} for p in r["patterns"])


def test_roles_the_concepts_need_come_with_the_apps_that_use_them():
    p = three_concept_pattern("pattern:p", "P")
    sl = make_slice(extra=[p], extra_edges=[(p, "requires_permission", "role:ops_head")])
    assert R.retrieve(sl, hits("concept:approval"), "x")["roles"] == []        # the seed concepts name no role of their own
    # a role that a concept requires is listed with the patterns / apps that use it
    from tests.unit._kg_support import edge
    nodes = dict(sl.nodes)
    approval, head = sl.by_key("concept:approval"), sl.by_key("role:ops_head")
    from src.repositories.kg_repository import GraphSlice
    sl2 = GraphSlice(PROJECT, "t1", nodes, sl.edges + [edge(approval, "requires_permission", head)])
    r = R.retrieve(sl2, hits("concept:approval"), "x")
    assert r["roles"] == [{"key": "role:ops_head", "label": head["label"], "used_for": ["concept:approval"], "apps": 1}]
