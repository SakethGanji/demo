"""The fixed holdout and the 5 folds every model trains on (plan §2: identical folds for a fair leaderboard).

A split, as JSON (the DL4 file; for a Parquet version a positional view of its `splits` row, P09):

    {"seed": 42, "target": "category", "stratified": true, "holdout_share": 0.2, "n_folds": 5,
     "holdout": [row, ...],                 # sorted row positions held out for the final score
     "fold": [f, ...]}                      # one entry per row: 0..4 for training rows, -1 for holdout

Use `load_splits(path)` and `fold_indices(splits, k)` from the engine; never re-split.

F4: a split belongs to a target, not to a dataset. Splits are stored per (target, strategy) under
`data/datasets/<id>/splits/<key>.json` (`split_key`), each recording its own `target`, `strategy` and `key`, and are
drawn once, the first time that target is used (confirmed in the UI or named by a run). Changing the target never
changes another target's split; every run records the key and SHA-256 of the split it used. The only strategy so far
was "random-stratified" (stratified on the target when every class has ≥ n_folds + 1 rows, else random).

F6 adds three strategies, each keyed by its parameters too (`split_key`):
  group       an entity column (customer id): holdout, tuning slice and the 5 bagging folds are group-disjoint;
              the folds go to AutoGluon as `groups=` (a derived fold-id column, LeaveOneGroupOut over 5 values).
  time        a date column and a cut-off: holdout = rows on or after the cut-off (default: the latest 20 %),
              tuning = the latest slice of the training period (fit(tuning_data=...)), train = the rest.
  time-group  forward in time and new customers only: holdout = rows after the cut-off of customers with no row
              before it (rows after the cut-off of known customers are excluded), tuning likewise inside the
              training period, bagging folds group-disjoint.
`fold` codes: 0..4 train fold, -1 holdout, -2 excluded, -3 tuning. The pre-F4 split drawn on upload for the profiler's
target is kept unchanged, because runs made before F4 point at it (P10 migrates it with its version).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import KFold, ShuffleSplit, StratifiedGroupKFold, StratifiedKFold, StratifiedShuffleSplit


RANDOM_STRATIFIED = "random-stratified"
GROUP = "group"
TIME = "time"
TIME_GROUP = "time-group"
STRATEGIES = (RANDOM_STRATIFIED, GROUP, TIME, TIME_GROUP)

# Fold codes (F6). 0..n_folds-1: a training row's bagging fold. Rows with a negative code are not in train_data.
HOLDOUT = -1    # the final holdout (also listed in "holdout")
EXCLUDED = -2   # time + group: a row after the cut-off from a customer already seen before it; in no set
TUNING = -3     # group / time strategies: the tuning slice, given to fit(tuning_data=...)

DEFAULT_TUNING_SHARE = 0.2  # share of the training period (time) or of the training groups (group) used for tuning


def needs_group(strategy: str) -> bool:
    return strategy in (GROUP, TIME_GROUP)


def needs_time(strategy: str) -> bool:
    return strategy in (TIME, TIME_GROUP)


def normalize_params(strategy: str, params: dict[str, Any] | None) -> dict[str, Any]:
    """The parameters that define a split of this strategy (and go into its key). Random has none."""
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown split strategy '{strategy}'. Use one of: {', '.join(STRATEGIES)}.")
    params = dict(params or {})
    out: dict[str, Any] = {}
    if needs_group(strategy):
        if not params.get("group_column"):
            raise ValueError(f"The {strategy} split needs group_column (the entity, e.g. a customer id).")
        out["group_column"] = str(params["group_column"])
    if needs_time(strategy):
        if not params.get("time_column"):
            raise ValueError(f"The {strategy} split needs time_column (a date column).")
        out["time_column"] = str(params["time_column"])
        out["cutoff"] = None if params.get("cutoff") in (None, "") else str(params["cutoff"])
    if strategy != RANDOM_STRATIFIED:
        ts = params.get("tuning_share")
        ts = DEFAULT_TUNING_SHARE if ts is None else float(ts)
        if not 0.05 <= ts <= 0.5:
            raise ValueError("tuning_share must be between 0.05 and 0.5.")
        out["tuning_share"] = ts
    return out


def split_key(target: str | None, strategy: str = RANDOM_STRATIFIED, params: dict[str, Any] | None = None) -> str:
    """File-safe key of a (target, strategy[, parameters]) split: readable slug + hash of the exact column name (names
    can collide once slugged, e.g. "a b" and "a_b") and, for group / time splits, of the parameters (group and time
    column, cut-off, tuning share), so a different cut-off is a different split. Random keys are unchanged from F4."""
    params = normalize_params(strategy, params)
    if target is None:
        return f"{strategy}__no-target" + (f"-{_hash(json.dumps(params, sort_keys=True))}" if params else "")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", target).strip("-")[:40] or "col"
    ident = target if not params else target + "\x1f" + json.dumps(params, sort_keys=True)
    return f"{strategy}__{slug}-{_hash(ident)}"


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:8]


def split_sha256(splits: dict[str, Any]) -> str:
    """Identity of a split: SHA-256 of its holdout rows and fold assignment (not of the JSON's other fields)."""
    return hashlib.sha256(json.dumps([splits["holdout"], splits["fold"]]).encode()).hexdigest()


def _stratifiable(labels: np.ndarray | None, n_folds: int) -> bool:
    if labels is None:
        return False
    counts = pd.Series(labels).value_counts()
    return len(counts) >= 2 and int(counts.min()) >= n_folds + 1 and len(counts) <= 50


def make_splits(y: pd.Series | None, n_rows: int, seed: int, holdout_share: float, n_folds: int,
                strategy: str = RANDOM_STRATIFIED, frame: pd.DataFrame | None = None,
                params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Draw a split. Random (stratified) needs only y; group / time / time + group read their columns from `frame`
    (the whole data.csv) and the parameters (group_column, time_column, cutoff, tuning_share)."""
    params = normalize_params(strategy, params)
    if strategy != RANDOM_STRATIFIED:
        if frame is None:
            raise ValueError(f"The {strategy} split needs the data frame.")
        return _structured_split(y, frame, seed, holdout_share, n_folds, strategy, params)
    idx = np.arange(n_rows)
    n_train = n_rows - int(np.ceil(n_rows * holdout_share))
    if n_train < n_folds or n_rows < 2:  # DL3 (m4): say it in words, not as sklearn's n_splits error
        need = int(np.ceil(n_folds / (1 - holdout_share))) + 1
        raise ValueError(f"This file has {n_rows} data row(s): after setting {holdout_share:.0%} aside as the holdout, {max(0, n_train)} "
                         f"are left, and at least {n_folds} (one per cross-validation fold) are needed. Upload at least {need} rows "
                         "(a model needs far more to be useful).")
    labels = None if y is None else y.astype(str).fillna("").to_numpy()
    # Stratify when every class can appear in the holdout and in each fold.
    stratified = labels is not None and pd.Series(labels).nunique() >= 2 and \
        int(pd.Series(labels).value_counts().min()) >= n_folds + 1

    splitter = (StratifiedShuffleSplit if stratified else ShuffleSplit)(n_splits=1, test_size=holdout_share, random_state=seed)
    train, holdout = next(splitter.split(idx, labels if stratified else None))

    fold = np.full(n_rows, -1, dtype=int)
    kf = (StratifiedKFold if stratified else KFold)(n_splits=n_folds, shuffle=True, random_state=seed)
    for k, (_, val) in enumerate(kf.split(train, labels[train] if stratified else None)):
        fold[train[val]] = k

    target = None if y is None else str(y.name)
    return {
        "seed": seed,
        "target": target,
        "strategy": strategy,
        "key": split_key(target, strategy),
        "stratified": bool(stratified),
        "holdout_share": holdout_share,
        "n_folds": n_folds,
        "holdout": sorted(int(i) for i in holdout),
        "fold": fold.tolist(),
    }


# --------------------------------------------------------------------------- group and time (F6)


def time_values(s: pd.Series) -> pd.Series:
    """A column as comparable time values: numbers stay numbers (a year, a period index), strings are parsed as
    dates. Unparseable values become NaT / NaN."""
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_numeric(s, errors="coerce")
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    return pd.to_datetime(s, errors="coerce", format="mixed")


def date_and_group_columns(df: pd.DataFrame, target: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """F6 (and G0 Smart Intake): the date columns (>= 95 % of values read as dates, at least 10 distinct) and the
    entity columns (ID-like name or a high-cardinality category whose values repeat: several rows per customer)."""
    n = len(df)
    dates, groups = [], []
    for c in df.columns:
        if c == target:
            continue
        s = df[c]
        nn = s.dropna()
        if len(nn) == 0:
            continue
        if s.dtype == object:
            sample = nn.astype(str).head(500)
            # A2 M1: also "YYYY-MM" (a month) and compact ISO ("20141013", "20141013T000000")
            looks = (sample.str.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}") | sample.str.match(r"^\d{1,2}[-/]\d{1,2}[-/]\d{4}")
                     | sample.str.fullmatch(r"\d{4}[-/](0?[1-9]|1[0-2])") | sample.str.fullmatch(r"(19|20)\d{6}(T\d{4,6}(\.\d+)?Z?)?"))
            if looks.mean() >= 0.95:
                t = time_values(s)
                if t.notna().mean() >= 0.95 and t.nunique() >= 10:
                    dates.append({"column": c, "from": _fmt_time(t.min()), "to": _fmt_time(t.max()),
                                  "distinct": int(t.nunique()), "parsed_share": float(t.notna().mean())})
                    continue
        k = int(nn.nunique())
        per = len(nn) / max(k, 1)
        id_name = bool(re.search(r"(^id$|_id$|^id_|uuid|^key$|(customer|account|client|member)(_?(id|no|number|key|ref))?$)", str(c), re.I))
        if per > 1.05 and k >= 20 and (id_name or (s.dtype == object and k >= max(50, 0.05 * n))):
            groups.append({"column": c, "groups": k, "rows_per_group": round(per, 2),
                           "max_rows": int(nn.value_counts().iloc[0]), "id_like_name": id_name})
    groups.sort(key=lambda g: (not g["id_like_name"], -g["rows_per_group"]))
    return dates, groups


def _fmt_time(v: Any) -> Any:
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
        return None
    if isinstance(v, (pd.Timestamp, np.datetime64)):
        ts = pd.Timestamp(v)
        return ts.date().isoformat() if ts == ts.normalize() else ts.isoformat()
    return float(v) if isinstance(v, (float, np.floating)) else int(v) if isinstance(v, (int, np.integer)) else v


def _parse_cutoff(cutoff: str, t: pd.Series) -> Any:
    if pd.api.types.is_numeric_dtype(t):
        try:
            return float(cutoff)
        except ValueError as e:
            raise ValueError(f"cutoff '{cutoff}' is not a number, and the time column is numeric.") from e
    c = pd.to_datetime(cutoff, errors="coerce")
    if pd.isna(c):
        raise ValueError(f"cutoff '{cutoff}' is not a date (use YYYY-MM-DD).")
    return c


def _group_folds(groups: np.ndarray, labels: np.ndarray | None, n_splits: int, seed: int) -> np.ndarray:
    """Fold id (0..n_splits-1) per row with every group in exactly one fold: StratifiedGroupKFold when the label
    can be stratified, else whole groups dealt at random into folds of about equal row counts."""
    n = len(groups)
    fold = np.full(n, -1, dtype=int)
    if n == 0:
        return fold
    n_groups = pd.Series(groups).nunique()
    n_splits = max(2, min(n_splits, n_groups))
    if _stratifiable(labels, n_splits):
        sgkf = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=seed)
        for k, (_, val) in enumerate(sgkf.split(np.zeros(n), labels, groups)):
            fold[val] = k
        return fold
    rng = np.random.default_rng(seed)
    uniq, inv, counts = np.unique(groups, return_inverse=True, return_counts=True)
    order = rng.permutation(len(uniq))
    load = np.zeros(n_splits)
    gfold = np.empty(len(uniq), dtype=int)
    for g in order:  # the next group goes to the fold with the fewest rows so far
        k = int(np.argmin(load))
        gfold[g] = k
        load[k] += counts[g]
    return gfold[inv]


def _time_blocks(t: pd.Series, n_blocks: int) -> np.ndarray:
    """Contiguous time blocks of about equal row counts (ties stay in one block)."""
    if len(t) == 0:
        return np.zeros(0, dtype=int)
    r = t.rank(method="min").to_numpy()
    return np.minimum(((r - 1) * n_blocks // len(t)).astype(int), n_blocks - 1)


def _structured_split(y: pd.Series | None, frame: pd.DataFrame, seed: int, holdout_share: float, n_folds: int,
                      strategy: str, params: dict[str, Any]) -> dict[str, Any]:
    n = len(frame)
    target = None if y is None else str(y.name)
    labels = None if y is None else y.astype(str).fillna("").to_numpy()
    fold = np.full(n, -99, dtype=int)
    gcol, tcol = params.get("group_column"), params.get("time_column")
    tuning_share = params["tuning_share"]
    for c in (gcol, tcol):
        if c is not None and c not in frame.columns:
            raise ValueError(f"Column '{c}' is not in the dataset.")
        if c is not None and c == target:
            raise ValueError(f"'{c}' is the target; split on another column.")
    groups = None
    if gcol:
        groups = frame[gcol].astype(str).where(frame[gcol].notna(), "__missing__").to_numpy()
    t = None
    resolved_cutoff = tuning_cutoff = None
    if tcol:
        t = time_values(frame[tcol])
        ok = t.notna().to_numpy()
        if ok.mean() < 0.9:
            raise ValueError(f"Only {ok.mean():.0%} of '{tcol}' could be read as dates or numbers; "
                             "a time split needs a date column.")
        fold[~ok] = EXCLUDED  # no date: cannot be placed before or after the cut-off
        if params.get("cutoff"):
            resolved_cutoff = _parse_cutoff(params["cutoff"], t)
        else:  # about holdout_share of the dated rows on or after the cut-off
            resolved_cutoff = t[ok].sort_values().iloc[int(np.floor((1 - holdout_share) * ok.sum()))]
        after = ok & (t >= resolved_cutoff).to_numpy()
        before = ok & ~after
        if before.sum() < 10 or after.sum() < 10:
            raise ValueError(f"The cut-off {_fmt_time(resolved_cutoff)} leaves {int(before.sum())} rows before and "
                             f"{int(after.sum())} after it; each side needs at least 10.")

    if strategy == GROUP:
        n_hold = max(2, int(round(1 / holdout_share)))
        hf = _group_folds(groups, labels, n_hold, seed)
        fold[hf == 0] = HOLDOUT
        rest = np.flatnonzero(hf != 0)
        n_tune = max(2, int(round(1 / tuning_share)))
        tf = _group_folds(groups[rest], None if labels is None else labels[rest], n_tune, seed + 1)
        fold[rest[tf == 0]] = TUNING
    elif strategy == TIME:
        fold[after] = HOLDOUT
        tb = np.flatnonzero(before)
        tt = t.iloc[tb].reset_index(drop=True)
        tuning_cutoff = tt.sort_values().iloc[int(np.floor((1 - tuning_share) * len(tt)))]
        fold[tb[(tt >= tuning_cutoff).to_numpy()]] = TUNING
    else:  # TIME_GROUP: forward in time and new customers only
        seen = set(groups[before])
        new_after = after & ~np.isin(groups, list(seen))
        fold[new_after] = HOLDOUT
        fold[after & ~new_after] = EXCLUDED
        tb = np.flatnonzero(before)
        tt = t.iloc[tb].reset_index(drop=True)
        tuning_cutoff = tt.sort_values().iloc[int(np.floor((1 - tuning_share) * len(tt)))]
        late = (tt >= tuning_cutoff).to_numpy()
        seen_early = set(groups[tb[~late]])
        tune = late & ~np.isin(groups[tb], list(seen_early))
        fold[tb[tune]] = TUNING
        if (fold == HOLDOUT).sum() < 10:
            raise ValueError(f"Only {int((fold == HOLDOUT).sum())} rows after the cut-off belong to customers not seen "
                             "before it; move the cut-off earlier or use the time split.")

    train = np.flatnonzero(fold == -99)
    if strategy == TIME:  # bagging folds (if the fit bags): contiguous time blocks
        fold[train] = _time_blocks(t.iloc[train].reset_index(drop=True), n_folds)
    else:  # group-disjoint folds, passed to AutoGluon as groups=
        fold[train] = _group_folds(groups[train], None if labels is None else labels[train], n_folds, seed)

    holdout = np.flatnonzero(fold == HOLDOUT)
    out = {
        "seed": seed, "target": target, "strategy": strategy, "params": params,
        "key": split_key(target, strategy, params),
        "stratified": bool(strategy == GROUP and _stratifiable(labels, n_folds)),
        "holdout_share": holdout_share, "n_folds": n_folds,
        "holdout": [int(i) for i in holdout],
        "tuning": [int(i) for i in np.flatnonzero(fold == TUNING)],
        "excluded": [int(i) for i in np.flatnonzero(fold == EXCLUDED)],
        "fold": fold.tolist(),
    }
    if t is not None:
        out["cutoff"] = _fmt_time(resolved_cutoff)
        out["tuning_cutoff"] = _fmt_time(tuning_cutoff)
    out["diagram"] = _diagram(fold, labels, groups, t, n_folds)
    return out


def _diagram(fold: np.ndarray, labels: np.ndarray | None, groups: np.ndarray | None, t: pd.Series | None,
             n_folds: int) -> dict[str, Any]:
    """What the UI draws: rows (and groups, label rate, time range) per role, and, for time splits, rows per period."""
    roles = {"train": fold >= 0, "tuning": fold == TUNING, "holdout": fold == HOLDOUT, "excluded": fold == EXCLUDED}
    pos = None
    if labels is not None:
        vc = pd.Series(labels).value_counts()
        if len(vc) == 2:
            pos = str(vc.index[-1])  # the rarer class
    d: dict[str, Any] = {"roles": {}, "positive_label": pos}
    for name, m in roles.items():
        r: dict[str, Any] = {"rows": int(m.sum())}
        if groups is not None:
            r["groups"] = int(pd.Series(groups[m]).nunique())
        if pos is not None and m.any():
            r["positive_rate"] = float((labels[m] == pos).mean())
        if t is not None and m.any():
            tm = t[m]
            r["from"], r["to"] = _fmt_time(tm.min()), _fmt_time(tm.max())
        d["roles"][name] = r
    if groups is not None:
        sets = {k: set(groups[m]) for k, m in roles.items()}
        d["shared_groups"] = {"train_holdout": len(sets["train"] & sets["holdout"]),
                              "train_tuning": len(sets["train"] & sets["tuning"]),
                              "tuning_holdout": len(sets["tuning"] & sets["holdout"])}
        per = pd.Series(groups).value_counts()
        d["rows_per_group"] = {"mean": float(per.mean()), "max": int(per.max()),
                               "hist": {str(k): int(v) for k, v in per.clip(upper=6).value_counts().sort_index().items()}}
        d["fold_groups"] = [int(pd.Series(groups[fold == k]).nunique()) for k in range(n_folds)]
    if t is not None:
        ok = t.notna().to_numpy()
        if pd.api.types.is_datetime64_any_dtype(t):
            span_days = (t[ok].max() - t[ok].min()).days
            freq = "M" if span_days > 120 else "W" if span_days > 21 else "D"
            per = t.dt.to_period(freq).astype(str)
        else:
            per = pd.cut(t, bins=min(24, max(2, t[ok].nunique())), duplicates="drop").astype(str)
        rows = []
        for p_ in sorted(per[ok].unique()):
            m = (per == p_).to_numpy() & ok
            row: dict[str, Any] = {"period": str(p_)}
            for name, rm in roles.items():
                row[name] = int((m & rm).sum())
            if pos is not None:
                row["positive_rate"] = float((labels[m] == pos).mean())
            rows.append(row)
        d["timeline"] = rows
    return d


def summarize(splits: dict[str, Any], y: pd.Series | None) -> dict[str, Any]:
    fold = np.asarray(splits["fold"])
    out: dict[str, Any] = {
        "seed": splits["seed"],
        "target": splits["target"],
        "strategy": splits.get("strategy", RANDOM_STRATIFIED),
        "key": splits.get("key") or split_key(splits["target"], splits.get("strategy", RANDOM_STRATIFIED)),
        "sha256": split_sha256(splits),
        "stratified": splits["stratified"],
        "holdout_share": splits["holdout_share"],
        "holdout_rows": len(splits["holdout"]),
        "n_folds": splits["n_folds"],
        "fold_rows": [int((fold == k).sum()) for k in range(splits["n_folds"])],
    }
    if splits.get("params"):  # F6: group / time / time + group
        out["params"] = splits["params"]
        out["train_rows"] = int((fold >= 0).sum())
        out["tuning_rows"] = len(splits.get("tuning") or [])
        out["excluded_rows"] = len(splits.get("excluded") or [])
        for k in ("cutoff", "tuning_cutoff", "diagram"):
            if k in splits:
                out[k] = splits[k]
    if y is not None:
        ys = y.astype(str).to_numpy()
        out["holdout_classes"] = {str(k): int(v) for k, v in pd.Series(ys[splits["holdout"]]).value_counts().items()}
    return out


def save_splits(path: Path, splits: dict[str, Any]) -> None:
    path.write_text(json.dumps(splits))


def load_splits(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def holdout_mask(splits: dict[str, Any], n: int) -> np.ndarray:
    """Boolean mask over the n rows of data.csv: True on the fixed holdout rows."""
    m = np.zeros(n, dtype=bool)
    m[np.asarray(splits["holdout"], dtype=int)] = True
    return m


def fold_indices(splits: dict[str, Any], k: int) -> tuple[np.ndarray, np.ndarray]:
    """Row positions (train, validation) for fold k. Holdout rows are in neither."""
    fold = np.asarray(splits["fold"])
    return np.flatnonzero((fold >= 0) & (fold != k)), np.flatnonzero(fold == k)
