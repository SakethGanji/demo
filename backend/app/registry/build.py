"""Build a registered model version from a finished run, in a worker process (`pool.HeavyPool`).

The run's predictor is never changed. The version gets its own predictor folder:

1. clone        `predictor.clone(work)`: a full copy to work on.
2. refit_full   (option) `refit_full(model='best')`, or `model='all'` when a latency budget is also given, so every
                candidate has a refit copy: the models are retrained on train + validation data with no bagging,
                which is faster to predict (one model per family instead of k fold models).
3. infer_limit  (option) a latency budget in seconds per row. Every candidate model's batch prediction time is
                measured on the holdout (AutoGluon's own `predict_1_time` when the run was fit with an
                infer_limit), and the best-validation model within the budget becomes `model_best`
                (`set_model_best`), as `get_model_best(infer_limit=…)` does.
4. deploy copy  (option) `clone_for_deployment(dest)`: only the best model and the models it needs are kept,
                and `save_space()` drops what prediction does not use. Otherwise `clone(dest)`.
5. measure      size on disk, batch latency (µs per row, p50 and p95 over repeats), single-row latency (ms, p50 and
                p95), holdout metrics with 95% bootstrap intervals, all for the source run and for the version,
                so the trade-off is measured, not estimated.
6. hash         SHA-256 of every file of the version's predictor (the model hash in the audit log) and of the
                dataset file.
7. mlflow       the source run logged (if not already) and a model version registered with the version's folder
                as its source.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..db import Database
from ..engine.jsonsafe import jsonable
from ..engine.store import RunStore
from . import fastpath
from .store import RegistryStore


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


def dir_hash(path: Path) -> str:
    h = hashlib.sha256()
    root = Path(path)
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(root)).encode())
        h.update(b"\0")
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pct(xs: list[float], q: float) -> float | None:
    return float(np.percentile(np.asarray(xs, dtype=float), q)) if len(xs) else None


def measure_latency(predictor, X: pd.DataFrame, model: str | None = None, *, batch_rows: int = 1000, repeats: int = 7,
                    singles: int = 40, budget_s: float = 3.0) -> dict[str, Any]:
    """Batch: predict on `batch_rows` holdout rows `repeats` times (after one warm-up) → µs per row.
    Single row: `singles` one-row calls → ms per call. Both as p50 / p95. Models are persisted in memory first."""
    try:
        predictor.persist(models=model or "best", with_ancestors=True)
    except Exception:  # noqa: BLE001 — persisting is an optimisation
        pass
    proba = predictor.problem_type in ("binary", "multiclass")
    fn = (lambda d: predictor.predict_proba(d, model=model)) if proba else (lambda d: predictor.predict(d, model=model))
    xb = X.iloc[:batch_rows]
    if len(xb) < batch_rows and len(xb) > 0:
        xb = pd.concat([xb] * int(np.ceil(batch_rows / len(xb))), ignore_index=True).iloc[:batch_rows]
    fn(xb.iloc[:50])
    per_row = []
    t_end = time.time() + budget_s
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn(xb)
        per_row.append((time.perf_counter() - t0) / len(xb) * 1e6)
        if time.time() > t_end and len(per_row) >= 3:
            break
    single = []
    t_end = time.time() + budget_s
    for i in range(singles):
        row = X.iloc[[i % len(X)]]
        t0 = time.perf_counter()
        fn(row)
        single.append((time.perf_counter() - t0) * 1e3)
        if time.time() > t_end and len(single) >= 10:
            break
    return {"batch_rows": len(xb), "batch_repeats": len(per_row), "us_per_row_p50": _pct(per_row, 50),
            "us_per_row_p95": _pct(per_row, 95), "us_per_row": per_row, "single_calls": len(single),
            "single_ms_p50": _pct(single, 50), "single_ms_p95": _pct(single, 95),
            "throughput_rows_per_s": 1e6 / _pct(per_row, 50) if per_row else None}


# --------------------------------------------------------------------------- holdout metrics with intervals


def _metric_fns(problem_type: str, positive: Any, classes: list[Any]):
    from sklearn import metrics as m

    if problem_type == "binary":
        def pos(y):
            return (np.asarray(y).astype(str) == str(positive)).astype(int)

        return {
            "accuracy": ("acc = (TP + TN) / n", lambda y, yp, P: m.accuracy_score(pos(y), pos(yp))),
            "balanced_accuracy": ("bacc = ½ (TPR + TNR)", lambda y, yp, P: m.balanced_accuracy_score(pos(y), pos(yp))),
            "f1": ("F₁ = 2PR / (P + R)", lambda y, yp, P: m.f1_score(pos(y), pos(yp), zero_division=0)),
            "precision": ("P = TP / (TP + FP)", lambda y, yp, P: m.precision_score(pos(y), pos(yp), zero_division=0)),
            "recall": ("R = TP / (TP + FN)", lambda y, yp, P: m.recall_score(pos(y), pos(yp), zero_division=0)),
            "roc_auc": ("AUC = P(p̂₊ > p̂₋)", lambda y, yp, P: m.roc_auc_score(pos(y), P[:, classes.index(positive)])),
            "average_precision": ("AP = Σ (Rₖ − Rₖ₋₁) Pₖ", lambda y, yp, P: m.average_precision_score(pos(y), P[:, classes.index(positive)])),
            "brier": ("BS = (1/n) Σ (p̂ᵢ − yᵢ)²", lambda y, yp, P: m.brier_score_loss(pos(y), P[:, classes.index(positive)])),
            "log_loss": ("ℒ = −(1/n) Σ log p̂(yᵢ ∣ xᵢ)", lambda y, yp, P: m.log_loss(pos(y), P[:, classes.index(positive)], labels=[0, 1])),
            "mcc": ("MCC = (TP·TN − FP·FN) / √(…)", lambda y, yp, P: m.matthews_corrcoef(pos(y), pos(yp))),
        }
    if problem_type == "multiclass":
        cl = [str(c) for c in classes]
        return {
            "accuracy": ("acc = (1/n) Σ 𝟙[ŷᵢ = yᵢ]", lambda y, yp, P: m.accuracy_score(np.asarray(y).astype(str), np.asarray(yp).astype(str))),
            "balanced_accuracy": ("bacc = (1/K) Σₖ recallₖ", lambda y, yp, P: m.balanced_accuracy_score(np.asarray(y).astype(str), np.asarray(yp).astype(str))),
            "f1_macro": ("F₁ᵐᵃᶜʳᵒ = (1/K) Σₖ F₁,ₖ", lambda y, yp, P: m.f1_score(np.asarray(y).astype(str), np.asarray(yp).astype(str), average="macro", zero_division=0)),
            "log_loss": ("ℒ = −(1/n) Σ log p̂(yᵢ ∣ xᵢ)", lambda y, yp, P: m.log_loss(np.asarray(y).astype(str), P, labels=cl)),
        }
    return {
        "rmse": ("RMSE = √((1/n) Σ (yᵢ − ŷᵢ)²)", lambda y, yp, P: float(np.sqrt(m.mean_squared_error(y, yp)))),
        "mae": ("MAE = (1/n) Σ |yᵢ − ŷᵢ|", lambda y, yp, P: m.mean_absolute_error(y, yp)),
        "r2": ("R² = 1 − SS_res / SS_tot", lambda y, yp, P: m.r2_score(y, yp)),
    }


def metrics_with_ci(hp: pd.DataFrame, problem_type: str, positive: Any, classes: list[Any] | None,
                    B: int = 500, seed: int = 0) -> dict[str, Any]:
    """Point estimates on the holdout and 95% percentile-bootstrap intervals (B resamples of the holdout rows)."""
    if problem_type == "quantile":  # F9: pinball per level, the main interval's coverage and width
        from ..engine import quantile as qt

        saved = qt.from_saved(hp)
        return qt.bootstrap(hp["y_true"].to_numpy(dtype=float), saved[1], saved[0], B=B, seed=seed)
    classes = [str(c) for c in classes] if classes else []
    pos = str(positive) if positive is not None else None
    y = hp["y_true"].astype(str).to_numpy() if problem_type != "regression" else hp["y_true"].to_numpy(dtype=float)
    yp = hp["y_pred"].astype(str).to_numpy() if problem_type != "regression" else hp["y_pred"].to_numpy(dtype=float)
    P = hp[[f"p_{c}" for c in classes]].to_numpy(dtype=float) if classes else None
    fns = _metric_fns(problem_type, pos, classes)
    rng = np.random.default_rng(seed)
    n = len(hp)
    idx = rng.integers(0, n, size=(B, n))
    out = {}
    for name, (formula, fn) in fns.items():
        try:
            point = float(fn(y, yp, P))
        except Exception:  # noqa: BLE001
            continue
        boots = []
        for b in range(B):
            i = idx[b]
            try:
                boots.append(float(fn(y[i], yp[i], P[i] if P is not None else None)))
            except Exception:  # noqa: BLE001 — a resample with one class
                pass
        lo, hi = (float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))) if boots else (None, None)
        out[name] = {"value": point, "ci_low": lo, "ci_high": hi, "formula": formula, "B": len(boots)}
    return {"metrics": out, "n": n, "B": B, "seed": seed, "method": "percentile bootstrap over holdout rows, 95%"}


def holdout_predictions(predictor, holdout: pd.DataFrame, label: str) -> pd.DataFrame:
    from ..engine.worker import holdout_prediction_frame

    return holdout_prediction_frame(predictor, holdout, label)


# --------------------------------------------------------------------------- the build


def _models_summary(p) -> list[dict[str, Any]]:
    lb = p.leaderboard(silent=True)
    return jsonable(lb[["model", "score_val", "stack_level", "can_infer", "fit_time", "pred_time_val"]].to_dict(orient="records"))


def _deps(p, model: str) -> list[str]:
    try:
        return list(p._trainer.get_minimum_model_set(model))
    except Exception:  # noqa: BLE001
        return [model]


def measure_candidates(p, X: pd.DataFrame, candidates: list[str], batch_rows: int, ag_times: dict[str, float | None]) -> list[dict[str, Any]]:
    from ..engine.worker import _natural

    lb = p.leaderboard(silent=True).set_index("model")
    out = []
    for m in candidates:
        parent = p._trainer.get_model_attribute(m, "refit_full_parent", default=None) if hasattr(p._trainer, "get_model_attribute") else None
        sv = lb.loc[m, "score_val"] if m in lb.index else None
        if (sv is None or pd.isna(sv)) and parent and parent in lb.index:
            sv = lb.loc[parent, "score_val"]
        t = measure_latency(p, X, model=m, batch_rows=batch_rows, repeats=3, singles=0, budget_s=4.0) if len(X) else {}
        out.append({"model": m, "refit_parent": parent, "score_val": None if sv is None or pd.isna(sv) else float(sv),
                    "score_val_natural": None if sv is None or pd.isna(sv) else _natural(p.eval_metric, float(sv)),
                    "us_per_row": t.get("us_per_row_p50"), "ag_predict_1_time_us": None if ag_times.get(m) is None else ag_times[m] * 1e6,
                    "n_models": len(_deps(p, m))})
    return out


def build_version(db_path: str, version_id: str, registry_dir: str) -> dict[str, Any]:
    db = Database(Path(db_path))
    reg, runs = RegistryStore(db), RunStore(db)
    v = reg.get_version(version_id)
    run = runs.get(v["run_id"])
    cfg = run["config"]
    opts = v["options"]
    vdir = Path(registry_dir) / v["model_name"] / f"v{v['version']}"
    work, dest = vdir / "work", vdir / "predictor"
    data_dir = Path(db_path).parent
    step = lambda name, status, **kw: reg.step(version_id, name, status, **kw)  # noqa: E731
    if cfg.get("kind") in ("forecast", "anomaly"):
        return build_other(db_path, version_id, registry_dir)
    try:
        from autogluon.tabular import TabularPredictor

        import ensemble_models  # noqa: F401

        from ..engine import serving
        from ..splits import load_splits

        vdir.mkdir(parents=True, exist_ok=True)
        holdout = serving.holdout_frame(cfg["csv_path"], load_splits(Path(cfg["splits_path"])), cfg["label"], cfg["drop_columns"])
        X = holdout.drop(columns=[cfg["label"]])

        step("clone", "running")
        t0 = time.time()
        src = TabularPredictor.load(cfg["predictor_path"], verbosity=0, require_py_version_match=False)
        if work.exists():
            shutil.rmtree(work)
        p = src.clone(str(work), return_clone=True, dirs_exist_ok=True)
        step("clone", "done", seconds=time.time() - t0, size_bytes=dir_size(work), source_best=src.model_best)

        # 2. refit_full
        refit_info = None
        if opts.get("refit_full"):
            step("refit_full", "running")
            t0 = time.time()
            if src.model_best.endswith("_FULL"):
                refit_info = {"skipped": True, "reason": f"the run's best model {src.model_best} is already a refit copy"}
                step("refit_full", "skipped", reason=refit_info["reason"])
            else:
                many = opts.get("infer_limit") or opts.get("serve_fastest")
                which = "all" if many else (opts.get("serve_model") or "best")
                mapping = p.refit_full(model=which)
                refit_info = {"model": which, "mapping": mapping, "seconds": time.time() - t0, "best": p.model_best}
                if p.problem_type == "quantile":
                    # F9: AutoGluon's conformal offsets c_τ sit on the model it calibrated; at fit time it calibrates
                    # the refit copy with its parent's out-of-fold scores, so the copies keep the parent's c_τ here too.
                    carried = []
                    for parent, full in mapping.items():
                        c = getattr(p._trainer.load_model(parent), "conformalize", None)
                        if c is not None and parent != full:
                            fm = p._trainer.load_model(full)
                            fm.conformalize = c
                            fm.save()
                            carried.append(full)
                    if carried:
                        refit_info["conformal_carried"] = carried
                step("refit_full", "done", seconds=refit_info["seconds"], best=p.model_best, refit=len(mapping))
        else:
            step("refit_full", "skipped", reason="not requested")

        # 2b. distillation: single student models fit on the ensemble's predictions
        distill_info = None
        if opts.get("distill"):
            step("distill", "running")
            t0 = time.time()
            hp = opts.get("distill_hyperparameters") or {"GBM": {}}
            teacher = p.model_best
            students = p.distill(time_limit=float(opts.get("distill_time_limit") or 120), hyperparameters=hp,
                                 teacher_preds="soft", augment_method="spunge", verbosity=0) or []
            singles = [m for m in students if "WeightedEnsemble" not in m]
            lbd = p.leaderboard(holdout, silent=True).set_index("model")
            from ..engine.worker import _natural

            nat = lambda v: None if v is None or pd.isna(v) else _natural(p.eval_metric, float(v))  # noqa: E731
            distill_info = {"teacher": teacher, "hyperparameters": hp, "students": students, "seconds": time.time() - t0,
                            "teacher_score_test": nat(lbd.loc[teacher, "score_test"]) if teacher in lbd.index else None,
                            "scores": {m: {"score_val": nat(lbd.loc[m, "score_val"]), "score_test": nat(lbd.loc[m, "score_test"])}
                                       for m in students if m in lbd.index},
                            "method": "predictor.distill(teacher_preds='soft', augment_method='spunge'): students fit on "
                                      "the teacher's predicted probabilities over training rows plus augmented rows"}
            if singles and not (opts.get("infer_limit") or opts.get("serve_model") or opts.get("serve_fastest")):
                pick = max(singles, key=lambda m: lbd.loc[m, "score_val"] if m in lbd.index else -np.inf)
                p.set_model_best(pick, save_trainer=True)
                distill_info["served"] = pick
            step("distill", "done" if singles else "warning", seconds=distill_info["seconds"], students=len(singles),
                 served=distill_info.get("served"))
        else:
            step("distill", "skipped", reason="not requested")

        def candidates() -> list[str]:
            cands = p.model_names(can_infer=True)
            if refit_info and not refit_info.get("skipped"):
                fulls = [m for m in cands if m.endswith("_FULL") or m.endswith("_DSTL")]
                cands = fulls or cands
            return cands

        def one_row_table(cands: list[str]) -> list[dict[str, Any]]:
            """Each candidate as it would be served: pipeline pruned to what it reads, persisted, one-row p95."""
            from ..engine.worker import _natural

            lb = p.leaderboard(silent=True).set_index("model")
            out = []
            for m in cands:
                try:
                    p.persist(models=[m], with_ancestors=True)
                except Exception:  # noqa: BLE001
                    pass
                with fastpath.pruned(p, m) as info:
                    t = fastpath.one_row(fastpath.predict_fn(p, m), X, n=40, budget_s=2.5)
                parent = p._trainer.get_model_attribute(m, "refit_full_parent", default=None)
                sv = lb.loc[m, "score_val"] if m in lb.index else None
                if (sv is None or pd.isna(sv)) and parent and parent in lb.index:
                    sv = lb.loc[parent, "score_val"]
                out.append({"model": m, "refit_parent": parent, "score_val": None if sv is None or pd.isna(sv) else float(sv),
                            "score_val_natural": None if sv is None or pd.isna(sv) else _natural(p.eval_metric, float(sv)),
                            "single_ms_p50": t["ms_p50"], "single_ms_p95": t["ms_p95"], "pipeline_pruned": info["pruned"],
                            "n_models": len(_deps(p, m))})
                try:
                    p.unpersist(models=[m])
                except Exception:  # noqa: BLE001
                    pass
            return out

        # 3. infer_limit (or the served model chosen by the registrant)
        limit_info = None
        if opts.get("infer_limit"):
            step("infer_limit", "running")
            t0 = time.time()
            limit = float(opts["infer_limit"])
            batch = int(opts.get("infer_limit_batch_size") or 1000)
            cands = candidates()
            if batch == 1:
                # One row per call: the time that matters is one call's wall time as served, at its 95th percentile.
                table = one_row_table(cands)
                for r in table:
                    r["time_used_us"] = r["single_ms_p95"] * 1e3 if r["single_ms_p95"] is not None else None
                    r["within"] = r["time_used_us"] is not None and r["time_used_us"] <= limit * 1e6
                rule_t = "measured one-row p95 as served (pipeline pruned, persisted)"
            else:
                ag_times = {m: p._trainer.get_model_attribute(m, "predict_1_time", default=None) for m in cands}
                table = measure_candidates(p, X, cands, min(batch, len(X)) or 1, ag_times)
                for r in table:
                    t = r["ag_predict_1_time_us"] if r["ag_predict_1_time_us"] is not None else r["us_per_row"]
                    r["time_used_us"] = t
                    r["within"] = t is not None and t <= limit * 1e6
                rule_t = f"measured time per row in batches of {batch}"
            ok = [r for r in table if r["within"] and r["score_val"] is not None]
            if ok:
                chosen = max(ok, key=lambda r: r["score_val"])
                met = True
            else:
                chosen = min(table, key=lambda r: r["time_used_us"] if r["time_used_us"] is not None else float("inf"))
                met = False
            p.set_model_best(chosen["model"], save_trainer=True)
            limit_info = {"infer_limit_s": limit, "infer_limit_us": limit * 1e6, "batch_size": batch,
                          "candidates": table, "chosen": chosen["model"], "met": met, "seconds": time.time() - t0,
                          "rule": f"best validation score among models with {rule_t} ≤ budget"
                                  + ("" if met else "; none met it, so the fastest model was kept")}
            step("infer_limit", "done" if met else "warning", chosen=chosen["model"], met=met, seconds=limit_info["seconds"])
        elif opts.get("serve_fastest"):
            step("infer_limit", "running")
            t0 = time.time()
            table = one_row_table(candidates())
            chosen = min(table, key=lambda r: r["single_ms_p95"] if r["single_ms_p95"] is not None else float("inf"))
            p.set_model_best(chosen["model"], save_trainer=True)
            limit_info = {"serve_fastest": True, "candidates": table, "chosen": chosen["model"], "met": True, "batch_size": 1,
                          "seconds": time.time() - t0, "rule": "the single model with the lowest measured one-row p95 as served"}
            step("infer_limit", "done", chosen=chosen["model"], reason="serve_fastest", seconds=limit_info["seconds"])
        elif opts.get("serve_model"):
            step("infer_limit", "running")
            want = opts["serve_model"]
            names = p.model_names(can_infer=True)
            if refit_info and not refit_info.get("skipped") and f"{want}_FULL" in names:
                want = f"{want}_FULL"
            if want not in names:
                raise ValueError(f"serve_model: {opts['serve_model']} is not a model of this run")
            p.set_model_best(want, save_trainer=True)
            limit_info = {"serve_model": want, "chosen": want, "met": True, "seconds": 0.0,
                          "rule": "chosen by the registrant (serve_model), from the measured one-row latency"}
            step("infer_limit", "done", chosen=want, reason="serve_model")
        else:
            step("infer_limit", "skipped", reason="no latency budget")

        # 4. the version's own predictor
        step("deploy_copy", "running")
        t0 = time.time()
        if dest.exists():
            shutil.rmtree(dest)
        prune_info = None
        if opts.get("clone_for_deployment"):
            p.clone_for_deployment(str(dest), model="best", dirs_exist_ok=True)
            how = "clone_for_deployment(model='best'): only the best model and its dependencies, save_space()"
            if opts.get("prune_pipeline", True):
                # Prune the deployed copy's feature pipeline to what its models read; keep it only if the holdout
                # predictions are unchanged.
                d = TabularPredictor.load(str(dest), verbosity=0, require_py_version_match=False)
                fn = fastpath.predict_fn(d)
                before = fn(X)
                prune_info = fastpath.prune_pipeline(d)
                after = fn(X)
                a = np.asarray(before, dtype=float) if d.problem_type != "multiclass" and d.problem_type != "binary" else before.to_numpy(dtype=float)
                b = np.asarray(after, dtype=float) if d.problem_type != "multiclass" and d.problem_type != "binary" else after.to_numpy(dtype=float)
                diff = float(np.nanmax(np.abs(a - b))) if len(a) else 0.0
                prune_info["max_abs_diff"] = diff
                prune_info["kept"] = bool(prune_info["pruned"] and diff <= 1e-9)
                if prune_info["kept"]:
                    d.save()
                    how += f"; feature pipeline pruned to {len(prune_info['features_in'])} input column(s) and {prune_info['n_features_out']} of {prune_info['n_features_out_before']} features"
                elif prune_info["pruned"]:
                    prune_info["reason"] = f"predictions changed (max |Δ| = {diff:.2e}); pipeline left whole"
                del d
        else:
            p.clone(str(dest), dirs_exist_ok=True)
            how = "clone(): every model kept"
        shutil.rmtree(work, ignore_errors=True)
        final = TabularPredictor.load(str(dest), verbosity=0, require_py_version_match=False)
        step("deploy_copy", "done", seconds=time.time() - t0, how=how, models=len(final.model_names()))

        # 5. measure source and version alike
        step("measure", "running")
        t0 = time.time()
        src_size = dir_size(Path(cfg["predictor_path"]))
        ver_size = dir_size(dest)
        lat_src = measure_latency(src, X)
        # The version as a predict worker serves it: loaded, then the first call cold (models read from disk), then
        # warmed (persisted, one call) and measured: one row (p50 / p95) and batches of 1 → 1,000.
        cold = TabularPredictor.load(str(dest), verbosity=0, require_py_version_match=False)
        tc = time.perf_counter()
        fastpath.predict_fn(cold)(X.iloc[[0]])
        cold_ms = (time.perf_counter() - tc) * 1e3
        del cold
        warm_info = fastpath.warm(final, X)
        lat_ver = measure_latency(final, X)
        served = fastpath.one_row(fastpath.predict_fn(final), X, n=200, budget_s=6.0)
        serving_info = {"cold_first_call_ms": cold_ms, "warm": warm_info, "one_row": served,
                   "batches": fastpath.batches(fastpath.predict_fn(final), X), "pipeline": prune_info,
                   "cpus": os.cpu_count(), "where": "the build's worker process, after load + persist + one warm-up call"}
        lat_ver["single_ms_p50"], lat_ver["single_ms_p95"], lat_ver["single_calls"] = served["ms_p50"], served["ms_p95"], served["calls"]
        hp = holdout_predictions(final, holdout, cfg["label"])
        hp.to_csv(vdir / "holdout_predictions.csv", index=False)
        res_run = run.get("result") or {}
        ci = metrics_with_ci(hp, final.problem_type, final.positive_class if final.problem_type == "binary" else None,
                             list(final.class_labels) if final.class_labels is not None else None)
        from ..engine.worker import holdout_metrics

        hold = holdout_metrics(final, holdout, cfg["label"])
        src_hp = Path(cfg["run_dir"]) / "holdout_predictions.csv"
        src_ci = metrics_with_ci(pd.read_csv(src_hp), final.problem_type,
                                 final.positive_class if final.problem_type == "binary" else None,
                                 list(final.class_labels) if final.class_labels is not None else None, B=200) if src_hp.exists() else None
        step("measure", "done", seconds=time.time() - t0)

        # 6. hashes
        step("hash", "running")
        model_hash = dir_hash(dest)
        data_hash = file_hash(Path(cfg["csv_path"]))
        splits_hash = file_hash(Path(cfg["splits_path"]))
        step("hash", "done", model_hash=model_hash[:12])

        mono_info = None
        if cfg.get("monotone"):  # F8: ICE / PD of the model this version serves, as deployed, on raw holdout rows
            from ..engine import monotone as mono

            chk = mono.ice_check(final, holdout, cfg["monotone"]["constraints"], final.model_best, curves=30)
            mono_info = {"constraints": cfg["monotone"]["constraints"], "compliant_only": cfg["monotone"]["compliant_only"],
                         "served_model": final.model_best, "columns": chk, "eps": mono.EPS,
                         "monotone": all(x.get("monotone", False) for x in chk.values())}

        quant_info = None
        if final.problem_type == "quantile":  # F9: the served model's range on the holdout, raw and calibrated
            from ..engine import quantile as qt

            conf = qt.conformal(final)
            if conf.get("error") and not conf.get("applied"):
                # A deployment clone keeps no validation rows: the offsets AutoGluon would compute come from the
                # source run's predictor, for the same model (or the bagged parent of a refit copy).
                m = final.model_best if final.model_best in src.model_names() else final.model_best.removesuffix("_FULL")
                if m in src.model_names():
                    c2 = qt.conformal(src, m)
                    if not c2.get("error"):
                        conf = {**c2, "model": final.model_best, "source": f"{c2['source']} (source run, model {m})"}
            quant_info = qt.report(final, holdout, cfg["label"], per_model=False, conf=conf)

        deps = _deps(final, final.model_best)
        threshold = float(final.decision_threshold) if final.problem_type == "binary" else None
        result = {
            "source": {"run_id": run["id"], "best_model": src.model_best, "size_bytes": src_size, "latency": lat_src,
                       "n_models": len(src.model_names()), "holdout_ci": src_ci,
                       "holdout": (res_run.get("holdout") or {}).get("metrics")},
            "version": {"best_model": final.model_best, "size_bytes": ver_size, "latency": lat_ver,
                        "n_models": len(final.model_names()), "models": final.model_names(), "dependencies": deps,
                        "holdout": hold.get("metrics"), "confusion_matrix": hold.get("confusion_matrix"),
                        "classes": jsonable(hold.get("classes")), "holdout_ci": ci, "problem_type": final.problem_type,
                        "eval_metric": final.eval_metric.name, "positive_class": jsonable(final.positive_class) if final.problem_type == "binary" else None,
                        "decision_threshold": threshold, "features": list(final.original_features)},
            "tradeoff": {"size_ratio": ver_size / src_size if src_size else None,
                         "latency_ratio_p50": lat_ver["us_per_row_p50"] / lat_src["us_per_row_p50"] if lat_src["us_per_row_p50"] else None,
                         "single_ratio_p50": lat_ver["single_ms_p50"] / lat_src["single_ms_p50"] if lat_src["single_ms_p50"] else None},
            "refit": refit_info, "infer_limit": limit_info, "distill": distill_info, "serving": serving_info,
            "monotone": mono_info, "quantile": quant_info,
            "hashes": {"model_sha256": model_hash, "data_sha256": data_hash, "splits_sha256": splits_hash,
                       "data_file": Path(cfg["csv_path"]).name},
            "path": str(dest),
        }
        reg.update_version(version_id, result=result, path=str(dest), model_hash=model_hash)

        # 7. mlflow
        step("mlflow", "running")
        try:
            from . import tracking

            mlr = res_run.get("mlflow")
            if not mlr:
                mlr = tracking.log_run(data_dir, runs.get(run["id"]), Path(cfg["run_dir"]))
                runs.merge_result(run["id"], mlflow=mlr)
            mv = tracking.register_version(data_dir, v["model_name"], mlr["run_id"], str(dest), {
                "ensemble.version_id": version_id, "ensemble.run_id": run["id"], "model_sha256": model_hash,
                "data_sha256": data_hash, "best_model": final.model_best, "refit_full": opts.get("refit_full"),
                "infer_limit": opts.get("infer_limit"), "clone_for_deployment": opts.get("clone_for_deployment"),
                "approval": "pending", "registered_by": v["created_by"]}, description=opts.get("description") or "")
            reg.merge_version_result(version_id, mlflow={**mv, "experiment_id": mlr["experiment_id"]})
            step("mlflow", "done", version=mv["version"])
        except Exception as e:  # noqa: BLE001 — the version is usable without the MLflow mirror
            step("mlflow", "failed", error=f"{type(e).__name__}: {e}")
        reg.update_version(version_id, status="ready")
        v = reg.get_version(version_id)
        reg.audit(actor=v["created_by"], action="registered", version=v, to_stage="None",
                  comment=opts.get("description") or "",
                  details={"options": {k: opts.get(k) for k in ("refit_full", "infer_limit", "infer_limit_batch_size", "clone_for_deployment", "serve_model", "serve_fastest", "distill", "prune_pipeline")},
                           "best_model": final.model_best, "size_bytes": ver_size,
                           "us_per_row_p50": lat_ver["us_per_row_p50"], "data_sha256": data_hash})
        return {"ok": True, "version_id": version_id}
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        reg.update_version(version_id, status="failed", error=f"{type(e).__name__}: {e}")
        prog = reg.get_version(version_id)["progress"] or {"steps": []}
        for s in prog["steps"]:
            if s.get("status") == "running":
                reg.step(version_id, s["name"], "failed", error=str(e)[:500])
        (vdir / "build_error.txt").write_text(tb)
        shutil.rmtree(work, ignore_errors=True)
        return {"ok": False, "error": str(e)}


def build_other(db_path: str, version_id: str, registry_dir: str) -> dict[str, Any]:
    """A forecast (TimeSeriesPredictor) or anomaly (IsolationForest pipeline) run as a version: copy the model folder,
    measure its own scoring latency on the run's data, hash, register in MLflow. No refit or infer_limit (they are
    TabularPredictor features), and the build says so."""
    db = Database(Path(db_path))
    reg, runs = RegistryStore(db), RunStore(db)
    v = reg.get_version(version_id)
    run = runs.get(v["run_id"])
    cfg, res = run["config"], run.get("result") or {}
    kind = cfg["kind"]
    vdir = Path(registry_dir) / v["model_name"] / f"v{v['version']}"
    dest = vdir / "predictor"
    data_dir = Path(db_path).parent
    step = lambda name, status, **kw: reg.step(version_id, name, status, **kw)  # noqa: E731
    try:
        step("clone", "running")
        t0 = time.time()
        vdir.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(cfg["predictor_path"], dest)
        step("clone", "done", seconds=time.time() - t0, how="copy of the run's model folder")
        step("refit_full", "skipped", reason=f"not used for {kind} models")
        step("infer_limit", "skipped", reason=f"not used for {kind} models")
        step("deploy_copy", "done", how="the copied folder is the deployable model")
        step("measure", "running")
        t0 = time.time()
        lat: dict[str, Any] = {}
        if kind == "forecast":
            from autogluon.timeseries import TimeSeriesPredictor

            from ..forecast import core as fc

            p = TimeSeriesPredictor.load(str(dest))
            obs, fut, static, _ = fc.read_long(cfg)
            data = fc.to_tsdf(obs, static)
            if p.freq and data.freq != p.freq:
                data = data.convert_frequency(p.freq)
            kc, _src = fc.future_covariates(p, data, fut, list(cfg["mapping"].get("known_covariates") or []),
                                            fc.seasonal_period(p.freq))
            p.predict(data, known_covariates=kc)  # warm-up
            ts = []
            for _ in range(3):
                t1 = time.perf_counter()
                p.predict(data, known_covariates=kc)
                ts.append(time.perf_counter() - t1)
            lat = {"forecast_all_items_s_p50": _pct(ts, 50), "items": int(data.num_items),
                   "ms_per_item_p50": _pct(ts, 50) * 1e3 / max(int(data.num_items), 1)}
            best = p.model_best
            holdout = (res.get("holdout") or {}).get("metrics")
            problem = "forecast"
            metric = (res.get("eval_metric") or {}).get("name")
        else:
            from ..anomaly import core as an

            df = pd.read_csv(cfg["csv_path"])
            an.score_frame(str(dest), df.head(50), explain_top=0)
            import joblib

            model = joblib.load(dest / "model.joblib")
            ts, single = [], []
            for _ in range(3):
                t1 = time.perf_counter()
                model.score(df)
                ts.append((time.perf_counter() - t1) / len(df) * 1e6)
            for i in range(20):
                t1 = time.perf_counter()
                model.score(df.iloc[[i % len(df)]])
                single.append((time.perf_counter() - t1) * 1e3)
            lat = {"us_per_row_p50": _pct(ts, 50), "us_per_row_p95": _pct(ts, 95), "single_ms_p50": _pct(single, 50),
                   "single_ms_p95": _pct(single, 95), "batch_rows": len(df)}
            best = "IsolationForest"
            a = res.get("anomaly") or {}
            holdout = {"threshold": a.get("threshold"), "contamination": a.get("contamination"),
                       **({f"precision@{r['k']}": r["precision"] for r in ((a.get("truth") or {}).get("precision_at_k") or [])})}
            problem = "anomaly"
            metric = "isolation score"
        step("measure", "done", seconds=time.time() - t0)
        step("hash", "running")
        model_hash = dir_hash(dest)
        data_hash = file_hash(Path(cfg["csv_path"]))
        step("hash", "done", model_hash=model_hash[:12])
        size = dir_size(dest)
        result = {
            "source": {"run_id": run["id"], "best_model": best, "size_bytes": dir_size(Path(cfg["predictor_path"])),
                       "latency": lat, "holdout": holdout},
            "version": {"best_model": best, "size_bytes": size, "latency": lat, "holdout": holdout,
                        "problem_type": problem, "eval_metric": metric, "features": res.get("features") or [],
                        "n_models": len(res.get("leaderboard") or [])},
            "kind": kind, "tradeoff": {"size_ratio": 1.0},
            "hashes": {"model_sha256": model_hash, "data_sha256": data_hash, "data_file": Path(cfg["csv_path"]).name},
            "path": str(dest),
        }
        reg.update_version(version_id, result=result, path=str(dest), model_hash=model_hash)
        step("mlflow", "running")
        try:
            from . import tracking

            mlr = res.get("mlflow")
            if not mlr:
                mlr = tracking.log_run(data_dir, runs.get(run["id"]), Path(cfg["run_dir"]))
                runs.merge_result(run["id"], mlflow=mlr)
            mv = tracking.register_version(data_dir, v["model_name"], mlr["run_id"], str(dest), {
                "ensemble.version_id": version_id, "ensemble.run_id": run["id"], "model_sha256": model_hash,
                "data_sha256": data_hash, "best_model": best, "kind": kind, "approval": "pending",
                "registered_by": v["created_by"]}, description=v["options"].get("description") or "",
                engine="autogluon.timeseries" if kind == "forecast" else "scikit-learn")
            reg.merge_version_result(version_id, mlflow={**mv, "experiment_id": mlr["experiment_id"]})
            step("mlflow", "done", version=mv["version"])
        except Exception as e:  # noqa: BLE001
            step("mlflow", "failed", error=f"{type(e).__name__}: {e}")
        reg.update_version(version_id, status="ready")
        v = reg.get_version(version_id)
        reg.audit(actor=v["created_by"], action="registered", version=v, to_stage="None",
                  comment=v["options"].get("description") or "", details={"kind": kind, "best_model": best, "size_bytes": size})
        return {"ok": True, "version_id": version_id}
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        reg.update_version(version_id, status="failed", error=f"{type(e).__name__}: {e}")
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "build_error.txt").write_text(tb)
        return {"ok": False, "error": str(e)}
