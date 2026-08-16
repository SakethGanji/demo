"""Unit tests — SQL safety helpers (no DB, no app)."""

from __future__ import annotations

import datetime

import numpy as np
import pytest
from fastapi import HTTPException

from app.shared.utils.sql import quote_ident, safe_value, sanitize_filter_expr


# --- quote_ident -------------------------------------------------------------

def test_quote_ident_wraps_in_double_quotes():
    assert quote_ident("amount") == '"amount"'


def test_quote_ident_escapes_embedded_quotes():
    # An attacker-controlled name can't break out of the quoted identifier.
    assert quote_ident('a"; DROP TABLE x; --') == '"a""; DROP TABLE x; --"'


# --- safe_value --------------------------------------------------------------

def test_safe_value_passthrough_and_none():
    assert safe_value(None) is None
    assert safe_value("x") == "x"
    assert safe_value(3) == 3


def test_safe_value_nan_and_inf_become_none():
    assert safe_value(float("nan")) is None
    assert safe_value(float("inf")) is None
    assert safe_value(np.float64("nan")) is None


def test_safe_value_numpy_scalars_become_python():
    assert safe_value(np.int64(7)) == 7 and type(safe_value(np.int64(7))) is int
    assert safe_value(np.float32(1.5)) == 1.5
    assert safe_value(np.bool_(True)) is True


def test_safe_value_datetimes_become_strings():
    d = datetime.datetime(2026, 8, 4, 12, 30)
    assert safe_value(d) == str(d)
    assert safe_value(datetime.date(2026, 8, 4)) == "2026-08-04"


# --- sanitize_filter_expr ----------------------------------------------------

def test_sanitize_allows_benign_expressions():
    for expr in ("amount > 100", "region = 'EU' AND amount < 5",
                 "updated_at IS NOT NULL", "created_by LIKE 'a%'"):
        assert sanitize_filter_expr(expr) == expr


def test_sanitize_blocks_semicolons():
    with pytest.raises(HTTPException) as e:
        sanitize_filter_expr("amount > 1; DROP TABLE datasets")
    assert e.value.status_code == 400


@pytest.mark.parametrize("kw", ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER",
                                "CREATE", "TRUNCATE", "EXEC", "EXECUTE",
                                "GRANT", "REVOKE", "UNION"])
def test_sanitize_blocks_ddl_dml_keywords_case_insensitive(kw):
    for variant in (kw, kw.lower(), kw.title()):
        with pytest.raises(HTTPException) as e:
            sanitize_filter_expr(f"amount > 1 OR {variant} x")
        assert e.value.status_code == 400, variant


def test_sanitize_keywords_match_whole_words_only():
    # Column names merely containing a keyword must stay usable.
    for expr in ("updated_at > '2026-01-01'", "created_by = 'x'",
                 "insertion_order > 3", "reunion = true"):
        assert sanitize_filter_expr(expr) == expr
