# Build sessions, round 3: bank features for the POC

Round 3 adds a guided Smart Intake and the features a bank team would expect in a POC: a Smart Intake (G0) first, then one session per feature (G1–G9) in sequence on `motion-components`, then the Vela Agent (G10) and one integration session (G11). **It must stay intuitive: a user gives a dataset, the app detects what each column is for, suggests what can be built, and pre-fills every setting so the user only confirms.** It is a POC, so there is no SSO, infrastructure or deployment work. The user asked for **speed**: build the feature and check it quickly, without long test runs.

## Rules for every G-session

1. `git pull --ff-only origin motion-components`, `make setup`. Read `README.md`, `docs/lean-plan.md` and the progress notes of the features you build on.
2. **Do only your feature.** Reuse what exists:
   - the shared helpers from the lean refactor: `app/stats.py`, `app/http.py`, `engine/jobs.py`, `useFetch`, the formatters, the `HeroCell`/`Loading`/`ErrorCard` atoms;
   - F12's `prepare_fit`/`fit_predictor`;
   - the F4/F6 splits;
   - F10 reason codes;
   - the registry, the model card and the Model Doctor.

   Use AutoGluon and our existing dependencies first, and keep it simple and lean: no new frameworks.
3. Backend first, then the UI, inside the design system and the README's "Show the mathematics" rules (real notation bound to real numbers, every symbol defined). Add your feature to the model card, and to the Model Doctor or Vela where it fits.
4. **Quick checks only.**
   - Add a short smoke section for your feature: a few chained API calls that assert its acceptance numbers.
   - Run `SMOKE_ONLY=<your section>,core make smoke` and `npm run build`.
   - Take 2–3 screenshots into `docs/progress/G<n>/` and look at them.
   - No full-suite runs.
5. **Zero-config by default.**
   - Your feature must use G0's column-role detection to pre-fill its own inputs, e.g. G6 finds the duration and event columns, G4 finds surname and zip, G3 finds the loss and exposure columns.
   - Add any new role you need to the detector.
   - The feature must be reachable from the G0 suggestions, and work with sensible defaults when the user just clicks through.
   - Show the user what was detected and why, so they can override it.
6. **Never edit an existing `backend/app/samples*.py`**: the fingerprint would regenerate those samples. If you need data, add a new sample module.
7. Make small commits, push only to `motion-components`, and open no pull requests.
8. Write `docs/progress/G<n>.md` (what it does, numbers, screenshots, limits), then report to the orchestrator with `send_message`, `session_id` `session_018upNdRigdGd9A2nGzFSQXq`. Include the head SHA, the smoke result, the key numbers and the open problems.


## Target navigation (5 items; no feature removed)

The sidebar today has 12 items plus a toolbelt that duplicates three of them. Round 3 converges on this:

1. **Home**: activity and next steps.
2. **Build**: G0 Smart Intake is the entry point.
   - It folds in New Model (classification/regression), Forecast, Anomaly and Import as choices.
   - The Training Gym becomes Build's **Advanced** switch.
   - Survival, multi-label, expected loss and segmented models (G3/G5/G6/G8) are new choices here too.
3. **Models**: was Experiments, the list of runs. A run page holds Results plus its **Explain** and **Doctor** tabs. Explain stops being a top-level item.
4. **Registry**: versions, approvals, Try It, batch, monitoring, the credit report, recourse.
5. **Vela**: the G10 agent page. Autopilot becomes its "run it end to end" autonomy mode. The Vela sidecar stays as the "Ask Vela" button on every page.

Settings moves to the sidebar footer. The toolbelt is removed. **Every old route keeps working as a redirect**, so links in the docs, demo script and walkthrough don't break. G0 builds Build, G10 builds the Vela page, and G11 finishes the sidebar and the redirects and updates `docs/demo-script.md` and `scripts/demo_walk.cjs`.


## UI rules: concise, usable, simple, powerful (every G-session)

- **No new top-level navigation.** Every feature lives inside Home, Build, Models, Registry or Vela, as a choice, a tab or a panel.
- **One primary action per screen.** Advanced options sit behind the existing Advanced switch or fold-out, closed by default. Guided mode shows only what most users need.
- **Reuse before adding:**
  - the shared atoms (`HeroCell`, `Loading`, `ErrorCard`, `ApiOnly`);
  - `useFetch`, `useHashParam` and `shared/lib/format.ts`;
  - PlotFrame and the existing charts;
  - the existing table, card and fold-out styles.

  A new component needs a second user before it goes in `shared/`.
- **Merge means one code path, not two kept alive.** When a flow folds into another, its old page component, styles and API client functions are **deleted**. Only a one-line route redirect remains. No duplicate wizards, steps or panels doing the same job.
- **Consistent patterns.** The same hero strip, the same formula-and-where block, the same Apply/Run button placement, the same empty, loading and error states, and the same words for the same thing everywhere.

## Sessions

### G0 · Smart Intake: one upload, guided from there
- **Column-role detection.** It extends the existing profiler with roles:
  - target candidates;
  - date or time, entity or customer ID, money amount;
  - duration and event (survival), loss fraction (LGD), exposure (EAD);
  - surname, zip or geography;
  - a multi-label list column (delimiter detected), segment or category;
  - free text, plus columns to exclude: IDs, leaky post-outcome columns, constant columns.

  Each role comes with a confidence and the evidence for it (name patterns, value patterns, ranges, cardinality). Use simple, explainable rules and no LLM. Claude wording through the existing LLM router is optional.
- **Use-case suggestions**, from the roles, each with a reason and a one-click entry that pre-fills the right flow. Examples:
  - a binary target with a date and an ID suggests default/churn with an out-of-time and group split and a business cost;
  - a time column plus a numeric series suggests a forecast;
  - no label suggests anomaly detection;
  - a duration and an event suggest time-to-event;
  - LGD and EAD columns suggest expected loss;
  - surname and zip suggest the fair-lending check;
  - a multi-label column suggests multi-label;
  - a segment column suggests segmented models.

  Features that don't exist yet (G1–G9) appear as suggestions only once their session adds them. Make the suggestion registry a simple list that each G-session appends to.
- **This is the Build page** of the target navigation (above): the single entry point. New Model, Forecast, Anomaly and Import open from its suggestions, and the Gym is its Advanced switch.
- **A new Home/New Model start screen:** "Upload a table → here is what we found → here is what you can build." Detected roles are editable chips. The existing New Model, Forecast and Anomaly flows open pre-filled.
- **Acceptance:**
  - On every bundled sample, the detector assigns the right roles, so the target, date, ID and segment come out correctly, and the top suggestion is the right flow.
  - A fresh CSV from `scripts/fresh_data.py` gets sensible suggestions.
  - Report the role accuracy per sample.

### G1 · Counterfactuals: "what would change the decision"
For a declined (adverse) applicant on a registered binary version, find the smallest realistic changes that flip the decision at the version's threshold. The search has these properties:
- Only actionable features change. Immutable ones are age, dates and IDs; an editable list per model, like F10's mapping, holds the rest.
- F8 monotone directions are respected.
- Values stay within the observed ranges and categories.
- The output is a few diverse options, each scored by how far and how plausible it is.

Use a simple, honest method: a greedy or coordinate search over the served model through `/score`, or DiCE if it installs cleanly. Show the counterfactuals next to the reason codes in Try It and in the Reason Codes tab, plus "Recourse" in batch scoring for the first k adverse rows.

**Acceptance:** on credit-default, at least 90% of the adverse holdout rows tested (n ≥ 100) get at least one valid counterfactual that flips the served decision when re-scored, and no counterfactual changes an immutable feature.

### G2 · Credit-risk model report
A standard credit model report for binary scoring models, built from a version's holdout and its scored batches.
- Rank-ordering and validation tables:
  - Gini and KS overall and by month (or by batch);
  - a score-band table: bands, counts, bad rate, cumulative capture, lift;
  - the bad rate by score decile.
- Stability:
  - PSI of the score;
  - CSI per feature per month, reusing F18's monitoring batches.
- Vintage curves: cumulative bad rate by months on book per origination cohort. Use the F18 loan-stream sample or a new small sample with performance windows.

Export the whole report as a downloadable HTML/Markdown "Model Performance Report" and link it from the model card.

**Acceptance:** on the F18 stream, Gini by month is shown with CIs, CSI flags `debt_to_income` from month 7, and the vintage curves render for at least 6 cohorts.

### G3 · Expected loss: PD × LGD × EAD, with stress scenarios
Use a new sample module with loans that carry the default flag, the loss given default (a fraction of 0–1, only for defaulters) and the exposure at default.
- An expected-loss workflow chains three AutoGluon models: PD (binary), LGD (regression on defaulters, clipped to [0,1]; quantile is optional) and EAD (regression).
- EL = PD·LGD·EAD per loan, with a portfolio total and segment breakdowns.
- Stress scenarios are simple user-defined shocks to inputs, e.g. "income −10%" or "utilisation +15pp". They re-score and show the change in EL by segment.

**Acceptance:** the holdout EL is within ±10% of the realised loss on the sample, and a stress scenario changes the EL in the expected direction and by a reported amount.

### G4 · Fair-lending check without protected attributes (BISG)
Estimate group membership probabilities with BISG: a surname table times a geography (zip/county) table. Use bundled public Census tables, or a small synthetic stand-in clearly labelled as such if the real tables are too large or need a download.
- Run the existing F4 fairness metrics on the probability-weighted groups.
- Report adverse impact ratios and the approval-rate differences with CIs.
- Add a new sample with surname and zip, and a hidden true group, to validate the proxy (report its accuracy honestly).

**Acceptance:** the weighted adverse impact ratios on the sample are within ±0.05 of those computed with the hidden true groups, and the page labels the proxy's error.

### G5 · Segmented models
Pick a segment column (e.g. product or customer type).
- Train one AutoGluon model per segment with the same config, and compare against the single global model on the same holdout: per segment and overall, with paired CIs.
- Register the segmented set as one routed version that scores each row with its segment's model, falling back to the global model.
- Try It, batch scoring, reason codes and the model card work across segments.

**Acceptance:** on a new sample where segments behave differently, the routed model beats the global one on at least one segment, with a CI, and the overall comparison is reported honestly.

### G6 · Time-to-event (survival)
Use `scikit-survival` (Cox PH, random survival forest) or `lifelines`, whichever installs cleanly. If AutoGluon has no survival support, this is its own small engine path, like anomaly.
- Survival curves, Harrell's C-index and time-dependent AUC on a holdout.
- Predicted survival at chosen horizons (e.g. P(still a customer at 12 months)).
- Feature effects (hazard ratios for Cox).
- A churn/prepayment sample with censoring.
- Register, Try It (a survival curve per applicant), batch scoring and the model card.

**Acceptance:** C-index > 0.7 on the sample, and the curves are calibrated against Kaplan–Meier by risk group.

### G7 · Reject inference
For credit data where only approved applicants have outcomes:
- A new sample includes declined applicants with no label, and a hidden label used only for validation.
- Implement the standard methods (augmentation/reweighting, parcelling and fuzzy augmentation) on top of AutoGluon training.
- Compare each with the approved-only model on a "through-the-door" holdout built from the hidden labels.

**Acceptance:** at least one method improves through-the-door AUC or calibration over approved-only on the sample (report the real numbers and CIs), and the page explains the assumption.

### G8 · Multi-label classification
Each row can carry several labels (e.g. complaint topics). Use AutoGluon one model per label (the MultilabelPredictor pattern from the AG docs) with a shared feature pipeline.
- Upload with a delimited label column or one column per label.
- Metrics: micro/macro F1, Hamming loss and per-label PR.
- Per-label thresholds.
- Try It and batch scoring return a label set.
- A complaint sample with overlapping topics.

**Acceptance:** macro F1 is reported per label on the sample, and Try It returns multiple labels for a multi-topic text.

### G9 · Load data from a SQL query
A data source besides CSV upload:
- Connect to a database by SQLAlchemy URL. For the POC that is SQLite and DuckDB files on the server, and Postgres if a URL is given. Credentials are entered per session and never stored.
- Run a read-only SELECT with a row limit and preview, then save it as a dataset with the query stored in its lineage.
- A "refresh" re-runs the query into a new dataset version.
- Ship a small DuckDB/SQLite demo database built from the existing samples.

**Acceptance:** a query against the demo database creates a dataset that trains end to end, and refresh creates a new version with lineage.

### G10 · Vela Agent: an AI agent that does the work for you
A chat in the Vela sidecar, and as a full page, where the user asks in plain language and the agent does it through the platform's own API. Examples:
- "Train a default model on credit_default.csv with an out-of-time split, run the Model Doctor, apply its fix and register the best version."
- "Score this batch and tell me if anything drifted."
- "Why was applicant C-500123 declined, and what would change it?"

**Tools.** A typed tool registry wraps the existing endpoints, each with a short description and JSON schema:
- list/upload/profile datasets; Smart Intake suggestions (G0);
- start/watch runs; results, explain, doctor;
- apply a Gym patch, split-twin, learning curve, shift, slices;
- register, approve request, Try It, batch score;
- monitoring, counterfactuals (G1), credit report (G2), expected loss (G3);
- the other G-features once they exist.

Reuse the HTTP calls; don't add new business logic.

**Loop.** Plan → call a tool → read the result → continue, with a step budget and a time budget.
- With `ANTHROPIC_API_KEY`: the Anthropic SDK with native tool use (`claude-sonnet-5-5`).
- Without a key: the existing LLM router's `claude` CLI path, with JSON-action prompting (one tool call per turn, validated against the schema).
- With neither: the agent says so and offers the matching one-click UI flow.

**Safety.**
- Read-only tools run freely.
- Tools that change state (train, apply patch, register, score, delete) need the user's one-click confirmation in the chat. The confirm card shows the exact call.
- Approval and promotion to Production stay human (the four-eyes rule): the agent can only request them.
- Every agent action goes to the audit log as "Vela agent on behalf of <user>".

**UI.** One **Vela** page in the target navigation: the agent chat. Autopilot's brief, run view and Ground-Truth Inbox become its "run it end to end" mode instead of a separate page; keep every Autopilot capability.
- The conversation shows each step as a compact card: tool, arguments, result summary, and a link to the page.
- Tool calls stream live with a stop button.
- Final answers state numbers only from tool results, checked by the existing number guard.

**Acceptance.** Scripted prompts run end to end in smoke with the CLI path, or a stub model if needed:
1. Train → doctor → apply patch → compare, from one instruction, stopping for confirmation before training.
2. Explain one decline with reason codes and counterfactuals.
3. Score a batch and report drift.

The number guard passes on every answer. Report the steps and wall time per scenario.

### G11a · UI simplicity review (Opus, read-only)
Before G11, an Opus 5.5 session reviews the whole UI and the backend routes against the target navigation and the UI rules. It finds:
- duplicated or near-duplicate pages, steps, panels and components;
- dead routes, components, CSS and API client functions;
- screens with more than one primary action, or advanced options shown by default;
- inconsistent wording and patterns;
- anything still reachable outside the 5 items.

It reports a concrete, ordered list (files, what to merge or delete, lines saved, risk) to the orchestrator. G11 implements it.

### G11 · Integration and cohesion
- Implement G11a's simplicity list. Old page components that were merged are **deleted**, not hidden; only redirects remain. Report the UI line count before → after and the number of components removed.
- Finish the 5-item navigation (Home, Build, Models, Registry, Vela; Settings in the footer). Move Explain into the run page, remove the toolbelt, and redirect every old route. Update the demo script and `scripts/demo_walk.cjs` to the new paths.
- Walk every bundled sample and one fresh CSV from upload through Smart Intake: does each suggestion open the right flow, pre-filled? Fix the gaps.
- Make all the new features, including the Vela Agent, feel like one product:
  - navigation;
  - consistent entry points (New Model offers the new problem types: survival, multi-label, expected loss, segmented);
  - the model card;
  - the Model Doctor (it can recommend segmentation, reject inference or recourse checks where they apply);
  - Vela.
- Update `docs/demo-script.md` with a short credit-risk add-on: recourse, the credit report, EL stress and fair lending.
- Update the README feature matrix.
- Run one full `make smoke` and `npm run build` at the very end, and fix what's broken.
- Refresh the screenshots for the UI tour into `docs/progress/G10/`.
