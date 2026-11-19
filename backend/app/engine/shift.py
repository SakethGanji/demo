"""F15 · Adversarial validation: is the new data different from the data the model learnt from?

Two sets of rows, R (the reference: a run's training rows, or the period before a cut-off) and N (the new rows: a
scored registry batch, the run's holdout, or the period after the cut-off), are stacked with an origin label
o = 1 for N and o = 0 for R, and an AutoGluon classifier learns to tell them apart:

    TabularPredictor(label=o, problem_type='binary', eval_metric='roc_auc').fit(…, num_bag_folds=5, num_stack_levels=0)

Every row is scored out of fold (`predict_proba_oof`: the fold child that did not see it), so the AUC is not
optimistic. AUC ≈ 0.5: the classifier cannot tell the sets apart, no detectable shift; AUC → 1: they are separable.

Evidence reported with the AUC:
  - a 95 % DeLong confidence interval (midrank structural components);
  - a label-permutation null: the AUC of the same out-of-fold scores against B shuffled origin labels → p-value;
  - a refit null: the whole fit repeated R times on shuffled origin labels (the pipeline's own AUC under "no shift");
  - the shifting features: AutoGluon's permutation importance on the out-of-fold rows of the bagged model
    (`feature_importance(data=None, feature_stage='transformed_model')`: each fold child is scored on its own
    out-of-fold rows), mapped back to the raw columns through the feature generator's links, with a 95 % t-interval;
  - PSI per column (registry/drift.py), the univariate view, next to it.

Importance weighting (density ratio): w(x) = p_N(x) / p_R(x) = [P(o=1|x) / P(o=0|x)] · (n_R / n_N), from the
out-of-fold P(o=1|x) of each reference row, clipped at quantiles of w and normalised to mean 1. Effective sample
size ESS = (Σw)² / Σw².

Runs in its own process:  python -m app.engine.shift <job_dir> [api pid]
State: <run_dir>/shift/<key>/state.json, written atomically; on finish the summary (`shift/1`) is merged into the run.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from ..stats import delong

SCHEMA = "shift/1"
ORIGIN = "__shift_origin"
WEIGHT_COLUMN = "shift_weight"
DEFAULT_FOLDS = 5
DEFAULT_NULL_REFITS = 3
DEFAULT_PERMUTATIONS = 2000
DEFAULT_CLIP = 0.01  # w clipped at its 1st and 99th percentiles
DEFAULT_SHUFFLES = 5  # permutation-importance shuffle sets
LEVEL = 0.95
TOP_K = 5

log = logging.getLogger("ensemble.shift")


# --------------------------------------------------------------------------- state


def shift_root(run_dir: Path) -> Path:
    return Path(run_dir) / "shift"


def job_dir(run_dir: Path, key: str) -> Path:
    return shift_root(run_dir) / key


def read_state(d: Path) -> dict[str, Any] | None:
    try:
        return json.loads((Path(d) / "state.json").read_text())
    except (OSError, ValueError):
        return None


def write_state(d: Path, state: dict[str, Any]) -> None:
    from .jsonsafe import jsonable

    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"state.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(jsonable(state), indent=1))
    os.replace(tmp, d / "state.json")


def summary(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """The small JSON the run payload carries and F17's Model Doctor reads (schema shift/1)."""
    if not state:
        return None
    res = state.get("result") or {}
    auc = res.get("auc") or {}
    null = res.get("null") or {}
    w = res.get("weights") or {}
    return {
        "schema": SCHEMA, "key": state.get("key"), "source": state.get("source"), "status": state.get("status"),
        "error": state.get("error"), "finished_at": state.get("finished_at"),
        "reference": (state.get("sets") or {}).get("reference", {}).get("name"),
        "new": (state.get("sets") or {}).get("new", {}).get("name"),
        "n_reference": (state.get("sets") or {}).get("reference", {}).get("rows"),
        "n_new": (state.get("sets") or {}).get("new", {}).get("rows"),
        "auc": auc.get("value"), "ci": auc.get("ci"), "level": LEVEL,
        "null": {"permutation_p": (null.get("permutation") or {}).get("p_value"),
                 "permutation_q95": (null.get("permutation") or {}).get("q95"),
                 "refit_aucs": (null.get("refit") or {}).get("aucs"),
                 "refit_mean": (null.get("refit") or {}).get("mean")} if null else None,
        "verdict": (res.get("verdict") or {}).get("label"), "verdict_reason": (res.get("verdict") or {}).get("reason"),
        "top_features": [{"feature": f["feature"], "importance": f["importance"], "ci": f.get("ci"), "psi": f.get("psi"),
                          "significant": f.get("significant")} for f in (res.get("features") or [])[:TOP_K]],
        "excluded": [e["column"] for e in state.get("excluded") or []],
        "ess_share": w.get("ess_share"),
    }


# --------------------------------------------------------------------------- statistics


def permutation_null(y: np.ndarray, s: np.ndarray, B: int, seed: int = 15) -> dict[str, Any]:
    """AUC of the fixed out-of-fold scores against B shuffles of the origin labels. AUC = (Σ ranks of o = 1 − m(m+1)/2)/(m·n)."""
    y = np.asarray(y).astype(int)
    r = rankdata(np.asarray(s, dtype=float))
    m, n = int(y.sum()), int(len(y) - y.sum())
    obs = float((r[y == 1].sum() - m * (m + 1) / 2) / (m * n))
    rng = np.random.default_rng(seed)
    null = np.empty(B)
    for b in range(B):
        idx = rng.permutation(len(y))[:m]
        null[b] = (r[idx].sum() - m * (m + 1) / 2) / (m * n)
    dev = np.abs(null - 0.5)
    p = float((1 + np.sum(dev >= abs(obs - 0.5) - 1e-15)) / (B + 1))
    return {"B": B, "p_value": p, "mean": float(null.mean()), "sd": float(null.std(ddof=1)),
            "q025": float(np.quantile(null, 0.025)), "q975": float(np.quantile(null, 0.975)),
            "q95": float(np.quantile(null, 0.95)), "max": float(null.max()),
            "test": "two-sided: |AUC₀ − 0.5| ≥ |AUC − 0.5|", "observed": obs,
            "hist": np.histogram(null, bins=30)[0].tolist(), "hist_edges": np.histogram(null, bins=30)[1].tolist()}


def verdict(auc: dict[str, Any], perm: dict[str, Any], refit: dict[str, Any] | None) -> dict[str, Any]:
    """A fixed rule (read by F17):
      no shift     the permutation p ≥ 0.05, or |AUC − 0.5| < 0.02 (not separable in practice);
      slight       separable but AUC < 0.60;  moderate  0.60 ≤ AUC < 0.70;  strong  AUC ≥ 0.70."""
    a, lo, hi = auc["value"], auc["ci"][0], auc["ci"][1]
    p = perm["p_value"]
    ref_max = max(refit["aucs"]) if refit and refit.get("aucs") else None
    beyond_refit = ref_max is None or a > ref_max
    if p >= 0.05 or abs(a - 0.5) < 0.02:
        label = "no shift"
        why = (f"AUC = {a:.3f} (95% CI {lo:.3f}–{hi:.3f}); the label-permutation test gives p = {p:.3g}"
               + (f" and the refit null reached up to {ref_max:.3f}" if ref_max is not None else "")
               + ": the classifier cannot tell the two sets apart better than chance.")
    else:
        label = "slight" if a < 0.6 else "moderate" if a < 0.7 else "strong"
        why = (f"AUC = {a:.3f} (95% CI {lo:.3f}–{hi:.3f}), permutation p = {p:.3g}"
               + (f", above every refit on shuffled labels (max {ref_max:.3f})" if ref_max is not None and beyond_refit else
                  f", but a refit on shuffled labels reached {ref_max:.3f}" if ref_max is not None else "")
               + f": a {label} shift — the new rows can be told apart from the reference rows.")
    return {"label": label, "reason": why, "shift": label != "no shift", "beyond_refit_null": beyond_refit,
            "rule": "no shift: p ≥ 0.05 or |AUC − 0.5| < 0.02 · slight: AUC < 0.60 · moderate: < 0.70 · strong: ≥ 0.70"}


def density_weights(p: np.ndarray, n_ref: int, n_new: int, clip: float) -> tuple[np.ndarray, dict[str, Any]]:
    """w = [p / (1 − p)] · (n_R / n_N) for each reference row (p = out-of-fold P(o = 1 | x)), clipped at the
    `clip` and 1 − `clip` quantiles, then normalised to mean 1. Returns (w, stats)."""
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    raw = p / (1 - p) * (n_ref / n_new)
    lo, hi = (float(np.quantile(raw, clip)), float(np.quantile(raw, 1 - clip))) if clip > 0 else (float(raw.min()), float(raw.max()))
    w = np.clip(raw, lo, hi)
    clipped = int(np.sum((raw < lo) | (raw > hi)))
    w = w / w.mean()
    ess = float(w.sum() ** 2 / np.sum(w ** 2))
    raw_n = raw / raw.mean()
    ess_raw = float(raw_n.sum() ** 2 / np.sum(raw_n ** 2))
    counts, edges = np.histogram(np.log10(w), bins=30)
    return w, {"column": WEIGHT_COLUMN, "formula": "w(x) = [P(o=1|x) / P(o=0|x)] · (n_R / n_N), clipped, normalised to mean 1",
               "n_ref": n_ref, "n_new": n_new, "prior_ratio": n_ref / n_new, "clip_quantile": clip,
               "clip_bounds_raw": [lo, hi], "clipped_rows": clipped, "min": float(w.min()), "max": float(w.max()),
               "median": float(np.median(w)), "q01": float(np.quantile(w, 0.01)), "q99": float(np.quantile(w, 0.99)),
               "ess": ess, "ess_share": ess / len(w), "ess_unclipped": ess_raw, "ess_unclipped_share": ess_raw / len(w),
               "sum_w": float(w.sum()), "sum_w2": float(np.sum(w ** 2)),
               "hist_log10": {"counts": counts.tolist(), "edges": edges.tolist()}}


def _t_ci(mean: float, sd: float, n: int) -> list[float] | None:
    from scipy.stats import t

    if n < 2 or not np.isfinite(sd):
        return None
    h = float(t.ppf(0.5 + LEVEL / 2, n - 1) * sd / np.sqrt(n))
    return [mean - h, mean + h]


def _roc(y: np.ndarray, s: np.ndarray, k: int = 101) -> list[list[float]]:
    from sklearn.metrics import roc_curve

    fpr, tpr, _ = roc_curve(y, s)
    grid = np.linspace(0, 1, k)
    return [[float(f), float(np.interp(f, fpr, tpr))] for f in grid]


# --------------------------------------------------------------------------- the job


def _cast(df: pd.DataFrame, kinds: dict[str, str]) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        k = kinds.get(c)
        if k == "number":
            out[c] = pd.to_numeric(out[c], errors="coerce")
        elif k in ("category", "text"):
            out[c] = out[c].map(lambda v: v if pd.isna(v) else str(v)).astype(object)
    return out


def load_sets(req: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    a = pd.read_csv(req["reference"]["csv"])
    if req["reference"].get("rows") is not None:
        a = a.iloc[np.asarray(req["reference"]["rows"], dtype=int)]
    b = pd.read_csv(req["new"]["csv"])
    if req["new"].get("rows") is not None:
        b = b.iloc[np.asarray(req["new"]["rows"], dtype=int)]
    return a, b


def _fit(frame: pd.DataFrame, path: Path, req: dict[str, Any], feature_metadata: Any):
    from autogluon.tabular import TabularPredictor

    shutil.rmtree(path, ignore_errors=True)
    fk = {"num_bag_folds": DEFAULT_FOLDS, "num_bag_sets": 1, "num_stack_levels": 0, "hyperparameters": {"GBM": {}},
          "fit_weighted_ensemble": False, **(req.get("fit_kwargs") or {})}
    if req.get("time_limit"):
        fk["time_limit"] = req["time_limit"]
    if feature_metadata is not None:
        fk["feature_metadata"] = feature_metadata
    pred = TabularPredictor(label=ORIGIN, problem_type="binary", eval_metric="roc_auc", path=str(path), verbosity=0)
    pred.fit(frame, **fk)
    return pred, fk


def _oof(pred) -> tuple[np.ndarray, str]:
    model = pred.model_best
    oof = pred.predict_proba_oof(model=model)
    col = 1 if 1 in oof.columns else oof.columns[-1]
    return oof[col].to_numpy(dtype=float), model


def _feature_metadata(frame: pd.DataFrame, kinds: dict[str, str]):
    text = [c for c, k in kinds.items() if k == "text" and c in frame.columns]
    if not text:
        return None
    from autogluon.common.features.feature_metadata import FeatureMetadata

    fm = FeatureMetadata.from_df(frame.drop(columns=[ORIGIN]))
    return fm.add_special_types({c: ["text"] for c in text})


def _watch_parent(ppid: int) -> None:
    def loop():
        while True:
            try:
                os.kill(ppid, 0)
            except ProcessLookupError:
                os._exit(3)
            except PermissionError:
                pass
            time.sleep(1)

    threading.Thread(target=loop, daemon=True).start()


def run(d: Path) -> int:
    from ..db import Database
    from ..registry.drift import feature_psi
    from .store import RunStore, now

    d = Path(d)
    req = json.loads((d / "request.json").read_text())
    store = RunStore(Database(Path(req["db_path"])))
    run_id = req["run_id"]
    state = read_state(d) or {}
    state.update({"schema": SCHEMA, "status": "running", "pid": os.getpid(), "started_at": now(), "error": None,
                  "result": None, "stage": "loading the two sets"})
    write_state(d, state)

    def save(stage: str | None = None):
        if stage:
            state["stage"] = stage
        write_state(d, state)

    try:
        t_all = time.time()
        a, b = load_sets(req)
        feats, kinds = req["features"], req["kinds"]
        frame = pd.concat([_cast(a[feats], kinds), _cast(b[feats], kinds)], ignore_index=True)
        y = np.r_[np.zeros(len(a), dtype=int), np.ones(len(b), dtype=int)]
        frame[ORIGIN] = y
        fm = _feature_metadata(frame, kinds)
        fits = d / "fits"
        save("fitting the origin classifier (bagged, out of fold)")
        t0 = time.time()
        pred, fk = _fit(frame, fits / "main", req, fm)
        fit_s = time.time() - t0
        s, model = _oof(pred)
        auc = delong(y, s)
        state["fit"] = {"model": model, "models": list(pred.model_names()), "fit_seconds": fit_s,
                        "fit_kwargs": {k: v for k, v in fk.items() if k != "feature_metadata"},
                        "call": "TabularPredictor(label='" + ORIGIN + "', problem_type='binary', eval_metric='roc_auc')"
                                ".fit(R ∪ N, " + ", ".join(f"{k}={v!r}" for k, v in fk.items() if k != "feature_metadata") + ")",
                        "num_bag_folds": fk.get("num_bag_folds"), "oof": "predict_proba_oof(model=" + repr(model) + ")"}
        save("permutation importance on the out-of-fold rows")
        t0 = time.time()
        fi = pred.feature_importance(data=None, model=model, feature_stage="transformed_model",
                                     num_shuffle_sets=int(req.get("shuffle_sets") or DEFAULT_SHUFFLES), silent=True)
        links = pred._learner.feature_generator.get_feature_links()  # raw column → the features made from it
        to_raw: dict[str, str] = {}
        for raw, outs in links.items():
            for o in outs:
                to_raw.setdefault(o, raw)
        agg: dict[str, dict[str, Any]] = {}
        for f, row in fi.iterrows():
            raw = to_raw.get(str(f), str(f))
            g = agg.setdefault(raw, {"importance": 0.0, "var": 0.0, "n": int(row["n"]), "parts": 0})
            g["importance"] += float(row["importance"])
            g["var"] += float(row["stddev"]) ** 2  # parts treated as independent: an approximation for text n-grams
            g["parts"] += 1
        fi_s = time.time() - t0
        save("PSI per column")
        features = []
        for c in feats:
            g = agg.get(c, {"importance": 0.0, "var": 0.0, "n": 0, "parts": 0})
            sd = float(np.sqrt(g["var"]))
            ci = _t_ci(g["importance"], sd, g["n"]) if g["n"] else None
            try:
                ps = feature_psi(c, kinds.get(c, "category"), a[c], b[c])
                psi, band = ps["psi"], ps["band"]
                stats = {k: ps.get(k) for k in ("mean_expected", "mean_actual", "std_expected", "std_actual") if ps.get(k) is not None}
            except Exception:  # noqa: BLE001
                psi, band, stats = None, None, {}
            features.append({"feature": c, "kind": kinds.get(c), "importance": g["importance"], "sd": sd, "n": g["n"],
                             "ci": ci, "ci_excludes_zero": bool(ci and ci[0] > 0), "significant": False, "model_features": g["parts"],
                             "psi": psi, "psi_band": band, **stats})
        features.sort(key=lambda f: -f["importance"])
        for i, f in enumerate(features):
            f["rank"] = i + 1
        R = int(req.get("null_refits", DEFAULT_NULL_REFITS))
        refit = None
        if R > 0:
            aucs, secs = [], []
            rng = np.random.default_rng(1500)
            for r in range(R):
                save(f"refit null {r + 1} of {R}: the same fit on shuffled origin labels")
                f2 = frame.copy()
                f2[ORIGIN] = rng.permutation(y)
                t0 = time.time()
                p2, _ = _fit(f2, fits / f"null{r}", req, fm)
                s2, _ = _oof(p2)
                secs.append(time.time() - t0)
                aucs.append(delong(f2[ORIGIN].to_numpy(), s2)["value"])
                del p2
                shutil.rmtree(fits / f"null{r}", ignore_errors=True)
            refit = {"R": R, "aucs": aucs, "mean": float(np.mean(aucs)), "max": float(np.max(aucs)), "min": float(np.min(aucs)),
                     "seconds": secs, "what": "the same fit call, origin labels shuffled (same m and n), out-of-fold AUC"}
        save("label-permutation null")
        perm = permutation_null(y, s, int(req.get("permutations") or DEFAULT_PERMUTATIONS))
        v = verdict(auc, perm, refit)
        for f in features:  # a column "shifts" only when the sets are separable at all; under no shift the
            # t-interval of a noise model's importance can exclude 0 (the 5 × k scores are not independent)
            f["significant"] = bool(f["ci_excludes_zero"] and v["shift"])
        w, wstats = density_weights(s[: len(a)], len(a), len(b), float(req.get("clip", DEFAULT_CLIP)))
        ref_rows = req["reference"].get("rows")
        wdf = pd.DataFrame({"row": ref_rows if ref_rows is not None else np.arange(len(a)), "p_new": s[: len(a)], "w": w})
        wdf.to_csv(d / "weights.csv", index=False)
        new_rows = req["new"].get("rows")
        pd.DataFrame({"set": ["reference"] * len(a) + ["new"] * len(b), "origin": y,
                      "row": np.r_[np.asarray(ref_rows if ref_rows is not None else np.arange(len(a))),
                                   np.asarray(new_rows if new_rows is not None else np.arange(len(b)))],
                      "p_new_oof": s}).to_csv(d / "scores.csv", index=False)
        # mean of the reference rows reweighted vs the new rows, per numeric feature: the weights should move the
        # reference towards the new rows (balance check)
        balance = []
        for f in features[:TOP_K]:
            c = f["feature"]
            if kinds.get(c) == "number":
                ra = pd.to_numeric(a[c], errors="coerce").to_numpy(dtype=float)
                nb = pd.to_numeric(b[c], errors="coerce").to_numpy(dtype=float)
                ok = np.isfinite(ra)
                balance.append({"feature": c, "mean_reference": float(np.nanmean(ra)), "mean_new": float(np.nanmean(nb)),
                                "mean_reference_weighted": float(np.sum(ra[ok] * w[ok]) / np.sum(w[ok]))})
        wstats["balance"] = balance
        state["result"] = {
            "auc": auc, "verdict": v, "features": features,
            "null": {"permutation": perm, "refit": refit},
            "roc": _roc(y, s),
            "score_hist": {"reference": np.histogram(s[y == 0], bins=25, range=(0, 1))[0].tolist(),
                           "new": np.histogram(s[y == 1], bins=25, range=(0, 1))[0].tolist(),
                           "edges": np.linspace(0, 1, 26).tolist()},
            "weights": wstats,
            "timing": {"fit_seconds": fit_s, "importance_seconds": fi_s, "total_seconds": time.time() - t_all},
            "importance_method": "AutoGluon permutation importance on out-of-fold rows (feature_stage='transformed_model'): "
                                 "the drop in out-of-fold ROC AUC when a column is shuffled, "
                                 f"{int(req.get('shuffle_sets') or DEFAULT_SHUFFLES)} shuffles × {fk.get('num_bag_folds')} fold models; "
                                 "model features made from one column are summed",
        }
        state.update({"status": "succeeded", "finished_at": now(), "stage": None})
        store.merge_result(run_id, shift=summary(state))
        save()
        del pred
        shutil.rmtree(fits, ignore_errors=True)
        return 0
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        log.error("shift check %s of %s failed:\n%s", d.name, run_id, tb)
        state.update({"status": "failed", "error": f"{type(e).__name__}: {e}", "traceback": tb[-3000:], "finished_at": now(),
                      "stage": None})
        store.merge_result(run_id, shift=summary(state))
        save()
        shutil.rmtree(d / "fits", ignore_errors=True)
        return 1


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(sys.argv) > 2:
        _watch_parent(int(sys.argv[2]))
    sys.exit(run(Path(sys.argv[1])))


if __name__ == "__main__":
    main()
