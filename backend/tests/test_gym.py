"""The Training Gym's backend: the configuration schema, validation against TabularPredictor.fit, the resolved call
and its diff, AutoGluon's bagging/stacking decision, the fit-time estimate, a run's config round trip, the export
script (run for real: its leaderboard must equal the run's), and the MLflow tracking fix."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import start_run, wait_for_run
from .test_runs import TINY_HP, make_tabular_csv

GYM_FAMILIES = ["GBM", "CAT", "XGB", "RF", "XT", "KNN", "LR", "NN_TORCH", "FASTAI", "TFIDF_LR", "TFIDF_SVM"]


@pytest.fixture(scope="module")
def tiny(client):
    content, _ = make_tabular_csv(n=300, seed=11)
    r = client.post("/api/datasets", files={"file": ("gym_tiny.csv", content, "text/csv")})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="module")
def tiny_run(client, tiny):
    run = start_run(client, dataset_id=tiny["id"], time_limit=60, feature_importance=False, calibrate_threshold="f1",
                    fit_kwargs={"hyperparameters": TINY_HP})
    return wait_for_run(client, run["id"])


def test_schema_families_and_params(client):
    s = client.get("/api/gym/schema").json()
    keys = [f["key"] for f in s["families"]]
    assert keys == GYM_FAMILIES
    for f in s["families"]:
        assert f["objective"] and f["symbols"], f["key"]
        for p in f["params"]:
            if "choices" in p:
                assert p["default"] in p["choices"]
                continue
            assert p["min"] < p["max"], (f["key"], p["key"])
            assert p["min"] <= p["default"] <= p["max"], (f["key"], p["key"], p["default"])
            # every slider's symbol is defined in the family's where-list or its help
            assert p["sym"] and p["help"]
    gbm = next(f for f in s["families"] if f["key"] == "GBM")
    lr = next(p for p in gbm["params"] if p["key"] == "learning_rate")
    assert (lr["min"], lr["max"], lr["log"], lr["sym"]) == (1e-3, 1.0, True, "η")
    assert lr["default_source"] == "autogluon" and lr["default"] == 0.05  # read from LGBModel's own defaults
    cat = next(f for f in s["families"] if f["key"] == "CAT")
    assert any(p["key"] == "depth" and p["integer"] for p in cat["params"])
    # the fit keywords come from the installed AutoGluon (named parameters and the **kwargs it validates)
    fit = s["fit"]
    for k in ("num_bag_folds", "num_bag_sets", "num_stack_levels", "dynamic_stacking", "hyperparameter_tune_kwargs",
              "refit_full", "infer_limit", "num_gpus", "holdout_frac", "excluded_model_types", "unlabeled_data", "calibrate_decision_threshold"):
        assert k in fit["keys"], k
    assert fit["defaults"]["dynamic_stacking"] is False and fit["defaults"]["num_gpus"] == "auto"
    assert s["gpu"]["count"] == 0 and "No CUDA" in s["gpu"]["note"]
    assert "roc_auc" in s["metrics"]["binary"] and "r2" in s["metrics"]["regression"]
    assert {p["key"] for p in s["presets"]} >= {"medium_quality", "good_quality", "high_quality", "best_quality"}


def _v(client, **body):
    r = client.post("/api/gym/validate", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_validate_rejects_what_autogluon_would(client, tiny):
    v = _v(client, dataset_id=tiny["id"], fit_kwargs={"num_bag_fold": 3})
    assert not v["ok"]
    e = v["errors"][0]
    assert e["path"] == "fit_kwargs.num_bag_fold" and "Did you mean 'num_bag_folds'" in e["message"]
    v = _v(client, dataset_id=tiny["id"], fit_kwargs={"num_bag_folds": 1})
    assert any("num_bag_folds = 1" in e["message"] for e in v["errors"])
    v = _v(client, dataset_id=tiny["id"], fit_kwargs={"num_gpus": 1})
    assert any(e["path"] == "fit_kwargs.num_gpus" and "0 CUDA" in e["message"] for e in v["errors"])
    v = _v(client, dataset_id=tiny["id"], fit_kwargs={"num_stack_levels": 1, "num_bag_folds": 0})
    assert any("needs bagging" in e["message"] for e in v["errors"])
    v = _v(client, dataset_id=tiny["id"], fit_kwargs={"hyperparameters": {"CATT": {}}})
    assert any("Did you mean 'CAT'" in e["message"] for e in v["errors"])
    v = _v(client, dataset_id=tiny["id"], eval_metric="r2")
    assert any(e["path"] == "eval_metric" for e in v["errors"])
    v = _v(client, dataset_id=tiny["id"], predictor_kwargs={"sample_weight": "nope"})
    assert any(e["path"] == "predictor_kwargs.sample_weight" for e in v["errors"])
    v = _v(client, dataset_id=tiny["id"], fit_kwargs={"hyperparameter_tune_kwargs": {"scheduler": "ray"}})
    assert any("local" in e["message"] for e in v["errors"])
    v = _v(client, dataset_id=tiny["id"], fit_kwargs={"hyperparameters": {"GBM": {"learning_rate": 5}}})
    assert v["ok"] and any("outside the usual interval" in w["message"] for w in v["warnings"])
    # POST /api/runs refuses the same body before anything trains
    r = client.post("/api/runs", json={"dataset_id": tiny["id"], "fit_kwargs": {"num_bag_fold": 3}})
    assert r.status_code == 400 and "num_bag_folds" in r.json()["detail"]


def test_validate_resolves_the_call_and_autogluons_choices(client, tiny):
    v = _v(client, dataset_id=tiny["id"], presets="best_quality", time_limit=90, eval_metric="roc_auc",
           fit_kwargs={"num_bag_folds": 4, "hyperparameters": {"GBM": {"learning_rate": 0.03}, "CAT": {"depth": 4}},
                       "hyperparameter_tune_kwargs": {"scheduler": "local", "searcher": "random", "num_trials": 3}},
           pseudolabel=None, distill={"time_limit": 20})
    assert v["ok"], v["errors"]
    fk = v["resolved"]["fit_kwargs"]
    assert fk["presets"] == "best_quality" and fk["time_limit"] == 90 and fk["num_bag_folds"] == 4
    # F11: a binary label adds the SCORECARD challenger (on by default) to the families asked for
    assert "SCORECARD" in fk["hyperparameters"]
    assert {k: x for k, x in fk["hyperparameters"].items() if k != "SCORECARD"} == {"GBM": {"learning_rate": 0.03}, "CAT": {"depth": 4}}
    assert v["resolved"]["post_fit"] == [{"call": "distill", "kwargs": {"time_limit": 20}}]
    assert "predictor.fit(\n    train_data,\n    presets=\"best_quality\"" in v["call"]
    assert "predictor.distill(time_limit=20)" in v["call"]
    # best_quality's own expansion is shown as preset-origin lines of the diff, against fit()'s defaults
    diff = {d["key"]: d for d in v["diff"]}
    assert diff["auto_stack"]["origin"] == "preset" and diff["auto_stack"]["value"] is True and diff["auto_stack"]["default"] is False
    assert diff["num_bag_folds"]["origin"] == "you" and diff["num_bag_folds"]["changed"]
    # AutoGluon's own decision for this data: k = 4 as given, stacking from auto_stack
    eff = v["effective"]
    assert eff["source"].startswith("autogluon") and eff["num_bag_folds"] == 4 and eff["num_stack_levels"] >= 1
    est = v["estimate"]
    assert est["k"] == 4 and est["L"] == eff["num_stack_levels"] and est["n"] == v["data"]["train_rows"]
    # F11: the default SCORECARD challenger is estimated too (one fit: it is not tuned)
    tuned = [f for f in est["families"] if f["family"] != "SCORECARD"]
    assert {f["family"] for f in est["families"]} == {"GBM", "CAT", "SCORECARD"} and all(f["trials"] == 3 for f in tuned)
    assert est["seconds"] <= 90
    # a smaller budget binds and says so
    small = _v(client, dataset_id=tiny["id"], presets="best_quality", time_limit=1)
    assert small["estimate"]["budget_binds"] and any(w["path"] == "time_limit" for w in small["warnings"])


def test_estimate_uses_measured_rates(client, tiny, tiny_run):
    v = _v(client, dataset_id=tiny["id"], time_limit=60, fit_kwargs={"hyperparameters": TINY_HP})
    fams = {f["family"]: f for f in v["estimate"]["families"]}
    assert fams["GBM"]["rate_source"].startswith("median of") and fams["LR"]["rate_source"].startswith("median of")


def test_run_config_round_trip(client, tiny, tiny_run):
    g = client.get(f"/api/runs/{tiny_run['id']}/gym").json()
    body = g["body"]
    assert body["dataset_id"] == tiny["id"] and body["fit_kwargs"] == {"hyperparameters": TINY_HP}
    assert body["calibrate_threshold"] == "f1" and body["drop_columns"] == ["row_id"]
    v = _v(client, **body)
    hp = v["resolved"]["fit_kwargs"]["hyperparameters"]  # F11: plus the default SCORECARD challenger (binary)
    assert v["ok"] and {k: x for k, x in hp.items() if k != "SCORECARD"} == TINY_HP and "SCORECARD" in hp


def test_completed_run_logs_to_mlflow_without_warning(client, tiny_run):
    """S5's bug: the worker's 'tracked' event passed run_id twice (RunStore.emit's own and MLflow's), so the log
    ended with 'MLflow logging failed'. The run must be tracked and its event stream must carry no such warning."""
    assert tiny_run["status"] == "succeeded"
    evs = client.get(f"/api/runs/{tiny_run['id']}/events", params={"format": "json"}).json()["events"]
    tracked = [e for e in evs if e["type"] == "tracked"]
    assert len(tracked) == 1
    t = tracked[0]
    assert t["mlflow_run_id"] == tiny_run["mlflow"]["run_id"] and t["logged"] is True
    bad = [e for e in evs if e["type"] == "log" and "MLflow" in e.get("message", "")]
    assert bad == [], bad
    assert not [e for e in evs if e["type"] == "log" and e.get("level") == "warning" and "ensemble" == e.get("logger")]


def test_export_reproduces_the_run(client, tiny_run, tmp_path):
    r = client.get(f"/api/runs/{tiny_run['id']}/export.py")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/x-python")
    script = tmp_path / "export.py"
    script.write_text(r.text)
    assert "FIT_KWARGS = " in r.text and "HOLDOUT_ROWS = " in r.text and 'THRESHOLD = {"source": "calibrated"' in r.text
    out = tmp_path / "repro"
    p = subprocess.run([sys.executable, "-I", str(script), "--out", str(out)], capture_output=True, text=True, timeout=600)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    assert "max |Δ score| = 0" in p.stdout, p.stdout[-1500:]
    import pandas as pd

    lb = pd.read_csv(out / "leaderboard.csv").set_index("model")
    run_lb = {x["model"]: x for x in tiny_run["leaderboard"]}
    assert set(lb.index) == set(run_lb)
    for m, row in run_lb.items():
        assert lb.loc[m, "score_val"] == pytest.approx(row["score_val"], abs=1e-9)
        assert lb.loc[m, "score_test"] == pytest.approx(row["score_test"], abs=1e-9)
    # the reproduced predictor uses the run's calibrated threshold
    from autogluon.tabular import TabularPredictor

    assert TabularPredictor.load(str(out), verbosity=0).decision_threshold == pytest.approx(tiny_run["threshold"]["value"], abs=1e-12)
    # a different data file is refused by its hash
    other = tmp_path / "other.csv"
    other.write_text(Path(tiny_run["config"]["csv_path"]).read_text().replace("yes", "no", 1))
    p = subprocess.run([sys.executable, "-I", str(script), "--data", str(other), "--out", str(tmp_path / "x")],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode != 0 and "SHA-256" in (p.stdout + p.stderr)
    assert client.get("/api/runs/run_nope/export.py").status_code == 404


@pytest.mark.slow
def test_export_reproduces_post_fit_steps_and_text_models(client, tickets_ds, tmp_path):
    """A text run (custom TF-IDF models), with pseudo-labelling on unlabeled rows and distillation after the fit:
    the exported script gives the same leaderboard, model for model."""
    from .seed_data import generate_tickets

    df = generate_tickets(n=2_000, seed=4).sample(300, random_state=4).drop(columns=["category"])
    u = client.post("/api/datasets", files={"file": ("tickets_unlabeled.csv", df.to_csv(index=False).encode(), "text/csv")}).json()
    # budgets far from binding: a step cut short by its time limit is not reproducible to the last model
    run = start_run(client, dataset_id=tickets_ds["id"], time_limit=600, feature_importance=False,
                    fit_kwargs={"hyperparameters": {"GBM": {"num_boost_round": 40}}},
                    pseudolabel={"dataset_id": u["id"], "method": "fit_pseudolabel", "max_iter": 1},
                    distill={"time_limit": 600, "hyperparameters": {"GBM": {"num_boost_round": 20}}})
    run = wait_for_run(client, run["id"])
    assert run["status"] == "succeeded", run["error"]
    models = {r["model"] for r in run["leaderboard"]}
    assert any(m.endswith("_PSEUDO_1") for m in models) and any(m.endswith("_DSTL") for m in models)
    assert {"TFIDF_LR", "TFIDF_SVM"} <= set(run["resolved"]["fit_kwargs"]["hyperparameters"])
    assert [p["call"] for p in run["resolved"]["post_fit"]] == ["fit_pseudolabel", "distill"]
    script = tmp_path / "export.py"
    script.write_text(client.get(f"/api/runs/{run['id']}/export.py").text)
    # Exact for every model except possibly a one-row flip of a validation score: the TF-IDF logistic regression
    # refit on pseudo-labelled rows uses multithreaded BLAS, whose summation order can move one probability across
    # a decision boundary. One row of the 1,600 validation rows is 0.000625, so the tolerance is 1/1,600.
    p = subprocess.run([sys.executable, "-I", str(script), "--out", str(tmp_path / "repro"), "--tolerance", "0.000626"],
                       capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    import pandas as pd

    lb = pd.read_csv(tmp_path / "repro" / "leaderboard.csv").set_index("model")
    for r in run["leaderboard"]:
        assert lb.loc[r["model"], "score_test"] == pytest.approx(r["score_test"], abs=1e-9), r["model"]  # holdout: exact
