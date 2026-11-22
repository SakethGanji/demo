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
    fitted = _verify_fitted(db, run_id, r)
    ok = blob_ok and osha == r["overlay_sha256"] and bool(snap.get("ok")) and fitted.get("ok") is not False
    return {**out, "ok": ok, "status": "ok" if ok else "mismatch", "overlay_blob_ok": blob_ok,
            "overlay_sha_ok": osha == r["overlay_sha256"], "snapshot": snap, "rows_applied": len(rows),
            "labels_fitted": fitted}


def _verify_fitted(db: Any, run_id: str, ri: dict[str, Any]) -> dict[str, Any]:
    """Recompute labels_fitted_sha256 from the lake: the run's training rows (its split, folds ≥ 0) with a label after the
    run's overlay, labels read through the same overlay. A sampled run: not recomputed here (its sample blob is the pin)."""
    if not ri.get("labels_fitted_sha256"):
        return {"ok": None, "status": "not_recorded"}
    if ri.get("sample_sha256"):
        return {"ok": None, "status": "sampled_run_not_recomputed"}
    with db.connect() as con:
        c = con.execute("SELECT config FROM runs WHERE id = ?", (run_id,)).fetchone()
    cfg = json.loads(c[0]) if c else {}
    from ..engine import serving

    try:
        rr = serving.RunRows(cfg)
        ids = rr.ids[(rr.fold >= 0) & rr.label_ok]
        df = rr.read(ids, [cfg["label"]])
        got = labels_fitted_sha(df.index.to_numpy(), df[cfg["label"]])
    except Exception as e:  # noqa: BLE001 - an unreadable input is a failed verify, reported
        return {"ok": False, "status": "error", "message": f"{type(e).__name__}: {e}"[:300]}
    return {"ok": got == ri["labels_fitted_sha256"], "status": "ok" if got == ri["labels_fitted_sha256"] else "mismatch",
            "rows": int(len(df)), "labels_fitted_sha256": got}


_ = (canon_cell, canon_json)


# --------------------------------------------------------------------------- post-fit readers (review C1)

_RUN_CACHE: dict[str, dict[int, str | None]] = {}


def run_applied(cfg: dict[str, Any] | None) -> dict[int, str | None] | None:
    """The applied set a finished run trained with ({__row_id: value}; training rows only by construction), read from
    its pinned overlay blob; None when the run has no overlay (or the pin is not written yet / purged)."""
    if not cfg or not cfg.get("labels") or not cfg.get("run_id") or not cfg.get("db_path"):
        return None
    import pyarrow.parquet as pq

    from .. import frames
    from ..db import Database

    with Database(Path(cfg["db_path"])).connect() as con:
        r = con.execute("SELECT r.overlay_blob, b.key, b.state FROM run_inputs r LEFT JOIN blobs b ON b.sha256 = r.overlay_blob"
                        " WHERE r.run_id = ? AND r.role = 'train'", (cfg["run_id"],)).fetchone()
    if not r or not r[0] or not r[1] or r[2] in ("purging", "purged"):
        return None
    if r[0] not in _RUN_CACHE:
        t = pq.read_table(frames._store().local_copy(r[1]))
        _RUN_CACHE[r[0]] = {int(a): b for a, b in zip(t.column("__row_id").to_pylist(), t.column("value").to_pylist())}
    return _RUN_CACHE[r[0]]


def apply_dict(frame: Any, label: str, applied: dict[int, str | None] | None) -> Any:
    """Set `label` on the frame's rows in `applied` (index = __row_id). Returns the frame (a copy when changed)."""
    if frame is None or not applied or label not in getattr(frame, "columns", []):
        return frame
    ids = [i for i in frame.index if int(i) in applied]
    if not ids:
        return frame
    frame = frame.copy()
    col = frame[label]
    vals = [_cast(applied[int(i)], col) for i in ids]
    if col.dtype.kind in "iub" and any(v is None or isinstance(v, float) for v in vals):
        frame[label] = col.astype("float64")
    frame.loc[ids, label] = vals
    return frame


def apply_run(cfg: dict[str, Any] | None, frame: Any) -> Any:
    """`apply_dict` with the run's own overlay (no-op for a run without one)."""
    a = run_applied(cfg)
    return apply_dict(frame, (cfg or {}).get("label") or "", a) if a else frame


def labels_fitted_sha(ids: Any, y: Any) -> str:
    """sha256 over (__row_id, canonical label) of the rows fitted, sorted by __row_id (review C1 evidence)."""
    import numpy as np

    h = hashlib.sha256(b"ensemble.labelsfitted/1\n")
    ids = np.asarray(ids, dtype="int64")
    order = np.argsort(ids, kind="stable")
    yv = list(y)
    for k in order:
        c = canon_cell(yv[k])
        h.update(int(ids[k]).to_bytes(8, "little", signed=True) + (b"\x00" if c is None else c.encode()) + b"\x1e")
    return h.hexdigest()
