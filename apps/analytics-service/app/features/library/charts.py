"""Shape tabular results into chart-ready series.

A `chart_definitions` row stores which saved definition or view to render plus
an opaque frontend config — it deliberately owns no query logic. This module is
the missing half: it takes the rows that definition produced and turns them
into `{categories, series}`, so the API can actually answer "render this chart"
instead of handing back config the caller has to interpret.

Everything here is pure — rows in, series out — so the interesting behaviour
(field inference, series pivoting, alignment against a shared category axis) is
unit-testable without a dataset.

Field selection is inferred when the config doesn't say, because a chart that
renders sensibly out of the box is worth more than one that requires a
correctly-filled config before it shows anything.
"""

from __future__ import annotations

from typing import Any, NamedTuple

# A chart with more categories than this is unreadable and the payload is
# large; callers get the first N and a `truncated` flag rather than a timeout.
MAX_CATEGORIES = 1000


class ChartData(NamedTuple):
    categories: list[str]
    series: list[dict[str, Any]]
    x_field: str | None
    y_fields: list[str]
    series_field: str | None
    truncated: bool


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def numeric_columns(columns: list[str], rows: list[dict]) -> list[str]:
    """Columns whose non-null values are all numeric.

    Judged from the rows rather than a declared dtype so this works for
    aggregation and pivot output, whose columns are computed.
    """
    out: list[str] = []
    for column in columns:
        seen = False
        for row in rows:
            value = row.get(column)
            if value is None:
                continue
            if not _is_number(value):
                seen = False
                break
            seen = True
        if seen:
            out.append(column)
    return out


def infer_fields(columns: list[str], rows: list[dict],
                 config: dict | None = None) -> tuple[str | None, list[str], str | None]:
    """Resolve (x_field, y_fields, series_field) from config, filling the gaps.

    Config wins wherever it is set; anything it omits is inferred so a chart
    saved with an empty config still renders. Unknown field names are ignored
    rather than raising — a stale config should degrade, not break the chart.
    """
    config = config or {}
    known = set(columns)

    x_field = config.get("x_field") or config.get("x")
    if x_field not in known:
        x_field = None

    series_field = config.get("series_field") or config.get("series")
    if series_field not in known:
        series_field = None

    y_fields = config.get("y_fields") or config.get("y") or []
    if isinstance(y_fields, str):
        y_fields = [y_fields]
    y_fields = [y for y in y_fields if y in known]

    numeric = numeric_columns(columns, rows)
    if x_field is None:
        # The first non-numeric column is almost always the dimension; fall
        # back to the first column for an all-numeric result.
        non_numeric = [c for c in columns if c not in numeric]
        x_field = non_numeric[0] if non_numeric else (columns[0] if columns else None)

    if not y_fields:
        y_fields = [c for c in numeric if c not in (x_field, series_field)]

    return x_field, y_fields, series_field


def build_series(rows: list[dict], columns: list[str],
                 config: dict | None = None) -> ChartData:
    """Turn result rows into aligned categories and series.

    Two shapes are produced, depending on whether a `series_field` is in play:

    * **Wide** (no series field) — one series per y-field, indexed by category.
    * **Long** (series field set) — one series per distinct value of that
      field, which is what a stacked bar or a multi-line chart needs.

    Every series is aligned to the same category list and padded with None for
    missing combinations, so a consumer can zip them positionally.
    """
    x_field, y_fields, series_field = infer_fields(columns, rows, config)
    if x_field is None or not rows:
        return ChartData([], [], x_field, y_fields, series_field, False)

    categories: list[str] = []
    seen: set[str] = set()
    for row in rows:
        label = "" if row.get(x_field) is None else str(row[x_field])
        if label not in seen:
            seen.add(label)
            categories.append(label)

    truncated = len(categories) > MAX_CATEGORIES
    if truncated:
        categories = categories[:MAX_CATEGORIES]
    index = {label: i for i, label in enumerate(categories)}

    if series_field:
        value_field = y_fields[0] if y_fields else None
        if value_field is None:
            return ChartData(categories, [], x_field, y_fields, series_field, truncated)
        buckets: dict[str, list[Any]] = {}
        for row in rows:
            label = "" if row.get(x_field) is None else str(row[x_field])
            position = index.get(label)
            if position is None:
                continue
            name = "" if row.get(series_field) is None else str(row[series_field])
            data = buckets.setdefault(name, [None] * len(categories))
            data[position] = row.get(value_field)
        series = [{"name": name, "data": data}
                  for name, data in sorted(buckets.items())]
        return ChartData(categories, series, x_field, y_fields, series_field, truncated)

    series = []
    for field in y_fields:
        data: list[Any] = [None] * len(categories)
        for row in rows:
            label = "" if row.get(x_field) is None else str(row[x_field])
            position = index.get(label)
            if position is not None:
                data[position] = row.get(field)
        series.append({"name": field, "data": data})
    return ChartData(categories, series, x_field, y_fields, series_field, truncated)
