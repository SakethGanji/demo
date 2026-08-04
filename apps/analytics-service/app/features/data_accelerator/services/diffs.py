"""Version diffs — workbook-level (sheets) and sheet-level (columns).

Both diffs run entirely on captured metadata in Postgres; sheets ingested
before schema capture existed get their schema filled lazily (and persisted)
on first diff. Sheets are matched across versions by ``sheet_key`` (normalized
name); renames are only ever *suggested*, never declared.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.shared.datasets import (
    _find_sheet,
    ensure_sheet_schema,
    get_version_sheet_rows,
    resolve_version,
)

from ..schemas import (
    ColumnNullabilityChange,
    ColumnOrderChange,
    ColumnTypeChange,
    ModifiedSheet,
    RenameCandidate,
    SheetColumn,
    SheetDiffResponse,
    WorkbookDiffResponse,
)
from .datasets import _sheet_summary


async def _sheets_with_schemas(dataset_id: str, version_number: int) -> tuple[dict, dict[str, dict]]:
    """(version row, sheet rows keyed by sheet_key) with schemas ensured."""
    ver = await resolve_version(dataset_id, version_number=version_number)
    rows = await get_version_sheet_rows(ver)
    by_key: dict[str, dict] = {}
    for r in rows:
        by_key[r["sheet_key"]] = await ensure_sheet_schema(ver, r)
    return ver, by_key


async def workbook_diff(dataset_id: str, from_version: int, to_version: int) -> WorkbookDiffResponse:
    _, sheets_a = await _sheets_with_schemas(dataset_id, from_version)
    _, sheets_b = await _sheets_with_schemas(dataset_id, to_version)

    added_keys = [k for k in sheets_b if k not in sheets_a]
    removed_keys = [k for k in sheets_a if k not in sheets_b]

    modified: list[ModifiedSheet] = []
    unchanged: list[str] = []
    for key in sheets_a:
        if key not in sheets_b:
            continue
        a, b = sheets_a[key], sheets_b[key]
        schema_changed = (
            a.get("schema_fingerprint") != b.get("schema_fingerprint")
        )
        delta = (
            b["row_count"] - a["row_count"]
            if a.get("row_count") is not None and b.get("row_count") is not None
            else None
        )
        visibility_changed = a.get("visibility", "visible") != b.get("visibility", "visible")
        if schema_changed or (delta not in (None, 0)) or visibility_changed:
            modified.append(ModifiedSheet(
                sheet_key=key,
                from_sheet=a["sheet_name"],
                to_sheet=b["sheet_name"],
                schema_changed=schema_changed,
                row_count_delta=delta,
                visibility_changed=visibility_changed,
            ))
        else:
            unchanged.append(b["sheet_name"])

    # Rename suggestions: a removed and an added sheet with the same schema
    # fingerprint probably moved. Advisory only — the caller decides.
    candidates: list[RenameCandidate] = []
    for rk in removed_keys:
        a = sheets_a[rk]
        if not a.get("schema_fingerprint"):
            continue
        for ak in added_keys:
            b = sheets_b[ak]
            if a["schema_fingerprint"] != b.get("schema_fingerprint"):
                continue
            same_rows = (
                a.get("row_count") is not None and a.get("row_count") == b.get("row_count")
            )
            candidates.append(RenameCandidate(
                from_sheet=a["sheet_name"],
                to_sheet=b["sheet_name"],
                confidence="high" if same_rows else "medium",
                reason=(
                    "identical schema fingerprint and row count"
                    if same_rows else "identical schema fingerprint"
                ),
            ))

    return WorkbookDiffResponse(
        dataset_id=dataset_id,
        from_version=from_version,
        to_version=to_version,
        added=[_sheet_summary(sheets_b[k]) for k in added_keys],
        removed=[_sheet_summary(sheets_a[k]) for k in removed_keys],
        modified=modified,
        unchanged=unchanged,
        rename_candidates=candidates,
    )


async def sheet_diff(
    dataset_id: str, from_version: int, sheet: str, to_version: int,
) -> SheetDiffResponse:
    ver_a = await resolve_version(dataset_id, version_number=from_version)
    rows_a = await get_version_sheet_rows(ver_a)
    a = _find_sheet(rows_a, sheet)
    if not a:
        available = ", ".join(r["sheet_name"] for r in rows_a) or "none"
        raise HTTPException(
            404, f"Sheet not found in version {from_version}: {sheet} (available: {available})",
        )

    ver_b = await resolve_version(dataset_id, version_number=to_version)
    rows_b = await get_version_sheet_rows(ver_b)
    b = next((r for r in rows_b if r["sheet_key"] == a["sheet_key"]), None)
    if not b:
        raise HTTPException(
            404, f"Sheet '{a['sheet_name']}' not present in version {to_version}",
        )

    a = await ensure_sheet_schema(ver_a, a)
    b = await ensure_sheet_schema(ver_b, b)
    cols_a = {c["normalized_name"]: c for c in a.get("schema_json") or []}
    cols_b = {c["normalized_name"]: c for c in b.get("schema_json") or []}

    added = [SheetColumn(**c) for n, c in cols_b.items() if n not in cols_a]
    removed = [SheetColumn(**c) for n, c in cols_a.items() if n not in cols_b]

    type_changes: list[ColumnTypeChange] = []
    nullability_changes: list[ColumnNullabilityChange] = []
    common = [n for n in cols_a if n in cols_b]
    for n in common:
        ca, cb = cols_a[n], cols_b[n]
        if ca["dtype"] != cb["dtype"]:
            type_changes.append(ColumnTypeChange(
                column=n, from_dtype=ca["dtype"], to_dtype=cb["dtype"],
            ))
        if bool(ca.get("nullable", True)) != bool(cb.get("nullable", True)):
            nullability_changes.append(ColumnNullabilityChange(
                column=n, from_nullable=bool(ca.get("nullable", True)),
                to_nullable=bool(cb.get("nullable", True)),
            ))

    # Order: compare the relative rank of common columns so pure adds/removes
    # don't flag everything downstream of them as "moved".
    seq_a = sorted(common, key=lambda n: cols_a[n]["position"])
    seq_b = sorted(common, key=lambda n: cols_b[n]["position"])
    order_changes = [
        ColumnOrderChange(
            column=n,
            from_position=cols_a[n]["position"],
            to_position=cols_b[n]["position"],
        )
        for i, n in enumerate(seq_a)
        if seq_b[i] != n
    ]

    delta = (
        b["row_count"] - a["row_count"]
        if a.get("row_count") is not None and b.get("row_count") is not None
        else None
    )
    identical = (
        a.get("schema_fingerprint") == b.get("schema_fingerprint")
        and a.get("schema_fingerprint") is not None
        and delta in (None, 0)
    )

    return SheetDiffResponse(
        dataset_id=dataset_id,
        sheet_key=a["sheet_key"],
        from_sheet=a["sheet_name"],
        to_sheet=b["sheet_name"],
        from_version=from_version,
        to_version=to_version,
        identical=identical,
        added_columns=added,
        removed_columns=removed,
        type_changes=type_changes,
        nullability_changes=nullability_changes,
        order_changes=order_changes,
        from_row_count=a.get("row_count"),
        to_row_count=b.get("row_count"),
        row_count_delta=delta,
    )
