"""F12 · Learning curves: "do I need more data?"

The run's exact fit call (`worker.prepare_fit`: presets, hyperparameters, the F7 cost metric, F8 monotone subclasses,
the F11 scorecard, F6 tuning slice and group folds, text models, column types, post-fit steps, the threshold rule) is
repeated on nested subsamples of the run's own training rows: 10 / 25 / 50 % by default, each with S seeds, and 100 %
(the run itself). Every fit is scored on the run's fixed holdout, the same rows for every point.

Subsamples are drawn inside the run's split (F4 / F6 split records):
  - nested: for one seed, the 10 % rows are inside the 25 % rows, which are inside the 50 % rows;
  - stratified by the label where the split is (k_c(f) = max(min(n_c, 5), round(f · n_c)) rows of class c);
  - group-aware for group and time + group splits: whole entities are drawn, inside each of the split's bagging folds,
    so no customer is cut in half and every fold keeps about f of its rows;
  - the split's tuning slice (F6) is kept whole: it is not training data.

The curve: u(n) = A − b · (n / N)^(−c) in the metric's improvement direction (u = score if greater is better, else
−score), b ≥ 0, 0 < c ≤ 2, A ≤ the metric's optimum, fitted by least squares to the mean per fraction (c on a grid,
A and b in closed form). Extrapolated to 2N and 4N; the gain of the next doubling is Δ₂ = b · (1 − 2^(−c)).
Intervals: a parametric bootstrap of the mean per fraction, N(mean, σ_pop(f)²/k) with σ_pop(f) = sd_f/√(1 − f) (seeds
share one pool of N rows, so their spread is corrected to fresh samples of n rows); the 100 % point (one fit) gets the
largest subsample's σ_pop. B = 1,000 replicates, each refitted.

Verdict (a fixed rule, read by F17's Model Doctor):
  data-limited  the 95 % interval of Δ₂ lies above ε: another doubling of the data is expected to help;
  saturated     the interval lies below ε: more data of the same kind is not expected to help measurably;
  noisy         the interval contains ε: the seeds disagree too much to tell.
ε (`min_gain`) defaults to 2.5 % of the remaining gap to the metric's optimum, |u* − u(N)|.

Runs in its own process:  python -m app.engine.learning_curve <run_dir> [api pid]
State: <run_dir>/learning_curve/state.json (written atomically after every fit), and on finish the headline is merged
into the run's result as `learning_curve`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import frames
from .jobs import watch_parent
from .jsonsafe import read_json, write_json

SCHEMA = "learning_curve/1"
DEFAULT_FRACTIONS = (0.1, 0.25, 0.5, 1.0)
DEFAULT_SEEDS = 3
MIN_PER_CLASS = 5
B = 1000
LEVEL = 0.95
GAP_SHARE = 0.025  # ε = 2.5 % of the remaining gap to the optimum
C_GRID = np.round(np.linspace(0.02, 2.0, 100), 4)

log = logging.getLogger("ensemble.learning_curve")


def lc_dir(run_dir: Path) -> Path:
    return Path(run_dir) / "learning_curve"


def read_state(run_dir: Path) -> dict[str, Any] | None:
    return read_json(lc_dir(run_dir) / "state.json")


def write_state(run_dir: Path, state: dict[str, Any]) -> None:
    write_json(lc_dir(run_dir) / "state.json", state)


def headline(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the run payload carries (and F17 reads): status, progress and the verdict."""
    if not state:
        return None
    return {"schema": state.get("schema"), "status": state.get("status"), "fits_done": state.get("fits_done"),
            "fits_total": state.get("fits_total"), "error": state.get("error"), "verdict": state.get("verdict"),
            "finished_at": state.get("finished_at")}


# --------------------------------------------------------------------------- subsamples


def subsample_plan(train: pd.DataFrame, label: str, split: dict[str, Any], groups: pd.Series | None,
                   fold_col: str | None, fractions: list[float], seed: int, classification: bool) -> dict[float, np.ndarray]:
    """Nested subsets of the training rows (index labels of `train`) for one seed: {fraction: sorted index}.

    Group splits: whole groups, drawn inside each bagging fold (`fold_col`) so the folds keep their shares.
    Else stratified by the label when the split is stratified and the task is classification, else random."""
    rng = np.random.default_rng(1000 + seed)
    idx = train.index.to_numpy()
    out: dict[float, np.ndarray] = {}
    if groups is not None:
        g = groups.reindex(train.index).astype(str).to_numpy()
        folds = train[fold_col].to_numpy() if fold_col and fold_col in train.columns else np.zeros(len(train), dtype=int)
        orders = []  # per fold: groups in a random order with their cumulative row counts
        for k in np.unique(folds):
            sel = folds == k
            uniq, counts = np.unique(g[sel], return_counts=True)
            perm = rng.permutation(len(uniq))
            orders.append((sel, uniq[perm], np.cumsum(counts[perm]), int(sel.sum())))
        for f in fractions:
            keep = np.zeros(len(train), dtype=bool)
            for sel, ug, cum, n_k in orders:
                if f >= 1:
                    take = len(ug)
                else:  # the shortest prefix of whole groups holding at least f of the fold's rows (≥ 1 group)
                    take = max(1, int(np.searchsorted(cum, f * n_k - 1e-9) + 1))
                keep |= sel & np.isin(g, ug[:take])
            out[f] = np.sort(idx[keep])
        return out
    y = train[label].to_numpy()
    if classification and split.get("stratified"):
        perms = {c: rng.permutation(np.flatnonzero(y == c)) for c in pd.unique(y)}
        for f in fractions:
            parts = [p[:len(p) if f >= 1 else max(min(len(p), MIN_PER_CLASS), int(round(f * len(p))))] for p in perms.values()]
            out[f] = np.sort(idx[np.concatenate(parts)])
        return out
    perm = rng.permutation(len(idx))
    for f in fractions:
        out[f] = np.sort(idx[perm[:len(idx) if f >= 1 else max(2, int(round(f * len(idx))))]])
    return out


# --------------------------------------------------------------------------- the curve


def _fit_curve(x: np.ndarray, u: np.ndarray, u_opt: float | None) -> tuple[float, float, float, float]:
    """Least squares u ≈ A − b·x^(−c) (x = n/N), b ≥ 0, A ≤ u_opt, c on a grid. → (A, b, c, sse)."""
    best = (float(np.mean(u)), 0.0, 1.0, float(np.sum((u - np.mean(u)) ** 2)))
    for c in C_GRID:
        z = x ** (-c)
        zc, uc = z - z.mean(), u - u.mean()
        szz = float(np.sum(zc * zc))
        b = max(0.0, -float(np.sum(zc * uc)) / szz) if szz > 0 else 0.0
        A = float(u.mean() + b * z.mean())
        if u_opt is not None and A > u_opt:  # A at the optimum; b ≥ 0 by least squares for that A
            A = u_opt
            b = max(0.0, float(np.sum((A - u) * z)) / float(np.sum(z * z)))
        sse = float(np.sum((u - (A - b * z)) ** 2))
        if sse < best[3] - 1e-15:
            best = (A, b, float(c), sse)
    return best


def analyse(points: list[dict[str, Any]], n_full: int, gib: bool, optimum: float | None, min_gain: float | None,
            metric: str) -> dict[str, Any]:
    """Summary per fraction, the power-law fit with bootstrap bands, extrapolation to 2N / 4N and the verdict."""
    sign = 1.0 if gib else -1.0
    u_opt = None if optimum is None or not np.isfinite(optimum) else sign * float(optimum)
    fr = sorted({p["fraction"] for p in points})
    by = {f: [p for p in points if p["fraction"] == f] for f in fr}
    t_crit = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}
    summary = []
    for f in fr:
        s = np.array([p["score"] for p in by[f]], dtype=float)
        k = len(s)
        sd = float(s.std(ddof=1)) if k > 1 else None
        half = (t_crit.get(k - 1, 1.96) * sd / np.sqrt(k)) if sd is not None else None
        summary.append({"fraction": f, "n": int(round(np.mean([p["n"] for p in by[f]]))), "k": k,
                        "mean": float(s.mean()), "sd": sd, "ci": [float(s.mean() - half), float(s.mean() + half)] if half is not None else None,
                        "scores": [float(v) for v in s]})
    xn = np.array([row["n"] / n_full for row in summary])
    u = sign * np.array([row["mean"] for row in summary])
    A, b_, c, sse = _fit_curve(xn, u, u_opt)

    def at(A_, b, c_, xs):
        return A_ - b * np.asarray(xs, dtype=float) ** (-c_)

    u_N = float(at(A, b_, c, 1.0))
    gain = b_ * (1 - 2 ** (-c))
    # Parametric bootstrap of the means. Seeds of one fraction draw from the same pool of N rows, so their spread
    # understates the spread over fresh samples of n rows by the finite-population factor √(1 − f): undone here,
    # σ_pop(f) = sd_f / √(1 − f). The 100 % point is one fit with no seed spread; it gets σ_pop of the largest
    # subsample (a model's spread shrinks with n, so this errs wide). Each replicate draws every mean, refits.
    sd_pool = [r["sd"] / np.sqrt(1 - r["fraction"]) for r in summary if r["sd"] is not None and r["fraction"] < 1]
    sd_last = next((r["sd"] / np.sqrt(1 - r["fraction"]) for r in reversed(summary) if r["sd"] is not None and r["fraction"] < 1), None)
    if sd_last is None:
        sd_last = float(np.sqrt(np.mean(np.square(sd_pool)))) if sd_pool else 0.0
    se = []
    for r in summary:
        if r["fraction"] >= 1:
            se.append(sd_last / np.sqrt(r["k"]))
        else:
            sdp = r["sd"] / np.sqrt(1 - r["fraction"]) if r["sd"] is not None else sd_last
            se.append(sdp / np.sqrt(r["k"]))
    se = np.array(se, dtype=float)
    rng = np.random.default_rng(12)
    grid = np.unique(np.concatenate([np.geomspace(xn.min(), 4.0, 60), [1.0, 2.0, 4.0]]))
    boot = np.empty((B, 4))
    band = np.empty((B, len(grid)))
    for i in range(B):
        ub = u + rng.normal(0.0, 1.0, len(u)) * se
        Ab, bb, cb, _ = _fit_curve(xn, ub, u_opt)
        boot[i] = [bb * (1 - 2 ** (-cb)), at(Ab, bb, cb, 2.0), at(Ab, bb, cb, 4.0), at(Ab, bb, cb, 1.0)]
        band[i] = at(Ab, bb, cb, grid)
    lo_q, hi_q = (1 - LEVEL) / 2, 1 - (1 - LEVEL) / 2

    def ci(col):
        return [float(np.quantile(col, lo_q)), float(np.quantile(col, hi_q))]

    def nat(v):  # back to the metric's own units
        return sign * v

    gain_ci = ci(boot[:, 0])
    u2, u4 = float(at(A, b_, c, 2.0)), float(at(A, b_, c, 4.0))
    ci2 = sorted(nat(np.array(ci(boot[:, 1]))).tolist())
    ci4 = sorted(nat(np.array(ci(boot[:, 2]))).tolist())

    gap = (u_opt - u_N) if u_opt is not None else None
    if min_gain is not None:
        eps, eps_rule = float(min_gain), "set in the request"
    elif gap is not None and gap > 1e-12:
        eps, eps_rule = GAP_SHARE * gap, f"{GAP_SHARE:.1%} of the remaining gap to the optimum, |u* − u(N)| = {gap:.4g}"
    else:
        eps, eps_rule = 0.005 * abs(u_N) or 1e-3, "0.5 % of |u(N)| (the metric has no finite optimum)"

    seed_sds = [r["sd"] for r in summary if r["sd"] is not None]
    seed_sd = float(np.sqrt(np.mean(np.square(seed_sds)))) if seed_sds else None
    sub = [r for r in summary if r["fraction"] < 1]
    last = sub[-1] if sub else None
    full = next((r for r in summary if r["fraction"] >= 1), None)
    observed = None
    if last and full:
        doublings = float(np.log2(full["n"] / last["n"]))
        d = sign * (full["mean"] - last["mean"])
        bs = [sign * (full["mean"] - np.random.default_rng(i).choice(last["scores"], len(last["scores"])).mean()) for i in range(B)] \
            if len(last["scores"]) > 1 else [d]
        observed = {"from_fraction": last["fraction"], "from_n": last["n"], "to_n": full["n"], "doublings": doublings,
                    "gain": d, "gain_per_doubling": d / doublings if doublings > 0 else None,
                    "ci": [float(np.quantile(bs, lo_q)), float(np.quantile(bs, hi_q))]}
    monotone = all(sign * (b2["mean"] - b1["mean"]) >= -(max(b1["sd"] or 0, b2["sd"] or 0)) for b1, b2 in zip(summary, summary[1:]))

    if len(fr) < 3:
        label, why = "noisy", f"only {len(fr)} sample sizes were fitted; a curve needs at least 3"
    elif gain_ci[0] > eps:
        label = "data-limited"
        why = (f"doubling the data is expected to improve {metric} by {gain:.4f} (95% CI {gain_ci[0]:.4f} to {gain_ci[1]:.4f}), "
               f"all of it above the {eps:.4f} that counts as a real gain")
    elif gain_ci[1] < eps:
        label = "saturated"
        why = (f"doubling the data is expected to improve {metric} by at most {gain_ci[1]:.4f} (95% CI upper end), below the "
               f"{eps:.4f} that counts as a real gain: more rows of the same kind will not move it")
    else:
        label = "noisy"
        why = (f"the gain of a doubling, {gain:.4f}, has a 95% CI from {gain_ci[0]:.4f} to {gain_ci[1]:.4f} that contains "
               f"the {eps:.4f} that counts as a real gain: the seeds disagree too much to tell")

    verdict = {
        "schema": SCHEMA, "label": label, "reason": why, "metric": metric, "greater_is_better": gib,
        "slope_per_doubling": {"value": float(gain), "ci": gain_ci, "level": LEVEL,
                               "units": f"{metric} per doubling of n, improvement direction (+ = better)"},
        "extrapolation": {
            "n_full": n_full, "score_at_n": nat(u_N),
            "x2": {"n": 2 * n_full, "score": nat(u2), "ci": ci2, "gain": u2 - u_N},
            "x4": {"n": 4 * n_full, "score": nat(u4), "ci": ci4, "gain": u4 - u_N},
        },
        "evidence": {
            "min_gain": eps, "min_gain_rule": eps_rule, "seed_sd": seed_sd, "observed_last_step": observed,
            "monotone_within_noise": monotone, "n_points": len(points), "fractions": fr,
            "seeds": max((r["k"] for r in sub), default=0), "fit_rmse": float(np.sqrt(sse / len(fr))),
            "power_law": {"A": A, "b": b_, "c": c, "asymptote": nat(A)},
            "B": B, "se_means": [float(v) for v in se], "sd_population_full": float(sd_last),
            "interval_method": "parametric bootstrap of the mean per fraction: N(mean, σ_pop(f)² / k), σ_pop(f) = sd_f / √(1 − f); "
                               "the 100 % point gets σ_pop of the largest subsample; power law refitted B times",
        },
    }
    curve = [[float(g * n_full), nat(float(at(A, b_, c, g)))] for g in grid]
    lo_b, hi_b = np.quantile(band, lo_q, axis=0), np.quantile(band, hi_q, axis=0)
    band_out = [[float(g * n_full), *sorted([nat(float(l)), nat(float(h))])] for g, l, h in zip(grid, lo_b, hi_b)]
    return {"summary": summary, "fit": {"form": "u(n) = A − b·(n/N)^(−c)", "A": A, "b": b_, "c": c, "sse": sse,
                                         "sign": sign, "u_optimum": u_opt, "x": xn.tolist()},
            "curve": curve, "band": band_out, "verdict": verdict}


# --------------------------------------------------------------------------- the job


def _rows_sha(index: np.ndarray) -> str:
    return hashlib.sha256(json.dumps([int(v) for v in np.sort(index)]).encode()).hexdigest()[:16]


def run(run_dir: Path) -> int:
    from ..db import Database
    from . import worker as w
    from .store import RunStore, now

    run_dir = Path(run_dir)
    d = lc_dir(run_dir)
    req = json.loads((d / "request.json").read_text())
    cfg = json.loads((run_dir / "config.json").read_text())
    store = RunStore(Database(Path(cfg["db_path"])))
    run_id = cfg["run_id"]
    rec = store.get(run_id) or {}
    res = rec.get("result") or {}
    state = read_state(run_dir) or {}
    state.update({"schema": SCHEMA, "status": "running", "pid": os.getpid(), "started_at": now(), "request": req,
                  "points": [], "fits_done": 0, "error": None, "verdict": None})
    write_state(run_dir, state)

    def save():
        write_state(run_dir, state)

    try:
        import ensemble_models  # noqa: F401 — custom models (TF-IDF, cost, monotone, scorecard)

        train, holdout, info, tuning = w.load_frames(cfg)
        label = cfg["label"]
        split = cfg.get("split") or {}
        strategy = split.get("strategy", "random-stratified")
        group_col = (split.get("params") or {}).get("group_column") if strategy in ("group", "time-group") else None
        groups = frames.read(frames.resolve(cfg), columns=[group_col])[group_col] if group_col else None
        fold_col = w.GROUP_FOLD_COLUMN if w.GROUP_FOLD_COLUMN in train.columns else None
        prep_full = w.prepare_fit(cfg, train, holdout, tuning, info)  # applies column types to the frames once
        em_info = res.get("eval_metric") or {}
        metric = em_info.get("name") or cfg.get("eval_metric")
        problem = res.get("problem_type")
        classification = problem in ("binary", "multiclass")
        fractions = sorted(float(f) for f in req["fractions"])
        seeds = int(req["seeds"])
        sub_fr = [f for f in fractions if f < 1]
        plans = {s: subsample_plan(train, label, split, groups, fold_col, fractions, s, classification) for s in range(seeds)}
        # Checks the smoke reads: nested per seed, whole groups, one holdout for every point.
        nested = all(set(plans[s][a]).issubset(plans[s][b]) for s in plans for a, b in zip(fractions, fractions[1:]))
        whole = None
        if groups is not None:
            g_train = groups.reindex(train.index).astype(str)
            sizes = g_train.value_counts()
            whole = True
            for s in plans:
                for ix in plans[s].values():
                    vc = g_train.loc[ix].value_counts()
                    whole &= bool((vc.to_numpy() == sizes.loc[vc.index].to_numpy()).all())
        holdout_sha = _rows_sha(holdout.index.to_numpy())
        full_from_run = bool(req.get("reuse_full", True)) and 1.0 in fractions
        state.update({
            "metric": {"name": metric, "greater_is_better": em_info.get("greater_is_better", True), "optimum": em_info.get("optimum")},
            "problem_type": problem, "n_full": len(train), "holdout_rows": len(holdout), "holdout_sha": holdout_sha,
            "tuning_rows": 0 if tuning is None else len(tuning),
            "split": {"key": split.get("key"), "sha256": split.get("sha256"), "strategy": strategy, "stratified": split.get("stratified"),
                      "group_column": group_col,
                      "subsample": ("whole groups of " + group_col + ", drawn inside each of the split's bagging folds") if groups is not None
                      else ("stratified by the label (≥ " + str(MIN_PER_CLASS) + " rows per class)") if classification and split.get("stratified")
                      else "random rows"},
            "nested": nested, "groups_whole": whole,
            "fit_call": {"predictor_kwargs": prep_full["resolved"]["predictor_kwargs"],
                         "fit_kwargs": prep_full["resolved"]["fit_kwargs"], "notes": prep_full["resolved"]["notes"],
                         "time_limit": req.get("time_limit")},
            "fits_total": len(sub_fr) * seeds + (0 if full_from_run else (1 if 1.0 in fractions else 0)),
        })
        save()
        if full_from_run:  # the run itself is the 100 % point: the same call on all the training rows
            h = res.get("holdout") or {}
            state["points"].append({"fraction": 1.0, "seed": None, "n": len(train), "score": float(h["metrics"][metric]),
                                    "source": "run", "best_model": res.get("best_model"), "fit_seconds": res.get("fit_seconds"),
                                    "models": [r["model"] for r in res.get("leaderboard") or []],
                                    "holdout_sha": holdout_sha, "rows_sha": _rows_sha(train.index.to_numpy())})
            save()
        jobs = [(f, s) for f in sub_fr for s in range(seeds)]
        if 1.0 in fractions and not full_from_run:
            jobs.append((1.0, 0))
        fits = d / "fits"
        for f, s in jobs:
            state["current"] = {"fraction": f, "seed": s}
            save()
            ix = plans[s][f]
            sub = train.loc[ix].copy()
            sub_cfg = dict(cfg)
            if req.get("time_limit") is not None:
                sub_cfg["time_limit"] = req["time_limit"]
            sub_cfg["predictor_path"] = str(fits / f"f{int(round(f * 1000)):04d}_s{s}")
            prep = w.prepare_fit(sub_cfg, sub, holdout.copy(), None if tuning is None else tuning.copy(), info)
            if prep["unlabeled"] is not None:
                prep["unlabeled"] = prep_full["unlabeled"]
            shutil.rmtree(sub_cfg["predictor_path"], ignore_errors=True)
            t0 = time.time()
            predictor, fit_s = w.fit_predictor(sub_cfg, prep, sub, sub_cfg["predictor_path"])
            w._decide_threshold(sub_cfg, predictor, sub, lambda *a, **k: None)
            hm = w.holdout_metrics(predictor, holdout, label)
            pt = {"fraction": f, "seed": s, "n": len(sub), "score": float(hm["metrics"][metric]), "source": "subsample",
                  "best_model": predictor.model_best, "n_models": len(predictor.model_names()),
                  "models": list(predictor.model_names()), "fit_seconds": fit_s,
                  "seconds": time.time() - t0, "holdout_sha": holdout_sha, "rows_sha": _rows_sha(ix),
                  "classes": int(sub[label].nunique()) if classification else None,
                  "groups": int(groups.reindex(ix).nunique()) if groups is not None else None}
            del predictor
            shutil.rmtree(sub_cfg["predictor_path"], ignore_errors=True)  # the scores are kept, the models are not
            state["points"].append(pt)
            state["fits_done"] = len([p for p in state["points"] if p["source"] == "subsample"])
            save()
        state.pop("current", None)
        mi = state["metric"]
        out = analyse(state["points"], len(train), bool(mi["greater_is_better"]), mi.get("optimum"), req.get("min_gain"), metric)
        state.update(out)
        state.update({"status": "succeeded", "finished_at": now()})
        store.merge_result(run_id, learning_curve=headline(state))  # the run payload first: readers poll the state file
        save()
        shutil.rmtree(fits, ignore_errors=True)
        return 0
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        log.error("learning curve of %s failed:\n%s", run_id, tb)
        state.update({"status": "failed", "error": f"{type(e).__name__}: {e}", "traceback": tb[-3000:], "finished_at": now()})
        store.merge_result(run_id, learning_curve=headline(state))
        save()
        shutil.rmtree(d / "fits", ignore_errors=True)
        return 1


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(sys.argv) > 2:
        watch_parent(int(sys.argv[2]), anchor=True)
    sys.exit(run(Path(sys.argv[1])))


if __name__ == "__main__":
    main()
