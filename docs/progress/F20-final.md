# F20 · Final verification

Status: **demo walkthrough green (47/47 numbers, 0 console errors) after 3 product fixes; clean-clone smoke run 1
98/98 when stopped; run 2 and the pytest rerun cancelled by the orchestrator (relaying the user).** The walkthrough took six attempts. The first five found the
bugs below, and the last one is fully green. The matrix's numbers come from the existing progress notes and smoke
outputs. None of them comes from a new run, apart from the clean-clone smoke and the walkthrough reported here.

## What F20 did

- **Clean clone, run 1 (stopped early).** `git clone --branch motion-components … /tmp/clean` (no data, no
  node_modules) → `make setup` in **26 s** (uv cache) → `make smoke` (the full default).
  - It was stopped by request partway through the `monotone` section, at **98 passed, 0 failed**:
    core 21/21 · samples 3/3 · autopilot 8/8 · forecast 12/12 · anomaly 10/10 · import 5/5 · fresh 9/9 · latency 8/8 ·
    targets 6/6 · splits 7/7 · cost 7/7 · monotone 2/2 (so far).
  - The text one-row latency step in that run: WeightedEnsemble_L2 τ₉₅ 18.0 ms vs LightGBM 9.2 ms, Δaccuracy −0.0030,
    and it serves LightGBM (the version's τ₉₅ is 9.6 ms).
  - Run 2 (after `make reset`) was not done (cancelled). The last complete full run is F19's **171/171 in 2,509 s**.
- **pytest fast tier:** not rerun (cancelled). The last result is F19's **117 passed, 16 deselected**.
- **Explain gets its own sidebar entry** (with a new icon). Before, the Explain page highlighted Experiments (an open
  issue from F8 and F19). `npx tsc --noEmit` is clean.
- **`scripts/demo_walk.cjs` (new)** walks `docs/demo-script.md` click by click in headless Chromium at 1920×1080. It is
  a walkthrough, not a test suite.
  - It covers the prep for part 8, then parts 1–8: New Model with an out-of-time split → the random twin → Gym cost
    and monotone → cost per 1,000 and the ROC AUC twin → Monotonicity → Register → Reason Codes → Try It on the first
    adverse holdout row → Batch → Model Card → Scorecard → Learning Curve → Model Doctor → Monitoring (challenger,
    stream, alerts, retrain) → the champion's Doctor.
  - It checks that each number the script promises is on screen, within a stated tolerance, and saves a screenshot
    per stop in `docs/progress/F20/`.
  - Usage: `node scripts/demo_walk.cjs [ui_url] [out_dir]` on a fresh workbench (`make reset`, `make dev`).

### Walkthrough result (final run, merged head, fresh `make reset`)

**7/7 steps, 47/47 promised numbers on screen, 0 console errors, in 5 min 12 s** at 1920×1080.
`docs/progress/F20/walkthrough.json` holds every check with the number seen.

| part | on screen (script's number) |
| --- | --- |
| prep | champion holdout ROC AUC 0.7765 |
| 1 | warning names `application_date`, 1.49 rows/customer; cut-off 2024-06-20; train 7,675 · tuning 1,925 · holdout 2,400; OOT 0.7150; random 0.7537; optimism +0.0386 |
| 2–3 | t* 0.091; split kept out-of-time; GBM_MONO, XGB_MONO, CAT_MONO; **702.5** per 1,000 at t 0.061; twin 857.1; Δ 154.6; 0/200 ICE curves violate |
| 4 | Try It p̂ 0.093 with R09 debt_to_income · R12 months_employed · R03 income_usd · R10 utilization; batch 126 adverse of 400; card diagnostics: out-of-time, c_FN 10 |
| 5 | ΔAUC +0.0010; scorecard 0.6977 vs served 0.6987 |
| 6–7 | verdict noisy, gain per doubling +0.0207; points n = 768 / 1,919 / 3,838 / 7,675; Overfitting (minor), 4.4× the noise |
| 8 | PSI(debt_to_income) 0.409 in 2025-07, first drift alert 2025-07; observed 0.351 vs p̂ 0.250 (z +9.0); challenger ΔAUC −0.0020; retrain window 2025-04-01 … 2025-09-30, 7,200 rows; retrained run 0.7739; champion's Doctor: Shift, Retrain On Recent Data |

Screenshots (`docs/progress/F20/`, full 1920×1080):
- `00-prep-registry`
- `01-split-random-warning`, `01-split-oot`, `02-results-oot`, `03-split-compare`
- `04-gym-cost`, `05-gym-monotone`, `06-cost-compare`, `07-explain-monotonicity`
- `08-register`, `09-reason-codes`, `10-try-it-reason-codes`, `11-batch-reasons`, `12-model-card-diagnostics`
- `13-explain-scorecard`, `14-explain-learning-curve`, `15-explain-doctor`
- `16-monitoring`, `17-monitoring-retrain`, `18-champion-doctor`

### What the walkthrough found and F20 fixed

1. **Registry crash right after Register (real bug).** While a version builds, the API returns `result: {}`, and
   `LiveRegistry` read `detail.result?.version.problem_type`. The page went blank: "Cannot read properties of undefined
   (reading 'problem_type')". It is now `result?.version?.` in `LiveRegistry`, `TryIt` and `ReasonCodes`. The smoke
   never renders the UI, so it could not see this.
2. **The Gym kept an F₁ threshold under a business cost (real bug).** "Tune In Gym" loads the run's body with
   `calibrate_threshold: "f1"`. Switching Business cost on left it there, so the cost run's threshold was tuned for F₁
   (t 0.140), not on the cost (t 0.061). The demo's headline became 860.8 per 1,000 instead of 702.5, and Δ against
   the ROC AUC twin was −3.8 instead of +154.6.
   - Turning cost on now resets `calibrate_threshold` to `auto`, which the API resolves to `expected_cost`.
   - The threshold card says so ("auto = expected_cost under a business cost").
   - `demo_numbers.py` sends `null`, which is why it never showed this.
3. **The demo script's part-8 prep drifted from its numbers.** The prep kept `application_date` as a feature on
   loan-stream. Every 2025 batch is then out of range, and a drift alert fires from 2025-01 (PSI 0.359 on
   application_date). With that, the AUC was 0.7826 and the challenger Δ −0.0083, while the script quotes 0.7765 and
   −0.0020, which come from the smoke's body, where the column is dropped. The script now says to set
   `application_date` to **ignore**, with the reason.
4. Script wording: the learning-curve button is "Run the learning curve". The Gym also lists the scorecard's
   monotone binning among the families that honour a constraint.
5. **Explain has its own sidebar entry** (an open issue since F8).

Walkthrough-only fixes, which were not product bugs:
- A sample's target is named on upload (no Confirm button).
- "Train Another" between New Model runs.
- The Gym's footer Start Training is `gym-start-now`.
- The learning-curve button is `lc-run`.
- Tighter tolerances on the scorecard AUCs and the doctor's z.

**Merges.** Another session pushed seven "Lean refactor" commits (L1–L6) to `motion-components` during F20, in two
batches. Both merges went through with no conflicts and `tsc` is clean. The final walkthrough ran on the merged code.
- The first merge reloaded the dev API in the middle of a walkthrough and killed one month's monitoring batch.
  That run was discarded and redone.
- The fast pytest tier and the smoke were not rerun on the merged code (cancelled on request), so the refactor is
  verified only by this walkthrough.

## Feature matrix

Status values: **done**, **done with caveat** or **partial**. "smoke `x` · step" names a section and step of
`scripts/smoke.py`. Screenshot paths are relative to `docs/progress/`. Each number is the latest one the notes give.

### Round 1 (S1–S9)

| use case | status | evidence number | where shown |
| --- | --- | --- | --- |
| Binary New Model + live race (fresh fraud CSV) | done | card_fraud holdout AP 0.3596 (prevalence 0.0208), ROC AUC 0.9168, t = 0.143 (F₁-calibrated) | smoke `fresh` · "fresh card_fraud: upload → train"; `S9/03-new-model-results_*`, `F19/03-results-credit` |
| Multiclass / text (TF-IDF models) | done | complaints macro-F₁ 0.9542 (7 classes); support-tickets accuracy 0.9130, 'billed twice' → Billing | smoke `fresh` · "fresh complaints: upload → train"; `samples` · "text sample: train + predict" (0.9130 again in F20 run 1) |
| Regression | done | deposits R² 0.9686, RMSE 2,434 (API = UI since F4); loan-amount RMSE 6,453.3 | smoke `fresh`, `samples` · "regression sample: train + predict" |
| Custom ensemble | done | WeightedEnsemble_L2_Custom1, Σw = 1 (LinearModel 0.786, RF 0.214) | smoke `core` · "custom ensemble" |
| Explain (Performance, Threshold, SHAP, Errors, Fairness) | done | argmax-F₁ t* = 0.290 (F₁ 0.5515); calibrated t = 0.311; SHAP f(x) 0.3067 = φ₀ 0.3227 + Σφ −0.0160; DP Δ 0.324 | smoke `core` · "explain", "threshold analysis", "SHAP one row", "fairness by segment"; `F19/05`, `F19/07` |
| Experiments (every run kind) | done | 24 runs listed: anomaly 2 · forecast 2 · import 2 · tabular 18 | smoke `import` · "runs list: every kind"; `F19/26-experiments` |
| Training Gym | done | 11 families · 56 fit keys · a typo refused · run trained | smoke `core` · "Gym schema + validate + start"; `F19/16-gym-goal`, `F20/04`, `F20/05` |
| Vela findings → patch → retrain, question box | done with caveat | planted leak found; patched retrain Δaccuracy −0.0281; CLI answer 6.6 s (F20 run 1) | smoke `core` · "Vela findings → patch → retrain", "Vela question box" |
| Autopilot (cleanlab inbox, layers 1/2) | done | inbox recall 252/300 = 84.0 %; layer 2 MET (F̄₁ 0.9242 ≥ 0.92, p95 29.7 ≤ 50 ms in F20 run 1); layer 1 alone MET, Δ +0.0349 (F17) | smoke `autopilot`; `F19/25-autopilot` |
| Registry · four-eyes · model card · audit | done | 169.3 MB → 148.4 KB, 14.9 → 7.29 µs/row; the registrant cannot approve | smoke `core` · "register", "approve + promote"; `F20/00`, `F20/08`, `F20/12` |
| Try It (with SHAP reasons) | done | p(yes) 0.307 vs t 0.311 | smoke `core` · "Try It predict"; `F20/10` |
| Batch scoring + PSI drift (all kinds) | done | tabular utilisation PSI 0.748; anomaly version 5/8 spiked rows flagged; forecast PSI(ŷ) 0.046 | smoke `core` · "batch score", "drift (PSI)"; `F20/11` |
| Forecasting | done | MASE 0.521 vs SeasonalNaive 1.147; WQL 0.0433 | smoke `forecast` · "forecast: leaderboard + weights"; `F19/22-forecast` |
| Anomaly detection | done | P@10 1.00 · P@50 0.94 · P@90 0.80 · AUC 0.996 | smoke `anomaly` · "anomaly: precision@k (hidden truth)"; `F19/23-anomaly` |
| Import predictor | done | 3 models, holdout ROC AUC 0.7699; wrong dataset refused | smoke `import` · "import: fit + zip + import" |
| export.py reproduction | done with caveat | leaderboard max \|Δ score\| = 0 (also quantile, monotone); not exact under a binding time limit | smoke `core` · "export.py → run in subprocess" |
| Reset | done | 0 runs after reset, 6 samples regenerated | smoke `reset` |
| Mock mode (API down) | done with caveat | every page shows SIMULATED; checked by hand only (S9) | no smoke step |
| Fresh-CSV browser walkthrough | done | 3/3 flows, 0 console errors | `S9/walkthrough/` |

### Round 2 (F1–F19)

| fix | status | evidence vs acceptance | where shown |
| --- | --- | --- | --- |
| F1 forecast intervals | done | 90 % band covers 168/168 → **152/168 = 90.5 %** (Wilson 85.1–94.1 %), target [0.85, 0.95]; MASE +1.2 % (limit 2 %) | smoke `forecast` · "forecast: conformal recalibration (F1)"; `F1/03` |
| F2 velocity + supervised fraud | done with caveat | bursts **13/18** (≥ 10); P@90 **0.80** (≥ 0.71); supervised AP 0.242 → 0.721; spikes 8 → 7/18 | smoke `anomaly` · "anomaly: velocity features (F2)", "anomaly: train supervised (PU, F2)" |
| F3 one-row latency | done with caveat | support-tickets τ₉₅ **2.76–4.4 ms** (≤ 10 ms) at Δaccuracy −0.030; serving a faster model: LightGBM 9.6 ms (F20 run 1) | smoke `latency` · "latency: register infer_limit 10 ms, batch size 1"; `F3/02`, `F19/27` |
| F4 target confirmation + splits | done | API = UI: 1,400/1,400 holdout rows identical, RMSE 2,434.33 both (before 2,434 vs 4,769) | smoke `targets` · "targets: API and UI holdouts identical" |
| F5 LLM layer | done with caveat | CLI p95 11.36 s; SDK p95 0.67 s **against a fake API only**; timeout → computed text in 1.02 s; `/api/health` max 5,088 → 79 ms | smoke `llm` |
| F6 out-of-time / group splits | done | random 0.7720 vs OOT **0.7144**; **0 shared customers** under the group split; demo 0.7537 vs 0.7150 | smoke `splits`; `F20/01`–`03` |
| F7 business cost | done with caveat | the cost run is still below the ROC-AUC run, but Δ is not significant: **522.1 vs 553.8** per 1,000, Δ +31.7 [−29.6, +90.9] (F19). Demo (OOT, monotone): 702.5 vs 857.1, Δ +154.6 [+86.2, +225.9] | smoke `cost` · "cost: cost-optimised vs ROC-AUC-optimised"; `F20/06` |
| F8 monotone constraints | done | debt_to_income ↑: **0/200** ICE curves violate (195/200 unconstrained); PD non-decreasing on the grid | smoke `monotone` · "monotone: served model PD on a grid"; `F20/07` |
| F9 quantile regression | done | [q0.1, q0.9] coverage **0.7879** raw, **0.8068** conformal (≈ 0.8) | smoke `quantile`; `F19/04` |
| F10 reason codes | done with caveat | **515/517** adverse rows carry 4 codes (2 have only 3 risk-raising features); 100 % sign-consistent; demo 126/126 | smoke `reasons` · "reasons: batch scoring with reason columns"; `F20/09`–`11` |
| F11 scorecard challenger | done | trains, table exported (re-scores 300/300); ΔAUC **+0.0248 [+0.0058, +0.0414]**; out of time +0.0010 | smoke `scorecard`; `F20/13` |
| F12 learning curves | done | 4 points + 2N/4N on both samples; both verdicts *noisy* (credit Δ₂ +0.0056 [0, +0.0197]) | smoke `learning`; `F20/14` |
| F13 active learning | done with caveat | after 2 rounds active 0.9384 vs random 0.9322, d̄ **+0.0062 [−0.0161, +0.0285]**: higher on the mean, not significant | smoke `active` · "active: active vs random (acceptance)" |
| F14 annotators | done | worst annotator dara found ✓; CROWDLAB **0.9927** vs majority **0.9033** (McNemar p 2.8e-34) | smoke `annotators` |
| F15 adversarial validation | done | shifted AUC **0.7655**, debt_to_income rank 1; unshifted **0.4984** | smoke `shift` · "shift: registry batches (acceptance)" |
| F16 weak slices | done | planted slice at **rank 2** (target 1–3) of 882 tested, q 1.3e-6 | smoke `slices` · "slices: planted slice (acceptance)" |
| F17 Model Doctor | done | **6/6** scenarios diagnosed correctly | smoke `doctor` · "doctor: determinism + acceptance"; `F20/15`, `F20/18` |
| F18 monitoring + challenger | done | first drift alert **2025-07** (PSI 0.409), none before; challenger ΔAUC −0.0020 [−0.0044, +0.0003]; retrain 0.7510 → 0.7739 | smoke `monitor`; `F20/16`, `F20/17` |
| F19 integrate + polish | done | full smoke **171/171 in 2,509 s**; pytest fast 117 passed; 0 px overflow | `F19/` |
| F20 final verification | partial (stopped by request) | clean clone `make setup` 26 s; smoke run 1 **98/98** when stopped (in `monotone`); demo walkthrough **47/47**, 0 console errors | this note, `F20/` |

## Remaining limits

Consolidated from S1–S9, F1–F19 and the README, without duplicates. Limits that a later session fixed are listed at
the end.

**Verification (F20)**
- Clean-clone run 2 after `make reset`, and the pytest rerun, were not done (cancelled on request). Run 1 was stopped
  at 98/98 in `monotone`. The sections from `monotone` to `reset` were last green in F19.
- Playwright suites have not been run since S7. The full smoke takes ≈ 42–46 min on 4 CPUs and leaves runs behind
  (`make reset`).

**Forecasting**
- The conformal fix assumes the backtest windows look like the future, and c_τ is fitted on the same windows that
  weight the ensemble. AutoGluon's raw band still covers 168/168.
- There is one c_τ per level for all horizon steps. Per-series scaling needs ≥ 60 backtest points per series.
- W = 5 windows costs time (18 vs 14 s) and MASE +1.2 %. Chronos-2 and the high/best presets download weights on first
  use and are slow on 4 CPUs.

**Anomaly**
- Velocity features trade signals: amount spikes 8 → 7/18, night/foreign 18 → 16. 5/18 bursts are still missed.
- The supervised PU model learns from the forest's own top picks, so the anomaly score nearly determines ŷ. The
  Registry can't score raw transactions with it.
- Velocity windows are fixed (1 h, 24 h). Labels belong to the dataset.

**Latency / serving**
- ≤ 10 ms on text costs ≈ 3 accuracy points (0.9130 vs 0.9433). No n-gram model gets under ~13 ms.
- Numbers move ±30 % with load. An `infer_limit` met on the candidate table served at 13.1 ms once.
- Pipeline pruning uses AutoGluon's private `_remove_features_out`. Distillation gives a worse student, and hard-label
  distill crashes in 1.6.3.
- In-process serving runs on the API event loop. A quantile one-row call takes ≈ 200 ms.

**Splits & validation**
- Under a time split, AutoGluon's bag folds are random inside the training period; only `tuning_data` is forward in
  time. Group splits always bag (≈ 5× fit time).
- The OOT gap (0.058) is about one holdout CI width, and the two holdouts are unpaired.
- Explain, the model card and drift treat tuning rows as training.
- Random is the wizard default (with a warning). The Gym's split card has no timeline or cut-off picker. Autopilot
  has no split or cost picker.
- Splits are keyed by target and strategy, not seed or share. Integer yyyymmdd dates are not detected.

**Cost / metrics**
- F7's saving is no longer significant (Δ +31.7 [−29.6, +90.9]). Most of it is the threshold.
- The Bayes threshold assumes calibrated p̂, and temperature scaling is skipped under 3,000 validation rows.
- Multiclass cost is served as arg-max.
- Monotone constraints: only GBM/XGB/CAT (+ scorecard). The ICE check is grid evidence, not a proof. Ordinal
  categories can't be constrained.
- Quantile: CQR is a constant shift per level (coverage by group 0.76–0.82). Cost, threshold, monotone and distill
  are refused.
- Scorecard: the shown table is a refit (bag 0.7538 vs refit 0.7492). Binning is greedy, and the scorecard can't be
  the served model.

**Explainability**
- Explain covers the run's best model only. Fairness is descriptive, with no mitigation.
- Kernel-SHAP reason codes are sampled: the top-4 set matches the exact game on 84 % of rows and is stable under noise
  on 80 %. Reason statements are templates for compliance review.
- export.py is not exact under a binding time limit or for pseudo-labelled TF-IDF (1/1,600 rows flip).

**Data-centric (F12–F16)**
- Learning curves read *noisy* with 3 seeds. The intervals omit holdout error. One curve runs at a time.
- Active learning's gain (+0.003 to +0.008) is inside the noise, with a simulated oracle.
- Annotators are simulated, with independent errors.
- The shift check sees covariate shift only. Importance weights are partial (ESS 44.7 %).
- Weak slices also rank hard-by-nature regions, use the evaluation holdout, and depend on the chosen loss.

**Model Doctor**
- Label noise and an inherently random outcome look alike.
- Thresholds were tuned on a prototype and 6 scenarios.
- It reads the latest result of each check, so stale results persist.
- There is no leakage check inside Autopilot, and no noise check for regression or quantile runs.

**Monitoring**
- Batch thresholds are fixed, with no multiplicity control or sequential test.
- The concept change shows only in calibration, after the 3-month label lag. There is no segment-level performance.
- Per-batch adversarial AUC is started by hand. Shadow scoring and the paired tests are binary only.
- The stream is synthetic. The champion's 0.7510 on the retrain run's holdout is reported by the smoke, not on screen (the walkthrough checks the retrained run's 0.7739).
- A date column kept as a feature makes every future batch out of range (PSI fires on it at once). Nothing warns about it at Register or in Monitoring; the demo script now says to ignore it.

**LLM / Vela**
- Real Anthropic SDK latency is unmeasured (no key; tested against a fake API). The CLI path has p50 ≈ 7 s and p95
  11–20 s.
- The cache is per-process. The number guard flags numbers but doesn't block them.

**UI / ops**
- Explain shows one tab group at a time.
- The walkthrough found two UI-only bugs the smoke could not see (registry crash, Gym threshold under cost); other UI paths outside the demo are covered only by F19's screenshots. The Vite bundle is one 1 MB chunk.
- Screenshots are reduced in F19 (F20's are full 1920 width).
- Non-quantile regression intervals are ±1.96·σᵣ.
- The curve, queue, annotator and shift jobs are one at a time and not queued with fits. An API restart marks them
  failed.
- There is no sign-in: four-eyes compares typed names. MLflow stages are deprecated upstream. No GPU.

**Closed since first reported**
- Forecast 168/168 coverage → F1 · bursts 2/18 → F2 · one-row latency → S9/F3 · CSV-without-target holdout mismatch → F4
- CLI blocking `/api/health` → F5 · quantile → F9 · monitoring → F18 · Autopilot layer 1 short by 0.0002 → F17
- Gym split and cost only in the raw body → F19 · Doctor not preselecting `serve_model` → F19 · pytest fast tier failing → F19
- Flaky text-latency check → F19 · SSE one event short → F19 · Explain highlighting Experiments in the sidebar → **F20**

## How to run

```
make setup                      # venv (uv, < 1 min) + npm install
make dev                        # API :8000 + UI :5190 → open http://localhost:5190
make reset                      # empty workbench, seeded samples regenerated
make smoke                      # every section (≈ 42–46 min on 4 CPUs); SMOKE_ONLY=core,cost picks sections
node scripts/demo_walk.cjs      # walk docs/demo-script.md headless (numbers: scripts/demo_numbers.py)
```
