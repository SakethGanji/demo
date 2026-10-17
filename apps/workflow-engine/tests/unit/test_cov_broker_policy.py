"""Broker/egress policy, read-only gate, secret host binding, crypto + fingerprint (pure)."""

from __future__ import annotations

import asyncio
import os

import pytest

from src.broker.policy import EgressGuard, PolicyError, check_parameters, check_read_only, egress_kind, host_matches, secret_refs, snake
from src.services.project_service import ProjectError
from src.services.secret_service import fingerprint, validate_hosts


@pytest.mark.parametrize("host,pats,ok", [
    ("api.stripe.com", ["api.stripe.com"], True), ("API.Stripe.com", ["api.stripe.com"], True),
    ("x.stripe.com", ["*.stripe.com"], True), ("stripe.com", ["*.stripe.com"], True), ("evilstripe.com", ["*.stripe.com"], False),
    ("stripe.com.evil.io", ["*.stripe.com"], False), ("127.0.0.1", ["localhost"], False), ("localhost", ["127.0.0.1"], False),
    ("a.b", [], False), ("", ["a"], False),
])
def test_host_matches(host, pats, ok):
    assert host_matches(host, pats) is ok


def test_egress_guard_project_allowlist_and_secret_binding():
    g = EgressGuard(["api.ok.com"], {})
    g.check_host("api.ok.com")
    with pytest.raises(PolicyError) as e:
        g.check_host("evil.com")
    assert (e.value.code, e.value.status) == ("host_not_allowed", 403) and g.violation is e.value
    open_ = EgressGuard([], {})
    open_.check_host("anything.example")  # empty allowlist = no project restriction
    bound = EgressGuard([], {"STRIPE": ["api.stripe.com"]})
    bound.check_host("api.stripe.com")
    with pytest.raises(PolicyError) as e:
        bound.check_host("evil.com")
    assert e.value.extra["key"] == "STRIPE"


def test_egress_guard_postgres_connection_strings():
    g = EgressGuard(["db.ok.com"], {})
    g.check_postgres_connection("postgresql://u:p@db.ok.com:5432/x")
    for bad in ("postgresql://u:p@evil.com/x", "host=a,b dbname=x", "host=db.ok.com hostaddr=1.2.3.4", "dbname=x",
                "host=/var/run/postgresql", "service=foo"):
        with pytest.raises(PolicyError):
            EgressGuard(["db.ok.com"], {}).check_postgres_connection(bad)


def test_read_only_destination_blocks_private_unless_explicitly_allowed():
    async def go(url, allow):
        import httpx
        g = EgressGuard(allow, {}, read_only=True)
        await g.check_read_only_destination(httpx.URL(url))
    for url in ("http://10.0.0.5/x", "http://169.254.169.254/", "http://[::1]:9/", "http://127.0.0.1:9/", "http://localhost:9/"):
        with pytest.raises(PolicyError) as e:
            asyncio.run(go(url, []))
        assert e.value.code == "read_only"
    asyncio.run(go("http://127.0.0.1:9/", ["127.0.0.1"]))  # explicitly allowlisted loopback is fine (non-engine port)


@pytest.mark.parametrize("node,params,ok", [
    ("DataTable", {"operation": "list"}, True), ("DataTable", {}, True), ("DataTable", {"operation": "GET"}, True),
    ("DataTable", {"operation": "insert"}, False), ("DataTable", {"operation": "{{ $json.op }}"}, False), ("DataTable", {"operation": 3}, False),
    ("HttpRequest", {"method": "GET"}, True), ("HttpRequest", {"method": "head"}, True), ("HttpRequest", {}, True),
    ("HttpRequest", {"method": "POST"}, False), ("HttpRequest", {"method": "{{ $json.m }}"}, False),
    ("Postgres", {}, False), ("SendEmail", {}, False), ("Set", {}, True), ("SomeOpaqueNode", {}, False),
])
def test_check_read_only(node, params, ok):
    if ok:
        check_read_only(node, params)
    else:
        with pytest.raises(PolicyError) as e:
            check_read_only(node, params)
        assert e.value.code == "read_only" and e.value.status == 403


def test_secret_refs_bare_only_and_credential_ban():
    assert secret_refs({"h": [{"v": "Bearer {{ $vars.A }}"}], "u": "{{$vars.B}}"}) == {"A", "B"}
    for bad in ("{{ $vars.A + 'x' }}", "{{ $vars['A'] }}", "{{ JSON.stringify($vars) }}"):
        with pytest.raises(PolicyError) as e:
            secret_refs({"x": bad})
        assert e.value.code == "secret_expression_not_allowed"
    with pytest.raises(PolicyError) as e:
        check_parameters({"a": {"credentialId": "c1"}}, [])
    assert e.value.code == "credential_not_allowed"
    assert check_parameters({"n": 1}, [{"x": "{{ $vars.Z }}"}]) == {"Z"}


def test_egress_kind_and_snake():
    assert egress_kind("DataTable") == "none" and egress_kind("HttpRequest") == "http" and egress_kind("Postgres") == "postgres"
    assert egress_kind("Slack") == "opaque"
    assert snake("HttpRequest") == "http_request" and snake("DataTable") == "data_table" and snake("SendEmail") == "send_email"


def test_validate_hosts():
    assert validate_hosts(None) is None
    assert validate_hosts(["API.Stripe.com", "*.x.io", "h:8080"]) == ["api.stripe.com", "*.x.io", "h:8080"]
    for bad in ("a.com", ["http://a.com"], ["a b"], [1], ["a.com/path"], ["*"] * 0 + ["**.a"]):
        with pytest.raises(ProjectError) as e:
            validate_hosts(bad)
        assert e.value.code == "invalid_allowed_hosts"


def test_fingerprint_and_secret_crypto(monkeypatch):
    from cryptography.fernet import Fernet
    from src.core.config import settings
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    f1 = fingerprint("v1")
    assert f1 == fingerprint("v1") and f1 != fingerprint("v2") and "v1" not in f1 and len(f1) == 64
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    assert fingerprint("v1") != f1  # keyed
    from src.services.variable_crypto import decrypt_secret, encrypt_secret
    enc = encrypt_secret("hunter2-secret")
    assert enc != "hunter2-secret" and decrypt_secret(enc) == "hunter2-secret"
    assert decrypt_secret("legacy-plaintext") == "legacy-plaintext"  # legacy rows pass through
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    assert decrypt_secret(enc) == enc  # wrong key never raises
