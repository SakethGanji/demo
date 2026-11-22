"""Materialise a snapshot into a new version (labeling plan §5.6): `derive()` with op `apply_labels` over the parent;
only the snapshot columns' cells change (`__row_id` kept, splits carried by __row_id), holdout cells are never written.
The manifest's provenance op and the derived_from edge record {snapshot_id, snapshot_sha256, columns, rows_changed}.
Correction proposals reach a version this way too (a proposal → its events → a snapshot → materialise)."""

from __future__ import annotations

from typing import Any

import numpy as np

from . import rows as ROWS
from . import snapshot as SN
from .overlay import _cast
from .status import judge
from .store import _layer_full, current_schema, default_version, schema_columns
from .values import LabelError


def holdout_ids(db: Any, store: Any, version_id: str, table: str) -> set[int]:
    """Every row that is a holdout row in any split of the version's table (conservative)."""
    from ..lake import splitstore as SS

    out: set[int] = set()
    for row in SS.rows_of_version(db, version_id):
        if row["table_name"] != table:
            continue
        key = row.get("blob_key")
        if not key:
            continue
        ids, folds = SS.read_blob(store, key)
        out |= set(int(i) for i in ids[folds == -1])
    return out


def materialise(app: Any, snapshot_id: str, version_id: str | None, by: str, *, columns: list[str] | None = None,
                reason: str = "") -> dict[str, Any]:
    from .. import frames
    from ..catalog import holds as H
    from ..catalog.schema import audit
    from ..datasets import DatasetError

    db, store = app.state.db, app.state.lake
    s = SN.get(db, snapshot_id)
    if s["state"] == "purged":
        raise LabelError(410, "snapshot_purged", "This snapshot was purged.")
    with db.connect() as con:
        lay = _layer_full(con, s["layer_id"])
        vid = version_id or default_version(con, lay["root_id"])
        vi = ROWS.version_info(con, vid)
        spec_cols = schema_columns(current_schema(con, s["layer_id"]))
    if vi["root"] != lay["root_id"]:
        raise LabelError(400, "version_other_root", f"{vid} is not a version of this snapshot's dataset.")
    if vi["storage"] != "parquet":
        raise LabelError(409, "labels_need_lake_version", f"{vid} is a CSV-era version.")
    if (vi["primary_table"] or "data") != lay["table_name"]:
        raise LabelError(400, "not_primary_table", "Materialise writes the version's primary table; this layer is on another table.")
    snap = SN.load(db, store, snapshot_id)
    cols = columns or sorted({r["column"] for r in snap})
    df = frames.read(frames.Table(vid, lay["table_name"]))
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise LabelError(400, "column_unknown", f"{vid} has no column {missing[0]!r}.", columns=missing)
    hold = holdout_ids(db, store, vid, lay["table_name"])
    vrows = ROWS.read(vid, lay["table_name"], {int(r["__row_id"]) for r in snap if r["column"] in cols})
    stale_keep = (s["policy"] or {}).get("stale") == "keep"
    changes: dict[str, list[tuple[int, Any]]] = {c: [] for c in cols}
    counts = {"applied": 0, "holdout_skipped": 0, "orphaned": 0, "stale": 0, "base_changed": 0, "conflict": 0, "absorbed": 0}
    for r in snap:
        if r["column"] not in cols or r["status"] != "applied":
            continue
        rid = int(r["__row_id"])
        st, _ = judge(lay["salt"], {**r, "row_id": rid}, vrows.get(rid), spec_cols.get(r["column"]))
        if st == "orphaned":
            counts["orphaned"] += 1
        elif rid in hold:
            counts["holdout_skipped"] += 1
        elif st == "absorbed":
            counts["absorbed"] += 1
        elif st in ("base_changed", "conflict") or (st == "stale" and not stale_keep):
            counts[st] += 1
        else:
            changes[r["column"]].append((rid, None if r["is_null"] else r["value"]))
            counts["applied"] += 1
    n = sum(len(v) for v in changes.values())
    if not n:
        raise LabelError(409, "nothing_to_apply", "No label of this snapshot changes a cell of this version.", counts=counts)
    for c, items in changes.items():
        if not items:
            continue
        ids = [i for i, _ in items]
        vals = [_cast(v, df[c]) for _, v in items]
        if df[c].dtype.kind in "iub" and any(v is None or isinstance(v, float) for v in vals):
            df[c] = df[c].astype("float64")
        df.loc[ids, c] = vals
    rec_op = {"snapshot_id": snapshot_id, "snapshot_sha256": s["snapshot_sha256"], "values_sha256": s["values_sha256"],
              "layer_id": s["layer_id"], "columns": cols, "rows_changed": n, "holdout": "original", "counts": counts}
    parent = app.state.db.get_dataset(vid) or {}
    try:
        rec = app.state.datasets.derive(
            vid, df, None, "apply_labels",
            [{"op": "apply_labels", "column": c, "rows": len(v)} for c, v in changes.items() if v],
            name=parent.get("name") or vid, note=f"labels from {snapshot_id} ({n} cells)", by=by,
            reason=reason or f"apply labels {snapshot_id}", extra={"apply_labels": rec_op}, ops_record={"apply_labels": rec_op})
    except DatasetError as e:
        raise LabelError(400, "derive_failed", str(e)) from None
    with db.connect() as con:
        audit(con, by, "label_snapshot_materialised", {**rec_op, "parent_version_id": vid, "version_id": rec.get("id")},
              comment=reason, object_kind="label_snapshot", object_id=snapshot_id)
    return {"snapshot_id": snapshot_id, "parent_version_id": vid, "version_id": rec.get("id"), "rows_changed": n,
            "counts": counts, "deduplicated": rec.get("deduplicated")}


_ = np
