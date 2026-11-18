"""F11: the scorecard challenger: binning → weight of evidence → logistic regression → points.

`ScorecardModel` (key `SCORECARD`, name `Scorecard`) is the classic credit scorecard as an AutoGluon model, so it
trains in the same folds, on the same features, and is scored on the same holdout as every other model. It is binary
only, and is never a stacker (`valid_stacker=False`): a scorecard reads applicant data, not other models' outputs.

Everything below is fitted inside `_fit`, i.e. on the rows AutoGluon gives the model: in a bagged run each fold child
bins on its own training folds only, so no out-of-fold or holdout row ever shapes a bin edge or a WoE value.

Notation (y = 1 is AutoGluon's positive class, the "event", e.g. default; y = 0 is a non-event):

* **Binning.** A numeric column is cut at quantiles into `fine_bins` fine bins. Adjacent bins are then merged
  (i) until every bin holds at least `min_bin_share` of the rows, (ii) until the event rate is monotone in x, in the
  direction of the column's F8 monotone constraint if it has one, else the direction that fits the fine bins best
  (`monotone_bins="auto"`; `"none"` skips this), and (iii) until there are at most `max_bins` bins, always merging the
  adjacent pair with the closest event rates. Missing values get their own bin. A category keeps one bin per level;
  levels under `min_bin_share` share an "other" bin, and levels are merged by event rate down to `max_bins`.
* **WoE.** For bin i with gᵢ non-events and bᵢ events (G, B the totals, k bins, α = 0.5 smoothing):
  WoEᵢ = ln( ((gᵢ + α) / (G + kα)) / ((bᵢ + α) / (B + kα)) ). High WoE = safer than average.
  IVⱼ = Σᵢ (gᵢ/G − bᵢ/B) · WoEᵢ (unsmoothed shares, smoothed WoE). Features with IV < `min_iv` are left out.
* **Logistic regression** on the WoE columns: logit P(y = 1) = β₀ + Σⱼ βⱼ·WoEⱼ(xⱼ). With this WoE sign every βⱼ
  should be ≤ 0 (more WoE, less risk). A positive βⱼ (collinearity) would make points run against the bins, so the
  feature with the largest positive βⱼ is dropped and the model refitted until every βⱼ ≤ 0 (`enforce_sign`).
* **Points.** factor = PDO / ln 2, offset = base_score − factor·ln(base_odds). The score is
  offset + factor·ln(odds of a non-event) = offset − factor·(β₀ + Σⱼ βⱼ·WoEⱼ), and it splits into points per bin:
  pointsᵢⱼ = −factor·βⱼ·WoEᵢⱼ + (offset − factor·β₀)/n, n = the features in the scorecard. Doubling the odds of a
  non-event adds PDO points; base_score points means odds of base_odds : 1.
  P(y = 1) = 1 / (1 + exp((score − offset)/factor)), exactly the logistic regression's probability.

**Monotone constraints (F8).** A column constrained ↑ (P(event) non-decreasing in x) is binned with a non-decreasing
event rate, so WoE is non-increasing in x; with βⱼ ≤ 0, βⱼ·WoEⱼ is non-decreasing, so P(event) is too. ↓ is the mirror.
The model records `monotone_applied` = {feature: sign} like the F8 subclasses, so the run's monotone report counts it
as monotone by construction.

**Reason codes (F10).** The points shortfall of feature j, maxᵢ pointsᵢⱼ − pointsⱼ(x), is how many points the
applicant lost on j against the best bin; ranking it is the scorecard's own adverse-action method (`reasons`).
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import pandas as pd
from autogluon.common.features.types import (R_BOOL, R_CATEGORY, R_FLOAT, R_INT, S_DATETIME_AS_INT, S_IMAGE_PATH,
                                             S_STACK, S_TEXT_AS_CATEGORY, S_TEXT_NGRAM, S_TEXT_SPECIAL)
from autogluon.core.constants import BINARY
from autogluon.core.models import AbstractModel

log = logging.getLogger(__name__)

ALPHA = 0.5
MISSING = "missing"
OTHER = "other"
DEFAULTS = dict(fine_bins=20, max_bins=6, min_bin_share=0.05, monotone_bins="auto", min_iv=0.02, C=1.0,
                max_iter=1000, enforce_sign=True, pdo=20.0, base_score=600.0, base_odds=50.0)
SCALING = ("pdo", "base_score", "base_odds")


# --------------------------------------------------------------------------- binning (plain numpy / pandas)


def _woe(good: np.ndarray, bad: np.ndarray) -> np.ndarray:
    k = len(good)
    G, B = good.sum(), bad.sum()
    return np.log(((good + ALPHA) / (G + k * ALPHA)) / ((bad + ALPHA) / (B + k * ALPHA)))


def _rate(c: list[float]) -> float:
    """The smoothed event rate (b + α)/(n + 2α). WoE is a decreasing function of it, so a bin order monotone in this
    rate is exactly monotone in the (smoothed) WoE."""
    return (c[1] + ALPHA) / (c[0] + 2 * ALPHA)


def _merge(counts: list[list[float]], i: int) -> None:
    """Merge bin i+1 into bin i (counts[i] = [n, events])."""
    counts[i] = [counts[i][0] + counts[i + 1][0], counts[i][1] + counts[i + 1][1]]
    del counts[i + 1]


def _direction(n: np.ndarray, e: np.ndarray) -> int:
    """+1 if the event rate rises with x on the fine bins, else -1: the sign of the weighted correlation of the bin
    index with the bin's event rate."""
    if len(n) < 2 or n.sum() == 0:
        return 1
    r = e / np.maximum(n, 1)
    idx = np.arange(len(n), dtype=float)
    w = n / n.sum()
    cov = float(np.sum(w * (idx - np.sum(w * idx)) * (r - np.sum(w * r))))
    return 1 if cov >= 0 else -1


def bin_numeric(x: np.ndarray, y: np.ndarray, *, fine_bins: int, max_bins: int, min_bin_share: float,
                direction: int | None) -> tuple[list[float], int | None]:
    """Cut points (right-closed bins (-inf, c₁], (c₁, c₂], …, (c_k, inf)) for the non-missing x, and the direction the
    event rate was made monotone in (None: not made monotone)."""
    ok = ~np.isnan(x)
    x, y = x[ok], y[ok]
    if len(x) == 0:
        return [], None
    uniq = np.unique(x)
    if len(uniq) <= fine_bins:
        cuts = list(uniq[:-1])
    else:
        q = np.quantile(x, np.linspace(0, 1, fine_bins + 1)[1:-1], method="higher")
        cuts = [c for c in np.unique(q) if c < uniq[-1]]
    if not cuts:
        return [], None
    idx = np.searchsorted(np.asarray(cuts), x, side="left")  # bin of x: number of cuts strictly below x
    nb = len(cuts) + 1
    n = np.bincount(idx, minlength=nb).astype(float)
    e = np.bincount(idx, weights=y, minlength=nb).astype(float)
    counts = [[a, b] for a, b in zip(n, e)]
    edges = list(cuts)  # edges[i] separates counts[i] and counts[i+1]

    def merge(i: int) -> None:
        _merge(counts, i)
        del edges[i]

    total = len(x)
    # (i) small bins: merge the smallest bin with its neighbour of closer event rate
    while len(counts) > 1:
        sizes = [c[0] for c in counts]
        j = int(np.argmin(sizes))
        if sizes[j] >= min_bin_share * total:
            break
        if j == 0:
            merge(0)
        elif j == len(counts) - 1:
            merge(j - 1)
        else:
            r = [_rate(c) for c in counts]
            merge(j - 1 if abs(r[j] - r[j - 1]) <= abs(r[j] - r[j + 1]) else j)
    # (ii) monotone event rate (pool adjacent violators)
    d = None
    if direction is not None and len(counts) > 1:
        d = direction if direction != 0 else _direction(np.array([c[0] for c in counts]), np.array([c[1] for c in counts]))
        while len(counts) > 1:
            r = [_rate(c) for c in counts]
            bad = [i for i in range(len(r) - 1) if d * (r[i + 1] - r[i]) < 0]
            if not bad:
                break
            merge(bad[0])
    # (iii) at most max_bins: merge the adjacent pair with the closest event rates
    while len(counts) > max(1, max_bins):
        r = [_rate(c) for c in counts]
        merge(int(np.argmin([abs(r[i + 1] - r[i]) for i in range(len(r) - 1)])))
    return [float(c) for c in edges], d


def bin_categorical(x: pd.Series, y: np.ndarray, *, max_bins: int, min_bin_share: float) -> dict[str, int]:
    """Level → bin id. Rare levels (share < min_bin_share) share the "other" bin; bins (ordered by event rate) are
    merged with their closest neighbour by event rate down to max_bins."""
    s = x.astype(object).where(x.notna(), None)
    ok = s.notna().to_numpy()
    lv = s[ok].astype(str).to_numpy()
    yy = y[ok]
    total = len(lv)
    if total == 0:
        return {}
    levels, inv = np.unique(lv, return_inverse=True)
    n = np.bincount(inv).astype(float)
    e = np.bincount(inv, weights=yy).astype(float)
    groups: list[list[str]] = []
    gc: list[list[float]] = []
    rare_l, rare_n, rare_e = [], 0.0, 0.0
    for l, a, b in zip(levels, n, e):
        if a < min_bin_share * total:
            rare_l.append(str(l))
            rare_n += a
            rare_e += b
        else:
            groups.append([str(l)])
            gc.append([a, b])
    if rare_l:
        groups.append([OTHER, *rare_l])
        gc.append([rare_n, rare_e])
    order = np.argsort([_rate(c) for c in gc], kind="stable")
    groups = [groups[i] for i in order]
    gc = [gc[i] for i in order]
    while len(gc) > max(1, max_bins):
        r = [_rate(c) for c in gc]
        i = int(np.argmin([abs(r[k + 1] - r[k]) for k in range(len(r) - 1)]))
        _merge(gc, i)
        groups[i] = groups[i] + groups[i + 1]
        del groups[i + 1]
    return {l: b for b, g in enumerate(groups) for l in g}


class FeatureBins:
    """One feature's bins: assignment of raw values to bins, counts, WoE and IV on the training rows."""

    def __init__(self, name: str, kind: str):
        self.name, self.kind = name, kind
        self.cuts: list[float] = []
        self.levels: dict[str, int] = {}
        self.direction: int | None = None
        self.constraint: int = 0
        self.labels: list[str] = []
        self.has_missing = False
        self.woe = np.zeros(0)
        self.n = np.zeros(0)
        self.events = np.zeros(0)
        self.iv = 0.0

    @property
    def k(self) -> int:
        return len(self.labels)

    def assign(self, x: pd.Series) -> np.ndarray:
        """Bin index per row; -1 = a value with no bin of its own (a missing value when training had none, an unseen
        level when there is no "other" bin): it gets WoE 0, the average applicant."""
        if self.kind == "numeric":
            v = pd.to_numeric(x, errors="coerce").to_numpy(dtype=float)
            out = np.searchsorted(np.asarray(self.cuts, dtype=float), v, side="left").astype(int) if self.cuts \
                else np.zeros(len(v), dtype=int)
            miss = np.isnan(v)
        else:
            s = x.astype(object)
            miss = s.isna().to_numpy()
            other = self.levels.get(OTHER, -1)
            out = np.asarray([self.levels.get(str(v), other) for v in s], dtype=int)
        out = np.where(miss, self.k - 1 if self.has_missing else -1, out)
        return out

    def woe_of(self, x: pd.Series) -> np.ndarray:
        b = self.assign(x)
        w = np.append(self.woe, 0.0)  # index -1 → 0
        return w[b]

    def fit(self, x: pd.Series, y: np.ndarray, p: dict[str, Any]) -> "FeatureBins":
        if self.kind == "numeric":
            v = pd.to_numeric(x, errors="coerce").to_numpy(dtype=float)
            direction = self.constraint if self.constraint else (0 if p["monotone_bins"] == "auto" else None)
            self.cuts, self.direction = bin_numeric(v, y, fine_bins=int(p["fine_bins"]), max_bins=int(p["max_bins"]),
                                                    min_bin_share=float(p["min_bin_share"]), direction=direction)
            nb = len(self.cuts) + 1
            self.labels = [_interval(self.cuts, i) for i in range(nb)]
            miss = np.isnan(v)
        else:
            self.levels = bin_categorical(x, y, max_bins=int(p["max_bins"]), min_bin_share=float(p["min_bin_share"]))
            nb = (max(self.levels.values()) + 1) if self.levels else 0
            groups: dict[int, list[str]] = {}
            for l, b in self.levels.items():
                groups.setdefault(b, []).append(l)
            self.labels = [", ".join(sorted(groups[b], key=lambda s: (s == OTHER, s))) for b in range(nb)]
            miss = x.isna().to_numpy()
        self.has_missing = bool(miss.any())
        if self.has_missing:
            self.labels.append(MISSING)
        b = self.assign(x)
        k = self.k
        self.n = np.bincount(b[b >= 0], minlength=k).astype(float)
        self.events = np.bincount(b[b >= 0], weights=y[b >= 0], minlength=k).astype(float)
        good, bad = self.n - self.events, self.events
        self.woe = _woe(good, bad)
        G, B = max(good.sum(), 1.0), max(bad.sum(), 1.0)
        self.iv = float(np.sum((good / G - bad / B) * self.woe))
        return self

    def numeric_order(self) -> list[int]:
        """The bins of a numeric feature in the order of x (the missing bin excluded)."""
        return list(range(len(self.cuts) + 1)) if self.kind == "numeric" else []


def _fmt(v: float) -> str:
    return f"{v:.6g}"


def _interval(cuts: list[float], i: int) -> str:
    if not cuts:
        return "all values"
    if i == 0:
        return f"≤ {_fmt(cuts[0])}"
    if i == len(cuts):
        return f"> {_fmt(cuts[-1])}"
    return f"({_fmt(cuts[i - 1])}, {_fmt(cuts[i])}]"


# --------------------------------------------------------------------------- the AutoGluon model


class ScorecardModel(AbstractModel):
    """Binning → WoE → logistic regression → points (see the module docstring)."""

    ag_key = "SCORECARD"
    ag_name = "Scorecard"
    ag_priority = 96
    _supported_problem_types = [BINARY]
    _default_auxiliary_params_extra = dict(
        valid_raw_types=[R_BOOL, R_INT, R_FLOAT, R_CATEGORY],
        # Dates, text n-grams and text statistics are not applicant attributes a scorecard bins; stack features
        # never reach it (valid_stacker=False).
        ignored_type_group_special=[S_TEXT_AS_CATEGORY, S_TEXT_NGRAM, S_TEXT_SPECIAL, S_DATETIME_AS_INT, S_IMAGE_PATH,
                                    S_STACK],
    )
    # Fit bagged folds in-process: binning + one logistic regression is milliseconds; a Ray worker costs more.
    _default_ag_args_ensemble_extra = dict(fold_fitting_strategy="sequential_local")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bins: list[FeatureBins] = []
        self.selected: list[str] = []
        self.dropped: dict[str, str] = {}
        self.coef: dict[str, float] = {}
        self.intercept: float = 0.0
        self.monotone_applied: dict[str, int] | None = None
        self.n_train: int = 0
        self.event_rate: float = 0.0
        # AutoGluon hands categories over as integer codes; a report can set {feature: {code: raw level}} so the table
        # reads in the applicant's own values.
        self.level_names: dict[str, dict[str, str]] | None = None

    @classmethod
    def _get_default_ag_args(cls) -> dict:
        return {**super()._get_default_ag_args(), "valid_stacker": False}

    def _set_default_params(self):
        for k, v in DEFAULTS.items():
            self._set_default_param_value(k, v)

    def _get_default_resources(self):
        return 1, 0

    def _more_tags(self):
        return {"can_refit_full": True}

    # -- preprocessing: raw (AutoGluon) features → WoE matrix of the selected features
    def _woe_matrix(self, X: pd.DataFrame, names: list[str]) -> np.ndarray:
        by = {b.name: b for b in self.bins}
        if not names:
            return np.zeros((len(X), 0))
        return np.column_stack([by[n].woe_of(X[n]) for n in names])

    def _preprocess(self, X: pd.DataFrame, **kwargs):
        X = super()._preprocess(X, **kwargs)
        return self._woe_matrix(X, self.selected)

    def _fit(self, X: pd.DataFrame, y: pd.Series, num_cpus: int = 1, **kwargs):
        from sklearn.linear_model import LogisticRegression

        p = self._get_model_params()
        spec = p.pop("monotone", None) or {}
        cons = {str(c): int(np.sign(s)) for c, s in (spec.get("constraints") or {}).items()}
        Xr = self.preprocess(X, preprocess_stateful=False)  # AutoGluon's feature subset, raw values
        yy = np.asarray(y, dtype=float)
        self.n_train, self.event_rate = len(yy), float(yy.mean())
        self.bins, self.dropped = [], {}
        for col in Xr.columns:
            s = Xr[col]
            kind = "categorical" if isinstance(s.dtype, pd.CategoricalDtype) or s.dtype == object else "numeric"
            fb = FeatureBins(col, kind)
            if kind == "numeric" and col in cons:
                fb.constraint = cons[col]
            fb.fit(s, yy, p)
            self.bins.append(fb)
        by = {b.name: b for b in self.bins}
        cand = [b.name for b in self.bins if b.k > 1 and b.iv >= float(p["min_iv"])]
        for b in self.bins:
            if b.k <= 1:
                self.dropped[b.name] = "a single bin"
            elif b.iv < float(p["min_iv"]):
                self.dropped[b.name] = f"IV {b.iv:.4f} < {float(p['min_iv']):g}"
        if not cand:  # keep the most informative feature, so the scorecard still ranks applicants
            multi = [b for b in self.bins if b.k > 1]
            if multi:
                best = max(multi, key=lambda b: b.iv)
                cand = [best.name]
                self.dropped.pop(best.name, None)
        while True:
            W = self._woe_matrix(Xr, cand)
            lr = LogisticRegression(C=float(p["C"]), max_iter=int(p["max_iter"]), solver="lbfgs")
            if W.shape[1] == 0:
                W = np.zeros((len(Xr), 1))
                lr.fit(W, yy.astype(int))
                break
            lr.fit(W, yy.astype(int))
            pos = [(c, n) for c, n in zip(lr.coef_[0], cand) if c > 0 and (p["enforce_sign"] or n in cons)]
            if not pos:
                break
            worst = max(pos)[1]
            self.dropped[worst] = (f"coefficient {max(pos)[0]:+.4f} > 0 with the others in the model (collinear): "
                                   "its points would run against its bins")
            cand = [c for c in cand if c != worst]
        self.selected = list(cand)
        self.model = lr
        self.coef = {n: float(c) for n, c in zip(cand, lr.coef_[0])} if cand else {}
        self.intercept = float(lr.intercept_[0])
        self.monotone_applied = {c: s for c, s in cons.items() if c in by}  # F8: monotone by construction
        self._scaling = {k: float(p[k]) for k in SCALING}
        self._check_monotone()

    def _check_monotone(self) -> None:
        """Every constrained feature's P(event) contribution must follow its sign over the ordered numeric bins."""
        for b in self.bins:
            if not b.constraint or b.name not in self.coef:
                continue
            contrib = self.coef[b.name] * b.woe[b.numeric_order()]
            if np.any(b.constraint * np.diff(contrib) < -1e-12):
                raise RuntimeError(f"{self.name}: scorecard is not monotone in {b.name}")

    # -- points
    def scaling(self) -> dict[str, float]:
        s = getattr(self, "_scaling", None) or {k: float(DEFAULTS[k]) for k in SCALING}
        factor = s["pdo"] / math.log(2)
        offset = s["base_score"] - factor * math.log(s["base_odds"])
        return {**s, "factor": factor, "offset": offset}

    def points_of(self, name: str) -> np.ndarray:
        """Points per bin of a feature in the scorecard (one more entry, last, for values with no bin: WoE 0)."""
        sc = self.scaling()
        n = max(len(self.selected), 1)
        b = {x.name: x for x in self.bins}[name]
        w = np.append(b.woe, 0.0)
        return -sc["factor"] * self.coef[name] * w + (sc["offset"] - sc["factor"] * self.intercept) / n

    def label(self, b: "FeatureBins", i: int) -> str:
        if i < 0:
            return "no bin (WoE 0)"
        names = (self.level_names or {}).get(b.name)
        if b.kind != "categorical" or not names or b.labels[i] == MISSING:
            return b.labels[i]
        lv = [l for l, j in b.levels.items() if j == i]
        return ", ".join(sorted((names.get(l, l) for l in lv if l != OTHER), key=str) + ([OTHER] if OTHER in lv else []))

    def table(self) -> dict[str, Any]:
        """The scorecard: one row per (feature, bin) with counts, event rate, WoE, IV share and points, plus the
        per-feature summary (IV, coefficient, in or out and why)."""
        sc = self.scaling()
        rows, feats = [], []
        for b in self.bins:
            good, bad = b.n - b.events, b.events
            G, B = max(good.sum(), 1.0), max(bad.sum(), 1.0)
            ivc = (good / G - bad / B) * b.woe
            inn = b.name in self.coef
            pts = self.points_of(b.name) if inn else None
            for i in range(b.k):
                rows.append({"feature": b.name, "bin": i, "label": self.label(b, i), "n": int(b.n[i]),
                             "share": float(b.n[i] / max(b.n.sum(), 1)), "events": int(b.events[i]),
                             "event_rate": float(b.events[i] / b.n[i]) if b.n[i] else None, "woe": float(b.woe[i]),
                             "iv": float(ivc[i]), "points": float(pts[i]) if inn else None,
                             "points_rounded": int(round(float(pts[i]))) if inn else None, "in_model": inn})
            feats.append({"feature": b.name, "kind": b.kind, "bins": b.k, "iv": b.iv, "in_model": inn,
                          "coefficient": self.coef.get(b.name), "dropped": self.dropped.get(b.name),
                          "direction": b.direction, "constraint": b.constraint or None,
                          "cuts": list(b.cuts) if b.kind == "numeric" else None, "has_missing": b.has_missing,
                          "points_range": [float(pts[:-1].min()), float(pts[:-1].max())] if inn else None})
        return {"rows": rows, "features": feats, "intercept": self.intercept, "scaling": sc,
                "n_features": len(self.selected), "n_train": self.n_train, "event_rate": self.event_rate}

    def score_frame(self, X: pd.DataFrame) -> dict[str, Any]:
        """Per row: points per feature, the score, P(event) = the model's probability, and the points shortfall per
        feature against its best bin (the scorecard's reason codes), for X in AutoGluon's feature space."""
        X = self.preprocess(X, preprocess_stateful=False)
        pts = {}
        short = {}
        for n in self.selected:
            b = {x.name: x for x in self.bins}[n]
            per = self.points_of(n)
            idx = b.assign(X[n])
            pts[n] = per[idx]
            short[n] = per[:-1].max() - pts[n]
        P = pd.DataFrame(pts, index=X.index)
        score = P.sum(axis=1).to_numpy() if len(self.selected) else np.full(len(X), self.scaling()["offset"] - self.scaling()["factor"] * self.intercept)
        sc = self.scaling()
        prob = 1.0 / (1.0 + np.exp((score - sc["offset"]) / sc["factor"]))
        return {"points": P, "score": score, "p_event": prob, "shortfall": pd.DataFrame(short, index=X.index)}


def reasons(shortfall: pd.DataFrame, k: int = 4) -> list[list[dict[str, Any]]]:
    """Top k features by points lost against their best bin (shortfall > 0), per row."""
    out = []
    cols = list(shortfall.columns)
    M = shortfall.to_numpy(dtype=float) if cols else np.zeros((len(shortfall), 0))
    for r in M:
        order = [i for i in np.argsort(-r, kind="stable") if r[i] > 1e-9][:k]
        out.append([{"feature": cols[i], "points_lost": float(r[i])} for i in order])
    return out


def register() -> None:
    from autogluon.tabular.registry import ag_model_registry

    if not ag_model_registry.exists(ScorecardModel) and ScorecardModel.ag_key not in ag_model_registry.keys:
        ag_model_registry.add(ScorecardModel)
