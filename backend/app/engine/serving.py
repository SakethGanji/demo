"""Load saved predictors in the API process (lazily, a few at a time) to predict, read out-of-fold predictions
and compute feature importance on demand. AutoGluon is imported on first use, not at API start."""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from typing import Any

import numpy as np
import pandas as pd

from .jsonsafe import jsonable

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")


class ServingError(ValueError):
    """Shown to the user as a 400."""


class PredictorCache:
    def __init__(self, size: int = 4) -> None:
        self.size = size
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, path: str):
        with self._lock:
            if path in self._cache:
                self._cache.move_to_end(path)
                return self._cache[path]
        from pathlib import Path as _P

        tomb = _P(path).parent / "EVICTED.json"
        if not _P(path).exists() and tomb.exists():  # P1: retention deleted it; the API answers 410 "re-fit from config"
            import json as _json

            from ..catalog.retention import EvictedError

            raise EvictedError(_P(path).parent.name, _json.loads(tomb.read_text()))
        from autogluon.tabular import TabularPredictor

        import ensemble_models  # noqa: F401 — custom models must be importable to unpickle them

        p = TabularPredictor.load(path, verbosity=0, require_py_version_match=False)
        with self._lock:
            self._cache[path] = p
            while len(self._cache) > self.size:
                self._cache.popitem(last=False)
        return p

    def drop(self, path: str) -> None:
        """Forget a predictor, so the next call loads it from disk again (after a model was added to it)."""
        with self._lock:
            self._cache.pop(path, None)


def _frame(predictor, rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        raise ServingError("Give at least one row.")
    df = pd.DataFrame(rows)
    features = list(predictor.original_features)
    missing = [c for c in features if c not in df.columns]
    for c in missing:
        df[c] = np.nan
    return df[features]


def predict(predictor, rows: list[dict[str, Any]], model: str | None = None) -> dict[str, Any]:
    if model is not None and model not in predictor.model_names():
        raise ServingError(f"No model '{model}' in this run.")
    X = _frame(predictor, rows)
    given = set().union(*(r.keys() for r in rows))
    missing = [c for c in predictor.original_features if c not in given]
    out: dict[str, Any] = {"model": model or predictor.model_best, "problem_type": predictor.problem_type,
                           "n_rows": len(X), "missing_columns": missing}
    if predictor.problem_type in ("binary", "multiclass"):
        proba = predictor.predict_proba(X, model=model)
        pred = predictor.predict_from_proba(proba)
        out["classes"] = jsonable(list(proba.columns))
        out["predictions"] = jsonable(pred.tolist())
        out["probabilities"] = [{str(c): float(v) for c, v in r.items()} for r in proba.to_dict(orient="records")]
        if predictor.problem_type == "binary":
            out["positive_class"] = jsonable(predictor.positive_class)
    elif predictor.problem_type == "quantile":  # F9: every level; `predictions` is the centre level
        from . import quantile as qt

        q = qt.qframe(predictor, X, model=model)
        out["quantile_levels"] = [float(t) for t in q.columns]
        out["centre"] = qt.centre(list(q.columns))
        out["predictions"] = jsonable(qt.point(q).tolist())
        out["quantiles"] = qt.as_records(q)
        out["probabilities"] = None
    else:
        out["predictions"] = jsonable(predictor.predict(X, model=model).tolist())
        out["probabilities"] = None
    return out


def oof(predictor, model: str | None = None) -> dict[str, Any]:
    if not predictor._trainer.bagged_mode:
        raise ServingError("This run has no out-of-fold predictions (it was fit without bagging: num_bag_folds = 0).")
    if model is not None and model not in predictor.model_names():
        raise ServingError(f"No model '{model}' in this run.")
    model = model or predictor.model_best
    if model.endswith("_FULL"):
        raise ServingError("Refit (_FULL) models have no out-of-fold predictions; ask for the bagged model.")
    out: dict[str, Any] = {"model": model, "problem_type": predictor.problem_type}
    if predictor.problem_type in ("binary", "multiclass"):
        proba = predictor.predict_proba_oof(model=model)
        out["rows"] = [int(i) for i in proba.index]
        out["classes"] = jsonable(list(proba.columns))
        out["probabilities"] = jsonable(proba.to_numpy().round(6))
        out["predictions"] = jsonable(predictor.predict_from_proba(proba).tolist())
    elif predictor.problem_type == "quantile":
        from . import quantile as qt

        q = predictor.predict_oof(model=model)
        q.columns = [float(c) for c in q.columns]
        out["rows"] = [int(i) for i in q.index]
        out["quantile_levels"] = [float(t) for t in q.columns]
        out["predictions"] = jsonable(qt.point(q).tolist())
        out["quantiles"] = jsonable(q.to_numpy().round(6))
    else:
        pred = predictor.predict_oof(model=model)
        out["rows"] = [int(i) for i in pred.index]
        out["predictions"] = jsonable(pred.tolist())
    return out


def feature_importance(predictor, holdout: pd.DataFrame, time_limit: float = 60, num_shuffle_sets: int = 3) -> list[dict[str, Any]]:
    fi = predictor.feature_importance(holdout, subsample_size=min(2000, len(holdout)), num_shuffle_sets=num_shuffle_sets,
                                      time_limit=time_limit, silent=True)
    return jsonable(fi.reset_index().rename(columns={"index": "feature"}))


def holdout_frame(src: Any, splits: dict[str, Any], label: str, drop: list[str]) -> pd.DataFrame:
    """The holdout rows as the fit saw them. `src` is a frames ref (or, from old callers, the data.csv path)."""
    from .. import frames

    ref = src if isinstance(src, (frames.Table, frames.JoinFrame, frames.LegacyPath)) else frames.LegacyPath(str(src))
    big = bounded_holdout(ref, splits, label, drop)
    if big is not None:
        return big
    df = frames.read(ref)  # P03: typed (engine_v1), the same dtypes as the fit's holdout
    h = df.iloc[np.asarray(splits["holdout"], dtype=int)]  # P09: holdout entries are read positions (a Parquet index is __row_id)
    h = h[h[label].notna()]
    return h.drop(columns=[c for c in drop if c in h.columns and c != label])


# --------------------------------------------------------------------------- F-X5a: a run's rows without the table
#
# X5 K1 / K2: the build, the Model Doctor and the gap analysis read the whole table to pick a few rows. At 10M rows that
# OOM-killed the heavy pool. These read __row_id (and the label's validity) once, map the run's split onto them, and then
# parse only the rows wanted (by __row_id, row group by row group), in batches when every holdout row is needed.

ROW_ID = "__row_id"
_SALT_TRAIN = 7  # the doctor's training-row sample: its own splitmix64 stream (P11 uses 0 for the fit, 1 for the holdout)


def parquet_ref(ref: Any) -> bool:
    from .. import frames

    return isinstance(ref, (frames.Table, frames.JoinFrame)) and frames.is_parquet(ref)


def read_ids(ref: Any, label: str | None = None) -> tuple[np.ndarray, np.ndarray | None]:
    """(__row_id in read order, whether the label is set on each row) of a Parquet table: two columns, no pandas frame."""
    import pyarrow as pa
    import pyarrow.compute as pc

    from .. import frames

    lt = frames.lake_table(ref)
    ids, ok = [], []
    for part in lt.parts:
        try:
            t = frames.read_part_columns(part["path"], [ROW_ID] + ([label] if label else []))
        except FileNotFoundError as e:
            raise frames.FrameError(f"A file of {ref.version_id} ({part['sha256'][:12]}…) is missing from the lake.") from e
        ids.append(t.column(0).to_numpy(zero_copy_only=False).astype("int64"))
        if label:
            col = t.column(1)
            v = pc.is_valid(col)
            if pa.types.is_floating(col.type):
                v = pc.and_(v, pc.invert(pc.is_nan(col)))
            ok.append(v.to_numpy(zero_copy_only=False).astype(bool))
        del t
    out = np.concatenate(ids) if ids else np.zeros(0, dtype="int64")
    return out, (np.concatenate(ok) if label and ok else (np.zeros(0, bool) if label else None))


def folds_by_read_order(cfg: dict[str, Any], ids: np.ndarray) -> np.ndarray:
    """The run's split as one fold per row in read order (-1 holdout, -2 excluded, -3 tuning, ≥ 0 training): from the pinned
    split blob by __row_id (P09), else the positional split file."""
    import json
    from pathlib import Path

    pin = cfg.get("split_pin") or {}
    if pin.get("blob_sha256") and cfg.get("db_path"):
        from .. import frames
        from ..db import Database
        from ..lake import splitstore as SS

        lake = frames._store()
        key = SS._blob_key(Database(Path(cfg["db_path"])), pin["blob_sha256"])
        if key and lake.exists(key):
            sid, sfold = SS.read_blob(lake, key)
            order = np.argsort(sid, kind="stable")
            sid, sfold = sid[order], sfold[order]
            pos = np.searchsorted(sid, ids)
            okp = (pos < len(sid)) & (sid[np.minimum(pos, len(sid) - 1)] == ids)
            if okp.all():
                return sfold[pos].astype("int64")
    sp = json.loads(Path(cfg["splits_path"]).read_text())
    fold = np.asarray(sp["fold"], dtype="int64")
    if len(fold) != len(ids):
        raise ValueError(f"the run's split covers {len(fold):,} rows, the table has {len(ids):,}")
    fold = fold.copy()
    fold[np.asarray(sp["holdout"], dtype="int64")] = -1
    return fold


def keep_at_most(ids: np.ndarray, cap: int, seed: int, salt: int) -> np.ndarray:
    """A keep mask over `ids`: every row when len ≤ cap, else a splitmix64 sample at rate cap / len (deterministic in the
    ids, the seed and the stream; the same hash as P11's recorded sample)."""
    from ..lake import frame as LF

    if len(ids) <= cap:
        return np.ones(len(ids), dtype=bool)
    return LF._u(ids, seed, salt) < LF._thr(LF.rate_str(cap / len(ids)))


def nulls_plan(ref: Any) -> dict[str, Any]:
    """What `lake.frame.iter_rows` needs to type a row subset as the whole read types it: which columns have nulls in the
    table, from the Parquet footers (no scan); a column without statistics is scanned with P11's estimate."""
    import pyarrow.parquet as pq

    from .. import frames
    from ..lake import frame as LF

    lt = frames.lake_table(ref)
    nulls: dict[str, int] = {}
    for part in lt.parts:
        md = pq.ParquetFile(str(part["path"])).metadata
        names = [md.schema.column(i).name for i in range(md.num_columns)]
        for rg in range(md.num_row_groups):
            g = md.row_group(rg)
            for i, name in enumerate(names):
                st = g.column(i).statistics
                if st is None or not st.has_null_count:
                    return {"estimate": LF.estimate(ref)}
                nulls[name] = nulls.get(name, 0) + int(st.null_count)
    return {"estimate": {"columns": [{"name": n, "nulls": v} for n, v in nulls.items() if n != ROW_ID]}}


def table_columns(ref: Any) -> list[str]:
    from .. import frames

    return [c["name"] for c in frames.lake_table(ref).schema if c["name"] != ROW_ID]


class RunRows:
    """A run's rows located without reading the table: `ids` (read order), `fold` (by read position), `label_ok`.
    `holdout_ids()` are the holdout rows with a label (sorted), `read(ids)` / `batches(ids)` parse only those rows, typed as
    the whole table reads, the wizard's column types applied as the fit applied them."""

    def __init__(self, cfg: dict[str, Any], ref: Any | None = None) -> None:
        from .. import frames

        self.cfg = cfg
        self.ref = ref if ref is not None else frames.resolve(cfg)
        self.label = cfg["label"]
        self.ids, self.label_ok = read_ids(self.ref, self.label)
        self.fold = folds_by_read_order(cfg, self.ids)
        from ..labels.overlay import run_applied  # LB-A (review C1): an overlay run's training labels, not the base's

        self.overlay = run_applied(cfg)
        if self.overlay and self.label_ok is not None:
            keys = np.fromiter(self.overlay.keys(), dtype="int64")
            nonnull = np.fromiter((k for k, v in self.overlay.items() if v is not None), dtype="int64")
            m = np.isin(self.ids, keys) & (self.fold != -1)
            ok = self.label_ok.copy()
            ok[m] = np.isin(self.ids[m], nonnull)
            self.label_ok = ok
        self.plan = nulls_plan(self.ref)
        self.n_rows = int(len(self.ids))
        self.seed = int(cfg.get("sample_seed") if cfg.get("sample_seed") is not None else (cfg.get("split") or {}).get("seed") or 42)

    def holdout_ids(self) -> np.ndarray:
        return np.sort(self.ids[(self.fold == -1) & self.label_ok])

    def train_ids(self) -> np.ndarray:
        return np.sort(self.ids[(self.fold >= 0) & self.label_ok])

    def columns(self, drop: list[str] | None = None) -> list[str]:
        drop = set(drop or []) - {self.label}
        return [c for c in table_columns(self.ref) if c not in drop]

    def read(self, ids: np.ndarray, columns: list[str]) -> pd.DataFrame:
        from ..lake import frame as LF

        from ..labels.overlay import apply_dict

        df = LF.read_rows(self.ref, np.sort(np.asarray(ids, dtype="int64")), columns, self.plan)
        return LF.cast_column_types(self.cfg, apply_dict(df, self.label, self.overlay))

    def batches(self, ids: np.ndarray, columns: list[str], batch_rows: int | None = None):
        from .. import config
        from ..lake import frame as LF

        for df in LF.iter_rows(self.ref, np.sort(np.asarray(ids, dtype="int64")), columns, self.plan,
                               batch_rows=int(batch_rows or config.HOLDOUT_BATCH_ROWS)):
            from ..labels.overlay import apply_dict

            yield LF.cast_column_types(self.cfg, apply_dict(df, self.label, self.overlay))


def iter_file_rows(path: str, ids: np.ndarray, columns: list[str], plan: dict[str, Any], batch_rows: int):
    """`lake.frame.iter_rows` over one Parquet file (a rebuilt serving frame): the rows whose __row_id is in `ids`."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from ..lake import frame as LF

    ids = np.sort(np.asarray(ids, dtype="int64"))
    nulls = LF._has_nulls(plan)
    pf = pq.ParquetFile(path)
    rid_col = pf.schema_arrow.get_field_index(ROW_ID)
    pending, n = [], 0
    for rg in range(pf.num_row_groups):
        rng = LF._rg_range(pf, rg, rid_col)
        sub = ids
        if rng is not None:
            a, b = np.searchsorted(ids, [rng[0], rng[1] + 1])
            if a == b:
                continue
            sub = ids[a:b]
        rid = pf.read_row_group(rg, columns=[ROW_ID]).column(0).to_numpy(zero_copy_only=False).astype("int64")
        pos = np.searchsorted(sub, rid)
        mask = (pos < len(sub)) & (sub[np.minimum(pos, len(sub) - 1)] == rid)
        if not mask.any():
            continue
        t = pf.read_row_group(rg, columns=[ROW_ID, *columns]).filter(pa.array(mask))
        pending.append(t)
        n += t.num_rows
        if n >= batch_rows:
            yield LF._to_frame(pa.concat_tables(pending, promote_options="default"), columns, nulls)
            pending, n = [], 0
    if pending:
        yield LF._to_frame(pa.concat_tables(pending, promote_options="default"), columns, nulls)


def bounded_holdout(ref: Any, splits: dict[str, Any], label: str, drop: list[str], cap: int | None = None,
                    seed: int = 42) -> pd.DataFrame | None:
    """F-X5a: `holdout_frame` of a Parquet table above ENSEMBLE_DOCTOR_ROWS: the table is never read; at most
    ENSEMBLE_BUILD_HOLDOUT_ROWS holdout rows (a splitmix64 sample, in __row_id order) are parsed by __row_id, typed as the
    whole read types them. These callers (importance, the threshold, the monotone check, latency, reason-code and recourse
    checks) are diagnostics; the metrics over every holdout row are the run's and the version's batched scoring. None for
    a smaller table (read whole, as before)."""
    from .. import config, frames
    from ..lake import frame as LF

    if not parquet_ref(ref) or frames.lake_table(ref).n_rows <= config.doctor_rows():
        return None
    ids, ok = read_ids(ref, label)
    pos = np.asarray(splits["holdout"], dtype="int64")
    pos = pos[ok[pos]]
    h_ids = np.sort(ids[pos])
    h_ids = h_ids[keep_at_most(h_ids, int(cap or config.build_holdout_rows()), seed, 1)]
    cols = [c for c in table_columns(ref) if c == label or c not in set(drop or [])]
    return LF.read_rows(ref, h_ids, cols, nulls_plan(ref))


def bounded_train(cfg: dict[str, Any], columns: list[str], cap: int | None = None) -> pd.DataFrame | None:
    """F-X5a: the training rows (folds ≥ 0 with a label) of a run on a Parquet table above ENSEMBLE_DOCTOR_ROWS, as a
    splitmix64 sample of at most `cap` rows (default ENSEMBLE_DOCTOR_ROWS), `columns` only; None for a smaller table."""
    from .. import config, frames
    from ..lake import frame as LF

    ref = frames.resolve(cfg)
    if not parquet_ref(ref) or frames.lake_table(ref).n_rows <= config.doctor_rows():
        return None
    cap = int(cap or config.doctor_rows())
    try:
        rr = RunRows(cfg, ref)
    except Exception:  # noqa: BLE001 — no label / no split (anomaly, forecast): the table's first `cap` rows
        df = frames.read(ref, rows=range(0, cap))
        return df[[c for c in columns if c in df.columns]]
    t = rr.train_ids()
    t = t[keep_at_most(t, cap, rr.seed, _SALT_TRAIN)]
    have = set(table_columns(ref))
    from ..labels.overlay import apply_dict

    return apply_dict(LF.read_rows(ref, t, [c for c in columns if c in have], rr.plan), rr.label, rr.overlay)
