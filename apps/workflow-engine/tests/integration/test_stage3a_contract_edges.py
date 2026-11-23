"""Stage 3A edge cases written from CONTRACTS-STAGE3A (not from the code): mock priority, smart mocks, error and
timeout mocks under retry/on_error, request capture, secrets, frozen time, assertion ops, contract checks, renames,
API catalog edges and the effect table."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any
from urllib.parse import quote

import pytest

from .conftest import uniq

pytestmark = pytest.mark.integration

COLUMNS = [{"name": "order_id", "type": "string"}, {"name": "amount", "type": "number"},
           {"name": "reason", "type": "string"}, {"name": "status", "type": "string"}]
TRIGGER = {"triggeredAt": "2026-01-01T09:00:00+00:00", "mode": "manual"}  # what a Start trigger's derived shape requires
UNMOCKED_FIXES = ["generate_from_spec", "use_last_real_reply"]


# ── helpers ──────────────────────────────────────────────────────────────────


def N(name: str, type_: str, params: dict | None = None, **kw) -> dict:
    return {"name": name, "type": type_, "parameters": params or {}, "position": {"x": 0, "y": 0}, **kw}


def E(a: str, b: str, port: str = "main") -> dict:
    return {"source_node": a, "target_node": b, "source_output": port, "target_input": "main"}


def tid() -> str:
    return "t_" + uuid.uuid4().hex[:8]


def T(expect: list, items: list | None = None, target: dict | None = None, mocks: dict | None = None,
      category: str = "normal", **kw) -> dict:
    return {"id": kw.pop("id", None) or tid(), "name": kw.pop("name", "case"), "target": target or {"kind": "workflow"},
            "input": {"items": [{**TRIGGER, "id": 7, "q": "abc", "note": "hi"}] if items is None else items},
            "mocks": mocks or {}, "expect": expect, "category": category, **kw}


def make(api, nodes: list, conns: list, tests: list | None = None, defaults: dict | None = None, **extra) -> str:
    body = {"name": uniq("s3a-edge"), "settings": {}, "nodes": nodes, "connections": conns,
            "tests": tests or [], "test_defaults": defaults or {}, **extra}
    r = api.post("/workflows", body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def run(api, wid: str, **body) -> dict:
    r = api.post(f"/workflows/{wid}/tests/run", body)
    assert r.status_code == 200, r.text
    return r.json()


def one(api, nodes, conns, test, defaults=None, **extra) -> tuple[dict, str]:
    wid = make(api, nodes, conns, [test], defaults, **extra)
    report = run(api, wid)
    assert len(report["results"]) == 1
    return report["results"][0], wid


def A(node: str, op: str, value: Any = None, **kw) -> dict:
    a = {"node": node, "op": op, **kw}
    if value is not None:
        a["value"] = value
    return a


def HTTP(name: str = "H", **p) -> dict:
    return N(name, "HttpRequest", {"method": "GET", "url": "http://h.test/x", "responseType": "json", **p})


START_H = ([N("Start", "Start"), HTTP()], [E("Start", "H")])


@pytest.fixture(scope="module", autouse=True)
def refunds_table(api):
    api.post("/projects/proj_default/tables", {"name": "refunds", "columns": COLUMNS})


# ── mock priority ────────────────────────────────────────────────────────────

PIN = [{"json": {"src": "pinned"}}]


@pytest.mark.parametrize("test_mock,default_mock,pinned,expected", [
    ("test", "default", True, "test"),
    (None, "default", True, "default"),
    (None, None, True, "pinned"),
    (None, None, False, None),
    ("test", None, False, "test"),
    (None, "default", False, "default"),
])
def test_mock_priority_test_then_default_then_pinned_then_unmocked(api, test_mock, default_mock, pinned, expected):
    node = HTTP()
    if pinned:
        node["pinned_data"] = PIN
    mocks = {"H": {"items": [{"src": test_mock}]}} if test_mock else {}
    defaults = {"mocks": {"H": {"items": [{"src": default_mock}]}}} if default_mock else {}
    t = T([A("H", "eq", expected, path="src")] if expected else [], mocks=mocks)
    r, _ = one(api, [N("Start", "Start"), node], [E("Start", "H")], t, defaults)
    if expected is None:
        assert r["status"] == "error" and r["failures"][0]["kind"] == "unmocked"
    else:
        assert r["status"] == "passed", r["failures"]


def test_a_mock_also_replaces_a_node_that_could_run_for_real(api):
    nodes = [N("Start", "Start"), N("S", "Set", {"mode": "manual", "fields": [{"name": "src", "value": "real"}]})]
    inp = {**TRIGGER, "id": 7, "q": "abc", "note": "hi"}  # Set keeps its input, and its derived shape now comes from the test input
    cases = [
        (T([A("S", "eq", "real", path="src")]), {}),
        (T([A("S", "eq", "default", path="src")]), {"mocks": {"S": {"items": [{**inp, "src": "default"}]}}}),
        (T([A("S", "eq", "test", path="src")], mocks={"S": {"items": [{**inp, "src": "test"}]}}), {"mocks": {"S": {"items": [{**inp, "src": "default"}]}}}),
    ]
    for t, defaults in cases:
        r, _ = one(api, nodes, [E("Start", "S")], t, defaults)
        assert r["status"] == "passed", (defaults, r["failures"])


def test_a_mock_on_a_non_main_port_is_emitted_on_that_port(api):
    nodes = [N("Start", "Start"), HTTP(), N("After", "Set", {"mode": "manual", "fields": [{"name": "x", "value": "1"}]})]
    t = T([A("H", "count", 1, port="odd"), A("H", "count", 0), A("After", "status", "not_run")],
          mocks={"H": {"items": [{"a": 1}], "port": "odd"}})
    r, _ = one(api, nodes, [E("Start", "H"), E("H", "After")], t)
    assert r["status"] == "passed", r["failures"]
    assert r["ports_taken"]["H"] == ["odd"]


# ── smart mocks ──────────────────────────────────────────────────────────────

def _http(src: str) -> dict:
    return {"http": {"status": 200, "body": {"src": src}}}


RULES = {"rules": [{"when": {"path": "id", "op": "gt", "value": 10}, "then": _http("big")},
                   {"when": {"path": "id", "op": "gt", "value": 5}, "then": _http("mid")}]}


@pytest.mark.parametrize("item_id,default,expected", [
    (70, None, "big"), (7, None, "mid"),      # first match wins (70 also matches the second rule)
    (1, None, None),                           # no match and no default: unmocked
    (1, _http("dflt"), "dflt"),
])
def test_smart_mock_rules_first_match_then_default_then_unmocked(api, item_id, default, expected):
    mock = {**RULES, **({"default": default} if default else {})}
    t = T([A("H", "eq", expected, path="body.src")] if expected else [], items=[{**TRIGGER, "id": item_id}], mocks={"H": mock})
    r, _ = one(api, *START_H, t)
    if expected is None:
        assert r["status"] == "error" and r["failures"][0]["kind"] == "unmocked" and r["failures"][0]["node"] == "H"
        assert r["failures"][0]["fixes"] == UNMOCKED_FIXES
    else:
        assert r["status"] == "passed", r["failures"]


def test_rules_with_items_leaf_on_an_http_node_output_the_items_like_a_plain_items_mock(api):
    mock = {"rules": [{"when": {"path": "id", "op": "gt", "value": 5}, "then": {"items": [{"src": "big"}]}}]}
    t = T([A("H", "eq", "big", path="src")], mocks={"H": mock})
    r, _ = one(api, *START_H, t)
    assert r["status"] == "passed", r["failures"]


def test_rules_with_items_leaf_on_a_non_http_node_output_the_items(api):
    nodes = [N("Start", "Start"), N("D", "DataTable", {"table": "refunds", "operation": "insert", "data": {}})]
    mock = {"rules": [{"when": {"path": "id", "op": "gt", "value": 5}, "then": {"items": [{"id": 1, "src": "big"}]}}],
            "default": {"items": [{"id": 2, "src": "small"}]}}
    t = T([A("D", "eq", "big", path="src")], mocks={"D": mock})
    assert one(api, nodes, [E("Start", "D")], t)[0]["status"] == "passed"
    t = T([A("D", "eq", "small", path="src")], mocks={"D": mock}, items=[{**TRIGGER, "id": 1}])
    assert one(api, nodes, [E("Start", "D")], t)[0]["status"] == "passed"


def test_mock_expressions_echo_the_input_item_in_items_http_body_and_error_message(api):
    t = T([A("H", "eq", "7-x", path="echo")], mocks={"H": {"items": [{"echo": "{{ $json.id }}-x"}]}})
    r, _ = one(api, *START_H, t)
    assert r["status"] == "passed", r["failures"]
    t = T([A("H", "eq", "hi", path="body.echo")], mocks={"H": {"http": {"status": 200, "body": {"echo": "{{ $json.note }}"}}}})
    assert one(api, *START_H, t)[0]["status"] == "passed"
    t = T([A("H", "status", "error")], mocks={"H": {"error": {"message": "fail {{ $json.id }}"}}})
    r, _ = one(api, *START_H, t)
    assert r["status"] == "passed", r["failures"]
    ex = api.get(f"/executions/{r['execution_id']}").json()
    assert "fail 7" in json.dumps(ex["errors"])


def test_an_error_mock_message_is_echoed_into_the_failure(api):
    t = T([], mocks={"H": {"error": {"message": "fail {{ $json.id }}"}}})
    r, _ = one(api, *START_H, t)
    assert r["status"] == "error" and r["failures"][0]["kind"] == "node_error" and "fail 7" in r["failures"][0]["message"]


# ── unmocked effect, retries, error and timeout mocks ───────────────────────


def test_unmocked_effect_bypasses_retry_and_on_error(api):
    nodes = [N("Start", "Start"), {**HTTP(), "retry_on_fail": 3, "on_error": "error_output"},
             N("Alert", "Set", {"mode": "manual", "fields": [{"name": "alerted", "value": "yes"}]})]
    t = T([A("Alert", "status", "not_run")])
    r, _ = one(api, nodes, [E("Start", "H"), E("H", "Alert", "error")], t)
    assert r["status"] == "error"
    f = [x for x in r["failures"] if x["kind"] == "unmocked"]
    assert len(f) == 1 and f[0]["node"] == "H" and f[0]["fixes"] == UNMOCKED_FIXES
    assert r["node_status"]["H"] == "error" and r["node_status"]["Alert"] == "not_run"
    assert r["requests"].get("H", []) == []
    assert r["ports_taken"].get("H", []) == []
    ex = api.get(f"/executions/{r['execution_id']}").json()
    assert ex["errors"][0]["error_code"] == "TEST_UNMOCKED_EFFECT"
    assert ex["node_metrics"]["H"].get("retries", 0) == 0


def test_an_error_mock_with_retry_on_fail_2_makes_three_attempts(api):
    nodes = [N("Start", "Start"), {**HTTP(), "retry_on_fail": 2}]
    t = T([A("H", "status", "error"), A("H", "count", 3, subject="request")], mocks={"H": {"error": {"message": "503"}}})
    r, _ = one(api, nodes, [E("Start", "H")], t)
    assert r["status"] == "passed", r["failures"]
    ex = api.get(f"/executions/{r['execution_id']}").json()
    assert ex["node_metrics"]["H"]["retries"] == 2


def test_error_mock_with_on_error_continue_keeps_the_run_going_on_a_non_http_node(api):
    nodes = [N("Start", "Start"), {**HTTP(), "on_error": "continue"},
             N("After", "Set", {"mode": "manual", "keepOnlySet": False, "fields": [{"name": "ran", "value": "yes"}]})]
    t = T([A("H", "status", "error"), A("After", "eq", "yes", path="ran"), A("H", "eq", "db down", path="error")],
          mocks={"H": {"error": {"message": "db down", "code": "E_DB"}}})
    r, _ = one(api, nodes, [E("Start", "H"), E("H", "After")], t)
    assert r["status"] == "passed", r["failures"]


def test_an_error_item_passed_on_by_on_error_continue_is_not_a_contract_failure(api):
    nodes = [N("Start", "Start"), N("D", "DataTable", {"table": "refunds", "operation": "insert", "data": {}}, on_error="continue")]
    t = T([A("D", "status", "error"), A("D", "contains", "TEST_MOCK_TIMEOUT: D", path="error")], mocks={"D": {"timeout": True}})
    r, _ = one(api, nodes, [E("Start", "D")], t)
    assert r["status"] == "passed", r["failures"]


def test_timeout_mock_raises_a_timeout_error_naming_the_node(api):
    nodes = [N("Start", "Start"), {**HTTP(), "on_error": "continue"}]
    t = T([A("H", "status", "error"), A("H", "contains", "TEST_MOCK_TIMEOUT: H", path="error")], mocks={"H": {"timeout": True}})
    r, _ = one(api, nodes, [E("Start", "H")], t)
    assert r["status"] == "passed", r["failures"]
    # without on_error the same mock is an unexpected node error, on any node type
    nodes = [N("Start", "Start"), N("D", "DataTable", {"table": "refunds", "operation": "insert", "data": {}})]
    r, _ = one(api, nodes, [E("Start", "D")], T([], mocks={"D": {"timeout": True}}))
    assert r["status"] == "error" and r["failures"][0]["kind"] == "node_error" and "TEST_MOCK_TIMEOUT: D" in r["failures"][0]["message"]


@pytest.mark.parametrize("status", [201, 404, 429, 500, 503])
def test_an_http_mock_is_output_even_for_error_statuses(api, status):
    t = T([A("H", "eq", status, path="statusCode"), A("H", "eq", "nope", path="body.error"), A("H", "status", "success")],
          mocks={"H": {"http": {"status": status, "body": {"error": "nope"}}}})
    r, _ = one(api, *START_H, t)
    assert r["status"] == "passed", r["failures"]
    assert r["node_status"]["H"] == "success"


# ── request capture and secrets ──────────────────────────────────────────────


def test_http_request_capture_builds_url_headers_and_body_from_expressions(api):
    nodes = [N("Start", "Start"), HTTP(method="POST", url="http://h.test/orders/{{ $json.id }}?x={{ $json.q }}",
                                      body='{"id": {{ $json.id }}, "note": "{{ $json.note }}"}',
                                      headers=[{"name": "X-Id", "value": "id-{{ $json.id }}"}])]
    t = T([A("H", "eq", "POST", subject="request", path="method"),
           A("H", "eq", "http://h.test/orders/7?x=abc", subject="request", path="url"),
           A("H", "eq", 7, subject="request", path="body.id"),
           A("H", "eq", "hi", subject="request", path="body.note"),
           A("H", "count", 1, subject="request")],
          mocks={"H": {"http": {"status": 200, "body": {}}}})
    r, _ = one(api, nodes, [E("Start", "H")], t)
    assert r["status"] == "passed", r["failures"]
    req = r["requests"]["H"][0]
    assert {"method", "url", "headers", "body"} <= set(req)
    assert "id-7" in json.dumps(req["headers"])


def test_a_request_is_captured_per_input_item(api):
    t = T([A("H", "count", 2, subject="request"), A("H", "eq", "http://h.test/x", subject="request", path="url", item=1)],
          items=[{**TRIGGER, "id": 1}, {**TRIGGER, "id": 2}], mocks={"H": {"http": {"status": 200, "body": {}}}})
    r, _ = one(api, *START_H, t)
    assert r["status"] == "passed", r["failures"]


def test_datatable_request_capture_has_the_resolved_params_without_credentials(api):
    nodes = [N("Start", "Start"), N("D", "DataTable", {"table": "refunds", "operation": "insert",
                                                       "data": {"order_id": "{{ $json.id }}", "status": "new"}})]
    t = T([A("D", "eq", "refunds", subject="request", path="params.table"),
           A("D", "eq", "insert", subject="request", path="params.operation"),
           A("D", "eq", 7, subject="request", path="params.data.order_id"),
           A("D", "eq", "new", subject="request", path="params.data.status"),
           A("D", "not_exists", subject="request", path="params._credentials"),
           A("D", "count", 1, subject="request")], mocks={"D": {"items": [{"id": 1}]}})
    r, _ = one(api, nodes, [E("Start", "D")], t)
    assert r["status"] == "passed", r["failures"]
    assert "_credentials" not in r["requests"]["D"][0]["params"]


def test_secret_vars_resolve_to_a_placeholder_and_plain_vars_resolve_normally(api):
    pid = api.post("/projects", {"name": uniq("s3a-sec")}).json()["id"]
    key = "RISK_TOKEN_" + uuid.uuid4().hex[:6].upper()
    plain = "REGION_" + uuid.uuid4().hex[:6].upper()
    secret_value = "sk-live-" + uuid.uuid4().hex
    assert api.post(f"/projects/{pid}/secrets", {"key": key, "value": secret_value, "type": "secret"}).status_code == 201
    assert api.post(f"/projects/{pid}/secrets", {"key": plain, "value": "eu-west", "type": "string"}).status_code == 201
    nodes = [N("Start", "Start"), HTTP(url="http://h.test/{{ $vars." + plain + " }}",
                                      headers=[{"name": "Authorization", "value": "Bearer {{ $vars." + key + " }}"}])]
    t = T([A("H", "eq", "http://h.test/eu-west", subject="request", path="url")], mocks={"H": {"http": {"status": 200, "body": {}}}})
    body = {"name": uniq("s3a-sec"), "settings": {}, "nodes": nodes, "connections": [E("Start", "H")], "tests": [t]}
    r = api.post(f"/projects/{pid}/workflows", body)
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    report = run(api, wid)
    res = report["results"][0]
    assert res["status"] == "passed", res["failures"]
    headers = json.dumps(res["requests"]["H"][0]["headers"])
    assert f"<secret:{key}>" in headers
    assert secret_value not in json.dumps(report)
    ex = api.get(f"/executions/{res['execution_id']}").text
    assert secret_value not in ex


# ── frozen clock, Wait, hidden executions ───────────────────────────────────

STAMP = N("Stamp", "Set", {"mode": "manual", "fields": [{"name": "iso", "value": "{{ date_now() }}"},
                                                      {"name": "ms", "value": "{{ now() }}"}, {"name": "ts", "value": "{{ timestamp() }}"}]})


def test_frozen_now_follows_test_defaults_and_a_per_test_override_wins(api):
    nodes, conns = [N("Start", "Start"), STAMP], [E("Start", "Stamp")]
    default = T([A("Stamp", "eq", "2027-03-04T05:06:07+00:00", path="iso")], id="t_00000001")
    over = T([A("Stamp", "eq", "2030-01-02T03:04:05+00:00", path="iso"), A("Stamp", "eq", 1893553445, path="ts")],
             now="2030-01-02T03:04:05+00:00", id="t_00000002")
    wid = make(api, nodes, conns, [default, over], {"now": "2027-03-04T05:06:07+00:00"})
    report = run(api, wid)
    by = {r["test_id"]: r for r in report["results"]}
    # the ms value is computed from the default: 2027-03-04T05:06:07Z
    from datetime import datetime, timezone
    ms = int(datetime(2027, 3, 4, 5, 6, 7, tzinfo=timezone.utc).timestamp() * 1000)
    assert by["t_00000001"]["outputs"]["Stamp"]["main"][0]["ms"] == ms
    assert by["t_00000001"]["outputs"]["Stamp"]["main"][0]["iso"] == "2027-03-04T05:06:07+00:00"
    assert by["t_00000002"]["status"] == "passed", by["t_00000002"]["failures"]
    again = run(api, wid)
    assert [r["outputs"] for r in again["results"]] == [r["outputs"] for r in report["results"]]


def test_the_default_clock_is_2026_01_01_0900_utc(api):
    t = T([A("Stamp", "eq", "2026-01-01T09:00:00+00:00", path="iso")])
    r, _ = one(api, [N("Start", "Start"), STAMP], [E("Start", "Stamp")], t)
    assert r["status"] == "passed", r["failures"]


def test_wait_does_not_sleep_in_a_test_run(api):
    nodes = [N("Start", "Start"), N("W", "Wait", {"unit": "seconds", "duration": 30}),
             N("After", "Set", {"mode": "manual", "fields": [{"name": "done", "value": "yes"}]})]
    t = T([A("W", "status", "success"), A("After", "eq", "yes", path="done")])
    t0 = time.perf_counter()
    r, _ = one(api, nodes, [E("Start", "W"), E("W", "After")], t)
    assert r["status"] == "passed", r["failures"]
    assert time.perf_counter() - t0 < 10 and r["duration_ms"] < 10_000


def test_test_executions_are_hidden_from_both_run_lists_unless_include_test(api):
    r, wid = one(api, *START_H, T([], mocks={"H": {"items": [{"a": 1}]}}))
    eid = r["execution_id"]
    assert eid
    for path in (f"/executions?workflow_id={wid}", f"/projects/proj_default/runs?workflow_id={wid}"):
        assert eid not in [e["id"] for e in api.get(path).json()]
        assert eid in [e["id"] for e in api.get(path + "&include_test=true").json()]
    assert api.get(f"/executions/{eid}").json()["mode"] == "test"


# ── the 16 assertion ops ────────────────────────────────────────────────────

ITEM = {**TRIGGER, "id": 7, "name": "Alice Smith", "price": 9.5, "ok": True, "none": None, "tags": ["a", "b"],
        "obj": {"k": 1}, "rows": [{"id": 1}, {"id": 2}]}
ITEM2 = {**TRIGGER, "id": 8, "name": "Bob", "price": 1, "ok": False, "none": None, "tags": [], "obj": {}, "rows": []}
S = "S"
OPS: list[tuple[str, dict, bool]] = [
    ("eq", A(S, "eq", 7, path="id"), True), ("eq-miss", A(S, "eq", 8, path="id"), False),
    ("eq-whole-item", A(S, "eq", ITEM, path=""), True),
    ("eq-no-path", A(S, "eq", ITEM), True),
    ("neq", A(S, "neq", 8, path="id"), True), ("neq-miss", A(S, "neq", 7, path="id"), False),
    ("gt", A(S, "gt", 9, path="price"), True), ("gt-eq", A(S, "gt", 9.5, path="price"), False),
    ("gte", A(S, "gte", 9.5, path="price"), True), ("gte-miss", A(S, "gte", 10, path="price"), False),
    ("lt", A(S, "lt", 10, path="price"), True), ("lt-eq", A(S, "lt", 9.5, path="price"), False),
    ("lte", A(S, "lte", 9.5, path="price"), True), ("lte-miss", A(S, "lte", 9, path="price"), False),
    ("gt-type-mismatch", A(S, "gt", 5, path="name"), False),
    ("lt-type-mismatch-vs-str", A(S, "lt", "z", path="price"), False),
    ("contains-str", A(S, "contains", "Smith", path="name"), True), ("contains-str-miss", A(S, "contains", "Jones", path="name"), False),
    ("contains-list", A(S, "contains", "a", path="tags"), True), ("contains-list-miss", A(S, "contains", "z", path="tags"), False),
    ("contains-dict-key", A(S, "contains", "k", path="obj"), True), ("contains-dict-key-miss", A(S, "contains", "j", path="obj"), False),
    ("matches", A(S, "matches", "^Alice", path="name"), True), ("matches-search-not-fullmatch", A(S, "matches", "Smi", path="name"), True),
    ("matches-miss", A(S, "matches", "^Smith", path="name"), False),
    ("exists", A(S, "exists", path="id"), True), ("exists-null-counts", A(S, "exists", path="none"), True),
    ("exists-miss", A(S, "exists", path="missing"), False),
    ("not_exists", A(S, "not_exists", path="missing"), True), ("not_exists-null", A(S, "not_exists", path="none"), False),
    ("count-items", A(S, "count", 2), True), ("count-items-miss", A(S, "count", 1), False),
    ("count-ignores-path-and-item", A(S, "count", 2, path="id", item=1), True),
    ("type-string", A(S, "type", "string", path="name"), True), ("type-integer", A(S, "type", "integer", path="id"), True),
    ("type-int-is-number", A(S, "type", "number", path="id"), True), ("type-float-number", A(S, "type", "number", path="price"), True),
    ("type-float-not-integer", A(S, "type", "integer", path="price"), False),
    ("type-bool", A(S, "type", "boolean", path="ok"), True), ("type-bool-not-number", A(S, "type", "number", path="ok"), False),
    ("type-bool-not-integer", A(S, "type", "integer", path="ok"), False),
    ("type-null", A(S, "type", "null", path="none"), True), ("type-array", A(S, "type", "array", path="tags"), True),
    ("type-object", A(S, "type", "object", path="obj"), True), ("type-wrong", A(S, "type", "string", path="id"), False),
    ("one_of", A(S, "one_of", [7, 9], path="id"), True), ("one_of-miss", A(S, "one_of", [1, 2], path="id"), False),
    ("range-both", A(S, "range", {"min": 9, "max": 10}, path="price"), True),
    ("range-inclusive-edges", A(S, "range", {"min": 9.5, "max": 9.5}, path="price"), True),
    ("range-min-only", A(S, "range", {"min": 9.5}, path="price"), True), ("range-max-only", A(S, "range", {"max": 9.5}, path="price"), True),
    ("range-below", A(S, "range", {"min": 10}, path="price"), False), ("range-above", A(S, "range", {"max": 9}, path="price"), False),
    ("status-success", A(S, "status", "success", subject="node"), True), ("status-wrong", A(S, "status", "error", subject="node"), False),
    ("status-not_run", A("Z", "status", "not_run", subject="node"), True),
    ("status-not_run-miss", A("Z", "status", "success", subject="node"), False),
    ("array-path-index", A(S, "eq", 2, path="rows[1].id"), True), ("array-path-first", A(S, "eq", "a", path="tags[0]"), True),
    ("array-path-out-of-range", A(S, "exists", path="rows[5].id"), False),
    ("array-path-not_exists", A(S, "not_exists", path="rows[5].id"), True),
    ("item-index-1", A(S, "eq", 8, path="id", item=1), True), ("item-index-missing", A(S, "exists", path="id", item=5), False),
    ("item-all-pass", A(S, "gt", 6, path="id", item="all"), True), ("item-all-fail", A(S, "gt", 7, path="id", item="all"), False),
    ("item-any-pass", A(S, "eq", 8, path="id", item="any"), True), ("item-any-fail", A(S, "eq", 99, path="id", item="any"), False),
    ("shape-inline", A(S, "shape", {"type": "object", "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}, "required": ["id"]}), True),
    ("shape-inline-miss", A(S, "shape", {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}), False),
    ("shape-inline-keywords", A(S, "shape", {"type": "object", "properties": {"price": {"type": "number", "maximum": 5}}}), False),
    ("shape-no-contract", A("C", "shape"), True),
]


@pytest.fixture(scope="module")
def ops_report(api):
    nodes = [N("Start", "Start"), N(S, "Set", {"mode": "manual", "keepOnlySet": False, "fields": []}),
             N("C", "Code", {"code": "return items"}), N("Z", "Set", {"mode": "manual", "fields": []})]
    tests = []
    for name, a, _ in OPS:
        target = {"kind": "node", "node": "C"} if a["node"] == "C" else {"kind": "node", "node": S}
        tests.append(T([a], items=[ITEM, ITEM2], target=target,
                       id=tid(), name=name))
    wid = make(api, nodes, [E("Start", S), E(S, "C"), E(S, "Z")], tests)
    report = run(api, wid)
    return {r["name"]: r for r in report["results"]}


@pytest.mark.parametrize("name,assertion,passes", OPS, ids=[o[0] for o in OPS])
def test_assertion_op_through_the_api(ops_report, name, assertion, passes):
    r = ops_report[name]
    got = r["assertions"][0]
    assert got["passed"] is passes, got
    assert r["status"] == ("passed" if passes else "failed"), (r["status"], r["failures"])  # a miss fails, never errors
    assert {"node", "op", "expected", "actual", "message"} <= set(got)
    if not passes:
        assert [f["kind"] for f in r["failures"]] == ["assertion"] and r["failures"][0]["node"] == assertion["node"]


def test_type_mismatch_message_and_shape_without_contract_note(ops_report):
    assert "type mismatch" in ops_report["gt-type-mismatch"]["assertions"][0]["message"].lower()
    got = ops_report["shape-no-contract"]["assertions"][0]
    assert got["passed"] is True and got["note"] == "no contract"


def test_status_no_output_and_error_are_distinguished(api):
    nodes = [N("Start", "Start"), N("F", "Filter", {"mode": "rules", "field": "id", "operation": "gt", "value": "1000"}), HTTP()]
    t = T([A("F", "status", "no_output", subject="node"), A("F", "count", 0), A("H", "status", "error", subject="node")],
          mocks={"H": {"error": {"message": "x"}}})
    r, _ = one(api, [*nodes], [E("Start", "F"), E("Start", "H")], t)
    assert r["assertions"][0]["passed"] and r["assertions"][1]["passed"] and r["assertions"][2]["passed"], r["assertions"]


# ── contract checks ─────────────────────────────────────────────────────────

SETN = lambda **kw: N("S", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "n", "value": "abc"}]}, **kw)  # noqa: E731
OUT_SHAPE = {"main": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}}


def test_a_set_output_that_violates_its_declared_shape_is_a_contract_failure(api):
    node = SETN(contract={"source": "declared", "approved_by": "sam", "outputs": OUT_SHAPE})
    t = T([], target={"kind": "node", "node": "S"}, items=[{**TRIGGER, "x": 1}])
    r, _ = one(api, [N("Start", "Start"), node], [E("Start", "S")], t)
    assert r["status"] == "failed"
    f = [x for x in r["failures"] if x["kind"] == "contract"]
    assert len(f) == 1 and f[0]["node"] == "S" and f[0]["path"] == "n" and f[0].get("direction", "output") == "output"
    assert f[0]["expected"] and f[0]["actual"] is not None and f[0]["message"]


def test_an_observed_shape_is_only_a_warning(api):
    node = SETN(contract={"source": "observed", "approved_by": "sam", "outputs": OUT_SHAPE})
    t = T([A("S", "eq", "abc", path="n")], target={"kind": "node", "node": "S"}, items=[{**TRIGGER, "x": 1}])
    r, _ = one(api, [N("Start", "Start"), node], [E("Start", "S")], t)
    assert r["status"] == "passed" and r["failures"] == []
    assert r["warnings"] and r["warnings"][0]["kind"] == "contract" and r["warnings"][0]["node"] == "S"


def test_a_declared_input_shape_failure_has_direction_input(api):
    node = N("S", "Set", {"mode": "manual", "fields": []}, contract={
        "source": "declared", "approved_by": "sam", "outputs": {"main": {"type": "unknown"}},
        "inputs": {"main": {"type": "object", "properties": {"amount": {"type": "number", "minimum": 0}}, "required": ["amount"]}}})
    cases = [({"amount": -1}, "amount"), ({"other": 1}, "amount")]
    for item, path in cases:
        t = T([], target={"kind": "node", "node": "S"}, items=[item])
        r, _ = one(api, [N("Start", "Start"), dict(node)], [E("Start", "S")], t)
        f = [x for x in r["failures"] if x["kind"] == "contract" and x.get("direction") == "input"]
        assert f and f[0]["node"] == "S", (item, r["failures"])
    ok = T([], target={"kind": "node", "node": "S"}, items=[{"amount": 5}])
    assert one(api, [N("Start", "Start"), dict(node)], [E("Start", "S")], ok)[0]["status"] == "passed"


# ── targets ─────────────────────────────────────────────────────────────────


def _chain():
    nodes = [N("Start", "Start")] + [N(x, "Set", {"mode": "manual", "keepOnlySet": False, "fields": [{"name": x.lower(), "value": "1"}]}) for x in "ABC"]
    return nodes, [E("Start", "A"), E("A", "B"), E("B", "C")]


def test_range_target_runs_from_first_through_last_only(api):
    nodes, conns = _chain()
    t = T([A("A", "eq", "1", path="a"), A("B", "eq", "1", path="b"), A("C", "status", "not_run", subject="node"), A("Start", "status", "not_run", subject="node")],
          items=[{**TRIGGER, "seed": True}], target={"kind": "range", "from": "A", "to": "B"})
    r, _ = one(api, nodes, conns, t)
    assert r["status"] == "passed", r["failures"]


def test_node_target_stops_at_the_node_and_unknown_nodes_are_setup_errors(api):
    nodes, conns = _chain()
    t = T([A("B", "eq", "1", path="b"), A("C", "status", "not_run", subject="node")], items=[{**TRIGGER, "z": 1}], target={"kind": "node", "node": "B"})
    assert one(api, nodes, conns, t)[0]["status"] == "passed"
    bad = T([], target={"kind": "node", "node": "Nope"})
    r, _ = one(api, nodes, conns, bad)
    assert r["status"] == "error" and r["failures"][0]["kind"] == "setup"
    bad = T([], target={"kind": "range", "from": "A", "to": "Nope"})
    assert one(api, nodes, conns, bad)[0]["failures"][0]["kind"] == "setup"


def test_merge_target_receives_the_input_items_on_its_first_connection(api):
    nodes = [N("Start", "Start"), N("L", "Set", {"mode": "manual", "fields": []}), N("R", "Set", {"mode": "manual", "fields": []}), N("M", "Merge", {})]
    conns = [E("Start", "L"), E("Start", "R"), E("L", "M"), E("R", "M")]
    t = T([A("M", "status", "success", subject="node"), A("M", "eq", 1, path="v"), A("M", "count", 1)],
          items=[{**TRIGGER, "v": 1}], target={"kind": "node", "node": "M"})
    r, _ = one(api, nodes, conns, t)
    assert r["status"] == "passed", (r["failures"], r["assertions"])


def test_default_covers_for_workflow_tests_are_the_nodes_named_in_expect(api):
    nodes, conns = _chain()
    t = T([A("A", "eq", "1", path="a"), A("C", "eq", "1", path="c")], items=[{**TRIGGER, "x": 1}])
    wid = make(api, nodes, conns, [t])
    report = run(api, wid)
    grid = {row["node"]: row["categories"] for row in report["grid"]["rows"]}
    assert t["id"] in grid["A"]["normal"]["tests"] and t["id"] in grid["C"]["normal"]["tests"]
    assert grid["B"]["normal"]["status"] == "none" and grid["B"]["normal"]["tests"] == []
    assert report["badges"].get("B") is None and report["badges"]["A"] == "pass"
    explicit = T([A("A", "eq", "1", path="a")], items=[{**TRIGGER, "x": 1}], covers=["B"])
    wid = make(api, nodes, conns, [explicit])
    grid = {row["node"]: row["categories"] for row in run(api, wid)["grid"]["rows"]}
    assert grid["B"]["normal"]["tests"] == [explicit["id"]] and grid["A"]["normal"]["status"] == "none"


# ── lock rules: approve and unlock ──────────────────────────────────────────


def test_approve_unlock_cycle_lets_a_test_be_edited_again_and_reports_approved_counts(api):
    t = T([A("H", "status", "success", subject="node")], mocks={"H": {"items": [{"a": 1}]}})
    wid = make(api, *START_H, [t])
    d = api.post(f"/workflows/{wid}/tests/{t['id']}/approve", {"approved_by": "Riley"}).json()["definition"]["tests"][0]
    assert d["locked"] and d["status"] == "approved" and d["approved_by"] == "Riley"
    assert run(api, wid)["summary"]["approved_total"] == 1
    again = api.post(f"/workflows/{wid}/tests/{t['id']}/approve", {"approved_by": "Other"})
    assert again.status_code in (200, 409)
    edited = [{**d, "name": "edited"}]
    assert api.put(f"/workflows/{wid}", {"tests": edited}).status_code == 409
    u = api.post(f"/workflows/{wid}/tests/{t['id']}/unlock", {"by": "Riley"}).json()["definition"]["tests"][0]
    assert u["locked"] is False and u["status"] == "draft" and not u.get("approved_by") and not u.get("approved_at")
    ok = api.put(f"/workflows/{wid}", {"tests": [{**u, "name": "edited"}]})
    assert ok.status_code == 200 and ok.json()["definition"]["tests"][0]["name"] == "edited"
    assert run(api, wid)["summary"]["approved_total"] == 0


def test_renaming_a_node_with_node_renames_keeps_locked_tests_valid_and_runnable(api):
    nodes = [N("Start", "Start"), HTTP("Old")]
    t = T([A("Old", "eq", "x", path="a")], mocks={"Old": {"items": [{"a": "x"}]}})
    wid = make(api, nodes, [E("Start", "Old")], [t], {"mocks": {"Old": {"items": [{"a": "x"}]}}})
    api.post(f"/workflows/{wid}/tests/{t['id']}/approve", {"approved_by": "sam"})
    d = api.get(f"/workflows/{wid}").json()["definition"]
    nn = json.loads(json.dumps(d["nodes"]))
    next(n for n in nn if n["name"] == "Old")["name"] = "New"
    cc = [{**c, "target_node": "New" if c["target_node"] == "Old" else c["target_node"]} for c in d["connections"]]
    r = api.put(f"/workflows/{wid}", {"nodes": nn, "connections": cc, "node_renames": {"Old": "New"}})
    assert r.status_code == 200, r.text
    out = r.json()["definition"]
    assert out["tests"][0]["locked"] and out["tests"][0]["expect"][0]["node"] == "New" and list(out["tests"][0]["mocks"]) == ["New"]
    assert list(out["test_defaults"]["mocks"]) == ["New"]
    assert run(api, wid)["summary"]["passed"] == 1


# ── persistence across version restore and copy ─────────────────────────────


def test_tests_defaults_and_all_test_fields_survive_restore_and_copy(api):
    t = T([A("H", "status", "success", subject="node")], mocks={"H": {"items": [{"a": 1}]}}, category="boundary",
          field="id", covers=["H"], source="ai", now="2029-05-06T07:08:09+00:00", seed=42, description="why", name="full")
    defaults = {"mocks": {"H": {"items": [{"a": 2}]}}, "now": "2028-01-01T00:00:00+00:00", "seed": 9}
    wid = make(api, *START_H, [t], defaults)

    def keys(d: dict) -> dict:
        x = d["tests"][0]
        return {k: x.get(k) for k in ("id", "name", "description", "category", "field", "covers", "source", "now", "seed", "mocks", "expect", "target", "input")}

    stored = api.get(f"/workflows/{wid}").json()["definition"]
    assert stored["test_defaults"] == defaults
    assert keys(stored)["field"] == "id" and keys(stored)["seed"] == 42 and keys(stored)["now"] == "2029-05-06T07:08:09+00:00"
    assert api.post(f"/workflows/{wid}/publish", {"message": "v"}).status_code == 200
    vid = api.get(f"/workflows/{wid}/versions").json()[0]["id"]
    assert api.put(f"/workflows/{wid}", {"tests": [], "test_defaults": {}}).json()["definition"]["tests"] == []
    restored = api.post(f"/workflows/{wid}/versions/{vid}/restore", {}).json()["definition"]
    assert keys(restored) == keys(stored) and restored["test_defaults"] == defaults
    # a copy made the way the studio duplicates / imports a definition keeps both keys
    body = {"name": uniq("copy"), "settings": {}, "nodes": stored["nodes"], "connections": stored["connections"],
            "tests": stored["tests"], "test_defaults": stored["test_defaults"]}
    r = api.post("/workflows", body)
    assert r.status_code == 201, r.text
    copy = api.get(f"/workflows/{r.json()['id']}").json()["definition"]
    assert keys(copy) == keys(stored) and copy["test_defaults"] == defaults


# ── repeatability ───────────────────────────────────────────────────────────


def test_a_time_dependent_workflow_is_identical_over_20_runs(api):
    nodes, conns = [N("Start", "Start"), STAMP], [E("Start", "Stamp")]
    t = T([A("Stamp", "type", "string", path="iso"), A("Stamp", "type", "integer", path="ts")])
    wid = make(api, nodes, conns, [t])
    card = api.post(f"/workflows/{wid}/score", {"mutations": False, "repeat": 20}).json()
    assert card["repeatability"] == {"runs": 20, "identical": True, "differing_tests": []}


# ── API catalog ─────────────────────────────────────────────────────────────

RISK_YAML = (__import__("pathlib").Path(__file__).resolve().parents[1] / "fixtures" / "openapi" / "risk_api.yaml").read_text()


def _detail_text(r) -> str:
    d = r.json()["detail"]
    return d if isinstance(d, str) else d.get("message", json.dumps(d))


def _project(api) -> str:
    return api.post("/projects", {"name": uniq("s3a-cat")}).json()["id"]


def test_swagger_2_is_refused_with_the_exact_message(api):
    r = api.post("/api-specs", {"project_id": _project(api), "spec_text": 'swagger: "2.0"\ninfo: {title: old, version: "1"}\npaths: {}\n'})
    assert r.status_code == 422
    assert "Swagger 2 isn't supported; convert to OpenAPI 3" in _detail_text(r)


@pytest.mark.parametrize("ref", ["https://example.com/schemas/pet.json", "http://example.com/x.yaml#/Pet", "other.yaml#/Pet"])
def test_remote_refs_are_refused(api, ref):
    spec = json.dumps({"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "paths": {"/p": {"get": {
        "operationId": "getP", "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": ref}}}}}}}}})
    r = api.post("/api-specs", {"project_id": _project(api), "spec_text": spec})
    assert r.status_code == 422, r.text
    assert _detail_text(r)


def test_garbage_and_non_openapi_documents_are_422(api):
    pid = _project(api)
    for text in ("::: not yaml :::\n\t- [", '{"hello": "world"}', "just words"):
        assert api.post("/api-specs", {"project_id": pid, "spec_text": text}).status_code == 422


def test_an_operation_without_operation_id_is_addressed_as_method_space_path_with_raw_slashes(api):
    pid = _project(api)
    spec = api.post("/api-specs", {"project_id": pid, "spec_text": RISK_YAML}).json()
    ids = {o["operation_id"] for o in spec["operations"]}
    assert "riskCheck" in ids and "GET /orders/{order_id}" in ids
    r = api.get(f"/api-specs/{spec['id']}/operations/GET /orders/{{order_id}}/mocks")
    assert r.status_code == 200, r.text
    assert {o["category"] for o in r.json()} >= {"ext_success", "ext_timeout"}
    assert api.get(f"/api-specs/{spec['id']}/operations/nope/mocks").status_code == 404

    # and the node can use it: method from the operation, url = base_url + path with {order_id} filled in
    node = N("H", "HttpRequest", {"api_ref": {"spec_id": spec["id"], "operation_id": "GET /orders/{order_id}"},
                                  "path_params": {"order_id": "{{ $json.id }}"}})
    t = T([A("H", "eq", "http://127.0.0.1:9999/orders/7", subject="request", path="url"),
           A("H", "eq", "GET", subject="request", path="method")], mocks={"H": {"http": {"status": 200, "body": {}}}})
    body = {"name": uniq("s3a-op"), "settings": {}, "nodes": [N("Start", "Start"), node], "connections": [E("Start", "H")], "tests": [t]}
    wid = api.post(f"/projects/{pid}/workflows", body).json()["id"]
    r = run(api, wid)["results"][0]
    assert r["status"] == "passed", r["failures"]


def test_from_example_contract_is_used_by_the_checker(api):
    pid = _project(api)
    r = api.post("/api-specs/from-example", {"project_id": pid, "name": "Users", "method": "GET", "base_url": "http://ex.test",
                                             "path": "/users", "example": {"id": 1, "name": "Ann", "tags": ["a"]}})
    assert r.status_code == 200, r.text
    spec = r.json()
    assert len(spec["operations"]) == 1 and spec["source"] == "example"
    op = spec["operations"][0]["operation_id"]

    def wf(field: str) -> list:
        return [N("Start", "Start"), N("H", "HttpRequest", {"api_ref": {"spec_id": spec["id"], "operation_id": op}, "path_params": {}}),
                N("After", "Set", {"mode": "manual", "fields": [{"name": "v", "value": "{{ $json.body." + field + " }}"}]})]

    conns = [E("Start", "H"), E("H", "After")]

    def findings(field: str) -> list:
        body = {"name": uniq("s3a-ex"), "settings": {}, "nodes": wf(field), "connections": conns}
        wid = api.post(f"/projects/{pid}/workflows", body).json()["id"]
        return api.post(f"/workflows/{wid}/check", {}).json()["findings"]

    assert [f for f in findings("name") if f["code"] == "WF002"] == []
    bad = [f for f in findings("nmae") if f["code"] == "WF002"]
    assert len(bad) == 1 and bad[0]["node"] == "After" and "nmae" in bad[0]["message"]
    # a missing spec or operation is WF006
    body = {"name": uniq("s3a-ex"), "settings": {}, "connections": conns, "nodes": [
        N("Start", "Start"), N("H", "HttpRequest", {"api_ref": {"spec_id": spec["id"], "operation_id": "nope"}}), wf("name")[2]]}
    wid = api.post(f"/projects/{pid}/workflows", body).json()["id"]
    assert "WF006" in [f["code"] for f in api.post(f"/workflows/{wid}/check", {}).json()["findings"]]


# ── release and effect table ────────────────────────────────────────────────


def test_release_run_checks_include_workflow_tests_pass_and_it_fails_for_a_failing_approved_test(api):
    pid = _project(api)
    api.post(f"/projects/{pid}/tables", {"name": "refunds", "columns": COLUMNS})
    t = T([A("H", "eq", 1, path="a")], mocks={"H": {"items": [{"a": 1}]}})
    body = {"name": uniq("s3a-rel"), "settings": {}, "nodes": START_H[0], "connections": START_H[1], "tests": [t]}
    wid = api.post(f"/projects/{pid}/workflows", body).json()["id"]
    assert api.post(f"/projects/{pid}/routes", {"method": "GET", "path": "/edge", "handler": {"type": "workflow", "workflow_id": wid}}).status_code == 201
    api.post(f"/workflows/{wid}/tests/{t['id']}/approve", {"approved_by": "sam"})
    ok = api.post(f"/projects/{pid}/releases?dry_run=true", {"environment": "dev"}).json()
    assert next(c for c in ok["checks"] if c["id"] == "workflow_tests_pass")["status"] == "pass"
    d = api.get(f"/workflows/{wid}").json()["definition"]
    nodes = json.loads(json.dumps(d["nodes"]))
    # break it without touching the locked test: change the mock default the test relies on? the test carries its own mock,
    # so break the node instead (a disabled node emits nothing)
    next(n for n in nodes if n["name"] == "H")["disabled"] = True
    assert api.put(f"/workflows/{wid}", {"nodes": nodes, "connections": d["connections"]}).status_code == 200
    bad = api.post(f"/projects/{pid}/releases?dry_run=true", {"environment": "dev"}).json()
    chk = next(c for c in bad["checks"] if c["id"] == "workflow_tests_pass")
    assert chk["status"] == "fail" and [f["test_id"] for f in chk["details"]["failed_tests"]] == [t["id"]]


@pytest.mark.parametrize("node_type", ["Aggregate", "Sample", "Profile", "HttpRequest", "SendEmail", "Poll", "Postgres",
                                       "MongoDB", "Neo4j", "DataTable", "LLMChat", "AIAgent", "ExecuteWorkflow"])
def test_every_outside_step_type_is_unmocked_in_a_test(api, node_type):
    nodes = [N("Start", "Start"), N("X", node_type, {})]
    t = T([])
    wid = make(api, nodes, [E("Start", "X")], [t])
    r = run(api, wid)["results"][0]
    assert r["status"] == "error", r
    f = [x for x in r["failures"] if x["kind"] == "unmocked"]
    assert f and f[0]["node"] == "X" and f[0]["fixes"] == UNMOCKED_FIXES, r["failures"]
    ex = api.get(f"/executions/{r['execution_id']}").json()
    assert ex["errors"][0]["error_code"] == "TEST_UNMOCKED_EFFECT"
    assert ex["errors"][0]["details"]["effect"] in ("network", "database", "llm", "subworkflow")


@pytest.mark.parametrize("node_type,effect", [("Aggregate", "network"), ("Sample", "network"), ("Profile", "network"),
                                              ("Postgres", "database"), ("DataTable", "database"), ("LLMChat", "llm"),
                                              ("ExecuteWorkflow", "subworkflow"), ("Set", "none"), ("Wait", "none"), ("Code", "none")])
def test_node_catalog_reports_the_effect(api, node_type, effect):
    cat = {c["type"]: c for c in api.get("/nodes/catalog").json()}
    assert cat[node_type]["effect"] == effect


# ── input validation on save, spec lifecycle ────────────────────────────────


@pytest.mark.parametrize("mutate", [
    lambda t: t.update(id="bad-id"),
    lambda t: t.update(id="T_ABCDEF12"),
    lambda t: t.update(name=""),
    lambda t: t.update(name="x" * 121),
    lambda t: t.update(category="nonsense"),
    lambda t: t.update(target={"kind": "node"}),
    lambda t: t.update(target={"kind": "range", "from": "A"}),
    lambda t: t.update(target={"kind": "nope"}),
], ids=["id-shape", "id-uppercase", "name-empty", "name-121", "category", "node-target-no-node", "range-no-to", "kind"])
def test_invalid_test_cases_are_rejected_on_save(api, mutate):
    t = T([])
    mutate(t)
    r = api.post("/workflows", {"name": uniq("s3a-bad"), "settings": {}, "nodes": START_H[0], "connections": START_H[1], "tests": [t]})
    assert r.status_code == 422, r.text


def test_duplicate_test_ids_are_rejected_and_an_unknown_test_id_to_run_is_404(api):
    a = T([], id="t_deadbeef")
    r = api.post("/workflows", {"name": uniq("s3a-dup"), "settings": {}, "nodes": START_H[0], "connections": START_H[1], "tests": [a, dict(a)]})
    assert r.status_code == 422, r.text
    wid = make(api, *START_H, [a])
    assert api.post(f"/workflows/{wid}/tests/run", {"ids": ["t_00000000"]}).status_code == 404
    assert api.post("/workflows/wf_missing/tests/run", {}).status_code == 404


def test_a_workflow_without_tests_runs_to_an_empty_report(api):
    wid = make(api, *START_H)
    rep = run(api, wid)
    assert rep["results"] == [] and rep["summary"]["total"] == 0 and rep["badges"] == {}


def test_deleting_a_spec_leaves_the_node_with_a_missing_spec_finding(api):
    pid = _project(api)
    spec = api.post("/api-specs", {"project_id": pid, "spec_text": RISK_YAML}).json()
    assert spec["id"] in [s["id"] for s in api.get(f"/api-specs?project_id={pid}").json()]
    node = N("H", "HttpRequest", {"api_ref": {"spec_id": spec["id"], "operation_id": "riskCheck"}, "path_params": {}})
    wid = api.post(f"/projects/{pid}/workflows", {"name": uniq("s3a-del"), "settings": {}, "nodes": [N("Start", "Start"), node],
                                                   "connections": [E("Start", "H")]}).json()["id"]
    assert not [f for f in api.post(f"/workflows/{wid}/check", {}).json()["findings"] if f["code"] == "WF006"]
    assert api.delete(f"/api-specs/{spec['id']}").json() == {"success": True}
    assert api.get(f"/api-specs/{spec['id']}").status_code == 404
    assert "WF006" in [f["code"] for f in api.post(f"/workflows/{wid}/check", {}).json()["findings"]]


# ── workflow-target shapes come from the test's actual input ─────────────────


def test_workflow_target_shapes_are_derived_from_the_test_input_not_the_trigger_default(api):
    # a Start trigger's default shape requires triggeredAt/mode; a test that omits them must not get downstream contract noise
    set_node = N("S", "Set", {"mode": "manual", "keepOnlySet": False, "fields": [{"name": "ok", "value": "yes"}]})
    t = T([A("S", "eq", "yes", path="ok")], items=[{"order_id": 1}], category="missing")
    r, _ = one(api, [N("Start", "Start"), set_node], [E("Start", "S")], t)
    assert r["status"] == "passed", r["failures"]
    assert not [f for f in r["failures"] if f["kind"] == "contract"]
    # a declared inputs rule on the node itself is still checked
    declared = N("S", "Set", {"mode": "manual", "keepOnlySet": False, "fields": [{"name": "ok", "value": "yes"}]}, contract={
        "source": "declared", "approved_by": "sam", "outputs": {"main": {"type": "unknown"}},
        "inputs": {"main": {"type": "object", "properties": {"amount": {"type": "number"}}, "required": ["amount"]}}})
    r, _ = one(api, [N("Start", "Start"), declared], [E("Start", "S")], T([], items=[{"order_id": 1}], category="missing"))
    bad = [f for f in r["failures"] if f["kind"] == "contract" and f.get("direction") == "input"]
    assert r["status"] == "failed" and bad and bad[0]["node"] == "S", r["failures"]
    r, _ = one(api, [N("Start", "Start"), declared], [E("Start", "S")], T([], items=[{"amount": 3}]))
    assert r["status"] == "passed", r["failures"]
