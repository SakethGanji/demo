# Build sessions, round 2: one fix per session

Round 1 (`build-sessions.md`, S1–S9) built the product. Round 2 closes its honest limits and adds what ML scientists at a bank use day to day with AutoGluon. **Each session implements exactly one fix (F1–F20), one after another on `motion-components`.** The orchestrator session starts each one, checks it, and only then starts the next.

## Rules for every F-session

1. `git pull origin motion-components`, then `make setup`. Read this file (your F-section), `README.md`, and `docs/progress/S9-walkthrough.md`. Skim the progress notes of earlier F-sessions in `docs/progress/F*.md`.
2. **Do only your fix.** If you find an unrelated bug, fix it only if it blocks you, and note it.
3. Use AutoGluon's own features first: read the installed AutoGluon 1.6.3 source or docstrings before writing your own. Pass everything through and cap nothing.
4. **Backend first, then UI.** Every number in the UI comes from the API. Stay inside the design system and the README's "Show the mathematics" rules: real notation bound to real numbers, every symbol defined on screen. Use guided defaults with an Advanced fold-out.
5. **Verification is `scripts/smoke.py`.** There is no new pytest or Playwright work. Add a section or steps for your fix that chain the real API calls the UI makes and assert the fix's acceptance criteria. Run `SMOKE_ONLY=<your section>,core make smoke` until it is green, then `npm run build`. Take a few headless screenshots of the changed pages (in `docs/progress/F<n>/`), look at them, and fix what looks off.
6. Report honestly. If an acceptance number is not reached, say so with the real number. Don't loosen the check to make it pass, but a check that depends on machine timing should test the behaviour, not a fixed ratio.
7. Make small commits and push only to `motion-components`. Open no pull requests.
8. Finish with `docs/progress/F<n>.md`: what changed, the smoke output for your section, before → after numbers, and open problems. Update the README's feature list and known limits if your fix changes them.
9. Report to the orchestrator with `send_message` (claude-code-remote), `session_id` `session_018upNdRigdGd9A2nGzFSQXq`: the head SHA, the smoke summary, before → after, and open problems.

---

## Phase 1: fix the limits from round 1

### F1 · Forecast intervals calibrated
- **Cause:** the WeightedEnsemble leans on DirectTabular (weight 0.83), whose quantiles are too wide. On the sample, the [q0.05, q0.95] interval covers 168/168 holdout points against 90% nominal. Weights are fitted on few validation windows.
- **Do:**
  - Default `num_val_windows` to 3 or more where the history allows. Offer a WQL-based or quantile-aware ensemble, and show per-model coverage.
  - Add **conformal recalibration** of the quantiles from backtest residuals (split-conformal or CQR-style scaling per quantile level, per item or pooled). It is opt-in and on by default for new runs, and it records both the raw and calibrated forecasts.
  - Show the empirical coverage against the nominal coverage, before and after, with Wilson intervals, in the existing Interval Calibration panel.
- **Acceptance:** sample holdout coverage of the 90% interval falls within [0.85, 0.95], or the reason it can't is stated with numbers. Point accuracy (MASE) is not worse by more than 2%.

### F2 · Anomaly: velocity features + supervised fraud
- **Cause:** IsolationForest scores each row alone, so bursts are invisible (2/18 found).
- **Do:**
  - When an entity id and a timestamp are mapped, add per-entity rolling features: count and sum over the last 1 h and 24 h, distinct merchants or categories in 24 h, amount ÷ the rolling mean, and the gap to the previous transaction. Compute them causally (past only).
  - Add a **"Confirm / dismiss" loop**: analysts mark flagged rows as fraud or not, and the confirmations are stored as labels.
  - Once at least N confirmed positives exist, offer **"Train supervised"**: an AutoGluon binary run on the confirmed labels with the anomaly score as a feature (positive-unlabeled framing), compared on the same hidden truth for the sample.
- **Acceptance:** sample velocity bursts found ≥ 10/18 (report the real number), overall P@90 ≥ the current 0.71, and the supervised path works end to end in smoke.

### F3 · One-row latency
- **Cause:** text GBM ensembles take 30–50 ms per single row, because every member model runs plus per-call overhead.
- **Do:**
  - Register and the Gym expose AutoGluon's `infer_limit` with `infer_limit_batch_size=1` (the ensemble is pruned to a one-row latency budget).
  - Use `refit_full`, `persist()` of the served models in the predict worker, `clone_for_deployment`, distillation into a single model, and an option to serve the single fastest model.
  - Measure p50/p95 for one row and for batches. Show the score–latency frontier and let the user pick a point on it.
  - Warm the predictor on load so the first call isn't slow.
- **Acceptance:** support-tickets can be served at ≤ 10 ms p95 for one row with the accuracy cost shown, or the real number and the reason are stated.

### F4 · Target confirmation and splits
- **Cause:** a CSV uploaded without naming its target is split on the profiler's label guess. When the user picks another target, the holdout differs (deposits: RMSE 2,434 via the API vs 4,769 in the browser).
- **Do:**
  - Splits are tied to the chosen target. When the target changes, recompute and store splits for that target, keyed by target name. Every run records which split it used.
  - Confirming the target is an explicit step in the UI.
  - API and browser give identical holdouts for the same dataset and target.
- **Acceptance:** the smoke uploads a CSV without a target, picks a non-guessed target, and trains via the API path and the UI path. The holdout row ids and scores are identical.

### F5 · LLM layer: fast and reliable
- **Cause:** each `claude` CLI call starts a process (seconds), and it timed out in the final smoke.
- **Do:**
  - In `app/llm.py`, use the Anthropic Python SDK directly when `ANTHROPIC_API_KEY` is set (model `claude-sonnet-5-5`). Fall back to the CLI, then to the computed text.
  - Run calls asynchronously, never blocking a request. Cache by (prompt hash, facts hash).
  - Make the timeouts configurable, show the decider and the latency in the UI, and keep the number guard.
- **Acceptance:** the Vela ask p95 is reported for both paths, a timeout falls back cleanly, and nothing blocks `/api/health`.

## Phase 2: bank validation essentials

### F6 · Out-of-time and group splits
- **Cause:** splits are random and stratified. Credit models are validated out-of-time, and a customer's rows must not straddle train and test.
- **Do:**
  - Split strategies: random (stratified), **group** (an entity column, using AutoGluon's `groups=` for bagging folds plus a group-aware holdout), **time** (a date column with a cut-off: train before, holdout after, tuning on the latest slice of the training period via `tuning_data`), and **time + group**.
  - The UI shows the split as a timeline or a group diagram with row counts, and warns when a random split is used on data with a date or ID column.
  - Report the score under random vs out-of-time side by side, so the optimism is visible.
  - Add a date column and a customer id to the credit-default sample, with mild temporal drift.
- **Acceptance:** on credit-default, both the random and out-of-time holdout scores are shown, and the out-of-time score is lower. The group split has zero shared ids between train and holdout, and the smoke asserts that.

### F7 · Business-cost metric
- **Do:**
  - The user enters costs (for binary: cost of FN, cost of FP, optional gain of TP; for multiclass: a cost matrix). This becomes an AutoGluon custom scorer (`autogluon.core.metrics.make_scorer`) used as `eval_metric`, so model selection, ensembling and the threshold are all chosen on expected cost.
  - Show the expected cost per 1,000 decisions with an interval, against the accuracy-optimal model, and the cost curve over the threshold.
- **Acceptance:** on credit-default with FN = 10 × FP, the cost-optimised model has a lower holdout expected cost than the ROC-AUC-optimised one (or the real numbers are reported).

### F8 · Monotonic constraints
- **Do:**
  - A per-feature control (↑ increasing, ↓ decreasing, none) in the Gym's Advanced view. Pass it to the families that support it:
    - LightGBM `monotone_constraints`
    - XGBoost `monotone_constraints`
    - CatBoost `monotone_constraints`

    Exclude or warn for families that can't honour it (NN, KNN, RF/XT), with an option to restrict the run to compliant families.
  - Verify after the fit with partial-dependence or ICE checks per constrained feature, and show them. Record the constraints on the model card.
- **Acceptance:** with debt_to_income ↑ on credit-default, the served model's partial dependence is monotone (checked numerically on a grid), and smoke asserts it.

### F9 · Quantile regression
- **Do:**
  - `problem_type='quantile'` with user-chosen `quantile_levels`, exposed in New Model (the regression → "predict a range" option).
  - Results show the pinball loss per level, interval coverage on the holdout, and a fan chart over sorted predictions. Try It returns the quantiles.
  - Registry and model card support it.
- **Acceptance:** on loan-amount, the [q0.1, q0.9] holdout coverage is reported and close to 0.8, and it works end to end in smoke.

### F10 · Reason codes (adverse action)
- **Do:**
  - For binary credit-style models: per-decision reason codes from SHAP. Map features to plain-English reason statements (an editable mapping table per model), rank the factors pushing toward the adverse outcome, and return the top 4.
  - Return them in Try It and batch scoring (extra columns). Add a model card section and a stability check: how often the top reasons change under small input noise.
- **Acceptance:** credit-default batch output carries 4 reason codes per adverse decision, and smoke asserts that they are consistent with the SHAP signs.

### F11 · Scorecard challenger
- **Do:**
  - A custom AutoGluon model `SCORECARD`: binning, then WoE, then logistic regression, with points scaling (PDO, base score/odds) and an exported scorecard table. It is registered like TFIDF_LR, and on by default for binary as the interpretable benchmark.
  - Show the scorecard table, IV per feature, and the ensemble's gain over the scorecard (ΔAUC with a bootstrap CI).
- **Acceptance:** on credit-default the scorecard trains, its table is shown and exported, and ΔAUC vs the ensemble is reported.

## Phase 3: the data-centric loop

### F12 · Learning curves ("do I need more data?")
- **Do:** fit the same config on nested subsamples (10/25/50/100%, the same holdout), run in a worker. Plot score against n with intervals, fit a power law, extrapolate to 2× and 4× the data, and give a verdict: data-limited, saturated, or noisy.
- **Acceptance:** on support-tickets and credit-default, curves and a verdict appear, and smoke asserts that the four points exist and the extrapolation is reported.

### F13 · Active-learning labelling queue
- **Do:**
  - Upload unlabelled rows, or hide a share of a sample's labels. Pick rows to label by uncertainty (margin or entropy) plus diversity (k-center or clustering on embeddings or features), with a budget per round.
  - Labelling happens in the Ground-Truth Inbox style. After each round, retrain and plot score against labels used, compared with random selection.
- **Acceptance:** on support-tickets with 90% of labels hidden, the active selection reaches a higher score than random at the same label budget after 2 rounds (report the numbers).

### F14 · Ground-truth reliability
- **Do:**
  - Support several annotators per row: upload a long-format annotations file, or collect labels from the inbox under different reviewer names.
  - Compute Cohen's and Fleiss' kappa and per-annotator quality, and use `cleanlab.multiannotator` for a consensus label with a quality score.
  - A disagreement queue for adjudication.
  - Add a sample with simulated annotators of differing quality.
- **Acceptance:** on the sample, the worst annotator is identified correctly and the consensus labels beat majority vote against the truth (report the numbers).

### F15 · Adversarial validation (shift check)
- **Do:**
  - Given the training data and a new batch, or train vs holdout, or two time periods: an AutoGluon classifier separates them. Report the AUC with a verdict (≈0.5 means no shift; high means shift) and the top shifting features.
  - Offer actions: out-of-time validation (F6), drop or transform the feature, or importance weighting via density ratio (p_new/p_train as `sample_weight`).
- **Acceptance:** a shifted batch gets AUC > 0.7 with the injected feature ranked first, and an unshifted batch gets AUC ≈ 0.5.

### F16 · Weak-slice discovery
- **Do:** search the holdout automatically for feature-value slices (single features and pairs, numeric binned) whose error is significantly worse than overall, with a minimum support, a multiple-testing correction, and the effect size. Rank them and link each to its rows, SHAP, and a targeted-labelling action (feeding F13).
- **Acceptance:** a planted weak slice in a sample is found at rank 1–3.

## Phase 4: Model Doctor

### F17 · Diagnosis → prescription
- **Do:** after every run, a fixed decision procedure combines:
  - the train–validation gap
  - the learning curve (F12)
  - the cleanlab noise rate
  - adversarial AUC (F15)
  - weak slices (F16)
  - the latency budget (F3)
  - the split type (F6)
  - leakage checks

  It names the **single most likely limiting factor** with its evidence numbers and a one-click prescription (a Gym patch, the labelling queue, an out-of-time split, …). Show it in Vela, the results pages and Autopilot. Autopilot layer 1 uses it to pick its next step.
- **Acceptance:** crafted scenarios are each diagnosed correctly: leaky feature, label noise, data-limited, shift, overfit, latency. Smoke runs the scenarios.

## Phase 5: Monitoring

### F18 · Monitoring over time + champion/challenger
- **Do:**
  - Scored batches carry a date, giving time series of PSI per feature, the score distribution, and (when labels arrive later) performance with a lag.
  - Alert thresholds and a monitoring page per production version.
  - Champion/challenger: shadow-score batches with a staging version and compare.
  - A retrain trigger proposes a new run on recent data with the same config.
  - Add a simulated 12-month sample stream with drift starting in month 7.
- **Acceptance:** the stream shows the drift alert from month 7, the challenger comparison works, and the retrain proposal launches a run.

## Phase 6: integration

### F19 · Integrate and polish
- **Do:**
  - A visual pass over every page touched in F1–F18 at 1366 and 1920 widths.
  - Make sure the new features appear in New Model, Gym, Explain, Registry, model card and Vela consistently.
  - Update `docs/demo-script.md` with the new bank story: an out-of-time split, cost metric, monotone constraints, reason codes, scorecard challenger, learning curve, and Model Doctor.
  - Update the README.
- **Acceptance:** the full `make smoke` is green, the build passes, and the screenshots are in `docs/progress/F19/`.

### F20 · Final verification
- **Do:**
  - Run the full `make smoke` from a clean clone with a fresh data folder, twice.
  - Walk the new demo script in a headless browser.
  - Fix what breaks.
  - Write `docs/progress/F20-final.md` with the final feature matrix (use case → status → evidence number) and the remaining limits.
