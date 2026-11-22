"""The training overlay (labeling plan §4.4, §5.5, §5.7).

At POST /runs: `pin_spec(db, labels, version_id, table, label)` checks the snapshot against the run and returns the
spec the run's cfg carries ({snapshot_id, snapshot_sha256, columns, holdout, stale}).
In the worker, before the fit: `Overlay(cfg).apply(df, is_holdout)` re-verifies the snapshot blob (409-style
overlay_integrity on a mismatch), computes the applied set (status applied, column in columns, row present, not stale /
base-changed / conflict per policy, NEVER a holdout row), writes it as a `label_overlay` blob pinned to the run, and
sets the label cells of the training rows. Base read hashes are untouched (the overlay is checked separately).
`verify_run(db, store, run_id)` re-verifies a finished run's pin (purged → status 'purged', never 'mismatch')."""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

from . import rows as ROWS
from . import snapshot as SN
from .status import judge
from .store import _layer_full, current_schema, schema_columns
from .values import LabelError, canon_cell, canon_json

UNSUPPORTED = "labels_unsupported_kind"


def pin_spec(db: Any, labels: Any, version_id: str, table: str | None, label: str) -> dict[str, Any]:
    if not isinstance(labels, dict) or not isinstance(labels.get("snapshot_id"), str):
        raise LabelError(400, "bad_labels", "labels is {snapshot_id, columns?, holdout?, stale?}.")
    s = SN.get(db, labels["snapshot_id"])
    if s["state"] == "purged":
        raise LabelError(409, "snapshot_purged", "This snapshot was purged.")
    with db.connect() as con:
        vi = ROWS.version_info(con, version_id)
    if vi["storage"] != "parquet":
        raise LabelError(409, "labels_need_lake_version", f"{version_id} is a CSV-era version; labels need a lake version.")
    table = table or vi["primary_table"] or "data"
    if vi["root"] != s["root_id"] or s["table_name"] != table:
        raise LabelError(400, "snapshot_other_dataset", "The snapshot's layer belongs to another dataset or table than this run's.")
    cols = labels.get("columns") or [label]
    if cols != [label]:
        raise LabelError(400, "overlay_label_only", "The training overlay applies to the run's label column only "
                         "(feature corrections are materialised into a version: POST /label-snapshots/{s}/materialise).")
    holdout = labels.get("holdout") or "original"
    if holdout != "original":
        raise LabelError(409, "holdout_corrected_unavailable", "Corrected-holdout evaluation is not available yet: the holdout "
                         "keeps its original labels.")
    stale = labels.get("stale") or "exclude"
    if stale not in ("exclude", "keep"):
        raise LabelError(400, "bad_labels", "stale is exclude or keep.")
    return {"snapshot_id": s["id"], "snapshot_sha256": s["snapshot_sha256"], "values_sha256": s["values_sha256"],
            "layer_id": s["layer_id"], "columns": cols, "holdout": holdout, "stale": stale}


def overlay_sha(snapshot_sha: str, table_sha: str, split_sha: str, holdout: str, stale: str, vsha: str) -> str:
    return hashlib.sha256("\n".join(["ensemble.overlay/1", snapshot_sha, table_sha or "", split_sha or "", holdout, stale, vsha])
                          .encode()).hexdigest()


def _cast(value: str | None, series: Any) -> Any:
    import pandas as pd

    if value is None:
        return None
    if pd.api.types.is_bool_dtype(series.dtype):
        return value == "true"
    if pd.api.types.is_integer_dtype(series.dtype):
        try:
            return int(value)
        except ValueError:
            return float(value)
    if pd.api.types.is_float_dtype(series.dtype):
        return float(value)
    # object / string column: keep the column's own spelling of a number when every value is numeric
    if series.dropna().map(lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)).all() and len(series.dropna()):
        try:
            f = float(value)
            return int(f) if f.is_integer() else f
        except ValueError:
            return value
    return value


class Overlay:
    def __init__(self, cfg: dict[str, Any]) -> None:
        from ..db import Database
        from .. import frames

        self.cfg = cfg
        self.spec = cfg["labels"]
        self.db = Database(Path(cfg["db_path"]))
        self.store = frames._store()
        self.applied: dict[int, str | None] = {}
        self.info: dict[str, Any] = {}

    def _snapshot_rows(self) -> list[dict[str, Any]]:
        s = SN.get(self.db, self.spec["snapshot_id"])
        if s["snapshot_sha256"] != self.spec["snapshot_sha256"]:
            raise LabelError(409, "overlay_integrity", "The pinned snapshot's hash changed since the run was queued.")
        v = SN.verify(self.db, self.store, s["id"])
        if not v.get("ok"):
            raise LabelError(409, "overlay_integrity", f"The pinned label snapshot does not verify ({v.get('status')}).", verify=v)
        return SN.load(self.db, self.store, s["id"])

    def compute(self, df: Any, is_holdout: Any) -> dict[str, Any]:
        """The applied set over `df` (index = __row_id) with the run's holdout mask; sets self.applied."""
        import numpy as np

        label = self.cfg["label"]
        it = self.cfg.get("input_table") or {}
        vid, table = it.get("version_id") or self.cfg["dataset_id"], it.get("table") or "data"
        snap = [r for r in self._snapshot_rows() if r["column"] in self.spec["columns"]]
        with self.db.connect() as con:
            lay = _layer_full(con, self.spec["layer_id"])
            spec_cols = schema_columns(current_schema(con, self.spec["layer_id"]))
        hold = set(int(i) for i in np.asarray(df.index)[np.asarray(is_holdout, dtype=bool)])
        present = set(int(i) for i in df.index)
        vrows = ROWS.read(vid, table, {int(r["__row_id"]) for r in snap if int(r["__row_id"]) in present})
        counts = {"snapshot_rows": len(snap), "applied": 0, "holdout_skipped": 0, "orphaned": 0, "stale": 0, "base_changed": 0,
                  "conflict": 0, "absorbed": 0, "not_applied_status": 0}
        applied: dict[int, str | None] = {}
        for r in snap:
            rid = int(r["__row_id"])
            if r["status"] != "applied":
                counts["not_applied_status"] += 1
                continue
            rr = {**r, "row_id": rid}
            st, _ = judge(lay["salt"], rr, vrows.get(rid), spec_cols.get(r["column"]))
            if st == "orphaned":
                counts["orphaned"] += 1
                continue
            if rid in hold:
                counts["holdout_skipped"] += 1  # §5.7: training overlays never touch holdout rows
                continue
            if st in ("base_changed", "conflict") or (st == "stale" and self.spec["stale"] == "exclude"):
                counts[st] += 1
                continue
            if st == "absorbed":
                counts["absorbed"] += 1
            applied[rid] = None if r["is_null"] else r["value"]
            counts["applied"] += 1
        self.applied = applied
        entries = [{"__row_id": k, "column": label, "value": v, "status": "applied"} for k, v in sorted(applied.items())]
        vsha = SN.values_sha(entries)
        osha = overlay_sha(self.spec["snapshot_sha256"], it.get("table_sha256") or "",
                           (self.cfg.get("split_pin") or {}).get("split_sha256") or "", self.spec["holdout"], self.spec["stale"], vsha)
        blob = self._write_blob(entries)
        self.info = {"snapshot_id": self.spec["snapshot_id"], "snapshot_sha256": self.spec["snapshot_sha256"],
                     "overlay_sha256": osha, "overlay_blob": blob, "values_sha256": vsha,
                     "spec": {"columns": self.spec["columns"], "holdout": self.spec["holdout"], "stale": self.spec["stale"],
                              "table_sha256": it.get("table_sha256") or "",
                              "split_sha256": (self.cfg.get("split_pin") or {}).get("split_sha256") or "",
                              "rows_applied": counts["applied"], "counts": counts}}
        return self.info

    def _write_blob(self, entries: list[dict[str, Any]]) -> str:
        import pyarrow as pa
        import pyarrow.parquet as pq

        from ..catalog.schema import now
        from ..lake import manifest as M

        tmp = self.store.tmp_dir("overlay-" + secrets.token_hex(4))
        try:
            f = tmp / "overlay.parquet"
            pq.write_table(pa.table({"__row_id": pa.array([e["__row_id"] for e in entries], pa.int64()),
                                     "column": pa.array([e["column"] for e in entries], pa.string()),
                                     "value": pa.array([e["value"] for e in entries], pa.string()),
                                     "status": pa.array([e["status"] for e in entries], pa.string())}), f, compression="zstd")
            sha, nbytes = M.file_sha256(f)
            key = self.store.put_file(f, sha, "parquet")
        finally:
            if hasattr(self.store, "remove_tmp"):
                self.store.remove_tmp(tmp)
        run_id = self.cfg.get("run_id") or "unknown"
        with self.db.connect() as con:
            team = con.execute("SELECT team_id FROM label_layers WHERE id = ?", (self.spec["layer_id"],)).fetchone()[0]
            con.execute("INSERT INTO blobs (sha256, key, kind, bytes, n_rows, state, created_at) VALUES (?, ?, 'label_overlay', ?, ?, 'live', ?)"
                        " ON CONFLICT (sha256) DO UPDATE SET state = 'live' WHERE blobs.state IN ('pending', 'live')",
                        (sha, key, nbytes, len(entries), now()))
            con.execute("INSERT INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at)"
                        " VALUES (?, 'run', ?, 'label_overlay', ?, ?) ON CONFLICT DO NOTHING", (sha, run_id, team, now()))
        return sha

    def apply_mask(self, frame: Any, mask: Any) -> int:
        """`apply` on the rows where `mask` holds (the whole read frame, minus the holdout)."""
        import numpy as np

        if not self.applied:
            return 0
        sub = frame.loc[np.asarray(mask, dtype=bool)]
        n = self.apply(sub)
        if n:
            label = self.cfg["label"]
            if frame[label].dtype != sub[label].dtype:
                frame[label] = frame[label].astype(sub[label].dtype)
            frame.loc[sub.index, label] = sub[label]
        return n

    def apply(self, frame: Any) -> int:
        """Set the label of the frame's rows that are in the applied set (never called on the holdout frame)."""
        if frame is None or not self.applied:
            return 0
        label = self.cfg["label"]
        if label not in frame.columns:
            return 0
        ids = [i for i in frame.index if int(i) in self.applied]
        if not ids:
            return 0
        col = frame[label]
        vals = [_cast(self.applied[int(i)], col) for i in ids]
        if col.dtype.kind in "iub" and any(v is None or isinstance(v, float) for v in vals):
            frame[label] = col.astype("float64" if col.dtype.kind != "b" else "object")
        frame.loc[ids, label] = vals
        return len(ids)


def verify_run(db: Any, store: Any, run_id: str) -> dict[str, Any]:
    import pyarrow.parquet as pq

    from ..lake import manifest as M

    with db.connect() as con:
        r = con.execute("SELECT * FROM run_inputs WHERE run_id = ? AND role = 'train'", (run_id,)).fetchone()
        if not r or not r["label_snapshot_id"]:
            raise LabelError(404, "no_overlay", f"Run {run_id} trained without a label overlay.")
        r = dict(r)
        b = con.execute("SELECT key, state FROM blobs WHERE sha256 = ?", (r["overlay_blob"],)).fetchone()
        sn = con.execute("SELECT state FROM label_snapshots WHERE id = ?", (r["label_snapshot_id"],)).fetchone()
    out: dict[str, Any] = {"run_id": run_id, "snapshot_id": r["label_snapshot_id"], "overlay_sha256": r["overlay_sha256"]}
    if (sn and sn[0] == "purged") or (b and b["state"] in ("purging", "purged")):
        return {**out, "ok": None, "status": "purged"}
    if not b:
        return {**out, "ok": False, "status": "missing"}
    p = Path(store.local_copy(b["key"]))
    spec = json.loads(r["overlay_spec"] or "{}")
    blob_ok = M.file_sha256(p)[0] == r["overlay_blob"]
    rows = pq.read_table(p).to_pylist() if blob_ok else []
    vsha = SN.values_sha(rows)
    osha = overlay_sha(r["label_snapshot_sha256"], spec.get("table_sha256", ""), spec.get("split_sha256", ""),
                       spec.get("holdout", "original"), spec.get("stale", "exclude"), vsha)
    snap = SN.verify(db, store, r["label_snapshot_id"])
    ok = blob_ok and osha == r["overlay_sha256"] and bool(snap.get("ok"))
    return {**out, "ok": ok, "status": "ok" if ok else "mismatch", "overlay_blob_ok": blob_ok,
            "overlay_sha_ok": osha == r["overlay_sha256"], "snapshot": snap, "rows_applied": len(rows)}


_ = (canon_cell, canon_json)
