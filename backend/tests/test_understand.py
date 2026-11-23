"""S4: understanding a run. Every endpoint's numbers are recomputed here independently, with scikit-learn, fairlearn,
pandas and scipy, from the run's holdout predictions (GET /holdout-predictions) and its data, and must match.

Tiny fits on small uploads (fast tier); the seeded credit data's calibrated F1 is in the slow tier."""

from __future__ import annotations

import io
import json
import math
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
from sklearn import metrics as skm
from sklearn.calibration import calibration_curve

from .conftest import BACKEND, start_run, wait_for_run

HP = {"GBM": {"num_boost_round": 30}, "LR": {}}


def _upload(client, name: str, df: pd.DataFrame, target: str | None = None) -> dict:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    r = client.post("/api/datasets", files={"file": (name, buf.getvalue().encode(), "text/csv")},
                    data={"target": target} if target else None)
    assert r.status_code == 201, r.text
    return r.json()


def _imbalanced(n: int = 900, seed: int = 3) -> pd.DataFrame:
    """~12% positives with a real signal, a categorical group that matters and a numeric one."""
    rng = np.random.default_rng(seed)
    x1, x2 = rng.normal(size=n), rng.normal(size=n)
    region = rng.choice(["north", "south", "east", "west"], size=n, p=[0.4, 0.3, 0.2, 0.1])
    age = rng.integers(20, 70, size=n)
    logit = -2.6 + 1.6 * x1 - 0.9 * x2 + (region == "south") * 0.7
    y = np.where(rng.random(n) < 1 / (1 + np.exp(-logit)), "Yes", "No")
    return pd.DataFrame({"app_id": [f"a{i}" for i in range(n)], "x1": x1.round(4), "x2": x2.round(4), "region": region,
                         "age": age, "default": y})


def _fit(client, ds_id: str, **kw) -> dict:
    run = start_run(client, dataset_id=ds_id, time_limit=60, feature_importance=False,
                    fit_kwargs={"hyperparameters": HP, **kw.pop("fit_kwargs", {})}, **kw)
    out = wait_for_run(client, run["id"], timeout=300)
    assert out["status"] == "succeeded", out["error"]
    return out


@pytest.fixture(scope="module")
def imb_ds(client):
    df = _imbalanced()
    assert 0.05 < (df["default"] == "Yes").mean() < 0.2
    return _upload(client, "imbalanced_default.csv", df, target="default")


@pytest.fixture(scope="module")
def binary_run(client, imb_ds):
    return _fit(client, imb_ds["id"], eval_metric="roc_auc", calibrate_threshold="f1")


@pytest.fixture(scope="module")
def multiclass_run(client):
    rng = np.random.default_rng(8)
    n = 600
    x1, x2 = rng.normal(size=n), rng.normal(size=n)
    seg = rng.choice(["retail", "sme", "corp"], size=n)
    score = np.stack([x1, x2, -x1 - x2 + (seg == "corp")], axis=1) + rng.normal(scale=0.7, size=(n, 3))
    y = np.array(["low", "mid", "high"])[score.argmax(axis=1)]
    df = pd.DataFrame({"x1": x1.round(4), "x2": x2.round(4), "segment": seg, "risk": y})
    ds = _upload(client, "risk_band.csv", df, target="risk")
    return _fit(client, ds["id"])


@pytest.fixture(scope="module")
def regression_run(client):
    rng = np.random.default_rng(21)
    n = 500
    x1, x2 = rng.normal(size=n), rng.normal(size=n)
    seg = rng.choice(["retail", "sme", "corp"], size=n)
    y = 5000 + 900 * x1 - 400 * x2 + (seg == "corp") * 700 + rng.normal(scale=150 + 100 * np.abs(x1), size=n)
    df = pd.DataFrame({"x1": x1.round(4), "x2": x2.round(4), "segment": seg, "amount": y.round(2)})
    ds = _upload(client, "amount.csv", df, target="amount")
    return _fit(client, ds["id"])


def _preds(client, run) -> pd.DataFrame:
    h = client.get(f"/api/runs/{run['id']}/holdout-predictions").json()
    df = pd.DataFrame({"row": h["rows"], "y_true": [str(v) for v in h["y_true"]], "y_pred": [str(v) for v in h["y_pred"]]})
    if h["probabilities"]:
        for i, c in enumerate(h["classes"]):
            df[f"p_{c}"] = [p[i] for p in h["probabilities"]]
    else:
        df["y_true"] = np.asarray(h["y_true"], dtype=float)
        df["y_pred"] = np.asarray(h["y_pred"], dtype=float)
    return df


def _yp(client, run):
    df = _preds(client, run)
    pos = str(run["positive_class"])
    return df, (df["y_true"] == pos).to_numpy().astype(int), df[f"p_{pos}"].to_numpy()


# --------------------------------------------------------------------------- threshold


def test_run_reports_calibrated_threshold(client, binary_run):
    t = binary_run["threshold"]
    assert t["source"] == "calibrated" and t["metric"] == "f1" and 0 <= t["value"] <= 1
    assert t["rule"] == "predict positive when p > t"
    df, y, p = _yp(client, binary_run)
    pos = str(binary_run["positive_class"])
    # The saved holdout predictions and AutoGluon's evaluate() both use t.
    assert ((df["y_pred"] == pos).to_numpy() == (p > t["value"])).all()
    yhat = (p > t["value"]).astype(int)
    m = binary_run["holdout"]["metrics"]
    assert math.isclose(m["f1"], skm.f1_score(y, yhat), abs_tol=1e-9)
    assert math.isclose(m["balanced_accuracy"], skm.balanced_accuracy_score(y, yhat), abs_tol=1e-9)
    assert binary_run["holdout"]["confusion_matrix"] == skm.confusion_matrix(
        df["y_true"], df["y_pred"], labels=[str(c) for c in binary_run["holdout"]["classes"]]).tolist()
    # The record's before/after counts are the same computation at 0.5 and at t.
    for key, thr in (("holdout_at_default", 0.5), ("holdout_at_threshold", t["value"])):
        tn, fp, fn, tp = skm.confusion_matrix(y, (p > thr).astype(int), labels=[0, 1]).ravel()
        assert (t[key]["tp"], t[key]["fp"], t[key]["fn"], t[key]["tn"]) == (tp, fp, fn, tn)


def test_threshold_grid_matches_sklearn(client, binary_run):
    r = client.get(f"/api/runs/{binary_run['id']}/threshold", params={"cost_fp": 1, "cost_fn": 5, "t": 0.3137})
    assert r.status_code == 200, r.text
    a = r.json()
    df, y, p = _yp(client, binary_run)
    assert a["n"] == len(y) and a["n_positive"] == int(y.sum()) and math.isclose(a["prevalence"], y.mean())
    assert len(a["grid"]) == 201
    for g in a["grid"][::7] + [a["at"], a["current"], a["default"]]:
        t = g["threshold"]
        yhat = (p > t).astype(int)
        tn, fp, fn, tp = skm.confusion_matrix(y, yhat, labels=[0, 1]).ravel()
        assert (g["tp"], g["fp"], g["fn"], g["tn"]) == (tp, fp, fn, tn), t
        assert math.isclose(g["precision"], skm.precision_score(y, yhat, zero_division=0), abs_tol=1e-12)
        assert math.isclose(g["recall"], skm.recall_score(y, yhat, zero_division=0), abs_tol=1e-12)
        assert math.isclose(g["f1"], skm.f1_score(y, yhat, zero_division=0), abs_tol=1e-12)
        assert math.isclose(g["balanced_accuracy"], skm.balanced_accuracy_score(y, yhat), abs_tol=1e-12)
        assert math.isclose(g["accuracy"], skm.accuracy_score(y, yhat), abs_tol=1e-12)
        assert math.isclose(g["mcc"], skm.matthews_corrcoef(y, yhat), abs_tol=1e-12)
        assert g["cost"] == 1 * fp + 5 * fn
    assert a["at"]["threshold"] == 0.3137
    assert math.isclose(a["current"]["threshold"], binary_run["threshold"]["value"])
    grid = a["grid"]
    assert a["best"]["f1"]["value"] == max(g["f1"] for g in grid)
    assert a["best"]["cost"]["value"] == min(g["cost"] for g in grid)
    assert math.isclose(a["bayes_threshold"], 1 / 6)
    h = a["score_histogram"]
    assert sum(h["positive"]) == y.sum() and sum(h["negative"]) == len(y) - y.sum()
    assert h["positive"] == np.histogram(p[y == 1], bins=np.linspace(0, 1, 51))[0].tolist()


def test_set_and_calibrate_threshold_persist(client, binary_run):
    rid = binary_run["id"]
    r = client.post(f"/api/runs/{rid}/threshold", json={"value": 0.3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["threshold"]["value"] == 0.3 and body["threshold"]["source"] == "manual"
    after = client.get(f"/api/runs/{rid}").json()
    assert after["threshold"]["value"] == 0.3
    df, y, p = _yp(client, after)
    yhat = (p > 0.3).astype(int)
    assert math.isclose(after["holdout"]["metrics"]["f1"], skm.f1_score(y, yhat), abs_tol=1e-9)
    pos = str(after["positive_class"])
    assert ((df["y_pred"] == pos).to_numpy() == (p > 0.3)).all()

    # /predict uses it: rows of the holdout, predicted again by the API.
    data = pd.read_csv(after["config"]["csv_path"])
    rows = data.loc[df["row"].to_numpy()[:40]].drop(columns=["default", "app_id"])
    pr = client.post(f"/api/runs/{rid}/predict", json={"rows": json.loads(rows.to_json(orient="records"))}).json()
    for label, probs in zip(pr["predictions"], pr["probabilities"]):
        assert (str(label) == pos) == (probs[pos] > 0.3)

    # It is saved in the predictor itself: plain AutoGluon loads it with that threshold.
    code = ("import sys; sys.path.insert(0, %r); import ensemble_models; from autogluon.tabular import TabularPredictor; "
            "print(TabularPredictor.load(%r, verbosity=0).decision_threshold)") % (str(BACKEND), after["predictor_path"])
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=180)
    assert out.stdout.strip().splitlines()[-1] == "0.3", out.stderr[-2000:]

    # Calibrate again (for balanced accuracy): AutoGluon picks t on its validation data, and it is kept.
    r = client.post(f"/api/runs/{rid}/threshold", json={"metric": "balanced_accuracy"})
    assert r.status_code == 200, r.text
    t = r.json()["threshold"]
    assert t["source"] == "calibrated" and t["metric"] == "balanced_accuracy" and 0 <= t["value"] <= 1
    assert client.get(f"/api/runs/{rid}").json()["threshold"]["value"] == t["value"]
    # Put the F1 calibration back for the other tests in this module.
    assert client.post(f"/api/runs/{rid}/threshold", json={"metric": "f1"}).status_code == 200


def test_threshold_bad_requests(client, binary_run, regression_run):
    rid = binary_run["id"]
    assert client.post(f"/api/runs/{rid}/threshold", json={}).status_code == 400
    assert client.post(f"/api/runs/{rid}/threshold", json={"metric": "f1", "value": 0.2}).status_code == 400
    assert client.post(f"/api/runs/{rid}/threshold", json={"metric": "roc_auc"}).status_code == 400
    assert client.post(f"/api/runs/{rid}/threshold", json={"value": 1.5}).status_code == 422
    assert client.get(f"/api/runs/{rid}/threshold", params={"t": 2}).status_code == 422
    assert client.get(f"/api/runs/{regression_run['id']}/threshold").status_code == 400
    assert client.post(f"/api/runs/{regression_run['id']}/threshold", json={"value": 0.3}).status_code == 400
    assert client.get("/api/runs/run_nope/threshold").status_code == 404


def test_calibrate_auto_and_user_override(client, imb_ds):
    auto = _fit(client, imb_ds["id"], eval_metric="roc_auc", calibrate_threshold="auto")
    assert auto["threshold"]["source"] == "calibrated" and auto["threshold"]["metric"] == "f1"
    # The user's own fit kwarg wins: AutoGluon is told not to calibrate, and ENSEMBLE does not either.
    off = _fit(client, imb_ds["id"], eval_metric="roc_auc", calibrate_threshold="f1",
               fit_kwargs={"calibrate_decision_threshold": False})
    assert off["threshold"]["source"] == "default" and off["threshold"]["value"] == 0.5
    assert off["resolved"]["fit_kwargs"]["calibrate_decision_threshold"] is False
    # Without the option nothing changes (S2/S3 behaviour, parity with raw AutoGluon).
    plain = _fit(client, imb_ds["id"], eval_metric="roc_auc")
    assert plain["threshold"]["source"] == "default" and plain["threshold"]["value"] == 0.5
    r = client.post("/api/runs", json={"dataset_id": imb_ds["id"], "calibrate_threshold": "roc_auc"})
    assert r.status_code == 400


# --------------------------------------------------------------------------- explain


def test_explain_binary_matches_sklearn(client, binary_run):
    e = client.get(f"/api/runs/{binary_run['id']}/explain").json()
    df, y, p = _yp(client, binary_run)
    pos = str(binary_run["positive_class"])
    assert e["kind"] == "classification" and e["positive_class"] == pos and e["n"] == len(df)
    fpr, tpr, _ = skm.roc_curve(y, p)
    assert np.allclose(e["roc"][pos]["fpr"], fpr) and np.allclose(e["roc"][pos]["tpr"], tpr)
    assert math.isclose(e["roc"][pos]["auc"], skm.roc_auc_score(y, p), abs_tol=1e-12)
    assert math.isclose(e["roc"][pos]["auc"], binary_run["holdout"]["metrics"]["roc_auc"], abs_tol=1e-6)
    prec, rec, _ = skm.precision_recall_curve(y, p)
    assert np.allclose(e["pr"][pos]["precision"], prec) and np.allclose(e["pr"][pos]["recall"], rec)
    assert math.isclose(e["pr"][pos]["average_precision"], skm.average_precision_score(y, p), abs_tol=1e-12)
    fp_, mp_ = calibration_curve(y, p, n_bins=10, strategy="uniform")
    cal = e["calibration"]
    assert np.allclose(cal["fraction_positive"], fp_) and np.allclose(cal["mean_predicted"], mp_)
    assert math.isclose(cal["brier"], skm.brier_score_loss(y, p), abs_tol=1e-12)
    assert sum(cal["counts"]) == len(y)
    bins = np.clip(np.digitize(p, np.linspace(0, 1, 11)[1:-1]), 0, 9)
    assert cal["counts"] == [int((bins == b).sum()) for b in cal["bins"]]
    ece = sum(c / len(y) * abs(f - m) for c, f, m in zip(cal["counts"], cal["fraction_positive"], cal["mean_predicted"]))
    assert math.isclose(cal["ece"], ece, abs_tol=1e-12)
    classes = e["classes"]
    assert e["confusion_matrix"] == skm.confusion_matrix(df["y_true"], df["y_pred"], labels=classes).tolist()
    P, R, F, S = skm.precision_recall_fscore_support(df["y_true"], df["y_pred"], labels=classes, zero_division=0)
    for i, c in enumerate(e["per_class"]):
        assert (c["class"], c["support"]) == (classes[i], S[i])
        assert math.isclose(c["precision"], P[i]) and math.isclose(c["recall"], R[i]) and math.isclose(c["f1"], F[i])
    assert math.isclose(e["log_loss"], skm.log_loss(df["y_true"], df[[f"p_{c}" for c in classes]].to_numpy(), labels=classes))
    # Worst mistakes: wrong, least probability on the true class first, with their features from the data.
    assert e["n_wrong"] == int((df["y_true"] != df["y_pred"]).sum())
    w = e["worst"]
    assert w and all(x["y_true"] != x["y_pred"] for x in w)
    assert [x["p_true"] for x in w] == sorted(x["p_true"] for x in w)
    wrong = df[df["y_true"] != df["y_pred"]]
    p_true = np.where(wrong["y_true"] == pos, wrong[f"p_{pos}"], 1 - wrong[f"p_{pos}"])
    assert math.isclose(w[0]["p_true"], p_true.min(), abs_tol=1e-6)
    data = pd.read_csv(binary_run["config"]["csv_path"])
    assert math.isclose(w[0]["features"]["x1"], data.loc[w[0]["row"], "x1"])
    assert "app_id" not in w[0]["features"] and "default" not in w[0]["features"]
    assert {g["column"] for g in e["group_candidates"]} >= {"region", "age"}


def test_explain_multiclass_one_vs_rest(client, multiclass_run):
    e = client.get(f"/api/runs/{multiclass_run['id']}/explain").json()
    df = _preds(client, multiclass_run)
    classes = e["classes"]
    assert set(classes) == {"low", "mid", "high"} and set(e["roc"]) == set(classes)
    Pm = df[[f"p_{c}" for c in classes]].to_numpy()
    aucs = []
    for k, c in enumerate(classes):
        yc = (df["y_true"] == c).astype(int)
        assert math.isclose(e["roc"][c]["auc"], skm.roc_auc_score(yc, Pm[:, k]), abs_tol=1e-12)
        assert math.isclose(e["pr"][c]["average_precision"], skm.average_precision_score(yc, Pm[:, k]), abs_tol=1e-12)
        aucs.append(skm.roc_auc_score(yc, Pm[:, k]))
    assert math.isclose(e["roc_macro_auc"], np.mean(aucs))
    onehot = (df["y_true"].to_numpy()[:, None] == np.array(classes)[None, :]).astype(float)
    assert math.isclose(e["calibration"]["brier"], float(np.mean(((Pm - onehot) ** 2).sum(axis=1))), abs_tol=1e-12)
    assert e["calibration"]["kind"] == "top_label"
    assert math.isclose(e["accuracy"], skm.accuracy_score(df["y_true"], df["y_pred"]))
    assert client.get(f"/api/runs/{multiclass_run['id']}/threshold").status_code == 400


def test_explain_regression_matches_sklearn(client, regression_run):
    e = client.get(f"/api/runs/{regression_run['id']}/explain").json()
    df = _preds(client, regression_run)
    y, yhat = df["y_true"].to_numpy(), df["y_pred"].to_numpy()
    r = y - yhat
    assert e["kind"] == "regression" and e["n"] == len(df)
    assert math.isclose(e["rmse"], math.sqrt(skm.mean_squared_error(y, yhat)), rel_tol=1e-12)
    assert math.isclose(e["rmse"], regression_run["holdout"]["metrics"]["root_mean_squared_error"], rel_tol=1e-6)
    assert math.isclose(e["mae"], skm.mean_absolute_error(y, yhat), rel_tol=1e-12)
    assert math.isclose(e["r2"], skm.r2_score(y, yhat), rel_tol=1e-12)
    assert math.isclose(e["mean_residual"], r.mean(), abs_tol=1e-9) and math.isclose(e["sd_residual"], r.std(ddof=1))
    counts, edges = np.histogram(r, bins=30)
    assert e["histogram"]["counts"] == counts.tolist() and np.allclose(e["histogram"]["edges"], edges)
    assert np.allclose(sorted(e["points"]["residual"]), sorted(r))
    q = np.quantile(r, [0.05, 0.5, 0.95])
    assert np.allclose([e["quantiles"]["q05"], e["quantiles"]["q50"], e["quantiles"]["q95"]], q)
    worst = e["worst"]
    assert [w["abs_error"] for w in worst] == sorted(np.abs(r))[::-1][:len(worst)]
    assert all(math.isclose(w["residual"], w["y_true"] - w["y_pred"], abs_tol=1e-9) for w in worst)


# --------------------------------------------------------------------------- fairness


def test_fairness_binary_matches_fairlearn_and_pandas(client, binary_run):
    from fairlearn.metrics import MetricFrame, demographic_parity_difference, equalized_odds_difference, selection_rate

    rid = binary_run["id"]
    f = client.get(f"/api/runs/{rid}/fairness", params={"group": "region"}).json()
    run = client.get(f"/api/runs/{rid}").json()
    t = run["threshold"]["value"]
    assert f["threshold"] == t and f["binning"] == "values"
    df, y, p = _yp(client, run)
    yhat = (p > t).astype(int)
    g = pd.read_csv(run["config"]["csv_path"])["region"].iloc[df["row"].to_numpy()].to_numpy()
    # Independently, with pandas: per-group selection rate, TPR and FPR, and the gaps.
    frame = pd.DataFrame({"g": g, "y": y, "yhat": yhat})
    sel = frame.groupby("g")["yhat"].mean()
    tpr = frame[frame.y == 1].groupby("g")["yhat"].mean()
    fpr = frame[frame.y == 0].groupby("g")["yhat"].mean()
    by = {row["group"]: row for row in f["by_group"]}
    assert set(by) == set(sel.index)
    for k in sel.index:
        assert by[k]["count"] == int((g == k).sum())
        assert math.isclose(by[k]["selection_rate"], sel[k], abs_tol=1e-12)
        assert math.isclose(by[k]["tpr"], tpr[k], abs_tol=1e-12) and math.isclose(by[k]["fpr"], fpr[k], abs_tol=1e-12)
    assert math.isclose(f["gaps"]["demographic_parity_difference"], sel.max() - sel.min(), abs_tol=1e-12)
    assert math.isclose(f["gaps"]["equalized_odds_difference"], max(tpr.max() - tpr.min(), fpr.max() - fpr.min()), abs_tol=1e-12)
    assert math.isclose(f["gaps"]["demographic_parity_difference"], demographic_parity_difference(y, yhat, sensitive_features=g))
    assert math.isclose(f["gaps"]["equalized_odds_difference"], equalized_odds_difference(y, yhat, sensitive_features=g))
    mf = MetricFrame(metrics=selection_rate, y_true=y, y_pred=yhat, sensitive_features=g)
    assert math.isclose(f["overall"]["selection_rate"], mf.overall)
    # A different threshold moves the selection rates.
    f2 = client.get(f"/api/runs/{rid}/fairness", params={"group": "region", "t": 0.05}).json()
    assert f2["overall"]["selection_rate"] == pytest.approx(float((p > 0.05).mean()))


def test_fairness_numeric_quartiles_and_regression(client, binary_run, regression_run):
    f = client.get(f"/api/runs/{binary_run['id']}/fairness", params={"group": "age"}).json()
    assert f["binning"] == "quartiles" and 2 <= len(f["by_group"]) <= 4
    assert sum(r["count"] for r in f["by_group"]) == f["n"]
    fr = client.get(f"/api/runs/{regression_run['id']}/fairness", params={"group": "segment"}).json()
    df = _preds(client, regression_run)
    run = regression_run
    seg = pd.read_csv(run["config"]["csv_path"])["segment"].iloc[df["row"].to_numpy()].to_numpy()
    mae = pd.Series(np.abs(df["y_true"] - df["y_pred"]).to_numpy()).groupby(seg).mean()
    for row in fr["by_group"]:
        assert math.isclose(row["mae"], mae[row["group"]], rel_tol=1e-12)
    assert math.isclose(fr["gaps"]["mae_difference"], mae.max() - mae.min(), rel_tol=1e-12)
    assert client.get(f"/api/runs/{binary_run['id']}/fairness", params={"group": "nope"}).status_code == 400
    assert client.get(f"/api/runs/{binary_run['id']}/fairness", params={"group": "default"}).status_code == 400


# --------------------------------------------------------------------------- SHAP


def _background(run, features: list[str]) -> pd.DataFrame:
    from sklearn.utils import shuffle

    data = pd.read_csv(run["config"]["csv_path"])
    splits = json.loads(open(run["config"]["splits_path"]).read())
    is_h = np.zeros(len(data), dtype=bool)
    is_h[np.asarray(splits["holdout"], dtype=int)] = True
    train = data.loc[~is_h & data[run["label"]].notna(), features]
    return shuffle(train, n_samples=min(50, len(train)), random_state=0)  # what shap.sample(train, 50, 0) does


def test_shap_waterfall_adds_up(client, binary_run):
    run = client.get(f"/api/runs/{binary_run['id']}").json()
    df = _preds(client, run)
    row = int(df["row"].iloc[0])
    r = client.get(f"/api/runs/{run['id']}/shap", params={"row": row})
    assert r.status_code == 200, r.text
    s = r.json()
    pos = str(run["positive_class"])
    assert s["target"] == pos and s["output"] == "probability" and s["background_size"] == 50
    phis = [c["phi"] for c in s["contributions"]]
    assert {c["feature"] for c in s["contributions"]} == set(run["features"])
    assert math.isclose(s["fx"], s["base_value"] + sum(phis), abs_tol=1e-9)
    assert [abs(v) for v in phis] == sorted((abs(v) for v in phis), reverse=True)
    # f(x) is the model's probability for this row, as /predict gives it.
    data = pd.read_csv(run["config"]["csv_path"])
    x = json.loads(data.loc[[row], run["features"]].to_json(orient="records"))
    pr = client.post(f"/api/runs/{run['id']}/predict", json={"rows": x}).json()
    assert math.isclose(s["fx"], pr["probabilities"][0][pos], abs_tol=1e-6)
    assert math.isclose(s["fx"], df[f"p_{pos}"].iloc[0], abs_tol=1e-5)
    # φ₀ is the mean prediction over the background sample.
    bg = _background(run, run["features"])
    pb = client.post(f"/api/runs/{run['id']}/predict", json={"rows": json.loads(bg.to_json(orient="records"))}).json()
    assert math.isclose(s["base_value"], float(np.mean([q[pos] for q in pb["probabilities"]])), abs_tol=1e-6)
    # Feature values shown are the row's.
    vals = {c["feature"]: c["value"] for c in s["contributions"]}
    assert math.isclose(vals["x1"], data.loc[row, "x1"]) and vals["region"] == data.loc[row, "region"]
    # Cached: same numbers, flagged.
    again = client.get(f"/api/runs/{run['id']}/shap", params={"row": row}).json()
    assert again["cached"] is True and again["contributions"] == s["contributions"]
    # A training row is refused; so is an unknown class.
    train_row = int(next(i for i in range(len(data)) if i not in set(df["row"])))
    assert client.get(f"/api/runs/{run['id']}/shap", params={"row": train_row}).status_code == 400
    assert client.get(f"/api/runs/{run['id']}/shap", params={"row": row, "target": "Maybe"}).status_code == 400


def test_shap_regression_and_multiclass(client, regression_run, multiclass_run):
    df = _preds(client, regression_run)
    row = int(df["row"].iloc[3])
    s = client.get(f"/api/runs/{regression_run['id']}/shap", params={"row": row}).json()
    assert s["output"] == "prediction" and s["target"] is None
    assert math.isclose(s["fx"], s["base_value"] + sum(c["phi"] for c in s["contributions"]), rel_tol=1e-9, abs_tol=1e-6)
    assert math.isclose(s["fx"], df["y_pred"].iloc[3], rel_tol=1e-6)
    dm = _preds(client, multiclass_run)
    rowm = int(dm["row"].iloc[0])
    sm = client.get(f"/api/runs/{multiclass_run['id']}/shap", params={"row": rowm}).json()
    assert sm["target"] == dm["y_pred"].iloc[0]  # default: the predicted class
    assert math.isclose(sm["fx"], dm[f"p_{sm['target']}"].iloc[0], abs_tol=1e-5)
    other = next(c for c in sm["classes"] if c != sm["target"])
    so = client.get(f"/api/runs/{multiclass_run['id']}/shap", params={"row": rowm, "target": other}).json()
    assert math.isclose(so["fx"], dm[f"p_{other}"].iloc[0], abs_tol=1e-5)
    assert math.isclose(so["fx"], so["base_value"] + sum(c["phi"] for c in so["contributions"]), abs_tol=1e-9)


# --------------------------------------------------------------------------- importance, status


def test_importance_has_t_intervals(client, binary_run):
    from scipy import stats

    r = client.get(f"/api/runs/{binary_run['id']}/feature-importance")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["meta"]["num_shuffle_sets"] == 5
    for f in body["features"]:
        assert f["n"] == 5
        half = stats.t.ppf(0.975, 4) * f["stddev"] / math.sqrt(5)
        assert math.isclose(f["ci_low"], f["importance"] - half, rel_tol=1e-9, abs_tol=1e-12)
        assert math.isclose(f["ci_high"], f["importance"] + half, rel_tol=1e-9, abs_tol=1e-12)
    top = max(body["features"], key=lambda f: f["importance"])["feature"]
    assert top in ("x1", "x2")
    tighter = client.get(f"/api/runs/{binary_run['id']}/feature-importance", params={"shuffles": 8}).json()
    assert tighter["meta"]["num_shuffle_sets"] == 8 and all(f["n"] == 8 for f in tighter["features"])


def test_status_reports_machine_and_active_run(client, imb_ds):
    s = client.get("/api/status").json()
    assert s["cpu_count"] == os.cpu_count() and s["version"] == client.get("/api/health").json()["version"]
    assert s["engine"]["name"] == "AutoGluon" and s["engine"]["version"]
    assert s["runs"] == len(client.get("/api/runs").json()["runs"])
    run = start_run(client, dataset_id=imb_ds["id"], time_limit=60, feature_importance=False,
                    fit_kwargs={"hyperparameters": {"GBM": {"num_boost_round": 400}, "LR": {}, "KNN": {}}})
    seen = None
    import time

    deadline = time.time() + 240
    while time.time() < deadline:
        st = client.get("/api/status").json()
        if st["active"] and st["active"]["id"] == run["id"]:
            seen = st["active"]
            break
        if client.get(f"/api/runs/{run['id']}").json()["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.2)
    assert seen is not None, "the run never showed as active"
    assert seen["time_limit"] == 60 and seen["models_started"] >= seen["models_finished"] >= 0
    wait_for_run(client, run["id"], timeout=300)
    assert client.get("/api/status").json()["active"] is None


# --------------------------------------------------------------------------- the seeded credit data (slow)


@pytest.mark.slow
def test_credit_default_calibrated_threshold_is_sensible(client, credit_ds):
    """S3 saw holdout F1 0.106 and balanced accuracy 0.528 at t = 0.5 on 8% positives. With the wizard's default
    (calibrate the threshold for F1 after the fit) both are sensible, and better than at 0.5."""
    run = _fit(client, credit_ds["id"], eval_metric="roc_auc", calibrate_threshold="f1",
               fit_kwargs={"hyperparameters": {"GBM": {}, "CAT": {"iterations": 300}, "LR": {}}})
    t = run["threshold"]
    m = run["holdout"]["metrics"]
    assert t["source"] == "calibrated" and 0.05 < t["value"] < 0.5
    assert m["f1"] >= 0.30 and m["balanced_accuracy"] >= 0.65, m
    assert m["f1"] > t["holdout_at_default"]["f1"] + 0.1
    assert m["balanced_accuracy"] > t["holdout_at_default"]["balanced_accuracy"] + 0.08
    assert m["roc_auc"] >= 0.75
