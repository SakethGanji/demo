# F-A1: fixes for the A1 adversarial review

- **Scope:** every critical and major finding in [A1.md](A1.md), the cheap minors, and the Section B wording.
- **Verification:** manual API calls chained the way the UI makes them, on A1's datasets regenerated from its
  descriptions; `npm run build`; screenshots at 1366 wide in [F-A1/](F-A1/). No smoke or integration suites were run.
- **Before numbers:** taken from A1.md, or from the unchanged code on the regenerated data where noted ("before (here)").
- **Parallel work:** D5 pushed during this session and was merged twice. Its D5.md covers parts of #3, #6, #8 and #16;
  this session built on them (see #3 and the label-noise note under #16).

## Run sizes (which numbers came from small runs)

The orchestrator's "train small" instruction reached this session after the runs had been made. The runs here were
not subsampled, but they were short:
- every fit used the API's default body with `time_limit` 60–180 s and one fit at a time;
- telco runs (7,043 / 7,743 rows): 120 s; approval (690 rows): 60 s; house (21,613 rows): 120 s;
- fraud (200,000 rows, full size, needed for #11 and #12): 180 s;
- reviews: max_experiments 2 (telco_leak) and 3 (telco_planted);
- the #7 / #9 checks (helper session): one 40 s fit on a synthetic 3,000-row table; #10 / #11 / #13–#15 (helper
  session): tiny fits on synthetic tables, plus a synthetic 200k-row binary run for #11's timing.
Every Data Doctor number is on the full regenerated file. D5's merged code (00184e5 and 1e55e2f) was re-run in all
the "after" Doctor and review calls above.

## The data (regenerated; under `backend/data/realdata/`, not committed)

| Dataset | Source | Plants (as A1 describes; measured here) |
|---|---|---|
| telco_clean | OpenML 42178, 7,043 rows | none |
| telco_leak | telco_clean | `plan_code`: 4 levels, post-outcome, AUC 0.927 alone |
| telco_planted | telco_clean | 7,743 rows. `svc_ticket_days` post-outcome, AUC 0.748; `segment_score` target-encoded over the whole file, AUC 0.808; 601 labels flipped (35 % of Mailed check); MonthlyCharges = −1 on 201 rows; `acct_ref` a numeric ID; 700 duplicated customers (same customerID, tenure + 1) |
| cc_fraud_200k | OpenML 1597, 200k sampled, 351 frauds | `txn_date` over 12 months; Amount × 100 from 2024-04 (64k rows) |
| house_planted | OpenML 42092, 21,613 rows | `tax_assessment` = 0.85 · price · lognormal, ρ 0.890; sqft_living in m² from 2015-01 (6,980 rows) |
| approval | OpenML 29, 690 rows | none |

The generator is in the session scratchpad (`make_data.py`), as A1's was.

## Per finding

### Critical

| # | Finding | Status | Before → after |
|---|---|---|---|
| 1 | Apply silently drops the strongest features | **fixed** | **Before:** after `dr-apply` on fraud, `decision-time` had `known: false` for V14, V12, V4, V3, V10, V11 and the next run dropped all six. **After:** Apply calls only `data-doctor/apply`. `decision-time` stays `{}` on the parent and on the fixed version. The run on the fixed version has `decision_time.dropped = []` and `unanswered = [V14, V4, V12, V11, V10, V3]`. The answers are three-state (not answered / known / not known); only an explicit "not known" drops a column. Strength-only suspects are titled "Leak or strong signal? … confirm each is known when you decide" and marked "(strength only)" (01-fraud-data-report.png). |
| 2 | A post-outcome column reads as "At the data's limit" | **fixed** | **Before** (telco_leak): leakage clear; headline "At the data's limit (minor) … Tuning will not help"; "at least 100 % of the error is beyond these columns"; gap #4 "collect information for plan_code = 3"; "within noise (z = 5.3)". **After:** headline **"Leakage (critical): 'plan_code' carries 79 % of the permutation importance, 7.7× the next feature, and the Data Doctor flags it (alone … AUC 0.914)"**. The ceiling check reads "A leak question is open: the data's limit is read only after it is answered". What's Missing carries the caveat and drops the gaps on the suspect column (04-telco-leak-doctor.png). See the rules below the table. |
| 3 | "Clean the labels" can't see real noise and reports the opposite | **fixed / partly** | **Before** (telco_planted): Doctor lever −0.0098 [−0.0176, −0.0025]; review clean_labels "rejected: worse beyond noise"; plan "the gain must come from new information"; Data Doctor "mostly where classes overlap". **After:** lever "not measurable on this holdout: cleanlab flags 5.4 % of the holdout labels too (6.3 % of training)…", with no Δ shown. Review verdict on telco_leak: `unmeasurable` (Δ −0.0009) and a plan action "Review the holdout labels, then measure cleaning". What's Missing ranks **"Wrong labels or missing information? PaymentMethod = Mailed check"** (cleanlab flags 7.5 % there vs 4.8 % elsewhere) before "Errors concentrate: Mailed check", whose request starts "If the labels check out: …". The plan no longer says "new information" while a question is open. The Data Doctor wording is "wrong labels or classes that overlap (this check cannot tell which…)", plus a by-category-level concentration note. **Partly:** cleaning is not yet scored on reviewed holdout labels (the label queue exists; the score against adjudicated labels is not wired). |
| 4 | The review loop rejects correct leak hypotheses | **fixed** | **Before:** four columns ablated together, Δ −0.0512, "rejected: worse beyond noise"; plan_code Δ −0.1297 "rejected"; no "confirm decision time" action; "drop customerID" as a no-op; Scorecard recommended on a leaky table. **After** (telco_planted, rv_1f8f892f, Claude CLI): the LLM's decision-time risks join the suspects. The journal has (1) `group_split customerID`: accuracy 0.8631 → 0.8502, Δ −0.0129 [−0.0378, +0.0120], verdict `leak-test` ("no measurable optimism"); (2) `ablate svc_ticket_days`: AUC 0.9350 → 0.9081, Δ −0.0269 [−0.0365, −0.0173], `leak-test`; (3) `ablate segment_score`: 0.9350 → 0.8901, Δ −0.0449, `leak-test`. Plan: "Confirm when svc_ticket_days is known" and "Confirm when segment_score is known". **After** (telco_leak, rv on run_c18eec9a): `ablate plan_code` 0.9609 → 0.8432, Δ −0.1177 [−0.1380, −0.0990], **kept-honest**, plus "Confirm when plan_code is known". "Serve the Scorecard" now adds "Only once the leak question above is settled…". See the review rules below the table (05 and 07 screenshots). |

**#2, the Model Doctor rules now on screen:**
- An open, unanswered Data Doctor suspect is a leak hint. Combined with dominance it is critical.
- An open suspect with ≥ 10 % of the importance is a leakage finding. It is major with a name or value hint (house: "'tax_assessment' is a Data Doctor leakage suspect nobody has answered yet … carries 39 % of the model's importance"), and a minor open question when it rests on strength alone (fraud V10).
- A model error below 0.6 × the lower bound of R* raises leakage or train/holdout overlap.
- No ceiling headline while any of these is open.
- A train–holdout gap with z ≥ 3 is no longer "within noise".

**#4, the review rules:**
- One column per ablation.
- An ablation of a non-feature entity becomes `group_split`.
- A leak test is judged by "does the score lean on it", never by "did it go up".

### Majors

| # | Finding | Status | Before → after |
|---|---|---|---|
| 5 | Mostly-numeric columns typed "row ID" | **fixed** | **Before (here):** telco_clean "Not features: customerID (a row ID), TotalCharges (a row ID)"; telco_planted "proxies: TotalCharges" (V 0.966). **After:** uploads read text columns where ≥ 90 % of non-blank cells parse ('12,000', '$12,000', '1e5') as numbers, and list the rest as a finding: "Read as numbers: TotalCharges: 11 blank". The finding is gone from the IDs, and the gender proxy is gone. Unit test on A1's mixed pattern: `['1','2',' ','$12,000','1e5',…,'n/a']` → float with 1 'n/a' and 5 blanks reported. Duplicates are computed on every column except row keys (columns unique per row). |
| 6 | Subtle plants not found | **partly** | **After, found:** see the plant list below the table. **Not found deterministically:** svc_ticket_days (AUC 0.75, neutral name) and segment_score (AUC 0.81) are not Data Doctor suspects. They are found by the review: the LLM names them, and the one-column leak tests show the score leans on them (Δ −0.027, −0.045) and put "confirm decision time" in the plan. |
| 7 | "Recalibrated" version serves uncalibrated p | **fixed** | **Before:** the new version returned the same p as v1 (0.78225); only the threshold moved. **After** (synthetic cost run): the Platt map is stored with the version and applied in the predictor. Row c100000 goes v1 p 0.12652 (t 0.1640) → v2 p **0.03620** (t 0.2000 on the calibrated scale). The batch log loss served is 0.3748 → **0.2930** (claimed 0.2934). The audit text names the map, the threshold move and the cost Δ. Older recalibrate results are refused (409, "run the experiment again"). |
| 8 | What's Missing gains measure segment size | **fixed** | **Before:** Month-to-month +0.098 (A1's independent random 55 %: +0.047); fraud segment "worth up to +0.255"; regression gaps were the expensive houses. **After** (telco_clean): "simulated perfect information on Contract = Month-to-month: +0.1228, minus +0.1027 that a random 55 % of rows gets" → **+0.0201**; DeviceProtection = No +0.1037 − 0.0880 → +0.0157; look-alike rows +0.1107 − 0.1098 → within noise, so no gain is shown. See the details below the table. |
| 9 | Four-eyes is a free-text comparison | **fixed** | **Before:** "Alice." approved after "Alice" registered; the experiment's author could approve. **After:** names are compared after NFKC normalisation, casefolding, and with punctuation and spaces removed. "Alice." / " alice " / "ALICE" → 409; the experiment author "Carol" and "carol!" → 409; "Erin" → 200. The UI says "Names, not authenticated identities: four-eyes compares names as typed (case, spaces and punctuation ignored)." The requester may still promote after another person approves (a policy call, not changed). |
| 10 | The API trains on ignored ID columns | **fixed** | **Before:** house `POST /api/runs` with `drop_columns: []` trained on `id`. **After:** `drop_columns ['id']`, `id_columns_dropped ['id']` (house run_3cc7eedb). Opt out with `keep_id_columns: true` or a `column_types` entry for the column. |
| 11 | The Results page freezes on 200k rows | **fixed** | **Before:** 46 s to show, `evaluate` 32 s, screenshot 72 s. Cause: a 300-resample bootstrap per metric over 40k rows, run during render. **After:** computed once after paint, with one sort and counts per resample. Results visible in 0.70 s, intervals 1.57 s, `evaluate` 1 ms (synthetic 200k). On the fraud run here: ladder visible in 2.1 s (09-fraud-results.png). The intervals are identical. |
| 12 | First GET /doctor takes 128–262 s | **fixed / partly** | The Doctor is computed in the API right after each fit, and a page view waits on that job rather than starting a second one. The gap-analysis fits use one LightGBM thread: the 4-thread fits spun under contention (approval 101 s → **2.3 s**, telco 11.5 s with a fit running next to it). **After**, cold first loads on an idle CPU: telco 9.9–12.3 s, house 21.8 s, approval 0.3 s, **fraud 200k 113–122 s**. Fraud is still slow cold, but that cost is now paid at the end of the fit; partial results are not streamed. |

**#6, plants now found (after):**
- **telco_planted:**
  - repeated customers: Data Doctor medium "The same customerID appears more than once: 700 values on 1,400 rows (18.1 %)". The Model Doctor finds train/holdout overlap: "249 holdout rows (16 %) are not new to the model … AUC 0.935 on all rows vs 0.928 ± 0.018 on the new rows only", with a group_split prescription.
  - `acct_ref` is "an ID" (profile type `id`, so the API drops it).
  - MonthlyCharges = −1 as disguised missing (it was already found on this copy before).
- **house:**
  - `tax_assessment` medium "Leakage suspect … may be known only after the outcome", from the regression rule |ρ| ≥ 0.85 plus a name hint for assessment / valuation / settlement words.
  - **unit change:** "sqft_living × 0.0907 from 2014-12-23 (× 0.0929, ft² → m²)", with a typed `rescale` fix.
- **fraud:** unit change "Amount × 91.6 from 2024-03-28 (× 100, pounds → pence)".

**#8, details:**
- Fraud's random baseline was negative on a 0.2 % class. Two fixes:
  - the oracle's noise now draws from the label's own distribution (a symmetric flip harmed rare classes);
  - a losing baseline is ignored.
- Regression errors are relative (|y − ŷ| / y) when the target is positive and skewed (skew > 1).
- Requests name the columns the model leans on in the segment ("the model already leans on tenure, InternetService here").
- Ranking is D5's (measured gains before simulations).

### Minors and unclear items

| # | Finding | Status | Before → after |
|---|---|---|---|
| 13 | Ladder ranks binary by AUC | **fixed** | A fraud-like run with average_precision: ladder metric `roc_auc` → `average_precision`. "Small data (12.8 events per feature)" on 200k rows no longer switches the interval to 99 %. Same-model rungs say "makes exactly the same holdout predictions…". RMSE gaps read "584 RMSE worse … (lower is better; … [-373, +1,618])". Regression rung 1 explains why it is blank. |
| 14 | 500 / 201 instead of 400 | **fixed** | `num_bag_folds: "x"` 500 → 400; `to_missing ["abc"]` on a numeric column 201 → 400; no matching cell 201 → 400; `merge_classes groups: null` 201 → 400. |
| 15 | Nasty CSVs | **fixed** | latin-1 with `;` and decimal commas, cp1252, UTF-16 with and without a BOM: 400 → 201 (encoding and separator in the note). Ragged rows: "Line 4 has 4 fields but the header has 3…". Duplicate headers: 400 naming the columns. Binary content: "looks like a binary file". Empty or one-class target: a clear 400 on PATCH / run, plus a **high** Data Doctor finding ("The target 'y' is empty…" / "…has one value ('No') in all 30 labelled rows"). |
| 16 | False alarms on clean data | **partly** | See the clean-data checks below the table. |
| 17 | The number guard checks presence | **fixed** | See the guard rules below the table. |
| B | Jargon, units, decimals | **partly** | See the wording changes below the table. |

**#16, clean-data checks:**
- **Fraud:** "Small sample (medium)" → "Few events: 351 rows of the rarest class for 30 features (12 per feature) in 200,000 rows" (low).
- **House:** grade 1 / 3 / 4 / 12 / 13 are no longer "undocumented codes": an ordinal scale whose rare ends keep the outcome's trend is excluded. Condition 1 / 2 is still listed (low), but no longer pre-selected.
- **Data Doctor clean:**
  - telco_clean shows only low / info findings.
  - approval keeps its pre-existing "Small sample (medium)" (307 rows of the rarest class); A1 counted it as no false alarm.
  - No unit-change or overlap findings on telco_clean / approval / telco_leak.
- **Model Doctor, telco_clean:** the headline is now "Label noise (**minor**)". The quick model gains +0.0111 [+0.0039, +0.0178] AUC without 15 % of the flagged rows. D5's rule had made this **major**; this session made a quick-model-only gain a minor, hedged hint ("dropping hard rows can also do this on clean labels"). It was "Overfitting (minor)" before. Still a minor headline on clean data.
- **Model Doctor, approval:** the headline is "Time budget (major)" (a 60 s fit that skipped a model). Leakage is clear, and the ceiling is not claimed (z = 3.8).

**#17, guard rules:** a number next to a metric word must come from a fact about that metric. A number written as a change ('by', 'gain', Δ, '+') must come from a change field (delta, CI, gain). A percentage must come from a share-like fact.
- Blocked: "raised AUC by 0.85 points", "85 % accuracy" (AUC 0.85 × 100), "the model reached 7 AUC".
- Passed: "AUC fell from 0.8504 to 0.7991 (Δ −0.0512)", "dropped by 0.0512 AUC".

**Section B, wording changed:**
- R_NN / Cover–Hart / irreducible → "look-alike customers who ended differently… the error no model can avoid with these columns, about X to Y (approximate)".
- "Indistinguishable twins" → "Look-alike rows with different outcomes".
- oracle → "simulated perfect information"; ablation run → "run without the column".
- "at least 100 % of the error is beyond these columns" → "the model is at or below this bound, so either the bound is loose here or the model reads something look-alikes cannot see… see Leakage".
- Money-scale numbers without 4 decimals ("+7,085" style), with "lower error is better".
- "More rows" with a CI below 0 is no longer listed as a gap.
- `q = 2.6e-04` is no longer in the concentration text.
- Not done: the plan role's failure reason in the UI; the monitoring warning on training rows; print / full-page capture of the inner scroll container.

## Clean data stays clean (after)

| Dataset | Data Doctor (high / medium) | Model Doctor headline |
|---|---|---|
| telco_clean | none (low: 11 blank TotalCharges read as numbers, 22 duplicates, 18 conflict groups, customerID) | Label noise (minor), see #16 |
| approval | Small sample (medium), unchanged | Time budget (major); leakage clear |
| fraud (signal, not a leak) | "Leak or strong signal?" question (medium, nothing dropped), unit change (planted) | Time budget (major); V10 an open, minor leak question |

## Screenshots ([F-A1/](F-A1/), 1366 wide)

1. 01-fraud-data-report: three-state decision-time answers; Apply leaves them alone; unit change.
2. 02-house-data-report: the regression leak suspect and the m² unit change.
3. 03-telco-planted-data-report: repeated customers, coded −1, TotalCharges read as numbers, acct_ref as an ID.
4. 04-telco-leak-doctor: the critical leak headline, the caveat on What's Missing, the review's plan.
5. 05-telco-leak-review: plan_code kept-honest, the Scorecard conditioned on the leak, cleaning unmeasurable.
6. 06-telco-planted-doctor: train/holdout overlap; "wrong labels or missing information?" before "collect".
7. 07-telco-planted-review: group split and one-column leak tests with confirm-decision-time actions.
8. 08-house-doctor: the open tax_assessment suspect.
9. 09-fraud-results: the 200k-row Results page (ladder visible in 2.1 s).

## Remaining risks for adversarial round 2

1. **Post-outcome columns with neutral names and AUC ≈ 0.75** (svc_ticket_days) and target encoding fitted on the full file (segment_score) are not found deterministically. Only the LLM review finds them, and the leak tests only show "the score leans on it". Offline mode will miss them.
2. **Cleaning measured on reviewed holdout labels is not wired.** The product says "unmeasurable" and points at the label queue, but never produces the clean Δ.
3. **The label-review hypothesis also appears on clean data** (telco_clean: "Wrong labels or missing information? Contract = Month-to-month"). It is framed as a question with a cheap first step, but it ranks first on a clean table.
4. **The R* contradiction rule** (model error < 0.6 × the lower bound) did not need to fire on these datasets: the leaks were caught by dominance and open suspects. Its threshold is uncalibrated; the finite-sample bias of the nearest-neighbour estimate is upward.
5. **The overlap check** finds near-duplicates only on ≤ 120k rows and ≤ 40 features. Entity detection relies on ID-like columns; a customer key named like a quantity would be missed.
6. **Fraud's cold Doctor still takes about 2 minutes.** It is computed at fit end, but a user who opens the Doctor right after the fit waits for it. There is no streaming.
7. **Four-eyes still compares names.** That is now stated on screen, but there is no authenticated identity, and the requester may promote after an independent approval.
8. **The number guard is stricter.** LLM text that quotes a level next to a gain word, or a share as a percentage from an unnamed field, is now blocked and falls back to the computed text. The fallback is safe but less fluent.
9. **What's Missing on a 0.2 %-positive table** still shows large simulated gains: +0.12 AUC on about 70 holdout positives, with wide CIs.
