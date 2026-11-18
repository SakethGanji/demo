# Project ENSEMBLE

ENSEMBLE is an internal AutoML workbench for the bank, with **AutoGluon as the engine**. Anyone brings a table (a CSV
upload or one of the samples), says what to predict, picks a goal and a time budget, and watches AutoGluon train and
stack its models live: one string per model family, the stacked ensemble forming as ŷ(x) = Σₘ wₘ fₘ(x) with its real
weights. The result is a leaderboard on a fixed holdout no model ever saw, an explanation of the model (ROC/PR,
calibration, the decision threshold and its cost, permutation importance, Kernel SHAP for any row, the worst errors,
fairness by group), and a path to production: register a version with a generated model card, approve it under a
four-eyes rule, try it on one row, and batch-score a file with drift checks. Forecasting (`TimeSeriesPredictor`),
unsupervised anomaly detection (IsolationForest) and importing a predictor trained elsewhere go through the same runs,
registry and audit trail.

It gives technical users everything AutoGluon offers and never locks them in: the Training Gym exposes every
`TabularPredictor.fit` argument, validated by the server against the installed AutoGluon; every run is saved as a plain
AutoGluon predictor (`TabularPredictor.load(path)` works); any run exports as a standalone Python script that
reproduces its leaderboard; and a small Python client drives the same API from a notebook. It is one process to start
(FastAPI, SQLite, a data folder, MLflow as a library), with no Redis, Celery, Docker or sign-in. Each fit runs in its own
process and streams its progress as events. When the API is down the UI opens in **mock mode** on a scripted
simulation, labelled SIMULATED, so a demo always opens.

## What it does, by use case

| Use case | What you get |
| --- | --- |
| **Binary classification · fraud, default, churn** | Imbalance handled by default: ROC AUC (or average precision) as the metric for an imbalanced label and the decision threshold calibrated for F₁ with AutoGluon's `calibrate_decision_threshold`. Explain's Threshold tab moves t over the holdout with precision, recall, F₁, MCC, FPR, the 2×2 counts and the cost C(t) = c_FP·FP + c_FN·FN against the Bayes threshold. Fairness by any column (fairlearn: demographic parity, equalised odds). |
| **Multiclass, including text · complaint and ticket routing** | Free-text columns detected and given to AutoGluon's n-gram features, plus two custom AutoGluon models (TF-IDF → one-vs-rest logistic regression and TF-IDF → linear SVM) with their top words per class. Per-class recall and precision, one-vs-rest curves. Vela Autopilot audits the labels with cleanlab and asks you only about the rows it cannot decide. |
| **Regression · balances, loan amounts** | RMSE / MAE / R², residuals against ŷ with ±1.96 σ, the residual histogram with its normal fit, the largest errors with SHAP for each. |
| **Time-series forecasting · transaction volumes** | `TimeSeriesPredictor` on long-format data with item id, timestamp, target, known covariates (holidays, paydays), static and past covariates; the last H steps held out; leaderboard, backtest windows, ensemble weights q̂ = Σ wₘ q̂⁽ᵐ⁾, the fan chart, per-series MASE/WQL/sMAPE/bias, and **interval calibration**: empirical against nominal coverage per quantile with Wilson intervals. |
| **Anomaly detection · card transactions** | IsolationForest (and LOF) without labels: s(x, ψ) = 2^(−E[h(x)]/c(ψ)), the score distribution with the contamination threshold s*, TreeSHAP reasons per flagged row, and **group-relative features** when an entity column is mapped: an amount divided by the card's own mean, its z-score within the card, and the time since the card's previous transaction. |
| **AutoGluon in full · the Training Gym** | Presets, model families, per-family hyperparameters, bag folds k, bag sets s, stack levels L, HPO, pseudo-labelling, distillation, `refit_full`, `infer_limit`, sample weights, a raw `fit_kwargs` editor; the resolved call as a diff against `fit()`'s defaults; the fit-time estimate T̂; export as Python. |
| **Explainability** | Performance, Threshold, Features & SHAP (permutation importance with 95% t-intervals, Kernel SHAP waterfall f(x) = φ₀ + Σ φᵢ), Errors, Fairness, Text: every number from the saved predictor and the fixed holdout. |
| **Registry, model card, approvals** | MLflow-backed registry; register with `refit_full`, `infer_limit` or a chosen faster model, over a measured latency · score frontier and the **one-row p95 latency** per model; an SR 11-7-style model card (JSON, Markdown, HTML); four-eyes review; stages None → Staging → Production → Archived; Try It with SHAP reasons; batch scoring with PSI drift for every kind of version; an append-only audit log with the model's SHA-256. |
| **Vela, the AI companion, and Autopilot** | Findings computed from a run (overfitting gap, weak class or segment, imbalance, leakage, budget, drift), each with one patch that applies in the Gym; a question box. Autopilot chains real runs against requirements you set (F̄₁ ≥ 0.92, τ₉₅ ≤ 50 ms, a budget B): baseline → cleanlab inbox → a new dataset version → retrain → rebalance, calibrate, refit for latency, escalate → Met or Not Met. Claude (CLI, then API) chooses steps when available; a fixed strategy otherwise, and the UI says which. |

## Quickstart

```
make setup       # Python venv (backend/.venv) with the pinned deps (AutoGluon tabular + timeseries, cleanlab, shap,
                 # fairlearn, MLflow), the Python client, and npm install
make dev         # API on :8000 and the UI on http://localhost:5190; Ctrl+C stops both
make smoke       # scripts/smoke.py: the UI's real calls end to end against the API (started if not running);
                 # ✓ / ✗ per step, exit 1 on any failure. SMOKE_ONLY=core,samples,autopilot,forecast,anomaly,import,fresh,reset
make reset       # delete every run, version, batch, autopilot, MLflow record and upload; regenerate the samples
```

Open http://localhost:5190. On a fresh workbench Home lists the six samples; pick one and the first model trains in
about a minute. `npm run build` type-checks and builds the UI. `node scripts/walkthrough.cjs` drives three fresh bank
CSVs (`scripts/fresh_data.py`) through the browser with a screenshot at every stop. The demo is scripted in
[`docs/demo-script.md`](docs/demo-script.md); the final walkthrough with screenshots of every page is
[`docs/progress/S9-walkthrough.md`](docs/progress/S9-walkthrough.md).

The older test suites remain (`make test`, `make test-full`: pytest and Playwright); since S7 the gate is `make smoke`.

## Architecture

```
 browser (React 18 + Vite, :5190) ──/api──▶ FastAPI (:8000) ─── SQLite (datasets, runs, events, registry, audit)
   live mode: every page calls the API            │         └─ data folder: datasets/, runs/<id>/predictor/, registry/
   mock mode: the scripted simulation             │
                                                  ├─ JobRunner: one `python -m app.engine.worker` process per fit
                                                  │    TabularPredictor / TimeSeriesPredictor / IsolationForest,
                                                  │    progress → events in SQLite → SSE with replay
                                                  ├─ worker pool (spawn): SHAP, importance, latency, version builds,
                                                  │    Try It, batch scoring, drift
                                                  ├─ MLflow (library, local SQLite store): runs tracked, versions registered
                                                  ├─ Autopilot threads (call the API over HTTP like the UI does)
                                                  └─ LLM layer: claude CLI → Anthropic API → computed text (always labelled)
```

- **No loss against raw AutoGluon.** `fit_kwargs` pass to `fit()` unchanged; the run's `resolved` field is the exact
  call; parity tests prove raw AutoGluon gives the same leaderboard; `GET /api/runs/{id}/export.py` reproduces it.
- **One fixed holdout per dataset** (20%, stratified on the target given at upload, else on the profiled label), plus
  five folds. Every model, run, version and batch on that dataset is scored on the same rows, so comparisons are like
  with like.
- **Restart-safe.** Runs survive an API reload in development (`ENSEMBLE_DEV_RELOAD=1`); the UI resumes a run's stream
  by replay from any event.

## Design system and the mathematics on screen

The UI follows the ENSEMBLE design system in `src/shared/styles/tokens.css`: dark shell, light canvas, 1 px borders,
monospace metrics, the Harmonic Thread (strings for connection and progress, waves for live computation), every chart
in a `PlotFrame` with a caption that defines its quantity, legends and hover read-outs on every plot.

**Show the mathematics.** The notation is real and bound to the run's numbers:

1. Every formula on screen is evaluated with the values of the run in view (ŷ(x) = 0.75·f₁(x) + 0.25·f₂(x), c(256) = 10.245, P(K ≥ 168) = 2.1×10⁻⁸), never a template.
2. Every symbol is defined on the same screen, in a where-list beside the formula. A symbol that is not defined is a bug.
3. Nothing is decorative: a formula appears only where it explains a number the user is looking at.
4. Intervals, not bare points, wherever the sample size allows: bootstrap CIs on holdout metrics, t-intervals on importance, Wilson intervals on coverage.
5. Honest limits: when a measured number is off its nominal value (interval coverage, one-row latency, a requirement Not Met), the page shows both and says why in plain words.

## The Python client

```python
from ensemble_client import Ensemble

ens = Ensemble("http://127.0.0.1:8000")
ds = ens.datasets.upload("card_fraud.csv", target="is_fraud")
run = ens.runs.start(ds, eval_metric="average_precision", time_limit=120, calibrate_threshold="f1",
                     fit_kwargs={"num_bag_folds": 3})
for ev in run.events():
    if ev["type"] == "model_finished":
        print(ev["model"], ev["val_score_natural"])
run.wait()
run.leaderboard()
run.predict([{"amount_gbp": 940, "merchant_type": "gift cards", "three_ds_passed": "no"}])
```

`make setup` installs it into `backend/.venv`; [`clients/python/README.md`](clients/python/README.md) covers the
registry, Vela and export.

## Known limits

- **Forecast intervals on the transaction-volume sample are too wide**: [q₀.₀₅, q₀.₉₅] covers 168/168 holdout points
  against 90% nominal. In the backtests ETS (92%) and Chronos-Bolt (88%) are near nominal; DirectTabular (98%), which
  carries 0.83 of the ensemble weight, is wide. The page shows the empirical coverage beside the nominal; nothing is
  rescaled.
- **Velocity bursts are hard for an isolation forest.** With group-relative features the card sample's amount spikes
  go from 0/18 to 8/18 found (P@90 0.61 → 0.71, AUC 0.954 → 0.990), but velocity bursts stay at 2/18: one short gap
  among many features rarely isolates a row. A rule on Δt is the practical complement.
- **One-row latency of text GBM ensembles** is 50–250 ms on 4 CPUs (featurisation plus every bagged child). Register
  shows the measured τ₉₅ and offers the fastest model within reach with its measured score cost (e.g. LightGBM at
  23 ms vs the ensemble's 50 ms for Δaccuracy −0.003 on support tickets).
- The holdout is fixed per dataset when it is uploaded: a CSV uploaded without naming the target is stratified on the
  profiler's guess, so the same file uploaded twice can have a different holdout (scores differ accordingly).
- Presets `high_quality`, `best_quality` and Chronos-2 download model weights on first use and are slow on 4 CPUs.
- No GPU support is exercised (shown as unavailable without CUDA). No sign-in: the "acting as" name goes in the audit log.
- Autopilots run in API threads; a development reload resumes them, but their wall time then counts from the resume.

## Screens (hash routes)

| Route | What it shows |
| --- | --- |
| `#/home` | The workbench: the samples on a fresh install, runs of every kind as strings, best scores per dataset, registry activity, a training monitor and log |
| `#/quick-train` | **New Model**: dataset or upload → label, problem type and column types → metric, preset, budget, raw `fit_kwargs` → the live race → results (metrics with bootstrap CIs, leaderboard, ensemble, confusion or residuals, custom ensemble, a first prediction). `?run=` resumes; `?dataset=` starts on a dataset |
| `#/explain?run=` | Performance, Threshold, Features & SHAP, Errors, Fairness, Text |
| `#/experiments` | Every run of every kind (binary, multiclass, regression, forecast, anomaly, imported) with a type filter and its own metric; parallel coordinates, Compare and Parameter Diff for tabular runs |
| `#/registry` | Register (frontier, one-row latency, faster model), lineage, versions, Overview, Model Card, Try It, Batch & Drift (all kinds), Audit |
| `#/gym` | Training Gym, Guided and Advanced |
| `#/autopilot` | Vela Autopilot: brief, the run as one string, Ground-Truth Inbox, decision log, requirements |
| `#/forecast` | New Forecast and forecast runs (`?run=`) |
| `#/anomaly` | Anomaly Detection and anomaly runs (`?run=`) |
| `#/import` | Import a TabularPredictor (zip or server path) |
| `#/settings` | Layout, string tension and motion, density, Guided/Advanced default, Vela, toolbelt |

## API reference

One FastAPI process with SQLite and a data folder, as in [`docs/demo-build-plan.md`](docs/demo-build-plan.md) (the build is split into sessions in [`docs/build-sessions.md`](docs/build-sessions.md); progress notes are in `docs/progress/`). Vite proxies `/api` to it. **AutoGluon is the engine**: each fit runs in its own process and streams its progress as events.

```
backend/
├── requirements.txt     what we depend on (pinned); requirements.lock is it fully resolved (`make lock`)
├── app/
│   ├── main.py          the FastAPI app: /api/health, /api/datasets; mounts the engine routes
│   ├── config.py        settings: ENSEMBLE_DATA_DIR (default backend/data), seed, holdout share, folds
│   ├── db.py            SQLite through the standard library (datasets, runs, run_events)
│   ├── datasets.py      dataset files and records; generates the samples on first start
│   ├── samples.py       seeded generators: support tickets (text), account churn, credit default, loan amount
│   ├── profiling.py     column types, class balance or regression target, missing values, duplicates, label
│   ├── splits.py        the fixed stratified holdout and 5 folds every model uses
│   ├── schemas.py       response models (mirrored in src/shared/api/types.ts)
│   └── engine/
│       ├── api.py       /api/runs, /api/runs/{id}/{events,predict,oof,feature-importance,cancel}, /api/models
│       ├── runner.py    the job runner: one `python -m app.engine.worker` process per fit; cancel; restart recovery
│       ├── worker.py    the fit: holdout out, missing labels dropped, TabularPredictor(...).fit(**fit_kwargs), results
│       ├── progress.py  AutoGluon callback + log handler → events (model started/finished, scores, weights)
│       ├── store.py     runs and their append-only events in SQLite (SSE replays from any seq)
│       ├── serving.py   loads saved predictors to predict, read out-of-fold predictions, compute importance
│       ├── custom_ensemble.py  fit_weighted_ensemble on chosen models, in its own process; honest comparison
│       ├── explain.py   threshold analysis, ROC/PR/calibration, errors, residuals, fairness (fairlearn), SHAP (Kernel)
│       ├── threshold.py calibrate / set the decision threshold, persist it in the predictor, re-score the holdout
│       ├── catalogue.py model families: name, kind, description, objective in maths notation
│       ├── pool.py      the worker pool (spawn processes) for heavy predictor work; predictors cached per worker
│       └── heavy.py     jobs that run in it: SHAP, permutation importance, predict, out-of-fold
│   ├── gym/             S6: schema.py (fit() keywords and per-family hyperparameters), validate.py (checks, resolved call,
│   │                      diff, AutoGluon's bagging/stacking choice, T̂), export.py (a run as a standalone script), api.py
│   ├── vela/            S6: findings.py (computed findings with patches), wording.py (claude CLI → API → computed), api.py
│   └── registry/        S5: model registry and use
│       ├── api.py       /api/registry/…, /api/runs/{id}/register, /api/runs/{id}/latency
│       ├── store.py     registered models, versions, the audit log and batch jobs in SQLite
│       ├── build.py     build a version in a worker: clone, refit_full, infer_limit, clone_for_deployment, measure, hash
│       ├── tracking.py  MLflow as a library on a local SQLite store: runs tracked, versions registered, stages mirrored
│       ├── card.py      the model card: JSON, Markdown and HTML
│       ├── scoring.py   Try It (prediction + SHAP reasons) and batch scoring, in workers
│       └── drift.py     PSI per feature and of the output, bands
├── ensemble_models/     custom AutoGluon models (TFIDF_LR, TFIDF_SVM); importing registers them with AutoGluon
├── tests/               pytest + httpx against a real uvicorn process (`-m slow` for real fits)
└── data/                git-ignored: ensemble.sqlite3, datasets/<id>/…, runs/<id>/{config.json, worker.log,
                         predictor/ (a plain AutoGluon predictor), holdout_predictions.csv},
                         registry/<model>/v<n>/{predictor/, holdout_predictions.csv}, registry/batches/<id>/,
                         mlflow/{mlflow.db, artifacts/}
clients/python/          ensemble_client: a pip-installable notebook client (datasets, runs, events, leaderboard, predict,
                         Vela, export, registry); `make setup` installs it into backend/.venv
e2e/                     Playwright: live mode, mock mode, every page in both, one real AutoGluon run
```

### Forecast, anomaly and import API (S8)

Forecast and anomaly runs are rows in the same `runs` table (forecast runs' `holdout.calibration` holds F̂(τ) per quantile level and each central interval's empirical coverage with a Wilson CI and the binomial tail; registry batches work for these versions too: new history to forecast, or rows to score, with PSI drift), run by the same job runner in their own process, with the same events and SSE (`GET /api/runs/{id}`, `/api/runs/{id}/events`). `GET /api/runs` lists tabular runs (and imports) by default; `?kind=forecast|anomaly|all` lists the others. Registering works the same (`POST /api/runs/{id}/register`): the version is a copy of the model folder, measured, hashed and mirrored to MLflow, and the model card gets a forecasting or anomaly section.

| Call | What it does |
| --- | --- |
| `GET /api/forecast/options` · `GET /api/forecast/mapping/{dataset}` · `POST /api/forecast/preview` | Presets, metrics with formulas, defaults · a suggested column mapping (the sample's spec, else names and types) · what a mapping gives: series, lengths, inferred freq, season m, future rows, the last 60 points per series |
| `POST /api/forecast/runs` | `{dataset_id, mapping: {item_id, timestamp, target, known_covariates, static_features, past_covariates}, prediction_length, freq?, eval_metric, presets, time_limit, quantile_levels, num_val_windows, hyperparameters?, fit_kwargs, predictor_kwargs}`. The last H steps of each series are held out; the predictor is fit on the rest. Result: leaderboard (val and holdout), ensemble weights, backtest windows scored per model, holdout metrics and per-series errors, the future forecast (known covariates from the file's future rows, else one season back) |
| `GET /api/forecast/runs/{id}/chart` · `…/forecast.csv?which=future\|holdout` · `POST …/predict` (multipart history CSV) | Fan-chart data per series · the forecast as CSV (mean + every quantile) · a forecast from new history with the saved predictor |
| `POST /api/anomaly/runs` | `{dataset_id, features?, contamination, n_estimators, max_samples, max_features, lof, lof_neighbors, iforest_kwargs}`; scores, threshold, distribution, TreeSHAP reasons for the top rows, precision@k against `anomaly_truth.json` when the dataset is the sample |
| `GET /api/anomaly/runs/{id}/top?contamination=&limit=` · `…/scores` · `…/scores.csv` · `POST …/score` (multipart CSV) · `POST …/score-one` | Top rows with reasons and flags at any contamination · histogram, quantiles, IF vs LOF · every row's score · a new batch scored and downloadable · one row |
| `POST /api/runs/import` (multipart: `dataset_id`, `file` = zip or `path`) | Import a TabularPredictor as a run (validated in a worker; scored on the dataset's holdout; not refit) |
| `POST /api/admin/reset` | `{"confirm": "reset"}`: `make reset` |
| `GET /api/anomaly/mapping/{dataset}` | Suggested `entity_column`, `time_column`, `relative_columns` for group-relative features (the sample's spec, else names and types). `POST /api/anomaly/runs` takes `group_features` (default: on when an entity column is mapped; on for the card sample), `entity_column`, `time_column`, `relative_columns`. |
| `GET /api/runs?kind=all` | Every run with its `kind` (tabular, import, forecast, anomaly): what Experiments and Home list. |

### Autopilot API (S7)

| Call | What it does |
| --- | --- |
| `POST /api/autopilot` | Brief: `dataset_id`, `label`, `requirements` (`[{metric, op, value}]`: f1_macro, accuracy, balanced_accuracy, mcc, roc_auc, f1, r2, rmse, mae, `recall[<class>]`, `p95_latency_ms`, `p50_latency_ms`), `budget_seconds`, `autonomy` (`ask` waits for the inbox; `auto` accepts suggestions with c ≥ `auto_confidence`), `decider` (`auto`: Claude then layer 1; `layer1`: fixed strategy only). Runs in a thread of the API, calling the API over HTTP; resumes after a restart. |
| `GET /api/autopilot[/{id}]` | State: steps (each run with its body, scores on its own and on the current labels, p95 latency, kept or dropped, which layer decided), decisions, the log in Vela's voice, dataset versions, budget, verdict (Met / Not Met with every check). |
| `GET /api/autopilot/{id}/inbox` | The Ground-Truth Inbox: label issues (cleanlab `find_label_issues` on out-of-fold and held-out p̂, ranked by m(x) = p̂_y − max_{k≠y} p̂_k), exact duplicates, near-duplicate conflicts, outliers (`OutOfDistribution`), each with text, given / suggested label, confidence and why; recall of `label_noise.json` when the dataset has one. |
| `POST …/inbox/answer`, `…/inbox/bulk`, `…/resume`, `…/cancel` | Answer rows (confirm, relabel, drop, skip), bulk-accept above a confidence, submit and continue (a new dataset version with lineage and the same holdout rows), stop. |
| `POST /api/vela/ask` | Vela's question box: `{question, run_id?, autopilot_id?}` answered from the computed facts by the Claude CLI, then the API, else `answer: null` and "no model available". |

### Engine API

| Call | What it does |
| --- | --- |
| `GET /api/models` | The catalogue: `key` (AutoGluon hyperparameters key), `name`, `kind`, `description`, `objective` (Unicode maths), `objective_tex`, `symbols` (every symbol defined), `problem_types`, `custom`. |
| `POST /api/runs` | `{dataset_id, label?, problem_type: auto\|binary\|multiclass\|regression\|quantile, eval_metric?, presets='medium_quality', time_limit=120, fit_kwargs={}, predictor_kwargs={}, custom_text_models=true, drop_columns?, feature_importance=true, column_types?}` → 201, the run (queued). `fit_kwargs` go to `TabularPredictor.fit` unchanged. `column_types` (`{column: number\|category\|text}`) overrides inferred types: the columns are cast and AutoGluon gets the matching `feature_metadata` (shown in `resolved`). `fit_kwargs.feature_metadata` may also be given as JSON (`{type_map_raw, type_group_map_special}`). |
| `GET /api/runs[?dataset_id=]` · `GET /api/runs/{id}` | Status, data counts, leaderboard (AutoGluon's extra info, holdout scores, fold scores and spread), ensemble weights, holdout metrics and confusion matrix, OOF availability, feature importance, TF-IDF top words, the exact `resolved` call, the predictor path. The list trims the heavy fields. |
| `GET /api/runs/{id}/events` | SSE: every event from `?from=N` (or after `Last-Event-ID`); ends after `run_succeeded`/`run_failed`/`run_cancelled`. `?format=json` returns them once. |
| `POST /api/runs/{id}/predict` | `{rows: [{col: value}], model?}` → predictions, per-class probabilities. |
| `GET /api/runs/{id}/oof[?model=]` · `GET /api/runs/{id}/feature-importance` | Out-of-fold predictions (bagged runs) · permutation importance on the holdout (computed on demand if the run skipped it). |
| `POST /api/runs/{id}/ensemble` | `{models: [base model, …]}` → 201: `predictor.fit_weighted_ensemble(base_models=models)` in its own process. Returns the new model's weights, validation and holdout scores, the best single model's, and `vs_best_single` / `vs_run_best` (`beats`, `delta`, `delta_natural`) on validation and holdout. The run's default model is not changed. On refit presets (good/high quality) the new ensemble is refit too and `predict_model` names its `_FULL` copy. The ensemble is added to the run's leaderboard (`custom: true`) and to `custom_ensembles`. |
| `GET /api/runs/{id}/holdout-predictions` | The best model's holdout predictions: `rows` (data.csv positions), `y_true`, `y_pred`, and per-class `probabilities` (classification). |
| `POST /api/runs/{id}/cancel` · `DELETE /api/runs/{id}` | Stop a run (its whole process group) · delete a finished run and its folder. |
| `GET /api/runs/{id}/threshold?cost_fp&cost_fn&t` | Binary: on the holdout, at every t on a 0.005 grid (and at `t`): TP, FP, FN, TN, precision, recall, F₁, specificity, FPR, balanced accuracy, accuracy, MCC, flagged share, cost C(t) = c_FP·FP + c_FN·FN; the t that maximises F₁ / bacc / MCC and minimises cost; the Bayes threshold c_FP/(c_FP + c_FN); the score histogram per class; the threshold in use. Positive = p > t, as AutoGluon predicts. |
| `POST /api/runs/{id}/threshold` | `{metric}` → `predictor.calibrate_decision_threshold(metric=…)`, or `{value}`; then `set_decision_threshold` and `save()`, so every later prediction (and `TabularPredictor.load`) uses it. The holdout is re-scored at t (`holdout`, confusion matrix, saved predictions). |
| `GET /api/runs/{id}/explain` | Classification: confusion matrix, per-class precision / recall / F₁ / support, ROC (fpr, tpr, thresholds, AUC) and PR (precision, recall, AP) per positive class or one-vs-rest, the reliability curve (10 uniform bins, counts, Brier, ECE; top-label for multiclass), log loss, the worst mistakes with their features. Regression: RMSE, MAE, R², residual mean and σ, quantiles, residuals vs ŷ, a 30-bin histogram with its normal fit, the largest errors. Plus the columns usable as groups. |
| `GET /api/runs/{id}/fairness?group=&t=` | fairlearn `MetricFrame` per group (numbers > 8 values cut into quartiles; missing is a group): binary count, selection rate, base rate, accuracy, TPR, FPR, precision at the run's t, with demographic parity difference / ratio and equalised odds difference; multiclass accuracy and macro F₁; regression MAE, RMSE, bias. |
| `GET /api/runs/{id}/shap?row=&target=` | Kernel SHAP (`shap.KernelExplainer` on `predict_proba` of the target class, or `predict` for regression; 50 background training rows from `shap.sample(…, random_state=0)`, 2048 coalitions, no L1 selection) for one holdout row: φ₀, every φᵢ with the row's value, f(x); f(x) = φ₀ + Σ φᵢ exactly. Cached in the run folder. |
| `GET /api/runs/{id}/feature-importance?shuffles=` | Permutation importance (now 5 shuffles when computed on demand, `shuffles` to recompute) with a 95% t-interval Î ± t₀.₉₇₅,ₙ₋₁·s/√n per feature. |
| `GET /api/status` | The status bar's live state: versions, CPU count, memory, host, the active run (stage, models started / finished, best validation score, started_at, τ), queued runs, run count, registry counts, worker-pool jobs. |
| `GET /api/runs/{id}/latency` | Every model of the run that can predict: measured batch µs per row on the holdout (p50 of 3), one-row call latency `single_ms_p50` / `single_ms_p95`, validation and holdout score, the models and bytes it needs. Measured in a worker; cached. |
| `POST /api/runs/{id}/register` | `{created_by, model_name?, description?, refit_full=false, infer_limit? (s/row), infer_limit_batch_size?, clone_for_deployment=true, serve_model?}` (`serve_model`: serve that model of the run, its `_FULL` copy after refit) → 202, a version `building`; its steps update until `ready` (or `failed`). The run is never changed. |
| `GET /api/registry` · `/registry/summary` · `/registry/versions[?model=]` · `/registry/versions/{id}` | Registered models, versions (with build steps, measured source vs version size and latency, holdout metrics with 95% bootstrap CIs, infer_limit candidates, hashes, MLflow), stage and approval counts, recent audit. |
| `POST /api/registry/versions/{id}/transition` | `{stage: None\|Staging\|Production\|Archived, actor, comment}`. Production needs an approved review; promoting archives the model's previous Production version; rejected versions cannot go live. Mirrored to MLflow (stage, `production` / `staging` aliases, tags). |
| `POST /api/registry/versions/{id}/review` | `{decision: approve\|reject, reviewer, comment}`. Four eyes: the reviewer cannot be the registrant; a rejection needs a comment and archives a live version; one review per version. |
| `GET /api/registry/audit?model=&version_id=` | The append-only audit log: who, action, from → to stage, comment, the model's SHA-256 at that moment. |
| `GET /api/registry/versions/{id}/card?format=json\|md\|html&download=` · `PUT …/card` | The model card; `PUT {actor, fields}` fills the intended-use template (audited). |
| `POST /api/registry/versions/{id}/predict` | `{row, explain=true, target?}` → prediction, probabilities, Kernel SHAP for that row with the top 5 reasons. In a worker. |
| `POST /api/registry/versions/{id}/batches` (multipart `file`, `created_by`) · `GET /api/registry/batches/{bid}` · `…/download` · `GET /api/registry/versions/{id}/batches` | Batch scoring in a worker with progress; result: summary, preview, the scored CSV (input + `prediction` + `p_<class>`), and drift: PSI per feature against the training rows (deciles / top-19 categories / text length, missing as a bin, ε = 10⁻⁴) and of the output against the version's holdout predictions, with KS. |
| `GET /api/gym/schema` | What the Gym configures: model families with objectives and slider specs (AutoGluon key, symbol, interval, log/linear, integer, AutoGluon's default), presets and what they expand to, metrics per problem type, HPO options, the GPU count, every keyword `TabularPredictor.fit` accepts with its default (read from the installed AutoGluon), the default model zoo. |
| `POST /api/gym/validate` | A run body (as for `POST /api/runs`) → `errors` / `warnings` (unknown fit arguments with a suggestion, invalid values, stacking without bagging, GPUs that are not there, metric vs problem type, columns), `resolved` (the exact predictor and fit kwargs, post-fit steps), `call` (Python), `diff` against fit()'s defaults with origin, `effective` (AutoGluon's `get_validation_and_stacking_method` for this data: k, s, L, dynamic stacking, holdout_frac, refit), `estimate` (T̂ with per-family rates and their source). `POST /api/runs` refuses a body with errors (400). |
| `GET /api/runs/{id}/gym` | The body that starts the run again, its parent run and patch. |
| `GET /api/runs/{id}/export.py` | A standalone script that reproduces the run with plain AutoGluon: data file with its SHA-256, the exact holdout rows (packed bitmask), dropped columns and column types, the resolved `TabularPredictor(...).fit(...)`, post-fit steps, the threshold step; it ends by comparing its leaderboard with the run's, model by model (`--tolerance`, `--no-check`). |
| `GET /api/runs/{id}/vela` · `POST …/vela/wording` | Findings computed from the run (numbers, formula, one patch each with `changes` and the patched `body`) · their wording by the claude CLI, else the Anthropic API, else the computed text (`source`, `model`, attempts), cached per findings. |
| `GET /api/registry/versions/{id}/mlflow` | MLflow's own record of the version and its source run (params, metrics, tags). |

`POST /api/runs` also takes `pseudolabel` (`{dataset_id, method: fit_pseudolabel | unlabeled_data, max_iter}`: unlabeled rows from another dataset, label column dropped), `distill` (kwargs of `predictor.distill`, run after the fit with a seeded generator so it reproduces), and `parent_run_id` / `patch` / `source` (lineage for Vela patches, the Gym and the client). `POST /api/runs` also takes `calibrate_threshold` (binary): a metric (`f1`, `balanced_accuracy`, `mcc`, …) to calibrate the decision threshold for after the fit, or `auto` (F₁ when the minority class is under 20% of the training rows). `fit_kwargs.calibrate_decision_threshold`, if given, always wins (AutoGluon does it, or not, inside `fit`). The New Model wizard sends `f1` by default when the label's minority class is under 20%; the Goal step shows the choice. Every binary run records `threshold` = `{value, source, metric, holdout_at_default, holdout_at_threshold}`.

**Heavy work runs in worker processes.** SHAP, permutation importance, predictions, version builds and batch scoring run in a small `ProcessPoolExecutor` (`ENSEMBLE_HEAVY_WORKERS`, default 2, spawn context), not in the API process, so `/api/health` and run reads stay fast while they compute (`tests/test_concurrency.py` proves it). Each worker caches a few predictors and reloads one whose files changed; a crashed worker is replaced on the next call.

**MLflow.** Every finished run is logged to MLflow (parameters from the resolved fit call, holdout metrics, light artefacts: config, resolved call, leaderboard, holdout predictions; the predictor path as a tag) and every registered version becomes an MLflow model version whose source is its own predictor folder, with its stage, aliases and approval tags mirrored. The store is `backend/data/mlflow/mlflow.db` (SQLite, a local file; MLflow 3.18 refuses the plain `./mlruns` folder store without an opt-out): `mlflow ui --backend-store-uri sqlite:///backend/data/mlflow/mlflow.db`.

**`make dev` and running fits.** uvicorn reloads the API when `backend/app` or `backend/ensemble_models` change (only those folders are watched). With `ENSEMBLE_DEV_RELOAD=1`, which `make dev` and `make api` set, a reload keeps fits alive: each worker watches uvicorn's reloader process rather than the API process, the old API leaves running workers and queued runs alone when it stops, and the new one adopts them (it follows the worker's pid, can cancel it, and re-queues queued runs). Workers write their own events, so the UI's stream resumes by replay. Ctrl+C on `make dev` stops the reloader, and the workers stop within a second. Without the variable (tests, production-like starts) a stop still fails in-flight runs, as S2 specified. Code changes reach a fit only when the next one starts: each fit runs the code it was started with.

Every run is a plain AutoGluon predictor: `TabularPredictor.load("backend/data/runs/<id>/predictor")`. Runs on text data also contain the custom TF-IDF models; load those with `backend/` on the Python path (`import ensemble_models`). The run's `resolved` field is the exact `TabularPredictor(**predictor_kwargs).fit(train, **fit_kwargs)` call; the parity tests prove raw AutoGluon gives the same leaderboard.


## Project structure

The tree mirrors the app: one folder per page under `features/`, the frame around the pages under `app/`, and anything used by more than one page under `shared/`.

```
src/
├── main.tsx
├── app/                         the frame around every page
│   ├── App.tsx                  state, routing, layout
│   ├── routes.ts                routes, titles, sidebar order
│   └── shell/                   Sidebar, ContextBar, StatusBar, CommandPalette (+ shell.css)
├── features/                    one folder per page
│   ├── home/                    HomePage · components/ToolCard
│   ├── quick-train/             New Model wizard: QuickTrainPage · wizard.ts (choices, metrics with formulas, presets)
│   │                              components/{DatasetStep, TargetStep, GoalStep} · datasets.ts
│   │                              race/{useRace (events → state, readable playback), RaceStage, ModelString, EnsembleMerge,
│   │                                StackEquation, mockRun (mock mode's simulated run)}
│   │                              results/{ResultsStep, MetricCards, Leaderboard (+ ScoreScatter, ModelCard), Diagnostics
│   │                                (ClassReport, Residuals, TopWords), CustomEnsemble, TryPrediction, stats (metrics, bootstrap)}
│   ├── autopilot/               AutopilotPage · components/{BriefStep, IterationString, RequirementGauge, DecisionLog,
│   │                              GroundTruthInbox, DataChanges} · run.tsx (the scripted run)
│   ├── training-gym/            TrainingGymPage · components/{Hyperparameters, ConfigBlock, ClassBalance, PipelineCard,
│   │                              ValidationCurve, FitEstimate, ConsoleDock, StepSummary} · params.ts
│   ├── experiments/             ExperimentsPage (live → LiveExperiments; mock → the simulated runs) · components/{RunTable (mock),
│   │                              RunCoordinates, CompareRuns, ParamDiff}
│   ├── explain/                 ExplainPage · components/{Curves (ROC, PR, calibration, per-class), ThresholdPanel, Importance,
│   │                              ShapPanel, Errors (worst rows, residuals), FairnessPanel}
│   ├── registry/                RegistryPage
│   ├── settings/                SettingsPage · components/SettingRow
│   └── vela/                    VelaSidecar · components/PatchCard (the assistant panel, shown on every page)
└── shared/
    ├── primitives/              fundamentals: one small thing each, no layout of their own
    │                              StatusPill, ParamChip, Segmented, Tabs, NumberInput, Icon, VelaCrest,
    │                              ResonanceWave, SparkWave, PluckString, ToolGlyph
    ├── components/              larger pieces composed from primitives
    │                              FretSlider, PlotFrame, LineChart, XYPlot (real-valued axes, hover, marks),
    │                              ConfusionMatrix, StringPlot, TopWords, Graph, RunString, StringField
    ├── api/                     the typed API client, its types, and the live/mock mode provider (useApi)
    ├── hooks/                   useWidth
    ├── lib/                     prefs (saved settings), tools (the toolkit list), random, format, metrics (metrics with
    │                              their formulas, number formatting)
    ├── data/                    mock runs and metrics used by more than one page
    ├── assets/                  the ENSEMBLE wordmarks and Vela crest, unchanged from the design system
    └── styles/                  tokens.css, base.css, surfaces.css, responsive.css, index.css
```

### Rules

- **A page owns what only it uses.** A component, data file or stylesheet used by one page lives in that page's folder. Move it to `shared/` when a second page needs it.
- **Primitives vs components.** `shared/primitives` are the fundamentals (a pill, a chip, a wave). `shared/components` are bigger and are built from primitives (a chart, a slider with its input, a graph).
- **Imports.** `@/` is `src/`. Features import from `shared/`; `shared/` never imports from `features/`. The one cross-feature import is Autopilot reusing Quick Train's sample datasets.
- **Styles.** Each folder keeps its own `.css` beside its code, and the same ownership rule applies: a class used by two pages lives in `shared/` (for example the navy `.stage` panel and the `.wizard` page layout). `surfaces.css` is the one deliberate exception: it is the tonal layer (gray bands, wells, navy readouts) applied across everything. `shared/styles/index.css` imports them all in a fixed order (tokens → base → shared → shell → features → overrides), so the cascade is explicit. Class names are global.
- **Live and mock.** Pages call the API through `shared/api` and fall back to their simulation when `useApi().mode` is `mock` or a call can't reach the API. Never remove the mock path. Numbers in mock mode that appear on more than one page come from one place (`shared/data/runs.ts`, `quick-train/recipes.tsx`).
- **Assets.** `ensemble-wordmark.svg` (for light surfaces) is kept although nothing uses it yet; the app only shows the on-shell wordmark.
