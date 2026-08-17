"""Keyed row-level diff between two versions of a sheet.

Schema diff answers "what columns changed" and profile drift answers "how did
the distribution move". Neither answers the question a reviewer actually asks
about a new version: **which rows changed, and to what?** This module does.

Rows are matched on a key (the sheet's declared primary key by default), and
every row falls into exactly one bucket:

* **added** — the key is present in the new version only
* **removed** — present in the old version only
* **changed** — present in both, and at least one compared column differs
* **unchanged** — present in both, all compared columns equal

The full result is materialized in one uniform long format —
``(change_type, row_key, column_name, before_value, after_value)`` — because a
cell-level shape describes all three buckets without a special case: an added
row is every cell going NULL → value, a removed row is every cell going value
→ NULL, and a changed row is just the differing cells. Values are cast to
VARCHAR so a single parquet holds a diff across mixed column types.

Everything here is a pure SQL builder over two registered relations, so the
interesting logic is unit-testable against in-memory DuckDB.

NULL handling uses ``IS DISTINCT FROM`` throughout: NULL → 5 is a change, and
NULL → NULL is not. Plain ``<>`` would silently classify both as unchanged.

A column may also change TYPE between the two versions — which is precisely
what a diff is for — so every comparison goes through :func:`_side`, which
pins a disagreeing pair to a type both versions can reach. See
:func:`reconcile_types` for why the engine, not this module, picks it.
"""

from __future__ import annotations

from typing import NamedTuple

from app.shared.utils.sql import quote_ident


class DiffCounts(NamedTuple):
    added: int
    removed: int
    changed: int
    unchanged: int

    @property
    def total_changes(self) -> int:
        return self.added + self.removed + self.changed


def _side(alias: str, column: str, casts: dict[str, str] | None) -> str:
    """One side of a comparison, reconciled when the two versions disagree.

    Absent an entry in *casts* this is the bare column reference, so a column
    typed the same in both versions is compared exactly as it was before this
    reconciliation existed.
    """
    ref = f"{alias}.{quote_ident(column)}"
    target = (casts or {}).get(column)
    return f"CAST({ref} AS {target})" if target else ref


def key_expr(alias: str, key_columns: list[str]) -> str:
    """A single comparable key expression for one side.

    Composite keys are concatenated with a unit separator that cannot appear
    in a rendered value, so ('a','bc') and ('ab','c') stay distinct.
    """
    parts = [f"COALESCE(CAST({alias}.{quote_ident(c)} AS VARCHAR), '')"
             for c in key_columns]
    return parts[0] if len(parts) == 1 else f"CONCAT_WS(CHR(31), {', '.join(parts)})"


def join_condition(left: str, right: str, key_columns: list[str],
                   casts: dict[str, str] | None = None) -> str:
    """Key equality that treats two NULL key parts as equal.

    A plain ``=`` drops rows whose key contains a NULL, which would silently
    report them as both added and removed.
    """
    return " AND ".join(
        f"{_side(left, c, casts)} IS NOT DISTINCT FROM {_side(right, c, casts)}"
        for c in key_columns)


def changed_predicate(left: str, right: str, compare_columns: list[str],
                      casts: dict[str, str] | None = None) -> str:
    """True when any compared column differs between the two sides."""
    if not compare_columns:
        return "FALSE"
    return " OR ".join(
        f"{_side(left, c, casts)} IS DISTINCT FROM {_side(right, c, casts)}"
        for c in compare_columns)


def _present(relation: str, sentinel: str) -> str:
    """*relation* wrapped so an anti-join can test presence, not key NULL-ness.

    An anti-join written ``LEFT JOIN other o ON ... WHERE o.<key> IS NULL``
    looks right but answers the wrong question: it is also true for a row that
    DID match, when the matched row's key column is itself NULL. Since
    :func:`join_condition` deliberately matches NULL to NULL, such a row is
    reported as both added and removed while the counts (which already use
    this sentinel) correctly call it matched — the endpoint contradicts itself.

    The sentinel column is constant and non-NULL, so it is NULL after the join
    exactly when the side did not match.
    """
    return f"(SELECT *, 1 AS {sentinel} FROM {relation})"


def counts_sql(left: str, right: str, key_columns: list[str],
               compare_columns: list[str],
               casts: dict[str, str] | None = None) -> str:
    """One pass producing all four bucket counts.

    A FULL OUTER JOIN on the key classifies every row exactly once — which is
    what makes the buckets provably disjoint rather than four independent
    queries that might disagree.
    """
    on = join_condition("l", "r", key_columns, casts)
    # Presence is decided by the join, not by NULL-ness of a key part — hence
    # the `_l`/`_r` sentinels, which are non-NULL exactly when that side matched.
    changed = changed_predicate("l", "r", compare_columns, casts)
    return f"""
        SELECT
            COUNT(*) FILTER (WHERE _l IS NULL AND _r IS NOT NULL) AS added,
            COUNT(*) FILTER (WHERE _l IS NOT NULL AND _r IS NULL) AS removed,
            COUNT(*) FILTER (WHERE _l IS NOT NULL AND _r IS NOT NULL
                             AND ({changed})) AS changed,
            COUNT(*) FILTER (WHERE _l IS NOT NULL AND _r IS NOT NULL
                             AND NOT ({changed})) AS unchanged
        FROM (SELECT *, 1 AS _l FROM {left}) l
        FULL OUTER JOIN (SELECT *, 1 AS _r FROM {right}) r ON {on}
    """


def column_change_counts_sql(left: str, right: str, key_columns: list[str],
                             compare_columns: list[str],
                             casts: dict[str, str] | None = None) -> str:
    """How many matched rows changed, per column — the "what moved" summary."""
    on = join_condition("l", "r", key_columns, casts)
    if not compare_columns:
        return "SELECT NULL AS column_name, 0 AS changed_rows WHERE FALSE"
    per_column = " UNION ALL ".join(
        f"SELECT '{c.replace(chr(39), chr(39) * 2)}' AS column_name, "
        f"COUNT(*) FILTER (WHERE {_side('l', c, casts)} IS DISTINCT FROM "
        f"{_side('r', c, casts)}) AS changed_rows "
        f"FROM {left} l JOIN {right} r ON {on}"
        for c in compare_columns)
    return (f"SELECT column_name, changed_rows FROM ({per_column}) c "
            f"WHERE changed_rows > 0 ORDER BY changed_rows DESC, column_name")


def cell_changes_sql(left: str, right: str, key_columns: list[str],
                     compare_columns: list[str], all_columns: list[str],
                     casts: dict[str, str] | None = None) -> str:
    """The full diff in long cell-level form.

    One uniform shape covers all three buckets — added rows are NULL → value
    across every column, removed rows are value → NULL, changed rows are only
    the differing cells.

    Only the WHERE predicate is reconciled; ``before_value``/``after_value``
    are still rendered from the stored column, so a reader sees what each
    version actually holds rather than a value coerced for the comparison.
    """
    on = join_condition("l", "r", key_columns, casts)
    lkey, rkey = key_expr("l", key_columns), key_expr("r", key_columns)
    # Presence must be decided by the join, never by NULL-ness of a key part —
    # see `_present` for why.
    lp, rp = _present(left, "_l"), _present(right, "_r")

    changed_parts = " UNION ALL ".join(
        f"SELECT 'changed' AS change_type, {lkey} AS row_key, "
        f"'{c.replace(chr(39), chr(39) * 2)}' AS column_name, "
        f"CAST(l.{quote_ident(c)} AS VARCHAR) AS before_value, "
        f"CAST(r.{quote_ident(c)} AS VARCHAR) AS after_value "
        f"FROM {left} l JOIN {right} r ON {on} "
        f"WHERE {_side('l', c, casts)} IS DISTINCT FROM {_side('r', c, casts)}"
        for c in compare_columns) if compare_columns else None

    added_parts = " UNION ALL ".join(
        f"SELECT 'added', {rkey}, '{c.replace(chr(39), chr(39) * 2)}', "
        f"NULL, CAST(r.{quote_ident(c)} AS VARCHAR) "
        f"FROM {right} r LEFT JOIN {lp} l ON {on} "
        f"WHERE l._l IS NULL"
        for c in all_columns)

    removed_parts = " UNION ALL ".join(
        f"SELECT 'removed', {lkey}, '{c.replace(chr(39), chr(39) * 2)}', "
        f"CAST(l.{quote_ident(c)} AS VARCHAR), NULL "
        f"FROM {left} l LEFT JOIN {rp} r ON {on} "
        f"WHERE r._r IS NULL"
        for c in all_columns)

    blocks = [b for b in (changed_parts, added_parts, removed_parts) if b]
    return " UNION ALL ".join(blocks)


def sample_rows_sql(source: str, other: str, key_columns: list[str],
                    limit: int, casts: dict[str, str] | None = None) -> str:
    """Full rows present on one side only — the added/removed samples."""
    on = join_condition("s", "o", key_columns, casts)
    return (f"SELECT s.* FROM {source} s LEFT JOIN {_present(other, '_o')} o ON {on} "
            f"WHERE o._o IS NULL LIMIT {int(limit)}")


def duplicate_keys_sql(relation: str, key_columns: list[str],
                       casts: dict[str, str] | None = None) -> str:
    """Rows whose key repeats — the diff is only trustworthy if this is zero.

    A duplicated key means the join fans out and a row could be reported as
    both added and removed, so callers check this first and refuse rather than
    return a plausible-looking wrong answer.

    Uniqueness is checked on the same reconciled key the join will use: if a
    type change collapses two distinct keys into one, the join fans out just
    the same, and a check on the native type would not see it.
    """
    cols = ", ".join(_side("k", c, casts) for c in key_columns)
    return (f"SELECT COUNT(*) FROM (SELECT {cols} FROM {relation} k "
            f"GROUP BY {cols} HAVING COUNT(*) > 1) d")


# ---------------------------------------------------------------------------
# Orchestration — everything above this line is pure and unit-tested
# ---------------------------------------------------------------------------

MAX_SAMPLE_ROWS = 50


def column_types(conn, relation: str) -> dict[str, str]:
    """Column → physical type, as DuckDB itself reports it for *relation*.

    The stored schema_json is a description of the version; this is what the
    generated SQL will actually be bound against, so the comparison is
    reconciled from the parquet's own types.
    """
    return {r[0]: r[1] for r in conn.execute(f"DESCRIBE {relation}").fetchall()}


def reconcile_types(conn, left_types: dict[str, str], right_types: dict[str, str],
                    columns: list[str]) -> dict[str, str]:
    """Column → the type both sides are cast to before being compared.

    A column whose type changed between versions is the single most
    interesting thing a diff can report — the schema diff already treats it as
    a first-class outcome. But DuckDB resolves ``BIGINT IS DISTINCT FROM
    VARCHAR`` by casting the text back to a number, which throws on the first
    non-numeric value and takes the whole endpoint down with it. So a
    disagreeing pair is pinned to a type both sides can reach.

    DuckDB picks that type, not us: ``COALESCE`` binds exactly when the two
    types have a lossless common supertype, which is also the condition under
    which comparing them is runtime-safe. So a numeric widening (BIGINT →
    DOUBLE) keeps comparing as numbers and reports no spurious row changes,
    and only a pair with no common type falls back to comparing as text.

    Comparing as text is the honest answer for that fallback: it says two
    values are the same when they render the same, which is what a reviewer
    means by "this row didn't change" when a column's storage type moved.
    Refusing instead would leave the row diff unable to answer on the exact
    input its sibling endpoint handles.

    Columns whose types agree are absent from the result, so they are compared
    with no cast at all.
    """
    casts: dict[str, str] = {}
    for c in columns:
        a, b = left_types.get(c), right_types.get(c)
        if a is None or b is None or a == b:
            continue
        casts[c] = _common_type(conn, a, b) or "VARCHAR"
    return casts


def _common_type(conn, a: str, b: str) -> str | None:
    """The lossless supertype of *a* and *b*, or None when there isn't one.

    Both arguments come from DuckDB's own DESCRIBE, never from a caller, so
    interpolating them is not a widening of the query surface.
    """
    import duckdb

    try:
        return conn.execute(
            f"SELECT typeof(COALESCE(CAST(NULL AS {a}), CAST(NULL AS {b})))"
        ).fetchone()[0]
    except duckdb.Error:
        return None


async def run_row_diff(ds: dict, from_version: int, to_version: int,
                       sheet: str | None, *, key_columns: list[str] | None,
                       compare_columns: list[str] | None, sample_limit: int,
                       principal) -> dict:
    """Diff two versions of a sheet row by row.

    The key defaults to the sheet's declared primary key (§17 data dictionary),
    which is the whole reason that metadata is worth capturing. A duplicated
    key makes the join fan out, so it is refused up front rather than answered
    with plausible-looking nonsense.
    """
    import hashlib

    from app.api.errors import ProblemException
    from app.features.discovery import repo as discovery_repo
    from app.features.library import repo as library_repo
    from app.infra.db.storage import ArtifactLayout, get_storage
    from app.shared.data_io import load_data
    from app.shared.datasets import sheet_data_path
    from app.shared.utils.sql import safe_value

    from .diffs import _sheets_with_schemas
    from .sampling import _persist_table, _physical_key

    dataset_id = str(ds["id"])
    layout = ArtifactLayout("diff_output", team_id=str(ds["team_id"]),
                            dataset_id=dataset_id)
    from_ver, from_sheets = await _sheets_with_schemas(dataset_id, from_version)
    to_ver, to_sheets = await _sheets_with_schemas(dataset_id, to_version)

    sheet_key, left_row, right_row = _resolve_sheet_pair(sheet, from_sheets, to_sheets)

    keys = await _resolve_keys(dataset_id, sheet_key, left_row, key_columns)
    left_cols = {c["name"] for c in left_row["schema_json"] or []}
    right_cols = {c["name"] for c in right_row["schema_json"] or []}

    phys_keys = [_physical_key(left_row, k) for k in keys]
    missing = [k for k, p in zip(keys, phys_keys)
               if p not in left_cols or p not in right_cols]
    if missing:
        raise ProblemException(
            400, f"Key column(s) not present in both versions: {missing}",
            code="unknown-column", columns=missing,
            available=sorted(left_cols & right_cols))

    # Only columns in BOTH versions can be compared; added/removed columns are
    # a schema change, which the schema diff already reports.
    common = sorted((left_cols & right_cols) - set(phys_keys))
    if compare_columns is not None:
        requested = [_physical_key(left_row, c) for c in compare_columns]
        unknown = [c for c in requested if c not in common]
        if unknown:
            raise ProblemException(
                400, f"Cannot compare column(s): {unknown}",
                code="unknown-column", columns=unknown, available=common)
        common = requested
    all_common = sorted(set(phys_keys) | set(common))

    conn = load_data(file_path=sheet_data_path(from_ver, left_row))
    try:
        for alias, ver, row in (("diff_a", from_ver, left_row),
                                ("diff_b", to_ver, right_row)):
            escaped = str(sheet_data_path(ver, row)).replace("'", "''")
            conn.execute(
                f"CREATE VIEW {alias} AS SELECT * FROM read_parquet('{escaped}')")

        # A column (including a key column) may be typed differently in the two
        # versions; every comparison below is reconciled so it answers a
        # question instead of raising a conversion error. See `reconcile_types`.
        # Resolved before the uniqueness check, which must see the same key the
        # join will.
        casts = reconcile_types(conn, column_types(conn, "diff_a"),
                                column_types(conn, "diff_b"), all_common)

        for alias, label in (("diff_a", from_version), ("diff_b", to_version)):
            dupes = conn.execute(
                duplicate_keys_sql(alias, phys_keys, casts)).fetchone()[0]
            if dupes:
                raise ProblemException(
                    409,
                    f"Key {keys} is not unique in version {label} "
                    f"({dupes} duplicated value(s)) — a row diff needs a unique key",
                    code="ambiguous-diff-key", version_number=label,
                    duplicate_keys=int(dupes), key=keys)

        counts = DiffCounts(*conn.execute(
            counts_sql("diff_a", "diff_b", phys_keys, common, casts)).fetchone())
        per_column = [
            {"column": c, "changed_rows": int(n)} for c, n in
            conn.execute(column_change_counts_sql(
                "diff_a", "diff_b", phys_keys, common, casts)).fetchall()]

        added = _rows(conn, sample_rows_sql("diff_b", "diff_a", phys_keys,
                                            sample_limit, casts))
        removed = _rows(conn, sample_rows_sql("diff_a", "diff_b", phys_keys,
                                              sample_limit, casts))
        changed = _rows(conn, (
            f"SELECT * FROM ({cell_changes_sql('diff_a', 'diff_b', phys_keys, common, all_common, casts)}) x "
            f"WHERE change_type = 'changed' LIMIT {int(sample_limit)}")) if common else []

        sample_file = None
        if counts.total_changes:
            conn.execute(
                "CREATE TABLE diff_cells AS "
                + cell_changes_sql("diff_a", "diff_b", phys_keys, common,
                                   all_common, casts))
            sample_file = _persist_table(conn, "diff_cells", "diff", layout)
    finally:
        conn.close()

    artifact_id = None
    if sample_file:
        key = layout.key(sample_file)
        blob = get_storage().read_bytes(key)
        artifact = await library_repo.create_artifact(
            key, "diff_output", filename=sample_file, format="parquet",
            media_type="application/vnd.apache.parquet",
            size_bytes=len(blob), checksum=hashlib.sha256(blob).hexdigest(),
            created_by=principal.user_id, dataset_id=dataset_id,
            team_id=str(ds["team_id"]))
        artifact_id = artifact["id"]

    # The inline samples are raw dataset rows, so the same masking policy that
    # governs the explorer applies here. The persisted artifact is governed by
    # /samples authorization instead.
    from app.shared.masking import mask_rows, resolve_masking

    masked = await resolve_masking(dataset_id, left_row, principal)
    if masked:
        added = mask_rows(added, masked)
        removed = mask_rows(removed, masked)
        changed = [c for c in changed if c.get("column_name") not in masked]

    return {
        "dataset_id": dataset_id, "from_version": from_version,
        "to_version": to_version, "sheet": left_row["sheet_name"],
        "key": keys, "compared_columns": common,
        "added": counts.added, "removed": counts.removed,
        "changed": counts.changed, "unchanged": counts.unchanged,
        "column_changes": per_column,
        "added_sample": added, "removed_sample": removed,
        "changed_sample": changed,
        "diff_file": sample_file, "diff_artifact_id": artifact_id,
        "masked_columns": sorted(masked),
    }


def _match_across_versions(row: dict, rows: list[dict]) -> dict | None:
    """The row in *rows* that is the same sheet as *row* — by key, then logical.

    A confirmed rename keeps one logical sheet across two versions while giving
    each version its own sheet_key, so the frozen key wins when present and the
    logical identity is the fallback the rename preserved.
    """
    for r in rows:
        if r["sheet_key"] == row["sheet_key"]:
            return r
    lid = row.get("logical_sheet_id")
    if lid:
        for r in rows:
            if r.get("logical_sheet_id") == lid:
                return r
    return None


def _resolve_sheet_pair(sheet: str | None, from_sheets: dict,
                        to_sheets: dict) -> tuple[str, dict, dict]:
    """(left key, left row, right row) for the sheet to diff, spanning renames.

    Honours the sheet-selection contract: a named sheet resolves rename-aware on
    both sides (``_find_sheet`` takes the current-name hop), and an unnamed diff
    over versions that share more than one logical sheet asks the caller to pick.
    """
    from app.api.errors import ProblemException
    from app.shared.datasets import _find_sheet
    from fastapi import HTTPException

    from_rows = list(from_sheets.values())
    to_rows = list(to_sheets.values())

    if sheet:
        left = _find_sheet(from_rows, sheet)
        right = _find_sheet(to_rows, sheet)
        if left is not None and right is None:
            right = _match_across_versions(left, to_rows)
        elif right is not None and left is None:
            left = _match_across_versions(right, from_rows)
        if left is None and right is None:
            raise HTTPException(404, f"Sheet not found: {sheet}")
        if left is None or right is None:
            raise HTTPException(
                404, f"Sheet '{sheet}' is not present in both versions")
        return left["sheet_key"], left, right

    pairs: list[tuple[dict, dict]] = []
    for lr in from_rows:
        rr = _match_across_versions(lr, to_rows)
        if rr is not None:
            pairs.append((lr, rr))
    if len(pairs) > 1:
        raise ProblemException(
            400, f"These versions share {len(pairs)} sheets — name one via 'sheet'",
            code="sheet-selection-required",
            sheets=[lr["sheet_name"] for lr, _ in pairs])
    if not pairs:
        raise HTTPException(404, "The two versions share no sheet to diff")
    lr, rr = pairs[0]
    return lr["sheet_key"], lr, rr


async def _resolve_keys(dataset_id: str, sheet_key: str, sheet_row: dict,
                        explicit: list[str] | None) -> list[str]:
    """Explicit key, else the sheet's declared primary key, else a 400."""
    from app.api.errors import ProblemException
    from app.features.discovery import repo as discovery_repo

    if explicit:
        return explicit
    for meta in await discovery_repo.list_sheet_metadata(dataset_id):
        if meta.get("sheet_key") == sheet_key and meta.get("primary_key_columns"):
            return list(meta["primary_key_columns"])
    raise ProblemException(
        400,
        "No key to match rows on. Declare the sheet's primary key via "
        "PUT /datasets/{id}/sheet-metadata/{sheet}, or pass 'key' explicitly.",
        code="diff-key-required", sheet=sheet_row["sheet_name"])


def _rows(conn, sql: str) -> list[dict]:
    from app.shared.utils.sql import safe_value

    cur = conn.execute(sql)
    names = [d[0] for d in cur.description]
    return [{n: safe_value(v) for n, v in zip(names, row)} for row in cur.fetchall()]
