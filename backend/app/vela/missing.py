"""D3 · What's missing: the information gap of a finished run, measured (a heavy-pool job, cached by the holdout
predictions' SHA in the run's result).

When a model stops improving, this says why the table cannot separate the cases and what information would. Every
number is computed here from the run's own files (data.csv, splits, holdout_predictions.csv, feature importance):

  errors         holdout errors ranked by confidence, each with its top reasons (TreeSHAP of a fixed LightGBM, the
                 "quick model": 200 trees, the run's features minus text/IDs, ≤ 30k training rows), by slice, by time,
                 and their overlap with cleanlab's label issues on the holdout's p̂
  twins          k = 15 nearest training rows of every row (importance-weighted rank space, one-hot categories): the
                 local disagreement U = 1 − Σ_c K_c(K_c − 1)/(k(k − 1)) is unbiased for 1 − Σ_c η_c², so its mean R_NN is
                 the asymptotic nearest-neighbour error. Cover–Hart: R* ≤ R_NN ≤ R*(2 − K/(K−1)·R*), so the irreducible
                 error R* lies in [ (K−1)/K · (1 − √(1 − K/(K−1)·R_NN)), R_NN ]; bootstrap CIs over holdout rows
  concentration  error rate by bin of each feature (lift, excess errors, binomial z, BH q), and each feature's
                 missingness inside the error rows (and inside the top slice's errors) against the rest
  thin           features with ≤ 12 values and ≥ 5 % of the importance whose levels leave most of the outcome's
                 variance inside them (within share = Σ n_l·var_l / (n·var))
  confusion      class pairs confused both ways (multiclass): the label definitions overlap
  quick curve    the quick model on nested 1/8, 1/4, 1/2, all training rows; gain per doubling and the next doubling
                 projected with the observed decay ratio (paired bootstrap over holdout rows)
  oracle         SIMULATION: the quick model with one extra column, the label with 10 % noise inside one segment and
                 missing elsewhere; Δ holdout score bounds what better information there could be worth
  clean labels   cleanlab on the quick model's out-of-fold p̂ over the training rows; refit without the flagged rows;
                 Δ on the untouched holdout
  leak           permutation-importance dominance (share, ratio to the next) and D1's single-column AUC
  overlap        holdout rows that are not new: the same entity (an ID-like column) or a (near-)identical row in training
  cost           decline rate, catch rate and cost per 1,000 at the served threshold and at the 3:1, 5:1, 10:1 rules
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

K_NN = 15
MAX_TRAIN = 30_000
B = 400
ORACLE_NOISE = 0.10
EPS = {"binary": 0.005, "multiclass": 0.005}  # a doubling worth less than this is "flat" (regression: 0.5 % of RMSE)
RATIOS = (3, 5, 10)


# --------------------------------------------------------------------------- the quick model


class Q:
    """The run's task as the quick model sees it."""

    def __init__(self, pt: str, y: pd.Series, positive: Any, classes: list[str] | None):
        self.pt = pt
        if pt == "binary":
            self.y = (y.astype(str) == str(positive)).astype(float).where(y.notna()).to_numpy()
            self.label = "AUC"
        elif pt == "multiclass":
            self.classes = classes or sorted(y.dropna().astype(str).unique())
            m = {c: i for i, c in enumerate(self.classes)}
            self.y = y.astype(str).map(m).where(y.notna()).to_numpy(dtype=float)
            self.label = "accuracy"
        else:
            self.y = pd.to_numeric(y, errors="coerce").to_numpy(dtype=float)
            self.label = "RMSE"

    def model(self, seed: int = 0):
        import lightgbm as lgb

        kw = dict(n_estimators=200, learning_rate=0.05, num_leaves=31, min_child_samples=20, subsample=1.0,
                  colsample_bytree=1.0, random_state=seed, verbose=-1, n_jobs=4)
        return lgb.LGBMRegressor(**kw) if self.pt in ("regression", "quantile") else lgb.LGBMClassifier(**kw)

    def fit_predict(self, X: pd.DataFrame, tr: np.ndarray, ho: np.ndarray):
        m = self.model()
        yt = self.y[tr]
        if self.pt == "multiclass":
            yt = yt.astype(int)
        m.fit(X.iloc[tr], yt)
        return m, self.predict(m, X.iloc[ho])

    def fit_predict_es(self, X: pd.DataFrame, tr: np.ndarray, ho: np.ndarray):
        """The learning-curve model: a regularised LightGBM (15 leaves, ≥ 40 rows a leaf, row and column subsampling,
        learning rate 0.03) early-stopped on 15 % of its own rows, so a small subset is not under- or over-fit and the
        gain per doubling is not inflated (a fixed 200-tree model on 1/8 of Taiwan understated it by 0.025 AUC)."""
        import lightgbm as lgb

        v = np.random.default_rng(2).random(len(tr)) < 0.15
        a, b = tr[~v], tr[v]
        kw = dict(n_estimators=3000, learning_rate=0.03, num_leaves=15, min_child_samples=40, subsample=0.8, subsample_freq=1,
                  colsample_bytree=0.8, reg_lambda=1.0, random_state=0, verbose=-1, n_jobs=4)
        reg = self.pt in ("regression", "quantile")
        m = lgb.LGBMRegressor(**kw) if reg else lgb.LGBMClassifier(**kw)
        ya, yb = self.y[a], self.y[b]
        if self.pt == "multiclass":
            ya, yb = ya.astype(int), yb.astype(int)
        if not reg and len(np.unique(yb)) < len(np.unique(ya)):  # a tiny subset: no early stop
            m.set_params(n_estimators=300)
            m.fit(X.iloc[tr], self.y[tr].astype(int))
        else:
            m.fit(X.iloc[a], ya, eval_X=(X.iloc[b],), eval_y=(yb,), callbacks=[lgb.early_stopping(100, verbose=False)])
        return self.predict(m, X.iloc[ho])

    def predict(self, m, X):
        if self.pt in ("regression", "quantile"):
            return m.predict(X)
        p = m.predict_proba(X)
        if self.pt == "binary":
            return p[:, 1]
        full = np.zeros((len(X), len(self.classes)))
        full[:, m.classes_.astype(int)] = p
        return full

    def score(self, y, p) -> float:
        """Higher is better: AUC; accuracy; −RMSE."""
        if self.pt == "binary":
            from ..engine.scorecard import auc

            return float(auc(y.astype(int), p))
        if self.pt == "multiclass":
            return float((p.argmax(1) == y.astype(int)).mean())
        return -float(np.sqrt(np.mean((y - p) ** 2)))

    def natural(self, s: float) -> float:
        return -s if self.pt in ("regression", "quantile") else s


def _paired(q: Q, y: np.ndarray, p_new, p_base, seed: int = 0, b: int = B) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    n = len(y)
    d0 = q.score(y, p_new) - q.score(y, p_base)
    ds = []
    for _ in range(b):
        i = rng.integers(0, n, n)
        if q.pt == "binary" and y[i].min() == y[i].max():
            continue
        ds.append(q.score(y[i], p_new[i]) - q.score(y[i], p_base[i]))
    ds = np.asarray(ds) if ds else np.zeros(1)
    return {"delta": float(d0), "ci": [float(np.quantile(ds, 0.025)), float(np.quantile(ds, 0.975))], "B": int(len(ds))}


def _frame(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    X = df[cols].copy()
    for c in X.columns:
        if not pd.api.types.is_numeric_dtype(X[c]) or pd.api.types.is_bool_dtype(X[c]):
            X[c] = X[c].astype(str).where(X[c].notna()).astype("category")
    return X


def _bh(p: np.ndarray) -> np.ndarray:
    n = len(p)
    if n == 0:
        return p
    o = np.argsort(p)
    q = p[o] * n / np.arange(1, n + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    out = np.empty(n)
    out[o] = np.clip(q, 0, 1)
    return out


def _z_sf(z: np.ndarray) -> np.ndarray:
    from scipy.stats import norm

    return norm.sf(z)


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        a = abs(v)
        return f"{v:,.0f}" if a >= 1000 else f"{v:.3g}"
    return str(v)


# --------------------------------------------------------------------------- slices


def _bins(s: pd.Series) -> tuple[pd.Series, str]:
    """Bin labels for one column: quintiles of a number with > 10 values, else its levels (top 12, the rest 'other');
    missing is its own bin."""
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s) and s.nunique() > 10:
        try:
            b = pd.qcut(s, 5, duplicates="drop")
            lab = b.astype(str).where(s.notna(), "missing")
            lab = lab.map(lambda v: v if v == "missing" else _interval(v))
            return lab, "number"
        except Exception:  # noqa: BLE001
            pass
    v = s.astype(str).where(s.notna(), "missing")
    top = v.value_counts().index[:12]
    return v.where(v.isin(top), "other"), "level"


def _interval(v: str) -> str:
    try:
        lo, hi = v.strip("(]").split(",")
        return f"{_fmt(float(lo))} – {_fmt(float(hi))}"
    except Exception:  # noqa: BLE001
        return v


def concentration(binned: dict[str, tuple[pd.Series, str]], err: np.ndarray, feats: list[str], min_n: int) -> list[dict[str, Any]]:
    """Error rate by bin of every feature on the holdout (bins fit on the whole column): lift, excess errors, one-sided
    binomial z, BH q."""
    n = len(err)
    base = float(err.mean())
    if base <= 0 or base >= 1:
        return []
    out = []
    for c in feats:
        lab, how = binned[c]
        for v, idx in lab.groupby(lab.to_numpy()).groups.items():
            m = lab.index.get_indexer(idx)
            k = len(m)
            if k < min_n or k > 0.8 * n:
                continue
            e = int(err[m].sum())
            rate = e / k
            z = (rate - base) / math.sqrt(base * (1 - base) / k)
            out.append({"column": c, "value": str(v), "kind": how, "n": k, "errors": e, "rate": rate, "lift": rate / base,
                        "excess": e - k * base, "z": z})
    if not out:
        return []
    q = _bh(_z_sf(np.array([o["z"] for o in out])))
    for o, qq in zip(out, q):
        o["q"] = float(qq)
    keep = [o for o in out if o["q"] <= 0.05 and o["lift"] >= 1.25]
    keep.sort(key=lambda o: -o["excess"])
    return keep[:8]


def _seg_label(o: dict[str, Any]) -> str:
    v = o["value"]
    if v == "missing":
        return f"{o['column']} missing"
    return f"{o['column']} {v}" if o["kind"] == "number" else f"{o['column']} = {v}"


def missingness(Xh: pd.DataFrame, err: np.ndarray, feats: list[str], inside: np.ndarray | None = None) -> list[dict[str, Any]]:
    """Each feature's missing rate inside the error rows vs the other rows (inside a segment when given)."""
    out = []
    scope = np.ones(len(err), bool) if inside is None else inside
    e_rows = scope & (err == 1)
    other = ~e_rows if inside is None else ~scope
    if e_rows.sum() < 10 or other.sum() < 10:
        return []
    for c in feats:
        na = Xh[c].isna().to_numpy()
        if not na.any():
            continue
        a, b = float(na[e_rows].mean()), float(na[other].mean())
        n1, n2 = int(e_rows.sum()), int(other.sum())
        p = (na[e_rows].sum() + na[other].sum()) / (n1 + n2)
        se = math.sqrt(max(p * (1 - p) * (1 / n1 + 1 / n2), 1e-12))
        z = (a - b) / se
        if a - b >= 0.05 and a >= 1.5 * b and z >= 3:
            out.append({"column": c, "missing_in_errors": a, "missing_elsewhere": b, "n_errors": n1, "z": z})
    out.sort(key=lambda o: -(o["missing_in_errors"] - o["missing_elsewhere"]))
    return out[:5]


# --------------------------------------------------------------------------- twins (Cover–Hart)


def _space(df: pd.DataFrame, feats: list[str], tr: np.ndarray, weights: dict[str, float]) -> np.ndarray:
    """Importance-weighted space: numbers as their training-rows percentile (missing → −0.5), categories one-hot
    (top 20 levels); a full-range difference on a number and a different category both cost the feature's weight."""
    cols = []
    for c in feats:
        w = weights.get(c, 0.0)
        if w <= 0:
            continue
        s = df[c]
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            ref = np.sort(s.iloc[tr].dropna().to_numpy(dtype=float))
            if len(ref) == 0:
                continue
            v = s.to_numpy(dtype=float)
            r = np.searchsorted(ref, v, side="right") / len(ref)
            r[np.isnan(v)] = -0.5
            cols.append(r * math.sqrt(w))
        else:
            v = s.astype(str).where(s.notna(), "∅")
            top = v.iloc[tr].value_counts().index[:20]
            for lv in top:
                cols.append((v == lv).to_numpy(dtype=float) * math.sqrt(w / 2))
    return np.column_stack(cols) if cols else np.zeros((len(df), 0))


def twins(Z: np.ndarray, ycode: np.ndarray, tr: np.ndarray, ho: np.ndarray, n_classes: int, seed: int = 0) -> dict[str, Any] | None:
    from sklearn.neighbors import NearestNeighbors

    if Z.shape[1] == 0 or len(tr) < 5 * K_NN:
        return None
    nn = NearestNeighbors(n_neighbors=K_NN + 1).fit(Z[tr])

    def local(rows: np.ndarray, self_in: bool) -> tuple[np.ndarray, np.ndarray]:
        d, ix = nn.kneighbors(Z[rows], n_neighbors=K_NN + 1)
        if self_in:  # a training row finds itself first; drop it (or the farthest when it ties with a twin)
            keep = np.ones_like(ix, bool)
            pos_self = np.array([np.flatnonzero(tr[ix[i]] == rows[i])[:1] for i in range(len(rows))], dtype=object)
            for i, p in enumerate(pos_self):
                keep[i, p[0] if len(p) else K_NN] = False
            ix = ix[keep].reshape(len(rows), K_NN)
            d = d[keep].reshape(len(rows), K_NN)
        else:
            ix, d = ix[:, :K_NN], d[:, :K_NN]
        lab = ycode[tr][ix].astype(int)
        cnt = np.stack([(lab == c).sum(1) for c in range(n_classes)], 1)
        u = 1 - (cnt * (cnt - 1)).sum(1) / (K_NN * (K_NN - 1))
        return u, d[:, -1]

    u_ho, dk = local(ho, False)
    u_tr, _ = local(tr, True)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(ho), size=(1000, len(ho)))
    r_nn = float(u_ho.mean())
    boots = u_ho[idx].mean(1)
    kk = n_classes / (n_classes - 1)

    def lower(r: float) -> float:
        return float((1 / kk) * (1 - math.sqrt(max(0.0, 1 - kk * min(r, 1 / kk)))))

    pair = rng.integers(0, len(tr), size=(2000, 2))
    typical = float(np.median(np.linalg.norm(Z[tr][pair[:, 0]] - Z[tr][pair[:, 1]], axis=1))) or 1.0
    return {"k": K_NN, "r_nn": r_nn, "r_nn_ci": [float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))],
            "lower": lower(r_nn), "lower_ci": [lower(float(np.quantile(boots, 0.025))), lower(float(np.quantile(boots, 0.975)))],
            "closeness": float(np.median(dk) / typical), "u_holdout": u_ho, "u_train": u_tr,
            "method": (f"the {K_NN} nearest training rows of every holdout row in importance-weighted percentile space "
                       "(categories one-hot); U = 1 − Σ_c K_c(K_c − 1)/(k(k − 1)) per row, R_NN = mean U (the nearest-"
                       "neighbour error); Cover–Hart: R* ≤ R_NN ≤ R*(2 − K/(K−1)·R*)")}


# --------------------------------------------------------------------------- cost trade-off


def cost_tradeoff(hp: pd.DataFrame, cost: dict[str, Any], positive: Any, t_served: float) -> dict[str, Any]:
    from ..engine.explain import binary_arrays

    y, p = binary_arrays(hp, positive)
    n = len(y)
    npos = max(1, int(y.sum()))

    def point(t: float, c_fp: float, c_fn: float, g_tp: float = 0.0) -> dict[str, Any]:
        yhat = p > t
        tp, fp = int((yhat & (y == 1)).sum()), int((yhat & (y == 0)).sum())
        fn = int((~yhat & (y == 1)).sum())
        return {"t": float(t), "decline_rate": float(yhat.mean()), "catch_rate": tp / npos, "tp": tp, "fp": fp, "fn": fn,
                "cost_per_1000": 1000 * (c_fp * fp + c_fn * fn - g_tp * tp) / max(1, n)}

    c_fp, c_fn, g_tp = float(cost["c_fp"]), float(cost["c_fn"]), float(cost.get("g_tp") or 0)
    rows = [{"name": "served", "ratio": c_fn / c_fp if c_fp else None, **point(t_served, c_fp, c_fn, g_tp)}]
    for r in RATIOS:
        t = 1 / (1 + r)
        pt = point(t, c_fp, c_fn, g_tp)
        pt["cost_per_1000_at_ratio"] = point(t, 1.0, float(r))["cost_per_1000"]
        rows.append({"name": f"{r}:1", "ratio": r, **pt})
    return {"rows": rows, "n": n, "prevalence": float(y.mean()), "positive_class": str(positive),
            "configured": {"c_fp": c_fp, "c_fn": c_fn, "g_tp": g_tp}, "served_t": float(t_served),
            "rule": "decline when p̂(positive) > t; at a ratio r = c_FN : c_FP the Bayes threshold is t = 1/(1 + r); "
                    "cost per 1,000 under the run's own costs, and under r with c_FP = 1"}


# --------------------------------------------------------------------------- the job


def measure(cfg: dict[str, Any], rec: dict[str, Any], hints: dict[str, Any] | None = None) -> dict[str, Any]:
    """(Runs in a worker.) Every part fails alone; failures are listed in `errors`."""
    from ..profiling import detect_type
    from ..splits import holdout_mask
    from .findings import holdout_frame

    t0 = time.time()
    hints = hints or {}
    res = rec.get("result") or {}
    pt = res.get("problem_type")
    out: dict[str, Any] = {"errors": [], "problem_type": pt}
    hp = holdout_frame(rec)
    if hp is None or pt not in ("binary", "multiclass", "regression"):
        out["errors"].append("no holdout predictions, or a problem type the gap analysis does not cover")
        return out
    df = pd.read_csv(cfg["csv_path"])
    label = cfg["label"]
    sp = json.loads(Path(cfg["splits_path"]).read_text())
    is_h = holdout_mask(sp, len(df))
    y_all = df[label]
    tr = np.flatnonzero(~is_h & y_all.notna().to_numpy())
    if len(tr) > MAX_TRAIN:
        tr = np.sort(np.random.default_rng(0).choice(tr, MAX_TRAIN, replace=False))
    ho = hp["row"].to_numpy().astype(int)
    feats_all = [f for f in (res.get("features") or []) if f in df.columns and f != label]
    types = {c: detect_type(str(c), df[c]) for c in feats_all}
    feats = [c for c in feats_all if types[c] not in ("text", "id")]
    out["text_columns"] = [c for c in feats_all if types[c] == "text"]
    fi = {r["feature"]: max(0.0, float(r.get("importance") or 0)) for r in (res.get("feature_importance") or []) if r.get("feature")}
    tot = sum(fi.values())
    share = {c: v / tot for c, v in fi.items()} if tot > 0 else {}
    out["importance_share"] = share
    positive = res.get("positive_class")
    pcols = [c for c in hp.columns if c.startswith("p_")]
    classes = [c[2:] for c in pcols] if pt == "multiclass" else None
    q = Q(pt, y_all, positive, classes)
    yh = q.y[ho]
    okh = ~np.isnan(yh)

    # served errors on the holdout
    if pt in ("binary", "multiclass"):
        err = (hp["y_true"].astype(str).to_numpy() != hp["y_pred"].astype(str).to_numpy()).astype(int)
        P = hp[pcols].to_numpy(dtype=float)
        conf = P.max(1)
        argmax_err = (np.asarray(pcols)[P.argmax(1)] != ("p_" + hp["y_true"].astype(str)).to_numpy()).astype(int)
    else:
        yt_ = pd.to_numeric(hp["y_true"], errors="coerce").to_numpy(dtype=float)
        resid = np.abs(yt_ - pd.to_numeric(hp["y_pred"], errors="coerce").to_numpy(dtype=float))
        pos_y = np.nanmin(yt_) > 0
        skew = float(pd.Series(yt_).skew()) if len(yt_) > 2 else 0.0
        relative = bool(pos_y and skew > 1.0)  # a skewed positive target (prices, amounts): errors relative to the value
        if relative:
            resid = resid / yt_
        out["relative_error"] = relative
        err = (resid >= np.nanquantile(resid, 0.8)).astype(int)
        conf, P, argmax_err = resid, None, err
    out["served"] = {"n": int(len(err)), "errors": int(err.sum()), "rate": float(err.mean()),
                     "argmax_error": float(argmax_err.mean()),
                     "definition": ("a holdout row whose served decision differs from its label" if pt != "regression"
                                    else "a holdout row in the top 20 % of " + ("relative error |y − ŷ| / y (the target is skewed, "
                                         "so absolute error would only pick the largest values)" if out.get("relative_error") else "absolute error"))}
    Xh = df.iloc[ho][feats].reset_index(drop=True)
    min_n = max(30, int(0.01 * len(ho)))

    # cleanlab on the holdout (overlap with the errors)
    flags = None
    if P is not None:
        try:
            from cleanlab.filter import find_label_issues

            yi = pd.Series(hp["y_true"].astype(str)).map({c[2:]: i for i, c in enumerate(pcols)}).to_numpy()
            if not np.isnan(yi.astype(float)).any() and len(np.unique(yi)) == len(pcols):
                flags = find_label_issues(yi.astype(int), P, filter_by="prune_by_noise_rate", n_jobs=1)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"cleanlab: {type(e).__name__}: {e}"[:200])

    # the quick model
    X = _frame(df, feats) if feats else None
    base_p = None
    if X is not None:
        try:
            base_m, base_p = q.fit_predict(X, tr, ho)
            out["quick"] = {"score": q.natural(q.score(yh[okh], base_p[okh])), "metric": q.label, "rows": int(len(tr)),
                            "features": len(feats), "model": "LightGBM, 200 trees, learning rate 0.05, 31 leaves, seed 0"}
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"quick model: {type(e).__name__}: {e}"[:200])
            X = None

    # A. errors, ranked by confidence, with TreeSHAP reasons of the quick model
    try:
        order = np.argsort(-conf * (err == 1))
        top = [i for i in order[: max(25, 0)] if err[i] == 1][:25]
        contrib = None
        if X is not None and top:
            raw = base_m.predict(X.iloc[ho[top]], pred_contrib=True)
            contrib = np.asarray(raw)
        items = []
        F = len(feats)
        for j, i in enumerate(top):
            it: dict[str, Any] = {"row": int(ho[i]), "y_true": str(hp["y_true"].iloc[i]), "y_pred": str(hp["y_pred"].iloc[i]),
                                  "confidence": float(conf[i]), "label_issue": bool(flags[i]) if flags is not None else None}
            if contrib is not None:
                if pt == "multiclass":
                    k = list(q.classes).index(str(hp["y_pred"].iloc[i])) if str(hp["y_pred"].iloc[i]) in q.classes else 0
                    c = contrib[j][k * (F + 1):(k + 1) * (F + 1)][:F]
                    sign = 1.0
                elif pt == "binary":
                    c = contrib[j][:F]
                    sign = 1.0 if str(hp["y_pred"].iloc[i]) == str(positive) else -1.0
                else:
                    c = contrib[j][:F]
                    sign = 1.0 if float(hp["y_pred"].iloc[i]) > float(hp["y_true"].iloc[i]) else -1.0
                o = np.argsort(-(sign * c))[:3]
                it["reasons"] = [{"feature": feats[f], "value": _jv(df.iloc[ho[i]][feats[f]]), "phi": float(c[f])} for f in o if sign * c[f] > 0]
            items.append(it)
        out["errors_top"] = {"rows": items, "reasons_from": "TreeSHAP of the quick model (pred_contrib, log-odds); the served "
                                                             "model's exact Kernel SHAP is one click away in Explain → Features & SHAP"}
        if flags is not None:
            out["errors_top"]["cleanlab"] = {"flagged": int(flags.sum()), "of": int(len(flags)),
                                             "errors_flagged": int((flags & (err == 1)).sum()), "errors": int(err.sum()),
                                             "share_of_errors": float(flags[err == 1].mean()) if err.sum() else 0.0,
                                             "share_of_correct": float(flags[err == 0].mean()) if (err == 0).sum() else 0.0}
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"errors: {type(e).__name__}: {e}"[:200])

    # B. concentration and missingness
    binned_all = {c: _bins(df[c]) for c in feats}
    binned = {c: (b.iloc[ho].reset_index(drop=True), how) for c, (b, how) in binned_all.items()}
    try:
        segs = concentration(binned, err, feats, min_n)
        for s in segs:
            m = (binned[s["column"]][0] == s["value"]).to_numpy()
            s["label"] = _seg_label(s)
            s["missing_inside"] = missingness(Xh, err, [c for c in feats if c != s["column"]], inside=m)[:2]
            if X is not None and m.sum() >= 10 and pt != "multiclass":
                try:  # the columns the quick model leans on for this segment's rows (mean |TreeSHAP|)
                    rows_ = ho[np.flatnonzero(m)[:2000]]
                    cc = np.abs(np.asarray(base_m.predict(X.iloc[rows_], pred_contrib=True))[:, :len(feats)]).mean(0)
                    s["top_features"] = [feats[k] for k in np.argsort(-cc)[:3] if feats[k] != s["column"]][:2]
                except Exception:  # noqa: BLE001
                    pass
            em, eo_ = m & (err == 1), ~m & (err == 1)
            if flags is not None and em.sum() >= 10 and eo_.sum() >= 10:
                # flags follow errors anywhere; a labelling problem shows as errors here being flagged more often than
                # errors elsewhere (the model is confidently "wrong" because the label is)
                fi_, fo_ = float(flags[em].mean()), float(flags[eo_].mean())
                if flags[em].sum() >= 10 and fi_ >= 1.3 * max(fo_, 1e-9) and fi_ - fo_ >= 0.1:
                    s["label_review"] = {"flagged": int(flags[m].sum()), "rate_inside": fi_, "rate_outside": fo_, "basis": "errors",
                                         "errors_flagged": int((flags[m] & (err[m] == 1)).sum()), "n": int(m.sum())}
        out["concentration"] = {"segments": segs, "overall_rate": float(err.mean()), "min_n": min_n,
                                "missing_in_errors": missingness(Xh, err, feats),
                                "rule": f"error rate by bin (quintiles of numbers, levels of categories, missing as its own bin; "
                                        f"n ≥ {min_n}); concentrated when lift ≥ 1.25 and BH q ≤ 0.05 (one-sided binomial z); "
                                        "ranked by excess errors = errors − n × overall rate"}
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"concentration: {type(e).__name__}: {e}"[:200])

    # C. errors by time
    dc = hints.get("date_column")
    if dc and dc in df.columns:
        try:
            t = pd.to_datetime(df.iloc[ho][dc], errors="coerce").to_numpy()
            okt = ~pd.isna(t)
            if okt.sum() >= 100:
                o = np.argsort(t[okt])
                e2 = err[okt][o]
                tt = pd.Series(t[okt][o])
                bins = np.array_split(np.arange(len(e2)), 5)
                rows = [{"from": str(tt.iloc[b[0]])[:10], "to": str(tt.iloc[b[-1]])[:10], "n": int(len(b)), "rate": float(e2[b].mean())} for b in bins]
                from scipy.stats import chi2_contingency

                tab = [[int(e2[b].sum()), int(len(b) - e2[b].sum())] for b in bins]
                p = float(chi2_contingency(tab)[1]) if all(sum(r) for r in tab) else None
                out["by_time"] = {"column": dc, "bins": rows, "p": p}
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"by time: {type(e).__name__}: {e}"[:200])

    # D. twins
    ycode = None
    tw = None
    if pt in ("binary", "multiclass") and feats:
        try:
            ycode = (q.y if pt == "multiclass" else q.y).copy()
            w = {c: share.get(c, 0.0) for c in feats}
            if sum(w.values()) <= 0:
                w = {c: 1.0 / len(feats) for c in feats}
            Z = _space(df, feats, tr, w)
            ncls = 2 if pt == "binary" else len(q.classes)
            tw = twins(Z, np.nan_to_num(ycode, nan=-1), tr, ho, ncls)
            if tw:
                u_ho = tw.pop("u_holdout")
                u_tr = tw.pop("u_train")
                mixed_h = u_ho >= 0.3
                tw["mixed_share_holdout"] = float(mixed_h.mean())
                tw["errors_in_mixed"] = float(mixed_h[argmax_err == 1].mean()) if argmax_err.sum() else 0.0
                tw["model_error"] = float(argmax_err.mean())
                tw["explained"] = float(min(1.0, tw["lower"] / tw["model_error"])) if tw["model_error"] > 0 else None
                tw["explained_upper"] = float(min(1.0, tw["r_nn"] / tw["model_error"])) if tw["model_error"] > 0 else None
                tw["_mixed"] = (u_tr >= 0.3, mixed_h)
                exact = hints.get("conflicts")
                if exact:
                    tw["exact_conflicts"] = exact
                out["twins"] = tw
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"twins: {type(e).__name__}: {e}"[:200])

    # E. thin or coarse features
    try:
        thin = []
        if pt in ("binary", "regression"):
            yy = q.y
            ok = ~np.isnan(yy)
            var = float(np.var(yy[ok]))
            for c in feats:
                s = df[c]
                k = int(s.nunique())
                if k > 12 or k < 2 or share.get(c, 0) < 0.05 or var <= 0:
                    continue
                g = pd.DataFrame({"x": s.astype(str).where(s.notna(), "missing"), "y": yy})[ok]
                st = g.groupby("x")["y"].agg(["count", "var", "mean"]).fillna(0)
                within = float((st["count"] * st["var"] * (st["count"] - 1) / st["count"].clip(lower=1)).sum() / (ok.sum() * var))
                if within >= 0.8:
                    worst = (st["count"] * st["var"]).idxmax()
                    thin.append({"column": c, "levels": k, "importance_share": share.get(c, 0), "within_share": within,
                                 "worst_level": str(worst), "worst_n": int(st.loc[worst, "count"]), "worst_rate": float(st.loc[worst, "mean"])})
        thin.sort(key=lambda o: -o["importance_share"])
        out["thin"] = {"features": thin[:4], "rule": "≤ 12 values, ≥ 5 % of the permutation importance, and ≥ 80 % of the outcome's "
                                                     "variance left inside its levels (within share = Σ n_l·var_l / (n·var))"}
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"thin features: {type(e).__name__}: {e}"[:200])

    # F. symmetric confusion
    if pt == "multiclass":
        try:
            yt, yp = hp["y_true"].astype(str), hp["y_pred"].astype(str)
            cm = pd.crosstab(yt, yp)
            pairs = []
            names = sorted(set(cm.index) | set(cm.columns))
            cmf = cm.reindex(index=names, columns=names, fill_value=0)
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    ab, ba = int(cmf.loc[a, b]), int(cmf.loc[b, a])
                    if min(ab, ba) >= 3 and min(ab, ba) / max(ab, ba) >= 0.4:
                        pairs.append({"a": a, "b": b, "a_as_b": ab, "b_as_a": ba, "n": ab + ba, "gain": (ab + ba) / len(yt)})
            pairs.sort(key=lambda o: -o["n"])
            out["confusion"] = {"pairs": pairs[:6], "n": int(len(yt)), "total_errors": int(err.sum()),
                                "rule": "both directions ≥ 3 errors and the smaller ≥ 0.4 × the larger: the two labels overlap"}
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"confusion: {type(e).__name__}: {e}"[:200])

    # G. quick learning curve
    if X is not None:
        try:
            perm = np.random.default_rng(1).permutation(tr)
            fr = [0.125, 0.25, 0.5, 1.0]
            preds = []
            for f in fr:
                sub = np.sort(perm[: max(50, int(f * len(perm)))])
                preds.append(q.fit_predict_es(X, sub, ho))
            yv = yh[okh]
            sc = [q.score(yv, p[okh]) for p in preds]
            g = np.diff(sc)
            rng = np.random.default_rng(2)
            boots = []
            for _ in range(300):
                i = rng.integers(0, len(yv), len(yv))
                if q.pt == "binary" and yv[i].min() == yv[i].max():
                    continue
                s2 = [q.score(yv[i], p[okh][i]) for p in preds]
                g2 = np.diff(s2)
                boots.append(g2[-1] * _decay(g2))
            nxt = float(g[-1] * _decay(g))
            eps = EPS.get(pt, 0.005 * abs(sc[-1]))
            ci = [float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))] if boots else [nxt, nxt]
            out["quick_curve"] = {"fractions": fr, "rows": [int(max(50, f * len(perm))) for f in fr],
                                  "scores": [q.natural(s) for s in sc], "gains": [float(x) for x in g], "decay": _decay(g),
                                  "next_doubling": nxt, "ci": ci, "eps": eps, "flat": bool(nxt < eps and ci[1] < 2 * eps),
                                  "metric": q.label,
                                  "rule": (f"a regularised, early-stopped LightGBM on nested 1/8, 1/4, 1/2 and all {len(perm):,} training rows; the next doubling is "
                                           f"projected as the last gain × the mean decay ratio of successive gains (clipped to [0, 1]); "
                                           f"flat when it is < ε = {eps:.4g} and its 95 % upper bound < 2ε")}
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"quick curve: {type(e).__name__}: {e}"[:200])

    # H. oracle per top error segment (a simulation)
    if X is not None:
        try:
            segs = []
            if tw and "_mixed" in tw:  # where the look-alikes are: the concentrated slice they over-fill most
                mh_ = tw["_mixed"][1]
                best = None
                for s in (out.get("concentration") or {}).get("segments", []):
                    inm = (binned[s["column"]][0] == s["value"]).to_numpy()
                    if mh_.sum() >= 10:
                        a_, b_ = float(inm[mh_].mean()), float(inm.mean())
                        if b_ > 0 and a_ / b_ >= 1.5 and (best is None or a_ / b_ > best["lift"]):
                            best = {"label": s["label"], "share_mixed": a_, "share_all": b_, "lift": a_ / b_}
                if best:
                    tw["where"] = best
            for s in (out.get("concentration") or {}).get("segments", [])[:3]:
                lab = binned_all[s["column"]][0]
                segs.append({"id": f"seg:{s['column']}={s['value']}", "kind": "concentration", "label": s["label"],
                             "mask": (lab == s["value"]).to_numpy()})
            if tw and "_mixed" in tw:
                mt, mh = tw.pop("_mixed")
                m = np.zeros(len(df), bool)
                m[tr[mt]] = True
                m[ho[mh]] = True
                segs.append({"id": "twins", "kind": "twins", "label": "rows whose nearest neighbours had mixed outcomes", "mask": m})
            orc = []
            rng = np.random.default_rng(3)
            if pt != "regression":
                flip = rng.random(len(df)) < ORACLE_NOISE
                ncls_o = 2 if pt == "binary" else len(q.classes)
                alt = (np.nan_to_num(q.y, nan=0) + rng.integers(1, ncls_o, len(df))) % ncls_o
            for s in segs:
                m = s["mask"]
                if m[ho].sum() < 20 or m[tr].sum() < 50:
                    continue
                Xo = X.copy()
                col = np.full(len(df), np.nan)
                yv = q.y.copy()
                if pt == "regression":
                    sd = float(np.nanstd(yv))
                    col[m] = yv[m] + rng.normal(0, ORACLE_NOISE * sd, m.sum())
                else:
                    col[m] = np.where(flip[m], alt[m], yv[m])
                Xo["__oracle__"] = col
                if pt == "multiclass":
                    Xo["__oracle__"] = Xo["__oracle__"].astype("category")
                _, p_o = q.fit_predict(Xo, tr, ho)
                d = _paired(q, yh[okh], p_o[okh], base_p[okh], seed=7)
                # the same oracle on a random segment of the same size: what any segment that large would gain
                mr_ = np.zeros(len(df), bool)
                mr_[rng.choice(tr, int(m[tr].sum()), replace=False)] = True
                mr_[rng.choice(ho, int(m[ho].sum()), replace=False)] = True
                Xr = X.copy()
                colr = np.full(len(df), np.nan)
                if pt == "regression":
                    colr[mr_] = yv[mr_] + rng.normal(0, ORACLE_NOISE * float(np.nanstd(yv)), mr_.sum())
                else:
                    colr[mr_] = np.where(flip[mr_], alt[mr_], yv[mr_])
                Xr["__oracle__"] = colr
                if pt == "multiclass":
                    Xr["__oracle__"] = Xr["__oracle__"].astype("category")
                _, p_r = q.fit_predict(Xr, tr, ho)
                dr = _paired(q, yh[okh], p_r[okh], base_p[okh], seed=8)
                dx = _paired(q, yh[okh], p_o[okh], p_r[okh], seed=9)
                orc.append({"id": s["id"], "kind": s["kind"], "label": s["label"], "rows_holdout": int(m[ho].sum()),
                            "rows_train": int(m[tr].sum()), "share": float(m.mean()), "delta": d["delta"], "ci": d["ci"], "metric": q.label,
                            "random": {"delta": dr["delta"], "ci": dr["ci"]}, "excess": {"delta": dx["delta"], "ci": dx["ci"]},
                            "beyond_noise": bool(dx["ci"][0] > 0)})
            out["oracle"] = {"segments": orc, "noise": ORACLE_NOISE,
                             "rule": (f"SIMULATION. The quick model refit with one extra column: the label with {ORACLE_NOISE:.0%} "
                                      "noise inside the segment and missing elsewhere (training and holdout rows alike). Δ holdout "
                                      "score with a paired bootstrap: an upper bound on what better information there is worth. Each is also run on a random "
                                      "segment of the same size: the excess over it is what the segment itself is worth; a gap whose "
                                      "excess is within noise (its 95 % interval reaches 0) is dropped.")}
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"oracle: {type(e).__name__}: {e}"[:200])
    if tw:
        tw.pop("_mixed", None)

    # I. clean labels: cleanlab on the training rows' out-of-fold p̂, refit without the flagged rows
    if X is not None and pt in ("binary", "multiclass"):
        try:
            from cleanlab.filter import find_label_issues
            from sklearn.model_selection import StratifiedKFold

            ytr = q.y[tr].astype(int)
            ncls = 2 if pt == "binary" else len(q.classes)
            Pt = np.zeros((len(tr), ncls))
            for a, b in StratifiedKFold(3, shuffle=True, random_state=0).split(np.zeros(len(tr)), ytr):
                m = q.model()
                m.fit(X.iloc[tr[a]], ytr[a])
                pp = m.predict_proba(X.iloc[tr[b]])
                Pt[np.ix_(b, m.classes_.astype(int))] = pp
            if len(np.unique(ytr)) == ncls:
                fl = find_label_issues(ytr, Pt, filter_by="prune_by_noise_rate", n_jobs=1)
                share_f = float(fl.mean())
                item: dict[str, Any] = {"flagged": int(fl.sum()), "of": int(len(fl)), "share": share_f}
                if fl.sum() >= max(10, 0.005 * len(fl)):
                    _, p_c = q.fit_predict(X, tr[~fl], ho)
                    d = _paired(q, yh[okh], p_c[okh], base_p[okh], seed=11)
                    item.update(delta=d["delta"], ci=d["ci"], metric=q.label)
                    # The holdout's labels come from the same process: if cleanlab flags them at a similar rate, a model trained
                    # on cleaner labels is scored against the same wrong labels and its gain cannot show (it can even look worse).
                    hshare = float(flags.mean()) if flags is not None else None
                    item["holdout_flagged_share"] = hshare
                    if hshare is not None and hshare >= max(0.02, 0.5 * share_f):
                        item["measurable"] = False
                        item["unmeasurable_reason"] = (
                            f"cleanlab flags {hshare:.1%} of the holdout labels too ({share_f:.1%} of training). If those are wrong labels, "
                            "a model trained on cleaned labels is scored against them and its gain cannot show (it can even look worse); "
                            "if they are classes that overlap, cleaning will not help. This holdout cannot tell which: reviewed holdout "
                            "labels can (the flagged holdout rows in the label queue).")
                    else:
                        item["measurable"] = True
                out["clean_labels"] = {**item, "rule": "cleanlab find_label_issues(prune_by_noise_rate) on the quick model's 3-fold "
                                                       "out-of-fold p̂ over the training rows; the quick model refit without the flagged "
                                                       "rows; Δ on the untouched holdout (paired bootstrap)"}
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"clean labels: {type(e).__name__}: {e}"[:200])

    # J. leak signals: dominance of the permutation importance, D1's single-column AUC, D1 / intake suspects
    try:
        from .. import data_doctor as dd
        from ..intake import detect

        cx = dd.Ctx(df, label, detect(df)["columns"])
        f_l = dd.check_leakage(cx)
        out["d1_leak"] = [{k: s_.get(k) for k in ("column", "confidence", "reasons", "auc")} for s_ in (f_l[0].get("suspects") or [])] if f_l else []
        f_c = dd.check_conflicts(cx)
        out["conflicts"] = f_c[0]["numbers"] if f_c else {"rows": 0, "groups": 0, "share": 0.0}
        if tw is not None and "twins" in out:
            out["twins"]["exact_conflicts"] = out["conflicts"]
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"data doctor hints: {type(e).__name__}: {e}"[:200])
    try:
        ranked = sorted(share.items(), key=lambda kv: -kv[1])
        leak = {"top": [{"feature": f, "share": s} for f, s in ranked[:3]]}
        if ranked:
            f0, s0 = ranked[0]
            s1 = ranked[1][1] if len(ranked) > 1 else 0.0
            leak.update(feature=f0, share=s0, next_share=s1, ratio=(s0 / s1) if s1 > 0 else None)
        if pt == "binary":
            from ..data_doctor import single_auc

            y01 = pd.Series(q.y)
            leak["single_auc"] = {}
            for f, _ in ranked[:3]:
                if f in df.columns and types.get(f) not in ("text",):
                    a = single_auc(df[f], y01)
                    if a is not None:
                        leak["single_auc"][f] = a
        out["leak"] = leak
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"leak: {type(e).__name__}: {e}"[:200])

    # L. train–holdout overlap: the same entity (an ID column with repeats) or a near-identical row on both sides of the split
    try:
        out["overlap"] = overlap(df, cfg, res, label, is_h, feats_all, hp, pt, positive)
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"overlap: {type(e).__name__}: {e}"[:200])

    # K. cost trade-off
    c = cfg.get("cost") or {}
    if pt == "binary" and c.get("kind") == "binary":
        try:
            t_s = float((res.get("threshold") or {}).get("value", 0.5))
            out["cost"] = cost_tradeoff(hp, c, positive, t_s)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"cost: {type(e).__name__}: {e}"[:200])
    out["seconds"] = round(time.time() - t0, 1)
    return out


def _row_keys(F: pd.DataFrame) -> np.ndarray:
    G = F.copy()
    for c in G.columns:
        if pd.api.types.is_float_dtype(G[c]):
            G[c] = G[c].map(lambda v: float(f"{v:.3g}") if pd.notna(v) else v)
    return pd.util.hash_pandas_object(G.astype(str), index=False).to_numpy()


def overlap(df: pd.DataFrame, cfg: dict[str, Any], res: dict[str, Any], label: str, is_h: np.ndarray, feats: list[str],
            hp: pd.DataFrame, pt: str, positive: Any) -> dict[str, Any]:
    """Holdout rows that are not new to the model: (1) the same entity as a training row (an ID-like column of the
    table, used or not, whose value repeats across the split: a customer seen twice), (2) the same row as a training
    row on every feature (numbers to 3 significant digits), (3) the same on all features but one. The served score
    on the holdout without those rows is the score on new entities."""
    from ..profiling import detect_type

    trm = ~is_h
    ent = []
    for c in df.columns:
        if c == label:
            continue
        s = df[c]
        if detect_type(str(c), s) != "id" and not (s.dtype == object and s.nunique() > 0.5 * len(s)):
            continue
        if s.dtype == object and pd.to_numeric(s.astype(str).str.strip().replace("", np.nan), errors="coerce").notna().mean() >= 0.9:
            continue  # numbers stored as text (amounts that repeat by chance), not an entity
        v = s.astype(str).where(s.notna())
        seen = set(v[trm].dropna())
        rep = v[is_h].isin(seen).to_numpy() & v[is_h].notna().to_numpy()
        if rep.sum():
            ent.append({"column": c, "holdout_rows": int(rep.sum()), "share": float(rep.mean()),
                        "repeats_in_table": int(v.duplicated(keep=False).sum()), "_mask": rep})
    ent.sort(key=lambda e: -e["share"])
    hidx = np.flatnonzero(is_h)
    out: dict[str, Any] = {"holdout_rows": int(len(hidx))}
    F = df[feats] if feats else None
    near_mask = np.zeros(len(hidx), bool)
    exact_mask = np.zeros(len(hidx), bool)
    if F is not None and len(feats) >= 2:
        k_all = _row_keys(F)
        exact_mask = np.isin(k_all[is_h], k_all[trm])
        if len(df) <= 120_000 and len(feats) <= 40:
            for c in feats:
                k = _row_keys(F.drop(columns=[c]))
                near_mask |= np.isin(k[is_h], k[trm])
        near_mask &= ~exact_mask
    out["exact"] = {"holdout_rows": int(exact_mask.sum()), "share": float(exact_mask.mean()) if len(hidx) else 0.0}
    out["near"] = {"holdout_rows": int(near_mask.sum()), "share": float(near_mask.mean()) if len(hidx) else 0.0,
                   "checked": bool(len(df) <= 120_000 and len(feats) <= 40)}
    out["entities"] = [{k: v for k, v in e.items() if k != "_mask"} for e in ent[:3]]
    seen_mask = exact_mask | near_mask | (ent[0]["_mask"] if ent else False)
    # the served score on the holdout rows that are new (by holdout_predictions' row numbers)
    pos_in_h = {int(r): i for i, r in enumerate(hidx)}
    hrow = hp["row"].to_numpy().astype(int)
    m_seen = np.array([bool(seen_mask[pos_in_h[r]]) if r in pos_in_h else False for r in hrow])
    out["seen_rows"] = int(m_seen.sum())
    out["seen_share"] = float(m_seen.mean()) if len(m_seen) else 0.0
    if pt == "binary" and m_seen.sum() >= 10 and (~m_seen).sum() >= 30:
        from ..data_doctor import auc_halfwidth
        from ..engine.scorecard import auc

        y = (hp["y_true"].astype(str) == str(positive)).to_numpy().astype(int)
        p = hp[f"p_{positive}"].to_numpy(dtype=float)
        sc = {}
        for name, m in (("all", np.ones(len(y), bool)), ("new", ~m_seen), ("seen", m_seen)):
            if y[m].min() != y[m].max():
                a = float(auc(y[m], p[m]))
                sc[name] = {"auc": a, "n": int(m.sum()), "pm": auc_halfwidth(a, int(y[m].sum()), int((1 - y[m]).sum()))}
        out["score"] = sc
    elif pt in ("binary", "multiclass") and m_seen.sum() >= 10:
        err = (hp["y_true"].astype(str) != hp["y_pred"].astype(str)).to_numpy()
        out["score"] = {"error_seen": float(err[m_seen].mean()), "error_new": float(err[~m_seen].mean())}
    out["rule"] = ("a holdout row is 'seen' when an ID-like column's value (any ID column of the table, used as a feature or "
                   "not) also occurs in a training row, or when the row equals a training row on every feature (numbers to 3 "
                   "significant digits) or on all features but one. Finding when seen rows are ≥ 1 % of the holdout; "
                   "the honest score is the one on the new rows")
    return out


def _decay(g: np.ndarray) -> float:
    """Mean ratio of successive gains, clipped to [0, 1] (0 when a gain is ≤ 0)."""
    r = []
    for a, b in zip(g[:-1], g[1:]):
        r.append(0.0 if a <= 0 or b <= 0 else min(1.0, b / a))
    return float(np.mean(r)) if r else 0.0


def _jv(v: Any) -> Any:
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, float) and math.isnan(v):
        return None
    return v
