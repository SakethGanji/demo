# ENSEMBLE · a 10-minute live demo for a bank audience

The story: *a file the system has never seen goes from upload to an approved, explained, monitored model in under ten
minutes, on AutoGluon, with nothing hidden.* Every number below was measured on this build (4 CPUs, 16 GB) with the
seeded data; AutoGluon is deterministic for these configs, so expect the same values or within the last digit.

## Before the audience arrives (5 minutes)

1. `make dev`, then `make reset` (an empty workbench; the six samples regenerated).
2. `python scripts/fresh_data.py ~/Desktop/demo` writes the three "fresh" CSVs: `card_fraud.csv` (12,000 card-not-present
   authorisations, 1.7% fraud, `is_fraud` = 1/0), `complaints.csv` (6,000 complaint narratives, 7 product teams),
   `deposit_balances.csv` (7,000 accounts, next quarter's average balance).
3. Open http://localhost:5190 at 1920×1080 (1366×768 works; Vela starts closed below 1600 px). Close Vela for now.
4. Optional safety net: stop the API and the UI still opens in mock mode, labelled SIMULATED.

## 0:00 – 0:45 · Home: one workbench for every model the bank builds

- **Click:** nothing yet. Point at the panel **No runs yet. Start with a sample.**
- **Say:** "This is ENSEMBLE, our AutoML workbench. The engine is AutoGluon, which is Amazon's open-source AutoML
  library. It trains a zoo of models, stacks them and picks the ensemble. Six samples are ready: text routing,
  churn, credit default, loan amounts, a forecasting set and card transactions for anomaly detection. Today I'll bring
  a file it has never seen."

## 0:45 – 3:15 · New Model on a fresh fraud file

1. **Click** *New Model →*. Drop `card_fraud.csv` on the drop zone.
   - **Expect:** 12,000 rows × 12 columns profiled; `auth_id` typed as an ID and dropped; `device_age_days` 3% missing.
   - **Say:** "No data prep. It reads the types, finds the ID, and fixes a holdout of 20% that no model will ever train on."
2. **Continue →**, pick label **is_fraud**.
   - **Expect:** *binary* inferred; label balance 2.1% fraud.
3. **Continue →** (Goal).
   - **Expect:** metric defaults to ROC AUC for an imbalanced label, and the threshold to *calibrate for F₁*. Click
     **Average precision** and point at its formula AP = Σₙ (Rₙ − Rₙ₋₁)·Pₙ: "With rare fraud, AP rewards catching fraud
     without false alarms; its floor is the fraud rate, 0.02, not 0.5."
   - Open **Advanced** and paste, so the race takes 20 s on stage:
     `{"hyperparameters": {"GBM": {"num_boost_round": 150}, "CAT": {"iterations": 150}, "LR": {}}, "num_bag_folds": 3}`
   - **Say:** "Every argument of AutoGluon's `fit()` passes straight through. Here is the exact call it will run."
4. **Start Training**. The race:
   - One string per model rings while it fits and settles at its validation score; the stack forms; the clock runs
     against τ = 2 min.
   - **Expect:** 4 models; the ensemble ŷ(x) = 0.556·f_Linear(x) + 0.333·f_LightGBM(x) + 0.111·f_CatBoost(x).
   - **Say:** "Those weights are Caruana's greedy selection on out-of-fold predictions. The equation on screen is the
     live one, with the real weights."
5. **Results →**.
   - **Expect:** holdout **AP 0.360** (17× the 0.021 fraud rate), **ROC AUC 0.917**, threshold **t = 0.143** (calibrated for F₁).
   - Point at the leaderboard: the linear model alone scores AP 0.368 on the holdout, slightly above the ensemble
     (0.360). **Say:** "It doesn't hide that. The ensemble was chosen on validation; the holdout tells you when a
     simpler model would do."

## 3:15 – 4:45 · Explain: the decision a fraud team actually makes

1. **Explain this model →**, tab **Threshold**.
   - **Expect at t = 0.143:** TP 23, FP 36, FN 27, TN 2,314 → precision 0.39, recall 0.46, F₁ 0.42, 2.5% flagged.
   - Set the costs **c_FP = 1, c_FN = 20** (a missed fraud costs 20 reviews). The cost curve's minimum moves to t ≈ 0.03,
     and the Bayes line sits at c_FP / (c_FP + c_FN) = 0.048.
   - **Say:** "The threshold is a business decision, and C(t) = c_FP·FP + c_FN·FN makes it one. *Calibrate threshold*
     runs AutoGluon's own `calibrate_decision_threshold` and saves it in the predictor."
2. Tab **Features & SHAP**: permutation importance with 95% intervals (3-D Secure, IP country match and device age
   lead), then **why?** on a row: the waterfall φ₀ → f(x) with f(x) = φ₀ + Σφᵢ written out to four decimals.
3. Tab **Fairness**, group **customer_segment**: selection rate per segment and the demographic-parity gap.

## 4:45 – 6:45 · Register, approve, use

1. **Register Model Version** (from the results or Experiments). Type your name in *acting as* (e.g. Dana Dev).
   - **Expect:** the latency · score frontier, every model measured on 1,000 holdout rows, and the **one-row latency**
     box: τ₉₅ of the ensemble per single-row call.
   - **Say:** "Batch µs per row hides what an online API waits for; this is the 95th percentile of real one-row calls.
     If it is too slow, it offers the fastest model within reach, with what it costs in score." (On the support-ticket
     text model: ensemble 50 ms vs LightGBM 23 ms for Δaccuracy −0.003.)
2. **Register version →**. The build steps run in a worker: clone → refit → deployment copy → measure → hash → MLflow.
3. Four eyes: the registrant cannot approve. Change *acting as* to **Riley Review**, add a comment, **Approve**, then
   **Production**. The audit log records who, what, when and the model's SHA-256.
4. **Model Card**: the SR 11-7-style card generated from the run (data hashes, the fit call, the ensemble, holdout
   metrics with bootstrap CIs, fairness, limitations); download as Markdown or HTML.
5. **Try It**: the form is generated from the schema. **Expect:** class 0, p̂ ≈ 0.001 against t = 0.143, with the top
   SHAP reasons.
6. **Batch & Drift**: score 500 new rows. **Expect:** 1.8% flagged (vs 2.5% on the holdout), max feature PSI 0.03
   (stable), PSI of p̂ 0.02, with PSI = Σ (aᵢ − eᵢ) ln(aᵢ/eᵢ) per bin on screen. Download the scored CSV.

## 6:45 – 8:00 · Anomalies with no labels, and a fix measured honestly

1. **Anomaly Detection** (sidebar). The card-transactions sample is selected, with **group-relative features** on
   (entity `card_id`, time `timestamp`, amount `amount_usd`). Point at r = x / x̄ₑ,₋ᵢ and z within the card.
2. **Find Anomalies** (≈ 6 s).
   - **Expect:** 90 flagged at s* = 0.550 (c = 1.5%); against the hidden planted truth (sample only):
     **P@10 1.00, P@50 0.94, P@90 0.71, ROC AUC 0.990**.
   - **Say:** "An amount can be normal for the bank and absurd for this card. Without the per-card features the forest
     found 0 of 18 amount spikes (AUC 0.954); with them it finds 8 of 18. Velocity bursts remain hard for an
     isolation forest, 2 of 18, and the page says so."
3. Click the top row: TreeSHAP reasons, e.g. *amount_usd_over_entity_mean = 56 (z = +125)*.

## 8:00 – 9:15 · Forecasting with intervals you can audit

1. **New Forecast**: the transaction-volume sample (6 series × 730 days, holidays and paydays as known covariates).
   Set presets **fast_training** and hyperparameters
   `{"SeasonalNaive": {}, "ETS": {}, "DirectTabular": {}, "Chronos": {"model_path": "bolt_tiny"}}`, num_val_windows 2,
   then **Start** (≈ 20 s).
2. **Expect:** holdout **MASE 0.515** against SeasonalNaive 1.147 (skill 55%), WQL 0.098; the fan chart with nested
   bands; ensemble weights DirectTabular 0.83, ETS 0.16, Chronos 0.01.
3. Scroll to **Interval Calibration**. **Expect:** [q₀.₀₅, q₀.₉₅] covers **168 of 168** actuals against 90% nominal,
   P(K ≥ 168 | calibrated) = 2×10⁻⁸ → *bands too wide*; in the backtests ETS (92%) and Chronos (88%) are near
   nominal, DirectTabular (98%) is wide.
   - **Say:** "We show the empirical coverage next to the nominal and explain it. The median forecast is good; the
     band is conservative. Nothing is rescaled to look right."

## 9:15 – 10:00 · Close: open, auditable, not a black box

- **Experiments**: every run of every kind in one table with a type filter (binary, multiclass, regression,
  forecast, anomaly, imported), each with its own metric.
- **Training Gym**: every `fit()` argument, validated by the server; *Export.py* reproduces any run with plain
  AutoGluon and checks the leaderboard model by model.
- **Say:** "Every model here is a plain AutoGluon predictor on disk. If ENSEMBLE disappeared tomorrow, your models and
  the code to rebuild them would still be there."

## If something goes wrong

| Symptom | Do this |
| --- | --- |
| The API stopped | The UI falls back to mock mode (SIMULATED badge). `make dev` again; open runs resume by replay. |
| A run is slow | The race has a 3× button; Cancel Run stops the whole process group. |
| Wrong numbers on stage | `make reset` and re-run: the data and the configs above are seeded. |
| Questions about a run | Open Vela (top right) and ask; it answers from the run's computed facts, and says which model answered. |

## Other numbers you may be asked for

| Flow | Data | Result |
| --- | --- | --- |
| Complaints routing (text) | `complaints.csv`, label `product_team`, TF-IDF LR + SVM + GBM | macro-F₁ **0.954**, accuracy 0.958 |
| Deposit balances (regression) | `deposit_balances.csv`, GBM + XT + LR | R² **0.952**, RMSE £4,769, MAE £1,248 (browser upload; holdout stratified on the profiler's label guess) |
| Support tickets (sample) | Autopilot, F̄₁ ≥ 0.92 and τ₉₅ ≤ 50 ms | inbox recall of planted label errors 252/300 = 84%; MET at F̄₁ 0.926, τ₉₅ ≈ 20 ms |
| Credit default (import) | a TabularPredictor trained outside, zipped | imported and scored on the holdout, ROC AUC 0.770 |
