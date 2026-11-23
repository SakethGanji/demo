"""Fast tier for the engine: the API contract, SSE order and replay, a tiny fit, predict, cancel, missing labels.

Fits here use a 300-row upload and a tiny fixed config, so each takes a few seconds (mostly process start)."""

from __future__ import annotations

import io
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from .conftest import TERMINAL, read_sse, start_run, wait_for_event, wait_for_run

TINY_HP = {"GBM": {"num_boost_round": 10}, "LR": {}}


def make_tabular_csv(n: int = 300, missing_labels: int = 0, seed: int = 7) -> tuple[bytes, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    grp = rng.choice(["a", "b", "c"], size=n)
    logit = 1.5 * x1 - x2 + (grp == "a") * 0.8
    y = np.where(rng.random(n) < 1 / (1 + np.exp(-logit)), "yes", "no").astype(object)
    if missing_labels:
        y[rng.choice(n, size=missing_labels, replace=False)] = None
    df = pd.DataFrame({"row_id": [f"r{i}" for i in range(n)], "x1": x1.round(4), "x2": x2.round(4), "grp": grp,
                       "outcome": y})
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode(), df


@pytest.fixture(scope="module")
def tiny_dataset(client):
    content, df = make_tabular_csv()
    r = client.post("/api/datasets", files={"file": ("tiny_binary.csv", content, "text/csv")})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="module")
def tiny_run(client, tiny_dataset):
    run = start_run(client, dataset_id=tiny_dataset["id"], time_limit=60, feature_importance=False,
                    fit_kwargs={"hyperparameters": TINY_HP})
    assert run["status"] in ("queued", "running")
    events = read_sse(client, f"/api/runs/{run['id']}/events")
    return wait_for_run(client, run["id"]), events


# -- catalogue and contract ----------------------------------------------------

def test_health_reports_engine(client):
    h = client.get("/api/health").json()
    assert h["engine"]["name"] == "AutoGluon" and h["engine"]["version"].startswith("1.")
    assert isinstance(h["runs"], int)


def test_model_catalogue(client):
    r = client.get("/api/models")
    assert r.status_code == 200
    fams = {f["key"]: f for f in r.json()["families"]}
    for key in ("GBM", "CAT", "XGB", "RF", "XT", "KNN", "LR", "NN_TORCH", "FASTAI", "TFIDF_LR", "TFIDF_SVM", "ENS_WEIGHTED"):
        assert key in fams, key
        f = fams[key]
        assert {"key", "name", "kind", "description", "objective", "objective_tex", "symbols", "problem_types", "custom"} <= set(f)
        assert f["description"] and f["objective"] and f["objective_tex"]
        assert isinstance(f["symbols"], dict) and f["symbols"]
    assert fams["TFIDF_LR"]["custom"] and fams["TFIDF_SVM"]["custom"] and not fams["GBM"]["custom"]
    assert "log" in fams["LR"]["objective"] and "C" in fams["LR"]["symbols"]
    assert fams["ENS_WEIGHTED"]["objective"].startswith("ŷ(x) = Σₘ wₘ·fₘ(x)")
    assert set(fams["TFIDF_LR"]["problem_types"]) == {"binary", "multiclass"}


@pytest.mark.parametrize("body, code, text", [
    ({"dataset_id": "nope"}, 404, "No dataset"),  # "{churn}": the seeded churn dataset's id
    ({"dataset_id": "{churn}", "label": "nope"}, 400, "not in the dataset"),
    ({"dataset_id": "{churn}", "problem_type": "clustering"}, 422, ""),
    ({"dataset_id": "{churn}", "time_limit": -5}, 422, ""),
    ({"dataset_id": "{churn}", "predictor_kwargs": {"path": "/tmp/x"}}, 400, "path"),
    ({"dataset_id": "{churn}", "predictor_kwargs": {"label": "plan"}}, 400, "label"),
    ({"dataset_id": "{churn}", "fit_kwargs": {"train_data": "x.csv"}}, 400, "train_data"),
    ({"dataset_id": "{churn}", "drop_columns": ["churned"]}, 400, "label column"),
    ({"dataset_id": "{churn}", "drop_columns": ["nope"]}, 400, "no column"),
])
def test_create_run_rejects_bad_requests(client, churn_ds, body, code, text):
    body = {**body, "dataset_id": body["dataset_id"].replace("{churn}", churn_ds["id"])}
    before = client.get("/api/runs").json()["runs"]
    r = client.post("/api/runs", json=body)
    assert r.status_code == code, r.text
    assert text in r.text
    assert client.get("/api/runs").json()["runs"] == before  # nothing was created


def test_unknown_run_is_404(client):
    for path in ("/api/runs/run_nope", "/api/runs/run_nope/events", "/api/runs/run_nope/oof"):
        assert client.get(path).status_code == 404
    assert client.post("/api/runs/run_nope/predict", json={"rows": [{}]}).status_code == 404


# -- a tiny fit -------------------------------------------------------------------

RUN_FIELDS = {
    "id", "name", "dataset_id", "label", "status", "created_at", "started_at", "finished_at", "error", "config",
    "predictor_path", "n_events", "problem_type", "eval_metric", "classes", "positive_class", "best_model",
    "fit_seconds", "elapsed_seconds", "data", "features", "ensemble", "ensembles", "holdout", "oof", "text_models",
    "feature_importance", "leaderboard", "resolved", "custom_ensembles", "threshold", "mlflow",
    # added by later sessions: the run's kind and split (F4/F6), forecast / anomaly / import (S5–S7), F8 monotone,
    # F9 quantile, F11 scorecard, F12–F17 summaries
    "kind", "split", "forecast", "backtests", "anomaly", "imported", "monotone", "quantile", "quantile_levels",
    "scorecard", "learning_curve", "active_learning", "annotations", "shift", "slices", "doctor",
}


def test_tiny_fit_result(client, tiny_dataset, tiny_run, data_dir):
    run, _ = tiny_run
    assert run["status"] == "succeeded", run["error"]
    assert set(run) == RUN_FIELDS
    assert run["custom_ensembles"] is None  # none until POST /runs/{id}/ensemble (S3)
    # S4: binary runs record their decision threshold (AutoGluon's 0.5 unless calibration was asked for)
    assert run["threshold"]["value"] == 0.5 and run["threshold"]["source"] == "default"
    # S5: every finished run is logged to MLflow (a local SQLite store in the data folder)
    assert run["mlflow"]["run_id"] and run["mlflow"]["artifact_uri"].startswith("file://")
    assert run["label"] == "outcome" and run["problem_type"] == "binary"
    assert run["eval_metric"]["name"] == "accuracy" and run["eval_metric"]["greater_is_better"] is True
    assert sorted(run["classes"]) == ["no", "yes"] and run["positive_class"] in ("no", "yes")
    # The fixed holdout (20%) is never trained on; ID columns are dropped by default.
    split = client.get(f"/api/datasets/{tiny_dataset['id']}").json()["profile"]["splits"]
    assert run["data"]["holdout_rows"] == split["holdout_rows"] == 60
    assert run["data"]["train_rows"] == 240 and run["data"]["dropped_columns"] == ["row_id"]
    assert run["data"]["features"] == ["x1", "x2", "grp"]
    # Leaderboard: the two models and the weighted ensemble, with extra info and holdout scores.
    lb = run["leaderboard"]
    assert {r["model"] for r in lb} == {"Scorecard", "LightGBM", "LinearModel", "WeightedEnsemble_L2"}  # F11: + the scorecard
    assert {r["family"] for r in lb} == {"SCORECARD", "GBM", "LR", "ENS_WEIGHTED"}
    assert [r["rank"] for r in lb] == [1, 2, 3, 4]
    for r in lb:
        for k in ("score_val", "score_test", "fit_time", "pred_time_val", "pred_time_test", "stack_level",
                  "num_features", "hyperparameters", "model_type"):
            assert k in r, k
        assert 0 <= r["score_test"] <= 1 and r["score_test_natural"] == pytest.approx(r["score_test"])
    # Ensemble weights sum to 1 and name leaderboard models.
    ens = run["ensemble"]
    assert ens["model"] == "WeightedEnsemble_L2" and ens["best"] is (run["best_model"] == "WeightedEnsemble_L2")
    assert math.isclose(sum(ens["weights"].values()), 1, abs_tol=1e-9)
    assert set(ens["weights"]) <= {"LightGBM", "LinearModel", "Scorecard"}
    # Holdout metrics, with the confusion matrix in class order.
    h = run["holdout"]
    assert h["n_rows"] == 60 and h["eval_metric"] == "accuracy" and h["model"] == run["best_model"]
    assert 0.6 <= h["metrics"]["accuracy"] <= 1 and "balanced_accuracy" in h["metrics"]
    assert sum(map(sum, h["confusion_matrix"])) == 60 and h["classes"] == run["classes"]
    assert run["oof"]["available"] is False and run["feature_importance"] is None
    # The exact call, so the run can be reproduced in plain AutoGluon.
    fk = run["resolved"]["fit_kwargs"]  # F11: the default SCORECARD challenger joins the families asked for
    assert {**fk, "hyperparameters": {k: x for k, x in fk["hyperparameters"].items() if k != "SCORECARD"}} == \
        {"presets": "medium_quality", "time_limit": 60, "hyperparameters": TINY_HP} and "SCORECARD" in fk["hyperparameters"]
    assert run["resolved"]["predictor_kwargs"] == {"label": "outcome", "problem_type": None, "eval_metric": None}
    # A plain AutoGluon predictor directory on disk, plus the holdout predictions.
    p = Path(run["predictor_path"])
    assert p.is_dir() and (p / "predictor.pkl").exists() and p.is_relative_to(data_dir)
    hp = pd.read_csv(p.parent / "holdout_predictions.csv")
    assert len(hp) == 60 and {"row", "y_true", "y_pred", "p_no", "p_yes"} <= set(hp.columns)


def test_sse_order(tiny_run):
    run, events = tiny_run
    seqs = [int(e["id"]) for e in events]
    assert seqs == list(range(len(events))) == [e["data"]["seq"] for e in events]
    types = [e["event"] for e in events]
    assert all(e["event"] == e["data"]["type"] for e in events)
    assert types[0] == "run_queued" and types[1] == "run_started"
    assert types[-1] == "run_succeeded" and sum(t in TERMINAL for t in types) == 1
    order = [t for t in types if t != "log"]
    assert order.index("data_ready") < order.index("config_resolved") < order.index("fit_started") < order.index("model_started")
    assert order.index("fit_finished") < order.index("leaderboard") < order.index("evaluation") < order.index("run_succeeded")
    started = [e["data"]["model"] for e in events if e["event"] == "model_started"]
    finished = [e["data"] for e in events if e["event"] == "model_finished"]
    # F11: a binary label trains the SCORECARD challenger beside the requested families, by default
    assert started == [f["model"] for f in finished] == ["Scorecard", "LightGBM", "LinearModel", "WeightedEnsemble_L2"]
    for f in finished:  # each finished event carries the numbers the live race needs
        assert f["val_score"] is not None and f["fit_time"] > 0 and f["predict_time"] >= 0 and f["stack_level"] in (1, 2)
        assert f["family"] in ("SCORECARD", "GBM", "LR", "ENS_WEIGHTED")
        assert types.index("model_started") < types.index("model_finished")
    ens = [e["data"] for e in events if e["event"] == "ensemble"]
    assert len(ens) == 1 and math.isclose(sum(ens[0]["weights"].values()), 1, abs_tol=1e-9)
    assert any(e["event"] == "log" for e in events)  # AutoGluon's own log lines come through
    assert run["n_events"] == len(events)


def test_sse_replay(client, tiny_run):
    run, events = tiny_run
    url = f"/api/runs/{run['id']}/events"
    assert read_sse(client, url) == events  # replay of a finished run from 0 is identical
    k = len(events) // 2
    assert read_sse(client, f"{url}?from={k}") == events[k:]
    assert read_sse(client, url, headers={"Last-Event-ID": str(k)}) == events[k + 1:]
    assert read_sse(client, f"{url}?from={len(events) + 5}") == []  # past the end: closes, no hang
    as_json = client.get(url, params={"format": "json", "from": 3}).json()
    assert [e["seq"] for e in as_json["events"]] == list(range(3, len(events)))
    assert as_json["events"] == [e["data"] for e in events[3:]] and as_json["next"] == len(events)


def test_runs_list_and_get(client, tiny_run):
    run, _ = tiny_run
    listed = client.get("/api/runs").json()["runs"]
    mine = next(r for r in listed if r["id"] == run["id"])
    assert set(mine) == RUN_FIELDS
    assert mine["leaderboard"][0]["model"] == run["leaderboard"][0]["model"]
    assert "hyperparameters" not in mine["leaderboard"][0]  # the list is trimmed; GET /runs/{id} has everything
    assert mine["resolved"] is None
    assert client.get("/api/runs", params={"dataset_id": run["dataset_id"]}).json()["runs"][0]["id"] == run["id"]
    assert client.get("/api/runs", params={"dataset_id": "nope"}).json()["runs"] == []
    assert client.get(f"/api/runs/{run['id']}").json() == run


def test_predict(client, tiny_run):
    run, _ = tiny_run
    rows = [{"x1": 2.0, "x2": -1.0, "grp": "a"}, {"x1": -2.0, "x2": 1.5, "grp": "c"}]
    r = client.post(f"/api/runs/{run['id']}/predict", json={"rows": rows})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["model"] == run["best_model"] and out["n_rows"] == 2 and out["missing_columns"] == []
    assert out["predictions"] == ["yes", "no"]
    assert sorted(out["classes"]) == ["no", "yes"]
    for p, pred in zip(out["probabilities"], out["predictions"]):
        assert math.isclose(sum(p.values()), 1, abs_tol=1e-5) and max(p, key=p.get) == pred
    # One named model; missing columns are allowed (treated as missing values) and reported.
    r = client.post(f"/api/runs/{run['id']}/predict", json={"rows": [{"x1": 1.0}], "model": "LinearModel"})
    assert r.status_code == 200 and r.json()["model"] == "LinearModel" and r.json()["missing_columns"] == ["x2", "grp"]
    assert client.post(f"/api/runs/{run['id']}/predict", json={"rows": []}).status_code == 400
    assert client.post(f"/api/runs/{run['id']}/predict", json={"rows": rows, "model": "Nope"}).status_code == 400


def test_oof_needs_bagging(client, tiny_run):
    run, _ = tiny_run
    r = client.get(f"/api/runs/{run['id']}/oof")
    assert r.status_code == 400 and "bagging" in r.json()["detail"]


def test_feature_importance_is_computed_lazily(client, tiny_run):
    run, _ = tiny_run
    r = client.get(f"/api/runs/{run['id']}/feature-importance")
    assert r.status_code == 200, r.text
    fi = r.json()["features"]
    assert {f["feature"] for f in fi} == {"x1", "x2", "grp"}
    assert {"importance", "stddev", "p_value", "p99_low", "p99_high"} <= set(fi[0])
    assert fi[0]["feature"] in ("x1", "x2")  # the signal is in x1 and x2
    assert client.get(f"/api/runs/{run['id']}").json()["feature_importance"] == fi  # stored


def test_missing_labels_are_dropped(client):
    content, df = make_tabular_csv(n=300, missing_labels=12, seed=11)
    ds = client.post("/api/datasets", files={"file": ("tiny_missing.csv", content, "text/csv")}).json()
    assert "12 rows have no label and will be dropped." in ds["profile"]["warnings"]
    run = start_run(client, dataset_id=ds["id"], time_limit=60, feature_importance=False, fit_kwargs={"hyperparameters": {"LR": {}}})
    run = wait_for_run(client, run["id"])
    assert run["status"] == "succeeded", run["error"]
    d = run["data"]
    assert d["dropped_missing_label"] == 12
    assert d["dropped_missing_label_train"] + d["dropped_missing_label_holdout"] == 12
    assert d["train_rows"] + d["holdout_rows"] == 300 - 12
    assert run["holdout"]["n_rows"] == d["holdout_rows"]


def test_cancel_queued_and_running(client, tickets_ds, churn_ds):
    # One fit at a time: the first runs, the second waits in the queue.
    slow = start_run(client, dataset_id=tickets_ds["id"], time_limit=600, feature_importance=False,
                     fit_kwargs={"hyperparameters": {"GBM": {"num_boost_round": 5000}}, "num_bag_folds": 5})
    queued = start_run(client, dataset_id=churn_ds["id"], time_limit=60, fit_kwargs={"hyperparameters": TINY_HP})
    assert client.get(f"/api/runs/{queued['id']}").json()["status"] == "queued"
    assert client.post(f"/api/runs/{queued['id']}/cancel").status_code == 200
    q = wait_for_run(client, queued["id"], timeout=30)
    assert q["status"] == "cancelled"
    assert read_sse(client, f"/api/runs/{queued['id']}/events")[-1]["event"] == "run_cancelled"

    wait_for_event(client, slow["id"], "model_started")  # cancel in the middle of the fit
    assert client.post(f"/api/runs/{slow['id']}/cancel").status_code == 200
    s = wait_for_run(client, slow["id"], timeout=60)
    assert s["status"] == "cancelled"
    events = read_sse(client, f"/api/runs/{slow['id']}/events")
    assert events[-1]["event"] == "run_cancelled"
    assert client.post(f"/api/runs/{slow['id']}/cancel").status_code == 409  # already finished
    assert client.post(f"/api/runs/{slow['id']}/predict", json={"rows": [{}]}).status_code == 409
    # The worker process is gone.
    pid = next(e["data"]["pid"] for e in events if e["event"] == "run_started")
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_delete_run(client, churn_ds):
    run = start_run(client, dataset_id=churn_ds["id"], time_limit=60, feature_importance=False,
                    fit_kwargs={"hyperparameters": {"LR": {}}})
    assert client.delete(f"/api/runs/{run['id']}").status_code in (409, 204)
    run = wait_for_run(client, run["id"])
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204
    assert client.get(f"/api/runs/{run['id']}").status_code == 404
    assert not Path(run["predictor_path"]).parent.exists()


def test_a_failing_model_is_reported_and_the_run_goes_on(client, tiny_dataset):
    run = start_run(client, dataset_id=tiny_dataset["id"], time_limit=60, feature_importance=False,
                    fit_kwargs={"hyperparameters": {"KNN": {"n_neighbors": -3}, "LR": {}}})
    events = read_sse(client, f"/api/runs/{run['id']}/events")
    run = wait_for_run(client, run["id"])
    assert run["status"] == "succeeded", run["error"]
    failed = [e["data"] for e in events if e["event"] == "model_failed"]
    assert len(failed) == 1 and failed[0]["model"].startswith("KNeighbors") and failed[0]["family"] == "KNN"
    assert failed[0]["skipped"] is False
    assert "n_neighbors" in failed[0]["error"] or "ValueError" in failed[0]["error"]
    assert "KNeighbors" not in {r["model"] for r in run["leaderboard"]}
