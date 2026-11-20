# F-A2: Doctor-side fixes for the A2 adversarial review

## Scope and method

- **Scope:** the Doctor findings in [A2.md](A2.md): C1, C2, M1, M3–M9, and the cheap minors and unclear items.
  - M2's first half (four-eyes) belongs to F-P2.
  - For M2's second half, this session adds a helper, `vela.doctor.open_critical`; F-P2 wires the gate.
- **Verification:** manual API calls, chained the way the UI makes them:
  - upload → Data Doctor → `POST /api/runs` → `GET /doctor` → `POST /review`;
  - register → batch → labels → Changes.
  - No smoke or integration suites were run.
  - `npm run build` passes, and screenshots at 1366 wide are in [F-A2/](F-A2/).

### The data

- Regenerated from A1.md and A2.md under `backend/data/realdata/`, which is not committed. The generator is in this session's scratchpad (`make_data.py`).
- Measured plants:

| Dataset | Plants |
|---|---|
| telco_planted (7,743 rows) | `svc_ticket_days` AUC 0.775; `segment_score` AUC 0.828; 564 flips in Mailed check; MonthlyCharges = −1 on 232 rows; `acct_ref`; 700 repeated customers |
| telco_leak | `plan_code` AUC 0.945 |
| credit_planted (9,900 rows, `application_month` "YYYY-MM") | P1 `contact_count_q` AUC 0.802; P2 emp_length blank for 31 % of defaults vs 5 %; P3 loan_amnt ÷ 1000 from 2024-07 (2,469 rows); P4 900 re-submissions (income ±1 %) |

- Other sets:
  - credit_planted_dd: the same, with dates written "YYYY-MM-15".
  - credit_clean: 9,000 rows, the false-alarm twin.
  - house_8k: 8,000 rows sampled from OpenML 42092, `date` as published ("20141013T000000"), `tax_assessment` ρ 0.89, m² from 2015.
  - house_8k_ref: the same, with tax_assessment renamed `ref_value_b` (integers).
  - fraud: 200k rows, 345 frauds, Amount × 100 from 2024-04-01. Used for the Data Doctor only; it was not trained.
  - approval: OpenML 29.

### Runs

- **Small:** each run was 75 s, medium_quality, `num_bag_folds` 0, `num_stack_levels` 0, one table of 690–9,900 rows.
- **Reviews:**
  - telco_planted offline, `max_experiments` 3;
  - telco_planted with the LLM (Claude CLI), `max_experiments` 2;
  - credit_planted offline, `max_experiments` 2.
- **M6:** the model was trained on credit rows before 2024-07, then a 2,469-row batch from 2024-07 to 2024-12 was scored and its labels attached.

## Before → after, per finding

| A2 | Before (A2) | After (here) |
|---|---|---|
| **C1 offline leak defence** | telco_planted offline: "At the data's limit (minor)", leakage clear, gap #1 "a finer or historical version of segment_score, svc_ticket_days", plan "the gain must come from new information" | **Doctor headline:** "Leakage (major): 'segment_score' carries 45 % of the model's importance (alone AUC 0.828) and nobody has said it is known when the decision is made. Open question…", the same for `svc_ticket_days` (44 %), plus "242 holdout rows (16 %) are not new to the model". **Ceiling:** not claimed. **Caveat:** names the question ("is segment_score / svc_ticket_days known when the decision is made?; 16 % of holdout rows are not new…"). **Data requests:** no gap names a suspect, and no "finer X" for an unanswered X. **Offline review** (rv_85c7dfd0): `ablate segment_score` Δ −0.0204 [−0.0292, −0.0123] leak-test; `ablate svc_ticket_days` Δ −0.0713 [−0.0873, −0.0552] leak-test; `group_split customerID` Δ +0.0032 (no optimism). Plan: "Confirm when segment_score is known" and "Confirm when svc_ticket_days is known", and the summary keeps the question open, with no "new information". **LLM review** (rv_ae7991a8): the same two one-column tests and confirm actions. **Rules:** see "C1 rules" after this table. |
| **m1 leak-test discrimination** | loan_grade (origination) and the real leak got the same verdict | The verdict now reads "the score uses X… confirm its timing", not "evidence for a leak". The plan compares the one-column drops. `svc_ticket_days` (−0.071, ≥ 2× the next): "leans on it far more than on any other column tested: the pattern of a column recorded after the outcome". `segment_score` (−0.020): "uses it, as it would any strong real signal; only its timing says whether that is a leak". The rule for "stands out": Δ ≥ 0.02 and ≥ 2× the next drop. Limit: the planted `segment_score` is a real leak but rates as "uses". |
| **C2 rescale cut** | "loan_amnt × 0.001 from 2024-06-15", which multiplied all 412 June rows by 1,000 (June median 8,250,000) | "loan_amnt × 0.001 from **2024-07-15**" (YYYY-MM-15) and "from **2024-07-01**" (YYYY-MM). **Applied:** 2,469 cells, exactly the planted rows; June median **7,200**, unchanged. **Other sets:** house "sqft_living from 2015-01-02" (the first 2015 date in the sample); fraud "Amount from **2024-04-01**" (F-A1 said 2024-03-28). **Cut rule:** see "C2 details" after this table. **Preview:** the finding carries the rows before → after, and the Data Report shows "Preview before Apply" (01 screenshot). |
| **M1 dates** | "YYYY-MM" and "20141013T000000" profiled as category; no time checks, unit check or out-of-time split | Both read as dates (split options, Smart Intake's date role and every time check). credit "application_month covers 2023-01-01 → 2024-12-01"; house "date covers 2014-05-02 → 2015-05-14". The unit change is now found with "YYYY-MM" (before: not found). |
| **M3 label noise** | telco_planted label check clear, telco_clean a finding and the headline; "Wrong labels…?" named Month-to-month on both, never Mailed check; ranked #1 on fraud | **Planted:** telco_planted names **"Wrong labels or missing information? PaymentMethod = Mailed check"**: "cleanlab flags 23 % … more than labels drawn from a reference model's own probabilities would give here (… z = 1.8)". It ranks 3rd, after the measured gaps. **Clean:** telco_clean, credit_clean and credit_planted get no label review (largest z: 0.15, 1.64 and 2.73; the last is below the 10-row excess). **Quick-model gain:** never a finding now ("no evidence" plus the offer to run the experiment). **Wording:** the circular "48 % of errors vs 0 % of correct rows" is replaced by the observed vs clean-label flag rate, and "new information" is not written while a label or leak question is open. **Rule:** see "M3 rule" after this table. **Not checked:** fraud (not trained). |
| **M4 near-duplicates** | Data Doctor saw 15 of 900; the prescription was exact `drop_duplicates` | **Data Doctor:** "903 near-duplicate rows (9.1 %): identical to another row but for one column", the Model Doctor's definition (numbers to 3 significant digits), with a typed `drop_near_duplicates` fix, selected. **Model Doctor:** with no entity column it prescribes "Drop Near-Duplicates And Retrain"; the review proposes the same `apply_data_fix`. **Measured** (credit_planted, rv_1cde49b6): accuracy 0.9470 → 0.9438, Δ −0.0032 [−0.0173, +0.0110], unpaired, "no measurable optimism". The re-submissions carry the same label, so they do not inflate this score. "But for two" was not added: one column already finds the planted 900. **Severity:** medium when exact duplicates are ≥ 2 % or near-duplicates ≥ 5 % (telco_clean's 186 near-duplicates, 2.6 %, stay low). |
| **M5 R\*** | credit_clean ratio 0.61 against a 0.60 threshold; text called it the unavoidable error; on credit_planted it blamed overlap | **Threshold:** 0.4 × the lower 95 % bound. Measured ratios: telco_clean 1.30, credit_clean 0.76, approval 1.94, telco_planted 1.22, telco_leak 1.44, credit_planted 0.67; none fire. **Wording:** when the model beats the estimate, the text says "not a lower bound on this table (it runs high with many columns)", in the API and the UI. **When it fires:** it names the top two features as the first leak candidates. **While a leak question is open:** the look-alike estimate is not read as the data's limit ("88 % or more is beyond these columns" is gone). |
| **M6 scale change after deployment** | "loan_amnt drifted PSI 7.90; suggested_drop: loan_amnt" | **GET /changes:** `scale_changes: loan_amnt × 0.001 (÷ 1,000)`, every quantile ratio 0.001–0.00095, "fix the feed (or rescale × 1,000 before scoring) first; retraining on it or dropping the feature is the last resort". **Suggestions:** `suggested_fix: Fix the feed: rescale loan_amnt × 1,000`; `suggested_drop` is no longer loan_amnt. **UI:** the Changes card shows it first (05 screenshot). |
| **M7 ID typing** | `ref_value_b` (tax_assessment renamed, 99.6 % unique integers, ρ 0.89) typed ID, dropped by the API, leak hidden | **API:** `POST /runs` drops only `id`; `ref_value_b` trains. **Data Doctor:** "Leakage suspect (high): ref_value_b … named like an ID or a reference, yet alone it predicts price (rank correlation +0.891): an identifier cannot do that, a value recorded with the outcome can". **Model Doctor:** "Leakage (critical): 'ref_value_b' carries 67 % … 6.9× the next" (03 screenshot). **Check:** `acct_ref` (AUC 0.51) is still an ID. **Rule:** an ID-typed column is kept, and becomes a suspect, when it is tied to the target (binary AUC ≥ 0.60, regression |ρ| ≥ 0.30). |
| **M8 number guard** | "improved AUC by 0.0512", "AUC rose from 0.7991 to 0.8504", "accuracy is 0.85" and "AUC 0.80" all passed | All four are now blocked. "AUC fell from 0.8504 to 0.7991 (Δ −0.0512 [−0.0601, −0.0423])", "dropped by 0.0512 AUC" and "Δ +0.03" (positive fact) still pass. **Rules:** see "M8 rules" after this table. **Cost:** the LLM plan had one line blocked (a Scorecard accuracy not in the journal). |
| **M9 target and fairness** | Build showed TARGET = cb_person_default_on_file while the Data Report said loan_status; "Fairness check only? person_age" pre-ticked | **Cause:** Smart Intake ignored the confirmed target and guessed `cb_person_default_on_file`. Clicking its QuickTrain suggestion then re-confirmed that column as the dataset's target. **Now:** a confirmed target is Intake's target too. credit_planted: target role `loan_status`, QuickTrain prefill `loan_status` (Intake alone still guesses cb_person_default_on_file). **Fairness:** `fairness_only` is `[]` until answered; the Data Report says "Fairness question open: person_age stays in runs until you tick it". |
| **m2 time budget** | "Time budget (major)" at 62–64 s of 60 s | Major only when models were skipped **and** the fit ran > 10 % over τ. approval: budget clear (was major). |
| **m3 informative missingness** | "low" at 0.614 vs 0.169 | **Medium** when the gap is ≥ 20 points (regression: 0.5 sd). credit_planted: medium, 0.631 vs 0.168; credit_clean: low, 0.316 vs 0.215. |
| **m4 ladder guard** | fraud Scorecard 0.054 worse, CI [−0.021, +0.124], "serve it" | A simpler rung counts as "within" only when its gap's upper bound is ≤ max(0.02, 5 % of the best) on score-like metrics, and (binary) with ≥ 50 holdout rows of each class. Otherwise the ladder says the interval is "too wide to show it is as good… so serve the best". The Doctor's "top models agree" reads the same flag. Not re-run on fraud (not trained). |
| **m7 years** | "1,900" | Year-like integers (1800–2100) print without separators in bin labels, slice labels and the Data Report numbers. |
| **m8 cached Doctor** | fraud: 10.7 s on every GET | **Cache:** the whole diagnosis is stored with a fingerprint of everything it reads (the run's result minus the Doctor's caches, the experiments and their states, the decision-time answers, the latest review, the version batches, the dataset's runs, the latency arguments, the procedure version). **Timings:** a repeated GET takes **0.01–0.02 s**, against 9–20 s to recompute (7.7k–9.9k rows). The first view after a fit reads the warm-up (0.0 s). Not measured on a 200k-row run. |
| **Unclear: caveat without its question** | "Measured with an open leak question" with no question shown | The caveat lists the questions: the columns, the overlap share, the look-alike contradiction. A small overlap alone is titled "Train–holdout overlap" and adds no leak caveat. |
| **Unclear: ladder vs ceiling wording** | "Scorecard is best" vs "Single XGBoost" | The ceiling now uses the ladder's words: "the ladder's best rung is X; Single LightGBM is … below it …; the ladder recommends Y". |

**C1 rules:**
- With no decision-time answer, a feature holding ≥ 45 % of the permutation importance, or two holding ≥ 75 % together with the second ≥ 25 %, is an open question.
- Severity is minor alone. It is major when an ablation shows the score leans on it, with overlap ≥ 5 %, or with a name hint.
- A train/holdout overlap ≥ 1 % is a finding: major from 5 % or when the seen rows score better beyond noise.
- The offline review ablates each suspect, one column at a time.

**C2 details:**
- The bins only detect the change. The cut is the first date that best separates the rows on the old and new scale.
- Every row from that date is rescaled. A per-row "which scale" filter was tried first and dropped: it left large houses (691 m²) unscaled.
- On ≥ 12,000 dated rows the check uses 24 bins, so a change in the last month of a year still has two bins.
- A measured factor within 2× of a power-of-ten unit is reported as "probably × 100 … confirm it". On fraud the medians moved × 60 because April's purchases are smaller.

**M3 rule:**
- **Reference model:** a quick model without the columns of an open leak question. The leak columns encode the recorded, flipped label and hide wrong labels.
- **Comparison:** cleanlab on the recorded labels vs on labels drawn from the reference model's own holdout probabilities (clean by construction, 30 draws).
- **Firing:** the strongest concentrated segment must beat the table-wide observed/expected ratio by z ≥ 1.75, with ≥ 10 excess flagged rows.

**M8 rules:**
- Signs are kept. An unsigned change must agree with its direction word (improved/rose vs fell/dropped).
- "From A to B" must not reverse a before → after pair and must agree with its direction word.
- A figure must match the fact at the precision written ("0.80" is not 0.85).
- A fact whose own key names a metric binds to that metric, using the metric word nearest the number.

## Clean twins (after)

| Dataset | Data Doctor high/medium | Model Doctor headline |
|---|---|---|
| telco_clean | none (low: 22 exact and 186 near-duplicates, customerID) | "Train–holdout overlap (minor): 58 holdout rows (4.1 %)… AUC 0.846 on all rows vs 0.845 ± 0.027 on new rows" (was "Label noise (minor)") |
| credit_clean | none (low: informative missingness 0.316 vs 0.215, 1 + 13 duplicates) | "Overfitting (minor)" (Vela's validation-vs-holdout optimism, unchanged); no label, leak, unit or R* alarm |
| approval | Small sample (medium), unchanged | "Leakage (minor): 'A9' carries 67 % of the model's importance (alone AUC 0.862) and nobody has said it is known when the decision is made. Open question…" (was "Time budget (major)"). This is the price of the C1 rule offline: A9 is an origination field, and only the decision-time answer can say so. |

## Open-critical helper (for F-P2, M2's second half)

`from app.vela.doctor import open_critical`

`open_critical(run_id: str, runs=None, datasets=None) -> list[str]`

- **Returns:** the run's open **critical** leak questions as sentences, or [] when there are none.
- **Source:** the Doctor's last diagnosis, stored on the run as `result.doctor.open_critical` after every fit (warm-up) and every Doctor view, minus columns answered "known at decision time" since.
- **Runs the Doctor never diagnosed:** return [].
- **From a route:** pass `runs=request.app.state.runs, datasets=request.app.state.datasets`.

## Screenshots ([F-A2/](F-A2/), 1366 wide)

1. 01-credit-dd-unit-change-preview: the row-level cut (2024-07-15) and the before → after preview.
2. 02-telco-planted-doctor: Leakage (major) on the neutral-name leaks offline, the caveat with its question, Mailed check in What's Missing.
3. 03-house-ref-value-doctor: `ref_value_b` as a critical leak, not an ID.
4. 04-approval-doctor: A9 as a minor open question on a clean table.
5. 05-changes-scale: "Fix the feed first: loan_amnt changed scale".

## Remaining risks

1. **Offline still misses a moderate neutral-name leak** that does not dominate the importance. credit_planted's `contact_count_q` (AUC 0.80 alone) holds less than the top features (the top share is 27 %). So it is not a suspect without the LLM, and a data request mentions it ("the model already leans on contact_count_q").
2. **The C1 rule asks on clean tables too.** approval's A9 becomes a minor open question. Only the decision-time answer separates a strong origination field from a leak.
3. **m1 discrimination is relative.** `segment_score`, a real leak, rates "uses" because `svc_ticket_days` drops 3.5× more. A table with one moderate leak and no stronger column gets only "confirm timing".
4. **M3 is weak evidence.** Mailed check fires at z = 1.8, against a threshold of 1.75 chosen on few tables. It was not run on fraud. Wrong labels encoded by a leak column are only visible once that column is set aside.
5. **The unit factor comes from medians.** It can be off when the mix of rows shifts (fraud × 60 for a × 100 change). Within 2× of a power of ten it is reported as "probably" and needs the owner's confirmation.
6. **M6 covers Changes only.** The monitoring alert still proposes a retrain window for the same drift (05 screenshot), and the Model Doctor's F18 shift prescription is still the retrain proposal.
7. **The R\* threshold of 0.4 rests on six tables.** The rule fired on none of them, so the leaks here were all caught by other rules.
8. **Not measured here:** m4 and m8 on a 200k-row fraud run (train small). M7's profile still types `ref_value_b` as "id" in the Build chips; the API and the Data Doctor treat it as a suspect.
9. **The dedupe experiment compares unpaired scores.** It shows no optimism on credit_planted, where the copies share labels; a table whose copies straddle the split with different labels was not tested.
