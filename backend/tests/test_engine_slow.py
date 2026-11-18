"""Slow tier for the engine: every sample trains over HTTP above a score floor, bagging gives fold spreads and
out-of-fold predictions, and the same data and config give the same leaderboard as raw AutoGluon."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from .conftest import read_sse, start_run, wait_for_run

pytestmark = pytest.mark.slow

HP = {"GBM": {"num_boost_round": 200}, "LR": {}, "RF": {"n_estimators": 100}}

# dataset, request, (holdout metric, floor). Floors sit a little under what these configs score (seed 42 data).
SAMPLES = [
    ("support-tickets", {"fit_kwargs": {"hyperparameters": {"GBM": {"num_boost_round": 200}}}}, ("accuracy", 0.935)),
    ("account-churn", {"eval_metric": "roc_auc", "fit_kwargs": {"hyperparameters": HP}}, ("roc_auc", 0.73)),
    ("credit-default", {"eval_metric": "roc_auc", "fit_kwargs": {"hyperparameters": HP}}, ("roc_auc", 0.75)),
    ("loan-amount", {"fit_kwargs": {"hyperparameters": HP}}, ("r2", 0.70)),
]


@pytest.mark.parametrize("dataset_id, body, floor", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_every_sample_trains(client, dataset_id, body, floor):
    run = start_run(client, dataset_id=dataset_id, time_limit=600, **body)
    events = read_sse(client, f"/api/runs/{run['id']}/events")
    assert events[-1]["event"] == "run_succeeded", events[-1]
    run = wait_for_run(client, run["id"])
    assert run["status"] == "succeeded", run["error"]

    metric, minimum = floor
    assert run["holdout"]["metrics"][metric] >= minimum, run["holdout"]["metrics"]
    lb = run["leaderboard"]
    assert lb[0]["model"] == run["best_model"] or lb[0]["score_test"] >= max(r["score_test"] for r in lb) - 1e-12
    assert math.isclose(sum(run["ensemble"]["weights"].values()), 1, abs_tol=1e-9)
    assert run["feature_importance"] and run["feature_importance"][0]["importance"] > 0

    if dataset_id == "support-tickets":  # text: the custom TF-IDF models train and explain themselves
        assert run["problem_type"] == "multiclass"
        fams = {r["family"] for r in lb}
        assert {"TFIDF_LR", "TFIDF_SVM", "GBM", "ENS_WEIGHTED"} <= fams
        assert "TFIDF_LR" in run["resolved"]["fit_kwargs"]["hyperparameters"]
        assert run["resolved"]["fit_kwargs"]["_feature_generator_kwargs"] == {"enable_raw_text_features": True}
        tm = run["text_models"]
        assert set(tm) == {"TfidfLogReg", "TfidfLinearSVM"}
        for words in tm.values():
            assert set(words) == set(run["classes"])
            assert len(words["Billing"]) == 15
            top = {w for w, _ in words["Billing"][:10]}
            assert top & {"invoice", "refund", "charged", "billed", "subscription", "payment", "receipt"}, top
        tfidf_scores = [r["score_test"] for r in lb if r["family"] in ("TFIDF_LR", "TFIDF_SVM")]
        assert min(tfidf_scores) >= 0.89
    if dataset_id == "loan-amount":
        assert run["problem_type"] == "regression"
        assert run["data"]["dropped_missing_label"] == 40
        assert run["eval_metric"] == {**run["eval_metric"], "name": "root_mean_squared_error", "greater_is_better": False}
        assert run["holdout"]["metrics"]["root_mean_squared_error"] < 6800
        assert all(r["score_test_natural"] == pytest.approx(-r["score_test"]) for r in lb)
    if dataset_id in ("account-churn", "credit-default"):
        assert run["problem_type"] == "binary" and run["positive_class"] == "Yes"

    # Predict a few holdout rows through the API.
    holdout_rows = json.loads(Path(run["config"]["splits_path"]).read_text())["holdout"][:5]
    rows = pd.read_csv(Path(run["config"]["csv_path"])).iloc[holdout_rows]
    rows = rows.drop(columns=[run["label"]]).replace({np.nan: None}).to_dict(orient="records")
    r = client.post(f"/api/runs/{run['id']}/predict", json={"rows": rows})
    assert r.status_code == 200, r.text
    assert len(r.json()["predictions"]) == 5


def test_bagging_gives_fold_scores_and_oof(client):
    run = start_run(client, dataset_id="credit-default", eval_metric="roc_auc", time_limit=600, feature_importance=False,
                    fit_kwargs={"hyperparameters": {"GBM": {"num_boost_round": 100}, "LR": {}}, "num_bag_folds": 5})
    events = read_sse(client, f"/api/runs/{run['id']}/events")
    run = wait_for_run(client, run["id"])
    assert run["status"] == "succeeded", run["error"]
    finished = {e["data"]["model"]: e["data"] for e in events if e["event"] == "model_finished"}
    for name in ("LightGBM_BAG_L1", "LinearModel_BAG_L1"):
        f = finished[name]
        assert len(f["fold_scores"]) == 5 and f["n_children"] == 5 and f["val_score_std"] > 0
        assert min(f["fold_scores"]) <= f["val_score"] + 0.05 and 0.6 < np.mean(f["fold_scores"]) < 0.9
        row = next(r for r in run["leaderboard"] if r["model"] == name)
        assert row["fold_scores"] == f["fold_scores"] and row["val_score_std"] == f["val_score_std"]
    assert run["oof"]["available"] is True and "LightGBM_BAG_L1" in run["oof"]["models"]
    r = client.get(f"/api/runs/{run['id']}/oof", params={"model": "LightGBM_BAG_L1"})
    assert r.status_code == 200, r.text
    oof = r.json()
    assert len(oof["rows"]) == run["data"]["train_rows"] and oof["classes"] == ["No", "Yes"]
    holdout = set(json.loads(Path(run["config"]["splits_path"]).read_text())["holdout"])
    assert not holdout & set(oof["rows"])  # out-of-fold rows are training rows only
    assert all(math.isclose(sum(p), 1, abs_tol=1e-4) for p in oof["probabilities"][:50])


# -- parity with raw AutoGluon ---------------------------------------------------------

def _raw_frames(run):
    cfg = run["config"]
    df = pd.read_csv(cfg["csv_path"])
    holdout = json.loads(Path(cfg["splits_path"]).read_text())["holdout"]
    is_h = np.zeros(len(df), dtype=bool)
    is_h[holdout] = True
    df = df.drop(columns=cfg["drop_columns"])
    has_label = df[cfg["label"]].notna().to_numpy()
    return df.loc[~is_h & has_label], df.loc[is_h & has_label]


def _compare(api_lb, raw_lb, tol=1e-6):
    api = {r["model"]: r for r in api_lb}
    raw = {r["model"]: r for r in raw_lb.to_dict(orient="records")}
    assert set(api) == set(raw)
    for m in api:
        assert api[m]["score_val"] == pytest.approx(raw[m]["score_val"], abs=tol), m
        assert api[m]["score_test"] == pytest.approx(raw[m]["score_test"], abs=tol), m
        assert api[m]["stack_level"] == raw[m]["stack_level"]


def test_parity_with_raw_autogluon(client, tmp_path):
    """Same data, same config: the API's leaderboard equals TabularPredictor's, model for model."""
    from autogluon.tabular import TabularPredictor

    hp = {"GBM": {"num_boost_round": 100}, "RF": {"n_estimators": 50}, "LR": {}}
    run = start_run(client, dataset_id="credit-default", eval_metric="roc_auc", time_limit=None,
                    presets="medium_quality", feature_importance=False, fit_kwargs={"hyperparameters": hp})
    run = wait_for_run(client, run["id"])
    assert run["status"] == "succeeded", run["error"]
    assert run["resolved"]["fit_kwargs"] == {"presets": "medium_quality", "hyperparameters": hp}

    train, holdout = _raw_frames(run)
    assert len(train) == run["data"]["train_rows"] and len(holdout) == run["data"]["holdout_rows"]
    raw = TabularPredictor(label="defaulted", eval_metric="roc_auc", path=str(tmp_path / "raw"), verbosity=0)
    raw.fit(train, presets="medium_quality", hyperparameters=hp)
    _compare(run["leaderboard"], raw.leaderboard(holdout, silent=True))
    # The saved run is a plain predictor: it loads without ENSEMBLE and scores the holdout the same.
    loaded = TabularPredictor.load(run["predictor_path"], verbosity=0)
    assert loaded.evaluate(holdout, silent=True)["roc_auc"] == pytest.approx(run["holdout"]["scores"]["roc_auc"], abs=1e-9)


def test_parity_text_run_from_resolved_config(client, tmp_path):
    """A text run (custom TF-IDF models added) is reproduced exactly by its recorded `resolved` call."""
    import ensemble_models  # noqa: F401 — registers TFIDF_LR and TFIDF_SVM
    from autogluon.tabular import TabularPredictor

    run = start_run(client, dataset_id="support-tickets", time_limit=None, feature_importance=False,
                    fit_kwargs={"hyperparameters": {"GBM": {"num_boost_round": 50}}})
    run = wait_for_run(client, run["id"])
    assert run["status"] == "succeeded", run["error"]
    res = run["resolved"]
    assert set(res["fit_kwargs"]["hyperparameters"]) == {"GBM", "TFIDF_LR", "TFIDF_SVM"}

    train, holdout = _raw_frames(run)
    raw = TabularPredictor(path=str(tmp_path / "raw"), verbosity=0, **res["predictor_kwargs"])
    raw.fit(train, **res["fit_kwargs"])
    _compare(run["leaderboard"], raw.leaderboard(holdout, silent=True))
