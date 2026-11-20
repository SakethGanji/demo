# Round 4 plan: strong, simple models and a Doctor for the data

**Status: approved by the user. The core ask: when a model can't do better, tell us as much as possible about *what is missing* from the data that limits it, and how to get it.**

## Goal

Upload any bank table, then:
1. **Data Doctor (before training):** what is wrong with this dataset, and how to fix it.
2. **Build:** the strongest model the data supports, then the **simplest model that is as good**, with proof.
3. **Model Doctor (after training):** where the model goes wrong, why, whether we are at the data's limit, and
   **what would improve it**, ranked by measured gain.
4. **Review (LLM, Claude Sonnet 5.5):**
   - Claude reads the data and the findings, then proposes hypotheses.
   - The platform proves or rejects each one with a fixed experiment on the same holdout.
   - It writes an improvement plan.
   - The numbers come from code; it works with the LLM off.

### Honest ceiling

A model cannot be more accurate than the information in the table allows. On Taiwan card default, AUC ≈ 0.78 is the
data's limit, and so is 0.80 on bank marketing without the leak. The product must say so, and must say what would move
the limit: more rows, new features, a better label, or a different decision.

## What we learn from others (read, not vendored)

- **H2O Driverless AI:** leakage by single-feature AUC, shift by train-vs-test classifier, both at strict thresholds
  (0.999). An LLM summarises results only.
- **DataRobot:**
  - Data Quality Assessment runs before training: outliers, inliers, excess zeros, disguised missing values,
    target leakage, plus extra checks for time series.
  - "Leakage Removed" feature list.
  - "Known in advance" is declared by the user.
  - Its docs say leakage needs domain knowledge.
- **AIDE ML:** a journal of attempts, and a propose → run → measure → keep loop with a greedy search. We keep the loop
  and drop free-form LLM code: it chases the score and is not auditable.
- **AutoGluon Assistant:** LLM roles (data perception, task description, error analysis, coder). We use the roles
  for our prompts. AutoGluon stays the engine.

## The design

### 1. Data Doctor (new; before training; seconds)

Each check gives a severity, the numbers, the rule on screen, and **a fix**: either a typed transform the user
applies with one click, or a question.

| Check | Example on real data | Fix |
|---|---|---|
| Leakage suspects: name + single-feature AUC + "known at decision time?" | bank `duration` | Question with suspects pre-ticked; unticked columns are dropped |
| Disguised missing (-1, 999, 0 bursts) and coded values | Taiwan PAY_* = -2 / -1, EDUCATION 0/5/6 | Map to missing / "unknown" |
| Informative missingness (missing rate differs by target) | — | Keep a "was missing" flag |
| Duplicates / near-duplicates, conflicting labels on identical rows | — | Drop, or send to label review |
| Label quality (cleanlab on a quick 60 s model) | — | Review queue (exists, F15) |
| Class balance and **rows per class** (too few to learn) | Banking77: 77 classes, 35 to 187 rows each | Merge rare classes, or collect more |
| Time coverage and drift across time (if a date exists) | — | Out-of-time split (exists) |
| IDs, constants, high-cardinality text masquerading as categories | Taiwan ID | Drop (exists) |
| Protected / proxy attributes | Taiwan SEX, AGE | Fairness only (exists) |
| Sample size vs features (rows per feature, events per variable) | German credit: 300 bads, 20 features | Warn; prefer simpler models |

The output is a **Data Report** card at the top of Build, with one primary action: "Apply the 3 fixes". Fixes produce
a new dataset version with lineage; the original is never changed.

### 2. Powerful, then simple (Build + Results)

One training request gives a **model ladder**, every rung on the same holdout with CIs:

| Rung | Model | Why |
|---|---|---|
| 0 | Baseline (majority / mean) | The floor |
| 1 | Scorecard (WoE logistic, exists F11) | Fully interpretable, the regulator's baseline |
| 2 | Single LightGBM / CatBoost (monotone where set) | Simple, fast, explainable with SHAP |
| 3 | AutoGluon best stack (good/best quality) | The ceiling of what the data supports |

- **Recommendation rule:** serve the **simplest rung whose holdout score is within the CI of the best** (paired
  bootstrap). Example: "The single CatBoost is 0.003 AUC below the stack (CI [-0.006, 0.001]): serve it. It is 40×
  faster and easier to explain."
- If the stack is clearly better, offer **distillation** of the stack into a single GBM (exists), and report what is
  kept.
- **Feature Lab** (optional, under Advanced; AIDE-style):
  - A search over a **whitelisted feature DSL**: ratios, differences, per-entity aggregates, date parts, bins,
    text TF-IDF.
  - Never free-form code.
  - Each candidate is scored on the training folds only, kept only if it improves the CI, and checked by the leakage and
    decision-time rules.
  - The result is a reviewable transform list attached to the dataset version.

### 3. Model Doctor (after training)

| Finding | Rule (all on screen) | Prescription |
|---|---|---|
| **Leakage** | Dominant feature (≥ 50% of permutation importance and ≥ 3× the next) + intake hint, or single-feature AUC ≥ 0.97 | **Ablation**: retrain without it; the card shows score with/without and the Δ CI |
| **Cost trade-off** (replaces weak class / imbalance for cost runs) | Run optimises a business cost | Shows decline rate, catch rate, cost/1,000 at 3:1, 5:1, 10:1; "confirm the ratio" |
| **Where it went wrong** (new Errors sub-tab) | Holdout errors ranked by confidence | Each error shows SHAP reasons. Errors are also split by slice, overlap with cleanlab label issues and by time. |
| **At the data's limit** (ceiling) | Flat learning curve, top models agree, gap within noise, errors not concentrated | "Tuning will not help. More rows, new features or a cleaner label would." |
| Overfit, shift, label noise, data-limited, budget, latency, weak slice | Existing rules, re-ranked | Existing patches |

**"How to improve" panel:** each lever has a **measured expected gain**, not a guess:
- **More data:** learning-curve extrapolation (exists F14). Example: "doubling rows → +0.012 AUC [0.006, 0.019]".
- **Cleaner labels:** retrain on cleanlab-cleaned training rows; measured Δ on the untouched holdout.
- **Fix a feature or drop a leak:** ablation Δ.
- **New features:** Feature Lab Δ. It also names where features are thin: "errors concentrate in self-employed
  applicants where income is missing 41%".
- **More budget or a better preset:** Δ from a budget-doubling run when the budget finding fires.

Headline precedence is re-tuned so the **primary finding is the most actionable one**.

### 3b. What's missing: the information gap (the user's core ask; part of D3, enriched in D4)

When the model stops improving, say **why the data cannot separate the cases**, and **what information would**.

| Signal | How it is measured | What it tells the user |
|---|---|---|
| **Indistinguishable twins** | Holdout rows whose nearest neighbours on all features (scaled, same categories) have the other label. The share of the error explained by them gives an estimate of the irreducible error with a CI. | "38% of errors are applicants who look identical on every column but had different outcomes: the table lacks the information that separates them." |
| **Where errors concentrate** | Error rate and lift by slice. Missingness of each feature inside the error rows vs overall. | "Errors concentrate in self-employed applicants, where income is missing 41% vs 6% elsewhere." |
| **Thin or coarse features** | Low-cardinality or binned features with high importance and high within-bin outcome variance. Features that are only a snapshot (no history). | "EDUCATION has 4 values and carries 12% of the signal; a finer measure or a history (6-month utilisation trend) would help." |
| **Label quality** | cleanlab issues inside the error rows; ambiguous label definitions (classes the model confuses symmetrically). | "Classes A and B are confused both ways 31 times: the label definition overlaps." |
| **Value of more rows** | Learning-curve extrapolation (exists). | "Doubling rows: +0.012 AUC [0.006, 0.019]." |
| **Value of a perfect feature (simulated)** | Oracle test: add a noisy copy of the label inside one segment only, then measure how much the holdout would move. This bounds the gain from better information there. Labelled clearly as a simulation. | "Perfect information on self-employed income would be worth up to +0.04 AUC." |
| **Domain gaps (LLM, D4)** | Claude compares the columns with what is typically predictive for this task (credit bureau, behaviour, transaction history, tenure, …) and lists candidate missing sources. Labelled "suggestions to investigate", never mixed with measured numbers. | "Typical strong signals for card default not present: bureau inquiries, payment-to-balance ratio trend, external delinquencies." |

The Doctor's "What's missing" card ranks these gaps by measured potential gain. Each gap names a concrete data
request the bank could act on ("collect", "join", "redefine the label", "fix the source").

### 4. Review (LLM layer; AIDE loop, AutoGluon-Assistant roles)

- **Roles** (one prompt each, Claude Sonnet 5.5 via the Claude CLI, through the existing F5 router):
  1. perceive the data (columns, samples, data dictionary if uploaded);
  2. describe the task and the decision point;
  3. analyse errors (Data and Model Doctor findings, Errors tab);
  4. propose hypotheses.
- **Hypotheses map to typed experiments only.** For example: ablate columns, apply a Data Doctor fix, add a Feature
  Lab feature, double the budget, raise the preset, change the cost ratio, segment, monotone constraint, clean labels.
- **Journal** (from AIDE): every hypothesis → experiment → result (Δ with CI) → kept/rejected. Greedy on the best
  node, capped by budget (for example 5 experiments or 30 minutes), stops when nothing improves beyond noise.
- **Output:** an **improvement plan**: what was tried, what helped (with numbers), and what the bank should do next
  (collect X, define the label as Y, drop Z). The number guard blocks any figure not in the journal. Every review is
  saved to the audit log with its inputs.
- **Offline:** the deterministic Doctor proposes the same kinds of experiments from its rules; only the wording is
  simpler.
- Vela's agent gets a "review this run" tool. In the UI: a Review panel in the Doctor tab with one primary action,
  "Run review".

### 5. Proof: real data with known answers

`scripts/realdata.py` (`make realdata`):
- **6+ public datasets** with published scores: Taiwan, bank marketing, German credit, Banking77, plus 2+ from
  OpenML / Hugging Face.
- **Planted issues on real data:** copies of each dataset with known problems injected. For example: a leak column,
  10% flipped labels, drift in the last 30%, disguised missing -999, a duplicated block, missing-not-at-random. Real
  distributions, known answers.
- **Assertions:**
  - the Data Doctor finds each planted issue;
  - the Model Doctor headline is the expected one;
  - ablation and cleaning Δs have the right sign;
  - scores land in the published ranges;
  - the ladder recommends a simpler rung when it is within the CI;
  - the review's kept experiments are real improvements.
- The report goes to `docs/progress/R4-realdata.md` with screenshots.

## Sessions (sequential, the orchestrator coordinates; speed mode: smoke sections + build + screenshots)

| # | Session | Builds |
|---|---|---|
| D1 | Data Doctor | Section 1: checks, fixes as typed transforms, dataset versions, the decision-time question, Build's Data Report card. Smoke section `datadoctor`. |
| D2 | Model ladder + Feature Lab | Section 2: ladder in one run, simplest-within-CI recommendation, distillation hook, Feature Lab DSL + search. Smoke `ladder`, `featurelab`. |
| D3 | Model Doctor 2 | Section 3: leakage dominance + ablation, cost trade-off, Errors tab, ceiling, how-to-improve with measured Δs, re-ranked precedence. Smoke `doctor2`. |
| D4 | Review loop | Section 4: roles, typed experiments, journal, improvement plan, audit, offline path, Vela tool, UI panel. Smoke `review`. |
| D5 | Real-data proof | Section 5: `make realdata`, planted-issue copies (including hidden-feature tests for 3b: drop a known strong column and check that 'What's missing' points at it), fixes for what it finds, screenshots, demo script update. |
| A1…An | Adversarial validation | An independent Opus agent (read-only, fresh eyes) attacks the finished system: new public datasets, nasty CSVs, planted issues it chooses, UI walk. It reports what is wrong, misleading or missing. A fix session addresses the findings, and this repeats (up to ~3 rounds) until a round finds nothing important. |

Rules for every D-session:
- Never edit `backend/app/samples*.py`.
- Small commits, pushed only to `motion-components`; no PRs.
- Keep the UI concise: one primary action per screen, no new nav items, reuse the shared atoms.
- Every number on screen is computed. Every rule is written on screen.
- Report to the orchestrator.

## Real datasets (download in-session; cache under backend/data/realdata/, not committed)

The UCI host has an expired TLS certificate; use these mirrors:
- Taiwan default: https://huggingface.co/datasets/scikit-learn/credit-card-clients/resolve/main/UCI_Credit_Card.csv
- Bank marketing (bank-additional-full, `;`-separated): https://raw.githubusercontent.com/selva86/datasets/master/bank-full.csv
- German credit: https://api.openml.org/data/v1/download/31 (ARFF; cost 5:1)
- Banking77: https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/master/banking_data/train.csv

## Expected headlines (round 3 got three of four wrong)

| Dataset | Data Doctor | Model Doctor |
|---|---|---|
| Bank marketing | Leakage suspect `duration` (decision-time question) | Leakage, ablation ≈ 0.95 → ≈ 0.80 |
| Taiwan | Coded values in PAY_* and EDUCATION; SEX/AGE protected | At the data's limit (≈ 0.78); simplest rung within CI; What's missing: twins share, thin features (no bureau or history data) |
| German credit (5:1) | Small sample: 300 bads, prefer simpler rungs | Cost trade-off; scorecard likely within CI of the stack |
| Banking77 | Many classes, few rows in the rarest | Time budget at 600 s; text model carries the ensemble |
