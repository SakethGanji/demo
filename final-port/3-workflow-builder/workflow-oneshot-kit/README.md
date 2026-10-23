# One-shot workflow builder: prototype, evals, live runs, edit mode

Everything needed to re-run the one-shot workflow builder experiments on another machine. Nothing here is wired into the
product yet: this folder is the prompt, the harness, the eval cases, the generated workflows and the live-run driver.

**To run it, copy this folder to `apps/workflow-engine/prototypes/workflow-oneshot/`.** The scripts import the engine from there.
The generated `runs/`, chat state and logs were left out, so the Results section describes runs you won't find here.
**Edit mode and the live runs need `runs/crazy-live`** (`wf_edit.py` and `live/setup.py` read the base workflows from it). Make it
first with a model call: `venv/bin/python -W ignore $P/wf_build.py --crazy --out $P/runs/crazy-live` (the first block below defines `$P`).
`live/enc.key` is also not included; step 4 of the live runs creates it.

All commands run from `apps/workflow-engine` with the engine's venv.

```bash
cd apps/workflow-engine
export PYTHONPATH=.:prototypes/workflow-oneshot
P=prototypes/workflow-oneshot
```

## What's here

| path | what |
|---|---|
| `PROMPT.md` | the whole builder: a ~190-line rules prompt, no examples. `{{...}}` slots are filled per project (node catalog, APIs, tables, variables, DB connections, sub-workflows, request) |
| `llm.py` | one model call. `ANTHROPIC_API_KEY` set → Messages API with `LLM_MODEL`; otherwise the `claude -p` CLI (default `sonnet`) |
| `wf_build.py` | build harness: 1 call → parse → platform layout → check (structure, checker, DB rules, the workflow's own tests) → ≤2 fix calls → score with hidden tests |
| `catalog.py` | the node catalog rendered into the prompt |
| `complex_cases.py`, `tough_cases.py`, `crazy_cases.py` | write the eval sets into `complex/`, `tough/`, `crazy/` (`eval/*.json` cases with hidden tests, `apis/*.yaml`) |
| `rescore.py` | re-score saved workflows without a model call |
| `edit_cases.py`, `wf_edit.py` | edit mode: 15 change requests on the 5 live-tested workflows, two reply formats compared |
| `runs/` | generated workflows + `results.json` per run (see Results). **Not included**; the harnesses create it |
| `live/` | real-run kit: mock bank API, DB seed, engine setup, scenario driver, logs |

## Build: run the evals

```bash
venv/bin/python -W ignore $P/wf_build.py --crazy --out $P/runs/my-run          # 5 hardest cases, 5 in parallel
venv/bin/python -W ignore $P/wf_build.py --complex [case_id ...] --out ...       # 15 complex; --tough: 15 tough; no flag: engine fixtures
venv/bin/python -W ignore $P/rescore.py --crazy $P/runs/crazy-live               # re-score without the model
```

The case generators need yaml, so run them with the engine venv: `venv/bin/python $P/crazy_cases.py`.

## Edit mode

```bash
venv/bin/python -W ignore $P/wf_edit.py --baseline              # no model: sanity of the cases against the unedited workflows
venv/bin/python -W ignore $P/wf_edit.py --mode patch [case ...] # or --mode full; writes runs/edit-<mode>/
venv/bin/python -W ignore $P/wf_edit.py --report                # compares both -> RESULTS-EDIT.md
```

The model always gets the whole current workflow (15–25k chars) plus the same rules as a build.
- `full`: it replies with the whole edited workflow.
- `patch`: it replies with `changes.json` (nodes to add/replace whole, `rename`, `remove_nodes`, `connect`/`disconnect`,
  `tests`/`remove_tests`) and the platform applies it (`apply_patch`; a rename also rewrites connections, `$node["..."]`
  references, test mocks and assertions).

The loop is the same as a build (check, ≤2 fix calls). Scoring per case:
- **landed**: python checks and the new hidden tests pass;
- **kept**: every base hidden test the change doesn't invalidate still passes;
- **drift**: original nodes outside the change (`may_change`) that were altered or removed, plus lost links;
- seconds, calls and characters.

## Live runs: real Postgres, mock bank API, real SMTP, real approvals and timeouts

1. **Postgres** (the bank's 3 databases plus the engine's own):
   ```bash
   docker run -d --name bank-demo-pg -e POSTGRES_USER=bank -e POSTGRES_PASSWORD=bankdemo -e POSTGRES_DB=bank -p 5440:5432 postgres:16-alpine
   export PGPASSWORD=bankdemo
   for db in core_banking iam_db ops_db workflows_live; do psql -h 127.0.0.1 -p 5440 -U bank -d bank -c "CREATE DATABASE $db"; done
   psql -h 127.0.0.1 -p 5440 -U bank -d bank -f $P/live/seed.sql            # tables + rows of core_banking, iam_db, ops_db
   psql -h 127.0.0.1 -p 5440 -U bank -d workflows_live -c 'CREATE SCHEMA "workflow-app"'
   venv/bin/python -m src.db.migrate --host 127.0.0.1 --port 5440 --dbname workflows_live --admin-user bank --admin-password bankdemo apply
   ```
2. **Mail**: Mailpit on SMTP :1025, UI/API :8025 (`docker run -d --name mailpit -p 1025:1025 -p 8025:8025 axllent/mailpit`,
   or the repo's docker-compose service).
3. **Mock bank API** on :9100: `(cd $P/live && nohup python3 bank_api.py 9100 > bank_api.log 2>&1 &)`.
4. **Engine** on :8020 against that Postgres (make a key once:
   `venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > $P/live/enc.key`;
   it's git-ignored-worthy, don't commit it):
   ```bash
   (WORKFLOW_ENCRYPTION_KEY=$(cat $P/live/enc.key) WORKFLOW_DB_HOST=127.0.0.1 WORKFLOW_DB_PORT=5440 WORKFLOW_DB_USER=bank \
    WORKFLOW_DB_PASSWORD=bankdemo WORKFLOW_DB_NAME=workflows_live WORKFLOW_SMTP_HOST=127.0.0.1 WORKFLOW_SMTP_PORT=1025 \
    WORKFLOW_SMTP_STARTTLS=false WORKFLOW_SMTP_SSL=false \
    nohup venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 8020 > $P/live/engine8020.log 2>&1 &)
   timeout 60 bash -c 'until curl -sf http://127.0.0.1:8020/docs >/dev/null; do sleep 1; done'
   ```
5. **Load the workflows** (project, variables, API specs pointed at :9100, the 3 DB connections with ops_db writable, the
   5 workflows with Waits shortened to 1s) and **drive the scenarios**:
   ```bash
   cd $P/live
   WF_DIR=runs/crazy-live python3 setup.py                     # writes live/setup.json
   RECERT_PARALLEL=1 RECERT_ASK1="Manager review" RECERT_ASK2="Second review" python3 drive.py [scenario-prefix]
   ```
   `drive.py` runs each scenario: it starts the workflow, decides tasks through `/api/tasks/{id}/decide`, lets the real
   sweeper time tasks out, and checks the database rows, mock-bank calls and Mailpit emails. To re-run from scratch, drop
   and recreate `workflows_live` (step 1, last 2 lines) and run setup again.

## In the product: the studio's AI chat (wired 2026-10-23)

The editor's AI chat (`POST /api/ai/chat`) now builds with this builder. The code is `src/services/workflow_oneshot.py`, with the
prompt in `src/services/oneshot_prompt.md`.
- **Nothing open, or "generate":** one call writes the whole workflow and its tests. The result is a `build` proposal.
- **A change:** one `changes.json` patch on the stored draft gives a `modify` proposal. Positions, test ids and approvals are kept.
- **A question:** one call answers it in plain language.
- **Fix loop:** the platform checks the result and runs its tests, with at most 2 fix calls. The studio's review then shows it, and
  nothing is saved before Keep.
- **The review's "Ask for a change"** (revise) is one more patch on the proposal.
- **Routing has no model call:** a question ("what/how/…", or ending in "?") is answered; anything else is a change.
- **Model:** `WF_ONESHOT_MODEL` (default `cli:sonnet` = `claude -p --model sonnet` on the engine host), timeout
  `WF_ONESHOT_TIMEOUT_S`.
- **Old builder:** `WF_CHAT_BUILDER=legacy` brings back the staged builder.

**Live, end to end** (`live/live_chat.py`). It chats on :8020 exactly like the studio, Keeps the proposal, then runs the
workflow against the real Postgres, the mock bank and Mailpit. Every chat was 1 model call, tests green:

| step | chat | then, live |
|---|---|---|
| edit `wire_release`: "when the customer says fraud, also email compliance" | 16s | 8/8 wire scenarios; compliance gets exactly the fraud email |
| edit it again: "block KP/IR wires at once, audit `blocked_country`, email compliance, no callback/screening" | 23s | 9/9 (the 8 + a new KP run) |
| build from nothing: delinquency sweep (core_banking read, per-loan email + ops_db audit, collections approval over 15000) | 36s, 9 nodes | approved and rejected runs both correct |
| edit it: "7 days past due, any status" | 19s | L1 and L2 reminded, only L1 escalated |

```bash
cd $P/live && python3 live_chat.py [wire_fraud_email wire_country_block build_delinquency edit_delinquency]
```
- Results go to `live/results-chat.json` and `live/live-chat.log`.
- Each chat's events and proposal go to `live/chat-log/`.
- `live/chat-state.json` holds the built workflow's id.
- `setup.json` must exist (run setup first).

Two bugs found live and fixed:
1. "When the customer says…" was routed as a question.
2. The PII scrubber turned every email address in a request into `[EMAIL]`. Addresses now stay; secrets, cards, IBANs and phones
   are still removed.

## Results so far

**Build** (Sonnet via `claude -p`, one call + ≤2 fix calls):
- 35/35 bank workflows pass their hidden tests: 15 complex, 15 tough and 5 crazy (17–30 nodes: approval relays,
  escalations, day-long waits, Postgres).
  - Times: ~25–30s for simple workflows, ~40–50s for complex ones, ~80–90s for the 17–30-node ones.
  - **Honesty caveat:** this is not a held-out score. The crazy cases were written right before the final prompt, and the
    prompt quotes their patterns. Freeze the prompt, then write new cases.
- Runs: `runs/r5-orig` (engine fixtures), `runs/r5-complex`, `runs/r5-tough`, `runs/crazy-sonnet2`.
- `runs/crazy-perItem` is the recert rebuilt with Approval `per_item`. `runs/crazy-live` is the set that ran live.

**Live**: 25/25 scenarios (`live/run-perItem.log`, plus `live/run-fixed.log` from before `per_item`).
- The live runs found 6 engine bugs that the mocked tests could not catch. All are fixed test-first in the engine:
  1. Postgres list params were never resolved.
  2. A Loop with an Approval lost its items after resume.
  3. Webhook runs had no API specs.
  4. Reject required form fields.
  5. A Postgres `transaction` ran only item 0.
  6. Test mode recorded only item 0's request.
- What's left for production is in `final-port/4-workflow-hardening/WORKFLOW-HARDENING-GUIDE.md`.
- `rescore.py` on crazy-live: recert scores 2/3 hidden. That's a known harness limit: a static mock can't count rows that
  the workflow wrote itself. Live it is correct. The other 4 are full.

**Edit mode** (2026-10-23, Sonnet, 15 edits × 2 modes, full table in `RESULTS-EDIT.md`):

| | full (whole workflow back) | patch (changes.json) |
|---|---|---|
| final pass | 15/15 | 15/15 |
| first-call pass | 15/15 | 14/15 (R2's first patch failed a platform check; the fix call repaired it) |
| no drift outside the change | 15/15 | 15/15 |
| avg time | 48.5s (tweak 45, add 45, restructure 56) | **22.3s** (tweak 14, add 21, restructure 31) |
| avg reply size | 20.9k chars | **5.5k chars** |

**Decision: patch is the default.** It's the same quality at about half the time and a quarter of the output. Its output is
also a reviewable diff ("changed X, added Y"), which is what a user wants to see after an edit. Full mode stays as a
fallback if a patch can't be applied.

Caveats:
- 15 cases, written by me against workflows I knew.
- 4 scoring bugs were found after the first run and fixed in the tests, identically for both modes. All four were the
  tests' fault: an `eq` on a comma-separated recipient list; asserting "customer told restricted" and "4 writes" when the
  change means no restriction; a mock that couldn't express "2nd of 2 per_item tasks rejects" (both modes chose one
  `per_item` task for the parallel committee).
- A5 legitimately touched the grouping and plan steps to carry the user's email (`may_change` widened).
- A3's "rewired" link is a legitimate insertion.
- Edited workflows have not yet been run live on :8020.
