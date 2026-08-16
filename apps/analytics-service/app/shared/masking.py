"""Column-level masking driven by the data dictionary's `sensitivity` field.

The dictionary (§17) already records how sensitive each column is. This module
is what makes that declaration *do* something: a caller without the elevated
permission sees masked values wherever raw dataset rows are returned.

**This is a real control, not a display convenience.** Masking rows while
leaving `/download` open would be theatre — anyone could fetch the original
file. So :func:`ensure_raw_access` gates the raw-file paths too, and only for
datasets that actually declare sensitive columns; a dataset with no declared
sensitivity behaves exactly as before.

The masks preserve *shape* rather than blanking values outright — an analyst
can still tell that a column holds emails, that two rows differ, and roughly
how long a value is, which is usually what exploration needs. They are
deliberately not reversible and not format-preserving encryption.
"""

from __future__ import annotations

import hashlib
from typing import Any

# Free-text in the dictionary by design, so match case-insensitively over the
# vocabulary the field's own documentation suggests plus the obvious synonyms.
SENSITIVE_LEVELS = frozenset({
    "confidential", "restricted", "pii", "sensitive", "secret", "phi",
})

MASK = "***"


def is_sensitive(level: str | None) -> bool:
    return bool(level) and level.strip().lower() in SENSITIVE_LEVELS


def mask_value(value: Any, semantic_type: str | None = None) -> Any:
    """Mask one value, keeping enough shape to stay explorable.

    Emails keep their first character and domain suffix so a reader can still
    tell records apart and recognise the column; everything else collapses to a
    fixed token. NULL stays NULL — hiding whether a value is missing would
    distort null-rate reasoning without protecting anything.
    """
    if value is None:
        return None
    text = str(value)
    kind = (semantic_type or "").strip().lower()

    if kind == "email" or ("@" in text and "." in text.rsplit("@", 1)[-1]):
        local, _, domain = text.partition("@")
        head = local[0] if local else ""
        suffix = domain.rsplit(".", 1)[-1] if "." in domain else ""
        return f"{head}***@***.{suffix}" if suffix else f"{head}***@***"

    if kind in ("phone", "postal_code", "identifier", "uuid"):
        tail = text[-2:] if len(text) > 2 else ""
        return f"{MASK}{tail}"

    return MASK


def mask_rows(rows: list[dict], masked: dict[str, str | None]) -> list[dict]:
    """Apply masking to a result set.

    *masked* maps column name → its declared semantic type (used to pick the
    mask shape). Columns absent from a row are left alone rather than added.
    """
    if not masked:
        return rows
    out = []
    for row in rows:
        copy = dict(row)
        for column, semantic in masked.items():
            if column in copy:
                copy[column] = mask_value(copy[column], semantic)
        out.append(copy)
    return out


def digest(value: Any) -> str | None:
    """A stable pseudonym for a value — equal inputs give equal output.

    Used where a masked column still has to support grouping or joining in a
    UI without revealing the value itself.
    """
    if value is None:
        return None
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Policy resolution
# ---------------------------------------------------------------------------

async def sensitive_columns(dataset_id: str,
                            logical_sheet_id: str | None) -> dict[str, str | None]:
    """Declared-sensitive columns for a sheet → their semantic type.

    Keyed by the NORMALIZED column name, which is what the dictionary stores.
    """
    if not logical_sheet_id:
        return {}
    from app.features.discovery import repo as discovery_repo

    entries = await discovery_repo.list_column_metadata(str(logical_sheet_id))
    return {e["column_name"]: e.get("semantic_type")
            for e in entries if is_sensitive(e.get("sensitivity"))}


async def dataset_has_sensitive_columns(dataset_id: str) -> bool:
    """Whether ANY sheet of this dataset declares a sensitive column."""
    from app.features.discovery import repo as discovery_repo

    for entry in await discovery_repo.list_dataset_column_metadata(dataset_id):
        if is_sensitive(entry.get("sensitivity")):
            return True
    return False


def may_see_raw(principal, team_id: str | None) -> bool:
    """Whether this caller is exempt from masking **in this dataset's team**.

    The team argument is load-bearing, and this function used to omit it. It
    asked "does this caller hold DATASET_READ_SENSITIVE in *any* team?" by
    OR-ing over every membership, so being an admin of one team unmasked
    sensitive columns in *every* team the caller could read — and unlocked the
    raw download with them. That contradicts the model the rest of the service
    enforces: "a user's authority is evaluated *within a team*"
    (``app/features/auth/permissions.py``).

    ``Principal.can`` is the primitive that gets this right, including the
    superuser bypass, so there is no separate permission walk here any more.
    A caller with no resolvable team is masked rather than exempt: failing
    closed is the only safe direction for a control whose whole job is to
    withhold data.
    """
    from app.features.auth.permissions import Permission

    if getattr(principal, "is_superuser", False):
        return True
    if not team_id:
        return False
    return principal.can(str(team_id), Permission.DATASET_READ_SENSITIVE)


async def _dataset_team_id(dataset_id: str) -> str | None:
    """The team that owns *dataset_id*, or None if it cannot be resolved."""
    from app.shared.repo import get_dataset

    dataset = await get_dataset(dataset_id)
    return str(dataset["team_id"]) if dataset and dataset.get("team_id") else None


async def resolve_masking(dataset_id: str, sheet_row: dict | None,
                          principal) -> dict[str, str | None]:
    """Columns to mask for this caller, mapped to physical parquet names.

    Empty when the caller may see raw values, or when nothing is declared —
    so this costs one metadata read and nothing else on ordinary datasets.
    """
    if not sheet_row:
        return {}
    if may_see_raw(principal, await _dataset_team_id(dataset_id)):
        return {}
    declared = await sensitive_columns(dataset_id, sheet_row.get("logical_sheet_id"))
    if not declared:
        return {}

    # The dictionary keys on normalized names; result rows carry physical ones.
    physical: dict[str, str | None] = {}
    for column in sheet_row.get("schema_json") or []:
        normalized = column.get("normalized_name") or column["name"]
        if normalized in declared:
            physical[column["name"]] = declared[normalized]
    return physical


async def ensure_raw_access(principal, dataset_id: str) -> None:
    """Guard the raw-file paths for datasets that declare sensitive columns.

    Without this, masking would be trivially bypassable by downloading the
    file. Datasets that declare nothing are unaffected, so this does not change
    behaviour for the common case.
    """
    from app.api.errors import ProblemException

    if may_see_raw(principal, await _dataset_team_id(dataset_id)):
        return
    if not await dataset_has_sensitive_columns(dataset_id):
        return
    raise ProblemException(
        403,
        "This dataset declares sensitive columns — downloading the raw file "
        "requires elevated access. Use the explorer, which masks them.",
        code="sensitive-data-restricted")


def redact_profile(profile: dict | None, masked: dict[str, str | None]) -> dict | None:
    """Strip raw values out of a persisted profile for masked columns.

    A profile's ``top_values`` hold verbatim cell values — exactly the strings
    the grid and the column drawer withhold — so handing back a stored profile
    unfiltered leaks the sensitive column by another route. Distribution shape
    (counts, ratios) is preserved; only the values themselves are masked, and
    the numeric extremes that would narrow them down are dropped.
    """
    if not profile or not masked:
        return profile
    out = dict(profile)
    cols = []
    for col in out.get("columns") or []:
        name = col.get("name")
        if name not in masked:
            cols.append(col)
            continue
        semantic = masked[name]
        c = dict(col)
        if c.get("top_values"):
            c["top_values"] = [
                {**tv, "value": mask_value(tv.get("value"), semantic)}
                for tv in c["top_values"]
            ]
        for k in ("min", "max", "mean", "median", "q25", "q75", "std", "examples", "histogram"):
            if k in c:
                c[k] = None
        cols.append(c)
    out["columns"] = cols
    return out
