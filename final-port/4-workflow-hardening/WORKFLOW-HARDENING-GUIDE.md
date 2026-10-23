# Workflow hardening guide: fix what the live runs found

**Session 4 of 5** (see `final-port/START-HERE.md`). Do it after session 3, once the one-shot workflow chat is in and green.

> **These guides were written on a different branch.** Your code may differ: file names, line numbers, sizes and test
> counts are pointers, not exact targets. Find the same thing in your copy by name and adapt. The prompts and the ideas
> stay the same.

## Goal

The one-shot workflow builder works: it builds hard bank workflows in one call, and they run end to end. But a live run
of the 5 hardest workflows and a review of all 35 found gaps that are fine for a demo and not fine for a bank. This
session closes them, so the builder's output is safe by default.

## How to work it

- **Fix the prompt or the platform, never add a stage.** Most builder-output issues are a rule in `oneshot_prompt.md`
  or a check in the existing workflow checker (for example, "every approval outcome is wired or flagged"). Platform issues
  are engine fixes.
- **Test first.** Write a failing test for each fix, then make it pass.
- **Rerun the eval kit after every prompt change** (`final-port/3-workflow-builder/workflow-oneshot-kit/`). A change that
  lowers the first-call pass rate gets reverted. Write any new eval cases *after* freezing the prompt (see
  "Evaluation honesty" below).
- **Suggested order:**
  1. The production-safety items, as prompt rules plus checker rules.
  2. The platform items, especially "DataTable acts on item 0 only", durable waits and four-eyes.
  3. The studio chat items.
  4. Stronger test assertions.
- Ask the owner before changing anything that alters behaviour users can see, such as webhook replies or approval rules.

## The issues

### Production safety (builder output)
- **Approval outcomes the request doesn't mention are left unwired.**
  - wire_release: `Callback task`, `Callback task again` and `SAR task` have nothing on `rejected`.
  - hardship_restructure: `Record answer` has nothing on `rejected`.
  - A Reject therefore ends the run silently. Wire it or flag it.
  - The checker already has this rule as a warning (`WF015` in `workflow_checker.py`: "a rejection goes nowhere"). The gap is that the builder's result passes with it, so the fix is a prompt rule and treating `WF015` as a fix-loop finding.
- **Invented risky defaults:** hardship `Officer assessment` uses `on_timeout: reject` without being asked.
- **Approvals with no deadline:** treasury, credit committee, refund manager.
- **"Four eyes" is not enforced.** (The checker has an info note, `WF016`, but the builder doesn't pass it on.) `four_eyes: true` is stored, but `authorize_decide` always allows, so one person can approve both steps. The builder doesn't say so.
- **No auth on bank API calls:** generated HTTP nodes never send `BANK_API_KEY`, and the specs declare no securitySchemes.
- **No error handling in batches.** One failed per-item call stops the run for everyone after it (kyc `Check documents`, recert `Revoke access`). Only 3 `on_error` uses in 20 hard workflows.
- **Step ordering doesn't allow a retry:**
  - sanctions_review_batch marks a row `hit` before the case is opened.
  - mortgage books in core banking before the ops_db transaction.
- **Summaries count by time window instead of this run:**
  - kyc `Count outcomes`: anything logged today.
  - recert `Read outcomes`: the last 14 days.
- **No duplicate protection:** e.g. the same loan written off twice, the same webhook replayed.
- **Webhook data is trusted:** e.g. hardship `customer_email` is not checked against the loan's customer.
- **Synchronous webhook replies wait behind human approvals:** loan_write_off with `responseMode: lastNode`.

### Platform
- DB connections are always created read-only, and there is no API to make one writable (only the `project_connections.readonly` column changes it).
- An `onReceived` webhook answers "success / Workflow triggered" before the run starts. A run that then fails is visible only in executions.
- Waits are in-process `asyncio.sleep`, capped at 300s and not durable across restarts. Still true after the patches (`nodes/flow/wait.py`).
- A failed Postgres `transaction` doesn't fail the step. (Patch 1 fixed the separate bug that a `transaction` only ran for item 0.) It emits `success: false, rolledBack: true` for that item, and the run carries on unless the workflow checks `$json.success`.
- `per_item` Approval (added by patch 1, so these are limits of the new feature):
  - Each task stores its own copy of the run snapshot.
  - The inbox has no grouped "N of M answered" view.
  - If resuming fails, only the task that triggered the resume is marked.
- **DataTable acts on item 0 only.** Still true after the patches (patch 1 doesn't touch `nodes/data/data_table.py`). A DataTable insert/update with several input items writes the first one and drops the rest
  (the prompt tells the model "one request per job"). A Postgres write handles every item. Fix the DataTable node to go per item.

### Studio chat on the one-shot builder (wired 2026-10-23)
- **The chat calls the `claude` CLI on the engine host** by default (`WF_ONESHOT_MODEL=cli:sonnet`). Any other value goes through
  `llm_provider`, but that path has had no live run: the account has no API credits. The engine user needs a logged-in `claude`.
- **No AI policy on this path:** model allow-list, call budget and region checks (`wf_ai`) are skipped. Secrets and PII are
  still scrubbed from the request (`ai_policy.scrub_request`). Variable values that aren't secret are shown to the model.
- **The builder's tests are drafts, not approved.** Approved means locked, and locked tests would block every later chat edit. A
  person approves tests in the review; only those are guarded on later edits.
- **No streaming progress:** one "building" phase event, then the proposal after 20s–2min. There's no per-round progress.
- **Busy lock is per process** (`B._BUSY`): two engine workers could build for the same person at once.
- **The legacy staged builder stays** behind `WF_CHAT_BUILDER=legacy`. The fake-LLM integration tests pin it, so the one-shot path
  has no HTTP-level test; `tests/unit/test_workflow_oneshot.py` drives the service in process.
- **Email addresses in a request reach the model** (they're where the workflow sends things). Other PII and secrets are still scrubbed.
- **Pre-existing red tests (not from this work, red on clean HEAD too):** `test_stage8_b5_install_pg.py` (30; the templates' DataTable
  mocks lack `created_at`/`updated_at` that the contract now requires), `test_kg_extract`, `test_kg_team`. Order-dependent flakes:
  `test_platform_data_layer::test_studio_db_values...`, `test_oneshot_builder::test_the_default_build_is_one_model_call...` (pass alone).
- **Keep with failing tests:** the review offers Keep on a red proposal (`accept_failing`), as for the old builder.

### Test harness
- Static mocks can't follow a workflow that uses the database as a counter. Recert counts audit rows to decide "every manager is done", and a mock returns the same rows on every pass, so the summary goes out twice in the test. Against real Postgres it goes out once. The live run proves the behaviour; the hidden test still fails on that one assertion.

### Evaluation honesty
- **35/35 first-call is not a held-out score.**
  - The crazy cases were written at 19:22 and the final prompt at 19:23.
  - The prompt quotes the cases' patterns almost word for word.
  - New cases must be written after the prompt is frozen.
- **The tests mostly prove routing.**
  - Every external is mocked.
  - Database writes are checked with "text contains".
  - The AI's own tests sometimes mock the node they assert on.
  - Add assertions on SQL parameters and email bodies, plus a check that every port is wired or flagged.
