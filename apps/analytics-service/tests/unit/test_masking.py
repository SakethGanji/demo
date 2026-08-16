"""Unit tests — column masking driven by the data dictionary.

The masks keep enough shape to stay explorable (you can still tell two rows
apart, and still recognise what kind of column it is) without revealing the
value. NULL stays NULL, because hiding whether a value is missing would distort
null-rate reasoning without protecting anything.
"""

from __future__ import annotations

from app.shared.masking import (
    MASK,
    digest,
    is_sensitive,
    mask_rows,
    mask_value,
)


# --- which levels count -------------------------------------------------------

def test_declared_sensitive_levels():
    for level in ("confidential", "restricted", "pii", "PII", "  Secret  "):
        assert is_sensitive(level), level


def test_ordinary_levels_are_not_sensitive():
    for level in ("public", "internal", None, "", "unknown"):
        assert not is_sensitive(level)


# --- value masking ------------------------------------------------------------

def test_emails_keep_their_shape():
    """A reader can still tell it's an email column and tell rows apart."""
    assert mask_value("ana@example.com", "email") == "a***@***.com"
    assert mask_value("bob@other.org", "email") == "b***@***.org"


def test_emails_are_detected_without_a_declared_semantic_type():
    assert mask_value("ana@example.com") == "a***@***.com"


def test_identifier_like_values_keep_a_short_tail():
    assert mask_value("4111111111111234", "identifier") == f"{MASK}34"
    assert mask_value("+1-555-0100", "phone") == f"{MASK}00"


def test_short_identifiers_do_not_leak_themselves():
    assert mask_value("7", "identifier") == MASK


def test_everything_else_collapses():
    assert mask_value("Ada Lovelace", "person_name") == MASK
    assert mask_value(123456, None) == MASK


def test_null_stays_null():
    """Masking whether a value is missing would distort null-rate reasoning."""
    assert mask_value(None, "email") is None


def test_masking_is_not_reversible():
    masked = {mask_value(f"user{i}@example.com", "email") for i in range(50)}
    assert masked == {"u***@***.com"}      # all collapse; no per-value leak


# --- row masking --------------------------------------------------------------

ROWS = [
    {"id": 1, "email": "ana@example.com", "amount": 10.0},
    {"id": 2, "email": "bob@example.com", "amount": 20.0},
]


def test_only_named_columns_are_masked():
    out = mask_rows(ROWS, {"email": "email"})
    assert out[0]["email"] == "a***@***.com"
    assert out[0]["id"] == 1 and out[0]["amount"] == 10.0


def test_masking_does_not_mutate_the_input():
    mask_rows(ROWS, {"email": "email"})
    assert ROWS[0]["email"] == "ana@example.com"


def test_no_masked_columns_is_a_passthrough():
    assert mask_rows(ROWS, {}) is ROWS


def test_a_masked_column_missing_from_a_row_is_not_added():
    out = mask_rows([{"id": 1}], {"email": "email"})
    assert "email" not in out[0]


# --- pseudonyms ---------------------------------------------------------------

def test_digest_is_stable_and_non_reversible():
    assert digest("ana@example.com") == digest("ana@example.com")
    assert digest("ana@example.com") != digest("bob@example.com")
    assert "ana" not in (digest("ana@example.com") or "")


def test_digest_preserves_null():
    assert digest(None) is None
