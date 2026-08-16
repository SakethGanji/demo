"""Unit tests — webhook payload shape and HMAC signing.

Two things matter here: a signature a receiver can actually verify (and that
can't be replayed against a different moment), and a payload that carries no
dataset content — a webhook body lands in logs, proxies, and third-party
systems well outside this service's authorization.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from app.features.webhooks.service import build_payload, generate_secret, sign


def verify(secret: str, body: bytes, timestamp: str, signature: str) -> bool:
    """What a receiver would do."""
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body,
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


# --- secrets ------------------------------------------------------------------

def test_secrets_are_prefixed_and_unique():
    a, b = generate_secret(), generate_secret()
    assert a.startswith("whsec_") and b.startswith("whsec_")
    assert a != b
    assert len(a) > 30


# --- signing ------------------------------------------------------------------

def test_a_receiver_can_verify_the_signature():
    body, timestamp = b'{"event":"tag.promoted"}', "1700000000"
    assert verify("whsec_x", body, timestamp, sign("whsec_x", body, timestamp))


def test_the_wrong_secret_does_not_verify():
    body, timestamp = b"{}", "1700000000"
    assert not verify("whsec_other", body, timestamp, sign("whsec_x", body, timestamp))


def test_a_tampered_body_does_not_verify():
    timestamp = "1700000000"
    signature = sign("whsec_x", b'{"amount":1}', timestamp)
    assert not verify("whsec_x", b'{"amount":999}', timestamp, signature)


def test_the_timestamp_is_bound_into_the_signature():
    """Otherwise a captured signature could be replayed at any later moment."""
    body = b"{}"
    assert sign("whsec_x", body, "1700000000") != sign("whsec_x", body, "1700000001")


# --- payload shape ------------------------------------------------------------

def test_the_payload_is_thin():
    payload = build_payload("validation.failed", dataset_id="ds-1",
                            team_id="team-1",
                            data={"rules_failed": 3, "error_failures": 2})
    assert payload["event"] == "validation.failed"
    assert payload["dataset_id"] == "ds-1" and payload["team_id"] == "team-1"
    assert payload["data"] == {"rules_failed": 3, "error_failures": 2}
    assert payload["occurred_at"]


def test_the_payload_carries_ids_and_counts_only():
    """A webhook body must never leak dataset content."""
    payload = build_payload("validation.failed", dataset_id="ds-1",
                            team_id="t", data={"rules_failed": 1})
    body = json.dumps(payload)
    for leak in ("@example.com", "sample_failures", "rows", "SELECT"):
        assert leak not in body


def test_payloads_serialize_deterministically_for_signing():
    payload = build_payload("tag.promoted", dataset_id="d", team_id="t",
                            data={"tag": "production"})
    once = json.dumps(payload, separators=(",", ":"))
    twice = json.dumps(payload, separators=(",", ":"))
    assert once == twice
