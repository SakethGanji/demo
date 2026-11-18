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
| `#/home` | The Workbench: the Project ENSEMBLE lockup over an interactive string field, the Toolkit (six tools you can pin to the sidebar toolbelt), runs drawn as strings, a live Training Monitor plot and a streaming log |
| `#/experiments` | Parallel coordinates of runs, runs table with selection and an `F1 ≥ 0.90` filter, multi-run comparison plot with crosshair hover, parameter diff, confusion matrix on the heat ramp and per-class recall |
| `#/registry` | Model Registry: stage KPIs, version lineage graph, versions table |
| `#/settings` | Customization, kept in the browser: layout (inset or flush), string tension, string motion, density, default Guided / Advanced mode, Vela sidecar, toolbelt |

The shell adds a status bar (API mode — `● live` or `▲ mock mode` — compute, live run, current config) and a command palette on ⌘K / Ctrl+K.

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
│       └── catalogue.py model families: name, kind, description, objective in maths notation
├── ensemble_models/     custom AutoGluon models (TFIDF_LR, TFIDF_SVM); importing registers them with AutoGluon
├── tests/               pytest + httpx against a real uvicorn process (`-m slow` for real fits)
└── data/                git-ignored: ensemble.sqlite3, datasets/<id>/…, runs/<id>/{config.json, worker.log,
                         predictor/ (a plain AutoGluon predictor), holdout_predictions.csv}
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
│   ├── experiments/             ExperimentsPage · components/RunTable
│   ├── registry/                RegistryPage
│   ├── settings/                SettingsPage · components/SettingRow
│   └── vela/                    VelaSidecar · components/PatchCard (the assistant panel, shown on every page)
└── shared/
    ├── primitives/              fundamentals: one small thing each, no layout of their own
    │                              StatusPill, ParamChip, Segmented, Tabs, NumberInput, Icon, VelaCrest,
    │                              ResonanceWave, SparkWave, PluckString, ToolGlyph
    ├── components/              larger pieces composed from primitives
    │                              FretSlider, PlotFrame, LineChart, ConfusionMatrix, StringPlot,
    │                              Graph, RunString, StringField
    ├── api/                     the typed API client, its types, and the live/mock mode provider (useApi)
    ├── hooks/                   useWidth
    ├── lib/                     prefs (saved settings), tools (the toolkit list), random, format
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
