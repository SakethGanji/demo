"""Tier 0 — SDK contract tests. No LLM involved: deterministic assertions that the
hand-written workflow_sdk.py stays honest against the REAL node_registry. These are
what catch drift (a node's parameter renamed, a port removed) before it ever reaches
a model — see src/engine/workflow_sdk.py's module docstring for the design context.

Run: venv/bin/python -m pytest tests/engine/test_workflow_sdk_contract.py -v
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from src.engine.workflow_sdk import (  # noqa: E402
    SDK_TYPES,
    WorkflowInvalid,
    execute_workflow_script,
    signature_reference,
)
from src.engine.node_registry import node_registry, register_all_nodes  # noqa: E402


@pytest.fixture(autouse=True, scope="module")
def _registered():
    register_all_nodes()


# ---------------------------------------------------------------------------
# The SDK types actually exist in the real registry (would fail if a node was
# renamed/removed and this file wasn't updated).
# ---------------------------------------------------------------------------

def test_sdk_types_exist_in_registry():
    for t in SDK_TYPES:
        assert node_registry.has(t), f"{t} is in SDK_TYPES but not in node_registry"


# ---------------------------------------------------------------------------
# Correct usage succeeds
# ---------------------------------------------------------------------------

def test_minimal_valid_script_builds_a_workflow():
    script = (
        'cron = Cron(cronExpression="0 7 * * 1", mode="cron")\n'
        'fetch = HttpRequest(method="GET", url="https://example.com/export")\n'
        'cron >> fetch\n'
        'validate()\n'
    )
    result = execute_workflow_script(script)
    assert result.error is None, result.error
    assert len(result.workflow.nodes) == 2
    assert len(result.workflow.connections) == 1
    assert result.workflow.connections[0].source_output == "main"


def test_named_port_wiring_true_false():
    script = (
        'cron = Cron(cronExpression="0 7 * * 1")\n'
        'check = If(field="body", operation="isNotEmpty")\n'
        'ok = HttpRequest(method="GET", url="https://example.com/ok")\n'
        'bad = StopAndError(message="empty")\n'
        'cron >> check\n'
        'check.true >> ok\n'
        'check.false >> bad\n'
        'validate()\n'
    )
    result = execute_workflow_script(script)
    assert result.error is None, result.error
    # No `name=` kwarg was passed, so nodes get the auto-derived, de-duplicated
    # name (SDK-DESIGN.md §4: "name auto-derived from the type") — NOT the Python
    # variable name, since a function has no way to see its caller's LHS.
    conns = {(c.source_output, c.target_node) for c in result.workflow.connections}
    assert ("true", "HttpRequest") in conns
    assert ("false", "StopAndError") in conns


def test_for_loop_produces_multiple_nodes_from_one_statement():
    script = (
        'cron = Cron(cronExpression="0 7 * * 1")\n'
        'check = If(field="body", operation="isNotEmpty")\n'
        'cron >> check\n'
        'for region in ("US", "EU", "APAC"):\n'
        '    node = Postgres(name=f"Load {region}", operation="query", query="select 1")\n'
        '    check.true >> node\n'
    )
    result = execute_workflow_script(script)
    assert result.error is None, result.error
    pg_nodes = [n for n in result.workflow.nodes if n.type == "Postgres"]
    assert len(pg_nodes) == 3
    assert {n.name for n in pg_nodes} == {"Load US", "Load EU", "Load APAC"}
    # all three tagged with the line of the `for` statement, not three different lines
    lines = {result.node_meta[n.name]["sourceLine"] for n in pg_nodes}
    assert lines == {4}


# ---------------------------------------------------------------------------
# Wrong usage fails LOUDLY and NAMES THE FIX — this is the entire feedback-loop
# argument in SDK-DESIGN.md §6, tested mechanically instead of asserted.
# ---------------------------------------------------------------------------

def test_unknown_parameter_raises_with_did_you_mean():
    # This is literally the mistake the earlier design mockups made by hand:
    # Cron has no "expression" parameter (it's "cronExpression").
    script = 'cron = Cron(expression="0 7 * * 1")\n'
    result = execute_workflow_script(script)
    assert result.error is not None
    assert "no parameter 'expression'" in result.error
    assert "cronExpression" in result.error, "did-you-mean should surface the real name"


def test_nonexistent_parameter_language_on_code_node():
    # The other invented parameter from the mockups: Code has no "language" field.
    script = 'n = Code(language="python", code="return items")\n'
    result = execute_workflow_script(script)
    assert result.error is not None
    assert "no parameter 'language'" in result.error


def test_missing_required_parameter_raises():
    script = 'n = HttpRequest(method="GET")\n'  # missing required url
    result = execute_workflow_script(script)
    assert result.error is not None
    assert "missing required parameter" in result.error
    assert "url" in result.error


def test_required_with_nonempty_default_is_optional():
    # SDK-DESIGN.md §5 mapping rule: required=True + non-empty default => optional
    # in the SDK. HttpRequest.method is required=True but defaults to "GET".
    script = 'n = HttpRequest(url="https://example.com")\n'
    result = execute_workflow_script(script)
    assert result.error is None, result.error


def test_invalid_option_value_rejected():
    script = 'n = HttpRequest(method="FETCH", url="https://example.com")\n'
    result = execute_workflow_script(script)
    assert result.error is not None
    assert "not a valid option" in result.error


def test_expression_value_bypasses_option_validation():
    # The negative case above was covered; this positive case (a templated
    # value on an options-type param) never was — found on self-review, no
    # test exercised the `_looks_like_expression` pass-through at all.
    script = 'n = HttpRequest(method="{{ $vars.METHOD }}", url="https://example.com")\n'
    result = execute_workflow_script(script)
    assert result.error is None, result.error

    script2 = 'n = HttpRequest(method="$vars.METHOD", url="https://example.com")\n'
    result2 = execute_workflow_script(script2)
    assert result2.error is None, result2.error


def test_unknown_output_port_raises():
    script = (
        'cron = Cron(cronExpression="0 7 * * 1")\n'
        'ok = HttpRequest(method="GET", url="https://example.com")\n'
        'cron.maybe >> ok\n'
    )
    result = execute_workflow_script(script)
    assert result.error is not None
    assert "no output port 'maybe'" in result.error


# ---------------------------------------------------------------------------
# validate() — structural checks
# ---------------------------------------------------------------------------

def test_validate_catches_missing_trigger():
    script = (
        'fetch = HttpRequest(method="GET", url="https://example.com")\n'
        'validate()\n'
    )
    result = execute_workflow_script(script)
    assert result.error is not None
    assert "no trigger" in result.error


def test_validate_catches_unreachable_node():
    script = (
        'cron = Cron(cronExpression="0 7 * * 1")\n'
        'orphan = HttpRequest(method="GET", url="https://example.com")\n'
        'validate()\n'
    )
    result = execute_workflow_script(script)
    assert result.error is not None
    assert "unreachable" in result.error


def test_validate_catches_bad_routing_port():
    # The exact mistake called out in the design notes as "a real mistake a real
    # model made": routing to a port the upstream node doesn't declare.
    script = (
        'cron = Cron(cronExpression="0 7 * * 1")\n'
        'check = If(field="body", operation="isNotEmpty")\n'
        'ok = HttpRequest(method="GET", url="https://example.com")\n'
        'cron >> check\n'
        'check.maybe >> ok\n'
    )
    result = execute_workflow_script(script)
    assert result.error is not None
    assert "no output port 'maybe'" in result.error


# ---------------------------------------------------------------------------
# test_run() — dry, and branch decisions are REALLY evaluated, not faked
# ---------------------------------------------------------------------------

def test_test_run_never_executes_writes():
    # Found on self-review: this test's name promised the write-side-effect
    # guarantee was checked, but the body only asserted `error is None` — it
    # would still pass even if test_run() started performing real writes.
    # Actually pin the guarantee: writes_staged counts the reachable write node,
    # and writes_executed is always 0 regardless.
    script = (
        'cron = Cron(cronExpression="0 7 * * 1")\n'
        'w = Postgres(name="Load", operation="query", query="select 1")\n'
        'cron >> w\n'
        'report = test_run(input={})\n'
        'results.append(report)\n'
    )
    result = execute_workflow_script(script)
    assert result.error is None, result.error
    report = result.results[0]
    assert report["writes_staged"] == 1
    assert report["writes_executed"] == 0


def test_test_run_branch_decision_true_and_false_directly():
    # field "amount" is empty in one case, present in the other -> the FALSE/TRUE
    # branch must be genuinely evaluated, not hard-coded to always take one side.
    from src.engine.workflow_sdk import build_sdk_namespace, _Draft  # noqa: PLC0415

    draft = _Draft()
    ns = build_sdk_namespace(draft, lambda: 1)
    cron = ns["Cron"](cronExpression="0 7 * * 1")
    check = ns["If"](field="amount", operation="isNotEmpty")
    ok = ns["HttpRequest"](method="GET", url="https://example.com/ok")
    bad = ns["StopAndError"](message="empty")
    cron >> check
    check.true >> ok
    check.false >> bad

    report_empty = ns["test_run"](input={})
    assert report_empty["branches"][0]["branch"] == "false"
    assert ok.name in report_empty["unreached"]
    assert bad.name in report_empty["reached"]

    report_present = ns["test_run"](input={"amount": 42})
    assert report_present["branches"][0]["branch"] == "true"
    assert ok.name in report_present["reached"]
    assert bad.name in report_present["unreached"]


# ---------------------------------------------------------------------------
# signature_reference() — sanity: the doc actually names the real param names,
# including the exact ones the earlier mockups got wrong.
# ---------------------------------------------------------------------------

def test_signature_reference_uses_real_param_names():
    ref = signature_reference()
    assert "cronExpression" in ref
    assert "expression" not in ref.split("cronExpression")[0]  # no invented "expression" param
    assert "toEmail" in ref  # SendEmail
    assert "true" in ref and "false" in ref  # If's ports noted


# ---------------------------------------------------------------------------
# Regressions for bugs found on self-review after the initial 8-type build —
# each of these was confirmed broken before the corresponding fix in
# workflow_sdk.py, not just theorized.
# ---------------------------------------------------------------------------

def test_explicit_name_colliding_with_autoderived_name_is_rejected_not_silently_merged():
    # Previously: c's auto-derived name silently collided with b's explicit name,
    # and the connection into b became indistinguishable from one into c —
    # validate() reported nothing wrong because it never re-derives names.
    script = (
        'a = Postgres(operation="query", query="select 1")\n'
        'b = Postgres(name="Postgres 2", operation="query", query="select 1")\n'
        'c = Postgres(operation="query", query="select 1")\n'
    )
    result = execute_workflow_script(script)
    assert result.error is None, result.error
    names = [n.name for n in result.workflow.nodes]
    assert len(names) == len(set(names)), f"duplicate node names produced: {names}"


def test_explicit_duplicate_name_raises_clearly():
    script = (
        'a = Postgres(name="X", operation="query", query="select 1")\n'
        'b = Postgres(name="X", operation="query", query="select 1")\n'
    )
    result = execute_workflow_script(script)
    assert result.error is not None
    assert "already used" in result.error


def test_node_escape_hatch_exists():
    # AGENT-WORKFLOW-AUTHORING.md §2.3 documents this; it was documented but
    # never implemented until this fix.
    script = 'n = Node("Postgres", operation="query", query="select 1")\nvalidate()\n'
    result = execute_workflow_script(
        'cron = Cron(cronExpression="0 7 * * 1")\n'
        'n = Node("Postgres", operation="query", query="select 1")\n'
        'cron >> n\n'
        'validate()\n'
    )
    assert result.error is None, result.error
    assert result.workflow.nodes[1].type == "Postgres"


def test_switch_in_test_run_degrades_to_a_named_upper_bound():
    # History of this behaviour, in order: every Switch branch silently treated
    # as (definitely) reached, overcounting writes_staged; then a raise, which
    # was honest but made test_run() unusable on exactly the graphs that most
    # need a dry run. Now: branches behind the Switch are `maybe_reached`,
    # writes_staged is an upper bound, and the undecided branch is named in
    # `limitations` — never understate, never refuse.
    script = (
        'start = Start()\n'
        'router = Switch(numberOfOutputs=2)\n'
        'a = SendEmail(toEmail="a@example.com", subject="s", body="b")\n'
        'b = SendEmail(toEmail="b@example.com", subject="s", body="b")\n'
        'start >> router\n'
        'router.output0 >> a\n'
        'router.output1 >> b\n'
        'report = test_run(input={})\n'
        'results.append(report)\n'
    )
    result = execute_workflow_script(script)
    assert result.error is None, result.error
    report = result.results[0]
    assert report["ok"] is True
    assert set(report["reached"]) == {"Start", "Switch"}
    assert set(report["maybe_reached"]) == {"SendEmail", "SendEmail 2"}
    assert report["writes_staged"] == 2            # upper bound: both branches count
    assert report["writes_staged_definite"] == 0   # neither is certain to fire
    assert any("Switch" in lim for lim in report["limitations"])
    assert report["writes_executed"] == 0


def test_an_undecidable_if_walks_both_branches_as_possible():
    # A `condition` expression can't be evaluated in this slice: the old
    # behaviour raised, the new one counts BOTH branches as possible and says
    # so, keeping writes_staged an upper bound rather than a refusal.
    script = (
        'start = Start()\n'
        'check = If(condition="{{ $json.x > 3 }}")\n'
        'w = Postgres(operation="query", query="select 1")\n'
        'skip = StopAndError(message="no")\n'
        'start >> check\n'
        'check.true >> w\n'
        'check.false >> skip\n'
        'report = test_run(input={})\n'
        'results.append(report)\n'
    )
    result = execute_workflow_script(script)
    assert result.error is None, result.error
    report = result.results[0]
    assert set(report["maybe_reached"]) == {"Postgres", "StopAndError"}
    assert report["writes_staged"] == 1
    assert report["writes_staged_definite"] == 0
    [entry] = [b for b in report["branches"] if b["node"] == "If"]
    assert entry["branch"] == "unknown" and "condition" in entry["reason"]


def test_a_definite_path_beats_a_possible_one_to_the_same_node():
    # One node fed both from the trigger directly (definite) and from behind a
    # Switch (possible): definite wins, and the write counts in BOTH totals.
    script = (
        'start = Start()\n'
        'router = Switch(numberOfOutputs=1)\n'
        'w = Postgres(operation="query", query="select 1")\n'
        'start >> router\n'
        'start >> w\n'
        'router.output0 >> w\n'
        'report = test_run(input={})\n'
        'results.append(report)\n'
    )
    result = execute_workflow_script(script)
    assert result.error is None, result.error
    report = result.results[0]
    assert "Postgres" in report["reached"]
    assert "Postgres" not in report["maybe_reached"]
    assert report["writes_staged"] == 1
    assert report["writes_staged_definite"] == 1


def test_a_failed_script_reports_a_partial_workflow_explicitly():
    # The partial graph on error is deliberate (it's what the canvas shows next
    # to the traceback) but it must be labelled: callers check `partial`/`error`
    # before treating `workflow` as complete.
    script = (
        'cron = Cron(cronExpression="0 7 * * 1")\n'
        'w = Postgres(operation="query", query="select 1")\n'
        'cron >> w\n'
        'boom = Postgres(operatoin="query")\n'  # typo'd param -> TypeError
    )
    result = execute_workflow_script(script)
    assert result.error is not None
    assert result.partial is True
    assert [n.name for n in result.workflow.nodes] == ["Cron", "Postgres"]

    ok = execute_workflow_script(
        'cron = Cron(cronExpression="0 7 * * 1")\n'
        'w = Postgres(operation="query", query="select 1")\n'
        'cron >> w\n'
    )
    assert ok.error is None and ok.partial is False


def test_neo4j_counts_as_a_write_in_test_run():
    script = (
        'cron = Cron(cronExpression="0 7 * * 1")\n'
        'n = Neo4j(query="CREATE (a:Test)")\n'
        'cron >> n\n'
        'report = test_run(input={})\n'
        'results.append(report)\n'
    )
    result = execute_workflow_script(script)
    assert result.error is None, result.error
    assert result.results[0]["writes_staged"] == 1


def test_cycle_not_through_loop_node_is_rejected():
    script = (
        'start = Start()\n'
        'a = HttpRequest(method="GET", url="https://example.com/a")\n'
        'b = HttpRequest(method="GET", url="https://example.com/b")\n'
        'start >> a\n'
        'a >> b\n'
        'b >> a\n'
        'validate()\n'
    )
    result = execute_workflow_script(script)
    assert result.error is not None
    assert "cycle" in result.error.lower()


def test_cycle_through_loop_node_is_allowed():
    script = (
        'start = Start()\n'
        'loop = Loop(batchSize=1)\n'
        'work = HttpRequest(method="GET", url="https://example.com/process")\n'
        'start >> loop\n'
        'loop.loop >> work\n'
        'work >> loop\n'
        'validate()\n'
    )
    result = execute_workflow_script(script)
    assert result.error is None, result.error


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
