"""Stage 8 T1 (tester): can real data or a real secret get past the template lints? (DESIGN-STAGE8-REUSE 8.2 rule 5, R6)

Each case plants one thing in a valid bundle (the ``rollback-on-publish`` fixture) in a place a hurried author might put it, and
expects the validator to report it. The controls show the lint works for the plain string case; the xfail cases are places where
it does not look. No database."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.services.template_bundle import luhn_ok, validate_bundle

ENGINE = Path(__file__).resolve().parents[2]
BASE = ENGINE / "tests" / "fixtures" / "templates_broken" / "rollback-on-publish"
API_TEXT = (BASE / "apis" / "svc.yaml").read_text()
REAL_PAN = "4532015112830366"                       # a Luhn-valid number that is not in the reserved 4000 00 range
FAKE_OK = next(f"400000123456789{d}" for d in range(10) if luhn_ok(f"400000123456789{d}"))
REAL_IBAN = "DE89370400440532013000"
STRIPE_LIVE = "sk_live_4eC39HqLyjWDarjtT1zdp7dc"


def base() -> dict:
    return json.loads((BASE / "template.json").read_text())


def lint(raw: dict, api_text: str = API_TEXT) -> set[str]:
    _bundle, issues = validate_bundle(raw, {"svc": api_text}, expected_slug=raw["slug"])
    return {i.code for i in issues}


def with_node_param(raw: dict, value) -> dict:
    out = copy.deepcopy(raw)
    mark = next(n for w in out["workflows"] for n in w["definition"]["nodes"] if n["name"] == "Mark")
    mark["parameters"]["jsonData"]["planted"] = value
    return out


def with_seed(raw: dict, value) -> dict:
    out = copy.deepcopy(raw)
    out["tables"][0]["seed_rows"][0]["k"] = value
    return out


def test_the_base_bundle_is_clean():
    assert lint(base()) == set()


# ── controls: the plain string cases ────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("planted", [REAL_PAN, "4532 0151 1283 0366", "4532-0151-1283-0366", f"card: {REAL_PAN}.", REAL_IBAN,
                                    "DE89 3704 0044 0532 0130 00", "Bearer abcdefghijklmnopqrstuvwx", "sk-abcdefghijklmnopqrstuv",
                                    "AKIAABCDEFGHIJKLMNOP", "ghp_abcdefghijklmnopqrstuvwxyz0123", "postgres://admin:hunter22@db.example/x"])
def test_a_planted_string_is_caught_in_a_node_parameter_and_in_a_seed_row(planted):
    assert "TPL_SECRET_VALUE" in lint(with_node_param(base(), planted))
    assert "TPL_SECRET_VALUE" in lint(with_seed(base(), planted))


def test_the_reserved_test_card_range_is_allowed():
    assert lint(with_node_param(base(), FAKE_OK)) == set()


@pytest.mark.parametrize("email", ["real.person@gmail.com", "ops@bank.com", "a@example.com.evil.net", "x@sub.example.com"])
def test_a_real_looking_email_is_refused(email):
    assert "TPL_FAKE_DATA" in lint(with_node_param(base(), email))


def test_a_planted_value_in_a_test_mock_or_a_test_input_is_caught():
    for where in ("mocks", "input"):
        raw = base()
        t = raw["workflows"][0]["definition"]["tests"][0]
        if where == "mocks":
            t["mocks"] = {"Mark": {"items": [{"card": REAL_PAN}]}}
        else:
            t["input"]["items"][0]["body"] = {"card": REAL_PAN}
        assert "TPL_SECRET_VALUE" in lint(raw), where


def test_a_planted_value_in_the_few_shot_is_caught():
    raw = base()
    raw["few_shot"] = {"request": f"Refund card {REAL_PAN}", "spec": {"steps": []}}
    assert "TPL_SECRET_VALUE" in lint(raw)


# ── formerly blind spots (S8-8, fixed) ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_card_number_stored_as_a_json_number_in_a_seed_row_is_caught():
    raw = base()
    raw["tables"][0]["columns"] = [{"name": "k", "type": "number", "required": True}]
    assert "TPL_SECRET_VALUE" in lint(with_seed(raw, int(REAL_PAN)))


def test_a_card_number_stored_as_a_json_number_in_a_test_input_is_caught():
    raw = base()
    raw["workflows"][0]["definition"]["tests"][0]["input"]["items"][0]["body"] = {"card": int(REAL_PAN)}
    assert "TPL_SECRET_VALUE" in lint(raw)


def test_a_secret_in_the_bundled_api_file_is_caught():
    leaked = API_TEXT.replace("openapi:", "# Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789\nopenapi:", 1)
    assert "TPL_SECRET_VALUE" in lint(base(), leaked)


def test_a_real_email_in_the_bundled_api_file_is_caught():
    leaked = API_TEXT.replace("openapi:", "# contact: jane.doe@realbank.com\nopenapi:", 1)
    assert "TPL_FAKE_DATA" in lint(base(), leaked)


def test_a_lower_case_iban_is_caught():
    assert "TPL_SECRET_VALUE" in lint(with_node_param(base(), REAL_IBAN.lower()))


def test_a_live_payment_key_with_underscores_is_caught():
    assert "TPL_SECRET_VALUE" in lint(with_node_param(base(), STRIPE_LIVE))


def test_a_basic_auth_header_value_is_caught():
    assert "TPL_SECRET_VALUE" in lint(with_node_param(base(), "Authorization: Basic dXNlcjpwYXNzd29yZDEyMzQ1Ng=="))
