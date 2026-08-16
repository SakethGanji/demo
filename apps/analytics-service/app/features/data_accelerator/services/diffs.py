"""Version diffs — workbook-level (sheets) and sheet-level (columns).

Both diffs run entirely on captured metadata in Postgres; sheets ingested
before schema capture existed get their schema filled lazily (and persisted)
on first diff. Sheets are matched across versions by ``sheet_key`` (normalized
name); an unconfirmed rename is only ever *suggested*, never declared. A rename
the caller already confirmed is reported as settled under ``renamed`` and drops
out of ``rename_candidates`` — otherwise the banner asks forever, because
confirm-rename relinks logical identity and never rewrites a version's
``sheet_key``.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.api.errors import ProblemException
from app.features.explorer import repo as explorer_repo
from app.shared.datasets import (
    _find_sheet,
    ensure_sheet_schema,
    get_version_sheet_rows,
    resolve_version,
)

from ..schemas import (
    ColumnDrift,
    ColumnNullabilityChange,
    ColumnOrderChange,
    ColumnTypeChange,
    ModifiedSheet,
    RenameCandidate,
    RenamedSheet,
    SheetColumn,
    SheetDiffResponse,
    SheetProfileDrift,
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


def _num_delta(a, b, ndigits: int = 6):
    if a is None or b is None:
        return None
    return round(b - a, ndigits)


def compute_profile_drift(profile_a: dict, profile_b: dict,
                          sheet_key: str | None = None) -> SheetProfileDrift:
    """Deltas between two persisted ProfileResponse dicts — pure, no I/O."""
    cols_a = {c["name"]: c for c in profile_a.get("columns") or []}
    cols_b = {c["name"]: c for c in profile_b.get("columns") or []}

    columns: list[ColumnDrift] = []
    for name in [n for n in cols_a if n in cols_b]:
        a, b = cols_a[name], cols_b[name]
        drift = ColumnDrift(
            column=name,
            from_null_percent=a.get("null_percent"),
            to_null_percent=b.get("null_percent"),
            null_percent_delta=_num_delta(a.get("null_percent"), b.get("null_percent"), 2),
            from_unique_count=a.get("unique_count"),
            to_unique_count=b.get("unique_count"),
            unique_count_delta=_num_delta(a.get("unique_count"), b.get("unique_count")),
            mean_delta=_num_delta(a.get("mean"), b.get("mean")),
            std_delta=_num_delta(a.get("std"), b.get("std")),
        )
        if "categorical" in (a.get("dtype"), b.get("dtype")):
            seen_a = {str(tv["value"]) for tv in a.get("top_values") or []}
            seen_b = {str(tv["value"]) for tv in b.get("top_values") or []}
            drift.added_categories = sorted(seen_b - seen_a)
            drift.removed_categories = sorted(seen_a - seen_b)
        columns.append(drift)

    ra, rb = profile_a.get("row_count"), profile_b.get("row_count")
    da, db = profile_a.get("duplicate_row_count"), profile_b.get("duplicate_row_count")
    return SheetProfileDrift(
        sheet_key=sheet_key,
        from_row_count=ra, to_row_count=rb, row_count_delta=_num_delta(ra, rb),
        from_duplicate_rows=da, to_duplicate_rows=db,
        duplicate_rows_delta=_num_delta(da, db),
        columns=columns,
        # Per-column deltas only cover columns profiled on BOTH sides; the
        # appearances and disappearances are drift in their own right (and are
        # how a transformation's projection shows up in §21's drift report).
        added_columns=sorted(n for n in cols_b if n not in cols_a),
        removed_columns=sorted(n for n in cols_a if n not in cols_b),
    )


async def _completed_profile(ver: dict, sheet_row: dict) -> dict | None:
    lsid = sheet_row.get("logical_sheet_id")
    if not lsid:
        return None
    run = await explorer_repo.get_completed_run(str(ver["id"]), str(lsid))
    return (run or {}).get("profile")


async def workbook_diff(dataset_id: str, from_version: int, to_version: int,
                        include_profile: bool = False) -> WorkbookDiffResponse:
    ver_a, sheets_a = await _sheets_with_schemas(dataset_id, from_version)
    ver_b, sheets_b = await _sheets_with_schemas(dataset_id, to_version)

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

    # A rename the caller ALREADY confirmed: confirm-rename relinks the two
    # versions onto one `logical_sheet_id` but deliberately leaves each
    # version's physical `sheet_key` alone (app/shared/repo.py:reassign_
    # logical_sheet), so the pair stays in added/removed forever. Report it as
    # a settled rename so the banner can stop asking, and so `added` does not
    # read as "a sheet arrived" for a sheet the server knows is the old one.
    renamed: list[RenamedSheet] = []
    confirmed_pairs: set[tuple[str, str]] = set()
    for rk in removed_keys:
        lsid = sheets_a[rk].get("logical_sheet_id")
        if not lsid:
            continue
        for ak in added_keys:
            if sheets_b[ak].get("logical_sheet_id") != lsid:
                continue
            confirmed_pairs.add((rk, ak))
            renamed.append(RenamedSheet(
                logical_sheet_id=str(lsid),
                from_sheet=sheets_a[rk]["sheet_name"],
                to_sheet=sheets_b[ak]["sheet_name"],
                from_sheet_key=rk,
                to_sheet_key=ak,
            ))

    # Rename suggestions: a removed and an added sheet with the same schema
    # fingerprint probably moved. Advisory only — the caller decides.
    candidates: list[RenameCandidate] = []
    for rk in removed_keys:
        a = sheets_a[rk]
        if not a.get("schema_fingerprint"):
            continue
        for ak in added_keys:
            b = sheets_b[ak]
            # Never re-suggest a rename that has already been confirmed: the
            # fingerprints still match (that is what gated the confirm), so the
            # suggestion would reappear on every diff and a second confirm 404s.
            if (rk, ak) in confirmed_pairs:
                continue
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

    profile_drift = profile_missing = None
    if include_profile:
        profile_drift, profile_missing = [], []
        for key in [k for k in sheets_a if k in sheets_b]:
            pa = await _completed_profile(ver_a, sheets_a[key])
            pb = await _completed_profile(ver_b, sheets_b[key])
            if pa and pb:
                profile_drift.append(compute_profile_drift(pa, pb, sheet_key=key))
            else:
                profile_missing.append(key)

    return WorkbookDiffResponse(
        dataset_id=dataset_id,
        from_version=from_version,
        to_version=to_version,
        added=[_sheet_summary(sheets_b[k]) for k in added_keys],
        removed=[_sheet_summary(sheets_a[k]) for k in removed_keys],
        modified=modified,
        unchanged=unchanged,
        renamed=renamed,
        rename_candidates=candidates,
        profile_drift=profile_drift,
        profile_missing=profile_missing,
    )


async def sheet_diff(
    dataset_id: str, from_version: int, sheet: str, to_version: int,
    include_profile: bool = False,
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
    if b is None and a.get("logical_sheet_id"):
        # A confirmed rename keeps one logical sheet across two versions but
        # gives each version its own sheet_key, so matching side B by key alone
        # 404s exactly the sheet the workbook diff reports under `renamed`. Fall
        # back to the logical identity the rename preserved, so the reviewer can
        # drill into what changed inside the renamed sheet.
        b = next((r for r in rows_b
                  if r.get("logical_sheet_id") == a["logical_sheet_id"]), None)
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

    profile_drift = None
    if include_profile:
        pa = await _completed_profile(ver_a, a)
        pb = await _completed_profile(ver_b, b)
        missing = ([v for v, p in (("from", pa), ("to", pb)) if not p])
        if missing:
            raise ProblemException(
                400,
                f"Profile drift needs a completed profile run on both versions; "
                f"missing: {', '.join(missing)} "
                f"(POST .../versions/{{v}}/profile-runs first)",
                code="profile-required", missing=missing,
            )
        profile_drift = compute_profile_drift(pa, pb, sheet_key=a["sheet_key"])

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
        profile_drift=profile_drift,
    )
