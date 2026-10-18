# Audit: Stage 3B Wave 1 (engine backend)

Scope: `CONTRACTS-STAGE3B.md` against the uncommitted engine changes. Paths are relative to `apps/workflow-engine/` unless noted.
Method: read every listed file in full plus the diffs of the modified files. Ran only `tests/unit`: **777 passed, 1 skipped**. I did not run integration or live tests, so claims about those are from reading them.
"(uncertain)" marks anything I could not confirm by reading.

## Verdict

Not mergeable as is. One blocker: the release check calls a method that does not exist, so every release dry run and publish would crash. Smoke (§5) has no tests at all. The API journey (§7) is missing. The AI (§1/§2), fuzz (§3) and drift (§4) halves are largely correct and well covered.

## Findings

1. **BLOCKER. `src/services/release_service.py:245`: `self._smoke_checks(snapshot)` is never defined.**
   - `grep -rn _smoke_checks src tests` finds only the call. The diff adds only the call and the comment, not the method.
   - Every `ReleaseService.run_checks` call raises `AttributeError`. That breaks dry-run and publish for all projects, not only workflows with smoke enabled.
   - Contract §5 Release: `smoke_tests_pass` per workflow in the snapshot with `enabled && before_release`, `trigger: "release"`, fail on a failed run or an error.
   - Fix: add `_smoke_checks` next to `_workflow_test_checks` (line 248).
     - For each pinned workflow, load `SmokeConfigModel`. Skip unless `enabled and before_release`.
     - Load the pinned version definition the same way `_workflow_test_checks` does (`vid not in (None, -1)`).
     - Call `smoke.run_smoke(repo, stored, "release", definition=definition)`.
     - Map `SmokeError(409)` to `fail` with the message "smoke enabled but nothing configured". Do not skip silently: a release gate that quietly passes is the wrong default.
     - Map `failed` or `error` to `fail`, with `details {workflow_id, run_id, results}`. Map `passed` to `pass`.
     - Wrap in try/except so an infrastructure exception becomes `fail`, not a 500.
   - Add an integration test for the check.

2. **MAJOR (bank-grade). `src/engine/workflow_runner.py:681-682` and `:957-960`: a live node may be any node type, and a non-HTTP live node runs `node.execute(context, ...)` for real.**
   - Postgres, email, DataTable writes and similar run against whatever they are configured for, with no read-only guard.
   - Contract §5 says "real or sandbox system". Nothing enforces "sandbox".
   - `put_config` (`src/services/smoke.py:75`) only checks that the node name exists.
   - A scheduled smoke (every cron tick) can therefore repeat side effects in production.
   - Fix:
     - In `put_config`, restrict `live_nodes` to effect `network`/`database` nodes.
     - Preferably allow only `HttpRequest` with a safe method (GET/HEAD) by default. Allow other methods or node types only with an explicit per-node `allow_writes` flag, shown in the UI.
     - Enforce the same check at run time, in `run_smoke`, not only at save time.
     - Document that the project egress allowlist still applies. I confirmed `_setup` copies it into the extras, and `http_request.py:207-219` checks it before using the injected client.

3. **MAJOR. `src/services/fuzz.py:326-333` and `src/services/test_runner.py:41,198`: the 20 s cap is checked only between runs.**
   - One hanging input can take up to `_RUN_TIMEOUT_S` = 60 s. A Code node loop does this.
   - Contract §3: "capped at count and at 20 s total".
   - Fix: wrap each fuzz run in `asyncio.wait_for(..., timeout=max(0.1, budget - elapsed))`. Treat a timeout as a `crash` finding ("input took too long") and stop. Or add a `timeout_s` argument to `_run_one`.

4. **MAJOR (bank-grade, partly policy). Data sent to the external LLM is not redacted.**
   - `src/services/test_ai_service.py:183-203`: `_definition_for_prompt` copies each node's raw `parameters`, and `test_defaults.mocks` goes in whole. A literal pasted `Authorization` header or an API key in a URL parameter would reach Gemini.
   - Contract header: "AI prompt must never contain credential values". The code has no redaction step and no test for it.
   - `src/services/fuzz.py:114-133` plus `propose_variations` (`test_ai_service.py:448`): `base_items` is the first test's real input. For `source: "replay"` tests that is production data. Nothing masks PII before it goes to the model. (uncertain whether replay tests are masked at creation; I did not check.)
   - I saw no `redact` helper imported anywhere in the new code.
   - Fix:
     - Add `_redact(definition)` that blanks values under keys matching `(?i)(authorization|api[-_]?key|token|secret|password|cookie)`.
     - Also blank query-string values in URLs and any string containing a `$secrets`/`$vars` value from the run setup (`setup.secrets`, via `vars_substitution.redact_deep`).
     - Never use `replay`-sourced inputs as `base_items`, or mask them.
     - Add a unit test: put a secret in a header and in `test_defaults`, then assert the prompt JSON does not contain it.

5. **MAJOR. `tests/` has no smoke tests at all.**
   - `grep -rl smoke tests` finds only `drift_smoke_support.py` (helpers) and the drift test file (which has no smoke test).
   - Contract §7 requires: a smoke run with `live_nodes` against `fake_refund_api`, the scheduler tick and the release check.
   - Untested:
     - 409 and 422 paths
     - info-only value ops
     - strict offline on non-live nodes
     - `live_calls` and the `next_run_at` logic
     - `_tick_smoke`
     - the release check (which is also broken, item 1)

6. **MAJOR. The API journey test (§7) is missing.**
   - No Refund-triage end-to-end covering: AI draft with the fake script, approve, fuzz finds the planted bug, save as test, v1 then v2 real run (drift and stale), smoke run, release blocked by smoke, resolve.
   - Drift, fuzz and AI each have isolated tests, but no test chains the flow.
   - It would have caught item 1.

7. **MINOR to MAJOR. `src/engine/cron_scheduler.py:64-103` `_tick_smoke`: no claim step, so multiple engine replicas double-run, and runs can overlap.**
   - Due rows are selected without `FOR UPDATE SKIP LOCKED`. The task is spawned before `next_run_at` is committed. Two replicas (or a slow run plus the next tick) can start the same smoke twice against a live system.
   - Fix: `.with_for_update(skip_locked=True)` and commit `next_run_at` before `create_task`. Skip if a `smoke_runs` row for this workflow has `finished_at IS NULL`.
   - Also: `_tick_smoke` runs only when the first half of `_tick` did not raise, so a failure there silently skips smoke (`_tick_loop` catches it). Wrap it separately.
   - `stop()` never cancels `_smoke_tasks`.

8. **MINOR. `src/services/smoke.py:78-80`: no minimum interval on the smoke schedule.**
   - `* * * * *` (or a 6-field seconds expression, which croniter accepts) would hit a bank system every minute. The tick is 30 s.
   - Fix: reject any schedule whose next two fire times are less than 15 minutes apart (value to confirm with the owner), with a 422.

9. **MINOR. `src/services/smoke.py:75`: `{n["name"] for n in definition.get("nodes")}` raises KeyError on a nameless node.** The rest of the module uses `.get`. Same in `run_smoke` lines 120-122. Use `n.get("name")`.

10. **MINOR. `src/services/drift.py:301-304`: `check_execution` compares against the current draft definition, not the version that ran.**
    - After an edit, or for released runs, contract and mock basis come from a different definition than the one executed.
    - Check whether the execution row stores a version or snapshot, and use it if so. (uncertain)

11. **MINOR (noise risk). `src/services/drift.py:190-193`: a status mismatch against the default mock is recorded as drift.**
    - Contract §4 defines the mock basis as shape only (body or items). A transient 429/500/503 from the real API (the `test_a_failing_drift_check...` case) opens an alert and marks every test that mocks the node as stale.
    - Fix: for basis `mock`, skip the status diff unless the status class (2xx vs not) matches the spec/contract. Better: do not compare shape when the status is 5xx or 4xx and the mock was 2xx; compare only against `spec`.
    - `extra` diffs on basis `mock` (`:206-208`) will also fire for every hand-written, minimal mock. That is by contract, but expect constant alerts. Consider a cap or a "mock out of date" aggregate severity.

12. **MINOR. `src/services/drift.py:264-291` `_upsert`: read-modify-write on `count` without a lock.**
    - Two concurrent runs both read `count=n` and both write `n+1`, so the count is lost. The insert race itself is handled (IntegrityError, retry).
    - Fix: `UPDATE ... SET count = count + 1 ... WHERE ...` (or `INSERT ... ON CONFLICT DO UPDATE SET count = api_drift.count + 1`).
    - In the IntegrityError branch, `session.rollback()` runs on the caller's shared session. It expires any ORM objects the caller still holds. `expire_on_commit=False` does not prevent that, and `guarded_check_execution` (`:378`) also rolls back. (uncertain whether any caller touches ORM objects after `ExecutionRepository.complete`.)

13. **MINOR (latency). `src/repositories/execution_repository.py:213-214`: drift check runs synchronously inside `complete()`.**
    - Each non-test run with a network or database node pays a few extra queries, a full `NodeOutputModel` fetch (unbounded JSON), and `project_operations(session, project_id)` for api_ref nodes.
    - This is after the run commit, so it cannot roll the run back. I confirmed `complete()` commits first, and the hook is guarded and logged. This part is correct.
    - It does delay the HTTP response for sync webhook runs.
    - Fix: run it as a fire-and-forget task on its own session, or at least `.limit()`/column-select the outputs.

14. **MINOR (unbounded reads).**
    - `src/services/drift.py:321-331`: `list_for_workflow` and `list_for_project` have no limit or pagination.
    - `smoke_runs` and `api_drift` have no retention or purge. `smoke_runs.results` is JSONB per run.
    - `src/services/drift.py:309-312`: node outputs loaded with no row or size cap.
    - Fix: `.limit(500)`, a retention job (for example 90 days) and a cap on stored result size.

15. **MINOR. `src/services/test_ai_service.py:358-378` `_dry_run`: runs sequentially with no overall time budget.** At `max` 20 and 60 s per run this can hold a request for 20 minutes. Add a total budget as fuzz has, and mark the rest `error`/"skipped".

16. **MINOR. Prompt-size control is partial (`test_ai_service.py:172-175`).** `shapes_by_node` falls back to outputs only above 60 000 chars. The rest of the prompt (definition, catalog, grammar) has no cap, and there is no model-side token estimate. Low risk; mention for cost control.

17. **MINOR. Rate limiting and cost control: none on `ai-draft` and `fuzz`.** A client can loop `ai-draft` (billable) or `fuzz` with count 200 and `ai: true`. Consistent with the rest of this API (no auth layer here), so flag for the platform level only.

18. **MINOR (test quality). `tests/unit/test_test_ai_unit.py:179-191`: the "never weaken" unit test is weaker than the contract wording.**
    - `_load` is patched to deep-copy the definition, so `assert DEFINITION == before` can never fail, whatever `draft_tests` does.
    - The real proof is `tests/integration/test_test_ai_draft.py:98-100`, which re-reads the stored tests over HTTP and compares the JSON byte for byte. That is good (not run by me).
    - Fix: in the unit test, pass `fake_load` the same object that `draft_tests` receives (no copy) and compare it after, or assert the model's `reuse`/`edit` ids never appear in `out["drafts"]` and that `draft_tests` has no write path (`repo` is `None` in the test, so a write would raise).
    - Sanitising itself is correct (`test_ai_service.py:293-312`): fresh id not in `taken`, `status`/`locked`/`source` forced, `approved_*` dropped, then validated.

19. **MINOR (test quality). `tests/integration/test_drift_live.py:111-124`: `test_a_failing_drift_check_never_changes_the_run` does not make the drift check fail.** It only shows that a 500 reply yields an alert. Add a test that monkeypatches `check_execution` to raise and asserts the run is still `success`. Add a test that a `mode="test"` run writes no drift row.

20. **MINOR (test gaps in fuzz).**
    - `tests/integration/test_fuzz_api.py:105`: `ai: true` is asserted only as `status in (200, 502, 503)`, so nothing proves `ai_inputs > 0` with the fake script.
    - There is no test of dedup counting (`count` > 1 for one finding), `crash` kind, or the 20 s budget.
    - `test_same_seed_same_findings` (`:80`) compares full responses, which is only deterministic while the time budget is not hit. This is flaky on a slow machine.
    - `src/routes/test_fuzz.py:397-399` has dead scaffolding: an ImportError fallback for a module that now exists.

21. **MINOR. Migration and model differ slightly.**
    - `db/migrations/20261018120000_drift_smoke.sql:271-272,294` create `ix_api_drift_workflow_id` and `ix_smoke_runs_workflow_id (workflow_id, started_at DESC)`, but the models declare plain `index=True` on `workflow_id` and a unique `Index` named `uq_api_drift_fingerprint` where the SQL has a `CONSTRAINT`. Harmless while tests use migrations. It matters if anything uses `create_all`.
    - No index on `smoke_configs (enabled, next_run_at)`. Fine at the current size.
    - The down migration is correct.

22. **MINOR. `src/engine/test_mode.py:286-302` `live_client`: records full request headers, including real credentials, in `context.test_requests`.** Smoke results do not expose `requests` (`_result_of`, `smoke.py:100-103`), and `persist=False`. It is safe today, but a future change that returns or persists `requests` would leak credentials. Redact `Authorization`/`Cookie` headers in `record` (the mock client has no such risk).

23. **INFO. Smoke can pass vacuously.** A smoke test whose assertions are all value ops never fails (they become `info`), per contract. `run_smoke` could warn when a selected test has no structural assertion, status ops included.

## Contract clauses verified as correct

- **§1 adapter** (`engine/test_ai.py`):
  - Fixed first lines.
  - `temperature=0.2` and `response_format` json_object.
  - Fence tolerant parser, first JSON object.
  - `not_configured` goes through `llm_provider.is_configured`, which honours the fake LLM state. `bad_json` and `provider_failed` are mapped; truncated output counts as `bad_json`.
  - The provider exception text is not leaked (only the type name).
  - Route mapping: 503 `ai_not_configured`, 502 `ai_failed`.
  - Unit and integration tests exist.
- **§2 draft**:
  - Server-side sanitising is correct (item 18).
  - The repair round happens at most once. The second `ask_json` is not retried, and a repair-time `TestAIError` is swallowed so the first-round results survive.
  - Overflow is rejected without retry.
  - Dry run with `persist=False` and `ai_run` attached.
  - Order passed, failed, error.
  - Nothing is saved. The route has no repo write path.
  - Existing tests go into the prompt as `{id, name, category, target, covers}` only.
  - Cells default to scorecard gaps.
  - Unknown cell node or category gives 422.
  - `max` is validated 1..20.
- **§3 fuzz**:
  - Deterministic per seed (`random.Random(seed)`, no set iteration or wall clock in generation).
  - Capped at `count`, baseline first, no duplicates.
  - Edge values, one-field removals, odd values, then seeded 2-3 field combos.
  - AI inputs joined and counted.
  - Dedup key `(kind, node, message with digits stripped)`, smallest input kept, `count` aggregated.
  - Finding `test`: draft, `source` fuzz or ai, `expect` status success, deterministic id that avoids existing ids.
  - Nothing is saved.
  - Schema `source` allows `fuzz`.
  - Not met: the 20 s cap (item 3).
- **§4 drift**:
  - Migration, models, upsert by sha1 fingerprint of sorted `path:kind`, reopen on repeat, spec then contract then mock order.
  - `extra` only on basis `mock`. `status` diff against the spec.
  - Hook is only for non-test modes (checked twice: `context.mode` in the repository and `execution.mode` in `check_execution`).
  - Guarded and logged. It runs after the run's own commit, so a drift failure cannot roll the run back.
  - Stale ids are derived and never written to the definition. They are present in the run report and the scorecard.
  - All three endpoints exist, including `GET /api/projects/{id}/drift` (open only) and the router is registered.
  - Resolve returns 404 for an unknown or foreign drift id.
  - Live smoke replies go through `check_outputs` (`test_runner.py:303-322`).
- **§5 smoke (implemented parts)**:
  - `live_nodes` only affects the named nodes (`workflow_runner.py:670`).
  - Other outside steps stay strictly offline (unmocked gives `UnmockedEffectError`).
  - Value ops (all 10 non-structural ops) are info-only. Structural ops and contract checks still fail.
  - 409 when no tests or no live nodes. 422 for unknown test ids, node names or cron.
  - Runs listed newest first, limit 1..200.
  - The scheduler tick exists (`_tick_smoke`). It selects enabled, scheduled and due rows, advances `next_run_at` with croniter, and disables rows with an invalid cron.
  - `fake_refund_api` has `POST /_shape` with v1 (default) and v2 as specified.
- **Item 7 (hand-copied `NodeDefinition` rebuilds)**: not applicable to this wave. The diff to `workflow_runner.py` adds no node field. `live_nodes` lives on `TestRunOptions`, so `workflow_runner.py:713`, `:1211` and the credential site, and `workflow_repository.py` dumps, need no change.
- **SQL injection**: none. All queries use the ORM with bound parameters.
- **Blocking calls**: no sync I/O in async paths. `check_workflow` and shape code are CPU-bound and run inline (fuzz and ai-draft call it once per request). Acceptable.

## Missing vs contract

| Clause | Status |
|---|---|
| §5 Release `smoke_tests_pass` check | Call site exists, **method missing** (item 1). Not implemented. |
| §7 Smoke tests (live run vs `fake_refund_api`, 409/422, info-only ops, scheduler tick, release check) | **Absent** (item 5) |
| §7 API journey test (AI draft, approve, fuzz, save, v1/v2 drift and stale, smoke, release blocked, resolve) | **Absent** (item 6) |
| §7 "drift detection ... stale ids" unit coverage | Integration only (3 tests). No unit tests for `_compare_item`, `_fingerprint`, `stale_test_ids`, spec status/`2XX`/`default` lookup. |
| §7 Fuzz "a bug is found" with AI inputs through the fake LLM | Not tested (item 20) |
| §3 20 s total cap | Not enforced per run (item 3) |
| §0 header: prompt must never contain credential values | No redaction, no test (item 4) |
| §5 "real or sandbox system" | Not enforced (item 2) |
| §6 Studio (Wave 2) and Playwright `17-ai-and-live.spec.ts` | Out of scope for Wave 1 |
| §7 Live Gemini tier | Present and correctly gated (`WORKFLOW_LIVE_GEMINI=1` plus credentials). It prints counts and names only, and asserts nothing changed. I did not run it. |
