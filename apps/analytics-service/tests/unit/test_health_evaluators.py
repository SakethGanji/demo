"""§17 pure health evaluators — threshold and evidence pinning, no I/O."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.features.discovery.health import (
    evaluate_documentation,
    evaluate_drift,
    evaluate_duplicates,
    evaluate_freshness,
    evaluate_missing,
    evaluate_schema_stability,
    evaluate_validation,
    split_profiled_pairs,
)

NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)


def _sheet_row(version, fingerprint, lsid="ls-1", name="data"):
    return {"version_number": version, "logical_sheet_id": lsid,
            "sheet_name": name, "schema_fingerprint": fingerprint}


def _profile(columns=None, row_count=10, dup=0):
    return {"columns": columns or [], "row_count": row_count,
            "duplicate_row_count": dup}


def _pair(version, profile, lsid="ls-1", name="data", run_id="r1"):
    return {"run_id": run_id, "logical_sheet_id": lsid, "sheet_name": name,
            "profile": profile, "version_number": version}


# --- schema stability ---------------------------------------------------------

def test_schema_stability_flags_fingerprint_change():
    d = evaluate_schema_stability([
        _sheet_row(1, "aaa"), _sheet_row(2, "aaa"), _sheet_row(3, "bbb")])
    assert d.status == "warning"
    assert d.evidence["changes"] == [
        {"sheet": "data", "from_version": 2, "to_version": 3}]


def test_schema_stability_ok_and_unknown():
    assert evaluate_schema_stability([]).status == "unknown"
    d = evaluate_schema_stability([_sheet_row(1, "aaa"), _sheet_row(2, "aaa")])
    assert d.status == "ok" and d.evidence["versions_considered"] == [1, 2]


def test_schema_stability_tracks_sheets_independently():
    d = evaluate_schema_stability([
        _sheet_row(1, "aaa", lsid="a", name="A"),
        _sheet_row(1, "xxx", lsid="b", name="B"),   # different sheets, same version
        _sheet_row(2, "aaa", lsid="a", name="A"),
        _sheet_row(2, "yyy", lsid="b", name="B"),
    ])
    assert d.status == "warning" and len(d.evidence["changes"]) == 1
    assert d.evidence["changes"][0]["sheet"] == "B"


# --- validation ---------------------------------------------------------------

def test_validation_statuses():
    assert evaluate_validation(None, 3).status == "unknown"
    run = {"id": "run-1", "rules_total": 4, "rules_passed": 4, "rules_failed": 0,
           "error_failures": 0, "warning_failures": 0, "completed_at": "t"}
    assert evaluate_validation(run, 3).status == "ok"
    assert evaluate_validation({**run, "warning_failures": 1}, 3).status == "warning"
    d = evaluate_validation({**run, "error_failures": 2, "rules_failed": 2}, 3)
    assert d.status == "attention" and d.evidence["run_id"] == "run-1"


# --- missing + duplicates -----------------------------------------------------

def test_missing_thresholds_and_worst_evidence():
    assert evaluate_missing([]).status == "unknown"
    cols = [{"name": "a", "null_percent": 3.0}, {"name": "b", "null_percent": 0.0}]
    assert evaluate_missing([_pair(2, _profile(cols))]).status == "ok"
    cols[0]["null_percent"] = 12.0
    d = evaluate_missing([_pair(2, _profile(cols))])
    assert d.status == "warning" and d.evidence["worst"]["column"] == "a"
    cols[0]["null_percent"] = 25.0
    assert evaluate_missing([_pair(2, _profile(cols))]).status == "attention"


def test_duplicates_ratio_thresholds():
    assert evaluate_duplicates([]).status == "unknown"
    assert evaluate_duplicates([_pair(1, _profile(row_count=100))]).status == "ok"
    d = evaluate_duplicates([_pair(1, _profile(row_count=100, dup=2))])
    assert d.status == "warning"
    d = evaluate_duplicates([_pair(1, _profile(row_count=100, dup=5))])
    assert d.status == "attention"


# --- drift ----------------------------------------------------------------------

def test_split_pairs_and_drift():
    old = _profile([{"name": "amt", "null_percent": 0.0, "dtype": "numeric"}],
                   row_count=4)
    new = _profile([{"name": "amt", "null_percent": 20.0, "dtype": "numeric"}],
                   row_count=5)
    latest, drifts = split_profiled_pairs(
        [_pair(2, new, run_id="r2"), _pair(1, old, run_id="r1")])
    assert [e["version_number"] for e in latest] == [2]
    assert drifts[0]["from_version"] == 1 and drifts[0]["to_version"] == 2

    d = evaluate_drift(drifts)
    assert d.status == "warning"
    assert d.evidence["notable"][0]["columns"] == ["amt"]
    assert d.evidence["compared"] == [
        {"sheet": "data", "from_version": 1, "to_version": 2}]


def test_drift_ok_when_deltas_small_and_unknown_without_baseline():
    assert evaluate_drift([]).status == "unknown"
    p = _profile([{"name": "amt", "null_percent": 1.0, "dtype": "numeric"}])
    p2 = _profile([{"name": "amt", "null_percent": 2.0, "dtype": "numeric"}])
    _, drifts = split_profiled_pairs([_pair(2, p2), _pair(1, p)])
    assert evaluate_drift(drifts).status == "ok"


# --- freshness ------------------------------------------------------------------

def test_freshness_thresholds():
    assert evaluate_freshness(None, "daily", version_number=None).status == "attention"
    fresh = NOW - timedelta(hours=6)
    d = evaluate_freshness(fresh, "daily", version_number=3, now=NOW)
    assert d.status == "ok" and d.evidence["version_number"] == 3
    stale = NOW - timedelta(days=1, hours=6)
    assert evaluate_freshness(stale, "daily", version_number=3,
                              now=NOW).status == "warning"
    dead = NOW - timedelta(days=3)
    assert evaluate_freshness(dead, "daily", version_number=3,
                              now=NOW).status == "attention"
    assert evaluate_freshness(dead, "monthly", version_number=3,
                              now=NOW).status == "ok"


def test_freshness_unknown_without_recognized_frequency():
    assert evaluate_freshness(NOW, None, version_number=1, now=NOW).status == "unknown"
    assert evaluate_freshness(NOW, "ad-hoc", version_number=1,
                              now=NOW).status == "unknown"


# --- documentation ---------------------------------------------------------------

def test_documentation_buckets():
    full = evaluate_documentation(True, True, total_sheets=2, documented_sheets=2,
                                  total_columns=10, documented_columns=5)
    assert full.status == "ok" and full.evidence["bucket"] == "full"

    partial = evaluate_documentation(True, False, total_sheets=2, documented_sheets=2,
                                     total_columns=10, documented_columns=10)
    assert partial.status == "warning" and partial.evidence["bucket"] == "partial"
    assert "domain" in partial.summary

    none = evaluate_documentation(False, False, total_sheets=2, documented_sheets=0,
                                  total_columns=10, documented_columns=0)
    assert none.status == "attention" and none.evidence["bucket"] == "none"

    low_cols = evaluate_documentation(True, True, total_sheets=1, documented_sheets=1,
                                      total_columns=10, documented_columns=4)
    assert low_cols.evidence["bucket"] == "partial"
