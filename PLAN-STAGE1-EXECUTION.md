# Stage 1 execution plan (how the work is run)

Backup of the plan given in chat on 2026-10-17. The task details are in `HANDOFF-WORKFLOW-EDITOR.md` §5.

## Roles
- **Lead (main session):** writes the contracts, launches the helper agents, reviews every diff, runs the quality gates and all e2e, runs the demo, and reports to the owner. Doesn't commit until the owner says "commit".
- **Helper agents:** at most 3 at once, each with its own set of files. They never touch git and never run Playwright, except the one agent that owns e2e in wave 2.
- **Testers are separate from builders.** Tests are written from the contract, not from the implementation.

## Step 0: contracts (lead)
`CONTRACTS-STAGE1.md` freezes:
- the node fields `disabled` and `on_error`, and the `error` output with its item shape;
- the request and response shapes for the test-step, run-to, run-from and restore routes, including the 409 body;
- the editor's `?execution=` param and the `data-testid`s for every new control.

## Wave 1 (3 agents in parallel)
- **A: engine builder.** Tasks 1.1, 1.2, 1.3, 1.4 and restore, in order. Adds unit tests. Owns `workflow_runner.py`, the schemas, the services and the routes.
- **B: logic and fixtures.** `workflowDiff.ts`, `autoLayout.ts` and their vitest tests; the `refund_triage.json` fixture; the fake HTTP server; and the seeded failed run in the e2e stack.
- **C: UI without new backend.** "Open in editor" from Runs, the `?execution=` loader, the banner, "Pin this data", the ⌘K palette shell and the History sheet.

## Wave 2 (after A is done)
- **D: backend tester.** Writes `test_stage1_run_debug.py` (integration, real Postgres) and an API end-to-end scenario test that follows the demo story over HTTP:
  1. Create Refund triage.
  2. Run it so the HTTP node fails.
  3. Fetch the execution.
  4. Test one step.
  5. Run from the fixed node.
  6. Check `node_outputs`.
  7. Restore a version.

  D also runs the full engine suite.
- **E: UI wiring.** The Test step button and its source switch, the right-click Test step / Run to here / Run from here, Pin from last run, Disable with `D`, the On error select and the red `error` handle. Every control calls the real API.
- **F: Playwright owner.** The only agent that runs e2e. Writes `14-editor-run-debug.spec.ts` against the real engine, studio and Postgres stack, with no mocks. Covers:
  - disable and passthrough;
  - the error branch;
  - test step (the other nodes show no new output);
  - run to here and run from here;
  - open a run in the editor and pin;
  - History diff and restore;
  - ⌘K Tidy up.

## Wave 3 (lead)
1. Gates:
   - engine suite: 509+ passed, 2 xfails;
   - studio unit tests, `tsc`, lint (≤ 96) and `design:check`.
2. Every e2e spec, run one at a time: the 114 existing ones plus spec 14.
3. Screenshots of every UI change.
4. The demo on Refund triage.
5. Report in chat, then wait for "commit".
