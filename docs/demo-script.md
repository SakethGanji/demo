# ENSEMBLE · a 15-minute demo for a bank's credit-risk audience

The story: *a credit-default model validated the way a model-risk team validates it.* Out of time, trained on what a
wrong decision costs, monotone where policy demands it, explained per decision, benchmarked against a scorecard,
diagnosed, and then watched in production until it needs retraining. All on AutoGluon, nothing hidden.

Every number below was measured on this build (4 CPUs, 16 GB) with the seeded samples, by replaying the clicks as the
API calls the UI makes (`backend/.venv/bin/python scripts/demo_numbers.py`, 70 s, prints each number in order). The
fits are deterministic for these configs; the threshold, the learning curve and the timings can move in the last digits.

## Where things are (G11)

The sidebar has five places: **Home**, **Build**, **Models**, **Registry** and **Vela**. Settings is the gear in the
sidebar footer.

- **Build** is where every model starts: upload a table or pick a sample, Smart Intake shows each column's role and
  what you can build. New Model, Forecast, Anomaly, Import and the credit flows (expected loss, segmented, survival,
  reject inference, multi-label) open from its suggestions, pre-filled. **Advanced** opens the Training Gym.
- **Models** lists every run. A run's page has three tabs:
  - **Results**: metrics, leaderboard, cost and split comparisons, and **Register…**;
  - **Explain**: Performance, Errors, Features & SHAP, Fairness (fair lending included), and an **Advanced** group
    with Threshold, Monotonicity, Scorecard and Text;
  - **Doctor**: Model Doctor, Weak Slices, Shift, Learning Curve and Labels.
- **Registry**: versions, reviews, the model card, Try It, reason codes, batch scoring and drift, monitoring and the
  credit report.
- **Vela**: ask the agent in plain language, or **Run it end to end** (Autopilot).

Old links (`#/experiments`, `#/explain?run=…&tab=…`, `#/autopilot`, `#/forecast?run=…` and the other per-flow
`?run=` links) still work: they redirect to the new places.

## Before the audience arrives (10 minutes)

1. `make dev`, then `make reset` (an empty workbench; the samples regenerated). Open http://localhost:5190 at 1920×1080
   (1366×768 works). Type your name in *acting as* (top right), e.g. **Dana Dev**.
2. **Prepare the production champion for part 8** (it needs a registered version with a challenger):
   - New Model → sample **loan-stream** → target `defaulted` (named on upload, already confirmed) → in the column list
     set `application_date` to **ignore** → How The Holdout Is Drawn: **out-of-time** on `application_date` → Goal:
     ROC AUC → Advanced:
     `{"hyperparameters": {"GBM": {"num_boost_round": 200}, "LR": {}}}` → Start Training (≈ 20 s).
   - Again with `{"hyperparameters": {"XGB": {"n_estimators": 200}}}`: the challenger.
   - Registry → Register both (model name `loan-stream`). As **Riley Review**: approve the first → **Production**,
     the second → **Staging**. Back as Dana.
   - **Expect:** champion holdout AUC **0.7765 [0.7495, 0.8034]**.
   - Do not skip *ignore* on `application_date`. Kept as a feature, every 2025 batch lies outside the training dates,
     so its PSI raises a drift alert from 2025-01 (seen in F20: PSI 0.359 on application_date, AUC 0.7826, challenger
     Δ −0.0083). Part 8's story would be lost.
3. Close Vela for now.

## 0:00 – 1:00 · The question

- **Say:** "A credit-default model. Five questions a validator asks: does it hold up on the future, does it optimise
  what a mistake costs, does it respect policy, can we tell an applicant why, and is it better than the scorecard we
  already trust? Then: what limits it, and how do we know when it stops working?"

## 1:00 – 3:30 · 1. Out of time, not at random

1. **Build →** pick the sample **credit-default** → **Yes/no classifier** (New Model opens pre-filled), or Build's
   New Model choice (12,000 applications 2022–2024, 8,074 customers, 7.4 % default,
   drifting from 5.6 % to 9.6 %).
2. **Continue →** target **defaulted**, **Confirm The Target**. In the column list set `customer_id` and
   `application_date` to **ignore**: neither may become a reason for declining someone.
3. **How The Holdout Is Drawn** (appears once the target is confirmed).
   - **Expect:** the warning on *random*: "'application_date' is a date: a random split trains on rows from after the
     holdout's dates …", and the customer warning (1.49 rows per customer). Suggested: out-of-time + group.
   - Click **out-of-time**. **Expect:** the timeline with the cut-off **2024-06-20**: train 7,675 · tuning 1,925 ·
     holdout 2,400 rows.
4. **Continue →** Goal: **ROC AUC**. Open **Advanced** and paste, so it trains in ≈ 10 s:
   `{"hyperparameters": {"GBM": {"num_boost_round": 40}, "RF": {"n_estimators": 40}, "LR": {}}}` → **Start Training**.
5. **Results →**. **Expect:** holdout ROC AUC **0.7150** (out-of-time).
6. **Random vs Out-Of-Time** card → **random split** (trains the same body under a random split, ≈ 10 s).
   - **Expect:** random **0.7537** vs out-of-time **0.7150**: random is optimistic by **+0.039**.
   - **Say:** "A random split scores the model on applications from the same months it learned from. The bank lends
     in the future. The honest number is the lower one." (With `customer_id` kept as a feature the random split even
     reaches 0.7720: the model remembers customers.)

## 3:30 – 5:30 · 2. Train on what a mistake costs · 3. Monotone where policy demands it

1. On the out-of-time run's Results: **Advanced** (the Training Gym opens with the run's exact body).
2. **Goal** step → **Business cost** card → **on**: c<sub>FP</sub> = 1, c<sub>FN</sub> = 10 (a missed default costs ten
   wrongly declined applicants). **Expect:** t* = c<sub>FP</sub> / (c<sub>FP</sub> + c<sub>FN</sub>) = **0.091**. The
   **Validation split** card above it already says **out-of-time** (the run's split is kept).
3. Switch the Gym to **Advanced**. **Models** step: in **Monotone Constraints** set **debt_to_income ↑**. **Expect:** RF and LR are left
   out (they cannot honour it); LightGBM, XGBoost, CatBoost get monotone subclasses (GBM_MONO, XGB_MONO, CAT_MONO) and the scorecard uses monotone binning. In the raw fit_kwargs editor:
   `{"hyperparameters": {"GBM": {"num_boost_round": 150}, "XGB": {"n_estimators": 150}, "CAT": {"iterations": 150}}}`.
4. **Train** (≈ 20 s). **Results → Business Cost · Per 1,000 Decisions**:
   - **Expect:** **702.5 [624.1, 787.1]** per 1,000 decisions at t = **0.061**, calibrated by AutoGluon on the cost.
   - Under *Compare: train this run again, same settings, optimising* click **ROC AUC** (the twin): **857.1** at its F₁ threshold t = 0.124.
     **Δ = +154.6 per 1,000 [+86.2, +225.9]**, paired on the same 2,400 holdout applications.
   - **Say:** "Same data, same models. Optimising the bank's cost instead of a textbook metric saves about 155 per
     thousand decisions, and the interval says it is not luck."
5. The run's **Explain** tab → **Advanced → Monotonicity**. **Expect:** **0 of 200** ICE curves of the served model go down in
   debt_to_income; partial dependence rises monotonically (range 0.09). "Policy says more debt never lowers the risk.
   It is enforced by construction and checked on a grid."

## 5:30 – 7:30 · 4. Why this applicant was declined

1. **Register…** (Results). Model name `demo.credit-default` → **Register version →** (≈ 20 s).
2. Version → **Reason Codes** tab: the mapping table (feature → plain-English statement, editable, audited).
3. **Try It** on a declined applicant (p̂ ≥ t). **Expect** for the first adverse holdout row: p = **0.093** and the
   four reasons **R09 debt_to_income · R12 months_employed · R03 income_usd · R10 utilization**, each a feature
   pushing toward default (φ > 0), ranked by φ.
4. **Batch & Drift**: score the holdout. **Expect** on the first 400 rows: **126 declined, all 126 with 4 codes**, as
   extra columns of the download.
   - **Say:** "The adverse-action letter is generated from the served model's own Shapley values, never a factor that
     lowered the risk. The codes are templates; compliance signs off the wording."
5. **Model Card**: section 5 shows the constraint, the reason codes, the scorecard benchmark and the new
   **Diagnostics of the source run** (split out-of-time, cost c<sub>FN</sub> = 10, learning curve, shift, slices,
   Model Doctor: "not run" until you run them).

## 7:30 – 9:00 · 5. Better than the scorecard?

1. The run's **Explain** tab → **Advanced → Scorecard** (trained beside the ensemble by default on binary labels).
   - **Expect:** a 9-feature scorecard (binning → WoE → logistic regression → points, PDO 20, 600 at 50:1) with its
     table, IV per feature and the CSV export.
   - **Expect:** ΔAUC (served − scorecard) = **+0.0010 [−0.0000, +0.0020]**: AUC 0.6987 vs 0.6977.
   - **Say:** "On the future, the interpretable scorecard is as good as the ensemble. That is a finding, not a failure:
     the product tells you when the simple model is enough." (Under a random split the ensemble wins by +0.025.)

## 9:00 – 11:00 · 6. Do we need more data? · 7. What limits this model?

1. The run's **Doctor** tab → **Learning Curve → Run the learning curve** (9 fits, ≈ 40–60 s).
   - **Expect:** points at n = 768 / 1,919 / 3,838 / 7,675 and the power-law fit with 2N and 4N extrapolations. Verdict
     **noisy**: the gain per doubling is +0.02 with a 95 % CI from 0 to 0.04 (expected cost per decision, lower is
     better). "The seeds disagree too much to promise that more data helps; 5 seeds in Advanced would tell."
2. **Model Doctor** (same tab). **Expect:** primary **Overfitting (minor)**: validation −0.593 vs holdout −0.779
   (expected cost, higher is better on this scale), 4.4× the noise. Checks To Run: **shift** and **weak slices** are
   offered with one click each; the learning curve is now read as *no evidence*.
   - **Say:** "One fixed procedure over every check the workbench has, the same one Autopilot uses to choose its next
     step. Each finding comes with the numbers and one prescription."
   - Optional: **Run** the shift check (≈ 15 s) and **Diagnose Again**.

## 11:00 – 14:00 · 8. Watching it in production

1. **Registry →** `loan-stream` (Production) → **Monitoring** tab.
2. **Champion · Challenger**: the Staging XGBoost is set as challenger (Set Challenger).
3. **Sample Stream · 2025 → Feed the rest** (12 monthly batches of 1,200 applications, scored and shadow-scored,
   ≈ 20 s; labels arrive 3 months later).
   - **Expect:** PSI(debt_to_income) per month 0.00–0.02 for January–June, then **0.41 in 2025-07** (⚑ the first drift
     alert, never before), 0.65, 1.03 …
   - **Which Signal Caught What:** in 2025-07 the observed default rate is **0.351 [0.324, 0.378] vs p̂ 0.250**
     (z = +9.0) while the AUC barely moves (**0.772 vs 0.776**): the concept change shows in calibration, visible
     only when the labels land (2025-10-31).
   - Challenger: pooled on 10,800 labelled rows, AUC **0.7861 vs 0.7841**, Δ −0.0020 [−0.0044, +0.0003] (DeLong):
     no reason to switch, and nothing is promoted automatically.
4. **Retrain Proposal** → **Launch Retrain Run**: the same config on the latest labelled window (2025-04-01 … 2025-09-30,
   7,200 rows). On its newest 1,464 rows the champion scores AUC **0.7510**, the retrained run **0.7739**.
5. Back in **Models →** the champion run → **Doctor**: **Shift (critical)**, prescription **Retrain On Recent Data**.

## Optional · the credit-risk add-on (about 5 minutes)

For a credit-risk audience that wants more than the core story. Each step stands alone.

1. **Recourse.** Registry → the `demo.credit-default` version → **Try It** on a declined applicant.
   - Under the reason codes: **What would change the decision**, the smallest realistic changes (re-scored by the served
     model) that flip the decline. Fixed columns (age, region, IDs) are never changed.
   - Codes on geography or protected characteristics carry a **not for adverse-action notices** tag: they explain
     the model but cannot be given as a reason.
2. **The credit report.** Same version → **Credit Report**: Gini and KS by month, score bands, PSI/CSI and vintage
   curves, exportable for the model-risk file.
3. **Expected loss and stress.** **Build** → sample **loan-losses** → **Expected loss (PD × LGD × EAD)** → **Train PD,
   LGD and EAD →**. On the run page: ΣEL against the realised loss on the holdout with its 95 % interval, by segment,
   then **Stress** → add a scenario (e.g. `income_usd` −10 %) → **Run scenario**: ΣEL stressed against the base, per
   segment.
4. **Fair lending.** Build → sample **fair-lending** → Yes/no classifier → train. On the run page: **Explain →
   Fairness**: BISG proxy groups from surname and zip, the adverse impact ratio per group against the four-fifths rule.
   The Model Doctor's **Also Worth Checking** card links here too, and to segmented models, reject inference and
   recourse when they apply.

## Optional · the Vela agent (about 2 minutes)

1. **Vela → Ask Vela.** Type: *"Train a default model on credit_default.csv with an out-of-time split, run the Model
   Doctor, apply its fix and compare the two runs. Keep each training to about 60 seconds."*
2. **Expect:** step cards, one per tool call (the tool, its arguments, the result and a link to the page it touched).
   Reads run at once. Anything that changes state (training, the Gym patch, registering) stops on a confirm card with
   the exact call: click **Run It** or **Skip**.
3. **Say:** "The agent uses the workbench's own API, one typed tool at a time. It can request an approval, but it
   cannot approve or promote: that stays human. Every call is in the audit log as *Vela agent on behalf of* you, and
   every number in its answer is checked against the tool results."
4. Ask: *"Why was applicant C-500123 declined by the latest version, and what would change it?"* Reason codes and
   counterfactuals, no confirmation needed (reads only).

## 14:00 – 15:00 · Close

- **Say:** "Out-of-time validation, a cost the business set, a policy constraint enforced and checked, reasons per
  decision, the scorecard benchmark, a diagnosis with evidence, and monitoring that knew in July. Every model is a plain
  AutoGluon predictor on disk; every number on screen comes from the API with its formula next to it."

## If something goes wrong

| Symptom | Do this |
| --- | --- |
| The API stopped | The UI falls back to mock mode (SIMULATED badge). `make dev` again; open runs resume by replay. |
| A run is slow | The race has a 3× button; Cancel Run stops the whole process group. |
| Wrong numbers on stage | `make reset` and re-run: the data and the configs above are seeded. `scripts/demo_numbers.py` prints the expected values. |
| Learning curve or shift check "busy" (409) | One runs at a time across the workbench; wait for the other or cancel it. |
| Questions about a run | Open Vela (top right) and ask; it answers from the run's computed facts, and the Model Doctor card is there too. |

## Appendix · the round-1 10-minute demo on fresh files

The fresh-file story (fraud, complaints, deposits, anomalies, forecasting) still works as written; its numbers predate
round 2 (e.g. the anomaly forest now finds 13/18 bursts, P@90 0.80, and forecast intervals are recalibrated to 90.5 %).

### 0:00 – 0:45 · Home: one workbench for every model the bank builds

- **Click:** nothing yet. Point at the panel **No runs yet. Start with a sample.**
- **Say:** "This is ENSEMBLE, our AutoML workbench. The engine is AutoGluon, which is Amazon's open-source AutoML
  library. It trains a zoo of models, stacks them and picks the ensemble. Six samples are ready: text routing,
  churn, credit default, loan amounts, a forecasting set and card transactions for anomaly detection. Today I'll bring
  a file it has never seen."

### 0:45 – 3:15 · New Model on a fresh fraud file

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

### 3:15 – 4:45 · Explain: the decision a fraud team actually makes

1. The run's **Explain** tab → **Advanced → Threshold**.
   - **Expect at t = 0.143:** TP 23, FP 36, FN 27, TN 2,314 → precision 0.39, recall 0.46, F₁ 0.42, 2.5% flagged.
   - Set the costs **c_FP = 1, c_FN = 20** (a missed fraud costs 20 reviews). The cost curve's minimum moves to t ≈ 0.03,
     and the Bayes line sits at c_FP / (c_FP + c_FN) = 0.048.
   - **Say:** "The threshold is a business decision, and C(t) = c_FP·FP + c_FN·FN makes it one. *Calibrate threshold*
     runs AutoGluon's own `calibrate_decision_threshold` and saves it in the predictor."
2. Tab **Features & SHAP**: permutation importance with 95% intervals (3-D Secure, IP country match and device age
   lead), then **why?** on a row: the waterfall φ₀ → f(x) with f(x) = φ₀ + Σφᵢ written out to four decimals.
3. Tab **Fairness**, group **customer_segment**: selection rate per segment and the demographic-parity gap.

### 4:45 – 6:45 · Register, approve, use

1. **Register…** (from the run's Results, or Models). Type your name in *acting as* (e.g. Dana Dev).
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

### 6:45 – 8:00 · Anomalies with no labels, and a fix measured honestly

1. **Build → Find unusual rows** (Anomaly). The card-transactions sample is selected, with **group-relative features** on
   (entity `card_id`, time `timestamp`, amount `amount_usd`). Point at r = x / x̄ₑ,₋ᵢ and z within the card.
2. **Find Anomalies** (≈ 6 s).
   - **Expect:** 90 flagged at s* = 0.550 (c = 1.5%); against the hidden planted truth (sample only):
     **P@10 1.00, P@50 0.94, P@90 0.71, ROC AUC 0.990**.
   - **Say:** "An amount can be normal for the bank and absurd for this card. Without the per-card features the forest
     found 0 of 18 amount spikes (AUC 0.954); with them it finds 8 of 18. Velocity bursts remain hard for an
     isolation forest, 2 of 18, and the page says so."
3. Click the top row: TreeSHAP reasons, e.g. *amount_usd_over_entity_mean = 56 (z = +125)*.

### 8:00 – 9:15 · Forecasting with intervals you can audit

1. **Build → Forecast**: the transaction-volume sample (6 series × 730 days, holidays and paydays as known covariates).
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

### 9:15 – 10:00 · Close: open, auditable, not a black box

- **Models**: every run of every kind in one table with a type filter (binary, multiclass, regression,
  forecast, anomaly, imported), each with its own metric.
- **Training Gym**: every `fit()` argument, validated by the server; *Export.py* reproduces any run with plain
  AutoGluon and checks the leaderboard model by model.
- **Say:** "Every model here is a plain AutoGluon predictor on disk. If ENSEMBLE disappeared tomorrow, your models and
  the code to rebuild them would still be there."

### If something goes wrong

| Symptom | Do this |
| --- | --- |
| The API stopped | The UI falls back to mock mode (SIMULATED badge). `make dev` again; open runs resume by replay. |
| A run is slow | The race has a 3× button; Cancel Run stops the whole process group. |
| Wrong numbers on stage | `make reset` and re-run: the data and the configs above are seeded. |
| Questions about a run | Open Vela (top right) and ask; it answers from the run's computed facts, and says which model answered. |

### Other numbers you may be asked for

| Flow | Data | Result |
| --- | --- | --- |
| Complaints routing (text) | `complaints.csv`, label `product_team`, TF-IDF LR + SVM + GBM | macro-F₁ **0.954**, accuracy 0.958 |
| Deposit balances (regression) | `deposit_balances.csv`, GBM + XT + LR | R² **0.952**, RMSE £4,769, MAE £1,248 (browser upload; holdout stratified on the profiler's label guess) |
| Support tickets (sample) | Autopilot, F̄₁ ≥ 0.92 and τ₉₅ ≤ 50 ms | inbox recall of planted label errors 252/300 = 84%; MET at F̄₁ 0.926, τ₉₅ ≈ 20 ms |
| Credit default (import) | a TabularPredictor trained outside, zipped | imported and scored on the holdout, ROC AUC 0.770 |
