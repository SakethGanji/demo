# Round 4: a Doctor that works on real data

Why: four public bank datasets (German credit, Taiwan card default, UCI bank marketing, Banking77) trained to published
scores, but the Doctor's headline was weak or misleading on three of them:
- bank marketing: `duration` (call length, known only after the call) dominated the model (AUC 0.956, ≈ 0.80 without it).
  Smart Intake warned about it, but the Doctor's headline was "imbalanced label, minor".
- German credit and Taiwan, trained on a business cost: the Doctor called the cost-optimal trade-off a "weak class"
  and prescribed reweighting, which would raise the cost.
- bank marketing: a generic imbalance note.

The goal: upload any table → a strong AutoGluon model → a Doctor whose headline a credit analyst would act on → an LLM
review that proposes hypotheses and **proves them with experiments**, not opinions.

## What we learn from AIDE ML and AutoGluon Assistant

Both are cloned for reference (read only, nothing vendored): `git clone --depth 1 https://github.com/WecoAI/aideml` and
`git clone --depth 1 https://github.com/autogluon/autogluon-assistant`.
- **AIDE** (aide/agent.py, journal.py): a journal of nodes. Each node is an attempt with its metric. A search policy
  drafts, debugs or improves (greedy on the best node). Keep the pattern: **journal + propose → run → measure → keep**.
  Do not keep free-form LLM-written training code: it maximises the score (it would use `duration`), and it is not
  auditable or reproducible.
- **AutoGluon Assistant** (agents/): separate roles. data_perception (read the files), task_descriptor (what is
  the task), error_analyzer (why did it fail), coder and executer. Keep the role split for prompts: perceive
  data → describe task → analyse errors → propose. Leave out its LLM-written AutoGluon code.
- **Our rule:** the LLM proposes. The platform runs a fixed, typed experiment on the same holdout. The evidence decides.
  Numbers and rankings come from code, never from the LLM. Everything also works with the LLM off.

## Honest ceiling

No tool reaches "99%" on a table whose information does not support it. On Taiwan default, AUC ≈ 0.78 is the data's
limit. The Doctor must **say when a model is at the data's limit**: flat learning curve, top models agree, error rows
look like label noise. It must also say what would move it: more rows, new features, a better label, a different
decision.

## Sessions (sequential; one session each; orchestrator coordinates)

| # | Session | Builds | Proven by |
|---|---|---|---|
| D1 | Doctor core (deterministic) | Leakage that real data needs, decision-time step, cost-aware checks, "where it went wrong", ceiling. Detail below. | smoke `doctor` + new `doctor2` section; the four real datasets give the expected headlines |
| D2 | LLM review loop | The AIDE-style journal of hypotheses → typed experiments → evidence. The role prompts, the Claude CLI (Sonnet 5.5) through the F5 router, audit, offline fallback. A "Review" panel in the Doctor tab and a Vela tool. | smoke `review` section on crafted + real data; offline path |
| D3 | Real-data benchmark + polish | `scripts/realdata.py`: 6+ public datasets (cached download, no UCI host: its TLS cert is expired), upload → intake → train → doctor → review. It asserts the expected headlines and published score ranges. Fix what it finds, take screenshots, update the demo script. | `make realdata` report in docs/progress/D3.md |

### D1 detail
1. **Leakage, as real data needs it:**
   - The existing single-feature AUC check stays.
   - Add **dominance**: one feature carries a large share of permutation importance (for example ≥ 50% and ≥ 3× the next).
   - Add **intake hint**: Smart Intake flagged the column as a possible leak, or the name reads post-outcome.
   - Dominance + hint → leakage is the primary finding (major), with a **one-click ablation**: retrain without the column,
     same holdout and config. The result card shows the score with and without it.
   - Ablation is a typed experiment the Doctor (and D2) can start.
2. **"Known at decision time?" step in Build:**
   - One question per dataset, with Smart Intake's suspects pre-ticked (DataRobot's "known in advance", for tables).
   - Stored with the dataset. Unticked columns are dropped from runs by default, and the run records it.
3. **Cost-aware class checks:**
   - When a run optimises a business cost, weak_class / imbalance no longer fire for the cheap error.
   - They become a **trade-off** finding: decline rate, catch rate, and cost per 1,000 at the served threshold and at
     3:1 / 5:1 / 10:1. Prescription: confirm the ratio, not reweight.
4. **Where it went wrong:** a Doctor sub-tab "Errors".
   - The holdout's confident errors, ranked, each with its SHAP reasons.
   - Errors by slice (reuse weak slices).
   - Overlap with cleanlab label issues ("these 40 errors look mislabelled").
   - Errors by time if a date exists.
5. **Ceiling:** an "at the data's limit" finding when all of these hold:
   - the learning curve is flat (reuse F14);
   - the top-k models agree;
   - the val/holdout gap is within noise;
   - the error rows are not concentrated.
   It says what would move the score. It ranks below real problems.
6. **Headline discipline:** re-tune precedence so the primary finding is the most actionable one. Keep every rule
   written on screen (as today).

Rules as before: speed mode (smoke sections + `npm run build` + screenshots); never edit `backend/app/samples*.py` (add new
generator files if needed); small commits, push only to `motion-components`, no PRs; progress report in
`docs/progress/D<n>.md`; report to the orchestrator session.

Real datasets (download in-session, cache under backend/data/realdata/, not committed):
- Taiwan default: https://huggingface.co/datasets/scikit-learn/credit-card-clients/resolve/main/UCI_Credit_Card.csv
- Bank marketing (bank-additional-full, `;`-separated): https://raw.githubusercontent.com/selva86/datasets/master/bank-full.csv
- German credit: https://api.openml.org/data/v1/download/31 (ARFF; cost 5:1)
- Banking77: https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/master/banking_data/train.csv
- D3 adds 2+ more from OpenML or Hugging Face, with published baselines.

Expected Doctor headlines:
- bank marketing → leakage on `duration`, with ablation showing ≈ 0.95 → ≈ 0.80;
- German/Taiwan with cost → trade-off, not weak class;
- Banking77 at 600 s → time budget;
- Taiwan → at the data's limit, AUC ≈ 0.78.
