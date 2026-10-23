# Audit: backend review fixes (12f9ae1 … 496e6ab)

Auditor: independent read-only review, 2026-10-15. Scope: commits 12f9ae1, 2545b5f, e680a96, 43a7cb8, 73efc6b,
69037a9, b41748d, 17646e0, 496e6ab, against `UI-DESIGN-REVIEW.md` (backend list + §D/§C engine findings) and the
agent's mapping in `BACKEND-PROGRESS.md` and `BACKEND-DESIGN.md` §13. The engine working tree matches HEAD (no
uncommitted engine changes).

## Suite

`venv/bin/python -m pytest tests -q`, one run: **168 passed, 0 skipped, 0 failed** (54 s). That matches the
agent's claim (108 → 168).

New skip markers: five `skipif(shutil.which("esbuild") is None)` (g1 public-app redaction, g4 app runs, the whole
g5 token module, g5 C4, g7 D17), plus the existing `needs_bwrap` on D8/D15/D16/D6. All of them ran here. On a
machine without esbuild or bwrap, the D3, D4-public-app, D6, D8, D16 and D17 proofs disappear silently.
There are no `xfail`, `if False`, `pass # TODO` or try/except blocks around asserts. There is one `time.sleep(3.2)`
(the D3 expiry test, which is legitimate given the 2 s TTL).

## Modified pre-existing tests (weakening check)

The only pre-existing test files that changed are j01/j17, j14, j16 and `conftest.py`.

| Test | Change | Verdict |
|---|---|---|
| j01 step 29/40, j17 step 6 | `is_active True / "active"` → `False / "snapshot"` for dev releases | Legitimate, documented behaviour change (D10, §13). The assertion is just as strict (exact tuple). Promote-to-prod still asserts `is_active True`. |
| j14 step 6 | api-usage `status "ok"` → `"stub"` | Legitimate. C11 required a `stub` status, and it is documented as **CHANGED** in §13. |
| j16 step 4 | `handler == {...}` → `{..., "line": 4}` | Stricter (exact dict plus exact line). Legitimate (G-UI-10). |
| conftest | adds `WORKFLOW_TEST_MODE=1` | Needed for the D3 TTL knob. It does not loosen any check. |

**No weakened tests found.**

## Findings

### 1. [major] D5 `read_only` can be bypassed through GET requests to the engine's own HTTP surface (SSRF to self)
`check_read_only` allows `http_request` with GET/HEAD (`broker/policy.py:166-170`). `EgressGuard` has no
loopback or private-address block (grep finds no `127.0.0.1`, `localhost` or `ipaddress` check in `src/broker` or
`http_request.py`), and the project allowlist is empty by default. So a read-only self-test can
`GET http://127.0.0.1:8000/webhook/<path>` or `GET /api/p/<slug>/<GET route>`. That runs workflows or python
handlers under a *normal* broker token, and those can write tables, send email and egress with secrets. The
sandbox itself is isolated correctly (`bwrap --unshare-all`; the UDS socket only speaks broker frames, so sandbox
code cannot mint a dev token). This bypass therefore goes only through the broker's HTTP egress. Severity is
capped because no production caller uses `read_only=True` yet (generate is out of scope), but the claim exists
precisely to make that self-test safe.
**Fix:** for `read_only` tokens, deny `http_request` to loopback, link-local, private ranges and the engine's own
host/port, resolving DNS before connecting (in `EgressGuard.check_host` when `claims.read_only`). Add a test that
a read-only GET to the test engine's `/webhook/...` → `403 read_only` and no execution row is created.

### 2. [major] D8 covers only router runs; published-app workflow runs in the live env still run drafts
`release_extras` is passed only from `RouteDispatcher._run_workflow` (`route_dispatcher.py:244`).
`public_apps._run_app_workflow` (`routes/public_apps.py:~479`) calls `run_saved_workflow` in the live (release) env
with no `version_id` and no `release_modules`. So `/a/{slug}/api/workflow/{W}/run` in prod serves the draft
workflow and draft PythonStep modules, which is the same "prod serves unreleased code" defect D8 describes. Cron
and webhook runs in release envs are not pinned either. The D8 test (`test_d8_python_step_in_pinned_release_uses_snapshot_module`)
is good and fails on revert, but it covers only `/api/p/`.
**Fix:** resolve the active release in `_run_app_workflow` (when the env kind is `release`), pass the pinned
`version_id` and `release_extras(release)`, and add the analogous test through `/a/{slug}/api/workflow/{W}/run`.

### 3. [major] D3: there is no test for password-change or switch-to-private rotation, and the refresh test only checks for substrings
`rotate_token_key` is called on password change and on switching to private (`app_service.py:~586,596`), but no
test asserts that an outstanding token dies afterwards. Only unpublish is tested. If those two calls were reverted,
nothing would fail. `test_d3_runtime_and_generated_client_refresh_on_401` only greps the page and `client.ts` for
`"window.__APP_REFRESH="` and `"res.status===401"`, so it would pass with broken JS. The review asked for "advance
the token clock and assert the page recovers" (an E2E concern, but no browser-level proof exists anywhere yet).
The code itself is sound:
- The unlock cookie is HMAC'd over the password hash (`public_apps.py:53-59`), so an old cookie cannot refresh after a password change.
- Private and unpublished apps 404 on `_token`.
- Preview tokens (`version_id 0`, no `rk`) cannot pass `app_token_valid_for`.
- The cookie is `httponly` and `samesite=lax`, but `secure=False` (pre-existing).

Missing hardening:
- **No rate limit on `POST /a/{slug}/_token`.** This is equivalent to page loads, so it is minor.
- **The TTL knob is controlled by an env var only.** `token_ttl_s` is honoured only when `WORKFLOW_TEST_MODE=1`, so a stray env var in prod enables it.

**Fix:** add tests that change the password and switch to private, then assert the old token → 401. Add a
Playwright or E2E check of refresh-on-401. Optionally, also require the test DB-name guard for `WORKFLOW_TEST_MODE`.

### 4. [minor] D4: the python-route trace and error are redacted without the project's team secrets
`python_handler.py:69` calls `load_run_secrets(environment=env, project_id=project.id)` with no `team_id`, so
`team_id="default"`. For a project in a non-default team, team-shared secret values are not redacted from the
stored `metadata.trace`, the node-output error, or `DispatchResult.error`. The HTTP response is still redacted,
because the central `redact_result` loads secrets with `team_id=project.team_id` (`route_dispatcher.py:162`). But
run detail (`GET …/runs/{id}`, whose `trace` is the stored one) can leak.

Other gaps in D4:
- `content_type` and non-str/dict/list bodies are not redacted.
- **No integration test for python routes.** Python-route header redaction is proven only by a unit test of `redact_result` (`test_d4_dispatch_redaction_unit_covers_python_headers`), not by a python handler that echoes a secret in a header and in exception text.
- **SSE not audited.** Studio SSE streams are not routed through the redactor. They are management surfaces, and the generate SSE is out of scope.

**Fix:** pass `team_id=project.team_id`, and add a bwrap integration test: a python route returns
`Response(headers={"X": studio.secret(...)})` and raises `ValueError(secret)`. Assert `***` in the response, the
`/test` `error`, and `runs/{id}.trace`.

### 5. [minor] B4: the optimistic-concurrency check is check-then-write (TOCTOU), and the new rename has no dependency check
`CodeModuleService.update` compares `base_source_sha` with the loaded row and then writes
(`code_module_service.py:240-256`), with no `SELECT … FOR UPDATE` and no conditional `UPDATE … WHERE source_sha=:base`.
Two concurrent PUTs with the same base can both succeed. Separately, the agent added module rename (`PUT {name}`),
but PythonStep nodes and other modules reference modules **by name** (`{"module": "steps"}`, `import steps`).
A rename silently breaks them, with no warning in the dry run.
**Fix:** lock the row (`with_for_update`) or do a conditional update, and add a concurrent test. On rename, report
PythonStep/import references in the dry run and in `breaking_changes`, or refuse the rename while the module is
referenced.

### 6. [minor] D6: `complete()`-path retention and the `RetentionCleaner._tick` wiring are untested
The test runs `prune_broker_calls_sql()` directly instead of `_tick` (`test_d6_retention_cleaner_prunes_old_and_orphaned_broker_calls`),
so a broken `text(stmt.replace("%s", ":days"))` in `_tick` would not be caught. The cron/retry `complete()` insert
path (the new `created_here` → `_cleanup`) has no test. Retention is global (100 executions engine-wide, not per
project). That matches the current spec text, but the Runs copy "keeps 100" may be read as per project.
Note also that the `virtual_tables` branch increments `calls_used` *before* the `max_calls` check, so virtual calls
are unlimited (`broker/service.py:~112`).
**Fix:** call `RetentionCleaner(sf)._tick()` in the test. Add a cron/retry-style `complete()`-only insert test.
Move the virtual branch after the call-limit check.

### 7. [minor] D10: release creation has side effects even when the release is blocked; the migration leaves legacy dev releases "active"
`build_snapshot` (non-dry) calls `_version_app`, which creates an app version and sets `app.current_version_id`
(`app_service._create_version_row`). This happens before the checks run, and `_store` commits blocked releases, so
a blocked release still mutates apps. Migration `20261015120000_release_snapshot_status.sql` only widens the CHECK.
Existing dev releases with `is_active=true` stay active. Rollback of dev now 422s, while `baseline()` prefers that
stale active row. The live `workflows` DB also needs this migration before the new code creates a dev release
(otherwise the CHECK is violated).
**Fix:** version apps only after checks pass (or inside the same transaction, rolled back on block). In the
migration's up step, add `UPDATE releases SET status='snapshot', is_active=false WHERE environment IN (draft envs)`
(it needs a join on the project env kinds). Note the migration in the progress log for the live DB.

### 8. [minor] Weak or tautological assertions
- `test_d5_allowed_functions_are_enforced`, last line: `allowed_functions is None or isinstance(..., list)` is always true. The §13 claim that "allowed_functions is now always set" is false for dev tokens without `functions` (they return `None`, meaning all functions). That is harmless but undocumented.
- `test_c5_http_stream_always_ends_with_done` runs against whatever LLM the shell env provides. The harness inherits `os.environ`, so a real `ANTHROPIC_API_KEY` would make a paid call. Its error-position assertion is vacuous when there are no errors. The two in-process C5 tests are good.
- C3: the run detail `release {id,number}` is asserted only as `None`. No test serves a run from a release.
- C4: `POST /api/apps` with a taken slug (claimed 409) is untested. The final assertion accepts `400 or 422`.
- C2: `cron`/`retry` trigger types are not exercised end to end.

**Fix:** tighten or remove the tautology. Unset provider keys in `EngineServer.start` (an allow-list env). Add a
release-served run assertion.

### 9. [minor] D2: "team scoping" is caller-asserted
`/api/variables/{id}` now refuses project rows and rows of another team (`variables.py:_team_row`), and the
list/create/env paths were already scoped to `project_id IS NULL`. The real defect (project secrets reachable
through the legacy API) is fixed, and the test fails on revert. But `team_id` is a client-supplied query parameter
with no authentication behind it, so "team scoping" is only a filter, not authorization. That is consistent with
the engine's unauthenticated management API, but the docs should not call it isolation.

### 10. [info] D1: solid
The parser rejects non-literal `auth`/`name`/`description`/path, `**kwargs`, extra positional args, unknown
keywords and out-of-set `auth` values (`code_module_service.py:44-70`). I reasoned through:
- **Rejected:** f-strings (`JoinedStr`), names, attribute access, `IfExp`, concatenation via `+` (`BinOp`).
- **Accepted:** implicit literal concatenation, which is a `Constant` (fine).
- **Not routes at all:** decorator aliases (`API = api; @API.get`), `@studio.api.get`, decorators inside `if`/class bodies, and non-decorator `api.get(...)(f)` calls. They are never discovered, so no route row is created and nothing becomes public.

Routing and auth come only from the static parse, never from runtime registration, so there is no fallback to
`"none"`. Tests are parametrised and fail on revert. Residual risk: the default with no `auth=` is still `"none"`
(public) by design.

### 11. [info] Spec conflict: apps without a saved version (D10) — implemented consistently and tested
- An app with `draft_source_code` but no version is versioned into the release (trigger `release`).
- An app with no source fails `app_calls_resolve`, with `details.unversioned` and the message "Save the app once before releasing".
- The dry run reports `version_id -1` and creates nothing.

`test_d10_unversioned_apps_are_versioned_into_the_release_or_fail_the_check` covers both branches. The behaviour is
documented in §13 as a spec disagreement. The owner should pick one. Until then, the UI-DESIGN §3.8 text
("without a saved version → fail") is wrong for draft-only apps.

### 12. [info] Out of scope, still open (review "required before/with UI")
- B1: `analysis.tables` and create-table-on-save. Only the `table_not_found` code is done.
- §8.1/§8.2 generate and run-draft (E1–E5).
- E6–E10 fake LLM (an E6 **blocker**).
- D16 for run-draft.

The agent states this explicitly. `read_only`/`virtual_tables` exist but no production caller sets them, so
finding 1 becomes live as soon as generate ships.

## Status table

| Finding | Status | Evidence |
|---|---|---|
| D1 non-literal decorator auth | verified fixed | g1 parametrised tests; §10 above |
| D2 /api/variables scoping | verified fixed (see 9) | `test_d2_project_secret_not_reachable…` |
| D3 app tokens + refresh | fixed, test weak (rotation paths, runtime JS) | finding 3 |
| D4 redaction | partially (team secrets in stored trace; python-route integration untested) | finding 4 |
| D5 read_only / allowed_functions | partially (GET-to-self bypass; not wired to a caller) | finding 1 |
| D6 retention | fixed, test weak (`_tick`, `complete()` path) | finding 6 |
| D7 module create dry_run + unsupported → 422 | verified fixed | g1 D7 tests |
| D8 release-pinned modules | partially (router only; public-app/cron runs unpinned) | finding 2 |
| D9 module delete dry_run | verified fixed | `test_d9_…` |
| D10 release outcomes | fixed (side effects / migration gaps) | finding 7, 11 |
| D11 auth loosening breaking | verified fixed | unit `test_d11_*` |
| D12 project dry-run validation | verified fixed | g6 test |
| D13 root response type | verified fixed | unit `test_d13_*` |
| D14 api_calls cache | verified fixed (tamper test is good) | `test_c11_…` D14 part |
| D15 Code-node lines | verified fixed | `test_d15_*` (sandbox + in-process) |
| D16 /test structured error | verified fixed (run-draft out of scope) | `test_d16_…` |
| D17 public-app error_code | verified fixed | `test_d17_…` |
| B1 table_not_found | partially (code only; analysis.tables / create-on-save not done) | §12 |
| B4 module concurrency | fixed, TOCTOU + rename risk | finding 5 |
| B7 (claimed "already worked") | verified: dry-run `affected_apps` + `references_slug` exist; the rest is UI | `test_b7_…` |
| C1 CORS expose | verified fixed | `test_c1_…` |
| C2 trigger.type | verified fixed (cron/retry untested) | `test_c2_*` |
| C3 request/response metadata | verified fixed; release # untested | `test_c3_…` |
| C4 slug_taken 409 | verified (POST /apps path untested) | `test_c4_…` |
| C5 builder SSE error+done | verified fixed | in-process C5 tests |
| C6 per-call stubs | verified fixed (stubs commit per call) | `test_c6_…` |
| C7 conflict payload | verified fixed | `test_c7_…` |
| C8 description preserved | verified fixed | `test_c8_…` |
| C9 one dry-run shape | verified fixed | `test_c9_…` |
| C10 /test semantics | verified (pinned) | `test_c10_…` |
| C11 api-usage stub/literals | verified fixed | `test_c11_…`, j14 |
| C13 UTC expires_at | verified fixed | `test_c13_…` |
| C14 / G-UI-5 SDK reference | verified fixed | `test_c14_…`, `test_g_ui_5_…` |
| C17 / C19 | verified fixed | g6 tests |
| G-UI-4 / G-UI-10 | verified fixed | `test_d16_…`, `test_g_ui_10_…`, j16 |
| E1–E10, generate/run-draft | not fixed (declared out of scope) | §12 |

## Verdict

**Mostly sound work. Suitable for the UI to build on, with three items to fix before generate/self-test or prod
publishing relies on them:**
1. Close the `read_only` GET-to-self/SSRF bypass (finding 1).
2. Pin public-app workflow runs to the active release (finding 2).
3. Add the missing D3 rotation tests and pass `team_id` in python-route redaction (findings 3–4).

No test was weakened. The four journey edits are documented contract changes. Claims match the code and the suite
(168/168). The cases I found where tests pass for the wrong reason are limited to substring checks
(D3 runtime), a tautology (D5) and a direct-SQL stand-in (D6 cleaner).
