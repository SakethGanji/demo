"""MLflow as a library, on local files: tracking (every run's parameters, metrics and artifacts) and the model
registry (a version per registered ENSEMBLE version, with its stage, aliases and approval tags).

Store: `<data>/mlflow/mlflow.db` (SQLite, a local file) with artifacts under `<data>/mlflow/artifacts`. MLflow 3.18
puts its plain-folder store (`./mlruns`) in maintenance mode and refuses it without an opt-out; the SQLite file is
the local store it recommends, and needs no server. Browse it with
`mlflow ui --backend-store-uri sqlite:///backend/data/mlflow/mlflow.db`.

Artifacts stay light: the run's config, resolved fit call, leaderboard, holdout metrics, importance and holdout
predictions. The predictor itself is not copied; its path is a tag (`ensemble.predictor_path`), and a registered
version's `source` is the path of that version's own predictor folder.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import tempfile
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
log = logging.getLogger("ensemble.mlflow")


def paths(data_dir: Path) -> tuple[str, str, Path]:
    root = Path(data_dir) / "mlflow"
    root.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{root / 'mlflow.db'}", (root / "artifacts").as_uri(), root


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    """One process at a time initialises / writes the store (SQLite migrations must not race)."""
    with open(root / ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


@contextmanager
def client(data_dir: Path):
    uri, art, root = paths(data_dir)
    with _locked(root), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        logging.getLogger("mlflow").setLevel(logging.WARNING)
        logging.getLogger("alembic").setLevel(logging.WARNING)
        from mlflow import MlflowClient

        yield MlflowClient(tracking_uri=uri, registry_uri=uri), art


def _experiment(c, art: str, name: str) -> str:
    exp = c.get_experiment_by_name(name)
    if exp is not None:
        return exp.experiment_id
    return c.create_experiment(name, artifact_location=f"{art}/{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}")


def _flat(prefix: str, obj: Any, out: dict[str, str]) -> None:
    if isinstance(obj, dict) and obj:
        for k, v in obj.items():
            _flat(f"{prefix}.{k}" if prefix else str(k), v, out)
    else:
        s = json.dumps(obj, default=str) if not isinstance(obj, str) else obj
        out[prefix] = s[:6000]


def _metric_key(k: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-. /]", "_", k)[:250]


def log_run(data_dir: Path, rec: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Log one finished ENSEMBLE run to MLflow (idempotent: a run already logged is returned as is)."""
    res = rec.get("result") or {}
    with client(data_dir) as (c, art):
        exp_id = _experiment(c, art, f"ensemble/{rec['dataset_id']}")
        found = c.search_runs([exp_id], filter_string=f"tags.`ensemble.run_id` = '{rec['id']}'", max_results=1)
        if found:
            r = found[0]
            return {"experiment_id": exp_id, "run_id": r.info.run_id, "artifact_uri": r.info.artifact_uri, "logged": False}
        resolved = res.get("resolved") or {}
        fk = {k: v for k, v in (resolved.get("fit_kwargs") or {}).items() if k != "callbacks"}
        params: dict[str, str] = {}
        _flat("predictor", resolved.get("predictor_kwargs") or {}, params)
        _flat("fit", fk, params)
        params["dataset_id"] = rec["dataset_id"]
        params["label"] = rec["label"]
        tags = {"ensemble.run_id": rec["id"], "ensemble.dataset_id": rec["dataset_id"], "ensemble.label": rec["label"],
                "ensemble.predictor_path": rec["config"].get("predictor_path", ""), "ensemble.best_model": str(res.get("best_model")),
                "ensemble.problem_type": str(res.get("problem_type")), "mlflow.runName": rec["name"][:250],
                "engine": {"forecast": "autogluon.timeseries", "anomaly": "scikit-learn", "survival": "scikit-survival", "multilabel": "autogluon.tabular"}.get(rec["config"].get("kind"), "autogluon.tabular")}
        run = c.create_run(exp_id, run_name=rec["name"][:250], tags=tags)
        rid = run.info.run_id
        from mlflow.entities import Metric, Param

        import time

        ts = int(time.time() * 1000)
        batch_p = [Param(k[:250], v) for k, v in list(params.items())[:400]]
        metrics = []
        hold = (res.get("holdout") or {}).get("metrics") or {}
        for k, v in hold.items():
            if isinstance(v, (int, float)) and v == v:
                metrics.append(Metric(_metric_key(f"holdout_{k}"), float(v), ts, 0))
        lb = res.get("leaderboard") or []
        best = next((r for r in lb if r.get("model") == res.get("best_model")), None)
        if best and best.get("score_val_natural") is not None:
            metrics.append(Metric("val_score", float(best["score_val_natural"]), ts, 0))
        for k in ("fit_seconds", "elapsed_seconds"):
            if isinstance(res.get(k), (int, float)):
                metrics.append(Metric(k, float(res[k]), ts, 0))
        metrics.append(Metric("n_models", float(len(lb)), ts, 0))
        thr = res.get("threshold") or {}
        if thr.get("value") is not None:
            metrics.append(Metric("decision_threshold", float(thr["value"]), ts, 0))
        for i in range(0, len(batch_p), 100):
            c.log_batch(rid, params=batch_p[i:i + 100])
        c.log_batch(rid, metrics=metrics)
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "resolved_fit.json").write_text(json.dumps(resolved, indent=1, default=str))
            (t / "leaderboard.json").write_text(json.dumps(lb, indent=1, default=str))
            (t / "holdout.json").write_text(json.dumps(res.get("holdout"), indent=1, default=str))
            (t / "ensemble.json").write_text(json.dumps(res.get("ensemble"), indent=1, default=str))
            if res.get("feature_importance"):
                (t / "feature_importance.json").write_text(json.dumps(res["feature_importance"], indent=1, default=str))
            (t / "predictor_path.txt").write_text(rec["config"].get("predictor_path", "") + "\n")
            c.log_artifacts(rid, str(t), "ensemble")
        for name in ("config.json", "holdout_predictions.csv"):
            f = Path(run_dir) / name
            if f.exists():
                c.log_artifact(rid, str(f), "ensemble")
        c.set_terminated(rid, "FINISHED")
        run = c.get_run(rid)
        return {"experiment_id": exp_id, "run_id": rid, "artifact_uri": run.info.artifact_uri, "logged": True,
                "n_params": len(batch_p), "n_metrics": len(metrics)}


def ensure_logged(data_dir: Path, runs: Any, run_id: str, run_dir: Path, cached: dict[str, Any] | None = None) -> dict[str, Any]:
    """The run's MLflow record: `cached` if the run already has one, else log it now and store it on the run."""
    if cached:
        return cached
    mlr = log_run(data_dir, runs.get(run_id), run_dir)
    runs.merge_result(run_id, mlflow=mlr)
    return mlr


def _mv_out(mv: Any, model_name: str) -> dict[str, Any]:
    return {"name": model_name, "version": int(mv.version), "stage": mv.current_stage, "aliases": list(mv.aliases),
            "tags": dict(mv.tags)}


def register_version(data_dir: Path, model_name: str, mlflow_run_id: str, source: str, tags: dict[str, str],
                     description: str = "", engine: str = "autogluon.tabular") -> dict[str, Any]:
    with client(data_dir) as (c, _):
        from mlflow.exceptions import MlflowException

        try:
            c.get_registered_model(model_name)
        except MlflowException:
            c.create_registered_model(model_name, tags={"engine": engine}, description=description)
        mv = c.create_model_version(model_name, source=source, run_id=mlflow_run_id,
                                    tags={k: str(v)[:5000] for k, v in tags.items()}, description=description)
        return {"name": model_name, "version": int(mv.version), "source": mv.source, "run_id": mlflow_run_id,
                "stage": mv.current_stage}


def sync_version(data_dir: Path, model_name: str, version: int, *, stage: str, tags: dict[str, str]) -> dict[str, Any]:
    """Mirror a stage change / review into MLflow: the (deprecated but still served) stage, an alias per live
    stage ('staging', 'production'), and tags for the approval."""
    with client(data_dir) as (c, _):
        c.transition_model_version_stage(model_name, str(version), stage, archive_existing_versions=False)
        for k, v in tags.items():
            c.set_model_version_tag(model_name, str(version), k, str(v)[:5000])
        for alias in ("staging", "production"):
            try:
                cur = c.get_model_version_by_alias(model_name, alias)
            except Exception:  # noqa: BLE001 — no such alias yet
                cur = None
            want = stage.lower() == alias
            if want:
                c.set_registered_model_alias(model_name, alias, str(version))
            elif cur is not None and int(cur.version) == int(version):
                c.delete_registered_model_alias(model_name, alias)
        mv = c.get_model_version(model_name, str(version))
        return _mv_out(mv, model_name)


def read_version(data_dir: Path, model_name: str, version: int) -> dict[str, Any]:
    with client(data_dir) as (c, _):
        mv = c.get_model_version(model_name, str(version))
        run = c.get_run(mv.run_id) if mv.run_id else None
        return {**_mv_out(mv, model_name), "source": mv.source, "run_id": mv.run_id,
                "run": None if run is None else {"params": dict(run.data.params), "metrics": dict(run.data.metrics),
                                                  "tags": {k: v for k, v in run.data.tags.items() if not k.startswith("mlflow.")},
                                                  "artifact_uri": run.info.artifact_uri}}
