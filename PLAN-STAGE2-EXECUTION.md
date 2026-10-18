# Stage 2 execution plan

The contracts are in `CONTRACTS-STAGE2.md`. The roles and rules are the same as in Stage 1 (`PLAN-STAGE1-EXECUTION.md`): Sonnet agents build, separate agents test, and the lead validates every step. There are at most 3 agents at once, and only one of them runs e2e.

## Wave 1 (parallel)
- **A: engine contracts.**
  - `engine/shapes.py`.
  - `BaseNode.contract()` and a contract for every node: calculated, declared and learned.
  - The instance `contract` field, which survives save, version, restore and runner rebuilds.
  - The learn, approve and remove endpoints.
  - Unit tests.
- **B: n8n import.**
  - `services/n8n_import.py`, plus a route in a new `routes/n8n_import.py`.
  - The mapping, connections and expression translation.
  - 10 real n8n workflows as fixtures in `tests/fixtures/n8n/`.
  - Unit tests.
  - `report.findings` stays empty until the checker exists.
- **C: studio shell.**
  - `lib/shapes.ts` and `stores/checkStore.ts`.
  - The red and amber node marks, the field messages and the Problems tab.
  - The n8n import dialog.
  - The publish 422 handling.
  - vitest tests with mocked replies.

## Wave 2
- **D: checker.**
  - `services/workflow_checker.py`, covering WF001–WF009, the `/check` endpoint and the publish block.
  - Wire the checker into the n8n import report.
  - Unit tests: one firing workflow and one clean workflow per code.
- **E: studio wiring.**
  - The live `/check` while editing.
  - Autocomplete from shapes before any run.
  - The "Use as contract" flow.
- **T: backend tester.**
  - Integration tests: check, learn/approve, n8n import round trip, and the publish block.
  - Golden snapshots over the seed, the Marketplace workflows, Refund triage and the 10 n8n workflows.
  - Broken copies that produce the expected codes.
  - An API journey.
- **F: Playwright.** Spec `15-editor-contracts-checker.spec.ts`:
  - a wrong field shows a mark;
  - autocomplete works before any run;
  - learn and approve a contract;
  - import an n8n workflow;
  - publish is blocked by an error.

## Wave 3 (lead)
- **Gates:**
  - the full engine suite, with no failures and the same 2 xfails;
  - the studio unit tests, `tsc`, lint (≤ 96) and `design:check`;
  - every e2e spec.
- **Screenshots** of every UI change.
- **The exit check:**
  - every existing workflow checks clean;
  - the broken copies are caught;
  - the 10 n8n workflows import.
- **Report** in chat, then wait for "commit".
