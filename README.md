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
| **Binary classification · fraud, default, churn** | Imbalance handled by default: ROC AUC (or average precision) as the metric for an imbalanced label and the decision threshold calibrated for F₁ with AutoGluon's `calibrate_decision_threshold`. Explain's Threshold tab moves t over the holdout with precision, recall, F₁, MCC, FPR, the 2×2 counts and the cost C(t) = c_FP·FP + c_FN·FN against the Bayes threshold. Fairness by any column (fairlearn: demographic parity, equalised odds). **Business cost** (F7): enter c_FP, c_FN and an optional gain g_TP (or a K × K matrix for multiclass) and it becomes AutoGluon's `eval_metric` (a `make_scorer` in `ensemble_models.cost`), so models, ensemble weights and (binary) the threshold are chosen on expected cost; Results shows the cost per 1,000 decisions with a bootstrap interval, the cost curve over t and a paired comparison with the same fit trained to ROC AUC and accuracy. **Monotone constraints** (F8): in the Gym's Advanced view (Models step) set ↑ / ↓ / none per numeric feature; LightGBM, XGBoost and CatBoost are trained as their monotone subclasses (`ensemble_models.monotone`: GBM_MONO, XGB_MONO, CAT_MONO), which map each constraint onto the columns AutoGluon actually gives the model and constrain stack inputs to +1 so bagged, stacked and weighted ensembles stay monotone; other families are left out (default) or kept and reported. Explain → Monotonicity draws ICE curves and partial dependence on raw holdout rows for any model, and the model card records the constraints with the served model's check. **Reason codes** (F10): for every adverse decision of a registered binary version (ŷ = the adverse class at the version's own threshold, so F7's cost threshold), the top 4 features with s·φⱼ > 0, ranked, as codes and plain-English statements from a mapping table edited per model (Registry → Reason Codes, audited). φ is SHAP of the served model against approved reference applicants: exact interventional TreeSHAP when the served model is (bagged / weighted) LightGBM on raw numeric columns, else a vectorised Kernel SHAP (B = 16, M = 128, ~60 rows/s on credit-default's ensemble in a worker). Returned by Try It, `/score` (`reasons=true`) and batch scoring (`adverse`, `reason_factors`, `reason_k_code`, `reason_k`, `reason_k_feature`, `reason_k_phi` columns); a stability check (input noise ε·σ) and a model card section. A monotone-constrained feature (F8) is always cited in its constrained direction. **Scorecard challenger** (F11): every binary run also trains `SCORECARD` (`ensemble_models.scorecard`, registered like TFIDF_LR; level 1 only): binning on each fold's training rows (monotone event rate, ≥ 5 % of rows per bin, missing values in their own bin, rare levels as "other") → weight of evidence → logistic regression with every coefficient ≤ 0 → points (PDO 20, 600 at 50 : 1 by default; set in the Gym or `scorecard = {pdo, base_score, base_odds, max_bins, …}`, `false` to switch it off). Explain → Scorecard shows the deployable scorecard (refitted on all training rows) with points per bin, IV per feature, rank ordering by score band, ΔAUC of the served model over it with a paired bootstrap CI, an applicant's points and the scorecard's own reason codes (points lost against each feature's best bin); the table exports as CSV and the model card carries the benchmark. With F8 constraints it bins in the constrained direction, so it is monotone by construction; with an F7 cost its expected cost per 1,000 decisions is shown next to the served model's. |
| **Multiclass, including text · complaint and ticket routing** | Free-text columns detected and given to AutoGluon's n-gram features, plus two custom AutoGluon models (TF-IDF → one-vs-rest logistic regression and TF-IDF → linear SVM) with their top words per class. Per-class recall and precision, one-vs-rest curves. Vela Autopilot audits the labels with cleanlab and asks you only about the rows it cannot decide. |
| **Regression · balances, loan amounts** | RMSE / MAE / R², residuals against ŷ with ±1.96 σ, the residual histogram with its normal fit, the largest errors with SHAP for each. **Predict a range** (F9): New Model → Target → "a range" makes it AutoGluon quantile regression (`problem_type='quantile'`, levels τ chosen as 80 / 90 / 50% ranges or typed in Advanced, scored by the pinball loss). Results shows the pinball loss and F̂(τ) per level, every interval's holdout coverage with a Wilson interval, raw and conformally calibrated (AutoGluon's own CQR, `fit(calibrate=…)`, opt-in: on / off / AutoGluon's auto), the fan chart of the holdout sorted by q̂₀.₅, and every model's coverage. Try It, `/score`, batch scoring (`q_τ` columns), SHAP (of any one level), fairness (coverage per group), the registry and the model card serve the quantiles. |
| **Time-series forecasting · transaction volumes** | `TimeSeriesPredictor` on long-format data with item id, timestamp, target, known covariates (holidays, paydays), static and past covariates; the last H steps held out; leaderboard, backtest windows, ensemble weights q̂ = Σ wₘ q̂⁽ᵐ⁾, the fan chart, per-series MASE/WQL/sMAPE/bias, and **interval calibration**: empirical against nominal coverage per quantile with Wilson intervals, before and after **conformal recalibration** (q̃_τ = m + c_τ(q̂_τ − m), c_τ from the backtest forecasts; on by default, raw and recalibrated both kept), per-model coverage, backtest windows W auto (≥ 3 where the history allows) and a quantile-aware ensemble (weights per model and quantile, pinball loss) beside the greedy one. |
| **Anomaly detection · card transactions** | IsolationForest (and LOF) without labels: s(x, ψ) = 2^(−E[h(x)]/c(ψ)), the score distribution with the contamination threshold s*, TreeSHAP reasons per flagged row, and **group-relative features** when an entity column is mapped: an amount divided by the card's own mean, its z-score within the card, and the time since the card's previous transaction. **Velocity features** (F2) from the card's earlier rows only: counts and sums in the last 1 h and 24 h, distinct merchant categories in 24 h, amount ÷ the running mean, the gap to the previous transaction. **Confirm / dismiss** flagged rows (labels kept with the dataset, audited), then **Train supervised**: an AutoGluon binary run on the confirmations (positive-unlabeled, anomaly score as a feature), compared with the forest out-of-fold on the rows nobody reviewed. |
| **AutoGluon in full · the Training Gym** | Presets, model families, per-family hyperparameters, bag folds k, bag sets s, stack levels L, HPO, pseudo-labelling, distillation, `refit_full`, `infer_limit`, sample weights, a raw `fit_kwargs` editor; the resolved call as a diff against `fit()`'s defaults; the fit-time estimate T̂; export as Python. |
| **Target confirmation and splits** | Confirming the column to predict is an explicit step (New Model → Target, `PATCH /api/datasets/{id}`). The holdout and folds belong to the target: one split per (target, strategy), drawn the first time it is used and never redrawn; every run records the key and SHA-256 of its split, so the browser and the API give identical holdouts for the same file and target, whatever the profiler guessed (F4). |
| **Out-of-time and group splits** | Four split strategies (F6): random (stratified), **group** (an entity column: holdout, tuning slice and the 5 bagging folds share no customer; the folds go to AutoGluon as `groups=`), **out-of-time** (a date column and a cut-off τ: holdout = rows on or after τ, `tuning_data` = the latest slice of the training period) and **out-of-time + group** (only customers new after τ in the holdout). New Model → Target draws the split as a timeline or a group diagram with its row counts, and warns when a random split is used on data with a date or ID column. Results → Random vs Out-Of-Time re-runs the same fit under another split and shows the scores side by side with bootstrap intervals: on credit-default, ROC AUC 0.772 random vs 0.714 out-of-time. |
| **Learning curves · do I need more data?** | Explain → Learning Curve (F12): the run's exact fit call (presets, hyperparameters, the F7 cost metric, F8 monotone subclasses, the F11 scorecard, the F6 tuning slice and group folds, text models, threshold rule) refitted on nested subsamples of its training rows, 10 / 25 / 50 % × 3 seeds (the run itself is 100 %), in a worker process, every fit scored on the run's holdout. Subsamples are drawn inside the run's split: stratified by the label where the split is, whole entities inside each bagging fold for group splits. Score against n on a log₂ axis with t-intervals, a power law u(n) = A − b·(n/N)^(−c) with a bootstrap band, extrapolation to 2N and 4N with 95 % intervals, and a verdict: **data-limited** (the interval of the gain per doubling Δ₂ = b(1 − 2^(−c)) lies above ε), **saturated** (below ε) or **noisy** (contains ε); ε defaults to 2.5 % of the remaining gap to the metric's optimum. The verdict is a stable JSON shape (`learning_curve/1`) on the run payload for the Model Doctor. |
| **Active-learning labelling queue** | Explain → Labelling Queue (F13), classification runs: hide a share of the training labels (default 90 %, stratified) or use the dataset's rows with an empty label. The run's exact fit call (F12's `prepare_fit` / `fit_predictor`) is refitted on the labelled set; each round picks b rows (default 200, 2 rounds) by uncertainty (margin, entropy or least-confident) among the β·b most uncertain, spread by weighted k-means or k-center on TF-IDF→SVD / one-hot embeddings. You label them in the Ground-Truth Inbox style (confirm / relabel / skip, with a reviewer name); each submitted round becomes a dataset version with lineage (the same split, holdout untouched) and a refit. Score against labels used; with hidden labels, a **simulated** active-vs-random comparison (the hidden labels as an oracle, 3 seeds, equal budgets) with the paired gap's 95 % t-interval and a verdict. Summary JSON `active_learning/1` on the run payload for the Model Doctor. |
| **Ground-truth reliability · several annotators** | Explain → Annotators (F14), classification runs: load a long-format annotations file (row or an ID column, annotator, label; holdout rows left out) or, on support-tickets, five **simulated** annotators of different quality (one careless, one swapping Billing and Account). Cohen's κ for every pair and Fleiss' κ (variable raters per row) with bootstrap intervals; each annotator's quality, weight and most frequent disagreement with the consensus (a systematic bias shows without any truth); the **CROWDLAB** consensus label per row with its quality score (`cleanlab.multiannotator`), from the annotators and the run's out-of-fold p̂ (its own OOF when bagged, else a quick bagged fit of its call through F12's `prepare_fit` / `fit_predictor`), with the posterior p̃ and its weights on screen. A disagreement queue (lowest consensus quality first) to vote under a reviewer name or adjudicate; the final labels saved as a dataset version with lineage (holdout untouched). On the sample, against the hidden truth: the worst annotator is found, consensus 0.993 vs majority vote 0.903. Summary JSON `annotations/1` on the run payload for the Model Doctor. |
| **Adversarial validation · has the data shifted?** | Explain → Shift (F15), tabular runs, and Registry → Batch & Drift: an AutoGluon classifier (`TabularPredictor(problem_type='binary', eval_metric='roc_auc')`, bagged LightGBM, k = 5, no stacking; `fit_kwargs` pass through) learns to tell reference rows R from new rows N, and every row is scored **out of fold** (`predict_proba_oof`), so the AUC is not optimistic. Three comparisons: the run's training rows vs its holdout; two periods on F6's date column (before / on or after a cut-off τ); the training rows vs a batch scored by a registered version (the AUC shown next to the batch's PSI). Reported: AUC with a 95 % DeLong interval, a label-permutation null (B = 2,000 shuffles of the origin labels against the fixed scores → p) and a refit null (the whole fit repeated R = 3 times on shuffled origins), a verdict (no shift / slight / moderate / strong), and the shifting columns ranked by AutoGluon's permutation importance on the out-of-fold rows (ΔAUC with a t-interval) beside each column's PSI. The label, identifiers, entity ids and dates are left out and listed with the reason (a date separates two periods by construction: put back on credit-default, AUC = 1.000). Actions: an out-of-time twin (F6), a Gym patch dropping a shifting column, and **importance weighting**: w(x) = ŝ/(1 − ŝ) · n_R/n_N, clipped at the 1st / 99th percentiles, saved as column `shift_weight` of a dataset version and passed as `sample_weight`, with its effective sample size (Σw)²/Σw². On credit-default: train vs holdout AUC 0.495, an unshifted batch 0.498, the same batch with debt_to_income × 2 0.766 with debt_to_income ranked first. Summary JSON `shift/1` on the run payload for the Model Doctor. |
| **Explainability** | Performance, Threshold, Features & SHAP (permutation importance with 95% t-intervals, Kernel SHAP waterfall f(x) = φ₀ + Σ φᵢ), Errors, Fairness, Text: every number from the saved predictor and the fixed holdout. |
| **Registry, model card, approvals** | MLflow-backed registry; register with `refit_full`, `infer_limit` (per row in batches, or **per one-row call with `infer_limit_batch_size = 1`**), **serve the fastest model**, **distillation** into a single student, or a model picked on the **score · latency frontier** (one-row p95 τ₉₅ **as served**: AutoGluon's feature pipeline pruned to what the model reads, models persisted; F3). The deployed copy's pipeline is pruned when its holdout predictions are unchanged, versions are persisted and **warmed on load** (on build and on promotion), and `POST /registry/versions/{id}/score` is the online path (support tickets: p95 4.4 ms one row, 6.9 ms over HTTP); an SR 11-7-style model card (JSON, Markdown, HTML); four-eyes review; stages None → Staging → Production → Archived; Try It with SHAP reasons; batch scoring with PSI drift for every kind of version; an append-only audit log with the model's SHA-256. |
| **Vela, the AI companion, and Autopilot** | Findings computed from a run (overfitting gap, weak class or segment, imbalance, leakage, budget, drift), each with one patch that applies in the Gym; a question box. Autopilot chains real runs against requirements you set (F̄₁ ≥ 0.92, τ₉₅ ≤ 50 ms, a budget B): baseline → cleanlab inbox → a new dataset version → retrain → rebalance, calibrate, refit for latency, escalate → Met or Not Met. Claude (Anthropic SDK when `ANTHROPIC_API_KEY` is set, else the CLI) chooses steps when available; a fixed strategy otherwise, and the UI says which. Every answer shows its decider, latency, route and number guard; Settings → Language Model shows τ₉₅ per decider, the cache and the timeouts (F5). |

## Quickstart

```
make setup       # Python venv (backend/.venv) with the pinned deps (AutoGluon tabular + timeseries, cleanlab, shap,
                 # fairlearn, MLflow), the Python client, and npm install
make dev         # API on :8000 and the UI on http://localhost:5190; Ctrl+C stops both
make smoke       # scripts/smoke.py: the UI's real calls end to end against the API (started if not running);
                 # ✓ / ✗ per step, exit 1 on any failure. SMOKE_ONLY=core,…,scorecard,learning,active,annotators,shift,llm,reset
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
                                                  └─ LLM router (app/llm.py, F5): Anthropic SDK → claude CLI → computed text, async,
                                                       cached by (prompt hash, facts hash), timeouts in Settings
```

- **No loss against raw AutoGluon.** `fit_kwargs` pass to `fit()` unchanged; the run's `resolved` field is the exact
  call; parity tests prove raw AutoGluon gives the same leaderboard; `GET /api/runs/{id}/export.py` reproduces it.
- **One fixed holdout per (target, split strategy)** (20%; random-stratified by default, or group / out-of-time /
  out-of-time + group, F6), plus five folds. Every model, run, version and batch on that dataset is scored on the same rows, so comparisons are like
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

- **Forecast intervals are recalibrated, not re-modelled** (F1). AutoGluon's ensemble bands on the transaction-volume
  sample are still too wide (168/168 inside the nominal 90% interval; DirectTabular carries most of the weight); the
  default conformal step scales them to 152/168 = 90.5% (Wilson 85.1–94.1%) with one c_τ per quantile level from the
  backtest forecasts. The guarantee is marginal (over all series and steps), assumes the backtest windows look like
  the future, and the c_τ are fitted on the same windows that weighted the ensemble.
- **Anomaly detection is still unsupervised first** (F2). Velocity features and ψ = min(1024, n) take the card sample's
  bursts from 2/18 to 13/18 found and P@90 from 0.71 to 0.80 (AUC 0.990 → 0.996), but amount spikes drop from 8/18 to
  7/18 and night/foreign from 18 to 16: the forest weighs every column equally, so each new signal dilutes the others.
  It sees one column per velocity signal for that reason (all seven: P@90 ≈ 0.70). The supervised path is better on
  the rows nobody reviewed (top-18: 12 vs 6 of 18 hidden frauds, AP 0.24 → 0.72) but learns only the kinds analysts
  confirmed, and its reviewed rows were picked by the forest's score (s alone nearly predicts ŷ; Vela flags it as a
  leakage suspect). Its model needs the derived columns, so registering it does not score raw transactions yet.
- **One-row latency costs accuracy on text** (F3). AutoGluon's feature pipeline alone (~1,200 n-gram and text
  statistics columns) takes ~10–13 ms per one-row call on 4 CPUs, so no model that reads the n-grams is served under
  10 ms (LightGBM / XGBoost ≈ 13–20 ms as served, the ensemble 18–450 ms by its members). Under 10 ms the support-ticket
  version serves TF-IDF → logistic regression on the raw text with the pipeline pruned to that one column: τ₉₅ 4.4 ms
  (HTTP 6.9 ms) for Δaccuracy −0.030 against the ensemble. AutoGluon's own fit-time `infer_limit` with
  `infer_limit_batch_size = 1` subtracts the pipeline time first and refuses a 10 ms limit on this data. Distillation
  (soft labels, augmented rows) gave a worse student here (LightGBM_DSTL 0.894–0.902 vs 0.913 for TF-IDF and 0.943
  for the ensemble). Latency numbers are this machine's and vary ±30% run to run.
- Random splits are stratified on the target when every class has ≥ 6 rows; a numeric target gets a plain random
  holdout. Group and out-of-time splits (F6) are opt-in: the wizard warns but keeps random as the default. Under the
  time split, bagged fits (when a preset or `num_bag_folds` turns bagging on) still draw AutoGluon's own random folds
  inside the training period; only `tuning_data` (ensemble weights, early stopping) is forward in time. Group splits
  always bag (AutoGluon's `groups=` implies it), so they take about 5× the fit time of a non-bagged preset. On
  credit-default the random vs out-of-time gap (Δ ROC AUC ≈ 0.06) is about the width of each holdout's 95% bootstrap
  interval; the two holdouts are different rows and cannot be paired. Explain, the model card and drift still treat
  every non-holdout row as training data, including tuning rows and the rows a time + group split excludes.
- **Business cost** (F7). On credit-default with FN = 10 × FP the cost-optimised run costs 478.3 per 1,000 decisions
  [419.1, 545.0] on the holdout against 553.8 for the ROC-AUC-optimised twin at its F₁-calibrated threshold (paired
  Δ = +75.4 [+12.1, +139.6]). Most of that saving is the threshold: calibrated for the same cost on its own validation
  data, the ROC-AUC twin costs 496.7 (Δ = +18.3 [−24.6, +62.5], within the noise). Binary models are ranked during the
  fit by deciding at the Bayes threshold t*, which assumes roughly calibrated probabilities (AutoGluon's temperature
  scaling is skipped under 3,000 validation rows). Multiclass predictions are still served as the arg-max: AutoGluon
  has no cost-aware multiclass decision rule, so the Bayes decision is only reported. A saved cost predictor needs
  `ensemble_models` importable to load (as the TF-IDF models do).
- **Monotone constraints** (F8). Numeric features of binary and regression runs only: a category has no order, AutoGluon
  turns text and dates into other features, and a softmax couples multiclass probabilities. Only LightGBM, XGBoost and
  CatBoost honour them; RF, XT, KNN, LR, the neural nets and the TF-IDF models are left out by default (on
  credit-default, leaving out LR costs ROC AUC 0.7743 → 0.7568 unconstrained; the constraint itself 0.7568 → 0.7557,
  within the noise). With "compliant families only" off, those families stay and the served model is monotone only if
  the ensemble gives them no weight: the run's report and Explain say which models break it. Distillation (in the run
  or at registration) would serve an unconstrained student and is refused. The guarantee is for raw values the model
  saw as numbers: a two-valued column becomes 0/1 in AutoGluon (larger value = 1), and an unseen third value maps to 0.
  The check is ICE on a grid of the column's quantiles (25 points, ε = 10⁻⁹), not a proof between grid points; tree
  constraints are exact, so a violation there would show on the grid.
- **Quantile regression** (F9). Numeric labels only; scored by the pinball loss (AutoGluon's only quantile metric).
  Refused with a 400 and the reason: a business cost, a decision threshold, monotone constraints, distillation (in the
  run or at registration) and pseudo-labelling. Explain's residual views and the drift PSI of ŷ read the centre level
  q̂₀.₅; Explain's SHAP tab explains q̂₀.₅ (`GET /runs/{id}/shap?target=0.9` and Try It explain any level). Conformal
  recalibration is AutoGluon's CQR on the served model only: a constant shift c_τ per level fitted on the validation
  (or out-of-fold) rows, so it fixes average coverage, not coverage per segment (Fairness shows coverage per group);
  `calibrate='auto'` turns it on only with ≥ 1,000 validation rows, and AutoGluon may still leave crossing quantiles
  (reported as the crossing share). On loan-amount (medium_quality, 120 s) the served ensemble includes neural nets,
  so a one-row call takes ~200 ms; register with "serve fastest" for a single-model version.
- **Reason codes** (F10). Binary versions only. The kernel estimator samples coalitions: on credit-default its top-4
  set equals that of the same game estimated with 4,096 coalitions on 84% of adverse rows (mean overlap 0.96, every
  cited feature has reference φ > 0); TreeSHAP is exact but applies only to (bagged / weighted) LightGBM on raw numeric
  columns (shap mishandles LightGBM's categorical splits: checked by additivity, with Kernel SHAP as the fallback). An
  adverse row with fewer than 4 features pushing toward the adverse class gets fewer codes (2 of 517 on the credit
  holdout; 37 of 530 for the 8-feature LightGBM). Small input noise (ε = 0.05·σ) keeps the top-4 set on ~80% of rows
  and the first reason on ~95%. Statements are templates (high / low by the reference median, or the category's
  value); a compliance team should review them before use in notices.
- **Scorecard challenger** (F11). Binary runs only. The in-run model is bagged (one set of bins per fold child), so the
  table shown and exported is the deployable refit on all training rows, scored on the holdout beside it (both AUCs
  are reported). Bins are on AutoGluon's features: a two-valued number appears as 0/1, categories are shown by their raw
  level, and dates, text and stack features are not binned. Unseen categories (and missing values when training had
  none) score WoE 0. Binning is greedy (quantile fine bins, then merges), not an optimal binning solver, and an
  ordinal category is binned by event rate, not by its order. The scorecard's reason codes explain the scorecard, not
  the served model (that is F10).
- **Learning curves** (F12). On credit-default (group split, cost FN = 10 × FP, monotone, scorecard) and support-tickets the
  verdict is *noisy* with 3 seeds: the seed spread at 50 % (σ ≈ 0.02 in cost, 0.002 in accuracy) is as large as the expected
  gain of a doubling. The intervals cover which rows a fit sees (seed spread, corrected by √(1 − f) because seeds share one
  pool of N rows); the 100 % point is the run itself (one fit) and is given the largest subsample's spread, an assumption.
  The holdout's own sampling error is common to every point and is not in the intervals. Extrapolation assumes the new rows
  look like the old ones (a time split's future may not). Time splits draw random rows inside the training period; the
  tuning slice is kept whole. One curve runs at a time across the workbench (409 otherwise), beside any fit in progress;
  with a time limit, small subsamples get relatively more time per row than the run did.
- **Active learning** (F13). On support-tickets with 90 % of the labels hidden, active selection scores above random after
  2 rounds of 200 in every run we made (mean gap +0.003 to +0.008 accuracy over 3 seeds), but the 95 % interval of the gap
  always contains 0: one refit's accuracy moves by up to ±0.006 between seeds and processes, as much as the effect. The
  comparison is labelled by an oracle (the sample's hidden labels), not by people; the sample's 300 planted label flips stay
  in the oracle. Pool = missing (really unlabelled rows) cannot be compared with random. Rows from a separate unlabelled file
  are not accepted: put them in the dataset with a blank label. One queue works at a time (409 otherwise).
- **Annotators** (F14). On the support-tickets sample the annotators are simulated (each label drawn from the hidden truth
  with a fixed error profile; errors are independent between annotators), so the consensus-vs-majority-vote gain (0.993 vs
  0.903 on 1,500 rows × 3 labels) is what CROWDLAB does on such data, not a measurement of real people. Much of the gain
  comes from the model: its weight w_M ≈ 1.58 is larger than any annotator's, and p̂ was trained on the dataset's own labels
  (98.5 % right on these rows), so where the dataset's labels are themselves wrong the consensus leans on a model that
  learnt them (out of fold, so not the row's own label). Majority-vote ties (116 of 1,500 with 3 labels) are broken at random;
  cleanlab's own majority vote breaks them with the model (0.963). Only training rows count; annotations on holdout rows are
  dropped. An annotator with fewer than 30 labels is shown but not ranked as the least reliable. F13's queue answers are
  not imported automatically: upload them, or vote in the disagreement queue under a reviewer name.
- **Shift checks** (F15). Adversarial validation sees covariate shift p(x) only: a change in p(y | x) with the same inputs (credit-default's drift is mostly that: the 2024 periods give AUC 0.525, "slight") is invisible to it, and the model's own score on recent labelled rows (F6's out-of-time split) is the check for that. The importance interval treats the shuffles × fold models as independent and is narrow; without a shift its lower end can lie above 0 for a noise model, so a column is marked as shifting only when the sets are separable. Identifiers that AutoGluon drops by itself (unique strings) would not have separated the sets anyway, but numeric sequence ids and dates would. The density-ratio weights are only as good as the classifier's calibration (bagged LightGBM, not recalibrated), and clipping trades bias for variance: on the × 2 batch the weights keep ESS 4,290 of 9,600 rows (44.7 %) and move the training mean of debt_to_income from 0.271 to 0.393 of the batch's 0.536, not all the way. The batch comparison's reference is every non-holdout labelled row (the drift reference), tuning rows included. One check runs at a time across the workbench (409 otherwise); a new check of the same pair replaces the old one.
- **The Claude CLI path still costs seconds per answer** (F5). Each ask starts a `claude` process: on this machine a
  Vela ask takes p50 ≈ 7 s, p95 13–20 s (4 at a time; the API time inside is 1.3–2.5 s). Asks no longer hold a request
  thread (`/api/health` max under 48 asks in flight: 5,088 ms before, < 80 ms after), a repeated question about the
  same facts is answered from the cache in < 0.1 ms server-side, and a timeout falls back to the computed text within
  its budget. The fast path is the Anthropic SDK, used when `ANTHROPIC_API_KEY` is set; it was verified here only
  against a stand-in Messages API (scripts/fake_llm.py), not the real one, so its real latency is not measured yet.
  The cache lives in the API process (lost on restart; at most 512 answers).
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
│   ├── splits.py        the holdout and 5 folds every model uses, one per (target, strategy[, parameters]): random, group, time, time + group (F4, F6)
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
│   ├── vela/            S6: findings.py (computed findings with patches), wording.py (via app/llm.py: SDK → CLI → computed, number guard), api.py
│   └── registry/        S5: model registry and use
│       ├── api.py       /api/registry/…, /api/runs/{id}/register, /api/runs/{id}/latency, versions/{id}/score|benchmark
│       ├── store.py     registered models, versions, the audit log and batch jobs in SQLite
│       ├── build.py     build a version in a worker: clone, refit_full, distill, infer_limit / serve fastest,
│       │                  clone_for_deployment + pruned pipeline, measure (one row, batches, cold vs warm), hash
│       ├── fastpath.py  F3: prune AutoGluon's feature pipeline to a model's inputs, warm, one-row and batch timing, frontier
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
| `POST /api/forecast/runs` | `{dataset_id, mapping: {item_id, timestamp, target, known_covariates, static_features, past_covariates}, prediction_length, freq?, eval_metric, presets, time_limit, quantile_levels, num_val_windows? (none = auto), ensemble (greedy · quantile · both), conformal (default true), conformal_scope (pooled · item), hyperparameters?, fit_kwargs, predictor_kwargs}`. The last H steps of each series are held out; the predictor is fit on the rest. Result: leaderboard (val and holdout), ensemble weights, backtest windows scored per model, holdout metrics and per-series errors, the future forecast (known covariates from the file's future rows, else one season back); with conformal on, `holdout.conformal` (c_τ, n), `holdout.calibration_raw` beside `holdout.calibration`, `metrics_raw`, per-model coverage, and `forecast.csv?which=future_raw|holdout_raw` |
| `GET /api/forecast/runs/{id}/chart` · `…/forecast.csv?which=future\|holdout` · `POST …/predict` (multipart history CSV) | Fan-chart data per series · the forecast as CSV (mean + every quantile) · a forecast from new history with the saved predictor |
| `POST /api/anomaly/runs` | `{dataset_id, features?, contamination, n_estimators, max_samples, max_features, lof, lof_neighbors, iforest_kwargs}`; scores, threshold, distribution, TreeSHAP reasons for the top rows, precision@k against `anomaly_truth.json` when the dataset is the sample |
| `GET /api/anomaly/runs/{id}/top?contamination=&limit=` · `…/scores` · `…/scores.csv` · `POST …/score` (multipart CSV) · `POST …/score-one` | Top rows with reasons and flags at any contamination · histogram, quantiles, IF vs LOF · every row's score · a new batch scored and downloadable · one row |
| `POST /api/runs/import` (multipart: `dataset_id`, `file` = zip or `path`) | Import a TabularPredictor as a run (validated in a worker; scored on the dataset's holdout; not refit) |
| `POST /api/admin/reset` | `{"confirm": "reset"}`: `make reset` |
| `GET/POST /api/anomaly/runs/{id}/labels` | The dataset's reviewed rows; POST `{items: [{row, label: fraud \| not_fraud \| null}], by}` confirms, dismisses or clears (stored in `anomaly_labels.json`, audited as `anomaly_review`). `can_train` once ≥ 10 confirmed. |
| `POST /api/anomaly/runs/{id}/supervised` · `GET …/supervised` | `{time_limit, presets, eval_metric='average_precision', num_bag_folds=5, negatives='unlabeled' \| 'dismissed_only', include_score, fit_kwargs}`: a new dataset version (data + group/velocity columns + s + `confirmed_fraud`) and an AutoGluon binary run on it. GET lists them with the comparison against the forest on unreviewed rows (out-of-fold scores; hidden truth for the sample). |
| `GET /api/anomaly/mapping/{dataset}` | Suggested `entity_column`, `time_column`, `relative_columns` for group-relative features (the sample's spec, else names and types). `POST /api/anomaly/runs` takes `group_features` (default: on when an entity column is mapped; on for the card sample), `entity_column`, `time_column`, `relative_columns`, and (F2) `velocity_features` (default: on with entity and time), `amount_column`, `category_column`, `velocity_in_forest`; `max_samples` defaults to min(1024, n). The mapping also suggests the amount and category columns. |
| `GET /api/runs?kind=all` | Every run with its `kind` (tabular, import, forecast, anomaly): what Experiments and Home list. |

### Autopilot API (S7)

| Call | What it does |
| --- | --- |
| `POST /api/autopilot` | Brief: `dataset_id`, `label`, `requirements` (`[{metric, op, value}]`: f1_macro, accuracy, balanced_accuracy, mcc, roc_auc, f1, r2, rmse, mae, `recall[<class>]`, `p95_latency_ms`, `p50_latency_ms`), `budget_seconds`, `autonomy` (`ask` waits for the inbox; `auto` accepts suggestions with c ≥ `auto_confidence`), `decider` (`auto`: Claude then layer 1; `layer1`: fixed strategy only). Runs in a thread of the API, calling the API over HTTP; resumes after a restart. |
| `GET /api/autopilot[/{id}]` | State: steps (each run with its body, scores on its own and on the current labels, p95 latency, kept or dropped, which layer decided), decisions, the log in Vela's voice, dataset versions, budget, verdict (Met / Not Met with every check). |
| `GET /api/autopilot/{id}/inbox` | The Ground-Truth Inbox: label issues (cleanlab `find_label_issues` on out-of-fold and held-out p̂, ranked by m(x) = p̂_y − max_{k≠y} p̂_k), exact duplicates, near-duplicate conflicts, outliers (`OutOfDistribution`), each with text, given / suggested label, confidence and why; recall of `label_noise.json` when the dataset has one. |
| `POST …/inbox/answer`, `…/inbox/bulk`, `…/resume`, `…/cancel` | Answer rows (confirm, relabel, drop, skip), bulk-accept above a confidence, submit and continue (a new dataset version with lineage and the same holdout rows), stop. |
| `POST /api/vela/ask` | Vela's question box: `{question, run_id?, autopilot_id?, timeout?, cache?}` answered from the computed facts by the Anthropic SDK (with `ANTHROPIC_API_KEY`), then the claude CLI, else `answer: null` with a message. Returns `decider`, `seconds`, `attempts` (the route), `cached`, the prompt and facts hashes and `guard` (numbers in the answer that the facts do not contain). |
| `GET /api/llm` · `PUT /api/llm/settings` · `POST /api/llm/cache/clear` | F5: the router's route, SDK / CLI availability, p50 / p95 per decider, cache counts; timeouts (`sdk_timeout`, `cli_timeout`, `total_timeout`), `cache_ttl`, `model`, `enabled`, `use_sdk`, `use_cli` (null restores the default; stored in `data/llm_settings.json`). |

### Engine API

| Call | What it does |
| --- | --- |
| `GET /api/models` | The catalogue: `key` (AutoGluon hyperparameters key), `name`, `kind`, `description`, `objective` (Unicode maths), `objective_tex`, `symbols` (every symbol defined), `problem_types`, `custom`. |
| `POST /api/runs` | `{dataset_id, label?, problem_type: auto\|binary\|multiclass\|regression\|quantile, eval_metric?, presets='medium_quality', time_limit=120, fit_kwargs={}, predictor_kwargs={}, custom_text_models=true, drop_columns?, feature_importance=true, column_types?}` → 201, the run (queued). `fit_kwargs` go to `TabularPredictor.fit` unchanged. The run trains and is scored on the split of its `label` (F4; recorded as `split` = key, strategy, SHA-256, holdout rows), drawn on first use if that label was never confirmed. `split_spec` (F6) picks the strategy: `{strategy, group_column, time_column, cutoff, tuning_share}`; group splits add an entity-disjoint fold column and `TabularPredictor(groups=…)`, every non-random split passes its tuning slice as `fit(tuning_data=…, use_bag_holdout=True)` (both shown in `resolved`), and the entity column is dropped from the features by default. `column_types` (`{column: number\|category\|text}`) overrides inferred types: the columns are cast and AutoGluon gets the matching `feature_metadata` (shown in `resolved`). `fit_kwargs.feature_metadata` may also be given as JSON (`{type_map_raw, type_group_map_special}`). |
| `PATCH /api/datasets/{id}` | F4, confirm the target: `{target}` → the dataset re-profiled for it (classes, task, conflicts) with `profile.target_source = confirmed` and `profile.splits` = that target's split (drawn on first use, else reused). 400 for an unknown or constant column. |
| `GET /api/datasets/{id}/splits` | F4: every stored split (`target`, `strategy`, `key`, `sha256`, holdout and fold sizes) and the `current` key. Files: `data/datasets/<id>/splits/<key>.json`. |
| `GET /api/datasets/{id}/split-options?target=` | F6: the strategies, the date columns (≥ 95% read as dates) and entity columns (an ID-like name or a high-cardinality category whose values repeat), the suggested strategy and its parameters, and warnings for a random split. |
| `POST /api/datasets/{id}/splits` | F6: `{target, strategy: random-stratified\|group\|time\|time-group, group_column?, time_column?, cutoff? (YYYY-MM-DD; default: the latest 20% of rows), tuning_share? (0.2)}` → the split, drawn once and keyed by its parameters (`<strategy>__<target>-<hash>`), with `diagram`: rows, entities, date range and positive rate per role (train, tuning, holdout, excluded), shared entities between roles (0), and rows per period for time splits. Fold codes in the file: 0–4 train fold, −1 holdout, −2 excluded, −3 tuning. 400 for an unknown column, a non-date time column or a cut-off that leaves fewer than 10 rows on a side. |
| `POST /api/runs/{id}/split-twin` · `GET /api/runs/{id}/split-comparison` | F6: the same run body again (presets, budget, hyperparameters, metric, dropped columns) with only `split_spec` changed · the run's holdout score beside the latest succeeded run per split of the same dataset, label and settings (`same_config`), with `optimism` Δ = score(random) − score(split) in the metric's direction. |
| `GET /api/runs[?dataset_id=]` · `GET /api/runs/{id}` | Status, data counts, leaderboard (AutoGluon's extra info, holdout scores, fold scores and spread), ensemble weights, holdout metrics and confusion matrix, OOF availability, feature importance, TF-IDF top words, the exact `resolved` call, the predictor path. The list trims the heavy fields. |
| `GET /api/runs/{id}/events` | SSE: every event from `?from=N` (or after `Last-Event-ID`); ends after `run_succeeded`/`run_failed`/`run_cancelled`. `?format=json` returns them once. |
| `POST /api/runs/{id}/predict` | `{rows: [{col: value}], model?}` → predictions, per-class probabilities. |
| `GET /api/runs/{id}/oof[?model=]` · `GET /api/runs/{id}/feature-importance` | Out-of-fold predictions (bagged runs) · permutation importance on the holdout (computed on demand if the run skipped it). |
| `POST /api/runs/{id}/ensemble` | `{models: [base model, …]}` → 201: `predictor.fit_weighted_ensemble(base_models=models)` in its own process. Returns the new model's weights, validation and holdout scores, the best single model's, and `vs_best_single` / `vs_run_best` (`beats`, `delta`, `delta_natural`) on validation and holdout. The run's default model is not changed. On refit presets (good/high quality) the new ensemble is refit too and `predict_model` names its `_FULL` copy. The ensemble is added to the run's leaderboard (`custom: true`) and to `custom_ensembles`. |
| `GET /api/runs/{id}/holdout-predictions` | The best model's holdout predictions: `rows` (data.csv positions), `y_true`, `y_pred`, and per-class `probabilities` (classification). |
| `POST /api/runs/{id}/cancel` · `DELETE /api/runs/{id}` | Stop a run (its whole process group) · delete a finished run and its folder. |
| `GET /api/runs/{id}/cost?c_fp&c_fn&g_tp` | F7: expected cost of the holdout decisions per 1,000 with a 95% bootstrap interval (B = 1,000), with the run's own cost or the given binary costs. Binary: at the threshold in use, at the Bayes t* = c_FP/(c_FP + c_FN + g_TP), at 0.5, at the threshold calibrated for this cost on the run's validation data, flagging nobody / everybody, the hindsight minimum, and the curve over t (0.005 grid). Multiclass: served (arg-max) and Bayes decisions, counts and cost per confusion cell, the best constant decision. |
| `POST /api/runs/{id}/metric-twin` · `GET …/cost-comparison` | F7: `{eval_metric | cost, calibrate_threshold='auto'}` re-runs the same body with only the metric changed. The comparison costs this run and its metric twins (same split key and fit settings) with this run's cost: per 1,000 at each run's threshold and at its own cost-calibrated threshold, paired-bootstrap Δ on the shared holdout rows, and each run's cost curve. |
| `POST /api/runs` with `quantile_levels`, `conformal` | F9: quantile regression. `quantile_levels` (0 < τ < 1, distinct; giving them makes an `auto` problem type `quantile`; default `[0.1, 0.5, 0.9]`) go to `TabularPredictor(quantile_levels=…)`; `conformal` true / false is AutoGluon's `fit(calibrate=…)` (null: its 'auto'). The run's `quantile` holds the holdout report: per level τ the pinball loss and F̂(τ) with a Wilson interval; per interval [τ, 1 − τ] the coverage, the shares below and above, the widths; the crossing share; the same raw (c = 0) and calibrated (+c_τ) with the offsets; every model's pinball and coverage; the fan chart. `/predict`, `/oof`, `/holdout-predictions`, registry `/predict` (Try It) and `/score` add `quantiles` (one value per level; `predictions` is the centre level); batch outputs add `q_τ` columns; `/shap?target=τ` explains one level; `/fairness` adds coverage per group. |
| `POST /api/runs/{id}/learning-curve` · `GET …/learning-curve` · `POST …/learning-curve/cancel` | F12: start a learning curve (`{fractions, seeds, time_limit, min_gain, reuse_full}`; default 10/25/50/100 %, 3 seeds) in its own process; read status, points, the summary per fraction, the power-law fit with its band, extrapolation to 2N / 4N and the verdict (`label`, `slope_per_doubling {value, ci}`, `extrapolation {x2, x4}`, `evidence`); cancel. |
| `POST /api/runs/{id}/active-learning` · `GET …/active-learning` · `POST …/active-learning/answer` · `…/simulate` · `…/submit` · `…/cancel` | F13: start a labelling queue (`{pool, hide_share, budget, rounds, strategy, diversity, candidates_factor, compare_random, seeds, time_limit}`) in its own process; read the queue with answers, the rounds, the simulated comparison and `summary` (`active_learning/1`); answer items (`{answers: [{id, answer: confirm\|relabel\|skip\|null, label}], reviewer}`); fill the open queue from the hidden labels as `simulated-oracle`; submit a round (dataset version + refit); cancel. |
| `GET /api/runs/{id}/scorecard` · `GET …/scorecard.csv` · `POST …/scorecard/score` | F11 (binary): the scorecard challenger: the deployable scorecard's table (bins with counts, event rate, WoE, IV share, points), per feature IV, coefficient and why it was left out, the points scaling (PDO, base score / odds, factor, offset), holdout AUC / Gini / KS of the scorecard, the in-run bagged scorecard and the served model, ΔAUC (served − scorecard) with a paired bootstrap 95% CI (B = 1,000), score bands, the F8 monotone check and (F7) both models' cost per 1,000 decisions · the table as CSV (+ a `_scaling` row) · `{holdout_index: [...]}` or `{rows: [...]}` → points per feature with the bin, score, P(event), the scorecard's reason codes (points lost) and the served model's P. `POST /api/runs` takes `scorecard` (null/true on for binary, false off, or its parameters). |
| `GET /api/runs/{id}/monotone?model&constraints&rows&grid&curves` · `GET /api/registry/versions/{id}/monotone` | F8: ICE curves and partial dependence of one model (default the best; for a version, the model it serves from its own files) on raw holdout rows, over a grid of the column's quantiles, for the run's constraints or `constraints=col:increasing,…` (any binary / regression run): curves and steps that go against the sign, the largest such step, whether PD is monotone, plus the worker's report (where each constraint landed in AutoGluon's features, the signs each model's library received, monotone by construction, every model's ICE check). |
| `GET /api/runs/{id}/threshold?cost_fp&cost_fn&t` | Binary: on the holdout, at every t on a 0.005 grid (and at `t`): TP, FP, FN, TN, precision, recall, F₁, specificity, FPR, balanced accuracy, accuracy, MCC, flagged share, cost C(t) = c_FP·FP + c_FN·FN; the t that maximises F₁ / bacc / MCC and minimises cost; the Bayes threshold c_FP/(c_FP + c_FN); the score histogram per class; the threshold in use. Positive = p > t, as AutoGluon predicts. |
| `POST /api/runs/{id}/threshold` | `{metric}` → `predictor.calibrate_decision_threshold(metric=…)`, or `{value}`; then `set_decision_threshold` and `save()`, so every later prediction (and `TabularPredictor.load`) uses it. The holdout is re-scored at t (`holdout`, confusion matrix, saved predictions). |
| `GET /api/runs/{id}/explain` | Classification: confusion matrix, per-class precision / recall / F₁ / support, ROC (fpr, tpr, thresholds, AUC) and PR (precision, recall, AP) per positive class or one-vs-rest, the reliability curve (10 uniform bins, counts, Brier, ECE; top-label for multiclass), log loss, the worst mistakes with their features. Regression: RMSE, MAE, R², residual mean and σ, quantiles, residuals vs ŷ, a 30-bin histogram with its normal fit, the largest errors. Plus the columns usable as groups. |
| `GET /api/runs/{id}/fairness?group=&t=` | fairlearn `MetricFrame` per group (numbers > 8 values cut into quartiles; missing is a group): binary count, selection rate, base rate, accuracy, TPR, FPR, precision at the run's t, with demographic parity difference / ratio and equalised odds difference; multiclass accuracy and macro F₁; regression MAE, RMSE, bias. |
| `GET /api/runs/{id}/shap?row=&target=` | Kernel SHAP (`shap.KernelExplainer` on `predict_proba` of the target class, or `predict` for regression; 50 background training rows from `shap.sample(…, random_state=0)`, 2048 coalitions, no L1 selection) for one holdout row: φ₀, every φᵢ with the row's value, f(x); f(x) = φ₀ + Σ φᵢ exactly. Cached in the run folder. |
| `GET /api/runs/{id}/feature-importance?shuffles=` | Permutation importance (now 5 shuffles when computed on demand, `shuffles` to recompute) with a 95% t-interval Î ± t₀.₉₇₅,ₙ₋₁·s/√n per feature. |
| `GET /api/status` | The status bar's live state: versions, CPU count, memory, host, the active run (stage, models started / finished, best validation score, started_at, τ), queued runs, run count, registry counts, worker-pool jobs. |
| `GET /api/runs/{id}/latency` | Every model of the run that can predict: measured batch µs per row on the holdout (p50 of 3), one-row call latency with the whole pipeline (`single_ms_p50` / `single_ms_p95`) and **as served** (`served_ms_p50` / `served_ms_p95`: pipeline pruned to what the model reads, persisted; `pipeline` says what was pruned), validation and holdout score, the models and bytes it needs, and `frontier` (models no other beats on both τ₉₅ and validation score). Measured in a worker; cached. |
| `POST /api/runs/{id}/register` | `{created_by, model_name?, description?, refit_full=false, infer_limit? (s/row), infer_limit_batch_size? (1 = per one-row call, measured as served), clone_for_deployment=true, prune_pipeline=true, serve_model?, serve_fastest=false, distill=false, distill_time_limit=120, distill_hyperparameters?}` (`serve_model`: serve that model of the run, its `_FULL` copy after refit; `serve_fastest`: the lowest one-row p95; `distill`: `predictor.distill()` students, the best one served unless another rule picks) → 202, a version `building`; its steps update until `ready` (or `failed`). The run is never changed. The version's `result.serving` holds the cold first call, the warm-up, one-row p50 / p95 over 200 calls, batches of 1 → 1,000 and the pipeline pruning (checked: max \|Δp\| on the holdout). A ready version is warmed in every predict worker (and in the API process if ≤ 200 MB). |
| `POST /api/registry/versions/{id}/score` | F3, the online path: `{rows, where=auto\|api\|worker}` → predictions and probabilities, no SHAP; `predict_ms`, `worker_ms`, `server_ms`, `where`, `warm_before_call`. `auto` serves a deployed version (≤ 200 MB, ≤ 100 rows) in the API process, else in a predict worker. Anomaly versions score each row as Try It does. |
| `POST /api/registry/versions/{id}/benchmark` · `GET /api/registry/serving` | One-row p50 / p95 and batches of 1 → 1,000 measured in a predict worker (stored as `result.serving_benchmark`); the versions loaded and warmed in the API process. |
| `GET /api/registry` · `/registry/summary` · `/registry/versions[?model=]` · `/registry/versions/{id}` | Registered models, versions (with build steps, measured source vs version size and latency, holdout metrics with 95% bootstrap CIs, infer_limit candidates, hashes, MLflow), stage and approval counts, recent audit. |
| `POST /api/registry/versions/{id}/transition` | `{stage: None\|Staging\|Production\|Archived, actor, comment}`. Production needs an approved review; promoting archives the model's previous Production version; rejected versions cannot go live. Mirrored to MLflow (stage, `production` / `staging` aliases, tags). |
| `POST /api/registry/versions/{id}/review` | `{decision: approve\|reject, reviewer, comment}`. Four eyes: the reviewer cannot be the registrant; a rejection needs a comment and archives a live version; one review per version. |
| `GET /api/registry/audit?model=&version_id=` | The append-only audit log: who, action, from → to stage, comment, the model's SHA-256 at that moment. |
| `GET /api/registry/versions/{id}/card?format=json\|md\|html&download=` · `PUT …/card` | The model card; `PUT {actor, fields}` fills the intended-use template (audited). |
| `POST /api/registry/versions/{id}/predict` | `{row, explain=true, target?}` → prediction, probabilities, Kernel SHAP for that row with the top 5 reasons. In a worker. |
| `GET · PUT /api/registry/versions/{id}/reasons` · `POST …/reasons/stability` | F10 (binary): the reason-code set-up (adverse class, served threshold t, reference applicants, attribution method chosen for the served model, mapping table) · edit `{actor, mapping?, adverse_class?, reference?: approved\|population, top_k?}` (per registry model; audited; unknown features, duplicate codes and unknown classes → 400) · `{rows=300, noise=0.05, repeats=3}` → how often the top reasons change under noise, plus the fast estimator against 4,096 coalitions (stored; the model card shows it). Try It adds `reasons`; `/score` takes `reasons` (and `reasons_reference` for the audit estimate); batch output adds the reason columns and `summary.reasons` (rows/s, methods, additivity, monotone check, most cited codes). |
| `POST /api/registry/versions/{id}/batches` (multipart `file`, `created_by`) · `GET /api/registry/batches/{bid}` · `…/download` · `GET /api/registry/versions/{id}/batches` | Batch scoring in a worker with progress; result: summary, preview, the scored CSV (input + `prediction` + `p_<class>`), and drift: PSI per feature against the training rows (deciles / top-19 categories / text length, missing as a bin, ε = 10⁻⁴) and of the output against the version's holdout predictions, with KS. |
| `GET /api/gym/schema` | What the Gym configures: model families with objectives and slider specs (AutoGluon key, symbol, interval, log/linear, integer, AutoGluon's default), presets and what they expand to, metrics per problem type, HPO options, the GPU count, every keyword `TabularPredictor.fit` accepts with its default (read from the installed AutoGluon), the default model zoo. |
| `POST /api/gym/validate` | A run body (as for `POST /api/runs`) → `errors` / `warnings` (unknown fit arguments with a suggestion, invalid values, stacking without bagging, GPUs that are not there, metric vs problem type, columns), `resolved` (the exact predictor and fit kwargs, post-fit steps), `call` (Python), `diff` against fit()'s defaults with origin, `effective` (AutoGluon's `get_validation_and_stacking_method` for this data: k, s, L, dynamic stacking, holdout_frac, refit), `estimate` (T̂ with per-family rates and their source). `POST /api/runs` refuses a body with errors (400). |
| `GET /api/runs/{id}/gym` | The body that starts the run again, its parent run and patch. |
| `GET /api/runs/{id}/export.py` | A standalone script that reproduces the run with plain AutoGluon: data file with its SHA-256, the exact holdout rows (packed bitmask), dropped columns and column types, the resolved `TabularPredictor(...).fit(...)`, post-fit steps, the threshold step; it ends by comparing its leaderboard with the run's, model by model (`--tolerance`, `--no-check`). |
| `GET /api/runs/{id}/vela` · `POST …/vela/wording` | Findings computed from the run (numbers, formula, one patch each with `changes` and the patched `body`) · their wording through the LLM router (Anthropic SDK, else the claude CLI, else the computed text; `source`, `decider`, `model`, attempts), cached per findings. |
| `GET /api/registry/versions/{id}/mlflow` | MLflow's own record of the version and its source run (params, metrics, tags). |

`POST /api/runs` also takes `monotone` (F8: `{constraints: {column: increasing | decreasing | none}, compliant_only: true}`; GBM / XGB / CAT become their `_MONO` subclasses, other families are left out unless `compliant_only` is false), `cost` (F7: `{c_fp, c_fn, g_tp}` or `{classes, matrix}`; the run's eval_metric becomes `expected_cost` and a binary run calibrates its threshold on it), `pseudolabel` (`{dataset_id, method: fit_pseudolabel | unlabeled_data, max_iter}`: unlabeled rows from another dataset, label column dropped), `distill` (kwargs of `predictor.distill`, run after the fit with a seeded generator so it reproduces), and `parent_run_id` / `patch` / `source` (lineage for Vela patches, the Gym and the client). `POST /api/runs` also takes `calibrate_threshold` (binary): a metric (`f1`, `balanced_accuracy`, `mcc`, …) to calibrate the decision threshold for after the fit, or `auto` (F₁ when the minority class is under 20% of the training rows). `fit_kwargs.calibrate_decision_threshold`, if given, always wins (AutoGluon does it, or not, inside `fit`). The New Model wizard sends `f1` by default when the label's minority class is under 20%; the Goal step shows the choice. Every binary run records `threshold` = `{value, source, metric, holdout_at_default, holdout_at_threshold}`.

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
