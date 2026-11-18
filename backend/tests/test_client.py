"""The notebook client (clients/python, installed by `make setup`) against the live API: datasets, a run started,
followed by its event stream and waited for, the leaderboard as a DataFrame, predict, config round trip, Vela,
export, register → approve → promote → predict with a version, and API errors."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

ensemble_client = pytest.importorskip("ensemble_client")
from ensemble_client import ApiError, Ensemble  # noqa: E402

HP = {"GBM": {"num_boost_round": 20}, "LR": {}}


@pytest.fixture(scope="module")
def ens(api_server):
    with Ensemble(api_server.url) as e:
        yield e


def test_client_end_to_end(ens):
    assert ens.health()["engine"]["name"] == "AutoGluon"
    assert {d["id"] for d in ens.datasets.list()} >= {"credit-default", "loan-amount"}
    assert any(f["key"] == "GBM" for f in ens.models())

    rng = np.random.default_rng(1)
    n = 400
    df = pd.DataFrame({"a": rng.normal(size=n), "b": rng.choice(["x", "y"], n)})
    df["label"] = np.where(df["a"] + (df["b"] == "x") * 0.5 + rng.normal(scale=0.5, size=n) > 0.3, "pos", "neg")
    ds = ens.datasets.upload(df, name="client.csv", target="label")
    assert ds.target == "label" and [c["name"] for c in ds.columns] == ["a", "b", "label"]

    check = ens.gym.validate(ds.id, time_limit=60, fit_kwargs={"hyperparameters": HP})
    assert check["ok"] and "hyperparameters=" in check["call"]

    run = ens.runs.start(ds, time_limit=60, feature_importance=False, fit_kwargs={"hyperparameters": HP})
    assert run.data["config"]["source"] == "client"
    types = [e["type"] for e in run.events()]
    assert types[0] == "run_queued" and types[-1] == "run_succeeded" and "model_finished" in types
    run.wait()
    lb = run.leaderboard()
    assert isinstance(lb, pd.DataFrame) and set(lb["model"]) == {r["model"] for r in run.data["leaderboard"]}
    assert lb["score_test"].notna().all()

    p = run.predict(df.drop(columns=["label"]).head(3))
    assert len(p["predictions"]) == 3 and set(p["predictions"]) <= {"pos", "neg"}

    body = run.config()
    assert body["fit_kwargs"] == {"hyperparameters": HP}
    text = run.export()
    assert text.startswith("#!/usr/bin/env python") and run.id in text
    assert isinstance(run.vela()["findings"], list)
    assert ens.runs.get(run.id).status == "succeeded" and any(r.id == run.id for r in ens.runs.list(ds.id))

    v = run.register(created_by="Analyst A").wait()
    assert v.data["status"] == "ready"
    v.review("approve", reviewer="Validator B", comment="ok")
    v.transition("Production", actor="Validator B")
    assert v.refresh().data["stage"] == "Production"
    out = v.predict({"a": 1.2, "b": "x"})
    assert out["prediction"] in ("pos", "neg")
    assert any(x.id == v.id for x in ens.registry.versions())


def test_client_errors(ens):
    with pytest.raises(ApiError) as e:
        ens.runs.start("credit-default", fit_kwargs={"num_bag_fold": 2})
    assert e.value.status == 400 and "num_bag_folds" in str(e.value.detail)
    with pytest.raises(ApiError) as e:
        ens.runs.get("run_nope")
    assert e.value.status == 404
