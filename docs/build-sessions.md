# Build sessions

This is the overnight build of Project ENSEMBLE: an internal AutoML workbench for a bank, with **AutoGluon as the engine**. It is run as ten sessions in sequence on the `motion-components` branch. An orchestrator session starts each one, reruns the full suite after it finishes, and only then starts the next. The scope here replaces the hand-rolled model list in [`demo-build-plan.md`](demo-build-plan.md). That document still holds for the product flow, the UI ideas, the demo-proofing in §9, and the architecture's spirit: one process to start, no Redis, Celery, Docker or sign-in.

## The product in one paragraph

Anyone at the bank brings a table (CSV upload or a sample), says what to predict, picks a goal and a time budget, and watches AutoGluon train and stack its models live. The result is a leaderboard and the stacked ensemble with its weights, plus an explanation of the model. Users can also clean mislabeled rows, register the model with a model card for approval, then try it on one example or batch-score a file. Technical users get everything AutoGluon offers and can leave at any time: full config, exported Python, and plain AutoGluon predictors on disk.

## Hard requirements

1. **No loss against raw AutoGluon.** Every `fit()` argument passes through, nothing is capped, and runs are saved as plain AutoGluon predictors (`TabularPredictor.load(path)` works). A parity test proves that the same config gives the same leaderboard as raw AutoGluon.
2. **Upload → train → output works end to end in the browser.** This is the top priority. Never leave it broken.
3. **Bank use cases**:
   - Binary classification (default, churn, fraud with imbalance).
   - Multiclass (including text: AutoGluon's own n-grams, plus custom TF-IDF → LogReg (OvR) and TF-IDF → Linear SVM models).
   - Regression.
   - Time-series forecasting (`TimeSeriesPredictor`).
   - Unsupervised anomaly detection (scikit-learn IsolationForest).
4. **A powerful-looking UI in the design system.** Use dense, real mathematical notation bound to real numbers: the stacking equation with live weights ŷ = Σ wₘ fₘ(x), objectives, SHAP f(x) = φ₀ + Σ φᵢ, intervals, and shapes as spaces. Follow the README's "Show the mathematics" rules: every symbol is defined on screen, and nothing is decorative. Keep the Harmonic Thread visuals: strings for connection and progress, waves for live computation.
5. **Mock mode stays.** The UI still opens with the API down.

## Runtime

- FastAPI in `backend/app`, SQLite, a data folder, SSE with replay.
- **Each AutoGluon fit runs in its own process** (`multiprocessing` / `concurrent.futures.ProcessPoolExecutor` or `subprocess`). Its progress becomes events: model started, model finished, score, fit time, stack level, ensemble weights.
- MLflow is used as a library (local file store) for tracking and the registry.
- Optional LLM layer: the `claude` CLI (`claude -p --model claude-sonnet-5-5 --output-format json`) is tried first, then `ANTHROPIC_API_KEY`, then a fixed fallback that needs no model. The UI always says which one is running.
- AutoGluon install: `autogluon.tabular[all]` and `autogluon.timeseries` (about 6 GB with torch; already confirmed working on Python 3.13). Also cleanlab, shap and fairlearn.
- For tests, pin small, fast configs: a fixed `hyperparameters` dict and seeds. Assert score floors, not exact values, except in the parity test.

## Sessions

| # | Session | Builds | Proven end-to-end by |
| --- | --- | --- | --- |
| S1 ✓ | Skeleton + data | FastAPI, SQLite, Makefile, proxy, API client, live/mock mode, samples, profiling, splits, harness | 32 pytest + 20 Playwright, green |
| S2 ✓ | AutoGluon engine | The process-per-fit job runner and events. SSE with replay and restart recovery. `POST /api/runs` with problem type, label, metric, presets, time budget and full kwargs pass-through. Leaderboard (extra info), out-of-fold predictions, the predictor saved. Custom TF-IDF OvR LogReg and Linear SVM AutoGluon models with top words per class. Model catalogue. New samples: credit default (binary, imbalanced) and loan amount (regression). Missing labels dropped. | 69 pytest (58 fast + 11 slow) + 21 Playwright, green |
| S3 | New Model + live race | Quick Train becomes the New Model wizard: dataset or upload → label column and inferred types (editable) → problem type, metric, goal preset, budget → live race (a string per model family, the stack forming, weights) → leaderboard with score, fit and predict time → custom ensemble (`fit_weighted_ensemble` on chosen models) → first predictions. | Playwright: upload a CSV, train, see the leaderboard, get a prediction. |
| S4 ✓ | Understand | Experiments on real runs. Explain tab: feature importance, SHAP for one row, confusion matrix, ROC and PR curves, calibration, the binary threshold slider (`calibrate_decision_threshold`), regression residuals, worst errors, top words per class for text, fairness by a chosen group column (fairlearn). | Chart numbers match the API; the threshold maths match scikit-learn. 84 pytest (fast) + 15 slow + 30 Playwright, green |
| S5 ✓ | Registry + use | MLflow registry, register with `refit_full` / `infer_limit`, a generated model card, approve with an audit record, Try It with reasons, batch scoring of a CSV with download, PSI drift on a new batch. Home and status bar on real data. | Register → approve → Try It → batch score, in the browser and over HTTP. 98 pytest (fast) + 15 slow + 33 Playwright, green |
| S6 | Training Gym + Vela | The Gym as the full AutoGluon config: presets, model families, per-family hyperparameters, bag folds, stack levels, HPO, pseudo-labelling, distillation, calibration, GPU, plus a raw kwargs editor. Export as Python. A Python client package for notebooks. Vela findings computed from the run, with patches that apply in the Gym. | Apply a patch, retrain, compare in Experiments. The exported script reproduces the run. |
| S7 | Autopilot | Layer 1, a fixed strategy: baseline → cleanlab inbox (label issues, near-duplicates, outliers) → fixes → escalate preset and budget → stop on requirements. Layer 2: Claude through the CLI picks the next step and writes the findings. | Inbox recall of the planted errors (`label_noise.json`) above a floor; answering raises the score; it completes with no LLM. |
| S8 | Forecasting, anomalies, import | New Forecast flow (`TimeSeriesPredictor`, a transaction-volume sample, quantile fan chart). Anomaly detection flow (IsolationForest, a score distribution, top anomalies with reasons). Import an existing AutoGluon predictor. `make reset`. | Forecast end to end; anomalies end to end; import end to end. |
| S9 | Visual + maths pass | Screenshot every page at 1366 and 1920 widths, then raise each to its best within the design system with dense, real notation. Fix layout, empty, loading and error states. | Screenshots with no console errors; the full suite green. |
| S10 | Final end-to-end | Fresh CSVs the build never saw (generate them locally if offline). Run every flow through the browser and fix what breaks. Rewrite the README. Write the demo script. `docs/progress/S10-walkthrough.md` with screenshots. | The full suite green three times from a clean clone. |

## Rules for every session

1. Start with `git pull origin motion-components`, `make setup`, then `make test-full`. If it's red, fix that before new work.
2. Build the backend first and test it over HTTP the way the UI calls it. Then build the UI. Then add Playwright coverage. Pass `--trace off`.
3. Add tests for everything you build. Never skip, delete or loosen a test to get to green.
4. Make small commits and push only to `motion-components`. Open no pull requests.
5. Keep the design system (`src/shared/styles/tokens.css` and the README rules), mock mode, and the folder rules.
6. Keep `make test` fast (a few minutes). Put AutoGluon fits longer than a few seconds behind the slow mark, so they run in `make test-full`.
7. Finish with `docs/progress/S<n>.md`: what works, what doesn't, the test counts, and notes for the next session. Then report to the orchestrator.
