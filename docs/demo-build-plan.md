# Project ENSEMBLE — end-to-end plan for a working demo

The goal: turn the mock UI into a product that really trains models, fast enough and visual enough to demo live. This plan replaces the heavier design in `backend-implementation-guide.md` for now. That guide stays as the reference for scaling up later.

**Constraints**: no Redis, no Celery, no sign-in. One Python process, one command to start. The UI can change wherever that makes the product better.

Nothing in this plan is built yet.

---

## 1. The product in one flow

The name is ENSEMBLE, so the demo should end with one. The headline flow:

1. **Give it a dataset.** Drop a CSV or pick a sample. It is profiled for real.
2. **Pick models.** Simple ones and ensembles, side by side. Select several.
3. **Watch them race.** Each model trains live as its own string; click one to see its own visual (classifiers settling, trees being added, residual damping).
4. **Read the leaderboard.** Real scores on identical folds, with fit time and prediction latency.
5. **Build an ensemble.** Pick two or more finished models, combine them by voting or stacking, and see whether the ensemble beats the best single model.
6. **Understand it.** Confusion matrix, weakest class, the words or features that drive each class.
7. **Register it and try it.** Promote the winner, then type a new example and get a live prediction with reasons.

Everything else in the app (Training Gym, Experiments, Autopilot, Vela) hangs off this flow.

---

## 2. The models

Eight to start: four simple, four kinds of ensemble. All from scikit-learn and XGBoost, called directly.

| Model | Kind | Why it's in | How it reports progress | Its visual |
| --- | --- | --- | --- | --- |
| Logistic Regression (OvR) | Simple | The strong, explainable baseline for text | Warm-started in short bursts, loss per class | One ringing string per classifier |
| Linear SVM | Simple | Fast max-margin alternative | SGD with hinge loss, one pass at a time | One string per classifier |
| Naive Bayes | Simple | Near-instant; shows what "simple" buys you | Single pass | Strings settle in one pluck |
| Decision Tree | Simple | The most readable model; the building block of the tree ensembles | Refit at depth 1, 2, 3 … with score per depth | The tree growing level by level |
| Random Forest | Ensemble · bagging | Many trees voting; reduces variance | Warm start, trees added in batches, score per batch | A chorus of strings converging as trees are added |
| XGBoost | Ensemble · boosting | Each tree fixes the last one's errors | Built-in per-round callback | The residual wave damping |
| Voting | Ensemble · combine | Average the predictions of models you already trained | Instant (uses saved predictions) | Chosen strings merging into one |
| Stacking | Ensemble · combine | A small model learns how much to trust each base model | A quick fit on saved predictions | Strings merging, with a weight on each |

The design decision that makes this work: **every model is trained on the same folds, and its out-of-fold predictions are saved.** That gives three things for free:

- the leaderboard is a fair comparison;
- Voting and Stacking need no retraining, so building an ensemble takes about a second on stage;
- stacking is leak-free, because the meta-model only sees predictions made on rows the base model did not train on.

Easy additions later, same pattern: k-Nearest Neighbours, Extra Trees, AdaBoost, scikit-learn's histogram gradient boosting. AutoGluon can come back later as an "auto" contestant in the race; it is a heavy install and not needed for the demo.

**Features, decided automatically from the profile**

- Text columns → TF-IDF (word 1–2-grams, capped vocabulary).
- Categorical columns → one-hot.
- Numeric columns → passed through, scaled for the linear models.
- Tree models train on the same TF-IDF features (no SVD), so their feature importances are actual words. This keeps every model explainable.

---

## 3. Architecture (deliberately small)

```
Browser (this repo's UI)
   │  REST + Server-Sent Events, through the Vite dev proxy at /api
   ▼
One FastAPI process
   ├── thread pool        runs training jobs (scikit-learn and XGBoost release the GIL while fitting)
   ├── in-memory events   one list per run; the SSE endpoint replays from any index, then follows live
   ├── SQLite file        datasets, runs, ensembles, questions
   ├── data folder        uploaded files, saved predictions, fitted models, per-run event logs
   └── MLflow (library)   local file store, no server: run records and the model registry
```

- **No queue.** `POST /runs` hands the job to a thread pool and returns the run ID.
- **No broker.** Events live in memory while a run is active and are also appended to a file, so a finished run can be replayed after a restart.
- **No sign-in.** Single user, local.
- **One command** starts the API and the UI together.
- If a run is in flight when the process stops, it is marked failed on the next start.

This is enough for one presenter and a handful of concurrent runs. Moving to workers and a broker later changes two small modules (jobs and events), not the training code or the UI.

---

## 4. What changes in the UI

| Page | Today (mock) | In the working product |
| --- | --- | --- |
| **Home** | Fake runs | Real active and recent runs, real best score, same visuals |
| **Quick Train** | One recipe, simulated | Becomes the model race: multi-select models → live race → leaderboard → build ensemble. The per-model stage visuals stay; two are new (tree growing, forest chorus) |
| **Training Gym** | Fixed logistic-regression sliders | Tune any one model: sliders generated from that model's parameter list; a real validation curve from a sweep; Start Training runs for real |
| **Experiments** | Fake table | Real runs and ensembles: leaderboard, string plot over real parameters, compare curves, per-run confusion matrix, new Explain tab |
| **Model Registry** | Fake versions | Real versions; Approve works; lineage from data to model; new "Try It" box for live predictions |
| **Vela Autopilot** | Scripted | Real, in two layers (see §6) |
| **Vela sidecar** | Fixed cards | Findings computed from the real run (weakest class, imbalance, overfitting gap), each with a patch that applies in the Gym |
| **Settings** | Works | Unchanged |

Two things stay as they are: the design system, and a **mock mode**. If the API isn't reachable the UI falls back to today's simulation, so the demo always opens.

One addition to the model step: each model card shows its equation and a one-line plain-English description, which brings back the useful part of the old Model Library without a separate page.

---

## 5. Data for the demo

Ship two generated sample datasets so the demo works offline and behaves predictably:

- **Support tickets** (text, six classes, about 20,000 rows): built from templates with realistic vocabulary overlap between classes, a rare class, some exact duplicates, and **a few hundred deliberately wrong labels**. The planted errors are what Autopilot's ground-truth inbox finds on stage.
- **Account churn** (tabular, two classes, about 10,000 rows): numeric and categorical columns with a known signal and class imbalance.

Sizes are chosen so a model trains in seconds to a minute. They are synthetic by design and labelled as samples in the UI. Uploading a real CSV goes through exactly the same path.

**Pacing for a live audience**: some fits finish in under a second. The UI plays each run's events over a minimum duration (a second or two) so every visual is seen, and a "3×" control still speeds up the long ones.

---

## 6. Autopilot and Vela, in two layers

The demo should never depend on a network call to a model. So:

- **Layer 1 — real, no language model.** Autopilot follows a fixed strategy on real training:
  1. Profile and clean.
  2. Fit a baseline.
  3. Rebalance.
  4. Race the simple models and the ensembles.
  5. Try a stack of the best ones.
  6. Check labels.
  7. Stop when the requirements are met or the budget is spent.
  
  Label checking is real: rows where the saved out-of-fold predictions confidently disagree with the label, and near-duplicate texts with different labels, go to the inbox. Your answer relabels the data and it retrains. Every number on screen is real.
- **Layer 2 — Claude on top.** Claude chooses the next step, where layer 1 follows a fixed order, and writes the findings in Vela's voice. It also generates synthetic examples for a starved class. It needs an API key. If the key is missing or a call fails, Autopilot drops back to layer 1 and says so.

The Vela sidecar works the same way: computed findings first, Claude wording optional.

---

## 7. API (small)

| Purpose | Endpoint |
| --- | --- |
| Health and mode check | `GET /api/health` |
| Datasets | `GET /api/datasets`, `POST /api/datasets` (upload), `GET /api/datasets/{id}` (profile) |
| Model catalogue | `GET /api/models` (name, kind, equation, parameters with ranges and defaults) |
| Start a race or a single run | `POST /api/runs` (dataset, list of models, optional parameters) |
| Live events | `GET /api/runs/{id}/events` (Server-Sent Events, replayable) |
| Results | `GET /api/runs`, `GET /api/runs/{id}` (scores, confusion, curves, explanation) |
| Ensembles | `POST /api/ensembles` (runs to combine, vote or stack) |
| Validation sweep | `POST /api/sweeps` (model, parameter, range) |
| Registry | `GET /api/registry`, `POST /api/registry` (register a run), `POST /api/registry/{v}/approve` |
| Predict | `POST /api/predict` (text or row → class, probabilities, top reasons) |
| Autopilot | `POST /api/autopilot`, `GET /api/autopilot/{id}/events`, `POST /api/questions/{id}/answer` |

---

## 8. Build order: ten steps

Each step ends with something you can show.

| # | Step | You can show |
| --- | --- | --- |
| 1 | **Skeleton.** FastAPI app, SQLite, one start command, Vite proxy, health check, mock-mode fallback in the UI | The UI reports "live" when the API is up and still works when it isn't |
| 2 | **Data.** Sample generators, CSV upload, real profiling, fixed holdout and folds | Quick Train's dataset step profiles real files from the server |
| 3 | **Engine and events.** The run loop, thread pool, event stream with replay; Logistic Regression first | The classifier strings move from a real fit; reload mid-run and it resumes |
| 4 | **The simple models.** Linear SVM, Naive Bayes, Decision Tree, with the tree-growing visual | Four simple models train live |
| 5 | **The tree ensembles.** Random Forest and XGBoost, with the forest and residual visuals | Bagging and boosting train live |
| 6 | **The race and leaderboard.** Multi-select, parallel runs, race view, real leaderboard with latency and fit time | Pick five models, watch them race, read the ranking |
| 7 | **Build an ensemble.** Voting and Stacking from saved predictions, the merge visual, ensemble vs best single | The headline moment: an ensemble assembled in a second that beats its parts (or honestly doesn't) |
| 8 | **Understand and keep.** MLflow records, Experiments on real data, Explain tab, Registry with Approve and "Try It" | Type a ticket, get a prediction and the words behind it |
| 9 | **Training Gym for real.** Sliders from each model's parameters, real validation sweep, Vela's computed findings and patches | Move a slider, retrain, see the score change |
| 10 | **Autopilot.** Layer 1 strategy with the real ground-truth inbox, then layer 2 with Claude and synthetic data | Autopilot finds the planted label errors, asks, fixes, and improves the score |

Steps 1–7 are the core demo. Steps 8–10 each stand alone, so the order among them can change.

---

## 9. What makes it demo-proof

- **Always opens**: mock mode if the API is down.
- **Offline**: sample data is generated locally; nothing is downloaded. Only Autopilot's layer 2 needs the network.
- **Predictable**: fixed seeds, so the same dataset and model give the same numbers every time.
- **Honest**: the ensemble doesn't always win, and the UI says so when it doesn't. Sample data is labelled as generated.
- **Recoverable**: every run can be replayed from its saved events, so a finished race can be shown again without retraining.
- **Reset**: one command clears runs and restores the samples.

---

## 10. Deliberately left out for now

- Sign-in, multiple users, projects.
- Redis, Celery, separate workers, containers.
- AutoGluon and neural models.
- Datasets too large for memory; regression and multi-label problems (classification only).
- Serving models outside the app.

---

## 11. Decisions to confirm

| Decision | Default in this plan |
| --- | --- |
| Model list | The eight in §2 |
| Quick Train becomes the model race | Yes |
| Sample data is generated, with planted label errors | Yes |
| MLflow stays, as a local library with no server | Yes |
| Autopilot in two layers, Claude optional | Yes |
| Problem type | Classification only |
