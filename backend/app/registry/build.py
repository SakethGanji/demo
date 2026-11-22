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
import json
import os
import shutil
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import config, frames
from ..db import Database
from ..engine.jsonsafe import jsonable
from ..engine.store import RunStore
from . import fastpath
from .fastpath import _pct
from .store import RegistryStore


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


def _feed(h, path: Path) -> None:
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)


def dir_hash(path: Path) -> str:
    h = hashlib.sha256()
    root = Path(path)
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(root)).encode())
        h.update(b"\0")
        _feed(h, f)
    return h.hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    _feed(h, path)
    return h.hexdigest()


def measure_latency(predictor, X: pd.DataFrame, model: str | None = None, *, batch_rows: int = 1000, repeats: int = 7,
                    singles: int = 40, budget_s: float = 3.0) -> dict[str, Any]:
    """Batch: predict on `batch_rows` holdout rows `repeats` times (after one warm-up) → µs per row.
    Single row: `singles` one-row calls → ms per call. Both as p50 / p95. Models are persisted in memory first."""
    try:
        predictor.persist(models=model or "best", with_ancestors=True)
    except Exception:  # noqa: BLE001 — persisting is an optimisation
        pass
    fn = fastpath.predict_fn(predictor, model)
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
    n = len(hp)
    # F-X5a: the resample indices were one (B, n) array (500 × 2M rows = 8 GB at 10M). They are drawn per resample now
    # (the same stream, so the same intervals); above ci_rows() each resample has m = ci_rows() rows and the interval
    # around the point estimate is scaled by √(m / n) (m-out-of-n bootstrap), with B ≤ 200.
    m = n if n <= config.ci_rows() else config.ci_rows()
    if m < n:
        B = min(B, 200)
    scale = float(np.sqrt(m / n)) if n else 1.0
    out = {}
    for name, (formula, fn) in fns.items():
        try:
            point = float(fn(y, yp, P))
        except Exception:  # noqa: BLE001
            continue
        rng = np.random.default_rng(seed)  # every metric sees the same resamples (as with the one array)
        boots = []
        for b in range(B):
            i = rng.integers(0, n, size=m)
            try:
                boots.append(float(fn(y[i], yp[i], P[i] if P is not None else None)))
            except Exception:  # noqa: BLE001 — a resample with one class
                pass
        if boots and m < n:
            boots = [point + (v - point) * scale for v in boots]
        lo, hi = (float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))) if boots else (None, None)
        out[name] = {"value": point, "ci_low": lo, "ci_high": hi, "formula": formula, "B": len(boots)}
    return {"metrics": out, "n": n, "B": B, "seed": seed,
            "method": "percentile bootstrap over holdout rows, 95%" if m == n else
                      f"m-out-of-n percentile bootstrap, 95%: resamples of m = {m:,} of the {n:,} holdout rows, deviations "
                      f"from the point estimate scaled by √(m/n) = {scale:.4f}", **({"m": m} if m < n else {})}


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
    if cfg.get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel"):
        return build_other(db_path, version_id, registry_dir)
    try:
        from autogluon.tabular import TabularPredictor

        import ensemble_models  # noqa: F401

        from ..engine import serving
        from ..splits import load_splits

        vdir.mkdir(parents=True, exist_ok=True)
        parity = None
        if cfg.get("join"):  # P15: serving parity first: a frame that no longer matches its inputs is refused early
            step("parity", "running")
            parity = serving_parity(db_path, cfg, vdir / "parity")
            reg.merge_version_result(version_id, parity=parity)
            if parity["status"] != "ok":
                step("parity", "failed", summary=parity["summary"][:500])
                raise ParityError(parity)
            step("parity", "done", summary=parity["summary"], seconds=parity["seconds"])
        # F-X5a (X5 K1): a run on a big Parquet table never has its table read here. Its holdout rows are located by
        # __row_id (two columns read), a bounded splitmix64 sample of them is held in memory (latency, distillation,
        # the monotone / quantile checks), and every holdout row is scored in batches below.
        ref = frames.resolve(cfg)
        rows = None
        if serving.parquet_ref(ref) and frames.lake_table(ref).n_rows > config.doctor_rows():
            step("holdout", "running")
            t0 = time.time()
            rows = serving.RunRows(cfg, ref)
            hold_ids = rows.holdout_ids()
            hold_cols = rows.columns(cfg["drop_columns"])
            keep = serving.keep_at_most(hold_ids, config.build_holdout_rows(), rows.seed, 1)
            holdout = rows.read(hold_ids[keep], hold_cols)
            holdout = holdout[holdout[cfg["label"]].notna()]
            step("holdout", "done", seconds=time.time() - t0, rows=int(len(hold_ids)), in_memory=int(len(holdout)),
                 how=f"{len(hold_ids):,} holdout rows located by __row_id in a {rows.n_rows:,}-row table; "
                     f"{len(holdout):,} held in memory (splitmix64 sample), every row scored in batches")
        else:
            holdout = serving.holdout_frame(ref, load_splits(Path(cfg["splits_path"])), cfg["label"], cfg["drop_columns"])
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
        cal = opts.get("calibration")
        if cal and final.problem_type == "binary":
            # D4 recalibration: the version serves σ(a · logit p + b); its threshold below is on that scale
            from ensemble_models.calibration import apply_platt, formula

            apply_platt(final, float(cal["a"]), float(cal["b"]), change_id=opts.get("change_id"))
            final.save()
            how += f"; probabilities recalibrated (Platt: p′ = {formula(cal['a'], cal['b'])})"
        if opts.get("decision_threshold") is not None and final.problem_type == "binary":
            # D4 post-deployment change: the approved threshold (a move; after a recalibration, on the calibrated scale)
            final.set_decision_threshold(float(opts["decision_threshold"]))
            final.save()
            how += f"; decision threshold set to {float(opts['decision_threshold']):.4f} (change request {opts.get('change_id') or ''})".rstrip()
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
        from ..engine.worker import holdout_prediction_frame, holdout_result

        hold = None
        if rows is None:
            hp = holdout_prediction_frame(final, holdout, cfg["label"])
            hp.to_csv(vdir / "holdout_predictions.csv", index=False)
        else:  # F-X5a: every holdout row, in batches read by __row_id (P11's batched scoring), written batch by batch
            from ..lake import frame as LF

            res_h, scored = LF.score_holdout(final, rows.batches(hold_ids, hold_cols), cfg["label"],
                                             vdir / "holdout_predictions.csv", holdout_prediction_frame)
            hold = holdout_result(final, res_h["scores"], res_h["y_true"], res_h["y_pred"], cfg["label"],
                                  scored={**scored, "of": int(len(hold_ids)), "in_memory": int(len(holdout))})
            del res_h
            hp = _read_hp(vdir / "holdout_predictions.csv", final.problem_type)
        if parity is not None and rows is not None:  # P15 at scale: the serving frame's holdout rows, in batches
            parity["predictions"] = prediction_parity_batched(final, hp, vdir / "parity" / "serving_frame.parquet",
                                                              hold_ids, hold_cols, rows, cfg["label"])
        elif parity is not None:  # P15: the version's predictions on the serving frame = on the training frame
            parity["predictions"] = prediction_parity(final, holdout, hp, vdir / "parity" / "serving_frame.parquet",
                                                      load_splits(Path(cfg["splits_path"])), cfg["label"], cfg["drop_columns"])
            if parity["predictions"]["status"] != "ok":
                parity["status"] = "mismatch"
                parity["summary"] = (f"predictions differ on the serving frame: max |Δ| {parity['predictions']['max_abs_diff']:.3g}, "
                                     f"ids equal {parity['predictions']['ids_equal']}, labels equal {parity['predictions']['labels_equal']}")
                reg.merge_version_result(version_id, parity=parity)
                raise ParityError(parity)
            parity["summary"] += f"; the version's predictions on {parity['predictions']['rows']:,} holdout rows equal"
            shutil.rmtree(vdir / "parity", ignore_errors=True)
        res_run = run.get("result") or {}
        ci = metrics_with_ci(hp, final.problem_type, final.positive_class if final.problem_type == "binary" else None,
                             list(final.class_labels) if final.class_labels is not None else None)
        from ..engine.worker import holdout_metrics

        if hold is None:
            hold = holdout_metrics(final, holdout, cfg["label"])
        src_hp = Path(cfg["run_dir"]) / "holdout_predictions.csv"
        src_ci = metrics_with_ci(frames.read_artifact(src_hp), final.problem_type,
                                 final.positive_class if final.problem_type == "binary" else None,
                                 list(final.class_labels) if final.class_labels is not None else None, B=200) if src_hp.exists() else None
        step("measure", "done", seconds=time.time() - t0)

        # 6. hashes
        step("hash", "running")
        model_hash = dir_hash(dest)
        data_hash = frames.input_sha256(cfg)  # the bytes the fit read (P05: a Parquet table's table_sha256)
        from ..lake.migrate import run_split_sha256

        splits_hash = run_split_sha256(cfg) or file_hash(Path(cfg["splits_path"]))  # P10: a migrated run's DL4 JSON hash
        step("hash", "done", model_hash=model_hash[:12])
        inputs = input_record(db_path, cfg, list(final.original_features), data_hash)  # P15: C §4.6, frozen with the version

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
                        "decision_threshold": threshold, "features": list(final.original_features),
                        "calibration": (getattr(final._learner, "ensemble_platt", None) if final.problem_type == "binary" else None)},
            "tradeoff": {"size_ratio": ver_size / src_size if src_size else None,
                         "latency_ratio_p50": lat_ver["us_per_row_p50"] / lat_src["us_per_row_p50"] if lat_src["us_per_row_p50"] else None,
                         "single_ratio_p50": lat_ver["single_ms_p50"] / lat_src["single_ms_p50"] if lat_src["single_ms_p50"] else None},
            "refit": refit_info, "infer_limit": limit_info, "distill": distill_info, "serving": serving_info,
            "monotone": mono_info, "quantile": quant_info,
            "hashes": {"model_sha256": model_hash, "data_sha256": data_hash, "splits_sha256": splits_hash,
                       "data_file": _data_file(cfg), **_input_hashes(inputs)},
            "inputs": inputs, "parity": parity,
            "path": str(dest),
        }
        reg.update_version(version_id, result=result, path=str(dest), model_hash=model_hash)

        # 7. mlflow
        step("mlflow", "running")
        try:
            from . import tracking

            mlr = tracking.ensure_logged(data_dir, runs, run["id"], Path(cfg["run_dir"]), res_run.get("mlflow"))
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
                  details={"options": {k: opts.get(k) for k in ("refit_full", "infer_limit", "infer_limit_batch_size", "clone_for_deployment", "serve_model", "serve_fastest", "distill", "prune_pipeline", "decision_threshold", "calibration", "change_id", "change_by")},
                           "best_model": final.model_best, "size_bytes": ver_size,
                           "us_per_row_p50": lat_ver["us_per_row_p50"], "data_sha256": data_hash})
        return {"ok": True, "version_id": version_id}
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        reg.update_version(version_id, status="failed",
                           error=f"serving parity mismatch: {e}" if isinstance(e, ParityError) else f"{type(e).__name__}: {e}")
        if isinstance(e, ParityError):
            reg.merge_version_result(version_id, parity=e.parity)
            reg.audit(actor=v["created_by"], action="register_refused", version=v, comment="serving parity mismatch",
                      details={"parity": {k: e.parity.get(k) for k in ("status", "summary", "cache", "inputs", "rows", "mismatched_cells")}})
        prog = reg.get_version(version_id)["progress"] or {"steps": []}
        for s in prog["steps"]:
            if s.get("status") == "running":
                reg.step(version_id, s["name"], "failed", error=str(e)[:500])
        (vdir / "build_error.txt").write_text(tb)
        shutil.rmtree(work, ignore_errors=True)
        return {"ok": False, "error": str(e)}


def _head(cfg: dict[str, Any]) -> pd.DataFrame:
    """F-X5a (X5 K1): the rows a build_other latency measurement scores (it uses ≤ 2,000 of them; anomaly scores all it
    gets): the whole table up to doctor_rows(), else its first build_holdout_rows() rows, read without the rest."""
    ref = frames.resolve(cfg)
    n = frames.n_rows(ref)
    if n <= config.doctor_rows():
        return frames.read(ref)
    return frames.read(ref, rows=range(0, config.build_holdout_rows()))


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
        if kind == "segmented":  # G5: the global model and the routed segment models only
            import json as _json

            sm = _json.loads((Path(cfg["predictor_path"]) / "meta.json").read_text())
            unrouted = {d for v, d in sm["models"].items() if v not in sm["routes"]}
            shutil.copytree(cfg["predictor_path"], dest, ignore=lambda src, names: [n for n in names if src == cfg["predictor_path"] and n in unrouted])
        else:
            shutil.copytree(cfg["predictor_path"], dest)
        step("clone", "done", seconds=time.time() - t0, how="copy of the run's model folder"
             + (" (the global model and the routed segment models)" if kind == "segmented" else ""))
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
        elif kind == "expected_loss":  # G3: the three predictors and meta.json, scored together
            from ..el import core as elc

            df = _head(cfg)
            elc.score_rows(str(dest), df.head(50).to_dict(orient="records"))  # warm-up
            ts, single = [], []
            for _ in range(3):
                t1 = time.perf_counter()
                elc.score_batch(str(dest), df.head(2000))
                ts.append((time.perf_counter() - t1) / min(len(df), 2000) * 1e6)
            for i in range(10):
                t1 = time.perf_counter()
                elc.score_rows(str(dest), [df.iloc[i].to_dict()])
                single.append((time.perf_counter() - t1) * 1e3)
            lat = {"us_per_row_p50": _pct(ts, 50), "single_ms_p50": _pct(single, 50), "single_ms_p95": _pct(single, 95),
                   "batch_rows": min(len(df), 2000)}
            el = res.get("expected_loss") or {}
            best = res.get("best_model") or "PD · LGD · EAD"
            port = el.get("portfolio") or {}
            holdout = {"el_ratio": port.get("ratio"), "el": port.get("el"), "realised": port.get("realised"),
                       "pd_auc": ((el.get("components") or {}).get("pd") or {}).get("auc")}
            problem = "expected_loss"
            metric = "ΣEL / Σ realised loss"
        elif kind == "survival":  # G6: the design, Cox PH and the forest (joblib) and meta.json; the chosen one serves
            from ..survival import core as svc

            df = _head(cfg)
            svc.score_rows(str(dest), df.head(50))  # warm-up
            ts, single = [], []
            for _ in range(3):
                t1 = time.perf_counter()
                svc.score_rows(str(dest), df.head(2000))
                ts.append((time.perf_counter() - t1) / min(len(df), 2000) * 1e6)
            for i in range(10):
                t1 = time.perf_counter()
                svc.score_rows(str(dest), [df.iloc[i].to_dict()])
                single.append((time.perf_counter() - t1) * 1e3)
            lat = {"us_per_row_p50": _pct(ts, 50), "single_ms_p50": _pct(single, 50), "single_ms_p95": _pct(single, 95),
                   "batch_rows": min(len(df), 2000)}
            sv = res.get("survival") or {}
            m = (sv.get("models") or {}).get(sv.get("served")) or {}
            best = res.get("best_model") or "survival"
            holdout = {"c_index": m.get("c_index"), **{f"auc_{a['t']:g}": a["auc"] for a in m.get("at") or []},
                       "calibration_max_diff": (sv.get("calibration") or {}).get("max_abs_diff")}
            problem = "survival"
            metric = "c_index"
        elif kind == "multilabel":  # G8: one predictor per label and meta.json (labels, thresholds); scored together
            from ..multilabel import core as mlc

            df = _head(cfg)
            mlc.score_rows(str(dest), df.head(50))  # warm-up
            ts, single = [], []
            for _ in range(3):
                t1 = time.perf_counter()
                mlc.score_rows(str(dest), df.head(2000))
                ts.append((time.perf_counter() - t1) / min(len(df), 2000) * 1e6)
            for i in range(10):
                t1 = time.perf_counter()
                mlc.score_rows(str(dest), [df.iloc[i].to_dict()])
                single.append((time.perf_counter() - t1) * 1e3)
            lat = {"us_per_row_p50": _pct(ts, 50), "single_ms_p50": _pct(single, 50), "single_ms_p95": _pct(single, 95),
                   "batch_rows": min(len(df), 2000)}
            ml = res.get("multilabel") or {}
            best = res.get("best_model") or "label models"
            holdout = {"macro_f1": ml.get("macro_f1"), "micro_f1": ml.get("micro_f1"), "hamming_loss": ml.get("hamming_loss"),
                       "labels": len(ml.get("labels") or [])}
            problem = "multilabel"
            metric = "macro_f1"
        elif kind == "segmented":  # G5: the global and segment predictors and meta.json, routed per row
            from ..segmented import core as sgc

            df = _head(cfg)
            sgc.score_rows(str(dest), df.head(50).to_dict(orient="records"))  # warm-up
            ts, single = [], []
            for _ in range(3):
                t1 = time.perf_counter()
                sgc.score_rows(str(dest), df.head(2000))
                ts.append((time.perf_counter() - t1) / min(len(df), 2000) * 1e6)
            for i in range(10):
                t1 = time.perf_counter()
                sgc.score_rows(str(dest), [df.iloc[i].to_dict()])
                single.append((time.perf_counter() - t1) * 1e3)
            lat = {"us_per_row_p50": _pct(ts, 50), "single_ms_p50": _pct(single, 50), "single_ms_p95": _pct(single, 95),
                   "batch_rows": min(len(df), 2000)}
            sg = res.get("segmented") or {}
            best = res.get("best_model") or "routed"
            ov = sg.get("overall") or {}
            head = "auc" if sg.get("problem_type") == "binary" else "rmse"
            dk = "delta_auc" if head == "auc" else "delta_mse"
            holdout = {head: (ov.get("routed") or {}).get(head), f"global_{head}": (ov.get("global") or {}).get(head),
                       dk: (ov.get(dk) or {}).get("delta"), "routed_segments": len(sg.get("routes") or [])}
            problem = sg.get("problem_type") or "binary"
            metric = head
        else:
            from ..anomaly import core as an

            df = _head(cfg)
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
        data_hash = frames.input_sha256(cfg)  # the bytes the fit read (P05: a Parquet table's table_sha256)
        step("hash", "done", model_hash=model_hash[:12])
        size = dir_size(dest)
        result = {
            "source": {"run_id": run["id"], "best_model": best, "size_bytes": dir_size(Path(cfg["predictor_path"])),
                       "latency": lat, "holdout": holdout},
            "version": {"best_model": best, "size_bytes": size, "latency": lat, "holdout": holdout,
                        "problem_type": problem, "eval_metric": metric, "features": res.get("features") or [],
                        "n_models": len(res.get("leaderboard") or [])},
            "kind": kind, "tradeoff": {"size_ratio": 1.0},
            "hashes": {"model_sha256": model_hash, "data_sha256": data_hash, "data_file": _data_file(cfg)},
            "path": str(dest),
        }
        if kind == "survival":  # G6: what Try It and the card read
            sv = res.get("survival") or {}
            result["version"].update(duration_column=sv.get("duration_column"), event_column=sv.get("event_column"),
                                     horizons=sv.get("horizons"), served=sv.get("served"), unit=sv.get("unit"))
        if kind == "multilabel":  # G8: what Try It and the card read
            ml = res.get("multilabel") or {}
            result["version"].update(labels=ml.get("labels"), thresholds={p["label"]: p["threshold"] for p in ml.get("per_label") or []},
                                     label_column=ml.get("label_column"), delimiter=ml.get("delimiter"), examples=ml.get("examples"))
        if kind == "segmented":  # G5: what the reason-code card section and Try It read for a binary version
            sg = res.get("segmented") or {}
            result["version"].update(positive_class=sg.get("positive_class"), decision_threshold=sg.get("threshold"),
                                     segment_column=sg.get("segment_column"), routes=sg.get("routes"))
        reg.update_version(version_id, result=result, path=str(dest), model_hash=model_hash)
        step("mlflow", "running")
        try:
            from . import tracking

            mlr = tracking.ensure_logged(data_dir, runs, run["id"], Path(cfg["run_dir"]), res.get("mlflow"))
            mv = tracking.register_version(data_dir, v["model_name"], mlr["run_id"], str(dest), {
                "ensemble.version_id": version_id, "ensemble.run_id": run["id"], "model_sha256": model_hash,
                "data_sha256": data_hash, "best_model": best, "kind": kind, "approval": "pending",
                "registered_by": v["created_by"]}, description=v["options"].get("description") or "",
                engine="autogluon.timeseries" if kind == "forecast" else "autogluon.tabular" if kind in ("expected_loss", "segmented", "survival", "multilabel") else "scikit-learn")
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


# --------------------------------------------------------------------------- P15: the input record and serving parity


def _one(db: Database, sql: str, args: tuple[Any, ...]) -> dict[str, Any] | None:
    with db.connect() as con:
        r = con.execute(sql, args).fetchone()
    return dict(r) if r else None


def _jsonish(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _table_origin(db: Database, version_id: str, table: str) -> dict[str, Any]:
    """Where a table's bytes came from: follow `carried` back to the version that wrote them (its upload or edit)."""
    seen, vid, t = set(), version_id, table
    while (vid, t) not in seen and len(seen) < 200:
        seen.add((vid, t))
        r = _one(db, "SELECT change, from_version, from_table, source_file FROM version_tables WHERE version_id = ? AND table_name = ?",
                 (vid, t))
        if not r:
            break
        if r["change"] == "carried" and r["from_version"]:
            vid, t = r["from_version"], r["from_table"] or t
            continue
        d = _one(db, "SELECT id, version_no, created_by, created_at, reason FROM datasets WHERE id = ?", (vid,)) or {}
        return {"origin_version": vid, "origin_v": d.get("version_no"), "change": r["change"], "source_file": r["source_file"],
                "uploaded_by": d.get("created_by"), "uploaded_at": d.get("created_at"), "upload_reason": d.get("reason")}
    return {}


def _version_block(db: Database, vid: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(dataset, version) of the §4.6 record for a dataset version."""
    d = _one(db, "SELECT d.id, d.root_id, d.version_no, d.created_by, d.created_at, d.reason, d.contract_result, d.project_id,"
                 " COALESCE(d.team_id, p.team_id) AS team_id FROM datasets d LEFT JOIN projects p ON p.id = d.project_id"
                 " WHERE d.id = ?", (vid,)) or {"id": vid}
    root_id = d.get("root_id") or vid
    root = _one(db, "SELECT id, name, team_id, project_id FROM dataset_roots WHERE id = ?", (root_id,)) or {}
    if not root.get("name"):
        root["name"] = (_one(db, "SELECT name FROM datasets WHERE id = ?", (root_id,)) or {}).get("name")
    cert = _one(db, "SELECT level, decided_by, decided_at, status FROM certifications WHERE version_id = ? AND status = 'active'"
                    " ORDER BY decided_at DESC", (vid,))
    cr = _jsonish(d.get("contract_result"))
    contract = None
    if isinstance(cr, dict):
        contract = {"id": cr.get("contract_id") or cr.get("id"), "version": cr.get("contract_version") or cr.get("version"),
                    "result": cr.get("result") or ("pass" if cr.get("ok") else "fail" if cr.get("ok") is False else None)}
    elif cr:
        contract = {"result": cr}
    dataset = {"root": root_id, "name": root.get("name"), "team": root.get("team_id") or d.get("team_id"),
               "project": root.get("project_id") or d.get("project_id")}
    version = {"dv": vid, "v": d.get("version_no"), "created_by": d.get("created_by"), "created_at": d.get("created_at"),
               "reason": d.get("reason"), "contract": contract,
               "certification": ({"level": cert["level"], "by": cert["decided_by"], "at": cert["decided_at"]} if cert else None)}
    return dataset, version


def _feature_sources(cfg: dict[str, Any], spec: dict[str, Any] | None, features: list[str]) -> dict[str, tuple[str, str]]:
    """frame column → (table, source column) for grain columns and n:1 columns (aggregates are derived: not listed)."""
    out: dict[str, tuple[str, str]] = {}
    if not spec:
        t = (cfg.get("input_table") or {}).get("table") or (cfg.get("input_ref") or {}).get("table") or "data"
        return {f: (t, f) for f in features}
    joined: dict[str, tuple[str, str]] = {}
    prefixes: list[tuple[str, str]] = []
    for j in spec.get("joins") or []:
        if j["type"] != "aggregate":
            tname = j["table"] if isinstance(j["table"], str) else j["table"]["table"]
            joined.update({j["prefix"] + c: (tname, c) for c in j.get("columns") or []})
            prefixes.append((j["prefix"], tname))
    for f in features:
        hit = joined.get(f) or next(((t, f[len(p):]) for p, t in prefixes if p and f.startswith(p)), None)
        out[f] = hit or (spec["grain"], f)
    return out


def input_record(db_path: str | Path, cfg: dict[str, Any], features: list[str], data_sha256: str | None) -> dict[str, Any]:
    """C §4.6: the model version's immutable record of the data it was developed on: the frame, the dataset and version
    (contract, certification), every input table with its sha, rows and the upload it came from, the join spec and its
    sha with coverage, the split, PII-tagged features and protected columns left out. Counts and hashes only, no rows."""
    db = Database(Path(db_path))
    jn = cfg.get("join") or None
    rec: dict[str, Any] = {"format": "ensemble.model_inputs/1"}
    split = cfg.get("split") or {}
    rec["split"] = {"id": split.get("split_id"), "key": split.get("key"), "sha256": split.get("split_sha256") or split.get("sha256"),
                    "target": split.get("target"), "strategy": split.get("strategy")}
    spec = None
    if jn:
        fr = _one(db, "SELECT id, version_id, n_rows, frame_sha256, cache_blob FROM join_frames WHERE id = ?", (jn["join_frame_id"],)) or {}
        jrow = _one(db, "SELECT id, name, spec_version, spec_json, spec_sha256 FROM joins WHERE id = ?", (jn["join_id"],)) or {}
        spec = _jsonish(jrow.get("spec_json"))
        pins = [{**x, "role": x.get("role") or ("grain" if i == 0 else f"join:{i - 1}")} for i, x in enumerate(jn.get("inputs") or [])]
        grain_vid = pins[0]["version_id"] if pins else fr.get("version_id")
        rec["frame"] = {"kind": "join_frame", "id": jn["join_frame_id"], "sha256": jn.get("cache_blob"),
                        "frame_sha256": jn.get("frame_sha256"), "rows": fr.get("n_rows")}
        tables = [{"name": x["table"], "role": x.get("role"), "dv": x["version_id"], "sha256": x["table_sha256"],
                   "rows": x.get("n_rows")} for x in pins]
        coverage = None
        try:
            from ..catalog import joins as J

            rep = J.validate(db, spec, grain_vid)
            rep.pop("_inputs", None)
            coverage = {c["table"]: c.get("coverage") for c in rep.get("coverage") or []}
        except Exception as e:  # noqa: BLE001 — the record stands without coverage; says why
            coverage = {"error": f"{type(e).__name__}: {e}"[:300]}
        rec["join"] = {"id": jn["join_id"], "name": jrow.get("name") or jn.get("name"), "spec_version": jrow.get("spec_version"),
                       "spec": spec, "spec_sha256": jrow.get("spec_sha256") or jn.get("spec_sha256"),
                       "spec_frozen": jrow.get("spec_sha256") == jn.get("spec_sha256"), "coverage": coverage}
    else:
        ref = frames.resolve(cfg)
        grain_vid = getattr(ref, "version_id", None) or (cfg.get("input_ref") or {}).get("version_id") or cfg.get("dataset_id")
        tname = None
        rows = None
        if isinstance(ref, frames.Table) and frames.is_parquet(ref):
            lt = frames.lake_table(ref)
            tname, rows = lt.name, lt.n_rows
        rec["frame"] = {"kind": "table", "id": grain_vid, "table": tname, "sha256": data_sha256, "rows": rows}
        tables = [{"name": tname or "data", "role": "grain", "dv": grain_vid, "sha256": data_sha256, "rows": rows}]
        rec["join"] = None
    rec["dataset"], rec["version"] = _version_block(db, grain_vid)
    for t in tables:
        if t["dv"]:
            o = _table_origin(db, t["dv"], t["name"])
            t.update({k: o.get(k) for k in ("source_file", "uploaded_by", "uploaded_at", "change")})
            if o.get("origin_version") and o["origin_version"] != t["dv"]:
                t["from_dv"] = o["origin_version"] + " (carried)"
            if t["dv"] != grain_vid:  # another dataset (a merged_from input): name it
                ds, _v = _version_block(db, t["dv"])
                t["dataset"] = {"root": ds["root"], "name": ds["name"], "team": ds["team"]}
    rec["tables"] = tables
    src = _feature_sources(cfg, spec, features)
    pii, protected = [], []
    roots = {t["name"]: (t.get("dataset") or {}).get("root") or rec["dataset"]["root"] for t in tables}
    with db.connect() as con:
        tags = [dict(r) for r in con.execute("SELECT root_id, table_name, column_name, tag FROM column_tags WHERE tag <> 'none'")]
    for tg in tags:
        hits = [f for f, (t, c) in src.items() if t == tg["table_name"] and c == tg["column_name"] and roots.get(t) == tg["root_id"]]
        if tg["tag"] in ("pii_direct", "pii_quasi"):
            pii += hits
        if tg["tag"] == "protected" and tg["table_name"] in roots and roots[tg["table_name"]] == tg["root_id"] and not hits:
            protected.append(f"{tg['table_name']}.{tg['column_name']}")
    rec["features"] = len(features)
    rec["pii_features"] = sorted(set(pii))
    rec["excluded_protected"] = sorted(set(protected))
    rec["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return rec


def _data_file(cfg: dict[str, Any]) -> str:
    """The hashed file's name: a join frame's id (its cache blob), else the table file's name."""
    if cfg.get("join"):
        return f"join_frame {cfg['join']['join_frame_id']} (cache {str(cfg['join'].get('cache_blob'))[:12]})"
    try:
        return frames.path_of(frames.resolve(cfg)).name
    except Exception:  # noqa: BLE001
        return str(cfg.get("dataset_id"))


def _input_hashes(rec: dict[str, Any] | None) -> dict[str, Any]:
    """C §6.5 #15: `inputs: [{table, dv, sha256}]` and `join: {id, spec_sha256}` beside the data hash."""
    if not rec:
        return {}
    out: dict[str, Any] = {"inputs": [{"table": t["name"], "dv": t["dv"], "sha256": t["sha256"]} for t in rec.get("tables") or []]}
    if rec.get("join"):
        out["join"] = {"id": rec["join"]["id"], "spec_sha256": rec["join"]["spec_sha256"]}
    return out


class ParityError(RuntimeError):
    def __init__(self, parity: dict[str, Any]) -> None:
        super().__init__(parity.get("summary") or "serving parity mismatch")
        self.parity = parity


def _frame_v1(path: Path) -> pd.DataFrame:
    """A frame file read the way `frames.read` reads a lake table (engine_v1 types, index = __row_id)."""
    import pyarrow.parquet as pq

    at = pq.read_table(str(path))
    names = [c for c in at.column_names if c != "__row_id"]
    df = pd.DataFrame({c: frames._v1_column(at.column(c), c) for c in names}, columns=names)
    df.index = pd.Index(at.column("__row_id").to_numpy(zero_copy_only=False))
    return df


def serving_parity(db_path: str | Path, cfg: dict[str, Any], work: Path) -> dict[str, Any]:
    """§7 risk 11: serving and training share one join path. The frame is rebuilt from the run's pinned inputs with
    `frames.materialise` (the path batch scoring uses) and compared by `__row_id`, cell by cell, with the training-time
    frame (the cache file the run read). The cache file and every pinned input are re-hashed first. Leaves the rebuilt
    frame at work/serving_frame.parquet for the prediction comparison. Single-table runs: not applicable."""
    jn = cfg.get("join")
    if not jn:
        return {"status": "not_applicable", "reason": "single-table model: training and serving read the table's columns directly"}
    from ..catalog import joins as J
    from ..lake import duck
    from ..lake import manifest as M

    t0 = time.time()
    db = Database(Path(db_path))
    out: dict[str, Any] = {"status": "mismatch", "join_frame_id": jn["join_frame_id"], "spec_sha256": jn.get("spec_sha256"),
                           "method": "frame rebuilt from the pinned inputs with frames.materialise (the serving path), "
                                     "compared with the training frame by __row_id, every cell (IS DISTINCT FROM)"}
    problems: list[str] = []
    jrow = _one(db, "SELECT spec_json, spec_sha256 FROM joins WHERE id = ?", (jn["join_id"],))
    if not jrow:
        out["summary"] = f"the join {jn['join_id']} is gone"
        return out
    spec = json.loads(jrow["spec_json"])
    if jrow["spec_sha256"] != jn.get("spec_sha256") or J.spec_sha256(spec) != jrow["spec_sha256"]:
        problems.append("the join spec is not the one the run pinned")
    # the training frame: the cache file the run read, re-hashed now
    try:
        lt = frames.lake_table(frames.JoinFrame(jn["join_frame_id"]))
        cache_path = Path(lt.parts[0]["path"])
        now, _n = M.file_sha256(cache_path) if cache_path.exists() else (None, 0)
    except Exception as e:  # noqa: BLE001
        cache_path, now = None, f"unreadable: {type(e).__name__}"
    out["cache"] = {"recorded": jn.get("cache_blob"), "now": now, "ok": now == jn.get("cache_blob")}
    if not out["cache"]["ok"]:
        problems.append(f"the training frame's cache file reads {str(now)[:12]}…, the run recorded {str(jn.get('cache_blob'))[:12]}…")
    pinned = [{k: x[k] for k in ("role", "version_id", "table", "table_sha256") if k in x} for x in jn.get("inputs") or []]
    ins = J.check_inputs(db, {"inputs": pinned})
    out["inputs"] = [{"table": x["table"], "version_id": x["version_id"], "pinned": x["pinned"], "now": x["now"], "ok": x["ok"]} for x in ins]
    problems += [f"input {x['table']} ({x['version_id']}) reads {str(x['now'])[:12]}…, pinned {x['pinned'][:12]}…" for x in ins if not x["ok"]]
    # the serving frame: the one join path over the pinned inputs
    work.mkdir(parents=True, exist_ok=True)
    rebuilt = work / "serving_frame.parquet"
    try:
        lts = [frames.lake_table(frames.Table(x["version_id"], x["table"])) for x in pinned]
        res = frames.materialise(spec, lts, rebuilt)
        out["serving"] = {"rows": res["n_rows"], "sha256": M.file_sha256(rebuilt)[0], "seconds": res["seconds"]}
    except Exception as e:  # noqa: BLE001
        problems.append(f"the frame could not be rebuilt: {type(e).__name__}: {e}"[:400])
        out.update(summary="; ".join(problems), seconds=round(time.time() - t0, 3))
        return out
    if cache_path is not None and cache_path.exists():
        a, b = str(cache_path).replace("'", "''"), str(rebuilt).replace("'", "''")
        try:
            with duck.job() as con:
                da = {r[0]: r[1] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{a}')").fetchall()}
                dbb = {r[0]: r[1] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{b}')").fetchall()}
                common = [c for c in da if c in dbb and c != "__row_id"]
                diffs = ", ".join(f"sum(CASE WHEN t.{frames._qi(c)} IS DISTINCT FROM s.{frames._qi(c)} THEN 1 ELSE 0 END)" for c in common)
                row = con.execute(
                    f"SELECT (SELECT count(*) FROM read_parquet('{a}')), (SELECT count(*) FROM read_parquet('{b}')),"
                    f" sum(CASE WHEN t.__row_id IS NULL OR s.__row_id IS NULL THEN 1 ELSE 0 END){', ' + diffs if diffs else ''}"
                    f" FROM read_parquet('{a}') t FULL OUTER JOIN read_parquet('{b}') s ON t.__row_id = s.__row_id").fetchone()
            n_t, n_s, unmatched = int(row[0]), int(row[1]), int(row[2] or 0)
            bad = {c: int(v or 0) for c, v in zip(common, row[3:]) if v}
            out["training"] = {"rows": n_t, "sha256": now}
            out["rows"] = {"training": n_t, "serving": n_s, "ids_unmatched": unmatched}
            out["columns_compared"] = len(common)
            out["column_set_equal"] = list(da) == list(dbb)
            out["types_equal"] = all(da[c] == dbb[c] for c in common)
            out["mismatched_cells"] = bad
            out["byte_equal"] = out["serving"]["sha256"] == now
            if n_t != n_s or unmatched:
                problems.append(f"rows differ: training {n_t:,}, serving {n_s:,}, {unmatched:,} __row_id without a partner")
            if not out["column_set_equal"]:
                problems.append("the columns differ: " + ", ".join(sorted(set(da) ^ set(dbb)) or ["order"]))
            if not out["types_equal"]:
                problems.append("types differ: " + ", ".join(f"{c} {da[c]} vs {dbb[c]}" for c in common if da[c] != dbb[c]))
            if bad:
                problems.append("cells differ: " + ", ".join(f"{c} ({n:,} rows)" for c, n in bad.items()))
        except Exception as e:  # noqa: BLE001 — an unreadable training frame is a mismatch, said plainly
            problems.append(f"the training frame could not be read for the comparison ({type(e).__name__})")
    elif cache_path is None or not cache_path.exists():
        problems.append("the training frame's cache file is missing")
    out["status"] = "ok" if not problems else "mismatch"
    out["summary"] = "serving frame = training frame (every cell, by __row_id)" if not problems else "; ".join(problems)
    out["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out["seconds"] = round(time.time() - t0, 3)
    return out


def prediction_parity(final: Any, holdout: pd.DataFrame, hp: pd.DataFrame, rebuilt: Path, splits: dict[str, Any],
                      label: str, drop: list[str]) -> dict[str, Any]:
    """The version scores the holdout rows of the rebuilt (serving) frame; its predictions must equal the ones it made
    on the training frame's holdout rows (`hp`), row by row by __row_id."""
    from ..engine.worker import holdout_prediction_frame

    df = _frame_v1(rebuilt)
    h = df.iloc[np.asarray(splits["holdout"], dtype=int)]
    h = h[h[label].notna()]
    h = h.drop(columns=[c for c in drop if c in h.columns and c != label])
    ids_equal = list(h.index) == list(holdout.index)
    hp2 = holdout_prediction_frame(final, h, label)
    pcols = [c for c in hp.columns if c.startswith("p_") or c in ("y_pred", "prediction")]
    diff = 0.0
    labels_equal = True
    for c in pcols:
        a, b = hp[c].to_numpy(), hp2[c].to_numpy() if c in hp2 else None
        if b is None or len(a) != len(b):
            labels_equal = False
            continue
        if a.dtype.kind in "fc" and b.dtype.kind in "fc":
            diff = max(diff, float(np.nanmax(np.abs(a - b))) if len(a) else 0.0)
        elif not (pd.Series(a).astype(str).to_numpy() == pd.Series(b).astype(str).to_numpy()).all():
            labels_equal = False
    ok = ids_equal and labels_equal and diff <= 1e-9
    return {"status": "ok" if ok else "mismatch", "rows": int(len(h)), "ids_equal": ids_equal, "columns": pcols,
            "max_abs_diff": diff, "labels_equal": labels_equal}


def _read_hp(path: Path, problem_type: str) -> pd.DataFrame:
    """holdout_predictions.csv as written batch by batch (labels as text for classification, as the readers take it)."""
    if problem_type in ("binary", "multiclass"):
        return frames.read_artifact(path, dtype={"y_true": str, "y_pred": str})
    return frames.read_artifact(path)


def prediction_parity_batched(final: Any, hp: pd.DataFrame, rebuilt: Path, ids: np.ndarray, columns: list[str],
                              rows: Any, label: str) -> dict[str, Any]:
    """`prediction_parity` without a whole frame in memory (F-X5a): the rebuilt serving frame's holdout rows are read by
    __row_id in batches, scored, and compared with the training frame's predictions (`hp`) row by row by __row_id."""
    from ..engine import serving
    from ..engine.worker import holdout_prediction_frame
    from ..lake import frame as LF

    pcols = [c for c in hp.columns if c.startswith("p_") or c in ("y_pred", "prediction")]
    ref = hp.set_index(hp["row"].astype("int64"))
    seen = 0
    diff = 0.0
    labels_equal = True
    for b in serving.iter_file_rows(str(rebuilt), ids, columns, rows.plan, config.HOLDOUT_BATCH_ROWS):
        b = LF.cast_column_types(rows.cfg, b)
        b = b[b[label].notna()]
        hp2 = holdout_prediction_frame(final, b, label)
        hp2.index = hp2["row"].astype("int64")
        common = hp2.index.intersection(ref.index)
        seen += len(common)
        if len(common) != len(hp2):
            labels_equal = False
        a_, b_ = ref.loc[common], hp2.loc[common]
        for c in pcols:
            if c not in b_:
                labels_equal = False
                continue
            x, y = a_[c].to_numpy(), b_[c].to_numpy()
            if x.dtype.kind in "fc" and y.dtype.kind in "fc":
                diff = max(diff, float(np.nanmax(np.abs(x - y))) if len(x) else 0.0)
            elif not (pd.Series(x).astype(str).to_numpy() == pd.Series(y).astype(str).to_numpy()).all():
                labels_equal = False
    ids_equal = seen == len(hp)
    ok = ids_equal and labels_equal and diff <= 1e-9
    return {"status": "ok" if ok else "mismatch", "rows": int(seen), "ids_equal": ids_equal, "columns": pcols,
            "max_abs_diff": diff, "labels_equal": labels_equal,
            "method": f"batches of {config.HOLDOUT_BATCH_ROWS:,} holdout rows of the rebuilt frame, read by __row_id"}


def register_gate(db_path: str, run_id: str, cfg: dict[str, Any], registry_dir: str | Path, who: str) -> dict[str, Any]:
    """POST /runs/{id}/register on a join run: the frame-level parity now, so a mismatch is refused with 409 before a
    version exists (audited `register_refused`). The build repeats it and adds the prediction comparison."""
    import secrets

    work = Path(registry_dir) / "_parity" / secrets.token_hex(4)
    try:
        par = serving_parity(db_path, cfg, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if par["status"] != "ok":
        from ..catalog.schema import audit as chained_audit

        with Database(Path(db_path)).connect() as con:
            chained_audit(con, who, "register_refused", jsonable({"why": "serving parity mismatch", "parity": par}), par["summary"][:500],
                          run_id, object_kind="run", object_id=run_id)
    return par
