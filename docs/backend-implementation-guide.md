# Project ENSEMBLE — backend implementation guide

This is the build guide for the backend behind the ENSEMBLE UI in this repo. The UI is finished as a mock: every page works, and every number is simulated in the browser. This guide describes how to replace the simulation with a real service, page by page, without changing how the UI looks or behaves.

**Stack decided so far**

| Concern | Choice |
| --- | --- |
| Training | scikit-learn and XGBoost, called directly |
| AutoML challenger | AutoGluon, used only inside Autopilot |
| Run and model records | MLflow (tracking + model registry) |
| Agent | Claude (`claude-opus-5-5`) with a fixed set of tools |
| Service | Python 3.11+, FastAPI |
| Jobs and live events | Redis (RQ for jobs, Streams for events) |
| App data | Postgres |
| Files | Local disk in development, S3-compatible object storage later |

Nothing here is built yet. Library calls marked **verify** should be checked against the version you install; the rest is standard usage.

---

## 1. Principles

1. **The UI talks to one service.** It never calls MLflow, Redis or Claude directly.
2. **Training never runs in the API process.** A request enqueues a job and returns a run ID; a worker does the work.
3. **Every run is a stream of small events.** The string visuals are driven by those events, live and on replay.
4. **Rules live in code, not in prompts.** Holdout access, budgets and synthetic-data isolation are enforced by the functions the agent calls.
5. **Datasets are immutable versions.** Cleaning, relabelling and synthesis each write a new version; nothing is edited in place.
6. **One interface over all engines.** scikit-learn, XGBoost and AutoGluon sit behind the same `start run → events → result` contract.

---

## 2. Architecture

```
Browser (this repo's UI)
   │  REST + Server-Sent Events
   ▼
API service (FastAPI) ──────────────► Postgres   datasets, versions, runs, autopilot, questions
   │        ▲                    └──► MLflow     run records, model versions (its own Postgres + files)
   │ enqueue│ read events
   ▼        │
Redis  ◄────┴──── Training worker(s)  scikit-learn · XGBoost · AutoGluon
  RQ queue          │
  Streams           └── Autopilot worker ──► Claude (tool use)
```

- **API service**: validation, persistence, enqueueing, relaying events. No heavy work.
- **Training worker**: one process per job (RQ forks a work-horse per job, so a crash or memory blow-up cannot take the API down).
- **Autopilot worker**: runs one agent step at a time, calling the same training code through tools.
- **Redis Streams**: each run has a stream `run:{id}:events`. The worker appends; the API reads from any offset. That gives live streaming and replay with one mechanism.

---

## 3. Repository layout

Put the backend in this repo next to the UI, mirroring the UI's feature folders.

```
backend/
├── pyproject.toml
├── docker-compose.yml           api, worker, redis, postgres, mlflow
├── app/
│   ├── main.py                  FastAPI app, routers, CORS
│   ├── config.py                settings from environment
│   ├── db.py                    SQLAlchemy session, models
│   ├── events.py                publish / read run events (Redis Streams)
│   ├── jobs.py                  enqueue helpers (RQ)
│   ├── storage.py               dataset files (local or S3)
│   ├── tracking.py              the only module that imports mlflow
│   ├── features/                one folder per UI page
│   │   ├── datasets/            upload, profile, versions            → Quick Train step 1
│   │   ├── runs/                start, stream, result                → Quick Train, Training Gym
│   │   ├── experiments/         list, compare, confusion, leaderboard → Experiments
│   │   ├── registry/            versions, promote, lineage           → Model Registry
│   │   └── autopilot/           brief, plan, loop, inbox             → Vela Autopilot
│   └── training/                the engine (no web code in here)
│       ├── recipes.py           recipe registry and builders
│       ├── split.py             holdout and folds
│       ├── loop.py              the run loop
│       ├── ovr.py               one-vs-rest with progress
│       ├── boosting.py          XGBoost with per-round callback
│       ├── automl.py            AutoGluon wrapper
│       ├── explain.py           top terms, feature importance
│       └── label_issues.py      suspect labels, conflicting duplicates
└── tests/
```

Rule, same as the UI: a feature folder owns what only it uses; `training/` is shared and imports nothing from `features/`.

---

## 4. Data model (Postgres)

| Table | Key columns | Notes |
| --- | --- | --- |
| `datasets` | id, name, created_at | The logical dataset |
| `dataset_versions` | id, dataset_id, version, parent_id, uri, rows, profile (json), change (json) | Immutable. `change` records what produced it (dedupe, relabel, synthesize) |
| `splits` | dataset_id, seed, holdout_ids_uri | Written once, at first use. Holdout row IDs never change |
| `row_flags` | version_id, row_id, synthetic (bool), relabelled_from | Lets training exclude synthetic rows from validation |
| `runs` | id, version_id, recipe, params (json), status, mlflow_run_id, autopilot_id, started_at, finished_at | One per training run |
| `autopilot_runs` | id, dataset_id, brief (json), status, agent_state (json), budget_spent | See §8 |
| `autopilot_steps` | id, autopilot_id, seq, tool, input (json), output (json), kept (bool) | This is the Decision Log |
| `questions` | id, autopilot_id, kind, finding, evidence (json), options (json), answer, answered_by, answered_at | This is the Ground-Truth Inbox |

Stable row IDs matter: give every row a `row_id` at upload and carry it through every version. Holdout membership, relabels and synthetic flags all hang off it.

---

## 5. Run events

Every event is one small JSON object appended to the run's stream. The UI already has a component waiting for each.

| Event | Payload | Drives |
| --- | --- | --- |
| `phase` | `name` (load, split, vectorize, fit, evaluate), `note` | Pipeline string |
| `fold_start` | `fold`, `of` | Fold strings |
| `class_progress` | `fold`, `class`, `loss`, `converged` | One ringing string per classifier |
| `boost_round` | `fold`, `round`, `of`, `loss` | Residual wave, round frets |
| `loss` | `step`, `train`, `validation` | Loss plot |
| `fold_done` | `fold`, `f1`, `per_class` | Fold strings, headline F1 |
| `log` | `level`, `text` | Run log |
| `done` | `metrics`, `confusion`, `fit_seconds` | Results |
| `error` | `message` | Failed state |

```python
# app/events.py
import json, redis
r = redis.Redis.from_url(settings.redis_url)

def publish(run_id: str, type: str, **data):
    r.xadd(f"run:{run_id}:events", {"e": json.dumps({"type": type, **data})}, maxlen=20_000)

def read(run_id: str, last_id: str = "0", block_ms: int = 15_000):
    """Yields (id, event). Start at "0" to replay, or at the last ID the browser saw."""
    while True:
        for _, entries in r.xread({f"run:{run_id}:events": last_id}, block=block_ms) or []:
            for entry_id, fields in entries:
                last_id = entry_id.decode()
                yield last_id, json.loads(fields[b"e"])
```

The API exposes it as Server-Sent Events. The browser's `EventSource` reconnects on its own and sends `Last-Event-ID`, so a dropped connection or page reload resumes without losing events.

```python
# app/features/runs/router.py
@router.get("/runs/{run_id}/events")
def stream(run_id: str, request: Request):
    last = request.headers.get("last-event-id", "0")
    def gen():
        for event_id, event in events.read(run_id, last):
            yield f"id: {event_id}\ndata: {json.dumps(event)}\n\n"
            if event["type"] in ("done", "error"):
                return
    return StreamingResponse(gen(), media_type="text/event-stream")
```

Throttle `class_progress` and `boost_round` to roughly ten per second per run; the visuals don't need more and the stream stays small.

---

## 6. Training engine

### 6.1 Recipes

One registry, matching the cards on the Quick Train page. The Gym's hyperparameters are just `params` passed to the same builders.

```python
# app/training/recipes.py
RECIPES = {
    "ovr":  Recipe(name="TF-IDF → LogReg (OvR)",   kinds={"text", "tabular"}, viz="strings", fit=ovr.fit_fold),
    "svm":  Recipe(name="TF-IDF → LinearSVC",      kinds={"text"},            viz="strings", fit=ovr.fit_fold),
    "xgb":  Recipe(name="TF-IDF → SVD → XGBoost",  kinds={"text", "tabular"}, viz="boost",   fit=boosting.fit_fold),
}
```

Features are built with a scikit-learn `ColumnTransformer`: `TfidfVectorizer` on text columns, `OneHotEncoder(handle_unknown="ignore")` on categoricals, pass-through or scaling on numerics. For XGBoost on text, add `TruncatedSVD` after TF-IDF. The profile from §7 decides which columns get which treatment.

### 6.2 Split

- **Holdout**: 15%, stratified, chosen once per dataset and stored as row IDs. Later versions inherit it by row ID.
- **Folds**: `StratifiedKFold(k=5, shuffle=True, random_state=seed)` on the remaining rows.
- **Synthetic rows** are added to training folds only. The fold builder filters them out of every validation fold and they can never be in holdout because they have no original row ID.

`split.py` is the only module that can load holdout rows, and it exposes them through one function, `evaluate_on_holdout(run)`, which records that it was called.

### 6.3 The run loop

```python
# app/training/loop.py
def run(run_id, version, recipe_id, params, emit):
    recipe = RECIPES[recipe_id]
    emit("phase", name="load");      df = storage.load(version)
    emit("phase", name="split");     folds = split.folds(version, seed=params.seed)
    fold_scores = []
    for i, (train_idx, val_idx) in enumerate(folds, start=1):
        emit("fold_start", fold=i, of=len(folds))
        emit("phase", name="vectorize")
        X_train, X_val, y_train, y_val, features = build_features(df, train_idx, val_idx, params)
        emit("phase", name="fit")
        model = recipe.fit(X_train, y_train, X_val, y_val, params, fold=i, emit=emit)
        scores = score(model, X_val, y_val)
        emit("fold_done", fold=i, f1=scores.macro_f1, per_class=scores.per_class)
        fold_scores.append(scores)
    emit("phase", name="evaluate")
    final = refit_on_all_training(df, recipe, params)        # one model on all non-holdout rows
    holdout = split.evaluate_on_holdout(final, version)       # the only holdout access
    tracking.log_run(run_id, recipe_id, params, version, fold_scores, holdout, final)
    emit("done", metrics=holdout.metrics, confusion=holdout.confusion, fit_seconds=...)
```

### 6.4 One-vs-rest with visible convergence

scikit-learn's `LogisticRegression` does not report progress while fitting, and its built-in one-vs-rest hides the individual classifiers. To drive one string per class, fit the binary classifiers yourself in short warm-started bursts and report the loss after each burst.

```python
# app/training/ovr.py
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss

def fit_fold(X, y, X_val, y_val, params, fold, emit, burst=20):
    classes = np.unique(y)
    models = {c: LogisticRegression(C=params.C, class_weight=params.class_weight,
                                    warm_start=True, max_iter=burst, tol=params.tol) for c in classes}
    done = set()
    for _ in range(params.max_iter // burst):
        for c in classes:
            if c in done:
                continue
            target = (y == c).astype(int)
            models[c].fit(X, target)                       # continues from the previous coefficients
            loss = log_loss((y_val == c).astype(int), models[c].predict_proba(X_val)[:, 1])
            converged = models[c].n_iter_[0] < burst       # stopped early: it has converged
            emit("class_progress", fold=fold, **{"class": str(c)}, loss=loss, converged=converged)
            if converged:
                done.add(c)
        if len(done) == len(classes):
            break
    return OneVsRestModel(models)                          # predict = argmax of the per-class scores
```

Notes:

- Each burst raises a `ConvergenceWarning` by design; filter it.
- The K classifiers are independent, so fit them in parallel with `joblib` once the sequential version works.
- **LinearSVC** has no warm start and no loss curve. Use `SGDClassifier(loss="hinge")` with `partial_fit` per epoch to get the same per-class progress, or accept "fitting → done" per class.
- **Verify** `warm_start` behaviour with the solver you choose; `lbfgs` and `saga` support it.

### 6.5 XGBoost with per-round events

```python
# app/training/boosting.py
import xgboost as xgb

class Progress(xgb.callback.TrainingCallback):
    def __init__(self, emit, fold, total):
        self.emit, self.fold, self.total = emit, fold, total
    def after_iteration(self, model, epoch, evals_log):
        loss = evals_log["validation"]["mlogloss"][-1]
        self.emit("boost_round", fold=self.fold, round=epoch + 1, of=self.total, loss=loss)
        return False                                        # True would stop training

def fit_fold(X, y, X_val, y_val, params, fold, emit):
    booster = xgb.train(
        {"objective": "multi:softprob", "num_class": params.n_classes, "eta": params.eta, "max_depth": params.max_depth},
        xgb.DMatrix(X, label=y), num_boost_round=params.M,
        evals=[(xgb.DMatrix(X_val, label=y_val), "validation")],
        callbacks=[Progress(emit, fold, params.M)], verbose_eval=False,
    )
    return booster
```

Use `binary:logistic` and `logloss` when there are two classes.

### 6.6 Measured, not estimated

- **Latency**: time `predict` on single rows (median of a few hundred) and report milliseconds. The Autopilot latency requirement is checked against this number.
- **Fit time**: wall clock per fold and total.
- **Dry run** (the Gym's console): the same loop on a 2,000-row sample with one fold.
- **Validation curve** (the Gym): a real sweep of `C` on a subsample, cached per dataset version and recipe.

---

## 7. Datasets

1. **Upload**: `POST /datasets` accepts CSV or Parquet, stores the file, assigns `row_id`, writes version 1.
2. **Profile** (pandas / pyarrow), stored on the version:
   - per column: inferred type (text, category, number, id), null rate, distinct count, an example;
   - suggested target and its class balance;
   - exact duplicates, and columns that look like leakage (near-perfect predictors, IDs correlated with the target).
3. **Versions**: every change writes a new Parquet file and a `change` record. The Data Changes panel is a sum over those records.

Size limit for the first version: what fits in one worker's memory (a few GB). Beyond that, switch `storage.load` to chunked or out-of-core reading; nothing else needs to change.

---

## 8. MLflow

Keep every MLflow call in `app/tracking.py` so the rest of the code doesn't depend on it.

```python
# app/tracking.py
import mlflow
from mlflow import MlflowClient

mlflow.set_tracking_uri(settings.mlflow_uri)

def log_run(run_id, recipe_id, params, version, fold_scores, holdout, model):
    mlflow.set_experiment(version.dataset.name)
    with mlflow.start_run(run_name=run_id) as active:
        mlflow.log_params({"recipe": recipe_id, **params.as_dict()})
        mlflow.set_tags({"dataset_version": version.version, "ensemble_run_id": run_id})
        for i, s in enumerate(fold_scores, start=1):
            mlflow.log_metric("fold_macro_f1", s.macro_f1, step=i)
        mlflow.log_metrics(holdout.metrics)                     # macro_f1, auc, latency_ms, fit_seconds, per-class recall
        mlflow.log_dict(holdout.confusion, "confusion.json")
        mlflow.sklearn.log_model(model, "model")                # mlflow.xgboost.log_model for boosters
        return active.info.run_id
```

- **Experiments page**: `MlflowClient().search_runs(...)` for the table and string plot; `get_metric_history` for curves; the confusion artifact for the matrix.
- **Registry page**: `mlflow.register_model(f"runs:/{id}/model", name)` creates a version. Promotion uses model **aliases** (`set_registered_model_alias(name, "production", version)`); map the UI's Staging / Production / Archived onto aliases and tags. **Verify**: older MLflow versions use stages instead of aliases.
- **Lineage**: run → dataset version (tag) → parent versions (your `dataset_versions.parent_id`). MLflow gives the run-to-model half; your database gives the data half.
- **Autologging** (`mlflow.sklearn.autolog()`, `mlflow.xgboost.autolog()`) is an option, but explicit logging as above keeps names stable for the UI.
- Development: `mlflow server` backed by SQLite and a local folder. Shared use: Postgres backend store and object storage for artifacts.

---

## 9. Autopilot

### 9.1 What the agent can do

The agent acts only through these tools. Each tool is an ordinary function in `features/autopilot/tools.py` that calls the training engine.

| Tool | Does | Returns to the agent |
| --- | --- | --- |
| `profile_dataset` | §7 profile | Summary statistics, never raw rows |
| `clean` | Dedupe, impute; writes a version | Counts and the new version number |
| `train` | Runs a recipe (§6) and waits | Fold scores, per-class recall, latency, fit time |
| `run_autogluon` | Time-boxed AutoGluon fit | Its leaderboard, best score, latency |
| `find_label_issues` | Suspect labels and conflicting near-duplicates | Counts and a few examples |
| `synthesize` | Writes examples for one class into a new version | Count kept, count filtered out |
| `ask_human` | Creates an inbox question; the run pauses | The human's answer, when it arrives |
| `finish` | Scores once on holdout, writes the report | Final metrics |

### 9.2 Rules enforced in code

| Rule | Where it lives |
| --- | --- |
| Iteration budget | `train`, `run_autogluon` and `synthesize` each spend one; when the budget is gone they return "budget exhausted, call finish" |
| Holdout is used once | Only `finish` calls `evaluate_on_holdout`; a second call fails |
| Synthetic rows never validate | The fold builder (§6.2), not the agent |
| Latency requirement | Measured in `train` (§6.6) and reported; `finish` states pass or fail |
| Autonomy level | `ask_human` checks the brief: **Ground Truth Only** waits for ground-truth questions and auto-answers the rest with the agent's suggestion; **Ask First** waits for all; **Fully Autonomous** auto-answers all and logs it |
| Label changes | Only applied from an answered question, never directly by the agent |

### 9.3 Where the agent loop runs: two options

**Option A — Claude Managed Agents (recommended starting point).** Anthropic runs the agent loop and keeps the session; your backend supplies the tools. You define the agent once with `client.beta.agents.create(...)` listing each tool above as a `custom` tool, then start one session per Autopilot run. When the agent calls a tool, the session emits an `agent.custom_tool_use` event and goes idle; your worker runs the tool and replies with a `user.custom_tool_result` event. A question for a human is just a tool result that arrives hours later — the session waits, and you hold no agent state.

What to know before choosing it:

- Anthropic hosts the loop and the session; your training and data stay on your side, but whatever your tools return (including the example rows in a ground-truth question) is stored in the session history until you delete it.
- It is in beta, and bills session runtime on top of tokens.
- It is not available under Zero Data Retention or a HIPAA BAA, nor on Bedrock, Vertex AI or Foundry.

**Option B — host the loop yourself on the Messages API.** Choose this if any of the points above rules Option A out. You own the loop and persist the conversation.

```python
# app/features/autopilot/loop.py  (Option B)
import anthropic
client = anthropic.Anthropic()

def step(run):                                   # one RQ job = run until blocked or finished
    messages = run.agent_state["messages"]       # stored as JSON in Postgres
    while True:
        response = client.beta.messages.create(
            model="claude-opus-5-5",
            max_tokens=16000,
            output_config={"effort": "high"},    # set explicitly: this model's default is medium
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",                 # a declined request is retried on a fallback model
            cache_control={"type": "ephemeral"}, # the system prompt and tool list are identical every turn
            system=SYSTEM_PROMPT,                # stable text only: no timestamps, no run IDs
            tools=TOOLS,                         # fixed order; each with strict: True
            messages=messages,
        )
        messages.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})
        if response.stop_reason == "refusal":
            return fail(run, response.stop_details)
        if response.stop_reason != "tool_use":
            return save(run, messages, status="finished")

        results, waiting = [], None
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name == "ask_human" and (q := tools.ask_human(run, block.input)).needs_human:
                waiting = (block.id, q.id)       # leave this one unanswered for now
                continue
            out = tools.call(run, block.name, block.input)         # records an autopilot_steps row
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(out.data), "is_error": out.failed})
        if waiting:
            return save(run, messages, status="waiting", pending=results, waiting=waiting)
        messages.append({"role": "user", "content": results})      # all results in ONE message
        save(run, messages, status="running")
```

When a question is answered, the API appends the saved `pending` results plus the answer as a single `user` message and enqueues `step` again. Points that matter:

- **Append only.** Store each assistant turn exactly as returned, including thinking blocks, and never edit earlier turns.
- **All tool results for a turn go back in one message.**
- Thinking is always on for this model; steer depth with `effort`. `tool_choice` must stay `auto`.
- Tool results should be compact summaries. Never send the dataset.
- The SDK's tool runner (`client.beta.messages.tool_runner`) can replace the hand-written loop if you gate `ask_human` inside the tool function; the manual loop is shown because pausing for hours and resuming from stored state is the core requirement here.

Either option uses the same tools and the same rules, so you can switch later.

### 9.4 The individual pieces

- **Plan** (shown before the run starts): one Claude call with the brief and the profile, returning a short numbered plan as structured output.
- **Label issues**: take out-of-fold predicted probabilities from the best model so far. Flag rows where the model is confident in a different class across folds (the `cleanlab` library implements this as confident learning — **verify** its API). For conflicting duplicates, group by normalized text and report groups with more than one label.
- **Synthetic data**: Claude writes paraphrases and recombinations from real examples of the starved class. Then filter: drop any synthetic row that a model trained without it classifies confidently as a different class, and drop near-duplicates of real rows. Tag every kept row `synthetic = true`. For large volumes, the Message Batches API runs this asynchronously at lower cost.
- **AutoGluon**: `TabularPredictor(label=target, eval_metric="f1_macro").fit(train, time_limit=…, presets="medium_quality")`, then `leaderboard()`. Train it on the same training rows and score it on the same folds' validation rows so its numbers are comparable. **Verify** text-column handling for the installed version; text support may need the multimodal extras.
- **Events**: Autopilot has its own stream (`autopilot:{id}:events`) carrying `step_started`, `step_done`, `question`, `answered`, `finished`. Each `train` step also has a normal run stream, so the classifier strings can be shown for whichever step is live.

---

## 10. API surface

| Page | Method and path | Purpose |
| --- | --- | --- |
| Quick Train | `POST /datasets` | Upload a file |
| | `GET /datasets/{id}/versions/{v}` | Profile and class balance |
| | `GET /recipes?dataset={id}` | Recipes that fit this data, with Vela's pick |
| | `POST /runs` | Start a run (`version`, `recipe`, `params`) |
| | `GET /runs/{id}/events` | Live and replayed events (SSE) |
| | `GET /runs/{id}` | Status and result |
| Training Gym | `POST /runs` | Same endpoint, explicit hyperparameters |
| | `POST /runs/dry` | 2,000-row dry run for the console |
| | `GET /sweeps?version=&recipe=&param=C` | Validation curve |
| Experiments | `GET /experiments/{dataset}/runs` | Table and string plot |
| | `GET /runs/{id}/curves`, `/confusion`, `/explain` | Compare, matrix, explanations |
| | `GET /experiments/{dataset}/leaderboard` | Every candidate, ranked |
| Model Registry | `GET /models/{name}/versions` | Versions and stages |
| | `POST /models/{name}/versions` | Register from a run |
| | `POST /models/{name}/versions/{v}/promote` | Approve |
| | `GET /models/{name}/lineage` | Data → run → version graph |
| Autopilot | `POST /autopilot` | Create from a brief |
| | `GET /autopilot/{id}/plan`, `POST /autopilot/{id}/start` | Plan, then run |
| | `GET /autopilot/{id}/events` | Decision log and state (SSE) |
| | `GET /autopilot/{id}/questions`, `POST /questions/{id}/answer` | Inbox |
| | `GET /autopilot/{id}/report` | Run report |
| Home | `GET /runs?active=true`, `GET /runs?recent=5` | Runs on the strings |

Return the shapes the UI already uses (`shared/data/runs.ts`, `features/quick-train/datasets.ts`, `features/autopilot/run.tsx`) so components don't change.

---

## 11. Connecting the UI

1. Add `src/shared/api/` with a small typed client and an `EventSource` helper.
2. Each feature gets a `use…` hook that returns the same data the mock returns today. Example: `useRun(id)` subscribes to `/runs/{id}/events` and reduces events into the state `QuickTrainPage` currently derives from its timer.
3. Keep the mock behind a flag (`VITE_API_URL` unset means mock mode), so the demo still runs with no backend.
4. Replace page by page, in the order of §13. The visuals don't change; only where their numbers come from.

---

## 12. Parity with other AutoML tools

Three things users expect from an AutoML product that the mock doesn't show yet. Each fits a page that already exists.

| Feature | Backend | Where it appears |
| --- | --- | --- |
| **Leaderboard** | Every candidate run for a dataset, ranked, including AutoGluon's internal models and dropped attempts | Experiments, Autopilot |
| **Explanations** | Linear models: top positive and negative terms per class from the coefficients. XGBoost: gain-based feature importance, and per-row contributions via `pred_contribs` (**verify**) | An "Explain" tab on a run |
| **Run report** | Rendered from `autopilot_steps`, `questions`, data changes and final metrics | A download on the Autopilot result |

---

## 13. Build order

Each stage ends with something you can see working in the UI.

| Stage | Build | Done when |
| --- | --- | --- |
| **1. Skeleton** | Compose file, FastAPI app, Postgres models, dataset upload and profile | Quick Train's dataset step profiles a real uploaded file from the server |
| **2. Real Quick Train** | Training engine (§6), RQ worker, events, SSE | The classifier strings and boosting wave move from real fits on the tickets and churn data |
| **3. MLflow** | `tracking.py`, Experiments endpoints, Registry endpoints, leaderboard | A finished run appears in Experiments; registering and approving it updates the Registry |
| **4. Training Gym** | Explicit params, dry run, real validation sweep, explanations | Moving a slider and starting a run changes the real result; the Explain tab shows top terms |
| **5. Autopilot** | Tools, rules, agent loop, inbox, AutoGluon, synthesis, report | A brief runs end to end, pauses on a real label question, resumes on the answer |
| **6. Hardening** | Sign-in, per-user projects, upload and runtime limits, retries, cleanup | A second person can use it without seeing the first person's data |

Stages 1–3 need no agent and no AutoGluon.

---

## 14. Testing

- **Engine**: a tiny fixed dataset where the expected F1 is known; assert that events arrive in order and that `done` matches a plain scikit-learn fit.
- **Isolation rules**: tests that fail if a synthetic row appears in a validation fold, if holdout rows are read outside `finish`, or if a second holdout evaluation succeeds.
- **Replay**: connect to a finished run's stream from offset 0 and from the middle; both must end at `done`.
- **Autopilot tools**: each tool tested without the model. Then scripted agent tests where the "model" is a fixture returning fixed tool calls, covering pause and resume.
- **Contract**: the UI's types are the contract; generate them from the API's OpenAPI schema or check them against it in CI.

---

## 15. Operations

- **Configuration**: environment variables only (`DATABASE_URL`, `REDIS_URL`, `MLFLOW_TRACKING_URI`, storage paths). The Claude credential is read from the environment by the SDK; never put it in the repo.
- **Limits**: maximum upload size, maximum rows, a wall-clock timeout per job, and the Autopilot iteration budget.
- **Failures**: a worker crash marks the run `error` and emits an `error` event; an Autopilot step that fails is retried once, then surfaced in the decision log.
- **Cost**: record `response.usage` per Autopilot run so each run shows what it spent.
- **Privacy**: the agent sees statistics and a handful of example rows, never the dataset. If example rows are sensitive, redact them before they leave your service, and prefer Option B in §9.3.

---

## 16. Open decisions

| Decision | Default assumed in this guide |
| --- | --- |
| Where the code lives | `backend/` in this repo |
| Where it runs | One machine with Docker Compose |
| Dataset size | Up to a few GB, in memory |
| Who signs in | One user until stage 6 |
| Agent hosting (§9.3) | Managed Agents, unless data-retention rules require self-hosting |
| Model for the agent | `claude-opus-5-5` |
