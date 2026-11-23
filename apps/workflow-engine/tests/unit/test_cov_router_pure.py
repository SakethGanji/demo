"""Pure-logic coverage of the router: path matching/specificity, body parsing/capping, result redaction."""

from __future__ import annotations

import json

import pytest

from src.services.route_dispatcher import DispatchResult, capped_body, compile_path, match_route, parse_body, redact_result


def R(method, path, name=None):
    return {"method": method, "path": path, "name": name or f"{method}{path}"}


def test_compile_path_params_and_root():
    assert compile_path("/orders/{id}").match("/orders/42").groupdict() == {"id": "42"}
    assert compile_path("/orders/{id}").match("/orders/42/x") is None
    assert compile_path("/orders/{id}").match("/orders/") is None
    assert compile_path("/").match("/") and compile_path("/").match("")
    assert compile_path("/a.b").match("/axb") is None  # literal segments are escaped


def test_match_route_specificity_literal_beats_param_regardless_of_order():
    specs = [R("GET", "/orders/{id}"), R("GET", "/orders/stats")]
    for s in (specs, specs[::-1]):
        r, params, allowed = match_route(s, "GET", "/orders/stats")
        assert r["path"] == "/orders/stats" and params == {}
        r, params, _ = match_route(s, "GET", "/orders/7")
        assert r["path"] == "/orders/{id}" and params == {"id": "7"}


def test_match_route_method_mismatch_reports_allowed_and_no_match():
    specs = [R("GET", "/items"), R("POST", "/items"), R("DELETE", "/items/{id}")]
    r, params, allowed = match_route(specs, "PUT", "/items")
    assert r is None and allowed == ["GET", "POST"]
    r, params, allowed = match_route(specs, "GET", "/nothing")
    assert (r, params, allowed) == (None, {}, [])


def test_match_route_normalises_slashes_and_root():
    specs = [R("GET", "/"), R("GET", "/a/b")]
    assert match_route(specs, "GET", "a/b/")[0]["path"] == "/a/b"
    assert match_route(specs, "GET", "//")[0]["path"] == "/"
    assert match_route(specs, "GET", "")[0]["path"] == "/"


def test_match_route_multiple_params():
    r, p, _ = match_route([R("GET", "/u/{uid}/orders/{oid}")], "GET", "/u/1/orders/2")
    assert p == {"uid": "1", "oid": "2"}


def test_parse_body_json_text_binary_and_garbage_json():
    assert parse_body(b"", "application/json") is None
    assert parse_body(b'{"a":1}', "application/json") == {"a": 1}
    assert parse_body(b"[1,2]", "text/plain") == [1, 2]  # sniffed by first byte
    assert parse_body(b"{not json", "application/json") == "{not json"
    assert parse_body(b"hello", "text/plain") == "hello"
    assert parse_body(b"\xff\xfe\x00", "application/octet-stream") is None


def test_capped_body_small_large_and_unserialisable():
    assert capped_body({"a": 1}) == {"body": {"a": 1}, "truncated": False}
    big = "x" * 20000
    out = capped_body(big)
    assert out["truncated"] is True and out["body"] is None and out["size_bytes"] == 20000 and len(out["body_preview"]) == 16384
    assert capped_body(object())["truncated"] is False  # falls back to str()
    exact = "y" * 16384
    assert capped_body(exact)["truncated"] is False
    assert capped_body(exact + "y")["truncated"] is True
    assert capped_body(None) == {"body": None, "truncated": False}


def test_redact_result_masks_body_headers_and_error_including_encodings():
    import base64
    secret = "sk_prod_SUPERSECRET"
    res = DispatchResult.__new__(DispatchResult)
    res.__dict__.update(dict(status=200, body={"tok": secret, "b64": base64.b64encode(secret.encode()).decode(), "ok": 1},
                             headers={"x-echo": secret}, error={"message": f"bad {secret}"}))
    out = redact_result(res, {secret})
    s = json.dumps([out.body, out.headers, out.error])
    assert secret not in s and base64.b64encode(secret.encode()).decode() not in s
    assert out.body["ok"] == 1


def test_redact_result_noop_without_secrets_or_with_short_ones():
    res = DispatchResult.__new__(DispatchResult)
    res.__dict__.update(dict(status=200, body={"a": "abc"}, headers={}, error=None))
    assert redact_result(res, set()).body == {"a": "abc"}
    assert redact_result(res, {"abc"}).body == {"a": "abc"}  # < 4 chars never masked
