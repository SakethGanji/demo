"""S3: a weighted ensemble of models the user chose (POST /api/runs/{id}/ensemble), holdout predictions, and
column types passed to AutoGluon as feature_metadata. Tiny fits on small uploads, so this stays in the fast tier."""

from __future__ import annotations

import io
import math
import sys

import numpy as np
import pandas as pd
import pytest

from .conftest import start_run, wait_for_run
from .test_runs import make_tabular_csv

HP3 = {"GBM": {"num_boost_round": 20}, "LR": {}, "KNN": {}}


def _upload(client, name: str, df: pd.DataFrame, target: str | None = None) -> dict:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    r = client.post("/api/datasets", files={"file": (name, buf.getvalue().encode(), "text/csv")},
                    data={"target": target} if target else None)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="module")
def binary_run(client):
    _, df = make_tabular_csv(n=320, seed=11)
    ds = _upload(client, "ens_binary.csv", df)
    run = start_run(client, dataset_id=ds["id"], time_limit=60, feature_importance=False,
                    fit_kwargs={"hyperparameters": HP3})
    out = wait_for_run(client, run["id"], timeout=240)
    assert out["status"] == "succeeded", out["error"]
    return out


@pytest.fixture(scope="module")
def regression_run(client):
    rng = np.random.default_rng(5)
    n = 300
    x1, x2 = rng.normal(size=n), rng.normal(size=n)
    seg = rng.choice(["retail", "sme", "corp"], size=n)
    y = 1000 + 300 * x1 - 120 * x2 + (seg == "corp") * 250 + rng.normal(scale=40, size=n)
    df = pd.DataFrame({"app_id": [f"a{i}" for i in range(n)], "x1": x1.round(4), "x2": x2.round(4), "segment": seg,
                       "amount": y.round(2)})
    ds = _upload(client, "ens_regression.csv", df, target="amount")
    assert ds["profile"]["task"] == "regression"
    run = start_run(client, dataset_id=ds["id"], time_limit=60, feature_importance=False,
                    fit_kwargs={"hyperparameters": HP3})
    out = wait_for_run(client, run["id"], timeout=240)
    assert out["status"] == "succeeded", out["error"]
    return out


def _base_models(run) -> list[str]:
    return [r["model"] for r in run["leaderboard"] if not r["model"].startswith("WeightedEnsemble")]


def _check_result(res, run, chosen):
    assert res["model"].startswith("WeightedEnsemble_L") and "_Custom" in res["model"]
    w = res["weights"]
    assert set(w) <= set(chosen) and w
    assert math.isclose(sum(w.values()), 1.0, abs_tol=1e-6)
    assert all(v >= 0 for v in w.values())
    assert res["n_selected"] == sum(1 for v in w.values() if v > 0)
    s = res["scores"]
    for k in ("score_val", "score_test", "score_val_natural", "score_test_natural", "fit_time", "pred_time_test"):
        assert s[k] is not None and math.isfinite(s[k]), k
    assert res["n_holdout"] == run["data"]["holdout_rows"]
    # The verdict is computed honestly from the numbers it reports.
    best = res["best_single"]
    assert best["model"] in _base_models(run)
    for split, key in (("val", "score_val"), ("holdout", "score_test")):
        v = res["vs_best_single"][split]
        assert math.isclose(v["delta"], s[key] - best[key], abs_tol=1e-9)
        assert v["beats"] == (s[key] > best[key])
    # Its validation score is no worse than the best chosen model's: greedy selection starts from it.
    assert s["score_val"] >= res["best_chosen_single"]["score_val"] - 1e-9


def test_custom_ensemble_binary(client, binary_run, data_dir):
    run = binary_run
    chosen = [m for m in _base_models(run) if m in ("LightGBM", "LinearModel")]
    assert len(chosen) == 2, _base_models(run)
    r = client.post(f"/api/runs/{run['id']}/ensemble", json={"models": chosen})
    assert r.status_code == 201, r.text
    res = r.json()
    _check_result(res, run, chosen)

    after = client.get(f"/api/runs/{run['id']}").json()
    # The run's default model is unchanged; the new ensemble is listed on the leaderboard and in custom_ensembles.
    assert after["best_model"] == run["best_model"]
    assert [c["model"] for c in after["custom_ensembles"]] == [res["model"]]
    row = next(x for x in after["leaderboard"] if x["model"] == res["model"])
    assert row["custom"] is True and row["family"] == "ENS_WEIGHTED" and row["weights"] == res["weights"]
    assert [x["rank"] for x in after["leaderboard"]] == list(range(1, len(after["leaderboard"]) + 1))
    vals = [x["score_val"] for x in after["leaderboard"]]
    assert vals == sorted(vals, reverse=True)

    # Predict with it by name; the default prediction still uses the run's best model.
    rows = [{"x1": 1.2, "x2": -0.4, "grp": "a"}, {"x1": -1.0, "x2": 0.8, "grp": "c"}]
    p = client.post(f"/api/runs/{run['id']}/predict", json={"rows": rows, "model": res["model"]}).json()
    assert p["model"] == res["model"] and len(p["predictions"]) == 2
    assert all(math.isclose(sum(pr.values()), 1, abs_tol=1e-6) for pr in p["probabilities"])
    d = client.post(f"/api/runs/{run['id']}/predict", json={"rows": rows}).json()
    assert d["model"] == run["best_model"]

    # A second one gets its own name; the first is kept.
    r2 = client.post(f"/api/runs/{run['id']}/ensemble", json={"models": _base_models(run)})
    assert r2.status_code == 201, r2.text
    assert r2.json()["model"].endswith("_Custom2")
    after2 = client.get(f"/api/runs/{run['id']}").json()
    assert len(after2["custom_ensembles"]) == 2


def test_custom_ensemble_of_one_model_is_that_model(client, binary_run):
    run = binary_run
    r = client.post(f"/api/runs/{run['id']}/ensemble", json={"models": ["LightGBM"]})
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["weights"] == {"LightGBM": 1.0}
    lgb = next(x for x in run["leaderboard"] if x["model"] == "LightGBM")
    assert math.isclose(res["scores"]["score_test"], lgb["score_test"], abs_tol=1e-9)


def test_custom_ensemble_saved_in_plain_autogluon(client, binary_run):
    """The predictor on disk is still a plain AutoGluon predictor, now with the new model in it."""
    import subprocess

    run = client.get(f"/api/runs/{binary_run['id']}").json()
    names = [c["model"] for c in run["custom_ensembles"]]
    code = ("import sys; from autogluon.tabular import TabularPredictor; "
            f"p = TabularPredictor.load({run['predictor_path']!r}, verbosity=0); "
            f"assert set({names!r}) <= set(p.model_names()), p.model_names(); "
            f"assert p.model_best == {run['best_model']!r}, p.model_best; print('ok')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and "ok" in out.stdout, out.stderr[-2000:]


@pytest.mark.parametrize("body, code, text", [
    ({"models": ["NoSuchModel"]}, 400, "No model named"),
    ({"models": ["WeightedEnsemble_L2"]}, 400, "not weighted ensembles"),
    ({"models": []}, 422, ""),
    ({}, 422, ""),
])
def test_custom_ensemble_rejects_bad_requests(client, binary_run, body, code, text):
    r = client.post(f"/api/runs/{binary_run['id']}/ensemble", json=body)
    assert r.status_code == code, r.text
    assert text in r.text


def test_custom_ensemble_unknown_or_unfinished_run(client, binary_run):
    assert client.post("/api/runs/run_nope/ensemble", json={"models": ["LightGBM"]}).status_code == 404
    ds = binary_run["dataset_id"]
    run = start_run(client, dataset_id=ds, time_limit=60, feature_importance=False, fit_kwargs={"hyperparameters": HP3})
    r = client.post(f"/api/runs/{run['id']}/ensemble", json={"models": ["LightGBM"]})
    assert r.status_code == 409 and "needs to finish" in r.text
    wait_for_run(client, run["id"], timeout=240)


def test_custom_ensemble_regression(client, regression_run):
    run = regression_run
    assert run["problem_type"] == "regression" and run["eval_metric"]["greater_is_better"] is False
    chosen = _base_models(run)
    r = client.post(f"/api/runs/{run['id']}/ensemble", json={"models": chosen})
    assert r.status_code == 201, r.text
    res = r.json()
    _check_result(res, run, chosen)
    assert res["greater_is_better"] is False
    # RMSE in natural units is positive; AutoGluon's higher-is-better score is its negative.
    assert res["scores"]["score_test_natural"] > 0
    assert math.isclose(res["scores"]["score_test_natural"], -res["scores"]["score_test"], rel_tol=1e-9)
    p = client.post(f"/api/runs/{run['id']}/predict", json={"rows": [{"x1": 0.5, "x2": 0.1, "segment": "corp"}],
                                                           "model": res["model"]}).json()
    assert p["probabilities"] is None and isinstance(p["predictions"][0], float)


def test_holdout_predictions(client, binary_run, regression_run):
    h = client.get(f"/api/runs/{binary_run['id']}/holdout-predictions").json()
    assert h["n_rows"] == binary_run["data"]["holdout_rows"] == len(h["y_true"]) == len(h["y_pred"]) == len(h["rows"])
    assert set(h["classes"]) == {"yes", "no"}
    assert all(math.isclose(sum(p), 1, abs_tol=1e-4) for p in h["probabilities"])
    acc = np.mean(np.array(h["y_true"]) == np.array(h["y_pred"]))
    assert math.isclose(acc, binary_run["holdout"]["metrics"]["accuracy"], abs_tol=1e-9)

    g = client.get(f"/api/runs/{regression_run['id']}/holdout-predictions").json()
    assert g["classes"] is None and g["probabilities"] is None
    rmse = float(np.sqrt(np.mean((np.array(g["y_true"]) - np.array(g["y_pred"])) ** 2)))
    assert math.isclose(rmse, regression_run["holdout"]["metrics"]["root_mean_squared_error"], rel_tol=1e-6)
    assert client.get("/api/runs/run_nope/holdout-predictions").status_code == 404


def test_column_types_become_feature_metadata(client, binary_run):
    ds = binary_run["dataset_id"]
    bad = client.post("/api/runs", json={"dataset_id": ds, "column_types": {"nope": "text"}})
    assert bad.status_code == 400 and "no column named" in bad.text
    bad = client.post("/api/runs", json={"dataset_id": ds, "column_types": {"outcome": "category"}})
    assert bad.status_code == 400 and "label" in bad.text
    bad = client.post("/api/runs", json={"dataset_id": ds, "column_types": {"x1": "vector"}})
    assert bad.status_code == 422

    run = start_run(client, dataset_id=ds, time_limit=60, feature_importance=False,
                    column_types={"x2": "category", "grp": "text"},
                    fit_kwargs={"hyperparameters": {"GBM": {"num_boost_round": 10}}})
    out = wait_for_run(client, run["id"], timeout=240)
    assert out["status"] == "succeeded", out["error"]
    fm = out["resolved"]["fit_kwargs"]["feature_metadata"]
    assert fm["type_map_raw"]["x2"] == "object" and fm["type_map_raw"]["x1"] == "float"
    assert fm["type_group_map_special"]["text"] == ["grp"]
    assert any("feature_metadata" in n for n in out["resolved"]["notes"])
    # Prediction takes the raw values; AutoGluon casts them as it did in training.
    p = client.post(f"/api/runs/{run['id']}/predict", json={"rows": [{"x1": 0.3, "x2": 1.25, "grp": "b"}]})
    assert p.status_code == 200, p.text
