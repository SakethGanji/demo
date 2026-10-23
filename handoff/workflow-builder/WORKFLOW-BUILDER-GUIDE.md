# Workflow builder guide: the one-shot workflow chat

2026-10-23. Guidance for the agent on the other machine. This brings the AI workflow builder from the owner's main machine
into your copy, then retires the old staged builder. **This is the last of the three guides:** do it after
`CUT-GUIDE.md` and `ONESHOT-BUILDER-PORTING-GUIDE.md` are done and green.

**Simple and fast is the rule.** When something fails, fix the prompt or the platform. Don't add a stage, a planner or
a question round.

## Contents

1. [What this is and what was proven](#1-what-this-is-and-what-was-proven)
2. [What's in this folder](#2-whats-in-this-folder)
3. [Order with the other guides](#3-order-with-the-other-guides)
4. [Step 1: apply the patches](#4-step-1-apply-the-patches)
5. [Patch 1: engine fixes](#5-patch-1-engine-fixes)
6. [Patch 2: the one-shot chat](#6-patch-2-the-one-shot-chat)
7. [Patch 3: the read view (studio)](#7-patch-3-the-read-view-studio)
8. [Step 2: check it works](#8-step-2-check-it-works)
9. [Step 3: retire the old builder](#9-step-3-retire-the-old-builder)
10. [Known issues (fine for a POC)](#10-known-issues-fine-for-a-poc)

---

## 1. What this is and what was proven

**Before:** the editor's AI chat (`POST /api/ai/chat`) built workflows in stages: understand → spec → clarifying questions
→ rounds → repairs. It took many model calls.

**Now:**
- **Nothing open, or "generate":** one model call writes the whole workflow and its tests. The result is a `build`
  proposal.
- **A change to an open workflow:** one call returns a `changes.json` patch: nodes to add or replace, `rename`,
  `remove_nodes`, `connect`/`disconnect`, `tests`/`remove_tests`. The platform applies it to the stored draft.
  Positions, test ids and approved tests are kept.
- **A question:** one call answers in plain language.
- **Fix loop:** the platform checks the result (structure, `workflow_checker`, database rules, the workflow's own tests).
  If anything fails, up to 2 fix calls follow.
- **Review is unchanged:** the studio's review shows the proposal, and nothing is saved before Keep. "Ask for a change"
  (revise) is one more patch.
- **Routing uses no model call:** a question ("what/how/why…", or ending in "?") is answered; anything else is a change.

**Proven on the owner's machine** (Sonnet, through the `claude` CLI):

| What | Result |
|---|---|
| Build: 35 bank workflows (15 complex, 15 tough, 5 with 17–30 nodes: approval relays, escalations, day-long waits, Postgres) | 35/35 pass their hidden tests. ~25–30s simple, ~80–90s for the biggest. **Not a held-out score:** the prompt was tuned on these cases. |
| Edit: 15 change requests, patch mode | 15/15 pass, 14/15 on the first call, no changes outside the request, ~22s average |
| Live, through the real chat endpoint, then run against real Postgres, a mock bank API and a mail catcher | 4/4 chats, 1 model call each: two edits to a wire-release workflow (8/8 and 9/9 scenarios), a delinquency sweep built from nothing (approved and rejected runs correct), and an edit to it |
| Engine bugs found by the live runs | 6, all fixed test-first. They're patch 1. |

## 2. What's in this folder

| File | What | Size |
|---|---|---|
| `WORKFLOW-BUILDER-GUIDE.md` | this guide | |
| `1-engine-fixes.patch` | engine bugs the live runs found, plus `per_item` approvals and a migration (23 files) | 1.6k lines |
| `2-ai-chat.patch` | the one-shot builder (`workflow_oneshot.py`, its prompt), wiring into the chat, and 22 tests (7 files) | 1.4k lines |
| `3-read-view.patch` | studio: a read-only "Read" view of a workflow as a top-to-bottom flowchart, beside "Canvas" (8 files) | 0.7k lines |
| `oneshot_prompt.md` | **the exact builder prompt**, as a plain file so you can read it. Byte-identical to the copy inside patch 2; patch 2 creates it at `apps/workflow-engine/src/services/oneshot_prompt.md`. If you apply patch 2 by hand, copy this file there verbatim. | 193 lines |

The patches are made against GitHub `revert-to-5684ba5` at `74e8575`. On a clean copy of that commit, all three apply,
205 engine tests pass, `tsc -b` gives 0 errors and the read-view tests pass. That was checked on 2026-10-23.

Patch 2 needs patch 1 (the tests use per-item approvals). Patch 3 is independent: skip it if the owner doesn't want
the read view.

## 3. Order with the other guides

1. **`CUT-GUIDE.md`** (cut and prune). Its A1 removes only the Understand stage from the old staged chat and keeps that
   chat working.
2. **`ONESHOT-BUILDER-PORTING-GUIDE.md`** (the app builder).
3. **This guide:** step 1 (patches), step 2 (check), step 3 (retire the old staged chat).

**Why this order:** the cut makes the tree small and green first; the app builder is the biggest change and the main
product; the workflow chat is self-contained (its own service, prompt and tests) and touches few shared files, so it
goes in last with the least conflict risk.

**What that means for the patches:** they were made against GitHub `74e8575`, before the cut. Patch 1 touches only
workflow-engine runtime files the cut and the port leave alone, so it should apply cleanly. Expect conflicts in:

| File | Who changed it before you | How to resolve |
|---|---|---|
| `services/ai_builder.py` (patch 2) | CUT-GUIDE A1 removed the Understand stage | The patch's only change is a 4-line branch in `revise()`, right after the "only a ready proposal can be revised" check: if `parent.get("provider") == "oneshot"`, return `await workflow_oneshot.revise(parent, instruction, actor)`. Add it by hand. |
| `services/ai_chat_service.py` (patch 2) | maybe A1 | Add `local_route`, `use_oneshot` and the `workflow_oneshot` call in the chat stream as described in section 6. |
| `tests/unit/test_stage6_b5_chat.py`, `tests/integration/conftest.py` (patch 2) | maybe A1 | The patch only sets `WF_CHAT_BUILDER=legacy` in them. Add that line. |
| `WorkflowNavbar.tsx`, `WorkflowEditorPage.tsx` (patch 3) | CUT-GUIDE A4 removed the interface panel | Keep A4's removal and add the Canvas/Read switch and the read view (section 7). |

New files in the patches always apply: `workflow_oneshot.py`, `oneshot_prompt.md`, the tests, the migration, the read
view. Before applying, check that every helper `workflow_oneshot.py` imports (section 6, "What it reuses") still exists
after the cut. If one was deleted, restore it from git history rather than rewriting it.

## 4. Step 1: apply the patches

Run from the repo root. Keep a copy or commit of your tree first.

```bash
git apply --check handoff/workflow-builder/1-engine-fixes.patch    # then 2, then 3
git apply handoff/workflow-builder/1-engine-fixes.patch
git apply handoff/workflow-builder/2-ai-chat.patch
git apply handoff/workflow-builder/3-read-view.patch
```

**If a patch doesn't apply** (your copy differs slightly):
1. Try `git apply --3way <patch>`. This needs a git repo with the base commit.
2. Or try `git apply --reject <patch>`: it applies what it can and writes `*.rej` files for the hunks that failed.
3. Apply the failed hunks by hand. Sections 5–7 say what each file's change does, so you can redo it on your version of
   the code.
4. New files (the migration, tests, `workflow_oneshot.py`, the prompt, the read view) always apply; they're whole files.

**After patch 1, run the migration** on every database the engine uses:
- the new migration is `src/db/migrations/20261030120000_human_task_items.sql`;
- it's additive: it adds `human_tasks.item_index`, default 0, and swaps a unique index;
- use the engine's usual migrate command (`python -m src.db.migrate ... apply`, see the engine README) on the dev
  database and the test database.

**After patch 3:** in `apps/workflow-studio`, run `npm install @dagrejs/dagre@^1.1.4`. The read view lays out with it.
The patch adds it to `package.json` but not to the lock file.

## 5. Patch 1: engine fixes

Each fix has a test. Each was a real wrong result in a live run, or needed for `per_item` approvals.

| File | Change | Why |
|---|---|---|
| `db/migrations/20261030120000_human_task_items.sql`, `db/models.py` (`HumanTask.item_index`), `repositories/task_repository.py`, `repositories/execution_repository.py` | An Approval with `per_item: true` opens one task per input item. The tasks share `(execution_id, node_name, run_index)` and are told apart by `item_index`. | "Each manager approves their own row" needs N tasks from one node. |
| `nodes/flow/approval.py`, `engine/task_resumer.py` | `per_item` approval: the node emits one task per item, and the run resumes once every task in the group is decided. Each item leaves by its own decision's port. | Same as above. |
| `engine/workflow_runner.py` (resume) | On resume, `node_internal_state` is restored from the snapshot. | A Loop containing an Approval lost its remaining items after the resume. |
| `engine/expression_engine.py` (`resolve_json_template`) | A parameter stored as real JSON (a list or object) is resolved per item. Before, only JSON strings were. | Postgres `queryParameters` written as a list kept the literal `{{ ... }}` text. |
| `nodes/integrations/postgres.py` | A `transaction` runs for every item, not just item 0, and list parameters resolve. | A batch write only wrote the first row. |
| `services/task_service.py` (`validate_form_data(..., rejecting=)`) | A rejection doesn't need the form's required fields. | People couldn't reject without filling in approval-only fields. |
| `services/webhook_service.py` | Webhook-started runs get the same extras as manual runs: the API catalog (`api_specs`), the egress allowlist and the environment. | Webhook runs couldn't call catalogued APIs. |
| `engine/test_mode.py` (`shape_plan`), `engine/workflow_runner.py` (test requests) | In test mode, a mock behaves like the real node with many items: Postgres and SendEmail answer once per item; a non-`per_item` Approval gives every item item 0's decision; `on_timeout` approve/reject leaves by that port. Effects record one request per item. | Tests only saw item 0's request, so "the second manager gets a task" couldn't be tested. |
| `nodes/flow/respond.py` | In test mode the reply is recorded as a request, so a test can assert on it. | Tests couldn't check what a webhook answered. |
| `nodes/flow/switch.py`, `engine/workflow_runner.py` (Switch rules) | Switch rules see `$vars`, and rules aren't pre-resolved before the Switch evaluates them per item. | A rule using `$node["X"].json.risk` compared against the wrong thing, so no rule matched. |
| `engine/assertions.py` (`resolve_path`) | A test path may write a list index with a dot (`items.0.id`) as well as `items[0].id`. | Models write both. |

The new tests are `test_approval_per_item_pg.py`, `test_loop_approval_resume_pg.py`,
`test_postgres_transaction_per_item_pg.py` and `test_json_template_structures.py`. More cases were added to
`test_approval_node.py`, `test_stage3a_assertions.py`, `test_stage3a_mock_runner.py` and `test_task_decide_pg.py`.

## 6. Patch 2: the one-shot chat

**New files:**
- `services/workflow_oneshot.py` (~700 lines), the builder:
  - `build`, `modify`, `explain`, `revise`;
  - `apply_patch` (a rename also rewrites connections, `$node["..."]` references, test mocks and assertions);
  - `parse` (tolerates trailing commas, maps API names to ids);
  - `db_lint`;
  - the fix loop;
  - `scrub_request`.
- `services/oneshot_prompt.md`: the whole builder is this rules prompt, about 190 lines with no examples. The `{{...}}`
  slots are filled per project: node catalog, APIs, tables, variables, DB connections, sub-workflows, request. **Copy it
  verbatim.** Every rule fixed a real failure.
- `tests/unit/test_workflow_oneshot.py`: 22 tests. A fake model drives real Postgres flows: build → keep, modify → keep,
  the fix round, never-passes, the approved-test guard, explain, revise, and email scrubbing.

**Edited files:**
- `services/ai_chat_service.py`:
  - `local_route(message)` picks `explain` or `modify` with regexes, and no model call;
  - `use_oneshot()` reads `WF_CHAT_BUILDER`;
  - the chat stream calls `workflow_oneshot` unless `WF_CHAT_BUILDER=legacy`.
- `services/ai_builder.py`: `revise()` sends a proposal whose `provider == "oneshot"` to `workflow_oneshot.revise`.
- `tests/unit/test_stage6_b5_chat.py` and `tests/integration/conftest.py`: set `WF_CHAT_BUILDER=legacy`, because their
  fake-LLM scripts drive the old staged builder.

**What it reuses** (keep these when retiring the old builder):
- from `ai_builder.py` (`B`):
  - `_finish`, `load_ctx`, `check_all`, `Ctx`;
  - `_sf`, `_sha`, `_now`, `_same_project`, `_canonical_definition`, `_findings_for_model`;
  - `get_view`, `attach_subworkflows`, `clean_actor`, `_Busy`, `MAX_REQUEST_CHARS`;
  - the errors `RequestInvalid`, `BadState`, `ProposalError`, `AIFailed`;
- from `proposal_sandbox` (`sb`): `draft_of`, `_as_draft`, `_check_structure`, `fingerprint`, `run_proposal_tests`;
- `ai_spec.SPEC_VERSION`;
- `auto_layout`, `workflow_diff`;
- `ai_policy.scrub_request`;
- `AIProposalRepository`.

**Settings** (environment variables on the engine):

| Variable | Default | Meaning |
|---|---|---|
| `WF_CHAT_BUILDER` | `oneshot` | `legacy` brings back the old staged builder |
| `WF_ONESHOT_MODEL` | `cli:sonnet` | `cli:<model>` runs `claude -p --model <model>` on the engine host. Anything else goes through `engine/llm_provider.call_llm` with that model. |
| `WF_ONESHOT_CLI` | `claude` | the CLI binary |
| `WF_ONESHOT_TIMEOUT_S` | `600` | per model call |

**The model must be reachable from the engine.**
- **With the default:** the user the engine runs as needs the `claude` CLI installed and logged in. Check it with
  `claude -p "say ok"`.
- **Otherwise:** set `WF_ONESHOT_MODEL` to a model your `llm_provider` can call. That path has unit tests but has never
  run live (no API credits on the owner's machine), so check it on one real chat first.
- **Use a Sonnet-class model.** The numbers above came from Sonnet; the owner chose it over Opus for speed.

**Design choices to keep:**
- **The builder's tests are stored as drafts, not approved.** Approved tests are locked, and locked tests would block
  every later chat edit. A person approves tests in the review; only those are guarded on later edits. A patch that
  changes or removes an approved test is refused.
- **Email addresses stay in the request.** They're where the workflow sends things. Secrets, cards, IBANs and phone
  numbers are still scrubbed (`workflow_oneshot.scrub_request` protects emails around `ai_policy.scrub_request`).
- **Patch, not whole-workflow, for edits.** Same quality as rewriting the whole workflow, at about half the time and a
  quarter of the output, and the reply is a reviewable diff.

## 7. Patch 3: the read view (studio)

- **New:** `features/workflow-editor/lib/readView.ts` (pure layout with dagre), its test, and
  `components/read-view/WorkflowReadView.tsx` with `readView.css`.
- **Edited:**
  - `stores/editorLayoutStore.ts`: `viewMode: 'canvas' | 'read'`, remembered in localStorage;
  - `WorkflowNavbar.tsx`: a Canvas / Read switch (`Segmented`; test ids `editor-view-canvas` and `editor-view-read`);
  - `WorkflowEditorPage.tsx`: shows the read view over the canvas;
  - `lib/nodeIcons.ts`: a `user-check` icon for approvals.
- **Why:** the owner dropped the visual editor; bank users read workflows and edit them by chat. Each box says what the
  step does (who decides and by when, which system, which database). Each arrow says which way a decision went.
- **Merge conflict note:** CUT-GUIDE A4 also edits `WorkflowNavbar.tsx` and `WorkflowEditorPage.tsx` (removing the
  interface panel). Keep both changes.

## 8. Step 2: check it works

1. **Engine tests:**
   ```bash
   cd apps/workflow-engine
   pytest -q tests/unit/test_workflow_oneshot.py tests/unit/test_approval_node.py tests/unit/test_stage3a_assertions.py \
     tests/unit/test_stage3a_mock_runner.py tests/unit/test_task_decide_pg.py tests/unit/test_approval_per_item_pg.py \
     tests/unit/test_loop_approval_resume_pg.py tests/unit/test_postgres_transaction_per_item_pg.py \
     tests/unit/test_json_template_structures.py tests/unit/test_stage6_b5_chat.py
   ```
   On `74e8575` plus the patches: 205 passed.
   - Then run the whole unit suite and compare it with your baseline.
   - Already red on `74e8575`: `test_stage8_b5_install_pg.py` (30), `test_kg_extract`, `test_kg_team`.
   - Order-dependent, pass alone: `test_platform_data_layer::test_studio_db_values...`,
     `test_oneshot_builder::test_the_default_build_is_one_model_call...`.
2. **Studio:** `npx tsc -b` (0 errors), `npx vitest run`, `npm run lint` (no worse than before), `npm run design:check`.
3. **One real chat in the studio.** This uses the model. Tell the owner first.
   - Open a project, open the editor with no workflow, and ask: "When a webhook gets a loan application over 10000, a
     credit officer approves it within 2 days; approved → email the applicant, rejected → email them a decline."
   - Expect one `build` proposal in 30–60s with green tests. Keep it.
   - Then ask "also email risk@bank.test when it's rejected". Expect a `modify` proposal in about 20s. Only the new
     email step and its connection change.
   - Then ask "what happens if nobody answers?". Expect a plain answer and no proposal.
   - Switch to **Read**. The workflow reads top to bottom.

## 9. Step 3: retire the old builder

Do this right after step 2 passes. One area per change, and grep for importers before every deletion. CUT-GUIDE A1
already removed the Understand stage and the `kg_*` calls; delete whatever of the legacy path is left.

**Engine:**
- Remove the `WF_CHAT_BUILDER=legacy` branch in `ai_chat_service.py` and `use_oneshot()`.
- In `ai_builder.py`:
  - delete what only the legacy path uses: the Understand stage (the `kg_*` calls), `draft_spec`, the clarify questions
    and rounds, repairs and grounding;
  - **keep** every helper listed under "What it reuses" in section 6, or move them into a small `proposal_common.py`, and
    point `workflow_oneshot.py` at it;
  - keep `revise()`'s one-shot branch; the spec path below it goes.
- `ai_spec.py`: keep `SPEC_VERSION` (move it next to the helpers), and delete the rest once nothing imports it.
- `ai_repairs.py` and `ai_grounding.py`: delete once nothing imports them.
- The AI builder's use of `fuzz` and `drift`: retire it. **The editor's Tests tab keeps fuzz, drift and smoke.**

**Tests:**
- rewrite `test_stage6_b5_chat.py` and the integration chat tests (`tests/integration/conftest.py` `fake_env`) for the
  one-shot path, using a fake `workflow_oneshot.call_model` the way `test_workflow_oneshot.py` does;
- or delete the parts that only test the staged builder;
- never weaken a test that still covers live code.

**Studio** (`features/ai-builder`):
- delete the spec, clarifying-questions and rounds UI (`ClarifyQuestions`, `QuestionControl`, the small knowledge-kit
  pieces A1 moved into `ai-builder/components/`, and those stages in `useStageDriver`/`buildState`). The Understand UI
  is already gone after A1;
- **keep** the chat panel and the proposal review: diff, tests, Keep, Undo, "Ask for a change".

**Check:** step 2 again, plus a revise from the review.

## 10. Known issues (fine for a POC)

**The chat:**
- **No streaming progress:** one "building" event, then the proposal after 20s–2min.
- **No AI policy on this path:** the model allow-list, call budget and region checks (`wf_ai`) are skipped. Secrets and
  PII other than email are still scrubbed.
- **The busy lock is per process** (`B._Busy`): two engine workers could build for the same person at once.
- **The one-shot path has no HTTP-level integration test.** The fake-LLM integration tests pin `legacy`, and
  `test_workflow_oneshot.py` drives the service in process.
- **Keep with failing tests:** the review still offers Keep on a red proposal (`accept_failing`).

**What the builder produces:**
- Approval outcomes the request doesn't mention can be left unwired; a Reject then ends the run silently.
- Approvals may have no deadline.
- No `on_error` in batches: one failed item stops the rest.
- No duplicate protection: a replayed webhook runs twice.
- Webhook data is trusted as-is.

**The platform:**
- **DataTable insert/update acts on item 0 only.** With several items it writes the first and drops the rest; a Postgres
  write handles every item. Fix the DataTable node to go per item.
- **Waits are in-process,** capped at 300s and not durable across restarts.
- **A failed Postgres `transaction` doesn't fail the step.** The item gets `success: false`.
- **`four_eyes: true` is stored but not enforced.**
- **DB connections are created read-only,** and there's no API to make one writable.
