# PromptLab — End-to-End Run Guide

Agentic prompt-optimization loop. An AI agent reads a session's history from
MongoDB, decides what to change about the prompt, calls a tiered evaluator
(smoke → quick → full), and iterates until it produces a canonical best run.

Assumes `workflow-engine` and `workflow-studio` are already installed (venv
created, `pnpm install` run, etc.). This guide covers only what you need to do
to actually run a PromptLab session end-to-end.

---

## Two services to boot, in this order

PromptLab needs both running simultaneously:

| Service | Port | Role |
|---|---|---|
| `prompt_lab_standalone.py` | **8001** | The evaluator — receives `/prompt-lab/evaluate` calls, runs LLM eval, persists runs to Mongo |
| `workflow-engine` (uvicorn) | **8002** | Orchestrates the agent loop — runs the `wf_promptlab` workflow |
| `workflow-studio` (optional) | 5173 | UI for triggering the webhook |

Port 8001 is hardcoded into the seeded `wf_promptlab` workflow (in `_PROMPTLAB_ANALYTICS_BASE`), so don't change it without re-seeding.

---

## Required env vars

All in `apps/workflow-engine/.env`:

```env
# Postgres (already configured if engine works)
WORKFLOW_DB_HOST=localhost
WORKFLOW_DB_PORT=5432
WORKFLOW_DB_USER=workflow
WORKFLOW_DB_PASSWORD=workflow
WORKFLOW_DB_NAME=workflows

# Mongo — MUST include auth credentials matching docker-compose
WORKFLOW_MONGO_URL=mongodb://admin:admin@localhost:27017

# Engine secrets (already configured if engine works)
WORKFLOW_ENCRYPTION_KEY=<fernet-key>

# LLM key — Gemini is required. Get one at https://aistudio.google.com/apikey
# Billing must be enabled on the GCP project; free tier daily quota is too tight.
WORKFLOW_GEMINI_API_KEY=AIza...
WORKFLOW_ANTHROPIC_API_KEY=sk-ant-...   # optional, only if you want Claude as eval model
```

---

## One-time setup

### 1. Bring up the databases

```bash
cd apps/workflow-engine
docker compose up -d mongo postgres
```

Wait for healthchecks (10–20s).

### 2. Apply schema + seed the workflow

```bash
set -a && source .env && set +a
venv/bin/python -m src.db.migrate reset    # only on first install — WIPES Postgres
venv/bin/python -m src.db.seed             # registers wf_promptlab; idempotent
```

You should see `Added [PUBLISHED v1]: PromptLab`.

Re-run only `python -m src.db.seed` (not `migrate reset`) any time you change
the workflow definition in `src/db/seed.py` — it detects drift and republishes.

### 3. Boot the standalone evaluator

```bash
cd apps/workflow-engine
set -a && source .env && set +a
PROMPTLAB_MONGO_URL='mongodb://admin:admin@localhost:27017' \
PROMPTLAB_MONGO_DB='promptlab' \
PROMPTLAB_STORAGE_DIR='/var/lib/promptlab' \
PROMPTLAB_PORT=8001 \
PROMPTLAB_LOG_LEVEL=INFO \
venv/bin/python prompt_lab_standalone.py
```

Leave running. First boot auto-creates Mongo collections and indexes
(`sessions`, `prompt_runs`, `eval_cache` + their TTL/lookup indexes).

Verify: `curl http://localhost:8001/health` → `{"status":"healthy",...}`.

### 4. Boot the workflow engine

In another terminal:

```bash
cd apps/workflow-engine
set -a && source .env && set +a
WORKFLOW_PORT=8002 SEED=0 \
venv/bin/python -m uvicorn src.main:app --host 0.0.0.0 --port 8002
```

(Or `SEED=1` to auto-seed on every boot; we already seeded manually.)

Verify: `curl http://localhost:8002/health` → `{"status":"healthy",...}`.

### 5. (Optional) Boot the UI

```bash
cd apps/workflow-studio
pnpm dev
```

---

## Running a PromptLab session

### Step A — Upload your dataset (once per dataset)

```bash
curl -X POST http://localhost:8001/prompt-lab/datasets \
  -F "file=@/path/to/your_dataset.parquet" \
  -F "target_column=expected" \
  -F 'input_columns=["input"]'
```

Returns:
```json
{"dataset_id":"ds_xxxxxxxxxxxx", "n_rows":..., "detected_classes":[...], ...}
```

**Save the `dataset_id`** — you'll reference it in every webhook call.

Supported formats: `.parquet`, `.csv`, `.xlsx`, `.xls`. The server normalizes
to parquet for storage. The dataset is content-addressed — uploading the
same file again returns the same `dataset_id` (no duplication).

Requirements:
- At least 10 rows
- A ground-truth column. Auto-detected if named `gt`/`GT`/`label`/`expected`;
  otherwise pass `-F "target_column=<name>"`.
- Input columns. Auto-detected as "everything except gt + split"; or pass
  `-F 'input_columns=["col_a","col_b"]'` to be explicit.

### Step B — Trigger the workflow

#### Option 1: webhook (curl or any HTTP client)

```bash
curl -X POST http://localhost:8002/webhook/p/promptlab/run \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "my_first_session",
    "dataset_id": "ds_xxxxxxxxxxxx",
    "intent_classes": ["positive","negative","neutral"],
    "prompt_system_seed": "Classify the sentiment as positive, negative, or neutral. Output JSON with a classified_intent field.",
    "prompt_user_template": "Row: {input}",
    "model": "gemini-2.5-flash",
    "max_iterations": 4,
    "max_cost_usd": 0.50
  }'
```

Returns when the loop finishes (typically 15–60s for a short session):
```json
{"status":"success","executionId":"exec_...","data":[...]}
```

#### Option 2: workflow-studio UI

1. Open the UI, navigate to the `PromptLab` workflow.
2. Open the bottom panel → `Input` tab.
3. Paste the same JSON body as above.
4. Click `Run`.

The UI auto-fixes raw control characters inside JSON string values — but
keep the `prompt_system_seed` value on one logical line (don't press Enter
inside the string).

### Payload field reference

| Field | Required | What it does |
|---|---|---|
| `session_id` | yes | Groups runs. Reuse to add iterations to a session; change to start fresh. |
| `dataset_id` | yes | Returned by Step A. Must exist on the standalone. |
| `intent_classes` | yes | The valid output labels. Used for confusion matrix + label extraction. |
| `prompt_system_seed` | yes | Starting prompt — the agent will refine this. Be specific about output schema + classes. |
| `prompt_user_template` | yes | Per-row template. `{column_name}` is replaced from each dataset row. Never rewritten by the agent. |
| `model` | yes | LLM for eval calls. `gemini-2.5-flash` recommended. |
| `max_iterations` | no (default 5) | Hard cap on loop iterations. |
| `max_cost_usd` | no (default 1.0) | Spend cap — agent stops at 70% of this. |

### Step C — Inspect the trajectory

#### Quick summary via mongosh

```bash
SID="my_first_session"
docker exec workflow-engine-mongo-1 mongosh -u admin -p admin --authenticationDatabase admin --quiet --eval "
db = db.getSiblingDB('promptlab');
print('=== TRAJECTORY ===');
db.prompt_runs.find({session_id:'$SID'},
  {_id:0, stage:1, strategy:1,
   'response.metrics.overall.accuracy':1,
   'response.metrics.overall.macro_f1':1,
   'response.metrics.overall.accuracy_ci_95':1,
   'response.warnings':1,
   prompt_hash:1}).sort({created_at:1}).forEach(d => print(JSON.stringify(d)));
print('=== BEST ===');
printjson(db.sessions.findOne({session_id:'$SID'},
  {_id:0, n_runs:1, best_run_id:1, best_macro_f1:1, budget_spent_usd:1}));
"
```

#### Read the winning prompt

```bash
docker exec workflow-engine-mongo-1 mongosh -u admin -p admin --authenticationDatabase admin --quiet --eval "
db = db.getSiblingDB('promptlab');
const best_id = db.sessions.findOne({session_id:'$SID'}).best_run_id;
const best = db.prompt_runs.findOne({run_id: best_id});
print(best.prompt_system);
"
```

#### See exactly which rows failed

```bash
docker exec workflow-engine-mongo-1 mongosh -u admin -p admin --authenticationDatabase admin --quiet --eval "
db = db.getSiblingDB('promptlab');
db.prompt_runs.aggregate([
  {\$match: {session_id:'$SID', stage:'full'}},
  {\$unwind: '\$response.per_row'},
  {\$match: {'response.per_row.correct': false}},
  {\$project: {_id:0, gt:'\$response.per_row.gt',
               predicted:'\$response.per_row.predicted',
               input:'\$response.per_row.inputs',
               raw_text:'\$response.per_row.raw_text'}}
]).forEach(d => print(JSON.stringify(d)));
"
```

---

## What the agent does each iteration

```
1. sessionStateQuery — reads session doc (best_macro_f1, budget)
2. trendQuery       — pulls last 10 runs (heavy fields stripped)
3. decideAgent      — gemini-2.5-flash agent with mongoQuery tool. Inspects
                      the trend, optionally runs deeper Mongo queries, and
                      emits structured JSON:
                        { action: "continue"|"stop",
                          strategy: "<what to change and why>",
                          entry_stage: "smoke"|"quick"|"full",
                          target_confusion_pair: "...", reasoning: "..." }
4. promptWriter     — gemini-2.5-flash rewrites the system prompt based on
                      the agent's strategy. On strategy="baseline" it passes
                      the seed through verbatim (pass-through rule).
5. evalCall         — POSTs to /prompt-lab/evaluate with the agent's chosen
                      stage. Server resolves stage to sample size:
                        smoke = n=10 random
                        quick = n=100 stratified by gt
                        full  = whole dataset
6. stopCheck        — stop if agent said 'stop' OR cumulative spend hit cap.
                      Otherwise loop back to step 1.
7. (loop ends) fetchAllRuns → summarize — produces markdown verdict
```

### Stage semantics

| Stage | Sample | Updates `best_run_id`? |
|---|---|---|
| smoke | 10 rows random | no |
| quick | 100 rows stratified by gt | no |
| full | entire dataset | **yes** (if macro_f1 > current best) |

The agent owns escalation across iterations — after a passing smoke, it
should pick `quick` on the next iteration with the **same** strategy; after
a passing quick, it should pick `full`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Command find requires authentication` on `sessionStateQuery` | `WORKFLOW_MONGO_URL` env var missing or lacks `admin:admin@` | Set it in `.env` and restart the engine |
| `evalCall` fails with `All connection attempts failed` | Standalone (port 8001) not running | Boot it (step 3) |
| `429 RESOURCE_EXHAUSTED` on `decideAgent` | Gemini free-tier daily quota exhausted | Enable billing on the GCP project, or wait until midnight Pacific for reset |
| `404 NOT_FOUND. This model models/gemini-2.0-flash is no longer available` | Retired model name in workflow | Edit `src/db/seed.py`, change model to `gemini-2.5-flash`, re-run `python -m src.db.seed` |
| `Payload JSON invalid: Bad control character` in UI | Raw newline pasted inside a JSON string | The UI now auto-fixes; if it doesn't, paste the JSON with `prompt_system_seed` value all on one line |
| Trajectory shows `[Expression Error: Attribute 'prompt_system' does not exist]` in iter 0's `prompt_system` | Out-of-date workflow definition | Re-run `python -m src.db.seed` (fix uses `documentCount > 0` guard) |
| `best_run_id` stays null despite high-scoring runs | Either no `stage="full"` run yet, or all full runs scored 0 | Check `db.prompt_runs.find({session_id:SID, stage:'full'})` |
| `n_runs == max_iterations` but workflow keeps exploring | Agent's stop logic didn't fire | Acceptable — the agent never declared `action="stop"`. Lower `max_iterations` or raise the seed quality. |

---

## Where things live in Mongo

Database: `promptlab` (configurable via `PROMPTLAB_MONGO_DB`)

| Collection | Holds | TTL |
|---|---|---|
| `sessions` | Per-session aggregate state — `best_run_id`, `best_macro_f1`, `budget_spent_usd`, `n_runs` | 7 days |
| `prompt_runs` | One doc per iteration. Has `stage`, `strategy`, `prompt_system`, `response.{metrics,per_row,failures_sample,warnings,config,...}` | 14 days |
| `eval_cache` | Idempotent eval results keyed on `(prompt_hash, dataset_id, model, splits, target, inputs, sample, stage)` | 24 hours |

Indexes auto-created on startup. Don't query `prompt_runs` without
`session_id` in the filter — there's no top-level index.

---

## Daily ops

After a reboot:

```bash
cd apps/workflow-engine
docker compose up -d mongo postgres
# Wait a few seconds, then start both services:
set -a && source .env && set +a

# Terminal 1 — standalone
PROMPTLAB_MONGO_URL='mongodb://admin:admin@localhost:27017' \
PROMPTLAB_MONGO_DB='promptlab' \
PROMPTLAB_PORT=8001 \
venv/bin/python prompt_lab_standalone.py

# Terminal 2 — engine
WORKFLOW_PORT=8002 SEED=0 \
venv/bin/python -m uvicorn src.main:app --port 8002
```

You don't need to re-migrate or re-seed unless you've changed
`src/db/seed.py` or `src/db/migrations/`.
