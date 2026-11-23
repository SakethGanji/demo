"""Stage 6 / DESIGN-STAGE6-AI-BUILDER §6.5: default mocks for the effectful nodes of an AI-built workflow.

Pinned from the contract table; `approval_items` parity with the studio's `approvalMocks.ts` goes through the shared fixture
`tests/fixtures/ai_builder/approval_items.json` (and the TS source is parsed textually, so a drift on either side fails here).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from src.engine.effects import effect_of
from src.engine.test_mode import mock_kind
from src.services import ai_default_mocks as dm
from src.services.mock_gen import example_from_shape, mock_options

ENGINE = Path(__file__).resolve().parents[2]
FIXTURE = ENGINE / "tests" / "fixtures" / "ai_builder" / "approval_items.json"
TS = ENGINE.parents[1] / "apps" / "workflow-studio" / "src" / "features" / "workflow-editor" / "lib" / "approvalMocks.ts"

OPS = {"refunds": {
    "issueRefund": {"method": "POST", "path": "/refunds", "responses": {
        "201": {"shape": {"type": "object", "properties": {"refund_id": {"type": "string"}, "status": {"type": "string", "enum": ["scheduled"]}}},
                "example": {"refund_id": "rf_1", "status": "scheduled"}},
        "422": {"shape": {"type": "object", "properties": {"error": {"type": "string"}}}}}},
    "noExample": {"method": "GET", "path": "/refunds/{id}", "responses": {
        "200": {"shape": {"type": "object", "properties": {"amount": {"type": "number", "minimum": 5}, "ok": {"type": "boolean"}}}}}},
    "noDocs": {"method": "GET", "path": "/x", "responses": {"4XX": {"description": "bad"}}},
}}


def node(name, type_, **params):
    return {"name": name, "type": type_, "parameters": params, "position": {"x": 0, "y": 0}}


def mock_of(n, ops=OPS):
    return dm.default_mock_for(n, ops)


# ── each node kind ──────────────────────────────────────────────────────────────────────────


def test_http_with_api_ref_uses_the_first_2xx_mock_option_with_the_documented_example():
    m, warn = mock_of(node("Pay", "HttpRequest", api_ref={"spec_id": "refunds", "operation_id": "issueRefund"}))
    first_ok = next(o for o in mock_options(OPS["refunds"]["issueRefund"]) if o["category"] == "ext_success")
    assert m == first_ok["mock"] == {"http": {"status": 201, "body": {"refund_id": "rf_1", "status": "scheduled"}}}
    assert warn is None


def test_http_with_api_ref_without_example_uses_the_shape():
    m, _ = mock_of(node("Get", "HttpRequest", api_ref={"spec_id": "refunds", "operation_id": "noExample"}))
    assert m["http"]["status"] == 200
    assert m["http"]["body"] == example_from_shape(OPS["refunds"]["noExample"]["responses"]["200"]["shape"])
    assert m["http"]["body"]["amount"] >= 5


def test_http_never_defaults_to_an_error_response():
    m, _ = mock_of(node("Pay", "HttpRequest", api_ref={"spec_id": "refunds", "operation_id": "issueRefund"}))
    assert 200 <= m["http"]["status"] < 300


def test_http_with_api_ref_whose_operation_documents_no_success_still_gets_a_2xx_mock():
    m, warn = mock_of(node("X", "HttpRequest", api_ref={"spec_id": "refunds", "operation_id": "noDocs"}))
    assert 200 <= m["http"]["status"] < 300 and mock_kind(m) == "http"
    assert warn, "a guessed mock is shown in review"


def test_http_without_api_ref_is_an_empty_200_with_a_warning():
    m, warn = mock_of(node("Call", "HttpRequest", url="https://api.bank.example/x"))
    assert m == {"http": {"status": 200, "body": {}}}
    assert warn and "Call" in warn


def test_http_with_an_unknown_api_ref_is_still_mocked_and_warns():
    m, warn = mock_of(node("Call", "HttpRequest", api_ref={"spec_id": "nope", "operation_id": "nope"}))
    assert m == {"http": {"status": 200, "body": {}}} and warn


@pytest.mark.parametrize("ntype", ["LLMChat", "AIAgent"])
def test_llm_nodes_with_an_output_schema_mock_one_example_item(ntype):
    schema = {"type": "object", "properties": {"verdict": {"type": "string", "enum": ["complete", "incomplete"]}, "score": {"type": "number"}}}
    m, _ = mock_of(node("AI", ntype, output_schema=schema))
    assert set(m) == {"items"} and len(m["items"]) == 1
    item = m["items"][0]["structured"] if ntype == "AIAgent" else m["items"][0]  # AIAgent: {response, structured}
    assert item["verdict"] == "complete" and isinstance(item["score"], (int, float))


def test_llm_output_schema_may_be_a_json_string_or_camel_case():
    schema = {"type": "object", "properties": {"label": {"type": "string", "enum": ["a"]}}}
    for params in ({"output_schema": json.dumps(schema)}, {"outputSchema": schema}, {"outputSchema": json.dumps(schema)}):
        m, _ = mock_of(node("AI", "LLMChat", **params))
        assert m["items"][0] == {"label": "a"}, params


@pytest.mark.parametrize("params", [{}, {"output_schema": ""}, {"output_schema": "not json"}, {"output_schema": {}}, {"output_schema": None}])
def test_llm_nodes_without_a_usable_schema_mock_a_text_item(params):
    m, _ = mock_of(node("AI", "LLMChat", **params))
    assert len(m["items"]) == 1 and "text" in m["items"][0]
    assert mock_kind(m) == "items"


def test_approval_mock_is_approved_with_the_port():
    m, _ = mock_of(node("Review", "Approval"))
    assert m == {"port": "approved", "approval": dm.approval_items("approved")[0]["approval"]}  # decision-only: the run adds the real incoming item
    assert mock_kind(m) == "approval"


def test_approval_port_is_one_of_the_nodes_real_ports():
    from src.engine.node_registry import node_registry
    from src.services.ai_grounding import full_catalog

    full_catalog()  # loads the node modules into the registry
    ports = node_registry.get("Approval").output_ports({})
    assert dm.APPROVAL_PORTS == ("approved", "rejected", "timed_out") and set(dm.APPROVAL_PORTS) <= set(ports)


@pytest.mark.parametrize("ntype", ["Postgres", "MongoDB", "DataTable"])
def test_database_nodes_mock_no_rows_with_a_warning(ntype):
    m, warn = mock_of(node("Rows", ntype))
    assert m == {"items": []} and warn and "Rows" in warn


def test_send_email_mocks_sent_true():
    m, _ = mock_of(node("Mail", "SendEmail"))
    assert m == {"items": [{"sent": True}]}


def test_execute_workflow_mocks_one_empty_item_with_a_warning():
    m, warn = mock_of(node("Sub", "ExecuteWorkflow"))
    assert m == {"items": [{}]} and warn


@pytest.mark.parametrize("ntype", ["If", "Switch", "Set", "Code", "Respond", "Webhook", "FormTrigger", "Merge", "StopAndError"])
def test_nodes_without_an_outside_effect_get_no_default_mock(ntype):
    assert effect_of(ntype) == "none"
    assert mock_of(node("N", ntype)) == (None, None)


def test_every_effectful_catalog_type_gets_a_valid_default_mock():
    from src.services.ai_grounding import full_catalog

    effectful = [e["type"] for e in full_catalog() if e.get("effect") not in (None, "none")]
    assert "HttpRequest" in effectful and "Approval" in effectful and len(effectful) >= 8
    for t in effectful:
        m, _ = mock_of(node("N", t))
        assert m is not None, t
        assert mock_kind(m) in ("items", "http", "approval"), (t, m)  # Approval defaults are decision-only now


def test_default_mocks_are_json_serialisable_and_independent_copies():
    n = node("Pay", "HttpRequest", api_ref={"spec_id": "refunds", "operation_id": "issueRefund"})
    a, _ = mock_of(n)
    json.dumps(a)
    a["http"]["body"]["refund_id"] = "mutated"
    b, _ = mock_of(n)
    assert b["http"]["body"]["refund_id"] == "rf_1"
    assert OPS["refunds"]["issueRefund"]["responses"]["201"]["example"]["refund_id"] == "rf_1"


# ── fill(): never overrides ─────────────────────────────────────────────────────────────────


def defn(*nodes, **extra):
    return {"name": "wf", "nodes": list(nodes), "connections": [], "tests": [], **extra}


def test_fill_adds_defaults_for_effectful_nodes_only_and_reports_guesses():
    d = defn(node("Form", "FormTrigger"), node("Gate", "If"), node("Pay", "HttpRequest", api_ref={"spec_id": "refunds", "operation_id": "issueRefund"}),
             node("Rows", "Postgres"), node("Review", "Approval"), node("Mail", "SendEmail"))
    out, warnings = dm.fill(d, OPS)
    mocks = out["test_defaults"]["mocks"]
    assert set(mocks) == {"Pay", "Rows", "Review", "Mail"}
    assert [w["node"] for w in warnings] == ["Rows"] and all(set(w) == {"node", "message"} for w in warnings)


def test_fill_never_overrides_an_existing_default_mock():
    custom = {"http": {"status": 503, "body": {"why": "custom"}}}
    d = defn(node("Pay", "HttpRequest", api_ref={"spec_id": "refunds", "operation_id": "issueRefund"}), test_defaults={"mocks": {"Pay": custom}, "other": 1})
    out, warnings = dm.fill(d, OPS)
    assert out["test_defaults"]["mocks"]["Pay"] == custom
    assert out["test_defaults"]["other"] == 1
    assert warnings == []


def test_fill_does_not_touch_the_mocks_of_spec_tests():
    spec_mock = {"http": {"status": 201, "body": {"refund_id": "from-test"}}}
    test = {"id": "t_aaaaaaaa", "name": "t", "status": "approved", "locked": True, "target": {"kind": "workflow"}, "mocks": {"Pay": spec_mock}, "expect": []}
    d = defn(node("Pay", "HttpRequest", api_ref={"spec_id": "refunds", "operation_id": "issueRefund"}), tests=[test])
    before = copy.deepcopy(d["tests"])
    out, _ = dm.fill(d, OPS)
    assert out["tests"] == before and out["tests"][0]["mocks"]["Pay"] == spec_mock
    assert out["test_defaults"]["mocks"]["Pay"] != spec_mock  # the default is the catalog's, not the test's


def test_fill_does_not_mutate_its_input():
    d = defn(node("Rows", "Postgres"))
    before = copy.deepcopy(d)
    dm.fill(d, OPS)
    assert d == before


def test_fill_is_idempotent():
    d = defn(node("Rows", "Postgres"), node("Review", "Approval"))
    once, w1 = dm.fill(d, OPS)
    twice, w2 = dm.fill(once, OPS)
    assert once == twice and w1 and w2 == []


def test_fill_skips_disabled_and_unnamed_nodes():
    d = defn({**node("Off", "Postgres"), "disabled": True}, {"type": "Postgres", "parameters": {}}, "garbage", node("On", "Postgres"))
    out, _ = dm.fill(d, OPS)
    assert set(out["test_defaults"]["mocks"]) == {"On"}


def test_fill_copes_with_missing_or_odd_test_defaults():
    for td in (None, "x", [], {"mocks": None}):
        out, _ = dm.fill(defn(node("Rows", "Postgres"), test_defaults=td), OPS)
        assert "Rows" in out["test_defaults"]["mocks"]


def test_fill_with_no_operations_still_mocks_every_http_node():
    out, warnings = dm.fill(defn(node("Pay", "HttpRequest", api_ref={"spec_id": "refunds", "operation_id": "issueRefund"})), None)
    assert out["test_defaults"]["mocks"]["Pay"]["http"]["status"] == 200 and warnings


def test_fill_empty_workflow():
    out, warnings = dm.fill({"nodes": []}, OPS)
    assert out["test_defaults"]["mocks"] == {} and warnings == []


def test_a_workflow_with_every_effect_runs_strict_without_unmocked_effects():
    """The point of the defaults: after fill, every enabled effectful node has a mock (TEST_UNMOCKED_EFFECT cannot fire)."""
    from src.services.ai_grounding import full_catalog

    nodes = [node(f"N_{e['type']}", e["type"]) for e in full_catalog() if e.get("effect") not in (None, "none")]
    out, _ = dm.fill(defn(*nodes), OPS)
    assert {n["name"] for n in nodes} <= set(out["test_defaults"]["mocks"])


# ── approval_items parity with the studio ───────────────────────────────────────────────────


def _fixture():
    return json.loads(FIXTURE.read_text())


@pytest.mark.parametrize("port", ["approved", "rejected", "timed_out"])
def test_python_approval_items_equal_the_shared_fixture(port):
    assert dm.approval_items(port) == _fixture()[port]


def test_default_port_is_approved_and_unknown_ports_fall_back_to_approved():
    assert dm.approval_items() == _fixture()["approved"]
    assert dm.approval_items("nonsense") == _fixture()["approved"]
    assert dm.approval_items("") == _fixture()["approved"]


def test_approval_items_returns_fresh_objects():
    a = dm.approval_items("approved")
    a[0]["approval"]["comment"] = "mutated"
    assert dm.approval_items("approved")[0]["approval"]["comment"] == ""


def test_fixture_has_exactly_the_three_ports():
    assert {k for k in _fixture() if not k.startswith("_")} == {"approved", "rejected", "timed_out"}


def _ts_items(port: str) -> list:
    """Evaluate `sampleApprovalItems(port)` from the TS source text, without a JS runtime (the function is one JSON.stringify of a literal)."""
    src = TS.read_text()
    ports = re.search(r"APPROVAL_PORTS\s*=\s*\[([^\]]*)\]", src).group(1)
    assert [p.strip().strip("'\"") for p in ports.split(",")] == ["approved", "rejected", "timed_out"], "studio ports changed"
    body = re.search(r"export function sampleApprovalItems\(port: ApprovalPort\): string \{(.*?)\n\}", src, re.S).group(1)
    decision = re.search(r"const decision = (.+)", body).group(1).strip()
    m = re.fullmatch(r"port === '(\w+)' \? '(\w+)' : port", decision)
    assert m, f"unexpected decision expression: {decision}"
    decision_val = m.group(2) if port == m.group(1) else port
    lit = re.search(r"JSON\.stringify\(\[\{ approval: \{ (.*?) \} \}\]", body).group(1)
    decided = re.search(r"decided_by: port === '(\w+)' \? '([^']+)' : '([^']+)'", lit)
    assert decided, f"unexpected decided_by: {lit}"
    by = decided.group(2) if port == decided.group(1) else decided.group(3)
    fields = re.sub(r"decided_by: .*$", "", lit).strip().rstrip(",")
    assert fields == "decision, comment: '', form_data: {}", f"studio payload shape changed: {fields}"
    return [{"approval": {"decision": decision_val, "comment": "", "form_data": {}, "decided_by": by}}]


@pytest.mark.parametrize("port", ["approved", "rejected", "timed_out"])
def test_studio_approvalmocks_ts_equals_the_shared_fixture_and_the_python_twin(port):
    assert TS.exists(), f"studio file missing: {TS}"
    ts = _ts_items(port)
    assert ts == _fixture()[port], "studio sampleApprovalItems drifted from the shared fixture"
    assert ts == dm.approval_items(port), "studio and engine approval items differ"


def test_approval_mock_carries_the_same_decision_for_every_port():
    for port in ("approved", "rejected", "timed_out"):
        assert dm.approval_mock(port) == {"approval": _fixture()[port][0]["approval"], "port": port}  # decision-only: the run adds the real incoming item
