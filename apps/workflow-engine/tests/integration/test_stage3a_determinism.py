"""Stage 3A repeatability: for every workflow fixture that can run offline, suggested tests are saved with generated
default mocks and 20 runs through /score give identical results (CONTRACTS-STAGE3A section 7, HANDOFF-STAGE3 section 4)."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest

from .conftest import uniq

pytestmark = pytest.mark.integration

FIX = Path(__file__).resolve().parents[1] / "fixtures"
WORKFLOWS = sorted((FIX / "workflows").glob("*.json"))
RISK_YAML = (FIX / "openapi" / "risk_api.yaml").read_text()
TABLES = {
    "refunds": [{"name": "order_id", "type": "string"}, {"name": "amount", "type": "number"},
                {"name": "reason", "type": "string"}, {"name": "status", "type": "string"}],
    "events": [{"name": "kind", "type": "string"}, {"name": "ref", "type": "string"}, {"name": "label", "type": "string"},
               {"name": "confidence", "type": "number"}, {"name": "detail", "type": "string"}],
}
# outside steps whose reply can't come from a spec or a table: the author writes the mock (items skip execute)
HAND_MOCKS = {
    "Classify": {"items": [{"response": "{\"label\": \"refund\", \"confidence\": 0.9}"}]},
    "Create refund": {"http": {"status": 201, "body": {"id": "r-1"}}},
    # Stage 5 fixtures (refund_triage_ui, chat_support_ui)
    "Issue refund": {"http": {"status": 200, "body": {"ok": True, "refund_id": "rf_1"}}},
    "Auto refund": {"http": {"status": 200, "body": {"ok": True, "refund_id": "rf_2"}}},
    "Assistant": {"items": [{"response": "Hello, how can I help?"}]},
    "Manager review": {"items": [{"approval": {"decision": "approved", "comment": "", "form_data": {}, "decided_by": "test",
                                               "decided_at": "2026-01-01T09:00:00Z", "task_id": "tsk_test"}}], "port": "approved"},
}
EFFECT_TYPES = {"HttpRequest", "SendEmail", "Poll", "Aggregate", "Sample", "Profile", "Postgres", "MongoDB", "Neo4j",
                "DataTable", "LLMChat", "AIAgent", "ExecuteWorkflow", "Approval"}


def _strip(report: dict, outputs: bool = False) -> list:
    drop = ("duration_ms", "execution_id") + (() if outputs else ("outputs",))
    return [{k: v for k, v in r.items() if k not in drop} for r in report["results"]]


_PREPARED: dict[str, tuple[str, list]] = {}


def _prepare(api, fixture: Path) -> tuple[str, list]:
    if fixture.stem in _PREPARED:
        return _PREPARED[fixture.stem]
    pid = api.post("/projects", {"name": uniq("s3a-det")}).json()["id"]
    for name, cols in TABLES.items():
        assert api.post(f"/projects/{pid}/tables", {"name": name, "columns": cols}).status_code == 201
    wf = json.loads(fixture.read_text().replace("__REFUND_API_URL__", "http://127.0.0.1:9999/risk-check"))
    wf["name"] = uniq(fixture.stem)
    wf["settings"] = {}
    spec_id = None
    if fixture.stem == "refund_triage":
        spec = api.post("/api-specs", {"project_id": pid, "spec_text": RISK_YAML})
        assert spec.status_code == 200, spec.text
        spec_id = spec.json()["id"]
        for n in wf["nodes"]:
            if n["name"] == "Risk API":
                n["parameters"] = {"api_ref": {"spec_id": spec_id, "operation_id": "riskCheck"}, "path_params": {},
                                   "body": n["parameters"]["body"], "responseType": "json"}
    for n in wf["nodes"]:
        if n["type"] == "Webhook":
            n["parameters"]["path"] = uniq("hook")
    r = api.post(f"/projects/{pid}/workflows", wf)
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    assert [f for f in api.post(f"/workflows/{wid}/check", {}).json()["findings"] if f["severity"] == "error"] == []

    # default mocks: from the spec, from the table contract, or written by hand
    defaults: dict = {"mocks": {}}
    for n in wf["nodes"]:
        if n["type"] not in EFFECT_TYPES:
            continue
        if n["name"] in HAND_MOCKS:
            defaults["mocks"][n["name"]] = HAND_MOCKS[n["name"]]
            continue
        kind = "from_spec" if n["parameters"].get("api_ref") else "from_contract"
        g = api.post(f"/workflows/{wid}/nodes/{quote(n['name'])}/mock/generate", {"kind": kind})
        assert g.status_code == 200, (n["name"], kind, g.text)
        defaults["mocks"][n["name"]] = g.json()["mock"]
    assert api.put(f"/workflows/{wid}", {"test_defaults": defaults}).status_code == 200

    # suggestions, saved as drafts
    drafts = api.post(f"/workflows/{wid}/tests/suggest", {}).json()
    assert drafts, "a fixture workflow always has something to suggest"
    assert api.put(f"/workflows/{wid}", {"tests": drafts}).status_code == 200
    _PREPARED[fixture.stem] = (wid, drafts)
    return wid, drafts


@pytest.mark.parametrize("fixture", WORKFLOWS, ids=[p.stem for p in WORKFLOWS])
def test_fixture_workflow_gives_identical_results_over_20_runs(api, fixture):
    wid, drafts = _prepare(api, fixture)
    first = api.post(f"/workflows/{wid}/tests/run", {}).json()
    second = api.post(f"/workflows/{wid}/tests/run", {}).json()
    assert first["summary"]["total"] == len(drafts)
    assert _strip(first) == _strip(second)
    assert first["badges"] == second["badges"] and first["grid"] == second["grid"]
    # nothing is left unmocked or broken in setup: every failure is about the test's own expectations
    kinds = {f["kind"] for r in first["results"] for f in r["failures"]}
    assert kinds <= {"assertion", "contract", "node_error"}, [(r["name"], r["failures"]) for r in first["results"] if r["status"] == "error"]

    card = api.post(f"/workflows/{wid}/score", {"mutations": False, "repeat": 20})
    assert card.status_code == 200, card.text
    assert card.json()["repeatability"] == {"runs": 20, "identical": True, "differing_tests": []}
    assert card.json()["tests"] == first["summary"]

    # and with planted bugs on, still deterministic: two scorecards agree
    a = api.post(f"/workflows/{wid}/score", {"mutations": True}).json()
    b = api.post(f"/workflows/{wid}/score", {"mutations": True}).json()
    assert a == b


@pytest.mark.parametrize("fixture", WORKFLOWS, ids=[p.stem for p in WORKFLOWS])
def test_every_recorded_output_is_identical_between_runs(api, fixture):
    wid, _ = _prepare(api, fixture)
    runs = [_strip(api.post(f"/workflows/{wid}/tests/run", {}).json(), outputs=True) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]
