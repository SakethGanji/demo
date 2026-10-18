"""Stage 8 T1 (tester): convert selection under random graphs (DESIGN-STAGE8-REUSE 7.2, R9).

For several hundred random workflows (trees with merges, optional branch nodes, expressions that reach back to earlier nodes) and
random selections, ``extract`` must either refuse with a documented code or return a plan whose parent and child lose nothing:
every node lands in exactly one of the two, every edge is conserved or re-pointed through the call, and no definition keeps a
connection to a node that is gone. No database."""

from __future__ import annotations

import copy
import random
from collections import Counter

import pytest

from src.services.subworkflow_extract import ExtractPlan, Refusal, extract
from src.services.workflow_checker import StaticLookups, check_workflow
from tests.unit._stage8_b3_bank import C, N, setf, start

REFUSAL_CODES = {f"CS{i:02d}" for i in range(1, 11)}
SEEDS = range(400)


def random_workflow(rng: random.Random) -> dict:
    n = rng.randint(4, 9)
    nodes = [start({"type": "object", "properties": {"order_id": {"type": "string"}, "amount": {"type": "number"}}})]
    names = ["Start"]
    conns: list[dict] = []
    kinds: dict[str, str] = {"Start": "Start"}
    for i in range(1, n):
        name = f"N{i}"
        reach = rng.choice(names[1:]) if len(names) > 1 and rng.random() < 0.35 else None
        expr = '{{ $node["%s"].json.v%s }}' % (reach, names.index(reach)) if reach and kinds[reach] == "Set" else "{{ $json.amount }}"
        if rng.random() < 0.18:
            node = N(name, "If", {"condition": "{{ $json.amount > %d }}" % rng.randint(1, 9)})
            kinds[name] = "If"
        else:
            node = setf(name, **{f"v{i}": expr})
            kinds[name] = "Set"
        nodes.append({**node, "position": {"x": 120 * i, "y": 40 * (i % 3)}})
        parent = rng.choice(names)
        port = rng.choice(["true", "false"]) if kinds[parent] == "If" else "main"
        conns.append(C(parent, name, port))
        if rng.random() < 0.2 and len(names) > 2:                       # a second incoming edge: a merge
            other = rng.choice([x for x in names if x != parent])
            oport = rng.choice(["true", "false"]) if kinds[other] == "If" else "main"
            conns.append(C(other, name, oport))
        names.append(name)
    return {"nodes": nodes, "connections": conns}


def edges(d: dict) -> Counter:
    return Counter((c["source_node"], c.get("source_output", "main"), c["target_node"]) for c in d["connections"])


def names_of(d: dict) -> list[str]:
    return [n["name"] for n in d["nodes"]]


@pytest.mark.parametrize("seed", SEEDS)
def test_a_plan_loses_no_node_and_no_edge_and_a_refusal_has_a_documented_code(seed):
    rng = random.Random(seed)
    d = random_workflow(rng)
    before = copy.deepcopy(d)
    pool = [x for x in names_of(d) if x != "Start"]
    sel = rng.sample(pool, rng.randint(1, min(len(pool), 5)))
    r = extract(d, sel, "Moved")
    assert d == before, "extract must not mutate its input"
    if isinstance(r, Refusal):
        assert r.code in REFUSAL_CODES, r
        assert r.message and set(r.nodes) <= set(names_of(d)), r
        return
    assert isinstance(r, ExtractPlan)
    S, parent, child = set(sel), r.parent_definition, r.child_definition
    call = r.call_node

    # every original node is in exactly one place; the parent gained exactly the call, the child exactly its trigger (and maybe a Return)
    pnames, cnames = names_of(parent), names_of(child)
    assert len(set(pnames)) == len(pnames) and len(set(cnames)) == len(cnames), "duplicate node names"
    assert set(pnames) == (set(names_of(d)) - S) | {call}
    extra_child = set(cnames) - S
    assert r.trigger_node in extra_child and len(extra_child) in (1, 2)           # the trigger, and at most a Return node
    assert S <= set(cnames)
    assert sum(1 for n in child["nodes"] if n["type"] == "ExecuteWorkflowTrigger") == 1
    assert next(n for n in parent["nodes"] if n["name"] == call)["type"] == "ExecuteWorkflow"

    # no dangling connection on either side
    for dfn in (parent, child):
        have = set(names_of(dfn))
        for c in dfn["connections"]:
            assert c["source_node"] in have and c["target_node"] in have, (seed, c)

    # edges among the untouched nodes are unchanged
    outside = lambda e: e[0] not in S and e[2] not in S  # noqa: E731
    assert Counter({e: n for e, n in edges(d).items() if outside(e)}) == Counter(
        {e: n for e, n in edges(parent).items() if e[0] != call and e[2] != call})
    # edges inside the selection are exactly the child's, apart from the trigger's and the Return's
    inner = Counter({e: n for e, n in edges(d).items() if e[0] in S and e[2] in S})
    child_inner = Counter({e: n for e, n in edges(child).items() if e[0] in S and e[2] in S})
    assert inner == child_inner, (seed, inner, child_inner)
    # what fed the selection now feeds the call; what the selection fed is now fed by the call
    into = {(e[0], e[1]) for e in edges(d) if e[0] not in S and e[2] in S}
    assert into == {(e[0], e[1]) for e in edges(parent) if e[2] == call}
    out_targets = {e[2] for e in edges(d) if e[0] in S and e[2] not in S}
    assert out_targets == {e[2] for e in edges(parent) if e[0] == call}
    # the trigger leads into the entry node
    assert any(c["source_node"] == r.trigger_node and c["target_node"] == r.entry for c in child["connections"])
    # nothing in the child still points at a node the parent kept (the reach-backs became inputs)
    for n in child["nodes"]:
        text = str(n.get("parameters"))
        for outside_name in set(names_of(d)) - S:
            assert f'$node["{outside_name}"]' not in text and f"$node['{outside_name}']" not in text, (seed, n["name"], outside_name)


@pytest.mark.parametrize("seed", SEEDS)
def test_a_plan_passes_the_checker_on_both_sides_when_the_original_did(seed):
    rng = random.Random(10_000 + seed)
    d = random_workflow(rng)
    if [f for f in check_workflow(d, lookups=StaticLookups())["findings"] if f["severity"] == "error"]:
        pytest.skip("the generated workflow already has a checker error")
    pool = [x for x in names_of(d) if x != "Start"]
    r = extract(d, rng.sample(pool, rng.randint(1, min(len(pool), 5))), "Moved")
    if isinstance(r, Refusal):
        return
    from src.services.subworkflow_extract import bind_child_id
    from src.services.workflow_checker import ChildWorkflow

    kid = ChildWorkflow("wf_kid", "Moved", "proj_bank", draft=r.child_definition, versions={1: r.child_definition}, published_version=1)
    lookups = StaticLookups(children={"wf_kid": kid}, current_project_id="proj_bank", current_workflow_id="wf_main")
    for label, dfn, lk in (("child", r.child_definition, StaticLookups()), ("parent", bind_child_id(r, "wf_kid"), lookups)):
        errs = [(f["code"], f.get("node"), f["message"]) for f in check_workflow(dfn, lookups=lk)["findings"] if f["severity"] == "error"]
        assert errs == [], (seed, label, errs)


def test_the_generator_exercises_both_plans_and_refusals():
    plans = refusals = 0
    codes = Counter()
    for seed in SEEDS:
        rng = random.Random(seed)
        d = random_workflow(rng)
        pool = [x for x in names_of(d) if x != "Start"]
        r = extract(d, rng.sample(pool, rng.randint(1, min(len(pool), 5))), "Moved")
        if isinstance(r, Refusal):
            refusals += 1
            codes[r.code] += 1
        else:
            plans += 1
    assert plans >= 40 and refusals >= 40, (plans, refusals, codes)
    assert {"CS03", "CS04", "CS05"} <= set(codes), codes
