# Project ENSEMBLE · UI

The Project ENSEMBLE UI and its demo API. When the API is running the UI is **live** (datasets are profiled for real and AutoGluon trains on them); when it isn't, the UI runs in **mock mode** on its in-browser simulation, so the demo always opens. A fresh React 18 + Vite build of the Project ENSEMBLE interface, built on the ENSEMBLE design system: dark shell, light canvas, 1px borders, monospace metrics, and the Harmonic Thread (strings for connection and progress, waves for live computation).

```
make setup       # Python venv (backend/.venv) with the pinned deps, plus npm install
make dev         # API on :8000 and the UI on http://localhost:5190, together; Ctrl+C stops both.
                 # Editing backend code reloads the API without stopping running fits (see below)
make test        # fast tier: pytest (not slow, includes tiny real fits), tsc --noEmit, vite build
make test-full   # adds the slow pytest marks and the Playwright e2e suite (live and mock)
make clean-data  # delete backend/data; the samples are regenerated on the next start
```

`npm run dev` alone still works: the UI shows "mock mode" in the status bar until an API answers on :8000.

## Screens (hash routes)

| Route | What it shows |
| --- | --- |
| `#/quick-train` | **New Model** (the route keeps its old name): (1) a sample or a CSV upload, profiled; (2) the label column, the problem type inferred (binary, multiclass, regression) with an override, and every column's type, editable, sent to AutoGluon as `feature_metadata`; (3) the metric (each with its formula), a preset ladder from medium to best (speed ↔ accuracy), the time budget τ, and an Advanced fold-out for raw `fit_kwargs` JSON with the resolved `TabularPredictor(...).fit(...)` call; (4) the **live race**: one Harmonic Thread string per model that rings while it fits and settles at its validation score on a shared axis (fold scores as beads, μ ± σ as a band), stack levels as layers, the WeightedEnsemble forming as the strings merge (thickness ∝ wₘ) with ŷ = Σ wₘ fₘ(x) and the real weights, a clock against τ, a minimum on-screen time per model and a 3× control; the URL carries `?run=<id>` so a reload or a dropped stream resumes by replay; (5) **results**: holdout metric cards with formulas and 95% bootstrap intervals, the leaderboard (validation μ ± σ, holdout, fit time, predict time per row, stack level, weights; best highlighted), validation-vs-holdout plot, each model's objective from the catalogue, the confusion matrix with recall and precision (classification) or residuals (regression), top words per class (TF-IDF models), **Build my own ensemble** (`fit_weighted_ensemble` on chosen models, with an honest comparison against the best single model) and **Try a prediction** (a form generated from the features). Mock mode plays a simulated run through the same screens |
| `#/autopilot` | Vela Autopilot (prototype, scripted): a brief with requirements and an autonomy level, Vela's plan, then a run drawn as one string with dropped experiments hanging off it, requirement gauges, a decision log, a ground-truth inbox that is the only place it stops for you, and a data-changes panel |
| `#/gym` | Training Gym: live Fit Estimate and Validation Curve that follow the sliders, a console dock (Validation checks, Dry Run), 10-step tension stepper, Guided / Advanced hyperparameters with fret-track sliders and exact numeric inputs, the objective as a Formula with its where-list, a catenary pipeline on graph paper, a live-diffed run config, label distribution, sticky footer |
| `#/home` | The Workbench: the Project ENSEMBLE lockup over an interactive string field and the Toolkit (six tools you can pin to the sidebar toolbelt). Live mode: real runs as strings (taut when finished with the holdout score, a wave up to t / τ while fitting), best scores per dataset, registry counts and recent audit events, a **Training Monitor** following the active run (each model's validation score as it finishes and the best-so-far staircase) or replaying the last one, and its AutoGluon log. Mock mode keeps the simulation, labelled SIMULATED |
| `#/experiments` | **Experiments** on the API's real runs: filters (dataset, problem type, status, a floor on the run's own metric, search), the runs table (holdout and validation score in natural units, decision threshold, models, presets · τ, fit time; Explain / Results per run), **parallel coordinates** of the comparable runs (same dataset, label and metric) over their real parameters and metrics (τ, presets, bag folds, models, fit time, µs per row, t, validation and holdout metrics; constant axes dropped), **Compare** (holdout metrics side by side with Δ against the first run, ROC and PR overlays or residual distributions, best validation score per model family) and **Parameter Diff** (the exact resolved `TabularPredictor(...).fit(...)` arguments, the data and the results, differences tinted). Mock mode keeps the simulated runs, labelled SIMULATED |
| `#/explain?run=<id>` | **Explain** one run (from Experiments, from New Model's results, or the run picker). A navy strip of threshold-free and threshold scores. Tabs: **Performance** (ROC with AUC and the operating point, precision–recall with AP and the prevalence line, the reliability diagram with Brier and ECE; one-vs-rest for multiclass; confusion matrix and per-class report; regression: residuals vs ŷ with ±1.96 σᵣ and a residual histogram with its normal fit), **Threshold** (binary: a slider over t with precision, recall, F₁, balanced accuracy, MCC, FPR, flagged share, the 2×2 counts and the cost C(t) = c_FP·FP + c_FN·FN, all from the API's 0.005 grid plus the exact point; metric curves, the score histogram per class, the cost curve with the Bayes threshold; *Calibrate threshold* runs AutoGluon's `calibrate_decision_threshold(metric)` and *Use t* sets it, both saved in the predictor), **Features & SHAP** (permutation importance with 95% t-intervals and p-values, re-run with 10 shuffles; Kernel SHAP for a holdout row as a waterfall φ₀ → f(x) with f(x) = φ₀ + Σ φᵢ written out), **Errors** (the most confidently wrong rows with their features, or the largest residuals; *why? φ* opens SHAP for that row), **Fairness** (fairlearn's MetricFrame by a group column you pick: per-group selection rate, base rate, accuracy, TPR, FPR, precision; demographic parity difference and ratio, equalised odds difference), **Text** (top words per class for TF-IDF runs) |
| `#/registry` | **Model Registry** on real data (MLflow registry mirrored on a local store). **Register a run** (from here, an Experiments row or New Model's results) with `refit_full`, `infer_limit` (a latency budget in µs per row) and `clone_for_deployment`, over a measured latency · score frontier of every model in the run; the version is built in a worker process (clone → refit → budget → deployment copy → measure → hash → MLflow) with its steps shown live. Stage KPIs, a lineage graph (dataset → run → version → registered model, catenary strings: live gradient for Production, dashed for pending), the versions table, and per version: **Overview** (stage rail None → Staging → Production → Archived, approve / reject with a comment under a four-eyes rule, the measured trade-off source vs version: size on disk, batch µs/row p50/p95 as beads per repeat, one-row ms p50/p95, throughput, holdout metric with its 95% bootstrap CI; the infer_limit candidate table; provenance: model, data and split SHA-256, predictor path, MLflow model URI), **Model Card** (an SR 11-7-style document: summary, intended use template you fill in, data with label balance and hashes, the resolved fit call, the stacked ensemble's weights, holdout metrics with CIs, importance, fairness, deployment trade-off, monitoring, approvals and sign-off; download as Markdown or HTML), **Try It** (a form generated from the schema, the prediction and probabilities, and Kernel SHAP's top reasons and waterfall for that row), **Batch & Drift** (upload a CSV, progress, preview, download the scored CSV; PSI = Σ (aᵢ − eᵢ) ln(aᵢ/eᵢ) per feature against the training rows with bands < 0.10 stable / 0.10–0.25 watch / > 0.25 shift, per-bin terms, and the output distribution's PSI and KS), **Audit** (who, what, when, the model hash). Mock mode keeps the scripted registry, labelled SIMULATED |
| `#/settings` | Customization, kept in the browser: layout (inset or flush), string tension, string motion, density, default Guided / Advanced mode, Vela sidecar, toolbelt |

The shell adds a status bar and a command palette on ⌘K / Ctrl+K. In live mode the status bar, the context bar's compute chip and the sidebar's user block show real state from `GET /api/status` (API and AutoGluon versions, the active run with models finished / started, best validation score and its clock against τ, the queue, the run count, CPUs and memory, the host; no sign-in). Mock mode keeps its simulated values.

Vela, the AI companion, is a collapsible sidecar that gives PatchCards; applying one changes the Training Gym config.

## Backend

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
e2e/                     Playwright: live mode, mock mode, every page in both, one real AutoGluon run
```

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
| `GET /api/runs/{id}/latency` | Every model of the run that can predict: measured batch µs per row on the holdout (p50 of 3), validation and holdout score, the models and bytes it needs. Measured in a worker; cached. |
| `POST /api/runs/{id}/register` | `{created_by, model_name?, description?, refit_full=false, infer_limit? (s/row), infer_limit_batch_size?, clone_for_deployment=true}` → 202, a version `building`; its steps update until `ready` (or `failed`). The run is never changed. |
| `GET /api/registry` · `/registry/summary` · `/registry/versions[?model=]` · `/registry/versions/{id}` | Registered models, versions (with build steps, measured source vs version size and latency, holdout metrics with 95% bootstrap CIs, infer_limit candidates, hashes, MLflow), stage and approval counts, recent audit. |
| `POST /api/registry/versions/{id}/transition` | `{stage: None\|Staging\|Production\|Archived, actor, comment}`. Production needs an approved review; promoting archives the model's previous Production version; rejected versions cannot go live. Mirrored to MLflow (stage, `production` / `staging` aliases, tags). |
| `POST /api/registry/versions/{id}/review` | `{decision: approve\|reject, reviewer, comment}`. Four eyes: the reviewer cannot be the registrant; a rejection needs a comment and archives a live version; one review per version. |
| `GET /api/registry/audit?model=&version_id=` | The append-only audit log: who, action, from → to stage, comment, the model's SHA-256 at that moment. |
| `GET /api/registry/versions/{id}/card?format=json\|md\|html&download=` · `PUT …/card` | The model card; `PUT {actor, fields}` fills the intended-use template (audited). |
| `POST /api/registry/versions/{id}/predict` | `{row, explain=true, target?}` → prediction, probabilities, Kernel SHAP for that row with the top 5 reasons. In a worker. |
| `POST /api/registry/versions/{id}/batches` (multipart `file`, `created_by`) · `GET /api/registry/batches/{bid}` · `…/download` · `GET /api/registry/versions/{id}/batches` | Batch scoring in a worker with progress; result: summary, preview, the scored CSV (input + `prediction` + `p_<class>`), and drift: PSI per feature against the training rows (deciles / top-19 categories / text length, missing as a bin, ε = 10⁻⁴) and of the output against the version's holdout predictions, with KS. |
| `GET /api/registry/versions/{id}/mlflow` | MLflow's own record of the version and its source run (params, metrics, tags). |

`POST /api/runs` also takes `calibrate_threshold` (binary): a metric (`f1`, `balanced_accuracy`, `mcc`, …) to calibrate the decision threshold for after the fit, or `auto` (F₁ when the minority class is under 20% of the training rows). `fit_kwargs.calibrate_decision_threshold`, if given, always wins (AutoGluon does it, or not, inside `fit`). The New Model wizard sends `f1` by default when the label's minority class is under 20%; the Goal step shows the choice. Every binary run records `threshold` = `{value, source, metric, holdout_at_default, holdout_at_threshold}`.

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
