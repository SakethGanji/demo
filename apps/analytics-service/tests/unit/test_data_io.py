"""Unit tests — column/sheet normalization, schema fingerprints, describe_parquet."""

from __future__ import annotations

import duckdb

from app.shared.data_io import (
    build_sheet_schema,
    describe_parquet,
    manifest_fingerprint,
    normalize_column_names,
    normalize_sheet_key,
    normalize_sheet_keys,
    schema_fingerprint,
)


# --- normalize_column_names --------------------------------------------------

def test_duplicate_headers_get_numeric_suffixes():
    assert normalize_column_names(["Amount", "Amount", "Amount"]) == [
        "amount", "amount_2", "amount_3"]


def test_blank_and_synthetic_headers_become_positional():
    assert normalize_column_names([None, "", "Unnamed: 2", "column3"]) == [
        "column_0", "column_1", "column_2", "column_3"]


def test_headers_are_snake_cased():
    assert normalize_column_names(["Market Value ($)", "CUSIP-Number"]) == [
        "market_value", "cusip_number"]


def test_normalization_collision_with_generated_name():
    # A real header that normalizes into an already-taken name gets suffixed.
    assert normalize_column_names(["a b", "A-B"]) == ["a_b", "a_b_2"]


def test_non_string_headers_are_stringified():
    assert normalize_column_names([2024, 2025.5]) == ["2024", "2025_5"]


# --- sheet keys --------------------------------------------------------------

def test_sheet_key_collapses_symbol_runs():
    assert normalize_sheet_key("Q 1") == "q_1"
    assert normalize_sheet_key("Q-1") == "q_1"
    assert normalize_sheet_key("  Revenue (EU) ") == "revenue_eu"


def test_sheet_key_empty_falls_back():
    assert normalize_sheet_key("!!!") == "sheet"


def test_sheet_key_collisions_get_deterministic_suffixes():
    assert normalize_sheet_keys(["Q 1", "Q-1", "Q.1"]) == ["q_1", "q_1_2", "q_1_3"]


# --- schema capture ----------------------------------------------------------

DESCRIBED = [("Amount", "BIGINT", "YES"), ("Amount.1", "BIGINT", "YES"),
             ("Unnamed: 2", "VARCHAR", "YES"), ("Region", "VARCHAR", "NO")]
ORIGINALS = ["Amount", "Amount", None, "Region"]


def test_build_sheet_schema_flags_and_names():
    cols, fp = build_sheet_schema(DESCRIBED, ORIGINALS)
    by_norm = {c["normalized_name"]: c for c in cols}
    assert set(by_norm) == {"amount", "amount_2", "column_2", "region"}
    assert by_norm["amount"]["header_was_duplicated"]
    assert by_norm["amount_2"]["header_was_duplicated"]
    assert by_norm["amount_2"]["name"] == "Amount.1"  # physical name kept
    assert by_norm["column_2"]["generated_name"]
    assert not by_norm["region"]["header_was_duplicated"]
    assert by_norm["region"]["nullable"] is False
    assert fp


def test_fingerprint_is_stable_and_shape_sensitive():
    _, fp1 = build_sheet_schema(DESCRIBED, ORIGINALS)
    _, fp2 = build_sheet_schema(DESCRIBED, list(ORIGINALS))
    assert fp1 == fp2  # same shape → same fingerprint

    retyped = [(n, "DOUBLE" if t == "BIGINT" else t, nl) for n, t, nl in DESCRIBED]
    _, fp3 = build_sheet_schema(retyped, ORIGINALS)
    assert fp3 != fp1  # dtype change → different fingerprint

    reordered = [DESCRIBED[3], *DESCRIBED[:3]]
    orig_reordered = [ORIGINALS[3], *ORIGINALS[:3]]
    _, fp4 = build_sheet_schema(reordered, orig_reordered)
    assert fp4 != fp1  # order matters


def test_schema_fingerprint_ignores_physical_names():
    cols = [{"normalized_name": "a", "dtype": "BIGINT", "nullable": True}]
    same = [{"normalized_name": "a", "dtype": "BIGINT", "nullable": True,
             "name": "totally different physical"}]
    assert schema_fingerprint(cols) == schema_fingerprint(same)


def test_build_sheet_schema_falls_back_to_physical_names():
    # original_names of the wrong length are ignored.
    cols, _ = build_sheet_schema(DESCRIBED, ["only-one"])
    assert [c["original_name"] for c in cols] == [
        "Amount", "Amount.1", "Unnamed: 2", "Region"]


# --- manifest fingerprint ----------------------------------------------------

def test_manifest_fingerprint_order_independent_input_canonical_output():
    rows = [
        {"sheet_key": "a", "sheet_index": 0, "checksum": "c1"},
        {"sheet_key": "b", "sheet_index": 1, "checksum": "c2"},
    ]
    assert manifest_fingerprint(rows) == manifest_fingerprint(list(reversed(rows)))
    assert manifest_fingerprint(rows) != manifest_fingerprint([
        {**rows[0], "checksum": "changed"}, rows[1]])


def test_manifest_fingerprint_none_when_incomplete():
    assert manifest_fingerprint([]) is None
    assert manifest_fingerprint([{"sheet_key": "a", "sheet_index": 0,
                                  "checksum": None}]) is None


# --- describe_parquet --------------------------------------------------------

def test_describe_parquet_roundtrip(tmp_path):
    p = tmp_path / "t.parquet"
    duckdb.connect().execute(
        f"COPY (SELECT 1 AS id, 'x' AS label) TO '{p}' (FORMAT PARQUET)")
    rows = describe_parquet(str(p))
    assert [(r[0], r[1]) for r in rows] == [("id", "INTEGER"), ("label", "VARCHAR")]
