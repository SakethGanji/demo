"""Pure probe builders and scoring for relationship discovery (§22) and joins (§23).

Everything here is either a SQL *builder* (returns a string, touches no
connection) or a pure scorer over already-measured numbers, so the interesting
logic is unit-testable without Postgres, storage, or a fixture workbook.

Two families:

* **§22 discovery** — score a candidate column pair from four signals:
  name similarity, type compatibility, value overlap (how much of the child's
  key space exists in the parent), and target uniqueness (a near-unique target
  is the PK side, which is what orients the edge).
* **§23 join warnings** — measure what a join would actually do before running
  it: duplicate keys per side, many-to-many, output-row expansion, unmatched
  key percentages, and column-name collisions.

NULL keys are excluded from every probe: SQL NULLs never join, so counting them
would overstate both overlap and expansion.
"""

from __future__ import annotations

from typing import NamedTuple

from app.shared.utils.sql import quote_ident

# --- §22 scoring --------------------------------------------------------------

# Weights over the three measured signals (type compatibility is a gate, not a
# score). Tuned so that a strong name match alone cannot clear the threshold —
# real evidence from the data has to carry it.
NAME_WEIGHT = 0.25
COVERAGE_WEIGHT = 0.45
UNIQUENESS_WEIGHT = 0.30
SUGGESTION_THRESHOLD = 0.6

# Two signals act as FLOORS rather than weights, because no amount of the
# others should be able to outvote them:
#   * a target whose values repeat is not a key — that pairing is a
#     many-to-many, not a reference, however well the names line up;
#   * an edge whose child keys mostly do NOT exist upstream is noise.
MIN_TARGET_UNIQUENESS = 0.9
MIN_COVERAGE = 0.5

# Column families that can meaningfully be compared. Joining a number to a
# string is almost always a modelling accident, so the gate rejects it.
_NUMERIC = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT",
            "USMALLINT", "UINTEGER", "UBIGINT", "DECIMAL", "NUMERIC", "DOUBLE",
            "FLOAT", "REAL")
_TEXT = ("VARCHAR", "CHAR", "TEXT", "STRING", "UUID")
_TEMPORAL = ("DATE", "TIMESTAMP", "TIME")


def type_family(dtype: str) -> str:
    """Coarse family of a DuckDB type: numeric | text | temporal | other."""
    base = (dtype or "").upper().split("(")[0].strip()
    if base.startswith(_NUMERIC):
        return "numeric"
    if base.startswith(_TEXT):
        return "text"
    if base.startswith(_TEMPORAL):
        return "temporal"
    return "other"


def types_compatible(from_dtype: str, to_dtype: str) -> bool:
    """Whether two columns are plausibly joinable (the gate before any I/O)."""
    a, b = type_family(from_dtype), type_family(to_dtype)
    if "other" in (a, b):
        return False
    return a == b


def name_score(from_column: str, to_column: str, to_sheet: str) -> float:
    """How strongly two column names suggest a reference, in [0, 1].

    Recognizes the three conventions that actually appear in workbooks: the
    same name on both sides, an ``x_id`` column pointing at ``id``, and a
    ``{sheet}_id`` column pointing at that sheet's key.
    """
    a, b = from_column.lower(), to_column.lower()
    sheet = (to_sheet or "").lower().rstrip("s")
    if a == b:
        return 1.0
    # customer_id -> id on the Customers sheet
    if b == "id" and a.endswith("_id") and sheet and a[:-3].rstrip("s") == sheet:
        return 1.0
    if a == f"{sheet}_id" or a == f"{sheet}id":
        return 0.9
    # Same stem, both key-shaped: customer_id -> customer_ref_id
    if a.endswith("_id") and b.endswith("_id") and a[:-3].rstrip("s") == b[:-3].rstrip("s"):
        return 0.8
    if a.endswith("_id") and b == "id":
        return 0.5
    return 0.0


def confidence(*, name: float, coverage: float, uniqueness: float) -> float:
    """Weighted confidence for a candidate edge, rounded to 4 places."""
    score = (NAME_WEIGHT * name + COVERAGE_WEIGHT * coverage
             + UNIQUENESS_WEIGHT * uniqueness)
    return round(min(1.0, max(0.0, score)), 4)


class PairSignals(NamedTuple):
    """The measured evidence behind one candidate relationship."""

    coverage: float     # share of child key values that exist in the parent
    uniqueness: float   # share of parent key values that are unique
    child_distinct: int
    parent_distinct: int
    matched_distinct: int


def coverage_sql(child_rel: str, child_col: str,
                 parent_rel: str, parent_col: str) -> str:
    """Distinct child keys, and how many of them exist in the parent.

    NULLs are excluded on both sides — a NULL key matches nothing, so counting
    it would understate coverage for a legitimately optional foreign key.
    """
    child, parent = quote_ident(child_col), quote_ident(parent_col)
    return (
        f"SELECT COUNT(*) AS child_distinct, "
        f"       COUNT(*) FILTER (WHERE matched) AS matched_distinct "
        f"FROM (SELECT DISTINCT c.{child} AS k, "
        f"             EXISTS (SELECT 1 FROM {parent_rel} p "
        f"                     WHERE p.{parent} = c.{child}) AS matched "
        f"      FROM {child_rel} c WHERE c.{child} IS NOT NULL) s"
    )


def uniqueness_sql(relation: str, column: str) -> str:
    """Distinct non-NULL values and total non-NULL rows for a candidate target.

    A target whose values are (near-)unique is the PK side, which is what
    orients an edge from child to parent.
    """
    col = quote_ident(column)
    return (f"SELECT COUNT(DISTINCT {col}) AS distinct_count, "
            f"COUNT({col}) AS non_null_count FROM {relation}")


def score_pair(signals: PairSignals, *, name: float) -> float:
    """Confidence for a pair from its measured signals."""
    return confidence(name=name, coverage=signals.coverage,
                      uniqueness=signals.uniqueness)


def qualifies(score: float, signals: PairSignals) -> bool:
    """Whether a scored pair is worth proposing to a human.

    The weighted score is necessary but not sufficient — see the floors above.
    """
    return (score >= SUGGESTION_THRESHOLD
            and signals.uniqueness >= MIN_TARGET_UNIQUENESS
            and signals.coverage >= MIN_COVERAGE)


def ratio(numerator: float, denominator: float) -> float:
    """Safe ratio — an empty side scores 0 rather than dividing by zero."""
    return round(numerator / denominator, 4) if denominator else 0.0


# --- §23 join warnings --------------------------------------------------------

def duplicate_keys_sql(relation: str, column: str) -> str:
    """How many key values repeat, and how many rows they account for."""
    col = quote_ident(column)
    return (f"SELECT COUNT(*) AS dup_keys, COALESCE(SUM(n), 0) AS dup_rows "
            f"FROM (SELECT {col}, COUNT(*) AS n FROM {relation} "
            f"      WHERE {col} IS NOT NULL GROUP BY {col} HAVING COUNT(*) > 1) d")


def expansion_sql(left_rel: str, left_col: str,
                  right_rel: str, right_col: str, how: str) -> str:
    """Exact output-row count of the join, without materializing it.

    Per-key multiplicities multiply, so summing ``lc * rc`` over matched keys is
    the precise inner-join size. A LEFT join additionally keeps every left row
    that matched nothing — including rows with a NULL key, which can never match.
    """
    left, right = quote_ident(left_col), quote_ident(right_col)
    matched = (
        f"SELECT COALESCE(SUM(l.n * r.n), 0) AS rows "
        f"FROM (SELECT {left} AS k, COUNT(*) AS n FROM {left_rel} "
        f"      WHERE {left} IS NOT NULL GROUP BY {left}) l "
        f"JOIN (SELECT {right} AS k, COUNT(*) AS n FROM {right_rel} "
        f"      WHERE {right} IS NOT NULL GROUP BY {right}) r ON r.k = l.k"
    )
    if how != "left":
        return matched
    unmatched = (
        f"SELECT COUNT(*) FROM {left_rel} l "
        f"WHERE l.{left} IS NULL OR NOT EXISTS "
        f"      (SELECT 1 FROM {right_rel} r WHERE r.{right} = l.{left})"
    )
    return f"SELECT (({matched}) + ({unmatched})) AS rows"


def unmatched_sql(source_rel: str, source_col: str,
                  other_rel: str, other_col: str) -> str:
    """Rows on one side whose key finds no partner (NULL keys count as unmatched)."""
    src, other = quote_ident(source_col), quote_ident(other_col)
    return (f"SELECT COUNT(*) AS total, "
            f"       COUNT(*) FILTER (WHERE s.{src} IS NULL OR NOT EXISTS "
            f"           (SELECT 1 FROM {other_rel} o WHERE o.{other} = s.{src})"
            f"       ) AS unmatched "
            f"FROM {source_rel} s")


def column_collisions(left_columns: list[str], right_columns: list[str],
                      left_key: str, right_key: str) -> list[str]:
    """Non-key column names present on both sides.

    These are the names a join has to disambiguate; surfacing them up front is
    the difference between a guided join and a surprising one.
    """
    keys = {left_key, right_key}
    right_set = set(right_columns)
    return sorted({c for c in left_columns if c in right_set and c not in keys})


class JoinShape(NamedTuple):
    """Per-side duplicate-key measurements, used to classify a join's cardinality."""

    left_dup_keys: int
    right_dup_keys: int


def is_many_to_many(shape: JoinShape) -> bool:
    """A join is many-to-many when BOTH sides repeat their key."""
    return shape.left_dup_keys > 0 and shape.right_dup_keys > 0


def expansion_factor(estimated_rows: int, left_rows: int) -> float:
    """Output rows per input left row — the number that says 'this will blow up'."""
    return ratio(estimated_rows, left_rows)
