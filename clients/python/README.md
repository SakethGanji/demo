# ensemble-client

A small Python client for the Project ENSEMBLE API: datasets, AutoGluon runs (start, wait, stream events),
leaderboards, predictions, Vela's findings, the model registry and the export of a run as a standalone script.
It needs only `httpx` (and `pandas` if you want leaderboards as DataFrames).

```bash
pip install -e clients/python            # from the repo root; `make setup` does this into backend/.venv
```

## A notebook session

```python
from ensemble_client import Ensemble

ens = Ensemble("http://127.0.0.1:8000")          # `make dev` serves the API on :8000
ens.health()["engine"]                            # {'name': 'AutoGluon', 'version': '1.6.3', …}

# 1 · data: a sample, a CSV path, or a DataFrame
ds = ens.datasets.get("credit-default")
# ds = ens.datasets.upload(df, name="applicants.csv", target="defaulted")

# 2 · check a config before it trains: errors, the resolved fit call, AutoGluon's own bagging/stacking choice, T̂
check = ens.gym.validate(ds.id, presets="good_quality", time_limit=120, eval_metric="roc_auc",
                         fit_kwargs={"num_bag_folds": 5, "hyperparameters": {"GBM": {"learning_rate": 0.03}, "CAT": {}}})
print(check["call"]); print(check["effective"], round(check["estimate"]["seconds"]), "s")

# 3 · train (every TabularPredictor.fit argument passes through fit_kwargs) and follow it live
run = ens.runs.start(ds, eval_metric="roc_auc", time_limit=120, calibrate_threshold="f1",
                     fit_kwargs={"num_bag_folds": 5, "hyperparameters": {"GBM": {"learning_rate": 0.03}, "CAT": {}}})
for ev in run.events():                           # server-sent events, ends with the run
    if ev["type"] == "model_finished":
        print(f"{ev['model']:<28} s_val = {ev['val_score_natural']:.4f}")
run.wait()
run.leaderboard()[["model", "score_val", "score_test", "fit_time", "stack_level"]]

# 4 · predict
run.predict([{"income": 52_000, "utilization": 0.41, "employment": "salaried"}])

# 5 · Vela: findings computed from the run, each with one patch you can train
for f in run.vela()["findings"]:
    print(f["title"], "·", f["text"])
patched = ens.runs.start(**run.vela()["findings"][0]["patch"]["body"])

# 6 · leave: a plain script that reproduces the run with AutoGluon alone
run.export("reproduce.py")                        # python reproduce.py  → same leaderboard, checked

# 7 · register, approve (four eyes), promote, score
v = run.register(created_by="analyst", refit_full=True).wait()
v.review("approve", reviewer="validator", comment="checked")
v.transition("Production", actor="validator")
v.predict({"income": 52_000, "utilization": 0.41, "employment": "salaried"}, explain=True)
```

Errors from the API raise `ensemble_client.ApiError` with `.status` and `.detail` (for example a `fit_kwargs` key
that `TabularPredictor.fit` does not accept: `400 … TabularPredictor.fit has no argument 'num_bag_fold'. Did you mean
'num_bag_folds'?`).
