# F-A3: fixes for the A3 final verification

## Scope and method

- **Scope:** the findings in [A3.md](A3.md): N1 (critical), N2, N3 and the M6 residual (major), and four cheap minors. Nothing else was changed.
- **Verification:** manual API calls, chained the way the UI makes them:
  - upload → PATCH target → Data Doctor → `POST /api/runs` → `GET /doctor` → `POST /review {offline: true, max_experiments: 2}`;
  - register → review → transition / champion;
  - register → batch → labels → monitoring / retrain / changes.
  - No smoke or integration suites were run.
- **Runs:** every run was 60 s, medium_quality, `num_bag_folds` 0, `num_stack_levels` 0.
- **Build and screenshots:** `npm run build` passes. Screenshots at 1366 wide are in [F-A3/](F-A3/).
- **Scratch:** the scratch predictors, data dirs and the baseline worktree were deleted afterwards.

### Data

The data was regenerated from the OpenML sources named in A1–A3. It is not committed; the generator was a scratch script.

| Set | Rows | Contents |
|---|---|---|
| telco_modleak | 7,043 | telco_clean plus `visit_index = round(N(0,1) + 0.9·[Churn=Yes], 2)`, AUC alone 0.735 |
| telco_planted | 7,743 | `svc_ticket_days` (AUC 0.771), `segment_score` (AUC 0.866), 634 flips in Mailed check, MonthlyCharges = −1, `acct_ref`, 700 repeated customers |
| credit_planted | 9,900 | OpenML 43454; `contact_count_q` (AUC 0.79), emp_length blanks, loan_amnt ÷ 1000 from 2024-07, 900 re-submissions |
| credit_clean | 9,000 | the false-alarm twin |
| house_ref | 8,000 | OpenML 42092 plus `ref_value_b = 0.85·price·lognormal(0, 0.25)` |
| approval | 690 | OpenML 29 |

**For M6:**
- The model was trained on credit_clean rows before 2024-07 (6,726 rows).
- One batch of 2024-07 … 12 (2,274 rows) was scored with loan_amnt ÷ 1000, and its labels were attached.

## Before → after

| A3 | Before | After |
|---|---|---|
| **N1** telco_modleak | **Baseline run** (unpatched code, same file): Doctor "Train–holdout overlap (minor) 1.4 %"; leakage had no suspect, though visit_index is top at 31 %. **What's Missing headline:** "Errors concentrate: DeviceProtection = No … the model already leans on visit_index, Contract here, so a finer or historical version of those is a start". **Look-alike text:** "roughly 93 % or more of the error is beyond what these columns can separate". **Other gaps:** "Thin feature: Contract" | **Doctor:** "Leakage (minor): 'visit_index' (31 %, alone AUC 0.735), 'tenure' (18 %), 'Contract' (18 %) carry the most of the model's importance and nobody has said they are known when the decision is made. Open timing questions…". **Ceiling:** not judged ("A leak question is open"). **Missing headline:** "First answer the open question (is visit_index / tenure / Contract known when the decision is made?), then: …". **Caveat:** names the questions. **Look-alike text:** "while a leak question is open … not read as the data's limit". **Gaps:** no "finer visit_index"; the Contract thin-feature gap is gone; the Contract segment gap reads "First confirm Contract is known at decision time…". **Offline review** (rv_b95b0c96): `ablate visit_index` Δ −0.0367 [−0.0502, −0.0244] leak-test, which stands out, then "Confirm when visit_index is known"; `ablate tenure` Δ −0.0051 [−0.0123, +0.0022], which barely moves. After the review the Doctor says "Leakage (major)" (01, 02 screenshots). |
| **N1** telco_planted | A3: leaks at 40 % and 25 % importance were not questioned, and offline neither was ablated | **Doctor:** "Leakage (major)" with both `segment_score` (89 %) and `svc_ticket_days` (8.4 %) as open timing questions, plus the 15 % customerID overlap. **Offline review** (rv_4991d4a0) ablates both, one column at a time: segment_score Δ −0.0907 [−0.1062, −0.0754], which stands out; svc_ticket_days Δ −0.0042 [−0.0105, +0.0023]. Both get "Confirm when X is known". **Mailed check:** still named, "Wrong labels or missing information? PaymentMethod = Mailed check" (z 1.88, 86 flagged, rank 3). Note that in this regeneration segment_score dominates, so the old rule would have caught it but not svc_ticket_days (8.4 %). |
| **N2** house ref_value_b | A3: "Leakage (major) … ref_value_b … 36 %". Production with no override returned 200; champion returned 201 | **Same Doctor finding** (run_28213f50, ref_value_b 36 %). Registered by Frank, approved by Gina. **Production (Hank), no override:** 409, "Production is blocked while a leak question is open on run_28213f50: Is 'ref_value_b' known at the moment the decision is made? The Doctor flags it (open suspect, 36 % of the importance; leakage major)…". **Champion (Frank), no override:** 409, the same text. **With `override_reason`:** Production 200 and champion 201. Both audit rows carry the question and the reason. **After answering** decision-time `ref_value_b: known`, `open_critical` returns []. |
| **N3** credit_planted | A3: "Wrong labels or missing information? loan_grade = C", ranked #2 (z 4.1) | **Label reviews:** none, on credit_planted or credit_clean. **Not reproduced:** the unpatched baseline did not fire on this regeneration either, so this row shows only that it does not fire, not a before → after. **telco_planted control:** Mailed check is still detected (see above). |
| **M6** monitoring | A3: alert "PSI 7.894 on loan_amnt", GET monitoring/retrain proposes a retrain, `suggested_drop` is application_month | **Alert:** `feed`, "loan_amnt changed scale (× 0.001): fix the feed (rescale × 1,000 before scoring) (PSI 7.933; a unit change, not drift: no retrain)". **Monitoring:** `retrain.suggested` false, `feed_fixes` lists loan_amnt. **GET retrain:** `why` [], `suggested` false, and `not_for`: "…a unit change, not a reason to retrain". **Changes:** the scale text, `suggested_drop` None, and the fix "Fix the feed: rescale loan_amnt × 1,000" (05, 06 screenshots). |
| **Confusables** | "AIice" and "ᴀlice" were taken as different people from "alice" | **identity_key:** "Alice", "AIice", "A1ice", "ᴀlice", "ᴀʟɪᴄᴇ", "Аlice" and "a.l.i.c.e" all map to the same key. "Ivan" and "ivan" still match. "Bob" stays apart. |
| **Number guard: up/down from** | A reversed "up from" passed | **Facts:** with 0.8504 → 0.7991, Δ −0.0513. **Blocked:** "AUC 0.8504, up from 0.7991", "AUC 0.7991, up from 0.8504" and "AUC 0.8504, down from 0.7991". **Passed:** "AUC 0.7991, down from 0.8504" and "AUC fell from 0.8504 to 0.7991". |
| **Date profile** | application_month and the house `date` were profiled as category | Both are profiled as `date`, with a range (2023-01-01 → 2024-12-01; 2014-05-02 → 2015-05-15). The Build chip shows "date". Training types are unchanged. |
| **Unit title factor** | The title used the median factor while the fix used the snapped one | **Title vs fix:** they agree, "loan_amnt × 0.001 from 2024-07-01 (÷ 1,000)". **When the medians are off:** both are shown, e.g. a synthetic × 81: "amount: medians × 79.6, likely × 100, from 2024-06-15". |

## Clean tables (after)

| Dataset | Doctor headline |
|---|---|
| telco_clean | "Leakage (minor)": tenure 38 %, Contract 25 %, MonthlyCharges 7.9 % as open timing questions.<br>This was computed before the title tweak below; the same check is now titled "Decision-time questions". |
| credit_clean | "Decision-time questions (minor)": loan_percent_income, loan_grade, person_home_ownership. |
| approval | "Decision-time questions (minor)": A9 at 71 % (03 screenshot). |

None of the clean tables shows a critical or major headline before a review.

## Rules changed

### N1
**Who is asked:**
- Offline, the top 3 features by permutation importance (each ≥ 5 %) that no one has answered "known at decision time" are open timing questions, whatever their share (`doctor.timing_questions`).

**Severity and title:**
- These questions are **minor** on their own.
- They become **major** when a one-column ablation stands out (the m1 rule), or when another part of the finding is major (overlap ≥ 5 %, a named suspect).
- **Critical** still needs a hard leak, or the R\* contradiction together with a named or dominant suspect (the old ≥ 45 % / 75 % rule, now `dominant_unanswered`).
- When the questions are the only content (no R\* contradiction, no major overlap), the check is titled "Decision-time questions".

**What's Missing:**
- Gaps about a *named* suspect are dropped.
- A top feature's timing question keeps its segment gap, reworded "First confirm X is known at decision time; if it is: …".
- Its thin-feature ("collect a finer X") gap is dropped.
- The headline leads with the open question.
- The caveat says the gaps *and the look-alike estimate* describe a model that reads those columns.

**Review:**
- The offline review ablates each question one column at a time, top feature first (the existing rule → ablation path).

### N2
`critical_questions` (stored as `doctor.open_critical`, read by `leak_gate`) now returns:
- every non-ID suspect when leakage is critical (as before);
- an unanswered suspect the model uses (≥ 10 % of importance) that is:
  - named high by the Data Doctor (confidence ≥ 0.85),
  - or a named suspect in a major finding,
  - or a hard leak,
  - or a top-feature question whose one-column run stands out.

Answered-known columns drop out, as before.

### N3
The label check's reference model leaves out the same `timing_questions` (plus the unanswered Data Doctor suspects). It also trains without near-duplicate training rows (identical but for one column, to 3 significant digits). `GAPS_PROCEDURE` moves to missing/14, and the gap cache key includes the suspects and answers.

### M6
Changes, the monitoring alerts and the retrain proposal now share `changes_api.scale_changes_for`.
- **Alerts:** a drifted feature with a unit change raises a `feed` alert, not `covariate`. Date columns are not covariate drift.
- **Score and performance:** alerts in the same batch are marked `explained_by: unit change`.
- **Retrain:** the proposal is not suggested for any of these.
- **Changes:** `suggested_drop` skips date columns.

## Screenshots ([F-A3/](F-A3/), 1366 wide)

1. 01-telco-modleak-doctor: visit_index, tenure and Contract as open timing questions; ceiling not judged; the What's Missing caveat.
2. 02-telco-modleak-review: offline ablations; "Confirm when visit_index is known".
3. 03-approval-doctor: A9 as a minor decision-time question.
4. 04-house-ref-value-doctor: ref_value_b, a major open suspect (gated).
5. 05-monitoring-alert-unit-change: the `feed` alert, "loan_amnt changed scale (× 0.001): fix the feed".
6. 06-retrain-proposal-feed-fix: "Fix the feed first…", not suggested by the alerts.

## Remaining limits

1. **The top-3 rule ranks by importance.** A leak outside the top 3 is still not questioned offline. On credit_planted, `contact_count_q` (AUC 0.79 alone) ranks below loan_percent_income, loan_grade and person_home_ownership. Only the Data Doctor (AUC ≥ 0.90) or the LLM review names such a column.
2. **Clean tables now always carry minor questions** (3 per table) and the What's Missing caveat, until someone answers the decision-time questions. Offline, only the answer separates a strong origination field from a leak.
3. **Some clean tables may turn major after a review.** A review on a clean table can make a strong real field "stand out" (Δ ≥ 0.02 and ≥ 2× the next drop), which turns the check major and gates promotion until it is answered or overridden. approval and telco_clean were not reviewed here.
4. **The review's hypothesis titles still read "X is recorded after the outcome"** for a timing question; the verdicts and plan say "confirm its timing".
5. **N3 was not reproduced** on this regeneration (the unpatched code did not fire either). The fix is checked only as "does not fire, and Mailed check survives" (z 1.88 here vs A3's 2.9).
6. **The `date` profile type is display-level.** Training still passes the column to AutoGluon as before; QuickTrain's type override treats it as category.
7. **The M6 unit test needs the batch's whole distribution to move by one factor** (every quantile within × 1.5). A partial feed change, such as some branches only, is still read as drift.
