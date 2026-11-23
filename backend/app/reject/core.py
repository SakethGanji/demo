"""G7 · Reject inference: what a credit model learns when only approved applicants have an outcome.

A lender sees y (default) only for the applicants it approved (A = 1). A model trained on them ("known good/bad", KGB)
is then used on everyone who applies: the through-the-door (TTD) population. Reject inference adds the declined
applicants (A = 0) back into training under a stated assumption, on top of the same AutoGluon fit:

    approved only   f_0      AutoGluon on the approved rows
    reweighting     f_w      the approved rows weighted by w = 1 / P̂(A = 1 | x), clipped to [1, W_MAX] and scaled to
                             mean 1, so approved applicants who look like the declined ones count for more. P̂(A | x) is
                             an AutoGluon model of the decision on every training applicant.
                             Assumes the decision depended only on x (missing at random): then it can only re-balance.
    parcelling      f_p      every declined applicant gets a drawn outcome: in band b of f_0's score,
                             P(y = 1 | declined, b) = min(0.95, k · bad rate of the approved in b)
    fuzzy           f_f      every declined applicant enters twice, as y = 1 with weight q and as y = 0 with weight 1 − q,
                             q = min(0.95, k · f_0(x))

k is the assumption that cannot be learned from the data: a declined applicant is k times as likely to default as an
approved applicant the approved-only model scores the same (k = 1: the decline told us nothing beyond x). The default
k = 2 is a common rule of thumb, not fitted. Every model has the same config (AutoGluon presets, time limit, log loss); the weighted
fits pass the weights as AutoGluon's sample_weight with weight_evaluation=True, so early stopping and the ensemble weigh
the validation rows the same way. AutoGluon's neural nets ignore sample weights, so they are left out of every fit.

The check is a TTD holdout: every applicant of the holdout, approved or declined. It needs the outcomes the declined
applicants would have had, which a lender rarely has (a bureau outcome, an approve-all test cell): it runs only when every
row of the dataset has an outcome. Each method is compared with f_0 on the same rows: ΔAUC with DeLong's paired CI; Δ log loss and Δ Brier with a
paired bootstrap CI (calibration and ranking together); mean p̂ against the observed bad rate; and the bad rate among
the applicants a model would approve at the old approval rate. Without hidden outcomes only the approved holdout rows
can be scored, and the page says that this is exactly the biased view reject inference exists to avoid.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import frames

METHODS = {
    "approved_only": "Approved only",
    "reweighting": "Reweighting (augmentation)",
    "parcelling": "Parcelling",
    "fuzzy": "Fuzzy augmentation",
}
DEFAULT_K = 2.0
W_MAX = 10.0
N_BANDS = 10
Q_MAX = 0.95
WEIGHT = "__ri_weight"
# AutoGluon's neural nets ignore sample_weight (FastAI fits a weighted pair as 50/50); they are left out of every fit, so
# all five models keep one config and the weights mean what the method says.
NO_WEIGHTS = ["FASTAI", "NN_TORCH"]
APPROVE = re.compile(r"^(approved?|accept(ed)?|booked|funded|granted|yes|y|1|1\.0|true)$", re.I)
DECLINE = re.compile(r"^(declined?|reject(ed)?|denied|refused|no|n|0|0\.0|false)$", re.I)


# ------------------------------------------------------------------------------------------- detection (G0 roles)

def decision_values(s: pd.Series) -> tuple[str, str] | None:
    """(approved value, declined value) of a two-valued decision column, from the words, or None."""
    vals = [str(v) for v in s.dropna().unique()]
    if len(vals) != 2:
        return None
    a = [v for v in vals if APPROVE.match(v.strip())]
    d = [v for v in vals if DECLINE.match(v.strip())]
    if len(a) == 1 and len(d) == 1 and a[0] != d[0]:
        return a[0], d[0]
    return None


def find(df: pd.DataFrame, cols: list[dict[str, Any]]) -> dict[str, Any]:
    """The outcome y and the decision A, from the G0 roles, each with its evidence. The outcome is the yes/no target
    with missing values; the decision is the `decision` role, else 'declined' = no outcome."""
    out: dict[str, Any] = {"label": None, "decision_column": None, "approved_value": None, "declined_value": None,
                           "evidence": {}, "positive_class": None}
    tgt = next((c for c in cols if c["role"] == "target"), None)
    if not tgt or df[tgt["name"]].nunique(dropna=True) != 2:
        return out
    y = df[tgt["name"]]
    out["label"] = tgt["name"]
    vals = sorted(y.dropna().unique().tolist(), key=lambda v: str(v))
    out["positive_class"] = vals[-1].item() if isinstance(vals[-1], np.generic) else vals[-1]
    miss = float(y.isna().mean())
    out["evidence"][tgt["name"]] = [f"the yes/no outcome; {miss * 100:.0f}% of rows have none (declined: never observed)",
                                    *tgt["evidence"][:2]]
    dec = next((c for c in cols if c["role"] == "decision"), None) or \
        next((c for c in cols if any(a["role"] == "decision" for a in c["also"])), None)
    if dec:
        av = decision_values(df[dec["name"]])
        if av:
            out["decision_column"], out["approved_value"], out["declined_value"] = dec["name"], av[0], av[1]
            ev = dec["evidence"] if dec["role"] == "decision" else next(a["evidence"] for a in dec["also"] if a["role"] == "decision")
            out["evidence"][dec["name"]] = ev
    out["missing_share"] = miss
    return out


def decided(df: pd.DataFrame, f: dict[str, Any]) -> np.ndarray:
    """A = 1 for an approved applicant: the decision column when there is one, else 'has an outcome'."""
    if f.get("decision_column"):
        return (df[f["decision_column"]].astype(str) == str(f["approved_value"])).to_numpy()
    return df[f["label"]].notna().to_numpy()


def truth_outcomes(df: pd.DataFrame, label: str, pos: Any) -> np.ndarray | None:
    """y for every row (1 = the positive class), declined applicants included; None when some row has no outcome."""
    if df[label].isna().any():
        return None
    return (df[label] == pos).astype(int).to_numpy()


# --------------------------------------------------------------------------------------------------------- numbers

def compare(y: np.ndarray, base: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    """A method's p̂ against the approved-only model's on the same rows."""
    from ..registry.monitoring import _logloss_rows, paired_bootstrap, paired_delong

    y = np.asarray(y).astype(int)
    out: dict[str, Any] = {"n": int(len(y)), "bad_rate": float(y.mean()), "mean_p": float(p.mean()),
                           "log_loss": float(_logloss_rows(y, p).mean()), "brier": float(((p - y) ** 2).mean())}
    if 0 < y.sum() < len(y):
        d = paired_delong(y, base, p)
        out["auc"] = d["auc_challenger"]
        out["delta_auc"] = {k: d[k] for k in ("delta", "ci", "se", "p_value")}
    ll = paired_bootstrap(_logloss_rows(y, base), _logloss_rows(y, p), B=1000)
    br = paired_bootstrap((base - y) ** 2, (p - y) ** 2, B=1000)
    out["delta_log_loss"] = {k: ll[k] for k in ("delta", "ci", "se")}
    out["delta_brier"] = {k: br[k] for k in ("delta", "ci", "se")}
    return out


def verdict(c: dict[str, Any]) -> str:
    """'better' when a paired CI excludes 0 in f's favour (AUC up, or log loss down) and none against it."""
    da, dl = c.get("delta_auc"), c.get("delta_log_loss")
    up = (da and da["ci"][0] > 0) or (dl and dl["ci"][1] < 0)
    down = (da and da["ci"][1] < 0) or (dl and dl["ci"][0] > 0)
    return "better" if up and not down else "worse" if down and not up else "mixed" if up and down else "no clear difference"


def deciles(y: np.ndarray, p: np.ndarray, k: int = 10) -> list[dict[str, Any]]:
    """Calibration: mean p̂ against the observed bad rate in k equal-count groups of p̂."""
    o = np.argsort(p, kind="stable")
    out = []
    for i, idx in enumerate(np.array_split(o, k)):
        if len(idx):
            out.append({"group": i + 1, "n": int(len(idx)), "mean_p": float(p[idx].mean()), "bad_rate": float(y[idx].mean())})
    return out


def bad_rate_at(y: np.ndarray, p: np.ndarray, share: float) -> dict[str, Any]:
    """Approve the `share` of applicants with the lowest p̂: their observed bad rate."""
    n = max(1, int(round(share * len(p))))
    idx = np.argsort(p, kind="stable")[:n]
    return {"share": float(share), "approved": int(n), "bad_rate": float(y[idx].mean())}


def parcel_labels(base_all: np.ndarray, base_acc: np.ndarray, y_acc: np.ndarray, base_rej: np.ndarray, k: float,
                  seed: int = 7) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Parcelling: bands of the approved-only score over every training applicant; the declined in band b are given
    y = 1 with probability min(Q_MAX, k · the approved bad rate in b)."""
    edges = np.unique(np.quantile(base_all, np.linspace(0, 1, N_BANDS + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    ba, br = np.digitize(base_acc, edges[1:-1]), np.digitize(base_rej, edges[1:-1])
    rng = np.random.default_rng(seed)
    yr = np.zeros(len(base_rej), dtype=int)
    bands = []
    for b in range(len(edges) - 1):
        ma, mr = ba == b, br == b
        acc_rate = float(y_acc[ma].mean()) if ma.sum() >= 20 else float(base_rej[mr].mean()) if mr.any() else 0.0
        q = min(Q_MAX, k * acc_rate)
        yr[mr] = (rng.random(int(mr.sum())) < q).astype(int)
        bands.append({"band": b + 1, "from": float(edges[b]) if np.isfinite(edges[b]) else None,
                      "to": float(edges[b + 1]) if np.isfinite(edges[b + 1]) else None, "approved": int(ma.sum()),
                      "approved_bad_rate": float(y_acc[ma].mean()) if ma.any() else None, "declined": int(mr.sum()),
                      "assigned_bad_rate": q, "drawn_bad": int(yr[mr].sum())})
    return yr, bands


# ------------------------------------------------------------------------------------------------------------- run

def run(cfg: dict[str, Any], run_dir: Path, emit, merge) -> dict[str, Any]:
    from ..engine.progress import ProgressCallback
    from ..engine.worker import fit_predictor, prepare_fit
    from ..splits import holdout_mask

    label, pos, k = cfg["label"], cfg["positive_class"], float(cfg.get("k") or DEFAULT_K)
    df = frames.read(frames.resolve(cfg))
    splits = json.loads(Path(cfg["splits_path"]).read_text())
    fold = np.asarray(splits["fold"], dtype=int)
    is_h = holdout_mask(splits, len(df))
    keep = ~is_h & (fold != -2) if splits.get("params") else ~is_h  # the split's tuning slice trains too
    A = decided(df, cfg["found"])
    if df.loc[A, label].isna().any() or df.loc[~A, label].notna().mean() > 0.05:
        n_bad = int(df.loc[A, label].isna().sum())
        emit("log", level="warning", logger="ensemble", message=f"reject inference: {n_bad} approved rows without an outcome are left out")
    has_y = df[label].notna().to_numpy()
    inputs = [c for c in df.columns if c not in set(cfg["drop_columns"]) | {label, cfg["found"].get("decision_column")}]
    X = df[inputs]
    y_obs = (df[label] == pos).astype(int).to_numpy()
    y_true = truth_outcomes(df, label, pos)

    tr_acc = keep & A & has_y
    tr_rej = keep & ~A
    info = {"rows": int(len(df)), "train_approved": int(tr_acc.sum()), "train_declined": int(tr_rej.sum()),
            "holdout": int(is_h.sum()), "holdout_approved": int((is_h & A).sum()), "holdout_declined": int((is_h & ~A).sum()),
            "approval_rate": float(A.mean()), "approval_rate_train": float(A[keep].mean()), "features": inputs, "label": label,
            "split": {kk: v for kk, v in (cfg.get("split") or {}).items() if kk != "path"}}
    emit("data_ready", **info)
    merge(data=info)
    root = Path(cfg["predictor_path"])
    root.mkdir(parents=True, exist_ok=True)

    def fit(key: str, frame: pd.DataFrame, target: str, weight: bool = False):
        emit("ri_model_started", model=key, rows=int(len(frame)))
        emit("log", level="info", logger="ensemble", message=f"reject inference: fitting {key} on {len(frame):,} rows")
        fk = {**(cfg.get("fit_kwargs") or {})}
        fk["excluded_model_types"] = sorted(set(fk.get("excluded_model_types") or []) | set(NO_WEIGHTS))
        sub = {**cfg, "fit_kwargs": fk, "label": target, "problem_type": "binary", "eval_metric": "log_loss", "cost": None, "quantile": None,
               "monotone": None, "scorecard": None, "column_types": {}, "text_columns": [], "pseudolabel": None, "distill": None,
               "predictor_path": str(root / key), "predictor_kwargs": {"sample_weight": WEIGHT, "weight_evaluation": True} if weight else {}}
        prep = prepare_fit(sub, frame, frame.head(0), None, {})
        p, secs = fit_predictor(sub, prep, frame, str(root / key), callbacks=[ProgressCallback(emit)], emit=emit)
        emit("ri_model_finished", model=key, best_model=p.model_best, seconds=secs, rows=int(len(frame)))
        return p, {"rows": int(len(frame)), "best_model": p.model_best, "fit_seconds": secs, "n_models": len(p.model_names())}

    def proba(p, rows: pd.DataFrame) -> np.ndarray:
        return np.asarray(p.predict_proba(rows, as_multiclass=False), dtype=float)

    Y = "__ri_y"
    acc = X.loc[tr_acc].assign(**{Y: y_obs[tr_acc]})
    rej = X.loc[tr_rej]
    models: dict[str, Any] = {}
    fits: dict[str, Any] = {}
    detail: dict[str, Any] = {}

    models["approved_only"], fits["approved_only"] = fit("approved_only", acc, Y)
    b_all, b_acc, b_rej = proba(models["approved_only"], X.loc[keep]), proba(models["approved_only"], X.loc[tr_acc]), proba(models["approved_only"], rej)

    # Reweighting: the decision model P̂(A = 1 | x) on every training applicant, then w = 1 / P̂ on the approved
    am, afit = fit("acceptance", X.loc[keep].assign(**{Y: A[keep].astype(int)}), Y)
    pa = np.clip(proba(am, X.loc[tr_acc]), 1 / W_MAX, 1.0)
    w = 1 / pa
    w = w / w.mean()
    detail["reweighting"] = {"acceptance_model": afit, "w_max": W_MAX, "weights": {"min": float(w.min()), "median": float(np.median(w)),
                                                                                   "max": float(w.max()), "clipped": int((pa <= 1 / W_MAX + 1e-12).sum())},
                             "effective_n": float(w.sum() ** 2 / (w ** 2).sum())}
    models["reweighting"], fits["reweighting"] = fit("reweighting", acc.assign(**{WEIGHT: w}), Y, weight=True)

    # Parcelling: drawn outcomes for the declined, by band of the approved-only score
    yr, bands = parcel_labels(b_all, b_acc, y_obs[tr_acc], b_rej, k)
    detail["parcelling"] = {"bands": bands, "assigned_bad_rate": float(yr.mean())}
    models["parcelling"], fits["parcelling"] = fit("parcelling", pd.concat([acc, rej.assign(**{Y: yr})]), Y)

    # Fuzzy augmentation: each declined applicant as a bad with weight q and a good with weight 1 − q
    q = np.minimum(Q_MAX, k * b_rej)
    fz = pd.concat([acc.assign(**{WEIGHT: 1.0}), rej.assign(**{Y: 1, WEIGHT: q}), rej.assign(**{Y: 0, WEIGHT: 1 - q})])
    detail["fuzzy"] = {"mean_q": float(q.mean()), "declined_weight": float(len(q))}
    models["fuzzy"], fits["fuzzy"] = fit("fuzzy", fz, Y, weight=True)

    emit("log", level="info", logger="ensemble", message="reject inference: comparing on the holdout")
    H = X.loc[is_h]
    hp = {m: proba(p, H) for m, p in models.items()}
    hA = A[is_h]
    old_share = float(hA.mean())
    hy_obs = y_obs[is_h]
    ho_lab = hA & has_y[is_h]
    base = hp["approved_only"]
    approved_view = {m: compare(hy_obs[ho_lab], base[ho_lab], p[ho_lab]) for m, p in hp.items()}
    ttd = None
    if y_true is not None:
        yt = y_true[is_h]
        ttd = {"n": int(len(yt)), "bad_rate": float(yt.mean()), "bad_rate_approved": float(yt[hA].mean()) if hA.any() else None,
               "bad_rate_declined": float(yt[~hA].mean()) if (~hA).any() else None, "old_policy": {"share": old_share,
               "bad_rate": float(yt[hA].mean()) if hA.any() else None}, "methods": {}}
        for m, p in hp.items():
            c = compare(yt, base, p)
            c["declined"] = {"mean_p": float(p[~hA].mean()), "bad_rate": float(yt[~hA].mean())} if (~hA).any() else None
            c["approve_at_old_rate"] = bad_rate_at(yt, p, old_share)
            c["deciles"] = deciles(yt, p)
            c["verdict"] = "baseline" if m == "approved_only" else verdict(c)
            ttd["methods"][m] = c
        better = [m for m, c in ttd["methods"].items() if c["verdict"] == "better"]
        ttd["better"] = better
        ttd["best"] = min(ttd["methods"], key=lambda m: ttd["methods"][m]["log_loss"])
    pd.DataFrame({"row": np.where(is_h)[0], "approved": hA.astype(int), "y_observed": np.where(has_y[is_h], hy_obs, -1),
                  **({"y_ttd": y_true[is_h]} if y_true is not None else {}), **{f"p_{m}": p for m, p in hp.items()}}) \
        .to_csv(run_dir / "ri_holdout.csv", index=False)
    shift = {m: {"declined_mean_p": float(p[~hA].mean()) if (~hA).any() else None, "approved_mean_p": float(p[hA].mean()) if hA.any() else None}
             for m, p in hp.items()}
    out = {"label": label, "positive_class": pos, "decision_column": cfg["found"].get("decision_column"),
           "approved_value": cfg["found"].get("approved_value"), "k": k, "k_default": DEFAULT_K, "methods": METHODS,
           "fits": fits, "detail": detail, "approved_holdout": approved_view, "ttd": ttd, "shift": shift,
           "truth": "the declined applicants' outcomes in the dataset: used only for this check"
                    if ttd else None, "data": info, "detected": cfg.get("detected") or {}, "presets": cfg.get("presets"),
           "time_limit": cfg.get("time_limit")}
    head = (ttd or {}).get("methods", {}).get((ttd or {}).get("best") or "approved_only") or approved_view["approved_only"]
    merge(reject_inference=out, problem_type="binary", features=inputs,
          best_model=f"{METHODS[(ttd or {}).get('best') or 'approved_only']} (k = {k:g})",
          holdout={"metrics": {"auc": head.get("auc"), "log_loss": head.get("log_loss")}},
          eval_metric={"name": "auc", "greater_is_better": True})
    return {"better": (ttd or {}).get("better") or []}
