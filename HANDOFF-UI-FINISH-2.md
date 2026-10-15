# Handoff 2 — studio UI + E2E (written 2026-10-15, after HANDOFF-UI-FINISH.md steps 1–3 and part of 5)

Branch `revert-to-5684ba5`, local commits. **Do not push** (history holds live Google keys; see HANDOFF-UI-FINISH.md).
A Gemini key was supplied by the owner for live testing; it is NOT in any file. Use it only as an env var
(`GEMINI_API_KEY` + `WORKFLOW_MODULE_GEN_MODEL=gemini-3.8-flash`; `gemini-2.5-flash` is retired). The test harness
strips provider keys on purpose.

## Done
1. Reconcile: tree was clean; baseline held.
2. `POST /api/projects/{p}/modules/generate` (UI-DESIGN §10.1): `src/services/module_generator.py`, route in
   `src/routes/modules.py`. 23 integration + 15 unit tests, 7 mutation checks. **Proven live with Gemini**: repair loop,
   self-test in the real sandbox. Live run found a real defect (model omitted `from studio import api`) → static
   `undefined_name` check + prompt rule. `result.self_test` is the results ARRAY (spec updated).
3. Audit items 2–7 (engine): published-app workflow runs pinned to the active release; password-change /
   switch-to-private token rotation tests; team-secret redaction (defense in depth — could not be made to fail first);
   module save row lock + rename guard (`409 module_referenced`); blocked releases create no versions; new migration
   `20261015130000_release_legacy_dev_snapshot.sql` (**live `workflows` DB must apply it**); notes in BACKEND-DESIGN §13.x.
   `app.description` is now returned by app detail (found by E2E T20).
4. E2E stack bootstrap: `apps/workflow-engine/tests/e2e_stack.py` (was missing). Specs run via
   `cd apps/workflow-studio && npx playwright test -c e2e tests/NN-*.spec.ts` (one spec at a time).
5. E2E results: **01 4/4, 02 10/10, 03 12/12, 04 5/5** green. **05**: T21, T23 green; T20 green after the engine
   `description` fix; **T22 and T24 still failing** (see below). 06, 07 not yet run.

## Real UI bugs found by E2E and fixed (commit 86d5528 + uncommitted)
redirect toast lost on cold load (`pendingToast.ts`); delete-module refetched a deleted module (404); secret widget kept
a stale generation-time snapshot; stray "untitled" `~new` draft after generate (`docStore.renamed`).

## Engine count at last full run: 274 passed (before the +1 description test and unit additions). tsc: 44 errors (unchanged
baseline, none in new files). vitest 23.

## Next, in order
1. **T22** (05-builder): after reload the route picker shows `refundStats` NOT checked although the server stored
   `[createRefund, refundStats]`. Base UI marks checked rows with the *presence* of `data-checked` (empty value); I changed
   the assertions to presence checks. Probe all three rows' attributes after reload to decide if the picker initialises
   wrongly (real bug) or the test is wrong. Look at `route-picker-row-*` in src/features/app-builder.
2. **T24**: `response.text()` on a streamed SSE fails; use `e2e/fixtures/sse.ts` (`watchSse`/`readSse`) like T15 in spec 03.
3. Run specs 06 (publish/release/settings), 07 (workflows/responsive), then write the missing specs (UI-DESIGN §12.4: T25–T36
   not yet covered, etc.; `E2E.md` lists what exists) until all 46 are green. Known spec-vs-behaviour corrections already
   applied: traces show the `{{ $vars.KEY }}` placeholder (never the value, never `***` for successful calls); run-draft
   handler failure is an inner 502; REST tables are addressed by id (`api.tablePath()`); builder URL keeps `?slug=`.
4. Step 4 of the original handoff: finish UI slices 6b (settings + release sheets are committed but unverified in a
   browser — spec 06 will show) and 7 (workflows list, responsive, dark mode).
5. Screenshots → `screenshots/ui-built/` (light+dark project page and backend editor); update the owner's artifact
   https://claude.ai/artifact/MHUTFXcWikgVdhVZK7cZ4Z.
6. Final independent audit (the owner declined one for step 2 this session — ask before spawning; no sub-agents-of-agents),
   then report: counts, E2E table, tsc, commits, deviations. Say plainly that AI features were tested with the fake LLM in
   E2E and once live with Gemini against the generate endpoint only.

## Gotchas
- Run ONE pytest/playwright at a time. Engine suite ~80 s. `git add` only your files (never screenshots/, test-results/,
  apps/analytics-service/web/, .coverage).
- `for … break` over an httpx `iter_lines()` closes the connection (it disconnects the client!); use `next()`.
- `pgrep -f` matches your own shell; use the `/proc` scan in `test_ui_generate.sandbox_procs()`.
