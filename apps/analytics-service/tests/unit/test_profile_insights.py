"""Unit tests for the §8 insight engine — pure functions over profile JSON."""

from __future__ import annotations

from datetime import datetime, timezone

from app.features.explorer.insights import compute_insights

NOW = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)


def _col(name, *, dtype="numeric", nulls=0, null_pct=0.0, unique=5,
         top=None, max_date=None):
    return {"name": name, "dtype": dtype, "null_count": nulls,
            "null_percent": null_pct, "unique_count": unique,
            "top_values": top or [], "max_date": max_date}


def _profile(columns, *, row_count=5, dup=0, correlations=None):
    return {"row_count": row_count, "columns": columns,
            "duplicate_row_count": dup, "correlations": correlations}


def _rules(insights):
    return {i["rule"] for i in insights}


def test_likely_primary_key_and_constant_column():
    p = _profile([
        _col("id", unique=5, nulls=0),
        _col("flag", unique=1, top=[{"value": "y", "count": 5, "percent": 100.0}]),
    ])
    ins = compute_insights(p, now=NOW)
    assert _rules(ins) == {"likely-primary-key", "constant-column"}
    pk = next(i for i in ins if i["rule"] == "likely-primary-key")
    assert pk["column_name"] == "id" and pk["severity"] == "info"
    const = next(i for i in ins if i["rule"] == "constant-column")
    assert const["evidence"]["value"] == "y" and const["severity"] == "warning"


def test_no_pk_insight_for_single_row():
    p = _profile([_col("id", unique=1)], row_count=1)
    assert compute_insights(p, now=NOW) == []


def test_high_null_rate_and_spike_vs_previous():
    prev = _profile([_col("email", nulls=0, null_pct=5.0, unique=4)])
    cur = _profile([_col("email", nulls=3, null_pct=60.0, unique=2)])
    ins = compute_insights(cur, prev_profile=prev, now=NOW)
    assert {"high-null-rate", "null-rate-spike"} <= _rules(ins)
    spike = next(i for i in ins if i["rule"] == "null-rate-spike")
    assert spike["evidence"] == {"previous_null_percent": 5.0, "null_percent": 60.0}


def test_new_categories_against_previous():
    prev = _profile([_col("tier", dtype="categorical", unique=2,
                          top=[{"value": "gold", "count": 3, "percent": 60.0},
                               {"value": "silver", "count": 2, "percent": 40.0}])])
    cur = _profile([_col("tier", dtype="categorical", unique=3,
                         top=[{"value": "gold", "count": 3, "percent": 60.0},
                              {"value": "copper", "count": 2, "percent": 40.0}])])
    ins = compute_insights(cur, prev_profile=prev, now=NOW)
    cat = next(i for i in ins if i["rule"] == "new-categories")
    assert cat["evidence"]["added"] == ["copper"]
    # Without a previous profile the rule stays silent.
    assert "new-categories" not in _rules(compute_insights(cur, now=NOW))


def test_future_timestamps():
    p = _profile([_col("event_at", dtype="datetime", unique=5,
                       max_date="2027-01-01 00:00:00")])
    ins = compute_insights(p, now=NOW)
    assert "future-timestamps" in _rules(ins)
    past = _profile([_col("event_at", dtype="datetime", unique=5,
                          max_date="2026-01-01 00:00:00")])
    assert "future-timestamps" not in _rules(compute_insights(past, now=NOW))


def test_high_correlation_reported_once_per_pair():
    p = _profile(
        [_col("a", unique=5), _col("b", unique=5)],
        correlations={"a": {"a": 1.0, "b": -0.99}, "b": {"b": 1.0, "a": -0.99}})
    ins = [i for i in compute_insights(p, now=NOW) if i["rule"] == "high-correlation"]
    assert len(ins) == 1
    assert ins[0]["evidence"]["correlation"] == -0.99
    # Self-correlation (the 1.0 diagonal) never fires.


def test_new_sheet_and_duplicate_rows():
    p = _profile([_col("id", unique=5)], dup=2)
    ins = compute_insights(p, is_new_sheet=True, sheet_name="Extras", now=NOW)
    assert {"new-sheet", "duplicate-rows"} <= _rules(ins)
    ns = next(i for i in ins if i["rule"] == "new-sheet")
    assert ns["evidence"]["sheet"] == "Extras"


# ---------------------------------------------------------------------------
# Numeric outliers (Tukey fences over the profiler's existing quartiles)
# ---------------------------------------------------------------------------

from app.features.explorer.insights import detect_outlier_bounds  # noqa: E402


def _numeric(**over):
    col = {"name": "amount", "dtype": "numeric", "q25": 10.0, "q75": 20.0,
           "min": 12.0, "max": 18.0, "null_count": 0, "null_percent": 0.0,
           "unique_count": 50, "top_values": []}
    col.update(over)
    return col


def test_a_high_outlier_is_detected():
    low, high, side = detect_outlier_bounds(_numeric(max=100.0))
    assert (low, high) == (-5.0, 35.0)      # 10 - 1.5*10, 20 + 1.5*10
    assert side == "high end"


def test_a_low_outlier_is_detected():
    assert detect_outlier_bounds(_numeric(min=-50.0))[2] == "low end"


def test_outliers_at_both_ends_are_reported_together():
    assert detect_outlier_bounds(_numeric(min=-50.0, max=100.0))[2] == "both ends"


def test_a_tight_distribution_has_no_outliers():
    assert detect_outlier_bounds(_numeric()) is None


def test_values_exactly_on_the_fence_are_not_outliers():
    assert detect_outlier_bounds(_numeric(min=-5.0, max=35.0)) is None


def test_a_zero_width_iqr_is_not_flagged():
    """Every value identical means no meaningful fence, not infinite outliers."""
    assert detect_outlier_bounds(_numeric(q25=5.0, q75=5.0, min=1.0, max=9.0)) is None


def test_non_numeric_columns_are_skipped():
    assert detect_outlier_bounds(_numeric(dtype="text")) is None


def test_missing_quartiles_are_skipped():
    assert detect_outlier_bounds(_numeric(q25=None)) is None


def test_the_insight_reports_fences_not_values():
    """Evidence lives in Postgres, so it must not carry dataset cell values."""
    from app.features.explorer.insights import compute_insights

    profile = {"row_count": 100, "duplicate_row_count": 0,
               "columns": [_numeric(max=100.0)]}
    [insight] = [i for i in compute_insights(profile)
                 if i["rule"] == "numeric-outliers"]
    assert insight["column_name"] == "amount"
    assert insight["evidence"]["upper_fence"] == 35.0
    assert insight["evidence"]["side"] == "high end"
    # The outlying values themselves are never recorded.
    assert "outlier_values" not in insight["evidence"]
    assert "examples" not in insight["evidence"]


# ---------------------------------------------------------------------------
# Correlation surfaces under masking — a coefficient is a bare float, so the
# e2e sentinel sweep is structurally blind to it; these pin the behaviour at
# the function level instead.
# ---------------------------------------------------------------------------

from app.features.explorer.insights import redact_insights  # noqa: E402
from app.shared.masking import redact_profile  # noqa: E402


def _corr_insights(insights):
    return [i for i in insights if i["rule"] == "high-correlation"]


def _sym(a, b, r):
    """A symmetric two-column matrix the way profiling.py stores one."""
    return {a: {a: 1.0, b: r}, b: {b: 1.0, a: r}}


def test_high_correlation_dropped_when_masked_column_sorts_first():
    p = _profile([_col("aaa_secret"), _col("zeta")],
                 correlations=_sym("aaa_secret", "zeta", 0.99))
    ins = compute_insights(p, now=NOW)
    assert _corr_insights(ins)  # precondition: the rule fired at all
    assert not _corr_insights(redact_insights(ins, {"aaa_secret": "confidential"}))


def test_high_correlation_dropped_when_masked_column_sorts_second():
    """The previously-leaking ordering: the gate only inspected column_name,
    which is the alphabetically-first name of the pair, so a masked column
    sorting second passed through with the full coefficient and message."""
    p = _profile([_col("alpha"), _col("mmm_secret")],
                 correlations=_sym("alpha", "mmm_secret", 0.99))
    ins = compute_insights(p, now=NOW)
    assert _corr_insights(ins)
    assert not _corr_insights(redact_insights(ins, {"mmm_secret": "confidential"}))


def test_high_correlation_between_unmasked_columns_survives_redaction():
    p = _profile([_col("alpha"), _col("zeta")],
                 correlations=_sym("alpha", "zeta", 0.99))
    ins = compute_insights(p, now=NOW)
    red = _corr_insights(redact_insights(ins, {"mmm_secret": "confidential"}))
    assert len(red) == 1
    assert red[0]["evidence"]["correlation"] == 0.99


def test_a_future_rule_cannot_leak_a_correlation_through_evidence():
    """Defence in depth: even if a rule other than high-correlation ever puts
    a coefficient in its evidence, the generic path must strip it and withhold
    the message rather than trusting the allow-list."""
    insight = {"rule": "some-new-rule", "severity": "info",
               "column_name": "mmm_secret",
               "message": "'mmm_secret' correlates (r=0.990)",
               "evidence": {"correlation": 0.99, "count": 5}}
    [red] = redact_insights([insight], {"mmm_secret": "confidential"})
    assert "correlation" not in red["evidence"]
    assert red["evidence"] == {"count": 5}
    assert "0.99" not in red["message"]


def test_redact_profile_strips_masked_columns_from_correlations():
    profile = {
        "columns": [{"name": "alpha"}, {"name": "mmm_secret"}, {"name": "zeta"}],
        "correlations": {
            "alpha": {"alpha": 1.0, "mmm_secret": 0.97, "zeta": 0.5},
            "mmm_secret": {"mmm_secret": 1.0, "alpha": 0.97, "zeta": 0.96},
            "zeta": {"zeta": 1.0, "alpha": 0.5, "mmm_secret": 0.96},
        },
    }
    out = redact_profile(profile, {"mmm_secret": "confidential"})
    corr = out["correlations"]
    # Gone as a row and as every other row's partner…
    assert "mmm_secret" not in corr
    assert all("mmm_secret" not in row for row in corr.values())
    # …while the unmasked pair is untouched (no over-redaction).
    assert corr["alpha"]["zeta"] == 0.5
    # The stored run state was rebuilt, not mutated through the shallow copy.
    assert "mmm_secret" in profile["correlations"]
    assert profile["correlations"]["alpha"]["mmm_secret"] == 0.97


def test_redact_profile_tolerates_absent_or_null_correlations():
    assert redact_profile({"columns": []}, {"x": None}) == {"columns": []}
    out = redact_profile({"columns": [], "correlations": None}, {"x": None})
    assert out["correlations"] is None
