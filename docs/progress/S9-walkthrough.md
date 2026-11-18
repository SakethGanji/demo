# S9 · Visual and maths pass, final end-to-end, hand-off

Status: **done. Final `make smoke`: 65/65 steps green in 858 s** (sections core, samples, autopilot, forecast,
anomaly, import, fresh, reset). **`npm run build` passes.** The browser walkthrough on three fresh CSVs: **3/3 flows,
0 console errors.** Every page was screenshotted at 1366×768 and 1920×1080 with no console errors in live mode (mock
mode's only console lines are the expected 503s of the health probe that switches it on).

## A. Open problems from earlier sessions

| # | Problem | What S9 did | Result |
| --- | --- | --- | --- |
| 1 | Forecast, anomaly and imported runs missing from Experiments and Home | `GET /api/runs?kind=all` everywhere runs are listed; `shared/lib/runKinds.ts` gives each kind its headline metric (tabular: eval metric on the holdout; forecast: the eval metric on the last H steps; anomaly: ROC AUC against the planted truth, else rows flagged at s*), its problem line and the page it opens on. Experiments has a type filter (binary · multiclass · regression · forecast · anomaly · imported); Compare, Parameter Diff and the parallel coordinates use tabular runs and say so. Home's strings, best scores and hero read every kind. | smoke: `runs list: every kind` |
| 2 | Batch scoring and drift for forecast/anomaly only on their run pages | `POST /api/registry/versions/{id}/batches` now accepts every kind (`scoring.score_flow_batch`): anomaly rows get s, E[h], percentile and the flag at s*, with PSI per feature against the fitted rows and of s against the fitted scores; forecast batches are new history, forecast H steps per series by the version's predictor, with PSI of the target and covariates against the training history and of ŷ against the run's own forecast. The registry's Batch & Drift tab serves all kinds. | anomaly: 800 rows, 6/8 rows with amount ×30 flagged, PSI(s) 0.262; forecast: 168 rows, PSI(ŷ) 0.041 |
| 3 | IsolationForest missed relative spikes (0/18) | The card sample now has `card_id` (420 cards with their own spending level) and `timestamp`. `anomaly/group.py` adds, when an entity column is mapped, r = x / x̄ₑ,₋ᵢ (leave-one-out mean), z = (x − x̄ₑ,₋ᵢ)/s̃ₑ,₋ᵢ and Δt = ln(1 + hours since the entity's previous row); stored entity statistics score new rows. On by default for the sample (its spec), suggested from names and types for uploads (`GET /api/anomaly/mapping/{ds}`), editable on the setup page; the run page shows the formulas bound to the run. | **amount spikes 0/18 → 8/18, P@90 0.61 → 0.71, ROC AUC 0.954 → 0.990** (P@10 1.00, P@50 0.94). Velocity bursts stay at 2/18, stated on the page. |
| 4 | Forecast intervals cover 100% vs 90% nominal | Investigated: it is real over-dispersion, not a bug. `holdout.calibration`: F̂(τ) = share of actuals ≤ q̂_τ per level with Wilson CIs; each central interval's coverage with its CI, the binomial tail P(K ≥ k) and width/mean; backtest coverage per model. The page has an Interval Calibration panel (F̂(τ) vs τ with the diagonal, the table, a plain verdict) and the hero says "bands too wide". | [q₀.₀₅, q₀.₉₅] covers 168/168 (CI 97.8–100%), P = 2.1×10⁻⁸; backtests: ETS 92%, Chronos 88% near nominal, DirectTabular 98% (0.83 of the weight) wide |
| 5 | One-row latency of text GBM ensembles | `GET /api/runs/{id}/latency` measures one-row calls per model (`single_ms_p50/p95`); Register shows τ₉₅ of the served model with its definition and offers the fastest model at ≤ ½ its τ₉₅ with the best holdout score, with Δscore; `serve_model` on register (its `_FULL` copy after refit). | support tickets: ensemble τ₉₅ 49.6 ms vs LightGBM 23.1 ms, Δaccuracy −0.003; the version serves LightGBM |
| 6 | Stale Playwright assertion | `e2e/registry.live.spec.ts` now expects the live Autopilot brief and no prototype banner. Suites not run or extended. | |

## B. Visual and maths pass

Every page was captured at both widths and reviewed. Fixes:

- **Shell:** New Model and Vela Autopilot in the sidebar; Vela starts closed below 1600 px (it took a third of a 1366
  screen); the toolbelt hides on short screens (it duplicated the nav and was clipped at 768 px); the status bar drops
  machine details below 1500 px instead of clipping.
- **First run (`make reset`):** Home shows "No runs yet. Start with a sample." above the toolkit, with the six samples,
  each one click from the flow it suits (`#/quick-train?dataset=…`), and a New Model button. Experiments and Explain
  have empty states with real actions (`shared/components/EmptyState`); the registry's already pointed to training. The Autopilot tool card no longer says
  "prototype · simulated" in live mode.
- **New Model:** a 0/1 label (`is_fraud`) now gets its class balance from the profile mean, so an imbalanced binary
  label defaults to ROC AUC and an F₁-calibrated threshold; the metric follows the label the user picks; average
  precision added (AP = Σₙ (Rₙ − Rₙ₋₁)·Pₙ, with what its floor is); the profiler prefers `is_*`, `fraud`, `default`
  columns as the label. The race header no longer overlaps its axis caption.
- **Maths on screen:** the weighted-ensemble objective and the forecast metric formulas used raw `argmin_{…}`, `X_val`,
  `Σ_{t>m}`: rewritten in Unicode with every symbol (K, Xᵥₐₗ, eₜ, m) defined. F̄₁ replaces the broken "F₁ᵐᵃᶜʳᵒ".
  New formula panels: interval calibration (F̂(τ), Wilson CI, binomial tail), group-relative features (r, z, Δt with
  their where-list bound to the run's entity and columns), one-row latency τ₉₅.
- **Layout:** forecast task formula no longer runs under the next cell; the hero's verdict is plain text; Vela finding
  titles wrap instead of overflowing; the toolkit and samples sit in three columns (no orphan card at 1920); the
  Production KPI lists two names and "+N more"; frontier labels no longer collide with the axis; Home monitor ticks;
  parallel-coordinate axes of bounded scores stop at 1.
- **Mock mode** still opens with the API down (checked on every page: SIMULATED labels, the scripted runs).

## C. Final end-to-end on fresh data

`scripts/fresh_data.py` generates three seeded bank CSVs the build never saw: **card_fraud.csv** (12,000 card-not-
present authorisations, 1.7% fraud labelled 1), **complaints.csv** (6,000 complaint narratives, 7 product teams, 3%
mis-routed), **deposit_balances.csv** (7,000 accounts, next quarter's average balance). `scripts/walkthrough.cjs`
drives each through the browser: upload → label → goal → race → results → every Explain tab → register (frontier,
one-row latency) → approve as a second person → Production → Try It → batch score with drift.

| Flow | Run | Holdout | Version | Try It | Batch |
| --- | --- | --- | --- | --- | --- |
| Fraud (binary, rare) | run_834260f1 | **AP 0.360** (prevalence 0.021), ROC AUC 0.917, t = 0.143 F₁-calibrated | mv_9761dd16 Production | class 0, p̂ 0.0007 | 500 rows, 1.8% flagged, max PSI 0.029 |
| Complaints (text, 7 classes) | run_824b197b | **macro-F₁ 0.954** | mv_5181c723 Production | Mortgages | 500 rows |
| Deposit balances (regression) | run_b36cf442 | **R² 0.952**, RMSE £4,769 | mv_2c6289dc Production | £22,635 | 500 rows |

Found and fixed on the way: fraud with string labels made "genuine" the positive class (AutoGluon takes the later
label), so the generator labels fraud 1; the wizard ignored the balance of a 0/1 label (it trained on accuracy at
t = 0.5: F₁ 0.21); the first fraud generator was barely learnable (AUC 0.60) and was given realistic signal (AUC 0.92).

`make smoke` gained the same flows over the API (section `fresh`, 9 steps) plus: one-row latency + serve a faster
model, forecast interval calibration, forecast registry batch + drift, anomaly group features vs plain, anomaly
registry batch + drift, every run kind listed.

## Final smoke output

```
ENSEMBLE smoke against http://127.0.0.1:8000

── core
✓ health                             AutoGluon 1.6.3 · 9 datasets · 3 runs  (0.0 s)
✓ upload generated CSV               ds_56e53f69 · 1600 rows × 8 cols  (0.0 s)
✓ profile                            task binary · imbalance ratio 2.09 · customer_id typed id (dropped)  (0.0 s)
✓ start run + follow SSE             run_cfd39c63 · 4 model_finished events · SSE → run_succeeded  (14.6 s)
✓ leaderboard + results              4 models · best WeightedEnsemble_L2 · holdout accuracy 0.7188  (0.0 s)
✓ explain                            Σ confusion = 320 holdout rows · ROC AUC 0.7028  (0.0 s)
✓ threshold analysis                 201 thresholds · argmax F1 t* = 0.290 (F1 0.5515)  (0.0 s)
✓ calibrate threshold                calibrate_decision_threshold(f1) → t = 0.311  (0.8 s)
✓ SHAP one row                       row 0 · f(x) = 0.3067 = φ₀ 0.3227 + Σφᵢ -0.0160  (0.7 s)
✓ fairness by segment                3 groups · demographic parity Δ 0.324  (0.1 s)
✓ custom ensemble                    WeightedEnsemble_L2_Custom1 · Σw = 1 · LinearModel 0.786, RandomForest 0.214  (3.0 s)
✓ register                           mv_fa44da75 · smoke.ds_56e53f69 v1 · ready  (16.1 s)
✓ approve + promote                  four-eyes approve → Production  (0.1 s)
✓ Try It predict                     prediction no · p(yes) 0.307 vs t 0.311 · 39 ms  (0.4 s)
✓ batch score                        batch_412f8d36 · 600 rows scored + downloaded  (1.0 s)
✓ drift (PSI)                        max PSI 0.748 · utilisation PSI 0.748  (0.0 s)
✓ Gym schema + validate + start      11 families · 56 fit keys · validate ok, typo refused · run_7089d2c9 trained  (6.2 s)
✓ Vela findings → patch → retrain    1 findings · patch drift → run_d977fea7 · Δaccuracy -0.0281  (14.3 s)
✓ compare (lineage)                  parent run_cfd39c63 ← child run_d977fea7 · same holdout (320 rows)  (0.0 s)
✓ Vela question box                  answered by claude-cli in 8.4 s · 485 chars  (8.6 s)
✓ export.py → run in subprocess      exported script reproduced the leaderboard · leaderboard check: 4 models, max |Δ score| = 0  (13.6 s)
── samples
✓ regression sample: train + predict run_9d975e4f · holdout root_mean_squared_error -6453.3 · predict → 32,480  (5.2 s)
✓ text sample: train + predict       run_203eebe1 · WeightedEnsemble_L2 · holdout accuracy 0.9130 · 'billed twice' → Billing  (15.3 s)
✓ text: one-row latency + serve a faster model run_31e5043d · τ95 one row: WeightedEnsemble_L2 36.0 ms vs TfidfLinearSVM 14.3 ms · Δaccuracy -0.0335 · mv_693c6d8f serves TfidfLinearSVM (version τ95 16.2 ms)  (42.3 s)
── autopilot
✓ autopilot: start → baseline → inbox ap_95f50754 · baseline run_89bc58da macro-F1 0.9020  (54.1 s)
✓ autopilot: inbox                   693 items · {'label': 331, 'duplicate': 303, 'outlier': 59}  (0.0 s)
✓ autopilot: inbox recall (label_noise.json) recall of planted flips 252/300 = 84.0% in the top 331 · precision 76.1%  (0.0 s)
✓ autopilot: bulk answer + resume    bulk-accepted 693 (≥ 0.5 confidence) · resumed  (0.0 s)
✓ autopilot: retrain → finish        7 steps · macro-F1 0.9222 → 0.9262 (Δ +0.0039) · 1 dataset version(s)  (84.3 s)
✓ autopilot: requirements check      MET · f1_macro 0.9262 >= 0.92 ✓ · p95_latency_ms 33.74 <= 50.0 ✓ · decided by ['layer-2']  (0.0 s)
✓ autopilot: Vela question box       answered by computed · No model available: the claude CLI and the Anthropic API both failed or are off (ENSEMBLE_…  (19.8 s)
✓ autopilot: layer 1 only (no LLM), auto ap_0c0df2b4 · no LLM · auto-accepted c ≥ 0.9 · 6 runs · NOT MET · Δf1_macro +0.0051  (339.8 s)
── forecast
✓ forecast: mapping + preview        6 series × 730 days · freq D · m = 7 · 168 future rows (known covariates)  (0.2 s)
✓ forecast: run + SSE                run_cac92122 · 5 started / 5 finished · ensemble event · SSE → run_succeeded  (18.7 s)
✓ forecast: leaderboard + weights    best WeightedEnsemble holdout MASE 0.515 (SeasonalNaive 1.147) · w = DirectTabular 0.83, ETS 0.16, Chronos[bolt_tiny] 0.01 · 2 backtest windows  (0.0 s)
✓ forecast: per-item errors          MASE 0.515 · WQL 0.0982 · coverage of [q0.05, q0.95] 100.0% · worst item cards-online MASE 0.747  (0.0 s)
✓ forecast: interval calibration     n = 168 · [q0.05, q0.95] covers 168/168 = 100.0% vs nominal 90% (95% CI 97.8%–100.0%, P(K ≥ k) = 2.1e-08) → too_wide · backtests: SeasonalNaive 96%, DirectTabular 98%, ETS 92%, Chronos[bolt_tiny] 88%, WeightedEnsemble 99%  (0.0 s)
✓ forecast: fan chart data           6 items · history 120 + holdout 28 + future 28 · q0.05 ≤ q0.5 ≤ q0.95 everywhere · 2026-11-18 → 2026-12-15  (0.0 s)
✓ forecast: download CSV             forecast.csv · 168 rows × 10 cols (mean + 7 quantiles)  (0.0 s)
✓ forecast: CSV upload + mapping + run ds_7a91c661 mapped branch/date/txns + known ['is_holiday'] + static ['segment'] · run_0b1e481a WQL 0.0753 · future covariates: one season back (m = 7)  (12.2 s)
✓ forecast: forecast from new history history to 2026-10-31 → 168 rows forecast by WeightedEnsemble in 6.64 s · covariates from one season back (m = 7)  (6.8 s)
✓ forecast: register + model card    mv_dc9085cd · smoke.transaction-volume.forecast v1 · card §11 Forecasting · forecast all items 1021 ms  (10.1 s)
✓ forecast: registry batch + drift   batch_cbb162a6 · history to 2026-09-30 → 6 series × 28 = 168 rows forecast · PSI volume 0.000, holiday 0.000, payday 0.000 · PSI(ŷ) 0.041  (2.0 s)
── anomaly
✓ anomaly: run + SSE                 run_f07751dd · ψ = 256 · c(ψ) = 10.245 · 300 trees · s* = 0.5498 · 90 flagged · LOF ρ = 0.48 · 2 models  (6.1 s)
✓ anomaly: top anomalies + reasons   c = 0.02 → s* = 0.5385, 120 flagged · #1 row 415 s = 0.682: amount_usd_over_entity_mean = 56.22 (z = +124.9 vs median 0.6356); amount_usd_entity_z = 1  (0.0 s)
✓ anomaly: precision@k (hidden truth) IF P@10 1.00 · P@50 0.94 · P@90 0.71 · AUC 0.990 · LOF P@90 0.63 (sample only)  (0.0 s)
✓ anomaly: group-relative features vs plain amount_usd_over_entity_mean, amount_usd_entity_z, log_hours_since_entity_prev · amount spikes found 0/18 → 8/18 · P@90 0.61 → 0.71 · AUC 0.954 → 0.990 (plain run run_bf913820)  (7.0 s)
✓ anomaly: score a new batch         ab_e53877a1 · 500 rows · 13 flagged at s* · 5 injected rows ($25k at 03:00) at percentiles 0.998, 0.995, 0.998, 0.994, 0.998 · downloaded  (1.5 s)
✓ anomaly: register + Try It         mv_df2bf7b4 · v1 ready · card §11 · Try It: s = 0.712 (pct 1.000) flagged — amount_usd = 9,000 (z = +160.1 vs median 60.76); distance_fr  (3.2 s)
✓ anomaly: registry batch + drift    batch_678b833c · 800 rows · 18 flagged at s* · 6/8 rows with amount ×30 flagged · max PSI 0.011 · PSI(s) 0.262  (2.0 s)
── import
✓ import: fit + zip + import         tiny predictor fit here (WeightedEnsemble_L2) · zipped · run_e7ad0f4e · 3 models · holdout ROC AUC 0.7699  (10.4 s)
✓ import: explain + predict          explain ✓ · importance top credit_score · predict 3 rows → ['No', 'No', 'No']  (0.3 s)
✓ import: register + Try It          mv_192fa5c6 ready · card engine 'AutoGluon TabularPredictor (imported)' · Try It No p(Yes) 0.061  (19.6 s)
✓ import: server path + validation   run_4dde8ca9 from a server path · 3 models · wrong dataset refused: The predictor predicts 'defaulted', which is not a column of Loan amou  (7.7 s)
✓ runs list: every kind (Experiments, Home) GET /api/runs?kind=all (Experiments, Home) · 24 runs · anomaly 2 · forecast 2 · import 2 · tabular 18  (0.1 s)
── fresh
✓ fresh card_fraud: upload → train   ds_496c9d9d 12,000 × 12 · task binary · run_e0445c82 4 models · best WeightedEnsemble_L2 · holdout average_precision 0.3596 · t = 0.143 (F1-calibrated)  (20.4 s)
✓ fresh card_fraud: explain          positive class 1 (fraud) · ROC AUC 0.9168 · AP 0.3596 vs prevalence 0.0208 · top feature three_ds_passed  (0.1 s)
✓ fresh card_fraud: register → approve → Try It → batch mv_19179374 approved → Production · Try It → 0 (46 ms; why: device_age_days, merchant_type) · batch_e23fef2a 400 rows scored · max PSI 0.044  (30.9 s)
✓ fresh complaints: upload → train   ds_ea76c374 6,000 × 6 · task multiclass · text ['narrative'] · run_f46d12ac 3 models · best WeightedEnsemble_L2 · holdout f1_macro 0.9542  (8.1 s)
✓ fresh complaints: explain          7 classes · confusion Σ = 1200 · top feature narrative  (0.1 s)
✓ fresh complaints: register → approve → Try It → batch mv_1f5fc2a9 approved → Production · Try It → Current accounts (16 ms; why: narrative, channel) · batch_4ac788c7 400 rows scored · max PSI 0.025  (14.5 s)
✓ fresh deposit_balances: upload → train ds_8f701f58 7,000 × 12 · task regression · run_5961c5a6 4 models · best WeightedEnsemble_L2 · holdout root_mean_squared_error -2434.3349  (8.3 s)
✓ fresh deposit_balances: explain    R² 0.9686 · RMSE 2,434 · MAE 1,173 · top feature balance_now_gbp  (0.0 s)
✓ fresh deposit_balances: register → approve → Try It → batch mv_c1e9ede1 approved → Production · Try It → 4,781 (177 ms; why: balance_now_gbp, salary_credited) · batch_508bd4f9 400 rows scored · max PSI 0.036  (15.6 s)
── reset
✓ reset: POST /api/admin/reset       throwaway API · before: 1 run(s), 7 datasets → reset (deleted 1 run, 7 dataset rows) → 0 runs, 6 samples regenerated  (11.4 s)

65/65 steps passed in 858 s
```

(The autopilot section's question box was answered by the computed fallback in this run: the Claude CLI did not answer
within its timeout; the step passes either way and the UI says which answered.)

## Screenshots

All in [`S9/`](S9/), each page at `_1366.png` (1366×768) and `_1920.png` (1920×1080), live API.

| Page | Files |
| --- | --- |
| First run after `make reset`: Home, Experiments, Explain, Registry, Anomaly | `00-first-run-*` |
| Home with runs of every kind | `01-home` |
| New Model: dataset step · results of the fraud run | `02-new-model-dataset`, `03-new-model-results` |
| Explain: Performance · Threshold · Features & SHAP · Errors · Fairness | `04`–`08` |
| Experiments with the type filter | `09-experiments` |
| Registry · Register (frontier) · one-row latency and the faster model | `10-registry`, `11-register`, `11b-register-onerow.png` |
| Model card · Try It · Batch & Drift (tabular) · Batch & Drift (anomaly version) | `12`, `13`, `14`, `14b` |
| Training Gym Guided · Advanced | `15`, `16` |
| Vela sidecar | `17-vela` |
| Autopilot | `18-autopilot` |
| New Forecast setup · forecast run · interval calibration | `19`, `20`, `20b-forecast-calibration.png` |
| Anomaly setup (group features) · anomaly run | `21`, `21b` |
| Import · Settings | `22`, `23` |
| The browser walkthrough, 15 stops per CSV (1600×1000) | [`S9/walkthrough/`](S9/walkthrough/): `fraud-*`, `complaints-*`, `deposits-*`, `results.json` |

## Still not right

- Forecast intervals on the sample are too wide (shown, explained, not rescaled). A conformal or per-model quantile
  recalibration would be the next step.
- Velocity bursts in the card sample: 2/18 found even with Δt; an isolation forest dilutes one short gap among many
  features. A rule on Δt, or a dedicated velocity score, would complement it.
- One-row latency of text ensembles: the faster option is offered with its measured cost, but an online-serving budget
  below ~20 ms on 4 CPUs needs a TF-IDF-only or refit model.
- A CSV uploaded without naming its target is stratified on the profiler's label guess, so its holdout differs from
  the same file uploaded with the target (deposits: RMSE £2,434 over the API with the target vs £4,769 in the browser,
  same data and config; heavy-tailed balances make RMSE sensitive to which accounts land in the holdout).
- The Vite bundle is one 1 MB chunk (a build warning, not an error).
- Playwright suites were not run (per the build plan's change); only the stale assertion was updated.
