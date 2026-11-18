"""S5: registry and use, over HTTP the way the UI calls it.

register (refit_full, infer_limit, clone_for_deployment) → stages and four-eyes review with an audit log → MLflow's
own record → Try It against the predictor loaded independently → batch scoring (the downloaded CSV against an
independent predict_proba) → PSI drift recomputed independently with pandas → the model card (JSON, Markdown, HTML).

Tiny fits on a small upload (fast tier)."""

from __future__ import annotations

import hashlib
import io
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from .conftest import start_run, wait_for_run
from .test_understand import _imbalanced, _upload

HP = {"GBM": {"num_boost_round": 30}, "LR": {}}
DEV, REVIEWER, OPS = "Dana Dev", "Riley Review", "Ops Batch"


def wait_version(client, vid: str, timeout: float = 240) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = client.get(f"/api/registry/versions/{vid}").json()
        if v["status"] != "building":
            return v
        time.sleep(0.4)
    raise TimeoutError(f"version {vid} still building")


def wait_batch(client, bid: str, timeout: float = 180) -> dict:
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        b = client.get(f"/api/registry/batches/{bid}").json()
        seen.append(b["progress"])
        if b["status"] in ("succeeded", "failed"):
            b["_progress_seen"] = seen
            return b
        time.sleep(0.15)
    raise TimeoutError(f"batch {bid} still {b['status']}")


@pytest.fixture(scope="module")
def reg_run(client):
    ds = _upload(client, "registry_default.csv", _imbalanced(n=900, seed=11), target="default")
    run = start_run(client, dataset_id=ds["id"], time_limit=60, feature_importance=True,
                    fit_kwargs={"hyperparameters": HP})
    out = wait_for_run(client, run["id"], timeout=300)
    assert out["status"] == "succeeded", out["error"]
    return ds, out


@pytest.fixture(scope="module")
def version(client, reg_run):
    _, run = reg_run
    r = client.post(f"/api/runs/{run['id']}/register", json={
        "created_by": DEV, "refit_full": True, "clone_for_deployment": True, "infer_limit": 0.01,
        "description": "first candidate"})
    assert r.status_code == 202, r.text
    v = r.json()
    assert v["status"] == "building" and v["stage"] == "None" and v["approval"] == "pending" and v["version"] == 1
    v = wait_version(client, v["id"])
    assert v["status"] == "ready", (v["error"], v["progress"])
    return v


def _sha_dir(path: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in path.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(path)).encode())
        h.update(b"\0")
        h.update(f.read_bytes())
    return h.hexdigest()


# --------------------------------------------------------------------------- MLflow tracking of runs


def test_run_is_tracked_in_mlflow(client, reg_run, data_dir):
    from mlflow import MlflowClient

    _, run = reg_run
    ml = run["mlflow"]
    uri = f"sqlite:///{data_dir / 'mlflow' / 'mlflow.db'}"
    r = MlflowClient(uri).get_run(ml["run_id"])
    assert r.data.tags["ensemble.run_id"] == run["id"]
    assert r.data.tags["ensemble.predictor_path"] == run["predictor_path"]
    assert r.data.params["fit.hyperparameters.GBM.num_boost_round"] == "30"
    assert r.data.params["fit.presets"] == "medium_quality"
    for k, v in run["holdout"]["metrics"].items():
        assert r.data.metrics[f"holdout_{k}"] == pytest.approx(v)
    arts = {a.path for a in MlflowClient(uri).list_artifacts(ml["run_id"], "ensemble")}
    assert {"ensemble/resolved_fit.json", "ensemble/leaderboard.json", "ensemble/config.json",
            "ensemble/holdout_predictions.csv", "ensemble/predictor_path.txt"} <= arts


# --------------------------------------------------------------------------- register


def test_register_refusals(client, reg_run):
    _, run = reg_run
    assert client.post("/api/runs/run_nope/register", json={"created_by": DEV}).status_code == 404
    r = client.post(f"/api/runs/{run['id']}/register", json={"created_by": "  "})
    assert r.status_code == 400 and "required" in r.json()["detail"]
    assert client.post(f"/api/runs/{run['id']}/register", json={"created_by": DEV, "model_name": "bad name!"}).status_code == 400
    assert client.post(f"/api/runs/{run['id']}/register", json={"created_by": DEV, "infer_limit": -1}).status_code == 422
    assert client.get("/api/registry/versions/mv_nope").status_code == 404


def test_latency_profile(client, reg_run):
    _, run = reg_run
    lp = client.get(f"/api/runs/{run['id']}/latency").json()
    names = {m["model"] for m in lp["models"]}
    assert {"LightGBM", "LinearModel", run["best_model"]} <= names
    for m in lp["models"]:
        assert m["us_per_row"] > 0 and m["size_bytes"] > 0 and m["n_models"] >= 1
    ens = next(m for m in lp["models"] if m["model"] == run["best_model"])
    assert ens["n_models"] == 3 and ens["best"]
    # The ensemble needs its base models: it is at least as large as any one of them.
    assert ens["size_bytes"] >= max(m["size_bytes"] for m in lp["models"] if m["model"] != ens["model"])
    assert client.get(f"/api/runs/{run['id']}/latency").json()["cached"] is True


def test_version_built(client, reg_run, version, data_dir):
    from autogluon.tabular import TabularPredictor

    _, run = reg_run
    steps = {s["name"]: s for s in version["progress"]["steps"]}
    assert [s["name"] for s in version["progress"]["steps"]] == ["clone", "refit_full", "infer_limit", "deploy_copy",
                                                                 "measure", "hash", "mlflow"]
    assert steps["refit_full"]["status"] == "done"
    assert steps["infer_limit"]["status"] == "done"
    res = version["result"]
    v, src = res["version"], res["source"]
    # refit_full + infer_limit: every candidate was refit, and the chosen one is a _FULL model within the budget
    li = res["infer_limit"]
    assert all(c["model"].endswith("_FULL") for c in li["candidates"])
    ok = [c for c in li["candidates"] if c["time_used_us"] <= 0.01 * 1e6]
    assert li["met"] and li["chosen"] == max(ok, key=lambda c: c["score_val"])["model"]
    assert v["best_model"] == li["chosen"]
    # The version's predictor is a plain AutoGluon predictor in its own folder; the run's is untouched.
    path = Path(version["path"])
    assert path.is_dir() and str(path).startswith(str(data_dir / "registry"))
    p = TabularPredictor.load(str(path), verbosity=0)
    assert p.model_best == v["best_model"]
    assert set(p.model_names()) == set(v["models"])  # clone_for_deployment kept only what the best model needs
    assert TabularPredictor.load(run["predictor_path"], verbosity=0).model_best == run["best_model"]
    # Measured, not estimated
    assert v["size_bytes"] == sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    assert v["size_bytes"] < src["size_bytes"]
    for lat in (v["latency"], src["latency"]):
        assert 0 < lat["us_per_row_p50"] <= lat["us_per_row_p95"]
        assert 0 < lat["single_ms_p50"] <= lat["single_ms_p95"]
        assert lat["batch_repeats"] >= 3 and lat["single_calls"] >= 10
    assert res["tradeoff"]["size_ratio"] == pytest.approx(v["size_bytes"] / src["size_bytes"])
    # Hashes recomputed independently
    assert version["model_hash"] == res["hashes"]["model_sha256"] == _sha_dir(path)
    ds_csv = data_dir / "datasets" / run["dataset_id"] / "data.csv"
    assert res["hashes"]["data_sha256"] == hashlib.sha256(ds_csv.read_bytes()).hexdigest()
    # Holdout metrics with bootstrap intervals that contain the point estimate
    ci = v["holdout_ci"]["metrics"]
    assert {"accuracy", "roc_auc", "f1", "brier", "log_loss"} <= set(ci)
    for m in ci.values():
        assert m["ci_low"] <= m["value"] <= m["ci_high"]
    hp = pd.read_csv(path.parent / "holdout_predictions.csv")
    from sklearn.metrics import roc_auc_score

    assert ci["roc_auc"]["value"] == pytest.approx(roc_auc_score(hp["y_true"] == "Yes", hp["p_Yes"]))
    assert ci["accuracy"]["value"] == pytest.approx((hp["y_true"] == hp["y_pred"]).mean())


def test_version_in_mlflow_registry(client, version, data_dir):
    from mlflow import MlflowClient

    ml = version["result"]["mlflow"]
    c = MlflowClient(registry_uri=f"sqlite:///{data_dir / 'mlflow' / 'mlflow.db'}",
                     tracking_uri=f"sqlite:///{data_dir / 'mlflow' / 'mlflow.db'}")
    mv = c.get_model_version(ml["name"], str(ml["version"]))
    assert mv.source == version["path"]
    assert mv.tags["model_sha256"] == version["model_hash"]
    assert mv.tags["ensemble.version_id"] == version["id"]
    assert mv.run_id == client.get(f"/api/runs/{version['run_id']}").json()["mlflow"]["run_id"]
    got = client.get(f"/api/registry/versions/{version['id']}/mlflow").json()
    assert got["version"] == ml["version"] and got["run"]["params"]["label"] == "default"


# --------------------------------------------------------------------------- stages, reviews and the audit log


def test_stages_reviews_and_audit(client, reg_run, version, data_dir):
    from mlflow import MlflowClient

    vid = version["id"]
    tr = lambda stage, actor=REVIEWER, comment="": client.post(f"/api/registry/versions/{vid}/transition",  # noqa: E731
                                                                json={"stage": stage, "actor": actor, "comment": comment})
    rv = lambda decision, reviewer=REVIEWER, comment="": client.post(f"/api/registry/versions/{vid}/review",  # noqa: E731
                                                                     json={"decision": decision, "reviewer": reviewer, "comment": comment})
    r = tr("Production")
    assert r.status_code == 409 and "approved review" in r.json()["detail"]
    assert tr("Staging", actor="").status_code == 400
    assert tr("Staging", actor=DEV, comment="for review").status_code == 200
    assert tr("Staging").status_code == 409  # already there
    r = rv("approve", reviewer=DEV.upper())
    assert r.status_code == 409 and "Four-eyes" in r.json()["detail"]
    assert rv("reject").status_code == 400  # a rejection needs a reason
    assert client.post(f"/api/registry/versions/{vid}/review", json={"decision": "maybe", "reviewer": REVIEWER}).status_code == 422
    r = rv("approve", comment="holdout AUC CI clears 0.75; card complete")
    assert r.status_code == 200, r.text
    assert r.json()["version"]["approval"] == "approved"
    assert rv("approve").status_code == 409  # one review per version
    r = tr("Production", comment="go live")
    assert r.status_code == 200 and r.json()["version"]["stage"] == "Production"
    assert r.json()["mlflow"]["stage"] == "Production" and "production" in r.json()["mlflow"]["aliases"]

    log = client.get("/api/registry/audit", params={"version_id": vid}).json()["entries"]
    acts = [(e["action"], e["actor"], e["from_stage"], e["to_stage"]) for e in reversed(log)]
    assert acts == [("registered", DEV, None, "None"), ("stage_changed", DEV, "None", "Staging"),
                    ("approved", REVIEWER, None, None), ("stage_changed", REVIEWER, "Staging", "Production")]
    assert all(e["model_hash"] == version["model_hash"] for e in log)
    assert log[1]["comment"] == "holdout AUC CI clears 0.75; card complete"
    uri = f"sqlite:///{data_dir / 'mlflow' / 'mlflow.db'}"
    mv = MlflowClient(tracking_uri=uri, registry_uri=uri).get_model_version(version["model_name"], "1")
    assert mv.current_stage == "Production" and mv.tags["approval"] == "approved" and mv.tags["reviewer"] == REVIEWER


def test_promotion_archives_previous_and_rejection(client, reg_run, version):
    _, run = reg_run
    # make sure v1 is in Production (the previous test may not have run first under -k)
    v1 = client.get(f"/api/registry/versions/{version['id']}").json()
    if v1["stage"] != "Production":
        client.post(f"/api/registry/versions/{version['id']}/review", json={"decision": "approve", "reviewer": REVIEWER})
        client.post(f"/api/registry/versions/{version['id']}/transition", json={"stage": "Production", "actor": REVIEWER})
    v2 = wait_version(client, client.post(f"/api/runs/{run['id']}/register", json={"created_by": DEV}).json()["id"])
    assert v2["status"] == "ready" and v2["version"] == 2
    assert {s["name"]: s["status"] for s in v2["progress"]["steps"]}["refit_full"] == "skipped"
    assert v2["result"]["version"]["best_model"] == run["best_model"]  # no refit, no budget: the run's own best
    assert client.post(f"/api/registry/versions/{v2['id']}/review", json={"decision": "approve", "reviewer": REVIEWER}).status_code == 200
    r = client.post(f"/api/registry/versions/{v2['id']}/transition", json={"stage": "Production", "actor": REVIEWER})
    assert r.status_code == 200 and r.json()["archived"] == [version["id"]]
    assert client.get(f"/api/registry/versions/{version['id']}").json()["stage"] == "Archived"
    e = client.get("/api/registry/audit", params={"version_id": version["id"]}).json()["entries"][0]
    assert (e["action"], e["from_stage"], e["to_stage"], e["comment"]) == ("stage_changed", "Production", "Archived", "superseded by v2")
    # v3: rejected, and then it cannot go live
    v3 = wait_version(client, client.post(f"/api/runs/{run['id']}/register", json={"created_by": DEV}).json()["id"])
    assert client.post(f"/api/registry/versions/{v3['id']}/transition", json={"stage": "Staging", "actor": DEV}).status_code == 200
    r = client.post(f"/api/registry/versions/{v3['id']}/review", json={"decision": "reject", "reviewer": REVIEWER, "comment": "no fairness review"})
    assert r.status_code == 200 and r.json()["version"]["stage"] == "Archived" and r.json()["version"]["approval"] == "rejected"
    assert client.post(f"/api/registry/versions/{v3['id']}/transition", json={"stage": "Staging", "actor": DEV}).status_code == 409
    reg = client.get("/api/registry").json()
    m = next(m for m in reg["models"] if m["name"] == version["model_name"])
    assert m["versions"] >= 3 and m["production"] == 2
    st = client.get("/api/status").json()["registry"]
    assert st["stages"]["Production"] >= 1 and st["approvals"]["rejected"] >= 1
    summ = client.get("/api/registry/summary").json()
    assert summ["audit"][0]["action"] in ("rejected", "stage_changed", "approved", "registered", "batch_scored", "card_edited")


# --------------------------------------------------------------------------- Try It


def test_try_it(client, reg_run, version):
    from autogluon.tabular import TabularPredictor

    row = {"x1": 1.3, "x2": "-0.4", "region": "south", "age": 41}  # a number as text, the way a form may send it
    r = client.post(f"/api/registry/versions/{version['id']}/predict", json={"row": row})
    assert r.status_code == 200, r.text
    out = r.json()
    p = TabularPredictor.load(version["path"], verbosity=0)
    X = pd.DataFrame([{"x1": 1.3, "x2": -0.4, "region": "south", "age": 41}])
    proba = p.predict_proba(X)
    assert out["probabilities"]["Yes"] == pytest.approx(float(proba["Yes"].iloc[0]), abs=1e-6)
    assert out["prediction"] == p.predict(X).iloc[0]
    assert sum(out["probabilities"].values()) == pytest.approx(1.0)
    sh = out["shap"]
    assert sh["fx"] == pytest.approx(out["probabilities"]["Yes"], abs=1e-6)
    assert sh["base_value"] + sum(c["phi"] for c in sh["contributions"]) == pytest.approx(sh["fx"], abs=1e-6)
    phis = [abs(c["phi"]) for c in sh["reasons"]]
    assert phis == sorted(phis, reverse=True) and len(sh["reasons"]) == 4
    assert sh["reasons"][0]["feature"] == "x1"  # the strongest true signal at x1 = 1.3
    assert all(c["direction"] == ("raises" if c["phi"] > 0 else "lowers") for c in sh["reasons"])
    # missing columns are allowed and reported; no explanation when not asked
    r = client.post(f"/api/registry/versions/{version['id']}/predict", json={"row": {"x1": 0.1}, "explain": False}).json()
    assert r["missing_columns"] == ["x2", "region", "age"] and "shap" not in r


# --------------------------------------------------------------------------- batch scoring and drift


def _independent_psi(e: pd.Series, a: pd.Series, numeric: bool, eps: float = 1e-4) -> float:
    if numeric:
        edges = np.unique(np.quantile(e.astype(float), np.arange(1, 10) / 10))
        bins = np.concatenate([[-np.inf], edges, [np.inf]])
        ce = pd.cut(e.astype(float), bins, right=True).value_counts(sort=False).to_numpy()
        ca = pd.cut(a.astype(float), bins, right=True).value_counts(sort=False).to_numpy()
    else:
        cats = list(e.astype(str).value_counts().index[:19])
        ce = np.array([(e.astype(str) == c).sum() for c in cats] + [(~e.astype(str).isin(cats)).sum()])
        ca = np.array([(a.astype(str) == c).sum() for c in cats] + [(~a.astype(str).isin(cats)).sum()])
        if ce[-1] == 0 and ca[-1] == 0:
            ce, ca = ce[:-1], ca[:-1]
    pe = np.maximum(ce / ce.sum(), eps)
    pa = np.maximum(ca / ca.sum(), eps)
    return float(np.sum((pa - pe) * np.log(pa / pe)))


def test_batch_scoring_and_drift(client, reg_run, version, data_dir):
    from autogluon.tabular import TabularPredictor

    ds, run = reg_run
    full = pd.read_csv(data_dir / "datasets" / ds["id"] / "data.csv")
    import json

    splits = json.loads((data_dir / "datasets" / ds["id"] / "splits.json").read_text())
    train = full.drop(index=splits["holdout"]).dropna(subset=["default"])
    # A new month: x1 shifted by +0.9σ, region mix moved towards 'west'; x2 and age as before
    rng = np.random.default_rng(5)
    batch = full.sample(700, random_state=2).drop(columns=["default"]).reset_index(drop=True)
    batch["x1"] = batch["x1"] + 0.9
    batch.loc[rng.random(len(batch)) < 0.45, "region"] = "west"
    buf = io.StringIO()
    batch.to_csv(buf, index=False)
    assert client.post(f"/api/registry/versions/{version['id']}/batches",
                       files={"file": ("2026-11.csv", buf.getvalue().encode(), "text/csv")}, data={"created_by": ""}).status_code == 400
    r = client.post(f"/api/registry/versions/{version['id']}/batches",
                    files={"file": ("2026-11.csv", buf.getvalue().encode(), "text/csv")}, data={"created_by": OPS})
    assert r.status_code == 202, r.text
    b = wait_batch(client, r.json()["id"])
    assert b["status"] == "succeeded", b["error"]
    assert b["progress"] == 1.0 and b["n_rows"] == 700
    s = b["result"]["summary"]
    assert s["missing_columns"] == [] and set(s["extra_columns"]) == {"app_id"}
    assert sum(s["predicted"].values()) == 700

    # The downloaded CSV: the input plus the prediction and per-class probabilities, equal to predict_proba
    d = client.get(f"/api/registry/batches/{b['id']}/download")
    assert d.status_code == 200 and "attachment" in d.headers["content-disposition"]
    out = pd.read_csv(io.StringIO(d.text))
    assert list(out.columns) == list(batch.columns) + ["prediction", "p_No", "p_Yes"]
    p = TabularPredictor.load(version["path"], verbosity=0)
    proba = p.predict_proba(batch)
    assert np.allclose(out["p_Yes"], proba["Yes"], atol=2e-6)
    assert (out["prediction"] == p.predict(batch)).all()
    assert b["result"]["preview"][0]["prediction"] == out["prediction"].iloc[0]

    # PSI of every feature, recomputed independently from the training rows
    drift = {f["feature"]: f for f in b["result"]["drift"]["features"]}
    for col, numeric in (("x1", True), ("x2", True), ("age", True), ("region", False)):
        assert drift[col]["psi"] == pytest.approx(_independent_psi(train[col], batch[col], numeric), abs=1e-9), col
        f = drift[col]
        assert sum(x["expected_count"] for x in f["bins"]) == len(train)
        assert sum(x["actual_count"] for x in f["bins"]) == 700
        assert f["psi"] == pytest.approx(sum(x["term"] for x in f["bins"]))
    assert drift["x1"]["band"] == "shift" and drift["x1"]["psi"] > 0.25
    assert drift["region"]["band"] in ("watch", "shift")
    assert drift["x2"]["band"] == "stable" and drift["age"]["band"] == "stable"
    assert b["result"]["drift"]["counts"]["shift"] >= 1
    # The prediction's PSI against the version's own holdout predictions
    ref = pd.read_csv(Path(version["path"]).parent / "holdout_predictions.csv")
    pred = b["result"]["drift"]["prediction"]
    assert pred["psi"] == pytest.approx(_independent_psi(ref["p_Yes"], proba["Yes"], True), abs=1e-9)
    from scipy.stats import ks_2samp

    assert pred["ks"]["statistic"] == pytest.approx(ks_2samp(ref["p_Yes"], proba["Yes"]).statistic)
    assert s["mean_p"] > s["mean_p_reference"]  # x1 raises the risk, and x1 moved up

    lst = client.get(f"/api/registry/versions/{version['id']}/batches").json()["batches"]
    assert lst[0]["id"] == b["id"] and lst[0]["max_psi"] == pytest.approx(max(f["psi"] for f in drift.values() if f["psi"] is not None))
    e = client.get("/api/registry/audit", params={"version_id": version["id"]}).json()["entries"]
    assert any(x["action"] == "batch_scored" and x["actor"] == OPS for x in e)


def test_batch_refusals(client, version):
    r = client.post(f"/api/registry/versions/{version['id']}/batches",
                    files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")}, data={"created_by": OPS})
    b = wait_batch(client, r.json()["id"])
    assert b["status"] == "failed" and "None of the model's features" in b["error"]
    assert client.get(f"/api/registry/batches/{b['id']}/download").status_code == 409
    assert client.get("/api/registry/batches/batch_nope").status_code == 404


# --------------------------------------------------------------------------- the model card


def test_model_card(client, reg_run, version):
    ds, run = reg_run
    vid = version["id"]
    assert client.put(f"/api/registry/versions/{vid}/card", json={"actor": DEV, "fields": {"nope": "x"}}).status_code == 400
    r = client.put(f"/api/registry/versions/{vid}/card", json={"actor": DEV, "fields": {
        "purpose": "Flag likely defaults for manual review", "out_of_scope": "Automated declines"}})
    assert r.status_code == 200 and r.json()["changed"] == ["purpose", "out_of_scope"]
    client.get(f"/api/runs/{run['id']}/fairness", params={"group": "region"})  # stored for the card
    c = client.get(f"/api/registry/versions/{vid}/card").json()
    assert c["data"]["rows"] == 900 and c["data"]["sha256"] == version["result"]["hashes"]["data_sha256"]
    bal = {b["class"]: b for b in c["data"]["label_balance"]}
    assert bal["Yes"]["train"] + bal["No"]["train"] == run["data"]["train_rows"]
    assert "TabularPredictor(label=\"default\"" in c["training"]["call"] and "\"num_boost_round\": 30" in c["training"]["call"]
    assert c["validation"]["holdout_ci"]["metrics"]["roc_auc"]["ci_low"] is not None
    assert c["validation"]["threshold"]["value"] == 0.5
    assert c["importance"]["features"][0]["feature"] in ("x1", "x2", "region", "age")
    assert "region" in c["fairness"]["reports"]
    assert c["ensemble"]["weights"] and abs(sum(c["ensemble"]["weights"].values()) - 1) < 1e-6
    fields = {f["key"]: f["value"] for f in c["use"]["fields"]}
    assert fields["purpose"] == "Flag likely defaults for manual review"
    assert c["approval"]["audit"][0]["action"] == "registered"
    assert "does not replace independent model validation" in c["generated_note"]
    md = client.get(f"/api/registry/versions/{vid}/card", params={"format": "md", "download": True})
    assert md.headers["content-type"].startswith("text/markdown") and "attachment" in md.headers["content-disposition"]
    for h in ("## 1. Summary", "## 3. Data", "## 4. Development", "## 5. Validation", "## 6. Feature importance",
              "## 7. Fairness", "## 8. Deployment", "## 10. Approval", version["model_hash"], "Flag likely defaults"):
        assert h in md.text, h
    html = client.get(f"/api/registry/versions/{vid}/card", params={"format": "html"}).text
    assert html.startswith("<!doctype html>") and "<h2>5. Validation (holdout)</h2>" in html and "<table>" in html
    e = client.get("/api/registry/audit", params={"version_id": vid}).json()["entries"]
    assert any(x["action"] == "card_edited" and x["comment"] == "purpose, out_of_scope" for x in e)
