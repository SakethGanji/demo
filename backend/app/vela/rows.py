"""F-X5a (X5 K2): the rows the Model Doctor, the gap analysis and Vela's findings read, bounded.

They read the whole table (`frames.read`) and the whole holdout_predictions.csv. At 10M rows that took the heavy pool
down at 7–10 GB, after every fit (warm_doctor). Here a table of at most `ENSEMBLE_DOCTOR_ROWS` (200,000) rows is read
whole, exactly as before. A bigger Parquet table is read as a recorded splitmix64 sample:

- the holdout: the rows of holdout_predictions.csv kept at rate (cap / 2) / n_holdout (their own stream);
- the training rows (folds ≥ 0): kept at rate (cap / 2) / n_train;
- only those rows are parsed (by __row_id, typed as the whole table reads), and the split is given back as a positional
  split over them (`fold`, `holdout`), so the checks run unchanged on the sample.

`info` says what was sampled (rows, of, seed, method); bootstrap standard errors on the holdout sample are scaled back to
the whole holdout by √(m / n).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import config, frames

_SALT_HOLDOUT = 11
_SALT_TRAIN = 12


class Bounded:
    def __init__(self, df: pd.DataFrame, sp: dict[str, Any], hp: pd.DataFrame | None, hp_n: int, info: dict[str, Any] | None):
        self.df, self.sp, self.hp, self.hp_n, self.info = df, sp, hp, hp_n, info

    @property
    def sampled(self) -> bool:
        return self.info is not None

    def se_scale(self) -> float:
        """√(m / n): a bootstrap SE on the holdout sample of m rows, as the SE over all n holdout rows."""
        if not self.sampled or self.hp is None or not self.hp_n:
            return 1.0
        return float(np.sqrt(len(self.hp) / self.hp_n))


def _big(ref: Any) -> bool:
    from ..engine import serving

    return serving.parquet_ref(ref) and frames.lake_table(ref).n_rows > config.doctor_rows()


def bound_hp(hp: pd.DataFrame | None, cap: int, seed: int = 42) -> pd.DataFrame | None:
    """At most about `cap` rows of a holdout_predictions frame (splitmix64 of `row`, the __row_id)."""
    from ..engine import serving

    if hp is None or len(hp) <= cap:
        return hp
    keep = serving.keep_at_most(hp["row"].to_numpy().astype("int64"), cap, seed, _SALT_HOLDOUT)
    return hp.loc[keep].reset_index(drop=True)


def load(cfg: dict[str, Any], hp: pd.DataFrame | None, *, need_frame: bool = True) -> Bounded:
    """The run's table (or its bounded sample), its split over those rows, and the holdout predictions on them."""
    ref = frames.resolve(cfg)
    if not _big(ref):
        df = frames.read(ref) if need_frame else pd.DataFrame()
        sp = json.loads(Path(cfg["splits_path"]).read_text())
        return Bounded(df, sp, hp, 0 if hp is None else len(hp), None)
    from ..engine import serving

    cap = config.doctor_rows()
    rr = serving.RunRows(cfg, ref)
    seed = rr.seed
    hp_b = bound_hp(hp, cap // 2, seed)
    hold_all = rr.ids[rr.fold == -1]
    h_ids = hp_b["row"].to_numpy().astype("int64") if hp_b is not None else \
        hold_all[serving.keep_at_most(hold_all, cap // 2, seed, _SALT_HOLDOUT)]
    tr_all = rr.ids[rr.fold >= 0]
    t_keep = serving.keep_at_most(tr_all, cap // 2, seed, _SALT_TRAIN)
    t_ids = tr_all[t_keep]
    want = np.unique(np.concatenate([h_ids, t_ids]))
    if need_frame:
        df = rr.read(want, serving.table_columns(ref))  # typed as the whole read, column types cast as the fit did
    else:
        df = pd.DataFrame(index=pd.Index(want))
    is_h = np.isin(df.index.to_numpy(), h_ids)
    pos = np.searchsorted(np.sort(rr.ids), df.index.to_numpy())
    order = np.argsort(rr.ids, kind="stable")
    fold_by_id = rr.fold[order]
    fold = fold_by_id[pos].copy()
    fold[is_h] = -1
    sp = {"fold": fold.tolist(), "holdout": np.flatnonzero(is_h).tolist(), "n_folds": int(max(0, fold.max()) + 1) if len(fold) else 0,
          "sampled": True}
    info = {"sampled": True, "rows": int(len(df)), "rows_total": int(rr.n_rows), "seed": int(seed),
            "holdout": {"rows": int(len(h_ids)), "of": int(len(hp) if hp is not None else len(hold_all))},
            "train": {"rows": int(len(t_ids)), "of": int(len(tr_all))},
            "method": (f"the table has {rr.n_rows:,} rows (> ENSEMBLE_DOCTOR_ROWS {cap:,}): a splitmix64 sample of the holdout "
                       f"({len(h_ids):,} of {int(len(hp) if hp is not None else len(hold_all)):,}) and of the training rows "
                       f"({len(t_ids):,} of {len(tr_all):,}), read by __row_id; standard errors scaled to the whole holdout")}
    return Bounded(df, sp, hp_b, 0 if hp is None else len(hp), info)


def sample_table(ref: Any, cap: int | None = None, seed: int = 42) -> pd.DataFrame:
    """The table, or above `cap` (ENSEMBLE_DOCTOR_ROWS) rows a splitmix64 sample of about `cap` rows, read by __row_id."""
    from ..engine import serving
    from ..lake import frame as LF

    cap = int(cap or config.doctor_rows())
    if not _big(ref) or frames.lake_table(ref).n_rows <= cap:
        return frames.read(ref)
    ids, _ = serving.read_ids(ref)
    ids = np.sort(ids)
    keep = serving.keep_at_most(ids, cap, seed, _SALT_TRAIN)
    return LF.read_rows(ref, ids[keep], serving.table_columns(ref), serving.nulls_plan(ref))
