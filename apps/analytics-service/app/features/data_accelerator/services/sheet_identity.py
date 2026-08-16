"""Logical sheet identity — confirm-rename.

A new version whose sheet was renamed gets a fresh auto-created logical sheet
(same key = same identity is the ingest default). Confirming the rename folds
that spurious identity back into the original one, so sheet metadata and
quality rules keyed by ``logical_sheet_id`` follow the sheet instead of
silently detaching. Candidates come from the workbook diff (schema
fingerprints); non-candidate pairs need an explicit ``force``.
"""

from __future__ import annotations

from app.api.errors import ProblemException
from app.shared.datasets import _find_sheet, get_version_sheet_rows, resolve_version
from app.shared.repo import (
    count_logical_sheet_state,
    get_latest_sheet_row_for_logical,
    get_live_dataset_sheet,
    reassign_logical_sheet,
)

from .. import repo
from ..schemas import ConfirmRenameResponse


async def confirm_sheet_rename(
    dataset_id: str,
    version_number: int,
    *,
    from_sheet: str,
    to_sheet: str,
    force: bool = False,
) -> ConfirmRenameResponse:
    ver = await resolve_version(dataset_id, version_number=version_number)
    rows = await get_version_sheet_rows(ver)

    to_row = _find_sheet(rows, to_sheet)
    if not to_row:
        available = ", ".join(r["sheet_name"] for r in rows) or "none"
        raise ProblemException(
            404, f"Sheet not found in version {version_number}: {to_sheet} "
                 f"(available: {available})",
        )
    if not to_row.get("logical_sheet_id"):
        raise ProblemException(
            400, "Rename confirmation is not available for versions ingested "
                 "before sheet metadata was first-class",
            code="legacy-version",
        )

    old = await get_live_dataset_sheet(dataset_id, from_sheet)
    if not old:
        raise ProblemException(404, f"No logical sheet named '{from_sheet}' on this dataset")
    if old["id"] == to_row["logical_sheet_id"]:
        raise ProblemException(
            400, f"'{from_sheet}' and '{to_sheet}' already share the same identity",
            code="not-a-rename",
        )
    if any(r.get("logical_sheet_id") == old["id"] for r in rows):
        raise ProblemException(
            400, f"'{from_sheet}' still exists in version {version_number} — "
                 "a rename requires the old sheet to be absent",
            code="not-a-rename",
        )

    # Candidate check: the old sheet's last known fingerprint must match the
    # new sheet's — the same criterion the diff endpoint uses for suggestions.
    prior = await get_latest_sheet_row_for_logical(
        dataset_id, old["id"], before_version_number=version_number,
    )
    is_candidate = bool(
        prior and prior.get("schema_fingerprint")
        and prior["schema_fingerprint"] == to_row.get("schema_fingerprint")
    )
    if not is_candidate and not force:
        raise ProblemException(
            400, f"'{from_sheet}' → '{to_sheet}' is not a diff rename candidate "
                 "(schema fingerprints differ) — pass force=true to confirm anyway",
            code="rename-not-candidate",
        )

    # The auto-created identity must not have accumulated its own keyed state —
    # merging two sets of metadata/rules is not decidable server-side.
    #
    # The count spans BOTH kinds of attachment. `count_logical_sheet_state`
    # covers the tables whose rows would be orphaned; `count_cascading_...`
    # covers the ones an `ON DELETE CASCADE` would destroy outright, because
    # `reassign_logical_sheet` deletes the spurious identity. Without the
    # second half, confirming a rename silently deleted the transformations,
    # saved views and relationships someone had already built on the new
    # version's sheet, and answered 200 with `versions_relinked` as if nothing
    # had been lost.
    state = {**await count_logical_sheet_state(to_row["logical_sheet_id"]),
             **await repo.count_cascading_logical_sheet_state(to_row["logical_sheet_id"])}
    if any(state.values()):
        raise ProblemException(
            409, f"Sheet '{to_sheet}' already has its own metadata, quality rules or "
                 "derived objects — delete them (or keep the sheets separate) "
                 "before confirming the rename",
            code="conflicting-sheet-state",
            attached=state,
        )

    moved = await reassign_logical_sheet(
        dataset_id=dataset_id,
        old_logical_id=old["id"],
        new_logical_id=to_row["logical_sheet_id"],
        new_sheet_key=to_row["sheet_key"],
        new_display_name=to_row["sheet_name"],
    )
    return ConfirmRenameResponse(
        dataset_id=dataset_id,
        logical_sheet_id=old["id"],
        from_sheet=old["display_name"],
        to_sheet=to_row["sheet_name"],
        sheet_key=to_row["sheet_key"],
        was_candidate=is_candidate,
        forced=not is_candidate,
        versions_relinked=moved,
    )
