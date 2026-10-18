"""Stage 2 golden fixtures (DESIGN-WORKFLOWS-BACKEND §4.5): seeded workflows, the Marketplace workflows, Refund
triage and the 10 n8n imports go through the checker.

* seeds, Marketplace and Refund triage must have ZERO error findings (with the project objects they need defined);
* every fixture's sorted (code, node, param, path) list is compared with tests/fixtures/checker_snapshots/<name>.json
  (regenerate with ``UPDATE_SNAPSHOTS=1``);
* "broken copies" of Refund triage and two seeds must produce the expected codes.

Pure in-process (the checker is pure apart from injected lookups), so no engine is needed.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

FIX = Path(__file__).resolve().parents[1] / "fixtures"
SNAP = FIX / "checker_snapshots"
UPDATE = os.environ.get("UPDATE_SNAPSHOTS") == "1"

EVENTS = [{"name": "kind", "type": "string"}, {"name": "ref", "type": "string"}, {"name": "label", "type": "string"},
          {"name": "confidence", "type": "number"}, {"name": "detail", "type": "string"}]
REFUNDS = [{"name": "order_id", "type": "string"}, {"name": "amount", "type": "number"},
           {"name": "reason", "type": "string"}, {"name": "status", "type": "string"}]


def lookups(variables=("DEMO_API_TOKEN",), tables=None, allowlist=()):
    from src.services.workflow_checker import StaticLookups
    tables = {"events": EVENTS, "refunds": REFUNDS} if tables is None else tables
    return StaticLookups(variables=set(variables), tables=dict(tables), egress_allowlist=list(allowlist))


def run_check(defn: dict, lk=None) -> dict:
    from src.services.workflow_checker import check_workflow
    return check_workflow(copy.deepcopy(defn), lookups=lk or lookups())


def key(f: dict) -> list:
    return [f["code"], f["node"], f["param"], f["path"]]


def snap_list(res: dict) -> list[list]:
    return sorted((key(f) for f in res["findings"]), key=lambda k: [str(x) for x in k])


def errors(res: dict) -> list[dict]:
    return [f for f in res["findings"] if f["severity"] == "error"]


# ── fixture sources ──────────────────────────────────────────────────────────────────────────


def _seed_defs() -> dict[str, dict]:
    from src.db import seed
    out = {"seed_promptlab": seed._PROMPTLAB_DEFINITION}
    for w in seed.EXAMPLE_WORKFLOWS:
        d = w.get("definition") or {"nodes": w.get("nodes", []), "connections": w.get("connections", [])}
        out["seed_" + w["name"].lower().replace(" ", "_")] = d
    return out


def _file_defs() -> dict[str, dict]:
    out = {}
    for p in sorted((FIX / "workflows").glob("*.json")):
        if p.name.startswith(("marketplace_", "refund_")):
            d = json.loads(p.read_text())
            if p.name == "refund_triage.json":  # placeholder url -> a literal host
                d = json.loads(json.dumps(d).replace("__REFUND_API_URL__", "http://127.0.0.1:9/ok"))
            out[p.stem] = d
    return out


def _n8n_defs() -> dict[str, tuple[dict, dict]]:
    from src.services.n8n_import import import_n8n
    from src.services.workflow_checker import StaticLookups
    out = {}
    for p in sorted((FIX / "n8n").glob("*.json")):
        wf, report = import_n8n(json.loads(p.read_text()))
        out["n8n_" + p.stem.split("_")[0]] = (wf, report)
    return out


def all_fixtures() -> dict[str, dict]:
    fx = {**_seed_defs(), **_file_defs()}
    fx.update({k: v[0] for k, v in _n8n_defs().items()})
    return fx


FIXTURES = all_fixtures()
CLEAN = [k for k in FIXTURES if not k.startswith("n8n_")]


def lookups_for(name: str):
    from src.services.workflow_checker import StaticLookups
    return StaticLookups() if name.startswith("n8n_") else lookups()


def test_fixture_inventory():
    assert {"seed_promptlab", "seed_vars_redaction_demo", "seed_prompt_evaluator", "refund_triage",
            "marketplace_refund_triage", "marketplace_scheduled_audit"} <= set(FIXTURES)
    assert len([k for k in FIXTURES if k.startswith("n8n_")]) == 10
    # the Marketplace fixtures are the definitions of tests/live/marketplace_steps.cjs (Refund triage + Scheduled audit)
    src = (Path(__file__).resolve().parents[1] / "live" / "marketplace_steps.cjs").read_text()
    for name in ("Refund triage", "Scheduled audit"):
        assert f"name:'{name}'" in src
    for nm in ("Classify", "Parse", "Is refund", "Create refund", "Log refund", "Needs review", "Every 10s", "Audit"):
        assert f"name:'{nm}'" in src


@pytest.mark.parametrize("name", CLEAN)
def test_seeds_marketplace_and_refund_triage_have_zero_errors(name):
    res = run_check(FIXTURES[name])
    assert errors(res) == [], json.dumps(errors(res), indent=1)
    # coverage is internally consistent
    c = res["coverage"]
    assert c["nodes_total"] == len(FIXTURES[name]["nodes"]) and c["refs_checked"] + c["refs_unknown"] <= c["refs_total"]


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_snapshot(name):
    res = run_check(FIXTURES[name], lookups_for(name))
    got = snap_list(res)
    path = SNAP / f"{name}.json"
    if UPDATE:
        SNAP.mkdir(exist_ok=True)
        path.write_text(json.dumps(got, indent=1) + "\n")
    assert path.exists(), f"no snapshot {path.name}: run with UPDATE_SNAPSHOTS=1"
    want = json.loads(path.read_text())
    assert got == want, f"checker output for {name} drifted from the snapshot (UPDATE_SNAPSHOTS=1 to accept)"


def test_checker_is_deterministic_and_does_not_mutate_input():
    for name, d in FIXTURES.items():
        before = json.dumps(d, sort_keys=True)
        a = snap_list(run_check(d, lookups_for(name)))
        from src.services.workflow_checker import check_workflow
        b = snap_list(check_workflow(d, lookups=lookups_for(name)))
        assert a == b and json.dumps(d, sort_keys=True) == before, name


# ── broken copies ────────────────────────────────────────────────────────────────────────────


def node(d: dict, name: str) -> dict:
    return next(n for n in d["nodes"] if n["name"] == name)


def broken(name: str) -> dict:
    return copy.deepcopy(FIXTURES[name])


def codes_at(res: dict, code: str, node_name: str | None = None) -> list[dict]:
    return [f for f in res["findings"] if f["code"] == code and (node_name is None or f["node"] == node_name)]


def test_refund_triage_typo_in_field_is_wf002_with_did_you_mean():
    d = broken("refund_triage")
    node(d, "Amount over 500")["parameters"]["condition"] = "{{ $json.amout > 500 }}"
    f = codes_at(run_check(d), "WF002", "Amount over 500")
    assert len(f) == 1 and f[0]["severity"] == "error" and "amount" in f[0]["message"]
    d2 = broken("refund_triage")
    node(d2, "Log escalated")["parameters"]["data"]["order_id"] = '{{ $node["Normalize"].json.order_idd }}'
    assert codes_at(run_check(d2), "WF002", "Log escalated")


def test_refund_triage_reference_to_missing_node_is_wf001():
    d = broken("refund_triage")
    node(d, "Log approved")["parameters"]["data"]["reason"] = '{{ $node["Normaliz"].json.reason }}'
    f = codes_at(run_check(d), "WF001", "Log approved")
    assert len(f) == 1 and "Normaliz" in f[0]["message"]
    d2 = broken("refund_triage")  # a node on the other branch is not an ancestor either
    node(d2, "Log approved")["parameters"]["data"]["reason"] = '{{ $node["Risk API"].json.body }}'
    assert codes_at(run_check(d2), "WF001", "Log approved")


def test_refund_triage_missing_table_and_missing_var():
    assert {f["node"] for f in codes_at(run_check(broken("refund_triage"), lookups(tables={})), "WF006")} == {"Log escalated", "Log approved"}
    d = broken("refund_triage")
    node(d, "Risk API")["parameters"]["body"] = '{"k": "{{ $vars.RISK_KEY }}"}'
    assert codes_at(run_check(d), "WF006", "Risk API")
    assert not codes_at(run_check(d, lookups(variables=("RISK_KEY",))), "WF006")


def test_refund_triage_disallowed_host_is_wf007():
    d = broken("refund_triage")
    assert not codes_at(run_check(d, lookups(allowlist=["127.0.0.1"])), "WF007")
    f = codes_at(run_check(d, lookups(allowlist=["api.company.test"])), "WF007", "Risk API")
    assert len(f) == 1 and "127.0.0.1" in f[0]["message"]


def test_refund_triage_broken_expression_is_wf009():
    d = broken("refund_triage")
    node(d, "Amount over 500")["parameters"]["condition"] = "{{ $json.amount > }}"
    f = codes_at(run_check(d), "WF009", "Amount over 500")
    assert len(f) == 1 and f[0]["severity"] == "error"


def test_refund_triage_removed_required_param_is_wf008():
    d = broken("refund_triage")
    del node(d, "Risk API")["parameters"]["url"]
    f = codes_at(run_check(d), "WF008", "Risk API")
    assert f and f[0]["param"] == "url"
    d2 = broken("refund_triage")
    del node(d2, "Log approved")["parameters"]["table"]
    f2 = codes_at(run_check(d2), "WF008", "Log approved")
    assert f2 and f2[0]["param"] == "table"


def test_refund_triage_unconnected_branch_is_wf004_warning():
    d = broken("refund_triage")
    d["connections"] = [c for c in d["connections"] if c["target_node"] != "Log approved"]
    f = codes_at(run_check(d), "WF004")
    assert f and f[0]["severity"] == "warning" and f[0]["node"] == "Amount over 500"


def test_seed_vars_redaction_broken_copies():
    base = "seed_vars_redaction_demo"
    assert errors(run_check(broken(base))) == []
    f = codes_at(run_check(broken(base), lookups(variables=())), "WF006", "Call Protected API")  # var deleted
    assert len(f) == 1 and "DEMO_API_TOKEN" in f[0]["message"]
    f = codes_at(run_check(broken(base), lookups(allowlist=["api.company.test"])), "WF007", "Call Protected API")  # host
    assert len(f) == 1
    d = broken(base)
    node(d, "Call Protected API")["parameters"]["url"] = "http://127.0.0.1:8765/protected?api_key={{ $vars.DEMO_API_TOKEN + }}"
    assert codes_at(run_check(d), "WF009", "Call Protected API")
    d = broken(base)
    node(d, "Call Protected API")["parameters"]["url"] = "http://127.0.0.1:8765/x?k={{ $node['Nope'].json.t }}"
    assert codes_at(run_check(d), "WF001", "Call Protected API")
    d = broken(base)
    node(d, "Call Protected API")["parameters"]["url"] = ""
    assert any(f["param"] == "url" for f in codes_at(run_check(d), "WF008", "Call Protected API"))


def test_seed_promptlab_broken_copies():
    base = "seed_promptlab"
    assert errors(run_check(broken(base))) == []
    d = broken(base)
    node(d, "summarize")["parameters"]["userMessage"] += " {{ $node['ghostNode'].json.x }}"
    f = codes_at(run_check(d), "WF001", "summarize")
    assert len(f) == 1 and "ghostNode" in f[0]["message"]
    d = broken(base)  # a node on a branch that is not upstream of promptWriter
    node(d, "promptWriter")["parameters"]["userMessage"] += " {{ $node['fetchAllRuns'].json.x }}"
    assert codes_at(run_check(d), "WF001", "promptWriter")
    d = broken(base)
    del node(d, "evalCall")["parameters"]["url"]
    assert any(f["param"] == "url" for f in codes_at(run_check(d), "WF008", "evalCall"))
    d = broken(base)
    node(d, "promptWriter")["parameters"]["userMessage"] += " {{ $json.x + }}"
    assert codes_at(run_check(d), "WF009", "promptWriter")
    d = broken(base)
    node(d, "evalCall")["parameters"]["body"] += ' {{ $vars.NOT_DEFINED_ANYWHERE }}'
    assert codes_at(run_check(d), "WF006", "evalCall")
    d = broken(base)
    node(d, "evalCall")["parameters"]["url"] = "https://evil.example.com/e"
    assert codes_at(run_check(d, lookups(allowlist=["localhost"])), "WF007", "evalCall")


def test_seed_prompt_evaluator_broken_copies():
    base = "seed_prompt_evaluator"
    assert errors(run_check(broken(base))) == []
    for append, code in (("{{ $node['MissingNodeX'].json.a }}", "WF001"), ("{{ $json.a + }}", "WF009"), ("{{ $vars.GONE_VAR }}", "WF006")):
        d = broken(base)
        node(d, "Prompt Evaluator")["parameters"]["task"] += " " + append
        f = codes_at(run_check(d), code, "Prompt Evaluator")
        assert len(f) == 1 and f[0]["param"] == "task", code
    d = broken(base)  # a missing property of the Input node's known output
    d["nodes"].append({"name": "Probe", "type": "Set", "parameters": {"mode": "manual", "fields": [{"name": "x", "value": "{{ $json.nonexistent_field }}"}]}, "position": {"x": 0, "y": 0}})
    d["connections"].append({"source_node": "Respond", "target_node": "Probe"})
    res = run_check(d)
    assert [f for f in res["findings"] if f["node"] == "Probe" and f["code"] in ("WF002", "WF005")]
