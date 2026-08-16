"""Rule-based insight engine over persisted profile JSON (§8).

Deterministic rules only — pure functions over ProfileResponse-shaped dicts
(this sheet's profile, optionally the previous version's), unit-testable
without Postgres. Each insight: rule, severity, column_name, message,
evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

NULL_RATE_WARNING_PCT = 50.0
NULL_RATE_SPIKE_PCT = 20.0
CORRELATION_THRESHOLD = 0.95

# Tukey's rule: values beyond 1.5×IQR from the quartiles are outliers. The
# profiler already computes q25/q75, so this costs no extra scan of the data.
IQR_MULTIPLIER = 1.5
# A distribution so skewed that the fence sits inside the observed range on one
# side is worth flagging; a couple of stragglers in a large column is not.
OUTLIER_RATE_WARNING_PCT = 1.0


def _insight(rule: str, severity: str, message: str, *,
             column: str | None = None, **evidence: Any) -> dict:
    return {"rule": rule, "severity": severity, "column_name": column,
            "message": message, "evidence": evidence}


def compute_insights(
    profile: dict,
    *,
    prev_profile: dict | None = None,
    is_new_sheet: bool = False,
    sheet_name: str = "",
    now: datetime | None = None,
) -> list[dict]:
    """Every insight the rules find in *profile* (vs *prev_profile* when given)."""
    now = now or datetime.now(timezone.utc)
    out: list[dict] = []
    row_count = profile.get("row_count") or 0
    prev_cols = {c["name"]: c for c in (prev_profile or {}).get("columns") or []}

    if is_new_sheet:
        out.append(_insight(
            "new-sheet", "info",
            f"Sheet '{sheet_name}' is new in this version",
            sheet=sheet_name))

    dup = profile.get("duplicate_row_count") or 0
    if dup > 0:
        out.append(_insight(
            "duplicate-rows", "info",
            f"{dup} fully duplicated row(s)",
            duplicate_row_count=dup, row_count=row_count))

    for col in profile.get("columns") or []:
        name = col["name"]
        nulls = col.get("null_count") or 0
        null_pct = col.get("null_percent") or 0.0
        unique = col.get("unique_count") or 0

        if row_count > 1 and nulls == 0 and unique == row_count:
            out.append(_insight(
                "likely-primary-key", "info",
                f"'{name}' is unique and never null — a key candidate",
                column=name, unique_count=unique, row_count=row_count))

        if row_count > 1 and unique <= 1:
            out.append(_insight(
                "constant-column", "warning",
                f"'{name}' has a single value across all rows",
                column=name,
                value=(col.get("top_values") or [{}])[0].get("value")))

        outlier = detect_outlier_bounds(col)
        if outlier is not None:
            low, high, side = outlier
            out.append(_insight(
                "numeric-outliers", "info",
                f"'{name}' has values outside the expected range "
                f"[{low:g}, {high:g}] ({side})",
                column=name, lower_fence=low, upper_fence=high, side=side,
                q25=col.get("q25"), q75=col.get("q75"),
                min=col.get("min"), max=col.get("max")))

        if null_pct >= NULL_RATE_WARNING_PCT:
            out.append(_insight(
                "high-null-rate", "warning",
                f"'{name}' is {null_pct:g}% null",
                column=name, null_percent=null_pct))

        prev = prev_cols.get(name)
        if prev is not None:
            prev_pct = prev.get("null_percent") or 0.0
            if null_pct - prev_pct >= NULL_RATE_SPIKE_PCT:
                out.append(_insight(
                    "null-rate-spike", "warning",
                    f"'{name}' null rate jumped {prev_pct:g}% → {null_pct:g}%",
                    column=name, previous_null_percent=prev_pct,
                    null_percent=null_pct))
            if col.get("dtype") == "categorical" and prev.get("dtype") == "categorical":
                seen = {tv["value"] for tv in col.get("top_values") or []}
                prev_seen = {tv["value"] for tv in prev.get("top_values") or []}
                added = sorted(str(v) for v in seen - prev_seen)
                if added:
                    out.append(_insight(
                        "new-categories", "info",
                        f"'{name}' has categories not seen in the previous "
                        f"version: {', '.join(added[:5])}",
                        column=name, added=added[:20]))

        if col.get("dtype") == "datetime" and col.get("max_date"):
            try:
                max_dt = datetime.fromisoformat(str(col["max_date"]))
                if max_dt.tzinfo is None:
                    max_dt = max_dt.replace(tzinfo=timezone.utc)
                if max_dt > now:
                    out.append(_insight(
                        "future-timestamps", "warning",
                        f"'{name}' contains timestamps in the future "
                        f"(max {col['max_date']})",
                        column=name, max_date=str(col["max_date"])))
            except ValueError:
                pass

    correlations = profile.get("correlations") or {}
    reported: set[tuple[str, str]] = set()
    for a, row in correlations.items():
        for b, corr in (row or {}).items():
            pair = tuple(sorted((a, b)))
            if a == b or pair in reported or corr is None:
                continue
            if abs(corr) >= CORRELATION_THRESHOLD:
                reported.add(pair)
                out.append(_insight(
                    "high-correlation", "info",
                    f"'{pair[0]}' and '{pair[1]}' are highly correlated "
                    f"(r={corr:.3f})",
                    column=pair[0], other_column=pair[1],
                    correlation=round(float(corr), 6)))
    return out


def detect_outlier_bounds(column: dict) -> tuple[float, float, str] | None:
    """Tukey fences for a numeric column, when its extremes fall outside them.

    Returns ``(lower_fence, upper_fence, side)`` or None. Uses the quartiles the
    profiler already computed, so no second pass over the data is needed.

    The insight deliberately reports **fences and which side is breached, never
    the outlying values themselves** — evidence is control-plane state stored in
    Postgres, and dataset cell values do not belong there. A caller who wants
    the actual rows filters on the fence via the explorer.
    """
    if column.get("dtype") != "numeric":
        return None
    q25, q75 = column.get("q25"), column.get("q75")
    low_value, high_value = column.get("min"), column.get("max")
    if None in (q25, q75, low_value, high_value):
        return None

    iqr = q75 - q25
    if iqr <= 0:
        return None  # a degenerate distribution has no meaningful fence

    lower = q25 - IQR_MULTIPLIER * iqr
    upper = q75 + IQR_MULTIPLIER * iqr
    below, above = low_value < lower, high_value > upper
    if not (below or above):
        return None
    side = "both ends" if below and above else ("low end" if below else "high end")
    return lower, upper, side
