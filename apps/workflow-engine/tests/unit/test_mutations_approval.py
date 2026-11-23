"""Planted bug for Approval: swap_branches trades the approved and rejected targets (Stage 5, DESIGN-STAGE5-UIS 5.7)."""

from __future__ import annotations

from src.services.mutations import generate_mutants


def N(name, type_, params=None):
    return {"name": name, "type": type_, "parameters": params or {}}


def E(src, dst, port="main"):
    return {"source_node": src, "target_node": dst, "source_output": port, "target_input": "main"}


def D():
    return {
        "nodes": [N("Start", "Start"), N("Review", "Approval"), N("Pay", "Set"), N("Refuse", "Set"), N("Late", "Set"), N("Gate", "If", {"condition": "{{ $json.a > 1 }}"})],
        "connections": [E("Start", "Review"), E("Review", "Pay", "approved"), E("Review", "Refuse", "rejected"), E("Review", "Late", "timed_out"),
                        E("Start", "Gate"), E("Gate", "Pay", "true"), E("Gate", "Refuse", "false")],
        "tests": [], "test_defaults": {},
    }


def swaps(defn):
    return [m for m in generate_mutants(defn) if m["operator"] == "swap_branches"]


def test_an_approval_has_a_swap_branches_mutant_that_trades_approved_and_rejected():
    ms = {m["node"]: m for m in swaps(D())}
    assert set(ms) == {"Review", "Gate"}
    m = ms["Review"]
    assert m["description"] == "Swap the approved and rejected branches of Review"
    conns = {(c["source_node"], c["source_output"]): c["target_node"] for c in m["definition"]["connections"]}
    assert conns[("Review", "approved")] == "Refuse" and conns[("Review", "rejected")] == "Pay"
    assert conns[("Review", "timed_out")] == "Late"  # the timed_out target is untouched
    assert conns[("Gate", "true")] == "Pay"  # and so is the If


def test_the_original_definition_is_not_modified():
    d = D()
    swaps(d)
    assert {(c["source_output"], c["target_node"]) for c in d["connections"] if c["source_node"] == "Review"} == {
        ("approved", "Pay"), ("rejected", "Refuse"), ("timed_out", "Late")}


def test_an_approval_with_no_decision_connections_has_no_swap():
    d = D()
    d["connections"] = [c for c in d["connections"] if not (c["source_node"] == "Review" and c["source_output"] in ("approved", "rejected"))]
    assert {m["node"] for m in swaps(d)} == {"Gate"}


def test_a_disabled_approval_has_no_swap():
    d = D()
    d["nodes"][1]["disabled"] = True
    assert {m["node"] for m in swaps(d)} == {"Gate"}


def test_the_swap_is_deterministic_and_the_if_swap_still_works():
    a = [m["id"] for m in generate_mutants(D(), seed=3)]
    b = [m["id"] for m in generate_mutants(D(), seed=3)]
    assert a == b
    gate = next(m for m in swaps(D()) if m["node"] == "Gate")
    conns = {(c["source_node"], c["source_output"]): c["target_node"] for c in gate["definition"]["connections"]}
    assert conns[("Gate", "true")] == "Refuse" and conns[("Gate", "false")] == "Pay"
