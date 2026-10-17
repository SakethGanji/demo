"""Pure-logic coverage: slug rules, $vars substitution, secret redaction (incl. encoded variants)."""

from __future__ import annotations

import base64
import json
from urllib.parse import quote, quote_plus

import pytest

from src.services.slug_utils import RESERVED_SLUGS, SlugValidationError, normalize_slug, validate_slug
from src.services.vars_substitution import expand_secret_variants, redact_deep, redact_secrets, substitute_vars


@pytest.mark.parametrize("slug", ["abc", "a-b-c", "shop-2", "a" * 63, "x1y"])
def test_validate_slug_accepts(slug):
    validate_slug(slug)


@pytest.mark.parametrize("slug", ["ab", "a" * 64, "Abc", "a--b", "-abc", "abc-", "a_b", "a b", "ünï", ""])
def test_validate_slug_rejects_format(slug):
    with pytest.raises(SlugValidationError):
        validate_slug(slug)


@pytest.mark.parametrize("slug", sorted(RESERVED_SLUGS - {"_assets", "_unlock"}))
def test_validate_slug_rejects_reserved(slug):
    if len(slug) < 3:
        pytest.skip("too short anyway")
    with pytest.raises(SlugValidationError, match="reserved"):
        validate_slug(slug)


@pytest.mark.parametrize("name", ["Hello World!", "  Spaced   Out  ", "Ünï Cödé 2", "a", "", "!!!", "x" * 200, "API", "My--App__v2"])
def test_normalize_slug_always_yields_a_valid_slug_or_reserved(name):
    s = normalize_slug(name)
    assert 3 <= len(s) <= 63
    try:
        validate_slug(s)
    except SlugValidationError as e:  # only "reserved" is an allowed outcome (e.g. "API" -> "api"); caller checks it
        assert "reserved" in str(e), (name, s, e)


def test_normalize_slug_basic_shape():
    assert normalize_slug("Hello World!") == "hello-world"
    assert normalize_slug("My--App__v2") == "my-app-v2"


def test_substitute_vars_walks_json_and_leaves_unknown_and_other_expressions():
    payload = {"h": "Bearer {{ $vars.TOK }}", "n": [1, "{{$vars.A}}-{{ $vars.A }}", {"k": "{{ $json.x }}"}],
               "unknown": "{{ $vars.NOPE }}", "num": 7, "none": None}
    out = substitute_vars(payload, {"TOK": "t0k", "A": "a"})
    assert out == {"h": "Bearer t0k", "n": [1, "a-a", {"k": "{{ $json.x }}"}], "unknown": "{{ $vars.NOPE }}", "num": 7, "none": None}
    assert payload["h"].startswith("Bearer {{")  # input is not mutated
    assert substitute_vars(payload, {}) is payload


def test_substitute_vars_does_not_re_expand_substituted_values():
    assert substitute_vars("{{ $vars.A }}", {"A": "{{ $vars.B }}", "B": "boom"}) == "{{ $vars.B }}"


def test_redact_secrets_min_len_and_multiple():
    assert redact_secrets("a=abcd b=xy", {"abcd", "xy"}) == "a=*** b=xy"  # 'xy' < 4 chars is not masked
    assert redact_secrets("", {"abcd"}) == ""
    assert redact_secrets("abcd", set()) == "abcd"


def test_expand_variants_cover_common_encodings():
    v = "p@ss w/rd+=\"q\""
    got = expand_secret_variants({v})
    assert v in got
    assert base64.b64encode(v.encode()).decode() in got
    assert base64.b64encode(v.encode()).decode().rstrip("=") in got
    assert base64.urlsafe_b64encode(v.encode()).decode().rstrip("=") in got
    assert quote(v, safe="") in got and quote_plus(v) in got
    assert json.dumps(v)[1:-1] in got
    assert expand_secret_variants({"abc", "", None}) == set()  # too short / empty ignored


def test_redact_deep_masks_values_keys_and_encodings_and_prefers_longest():
    secret = "sk-live-1234567890"
    basic = base64.b64encode(f"user:{secret}".encode()).decode()
    body = {"echo": f"token={secret}", "hdr": {"Authorization": f"Basic {base64.b64encode(secret.encode()).decode()}"},
            "list": [secret, 3, True, None], secret: "keyed", "url": f"/x?t={quote(secret + '/+')}", "tup": (secret,)}
    out = redact_deep(body, {secret})
    dumped = json.dumps(out)
    assert secret not in dumped and base64.b64encode(secret.encode()).decode() not in dumped
    assert out["list"] == ["***", 3, True, None]
    assert out["tup"] == ["***"]
    assert "***" in out and out["***"] == "keyed"
    assert basic  # user:secret base64 differs from the secret-only encoding: documented limitation (not masked)
    assert redact_deep(body, set()) is body


def test_redact_deep_longest_first_masks_nested_secret_fully():
    out = redact_deep("abcd-efgh-ijkl", {"abcd-efgh", "efgh"})
    assert out == "***-ijkl"
