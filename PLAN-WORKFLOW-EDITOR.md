# PLAN: the workflow editor (main product focus)

Rewritten 2026-10-17. This replaces the earlier version of this file. The owner's direction:
- Make the workflow editor really powerful. It is the main product. Copy n8n closely where n8n is good.
- Every workflow can have a UI: Form, Chat or Custom UI (AI-built, wired to the workflow).
- Every workflow AI builds arrives with tests.
- Login, roles and audit are **out of scope for now**. Don't build them, and don't block features on them.

**Target picture:** `screenshots/concepts/workflow-editor-with-ui.png` (source `.html` beside it; also https://claude.ai/artifact/JqZksK3N5fUG9sdmvQMGoB).
**Technical detail** (schemas, endpoints, runner changes, test layers): `DESIGN-WORKFLOWS-BACKEND.md`. Where that file and this one disagree on order or scope, this file wins. Its B0 (identity) and the role and four-eyes rules in B4 and B6 are deferred.

## How we build
- Each stage: backend first (engine, migration, API, tests), then the studio UI, then Playwright.
- A stage is done when its "done when" passes, the full engine suite and the existing e2e stay green, and lint is no worse than 96.
- Every bug fix comes with a regression test.
- Run e2e one at a time under `flock`.
- Don't commit or push until the owner says so.

## What the editor already has (verified 2026-10-17, `apps/workflow-studio/src/features/workflow-editor`)
- The three-pane node panel: `ndv/InputPanel`, `NodeSettings`, `OutputPanel`.
- Drag a field from the input into a parameter (`RunDataDisplay.tsx:107`).
- Expression editor with autocomplete from the upstream output (`ExpressionEditor.tsx:30`), but only after a run.
- Copy, cut and paste; undo and redo; duplicate; select all; nudge; sticky notes; minimap; shortcut help (`hooks/useKeyboardShortcuts.ts`).
- cURL import, execution trace tree, env profiles, AI chat panel, node creator.

**Missing** (verified):
- Single-step test, run to here, run from here.
- Loading a past run into the editor.
- Disabling a node, and a per-node error output.
- A version history UI.
- Autocomplete before the first run.
- The n8n importer.
- Tests, durable runs, interfaces, and the generator loop.

## The stages, in build order

### Stage 1. Run and debug like n8n (about 3 weeks)
The first thing a power user notices. Most of it is runner options plus UI.
1. **Test this step:** run one node with input from the upstream pin, the last run, or typed JSON. Engine: `RunOptions(start_node, stop_at)`; `POST /workflows/{id}/nodes/{name}/test`.
2. **Run to here** and **run from here.** "From here" reuses the last run's upstream outputs, so only the changed part re-runs (n8n's partial execution).
3. **Pin from last run** with one click, and show pinned nodes clearly on the canvas.
4. **Debug a past run in the editor:** open any run from Runs and see its data on every node, then pin it and fix it.
5. **Disable a node** (it passes items through) and **an error output per node** (failed items go down a red branch instead of stopping the run). Engine: `disabled` and `on_error: stop|continue|error_output` on the node definition.
6. **Version history:** list the saved versions, show a visual diff on the canvas (added, changed, removed), and restore one. `workflow_versions` already exists.
7. **⌘K command palette** for every editor action, plus **tidy up** (auto-layout).
8. **Fix the "execute" button in node details,** which says one node but runs everything.

Tests:
- pytest for start/stop/partial runs, disabled nodes, the error output and retry.
- Playwright for each item: test step, run to here, debug a past run, version diff and restore.
- This also adds the missing editor coverage: node creator, node details, import/export round trip.

**Done when:** on Refund triage you can open yesterday's failed run, see the data on every node, fix one node, re-run only from that node, and restore the previous version if needed.

### Stage 2. Contracts and the mistake checker (about 3 weeks)
1. **Node contracts:** every node knows the shape of what it takes in and gives out.
   - *Calculated:* Set, Filter, If, Merge.
   - *Declared:* triggers, Code, AI Agent.
   - *Learned:* HTTP and databases. Learned from a sample run, then approved by the user.
2. **Autocomplete without running:** the expression editor and the input panel use contracts, so fields show up before the first run.
3. **Live mistake checker,** shown as red and amber marks on nodes and parameters, with a list in the bottom panel:
   - A wrong field name.
   - A missing secret, table or sub-workflow.
   - A branch that goes nowhere.
   - A host that isn't allowed.
   - An expression that doesn't parse.

   Fixed codes, WF001 to WF009 (design §4.3).
4. **n8n import:** paste n8n JSON and get a workflow plus a report of what didn't map. JavaScript Code nodes come in switched off, with the code kept.

Tests:
- Unit tests for every node's contract and every checker code.
- Snapshot tests on the seeded and Marketplace workflows plus 10 real n8n workflows.
- Playwright: type a wrong field and see the mark; autocomplete works before any run.

**Done when:** every existing workflow checks clean, broken copies are caught, and 10 n8n workflows import.

### Stage 3. Tests inside the editor (about 3 weeks)
1. **Test cases saved with the workflow:** input, fake responses for outside systems, expected results.
2. **"Save as test" from any run:** one click turns a real run into a test case (the input plus the recorded outside responses).
3. **Tests tab** with pass/fail; **a check badge on every node**, as in the mockup; a test bar ("7/7 passing · 6 of 7 nodes covered · 0 wiring issues").
4. **Fail-closed test mode:** an outside call without a fake response fails, so tests never touch real systems.
5. **Release blocked when a test fails.**

Tests:
- Unit tests for the assertion evaluator.
- Integration tests for tests/run and the release check.
- A determinism run (20 repeats).
- Playwright: save a run as a test, break the workflow, see it fail.

**Done when:** the mockup's test bar and node badges are real on Refund triage.

### Stage 4. Runs that survive (about 3-4 weeks, after a 3-day spike)
1. **Runs continue after a crash or restart:** a database queue with worker leases, saving progress after each step. The spike decides between building it on our Postgres and adopting DBOS.
2. **Retry from failure re-runs only the failed steps.**
3. **Waits of any length** (today the limit is 300 s).
4. **Error workflows actually fire.**
5. **Idempotency key per step,** so a step that runs twice after a crash doesn't pay a refund twice.

Tests:
- Kill the worker 20 times during 50 runs; every run finishes exactly once.
- 1,000 queued runs drain with no double claims.

**Done when:** both of those tests pass nightly.

### Stage 5. UIs on workflows (about 4-5 weeks)
1. **Interface tab and panel,** as in the mockup: choose Form, Chat or Custom UI; preview with live data; the "wired to" chips; "Promote to app".
2. **Form trigger:** generated from the trigger's input contract; submitting starts a run and shows progress and the result.
3. **Chat trigger:** chat sessions with history, built on the existing ChatInput node.
4. **Custom UI:** the app builder engine generates the screen with a typed client (start run, watch run, list tasks, approve task); "change the screen" by asking AI.
5. **Approval step:** the run pauses, a task appears in an Approvals inbox with context and a form, and it continues on Approve or Reject (or times out). For now the approver is a name or email typed into the node; real users and roles come later.

Tests:
- Integration tests for form validation, the chat history, the generated client type-checking, approve/reject/timeout, double-click safety, and a task surviving a restart.
- Playwright for each interface.

**Done when:** Refund triage works end to end exactly as in the mockup (form in, AI classify, the review screen, approve, refund, result shown).

### Stage 6. AI builds the workflow (about 4 weeks)
1. **Spec first:** from the request, AI writes plain-English rules and test cases; the user edits and approves them.
2. **Build and self-test:**
   - AI builds the workflow and its interface.
   - The checker and the approved tests run, with up to 3 repairs.
   - It may change the workflow, never an approved test.
3. **Review on the canvas:** see the diff, the test results, then Keep or Undo. Nothing is saved before Keep.
4. **AI on any node:** "fix this error", "write this code", "write this expression", "explain this node". Every answer goes through the same checker and tests.
5. The current AI chat stops saving directly and uses this loop.

Tests:
- Fake-LLM scripts: first try, repairable, unrepairable, an attempted test change, secret scrubbing.
- A live opt-in run with 5 real prompts, reporting the call count.

**Done when:** "triage refunds over $500 to a manager and notify the requester" produces the mockup's workflow with passing tests, and nothing is saved before Keep.

### Stage 7. Safer releases (about 3 weeks)
1. **Shadow replay:** run the new version on the last 200 real inputs and show the differences.
2. **Release report:** the diff, the test results, what AI built, and the outside systems and tables touched.
3. **Sensitive-data labels:** mark columns as PII; the checker warns when they flow to an outside host or an LLM.
4. **AI step evals:** fixed inputs for AI nodes, and a forced re-check when the model changes.

### Stage 8. Reuse (about 3 weeks)
1. **Sub-workflows as blocks,** with typed inputs and outputs, version pinning (draft, latest or a number) and cycle detection. **Convert selection to sub-workflow** in the editor.
2. **Templates:** workflow, interface, tables and tests in one bundle; each passes its own tests in CI.

### Stage 9. Connectors (about 1 week each)
1. **A one-file node scaffold** (today a new node touches 4 places).
2. **Teams, Outlook, ServiceNow, SFTP, SQL Server, SharePoint,** in the order the pilot needs. Finish or hide `SendEmail`.

## Milestones
- **After Stages 1-3:** "a better n8n": powerful debugging, mistakes caught live, tests built in. Demo-able.
- **After Stage 5:** the mockup is real. This is the headline demo.
- **After Stage 6:** AI builds it, with tests.
- **Total:** about 30 weeks for one engineer; re-size after Stage 1 and the Stage 4 spike.

## Open decisions
1. Stage 4: build on our Postgres or adopt DBOS (the spike decides).
2. Which LLM the bank allows, and on what data (affects Stages 6 and 7).
3. Pilot use case and team (affects Stage 8 templates and Stage 9 connectors).
4. Defaults unless told otherwise: test runs kept for 7 days; n8n JavaScript Code nodes imported switched off.

## Kickoff prompt for the next session
> Read `demo/HANDOFF-WORKFLOW-EDITOR.md` first and follow it exactly. It lists what to read, the environment, the rules, and Stage 1 as tasks 1.1 to 1.8 with files and tests. Build them in order, backend and tests before UI. Don't commit until the owner says so. Report progress in chat as short ordered lists.
