"""Stage 3A: HttpRequest with api_ref in the contract (node_contract) and the checker (WF006)."""

from pathlib import Path

from src.engine.contracts import node_contract
from src.engine.node_registry import register_all_nodes
from src.engine.shapes import lookup, object_of, scalar
from src.engine.types import NodeDefinition
from src.services.openapi_import import import_openapi
from src.services.workflow_checker import StaticLookups, check_workflow

register_all_nodes()
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "openapi"
SPEC = import_openapi((FIX / "risk_api.yaml").read_text())
OPS = {o["operation_id"]: o for o in SPEC["operations"]}
REF = {"spec_id": "api_1", "operation_id": "riskCheck"}


def node(**params):
    return NodeDefinition(name="Risk", type="HttpRequest", parameters=params)


def N(name, type_="Set", params=None, **kw):
    return {"name": name, "type": type_, "parameters": params or {}, **kw}


def C(a, b):
    return {"source_node": a, "target_node": b, "source_output": "main", "target_input": "main"}


def test_contract_for_api_ref():
    c = node_contract(node(api_ref=REF), {"main": object_of({"a": scalar("string")})}, api_operation=OPS["riskCheck"])
    main = c["outputs"]["main"]
    assert main["x-source"] == "spec" and main["properties"]["statusCode"]["type"] == "integer"
    assert main["properties"]["headers"]["type"] == "object"
    body = main["properties"]["body"]
    assert body["properties"]["decision"]["enum"] == ["approve", "review", "deny"]
    assert body["properties"]["score"]["maximum"] == 100
    assert "error" in body["properties"]  # documented 400/500 replies fit the same body
    assert "required" not in body  # lenient: a non-2xx reply lacks the 2xx fields


def test_contract_body_is_exact_2xx_shape_without_error_shapes():
    c = node_contract(node(api_ref={"spec_id": "x", "operation_id": "GET /orders/{order_id}"}), {}, api_operation=OPS["GET /orders/{order_id}"])
    body = c["outputs"]["main"]["properties"]["body"]
    assert body == OPS["GET /orders/{order_id}"]["responses"]["200"]["shape"]


def test_contract_without_operation_or_ref_stays_unknown():
    assert node_contract(node(api_ref=REF), {})["outputs"]["main"]["type"] == "unknown"
    assert node_contract(node(url="http://x"), {}, api_operation=OPS["riskCheck"])["outputs"]["main"]["type"] == "unknown"
    assert node_contract(node(api_ref=REF), {}, api_operation={"responses": {}})["outputs"]["main"]["properties"]["body"]["type"] == "unknown"


def run(nodes, conns, **lk):
    return check_workflow({"nodes": nodes, "connections": conns}, lookups=StaticLookups(**lk))


def codes(res, code):
    return [f for f in res["findings"] if f["code"] == code]


def flow(ref, expr="{{ $json.body.decision }}"):
    return [N("Start", "Start"), N("Risk", "HttpRequest", {"api_ref": ref, "path_params": {}}),
            N("Out", "Set", {"fields": [{"name": "d", "value": expr}]})], [C("Start", "Risk"), C("Risk", "Out")]


def test_checker_resolves_api_ref_and_knows_the_shape():
    nodes, conns = flow(REF)
    r = run(nodes, conns, api_specs={"api_1": OPS})
    assert not codes(r, "WF006") and not codes(r, "WF008")  # url / method aren't required for an endpoint
    assert lookup(r["shapes_by_node"]["Risk"]["outputs"]["main"], ["body", "decision"])["enum"] == ["approve", "review", "deny"]
    assert not codes(r, "WF002") and not codes(r, "WF005")


def test_checker_wf002_on_missing_body_field_is_an_error_for_spec_shapes():
    nodes, conns = flow(REF, "{{ $json.body.decison }}")
    f = codes(run(nodes, conns, api_specs={"api_1": OPS}), "WF002")
    assert len(f) == 1 and f[0]["severity"] == "error" and "decision" in f[0]["message"]


def test_wf006_dangling_api_ref():
    nodes, conns = flow({"spec_id": "gone", "operation_id": "riskCheck"})
    f = codes(run(nodes, conns), "WF006")
    assert len(f) == 1 and f[0]["severity"] == "error" and f[0]["node"] == "Risk" and f[0]["param"] == "api_ref"
    nodes, conns = flow({"spec_id": "api_1", "operation_id": "nope"})
    assert codes(run(nodes, conns, api_specs={"api_1": OPS}), "WF006")


def test_plain_url_node_still_needs_its_url():
    r = run([N("Start", "Start"), N("H", "HttpRequest", {"method": "GET", "url": ""})], [C("Start", "H")])
    assert [f["param"] for f in codes(r, "WF008")] == ["url"]


# ── run time: the node builds the request from the catalog entry ──

import asyncio
import json as _json
from datetime import datetime

import httpx
import pytest

from src.engine.types import ExecutionContext, NodeData
from src.nodes.integrations.http_request import HttpRequestNode

from ._runner_fakes import wf

DETAIL = {"id": "api_1", "name": "Risk API", "base_url": "http://risk.test/", "operations": SPEC["operations"]}


def run_node(params, extras, handler=None):
    seen = []

    def default(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"decision": "approve", "score": 3})

    nd = NodeDefinition(name="Risk", type="HttpRequest", parameters=params)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler or default))
    ctx = ExecutionContext(workflow=wf([nd], []), execution_id="e", start_time=datetime.now(), mode="manual",
                           http_client=client, extras=extras)

    async def go():
        try:
            return await HttpRequestNode().execute(ctx, nd, [NodeData(json={"order": {"id": "A 1/2"}, "amt": 7})])
        finally:
            await client.aclose()

    return asyncio.run(go()), seen


def test_node_uses_method_base_url_and_path_params():
    res, seen = run_node(
        {"api_ref": {"spec_id": "api_1", "operation_id": "GET /orders/{order_id}"}, "path_params": {"order_id": "{{ $json.order.id }}"},
         "url": "http://ignored.test/x", "method": "DELETE"},
        {"api_specs": {"api_1": DETAIL}})
    assert len(seen) == 1 and seen[0].method == "GET"
    assert str(seen[0].url) == "http://risk.test/orders/A%201%2F2"  # value is url-quoted; the url param is ignored
    assert res is not None


def test_node_post_with_body():
    res, seen = run_node(
        {"api_ref": {"spec_id": "api_1", "operation_id": "riskCheck"}, "body": '{"order_id": "{{ $json.order.id }}", "amount": {{ $json.amt }}}'},
        {"api_specs": {"api_1": DETAIL}})
    assert seen[0].method == "POST" and str(seen[0].url) == "http://risk.test/risk-check"
    assert _json.loads(seen[0].content) == {"order_id": "A 1/2", "amount": 7}


@pytest.mark.parametrize("params,extras,msg", [
    ({"api_ref": {"spec_id": "api_1", "operation_id": "riskCheck"}}, {}, "not loaded"),
    ({"api_ref": {"spec_id": "api_9", "operation_id": "riskCheck"}}, {"api_specs": {}}, "not found in the API catalog"),
    ({"api_ref": {"spec_id": "api_1", "operation_id": "zzz"}}, {"api_specs": {"api_1": DETAIL}}, "Endpoint"),
    ({"api_ref": {"spec_id": "api_1", "operation_id": "GET /orders/{order_id}"}, "path_params": {}}, {"api_specs": {"api_1": DETAIL}}, "Path parameter"),
])
def test_node_fails_clearly(params, extras, msg):
    with pytest.raises(ValueError, match=msg):
        run_node(params, extras)


def test_node_egress_allowlist_still_applies_to_api_urls():
    with pytest.raises(ValueError, match="egress allowlist"):
        run_node({"api_ref": {"spec_id": "api_1", "operation_id": "riskCheck"}},
                 {"api_specs": {"api_1": DETAIL}, "egress_allowlist": ["other.test"]})
