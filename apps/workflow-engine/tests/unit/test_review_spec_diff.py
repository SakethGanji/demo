"""UI-DESIGN-REVIEW.md D11 / D13 (G28): auth changes and root response type changes are breaking."""

from __future__ import annotations

from types import SimpleNamespace

from src.services.spec_tools import build_openapi, diff_specs

P = SimpleNamespace(name="p", slug="p")


def _route(**kw):
    base = {"id": "route_1", "name": "getX", "method": "GET", "path": "/x", "auth": "api_key", "description": None,
            "request_schema": None, "response_schema": {"type": "object", "properties": {"a": {"type": "string"}}},
            "response_mode": "auto", "handler": {"type": "stub"}}
    return {**base, **kw}


def _diff(old, new):
    return diff_specs(build_openapi(P, [old]), build_openapi(P, [new]))


def test_d11_loosening_auth_is_breaking_and_flagged_as_security():
    ch = _diff(_route(auth="api_key"), _route(auth="none"))
    # UI-DESIGN §9 D11: loosening is its own kind, auth_loosened, and breaking
    auth = next(c for c in ch if c["kind"] == "auth_loosened")
    assert auth["breaking"] is True and auth["security"] == "loosened"
    assert auth["before"] == "api_key" and auth["after"] == "none"
    assert not [c for c in ch if c["kind"] == "auth_changed"]
    for before, after in (("app_token", "none"), ("api_key", "app_token")):
        ch = _diff(_route(auth=before), _route(auth=after))
        assert next(c for c in ch if c["kind"] == "auth_loosened")["breaking"] is True


def test_d11_tightening_auth_stays_breaking():
    auth = next(c for c in _diff(_route(auth="none"), _route(auth="app_token")) if c["kind"] == "auth_changed")
    assert auth["breaking"] is True and auth["security"] == "tightened"


def test_d13_root_response_type_change_is_breaking():
    ch = _diff(_route(), _route(response_schema={"type": "array", "items": {"type": "object"}}))
    root = [c for c in ch if c["kind"] == "response_type_changed"]
    assert len(root) == 1 and root[0]["breaking"] is True and root[0]["field"] is None
    assert root[0]["before"] == "object" and root[0]["after"] == "array"
    # an unchanged root type reports no root change
    assert not [c for c in _diff(_route(), _route()) if c["kind"] == "response_type_changed"]
    # adding a root type to an untyped schema is not a change of type
    assert not [c for c in _diff(_route(response_schema={}), _route()) if c["kind"] == "response_type_changed"]
