# UI-DESIGN review

Reviewed 2026-10-15 against `UI-DESIGN.md` (1009 lines), the six mockups, all 10 screenshots in
`screenshots/ui-design/`, `BACKEND-DESIGN.md` §3/§10/§12, the engine at `apps/workflow-engine/src`, the live
`/openapi.json` (GET only), and the studio at `apps/workflow-studio/src`. No app code was changed.

Paths: `engine:` = `apps/workflow-engine/src/`, `studio:` = `apps/workflow-studio/src/`.
Severity: **blocker** = the feature is broken, insecure, or the E2E suite cannot pass as written;
**major** = the implementer will guess, or a flow or test is wrong; **minor** = polish or a doc fix.

---

## Summary

The structure meets the owner's direction. There are 4 pages, the project workspace does most of the
work, and the flow runs generate → edit → save → test, then Create UI, with published apps calling the
project's Python backend. The visual direction is consistent with the studio.

The design is not implementable overnight as written, for three reasons:
- **The flagship flow breaks on the backend.** The generated `refunds` module writes to a table that
  nothing creates. The self-test depends on a `read_only` broker claim that does not exist. Published
  apps stop working after one hour or after any promote.
- **Three backend security holes sit directly on the paths this UI exercises:** non-literal `auth=`,
  unscoped `/api/variables/{id}`, and unredacted router responses.
- **Large parts of the UI have no mockup and no state spec,** and several E2E scenarios cannot pass, or
  could pass while the feature is broken.

Counts: **7 blockers, 46 major, 28 minor** (81 findings).

---

## A. Owner-direction compliance

| # | Sev | Finding | Evidence | Required change |
|---|---|---|---|---|
| A1 | minor | Page count and main-page scope comply: 4 pages (D1), and Overview/Backend/Data/Secrets/Runs/Apps are workspace views. | UI-DESIGN §2.1–2.2 | None. |
| A2 | major | "Select that backend and create a UI from it **in the app builder**" is only half there. Selection happens in a dialog on the project page (§5.1). Inside the builder, the only control is the "2 routes" chip, described only as "(click to change)" (§5.2). A blank app opened from `/builder` → `/p/default?dialog=create-ui` has no way to attach routes later. | §5.1, §5.2 "Chat" bullet | Specify the builder's **route picker popover**: the same checklist as the Create UI dialog, writes `route_ids` for subsequent chat turns, persisted per app (where? `localStorage` key or the app `definition`). Add an E2E step that changes the attached routes in the builder and asserts the next chat body's `route_ids`. |
| A3 | major | "Published apps primarily use the custom Python backend" is designed, but it does not work in practice today: app tokens expire after 1 h with no refresh and are bound to `published_version_id` (see D3). | `engine:core/public_tokens.py:30`, `engine:services/route_dispatcher.py:131` | Fix D3 before the publish slice. |
| A4 | minor | "Generate by prompt using SDK functions" is covered. The SDK reference the design will feed to the model has a wrong signature (C14) and a wrong secret rule (C15). | §4.6, §8.1 step 2 | See C14/C15. |

## B. User-flow completeness

| # | Sev | Finding | Evidence | Required change |
|---|---|---|---|---|
| B1 | **blocker** | **The generated module's table is never created.** `refunds.py` calls `studio.db.table("refunds")`, but no step in setup, generate, save, test or E2E creates the `refunds` table. `DataTable` raises `table 'refunds' not found` (`engine:nodes/data/data_table.py:81-83`). The self-test in E3 ("1/1 passed"), the draft test in E4, and E6/E13/E16 all fail. `analysis.tables[{name, exists:false}]` (§8.1) is produced but has no UI. | §4.2, §8.1 analysis, §10.3 E3–E16; mockup `backend.png` outline shows a `refunds 0 rows` table that nothing created | Add **"Create missing tables"** to the draft result row and to Save. `analysis.tables` must carry inferred `columns` (from `insert(...)` keys or a model-emitted schema in `emit_module`). On Save, the UI shows "Also create table `refunds` (reason string, amount number)" (checkbox, default on) → `POST …/tables` before `POST …/modules`. Self-test and run-draft must report `table_not_found` as a distinct `error_code` with a "Create table" fix. E3 asserts the table does **not** exist after generation. E5 asserts it **does** exist after save. |
| B2 | major | Several flows have no specified UI at all: delete module (endpoint mapped, no entry point or confirm content), rename module, delete app, unpublish (endpoint mapped, no UI), the Workflows list page ("simple table", no columns, actions, create, delete or empty state), delete workflow, app settings beyond "name, workflow_ids", and API keys beyond one sentence. | §9 rows "Delete module", "Publish / unpublish"; §2.1 Workflows row; §3.7 | Add a short spec per flow: entry point, confirm text (delete module lists `routes.removed` plus used-by apps from `GET …/routes/{R}/used-by`; no module-delete dry run exists, see D9), the API calls, invalidations, and toasts. Add E2E for delete module (routes gone, app api-usage → `missing`) and unpublish (`/a/<slug>` → 404). |
| B3 | major | **Environment semantics are misleading.** Data tables are project-scoped, not per environment (`engine:db/models.py:444-475`, no `environment` column). Draft tests, self-tests, preview writes ("Preview calls your dev backend"), and the E16 in-app form all write the same rows prod serves. The dev/prod switch and the create-UI footer imply isolation. | `create-ui.png` footer; §2.4; §5.1 | State it in the UI: Test panel note "Writes go to your real tables (shared by dev and prod)", and the same in the Create UI footer and Data header. Alternatively, make POST/PUT/DELETE test sends confirm once per session. Document it in §2.4. |
| B4 | major | **Concurrent edits are lost.** `PUT …/modules/{mid}` has no version or ETag (`engine:routes/modules.py:65-69`). Two tabs, or the builder's `declare_endpoint` stubs plus an open editor, silently overwrite each other. The design keeps drafts in `sessionStorage` per module, so a stale draft can be saved over newer code. | §4.2 "Drafts … sessionStorage" | Send `base_updated_at` (or the source hash) with PUT and return `409 module_changed` on mismatch. The UI shows "refunds.py changed since you opened it — Reload / Diff / Overwrite". On reload, a restored draft whose base hash differs from the saved source must show that banner. |
| B5 | major | Undo across AI drafts is unspecified. The existing `CodeEditor` is recreated when language or placeholder changes (`studio:shared/components/ui/code-editor.tsx:142`), and it has no `readOnly`, `extensions` or `diagnostics` props (`:13-21`). The design says the draft "replaces content" and Discard "restores the previous source", but not whether ⌘Z crosses that boundary. | §4.2 step 2–3, §6.2 CodeEditor row | Specify that a draft is applied as one CM transaction (undoable with one ⌘Z), and that Discard is a transaction too. The editor instance must persist across draft/refine/save (no remount). List the new props explicitly, and that `buildTheme` (`code-editor.tsx:25-81`) is rewritten to read `--code-bg`/`--syn-*`. |
| B6 | major | **Empty, loading and error states are generic.** §7.1 has one table. There is no spec for: Backend view with sandbox unavailable *and* no module; generation streaming UI (only described in prose, no mockup); `llm_unavailable` (no key, the real default on this machine); project 404 vs. slug renamed; Runs empty when everything was pruned (retention, D6); Secrets matrix with team-inherited values; Release sheet with a blocked release (D12). | §7.1, §4.4 | Add a state table per view (Overview, Backend, Data, Secrets, Runs, Release, Builder panel, Publish), giving copy, actions and `data-testid` for each state. Mock at least: streaming composer, generation error, syntax-error state, `llm_unavailable` state. |
| B7 | major | Slug rename is unsafe and misdescribed. After a rename the old slug stops resolving. Published apps call `/api/p/<old-slug>` (relative BASE baked at publish, `engine:services/client_codegen.py:63`), so they break until republished, and hand-written `fetch('/api/p/old')` literals break permanently. The dialog copy "they keep working after republish" hides that they are broken *now*. | §3.7 General | The Settings slug change must be a confirm step listing affected apps, with the choice "Rename and republish N apps" (calls `POST /api/apps/{A}/publish` for each). Copy: "Published apps break until republished." Add E2E. |
| B8 | minor | First-run guide contradicts itself. §3.3 says steps "are not strictly sequential", but `project-first-run.png` shows steps 3 and 4 with an "after step 2" pill and no action. Step 3's "done" test (`mode in (api, sandbox)`) cannot be computed: see C2. | §3.3; screenshot | Decide. If they are gated, say so and give the reason. Redefine step 3 as "any execution whose `trigger.type` is `route_test` or `draft_test`" after C2 lands. |
| B9 | minor | The URL `/p/$project/backend/$module` uses the module **name** with `new` reserved, but `new` is a valid Python identifier and module name. | §2.2 | Reject `new` (and `generate`) in the New-module dialog, or use `/backend/~new`. |
| B10 | minor | The Overview "Secrets" card shows `PAYMENTS_TOKEN prod missing`, but the outline shows `Secrets 1` with no amber dot. `summary.missing_secrets` counts only *referenced* missing keys, while the card shows *per-env absence*. The two meanings of "missing" are never defined. | `project.png`; §3.1, §3.2 | Define "missing" = referenced by code/workflows and absent in an env (from `/secrets/missing`), versus "not set in prod" (a matrix fact). Use different copy and colours. |

## C. API mapping vs real code

| # | Sev | Finding | Evidence | Required change |
|---|---|---|---|---|
| C1 | major | **CORS exposes no headers**, and the studio calls `:8000` cross-origin (no Vite proxy). Runs pagination (`X-Next-Cursor`) and any ETag are unreadable, so the Runs "load more" is silently broken. | `engine:main.py:101-107`; `studio:shared/lib/config.ts`; `vite.config.ts` (no proxy) | Add `expose_headers=["X-Next-Cursor","X-Execution-Id","ETag"]`. E2E: create >50 runs and assert page 2 loads. |
| C2 | major | **Run `mode` values in the design do not exist.** `sandbox` is never written. `/test` runs are stored as `api` (`engine:routes/api_routes.py:137`, `engine:services/route_dispatcher.py:142`). App-token calls are `api` with `trigger.app_id`, not `app`. `POST /api/workflows/{W}/run` with `input_data` is stored as `webhook` (`engine:services/execution_service.py:91-100`). The Runs mode filter, first-run step 3, E6 ("mode api" is true only by accident) and E23 ("mode manual") fail. | §3.6, §3.3, E6, E23 | Backend: add explicit `trigger.type` ∈ {`route_test`,`draft_test`,`generate_self_test`,`app`,`api_key`,`public`} and fix the workflow-run mode. UI: filter on `trigger.type`, not `mode`. Update E6/E23 to assert those values. |
| C3 | major | **Runs do not store request/response** (only trigger, labels, handler, trace). The run sheet's "request/response (from `metadata`)" has no backing data, and the release number exists only as `release_id`. | §3.6 run sheet; agent check of execution rows | Either store a redacted `metadata.request`/`metadata.response` (size-capped, run through the redactor) or drop those sections. Resolve release # via `GET …/releases/{id}`. |
| C4 | major | **Publish errors are wrong.** A taken slug returns `400` with a plain-string `detail`, not `409` (`engine:routes/apps.py:90-97`, `engine:services/app_service.py:533`). `access` defaults to **private**, and private apps 404 (`engine:routes/public_apps.py:78`). | §5.3 step 1/3 | Backend: return `409 {"detail":{"code":"slug_taken"}}`. UI: always send `access`, default `public`, and show that it is private when chosen. E17 asserts the HTTP code. |
| C5 | major | **App-builder SSE never emits `error`.** LLM failures arrive as `text`, and unhandled exceptions end the stream without `done` (`engine:services/app_builder_ai_service.py:471,545`; `engine:routes/app_builder_ai.py:29-39`). The studio handler also lacks `phase` and `endpoints_declared` and has an extra `plan` (`studio:features/app-builder/hooks/useAppBuilderChat.ts:78-156`). | §5.2 "SSE events" row | Backend: always emit `error {code,message}` and then `done`, and wrap the generator in try/finally. Studio: treat "stream closed without done" as an error. Add an E2E (`__llm_fail__` in the builder) that asserts the error UI. |
| C6 | major | **`endpoints_declared` is unreliable.** It is lost if any stub fails (earlier stubs were already created), stubs are created only after the loop with errors swallowed, `client.ts` is not regenerated in the same turn, and `declare_endpoint` is **not offered on the first request** (`app_builder_ai_service.py:330-335,364-378,435-436,671-679`). The mockup shows "Declared GET /regions (stub)" on the first turn. | `builder.png`; §5.2 | Backend: create each stub inside the tool call, report per-stub results, offer the tool on every turn, and regenerate the client before the next model turn. Otherwise remove the first-turn stub from the mockup and the copy. |
| C7 | major | **Module-save error contract differs from §4.4.** `409 route_conflict` carries no handler info (so "handled by workflow 'Create order'" cannot be rendered), and it has a line only for same-module duplicates. Save can also return `409 name_taken` and `422 invalid_decorator`. `syntax_error.column` can be `null`. | §4.4 table; `engine:services/code_module_service.py` | Backend: add `{existing_route_id, handler:{type,id,name}, line}` to `route_conflict`. UI: handle `name_taken`, `invalid_decorator`, and a null column (whole-line diagnostic). Add E11 assertions on the payload. |
| C8 | major | **Module save overwrites route `description`** (`engine:services/code_module_service.py:130`). So the §9 "Route settings" edit (description/schema on a code route) is silently reverted on the next module save. | §9 "Route settings" row | Backend: preserve route-level fields that the decorator doesn't own. E2E: edit a description, re-save the module, assert it is unchanged. |
| C9 | major | **Module dry-run shape differs from route dry-run** (no `changes`; `engine:services/api_route_service.py:288-290` vs `code_module_service.py:181-182`). §4.2 step 5 and `BreakingChangesDialog` assume one shape. | §4.2 step 5 | Normalise both to `{routes, breaking_changes:[{kind,route,field,breaking,affected:[{app_id,app_name,file,line}]}]}` and pin it with a test. |
| C10 | major | `/test` semantics are not in the design. A non-draft `environment` → `422 environment_not_draft`. `apply_schemas` is a no-op on non-2xx. Stub routes return 501 with `execution_id:null`. | §4.7; `engine:routes/api_routes.py:111-160` | UI: disable "Use as schema" unless 2xx, and handle a null `execution_id` (no trace link). State in §2.4 that the env switch never changes the test env. |
| C11 | major | **`api-usage` has no `stub` status** (only `ok|changed|missing`), and the scanner matches only `fetch()` first arguments (`engine:services/api_usage.py:84-89`). The builder panel's `stub · 501`, `✓ App.tsx:14`, and E15's "`refundStats ✓ App.tsx:<n>`" depend on detecting `api.refundStats(` calls. | §5.2 Operations; E15 | Verify detection of typed-client calls. If it is missing, extend the scanner to `api\.(\w+)\(` against the operation set. Derive `stub` from `routes[].handler.type`. Add a unit test. |
| C12 | major | **The preview base URL needs to be specified.** The generated client uses a relative `BASE='/api/p/<slug>'` (`engine:services/client_codegen.py:63`). In preview that resolves against the Vite origin unless the IframeSandbox bridge prefixes the engine URL. The token-injection rule (UI-INTEGRATION §4.7) compares origins after that rewrite. The design never says which happens. In the E2E stack the engine is on a random port. | §5.2 "Preview auth" | Spec it: the bridge resolves relative URLs against `config.workflowApiUrl` (the §8.4 override), then injects the token only when `origin === engineOrigin && path startsWith '/api/p/'`. E16 asserts the request URL host:port equals the E2E engine. |
| C13 | minor | `preview-token.expires_at` is a **naive local-time** ISO string (`engine:routes/api_routes.py:293`, `datetime.fromtimestamp(exp).isoformat()`). "Refetch at expires_at − 5 min" is wrong when browser and server time zones differ. | §5.2 | Return UTC with `Z` (or epoch seconds). |
| C14 | major | **The static SDK reference has the wrong `Response` signature.** The design says `Response(status, headers, body)`. The code is `Response(status=200, body=None, headers=None)` (`engine:sdk/static/api.py:34-36`). This text is also the generation context (§8.1 step 2), so positional calls would swap body and headers. | §4.6 | Correct it. Generate `sdk-static.ts` and the LLM context from one source (the SDK files, or a JSON emitted by `engine:sdk/generate.py`). |
| C15 | minor | The sandbox rule "secrets only as bare `studio.secret('K')`" is wrong. `SecretRef` is a `str` placeholder `{{ $vars.K }}`, and f-strings and concatenation are allowed. Only *other* `$vars` text is rejected (`engine:broker/policy.py:88-98`, `engine:sdk/static/secret.py:6-16`). The flagship mockup (`backend.png` line 21, `f"Bearer {studio.secret(...)}"`) is valid. The rule as written would make the model avoid the correct pattern. | §8.1 step 2, §4.4 last row | Reword: "Use `studio.secret('K')` inside node parameters; embedding it in an f-string is fine; never transform it (`.upper()`, slicing, hashing)". |
| C16 | minor | G-UI-5 is already mostly implemented. `/api/sdk/functions` returns `python_names`, `egress` (bool) and `returns` today (live GET). Only signature, defaults and required-per-param are missing. | §8.5 G-UI-5; §4.6 | Rescope G-UI-5 to "add `required`/`default` per python param" and use `python_names` now. |
| C17 | minor | `summary` counts exclude modules (`engine:services/project_service.py:225`). The first-run condition "no modules" and the outline need `GET …/modules`, which the design lists. | §3.3 | Note it, or add `modules` to summary. |
| C18 | minor | The app `description` is capped at 1000 chars (`engine:schemas/app.py:35`). Create UI stores the prompt there, so a long prompt → 422. | §5.1 step 1 | Truncate to 1000, or pass the prompt only through the URL/chat. |
| C19 | minor | `client.ts`/`openapi.json` treat an unknown `environment` as draft without an error. | agent audit | Return `422 unknown_environment`. |

## D. Backend bugs and contract violations found while verifying

| # | Sev | Finding | Evidence | Required change |
|---|---|---|---|---|
| D1 | **blocker** | **SECURITY: a non-literal decorator `auth` silently becomes `"none"`**, so `@api.get("/x", auth=AUTH)` is a **public** route. AI generation makes this pattern likely. | `engine:services/code_module_service.py:49,52` | Reject non-literal `auth` (and `name`/path) with `422 invalid_decorator {line}`. Add a test. Include the rule in the generation context. |
| D2 | **blocker** | **SECURITY: `/api/variables/{id}` GET/PUT/DELETE are not scoped to team or kind**, so project secrets can be read-overwritten or deleted through the legacy Variables modal that §9 keeps "unchanged". | `engine:routes/variables.py:73-99`, `engine:repositories/variable_repository.py:48,143,164` | Scope by team and exclude project-scoped rows (or require the project secrets API). Add a test that a project secret id returns 404 on `/api/variables/{id}`. |
| D3 | **blocker** | **Published-app tokens expire after 1 h with no refresh, and are bound to `published_version_id`.** Open published pages 401 after an hour, or right after promote/rollback. E19/E20 ("published app shows the field after promote") would fail, or pass only within the hour on a fresh load. | `engine:core/public_tokens.py:30`, `engine:services/route_dispatcher.py:131` | Bind the token to the app (not the version) plus a rotation key. Add a refresh endpoint (`POST /a/{slug}/_token`) that the generated client calls on 401. E2E: advance the token clock (test-only TTL env) and assert the page recovers. |
| D4 | **blocker** | **SECURITY: router responses are not fully redacted.** The `502 handler_failed` message (`engine:services/route_dispatcher.py:207-208`), Respond-node headers (`:214`), Python-route response headers (`engine:sandbox/python_handler.py:81`) and the public-app `e.message` (`engine:routes/public_apps.py:~444`) can carry secret values. The design's run-draft, self-test `body_preview`, generate stream and E4 "`***` not the value" all rely on full redaction. | BACKEND-DESIGN §5.3 | Route every outward string (body, headers, error message, trace, stdout/stderr, SSE payloads) through the one redactor. Add an engine test where the upstream echoes the secret in body, header and exception text. |
| D5 | **blocker** | **There is no `read_only` broker claim**, and `allowed_functions` is never set (`engine:broker/tokens.py:23`). §8.1 step 5's self-test safety depends on it. Without it, generate self-tests can write data and egress with secrets on a model's whim. | UI-DESIGN §8.1 step 5 | Implement `read_only` in the token and enforce it in the broker dispatch: deny `DataTable` insert/update/delete/upsert, `send_email`, non-GET/HEAD `http_request`, and every `egress:opaque` function. Return `error_code:"read_only"`. Add tests per denied function. If you don't implement it, remove self-test from v1. |
| D6 | major | **Retention does not do what §3.6 says.** Pruning to 100 runs only in `ExecutionRepository.start` (`engine:repositories/execution_repository.py:57,340-356`). Python-route executions bypass it (`engine:sandbox/python_handler.py:40-43`). `broker_calls` are never pruned. A pruned `before` cursor silently restarts paging (`execution_repository.py:206-212`), which gives a duplicate-loop in the Runs infinite scroll. | §3.6 note | Apply retention on every insert path, cascade `broker_calls`, and return an empty page (not page 1) for an unknown cursor. |
| D7 | major | **POST `…/modules?dry_run=true` ignores `dry_run` and creates** (confirmed: `engine:routes/modules.py:53-56` has no `dry_run` parameter). Unknown query params are silently ignored on all writes. `_plan()` (`engine:services/code_module_service.py:85-116`) can back a create-preview. | G-UI-3 | Implement it now: it is ~10 lines and removes the client-side regex path in §4.3. Add a test that the module count is unchanged after the dry run. Separately, reject unknown query params on mutating routes (or at least `dry_run` where it is unsupported). |
| D8 | major | **PythonStep in release-pinned workflows runs the draft module source** (`engine:sandbox/node_runner.py:41`). Prod serves unreleased code. | — | Load modules from the release snapshot when the run is pinned. |
| D9 | major | There is no module-delete dry run, so the delete confirm cannot list affected apps before deleting. | — | Add `DELETE …/modules/{M}?dry_run=true` returning `routes.removed` + `breaking_changes`. |
| D10 | major | **Release flows have undocumented outcomes.** A blocked create/promote returns `422 checks_failed` *and stores a blocked release*. A blocked promote is `409 release_blocked`. Rollback returns `409 no_previous_release` / `422 wrong_environment`. The diff lists unchanged workflows. dev shows an "active" release while dev serves drafts. Apps without a version are skipped from snapshots (`engine:services/release_service.py:104-107`). | §3.8 | Document each code in §3.8 with its UI. Hide blocked releases from history (or badge them). Filter unchanged items from the diff. Hide "active" for draft envs. Snapshot unversioned apps or fail the `app_calls_resolve` check. |
| D11 | major | **G28: loosening auth (e.g. `api_key`→`none`) is treated as non-breaking** (`engine:services/spec_tools.py:169`). Making a route public raises no dialog. | §4.2 step 5 | Classify it as a breaking/security change that requires confirmation. |
| D12 | minor | PUT project `dry_run` validates only the slug and skips environment checks (`engine:services/project_service.py:281-303` vs `174-193`). | §3.7 | Run the same validation in dry-run mode. |
| D13 | minor | A change of root response type is not detected as breaking (`engine:services/spec_tools.py:122`). | — | Detect it. |
| D14 | minor | `api_calls` usage is re-parsed on every request instead of being cached at version creation. | — | Cache as BACKEND-DESIGN §3.6 says. |
| D15 | minor | Code-node syntax-error line numbers are off by one (`engine:sandbox/runner.py:73-77` wraps code). | Workflows page | Subtract the wrapper offset. |
| D16 | major | **The traceback location is wrong in the design.** The traceback is in `out.json error.traceback`, path `/run/job/in/modules/<name>.py`, and stderr is usually empty. The §4.4 link regex on stderr (`/run/job/modules/…`) never matches. | §4.4 "Runtime error" row | run-draft and `/test` return `error:{type,message,traceback,line,module}` (redacted). The UI links `line` directly. E2E: a handler raising on line N → the clickable link targets editor line N. |
| D17 | minor | G24: public-app run errors lack `error_code`/`details` (`engine:routes/public_apps.py:~459`). | — | Add them. |

## E. New endpoints: precision (generate, run-draft, fake LLM)

| # | Sev | Finding | Required change |
|---|---|---|---|
| E1 | major | **The generate SSE framing is ambiguous.** `error` is listed as "(event) error" in the same table as `event: message` types. It does not say whether `done` follows an error, or what the JSON mode (`Accept: application/json`) returns on error (status code and body). The `503` pre-check and the streamed `llm_unavailable` overlap. | Fix it: all events are `event: message` with `type`; terminal events are `result` or `error`, always followed by `event: done`. JSON mode: `200 result`, `422 {detail:{code:"generation_invalid",errors}}`, `502 llm_failed`, `503 llm_unavailable`, `404 module_not_found`, `422 invalid_module_name`. `llm_unavailable` is only the pre-stream 503. |
| E2 | major | **The generate spec has no limits.** It lacks max `prompt` length, max `current_source` size, enforcement of `conversation ≤ 6` (truncate or 422?), max module size on save, per-project concurrency (a second generate while one runs?), total wall-clock budget, and server behaviour on client abort (cancel the LLM call and the self-test sandbox). | Specify: prompt ≤ 8 000 chars, source ≤ 200 KB, conversation truncated server-side to the last 6, one in-flight generation per project+user (`409 generation_in_progress`), 120 s total, and cancellation on disconnect. Each gets a test. |
| E3 | major | **`module_name` collisions are unspecified.** A new-module generate with no `module_id` can propose an existing name. Save then fails `409 name_taken` after the user has tested the draft. | Validate in the analysis step: emit `analysis.conflicts` with `{kind:"module_name"}` and have the repair loop rename it. The UI shows an editable name field in the result row. |
| E4 | major | **Secret-value safety is asserted but not enforced.** Context assembly "never" includes values, but `current_source` and `conversation` are client-supplied and may contain pasted secret values. Self-test `body_preview` and repair feedback could carry upstream data back into the prompt. | Add a server-side outbound scrub: before every `call_llm`, scan the serialized messages for every decrypted secret value of the project (all envs) and replace them with `***` (log a `secret_scrubbed` warning). Self-test output is never added to LLM messages. Test: put the value in `current_source` and assert the fake call log lacks it. |
| E5 | major | **run-draft is underspecified.** It does not say: route resolution when two decorators match; how `inferred` schemas are computed (and whether they're ever persisted: they must not be); the timeout (30 s sandbox default, `engine:sandbox/backend.py:21`); request size limits; or how the draft module interacts with *other unsaved* modules open in tabs. It also never says draft runs use `default_environment` secrets (dev) and write real data (B3). | Add these, plus `error` shape (D16), `table_not_found` code (B1) and `trigger.type:"draft_test"` (C2). |
| E6 | **blocker** | **Fake-LLM activation is not test-only.** `_get_env("LLM_PROVIDER")` also reads raw `LLM_PROVIDER` and falls back to pydantic settings, which load `.env` (`engine:engine/llm_provider.py:112-127`, `engine:core/config.py:13`). A stray `.env` line or a generic `LLM_PROVIDER` env var enables it. The `/api/_test/*` endpoints then let anyone script every LLM response on that server. | Read only `os.environ["WORKFLOW_LLM_PROVIDER"]` (no `.env`, no raw fallback). Refuse to start in fake mode unless `WORKFLOW_DB_NAME` starts with `workflows_test_` **and** the bind host is loopback. Register `/api/_test/*` only then, and also require a header token from `WORKFLOW_FAKE_LLM_TOKEN`. Add an engine test that fake mode + DB `workflows` → startup error. |
| E7 | major | **The fake bypasses the provider adapters.** Dispatching in `_call_llm_once` skips `_call_anthropic`'s message and tool-schema conversion and tool-call parsing (`engine:engine/llm_provider.py:828-858`). E2E therefore does not prove the path that runs in production. It does prove prompt assembly, the app-builder tool loop and `call_llm` (good). | Keep the hook, and add adapter-level contract tests with recorded Anthropic request/response fixtures (tools as callables and as dicts, `tool_choice`, multi tool-call). Have the fake normalise `tools` with the same schema function the adapters use, and log the normalised schemas so E2E asserts `emit_module`'s schema. |
| E8 | major | **The retry loop is untestable as designed.** `raise` is non-retryable only, so the `call_llm` retry path (`llm_provider.py:790-826`) and the app-builder light→heavy model escalation (`app_builder_ai_service.py:465-541`) are never exercised. | Add `{"raise":"retryable","status":529,"times":2}` (raises an exception with `status_code`), plus `nth` matching. Add engine tests for retry-then-success and escalation (assert `model` in the call log). Make backoff sleep injectable (0 in fake mode) so tests stay fast. |
| E9 | major | **The E12 script is wrong.** `gen-broken-then-fixed` fires once. The repair call's last user message contains the syntax errors, not "broken" or "refund", so it falls to `default {"text":"OK"}` → no code → `generation_invalid`. "The repair rule then returns refunds.py" is not in the script. Also, the spec does not say whether `@file:` contents get `${E2E_UPSTREAM_URL}` interpolation, which `refunds.py` needs. | Add an explicit rule `{"id":"repair","when":{"tool":"emit_module","last_user_matches":"(?i)syntax error"},"respond":…refunds.py}`. State that interpolation applies to loaded file contents. Pin `fixtures/llm-script.json` in the doc in full. |
| E10 | minor | `default {"text":"OK"}` hides unmatched calls. | In E2E mode, make the default `{"raise":"unmatched"}` and fail the test if the call log has any `rule_id:null`. |

## F. E2E plan

| # | Sev | Finding | Evidence | Required change |
|---|---|---|---|---|
| F1 | major | **The DB name is rejected by the existing guard.** `TestDatabase` refuses names not starting with `workflows_test_` (`apps/workflow-engine/tests/integration/conftest.py:61`), and the plan uses `workflows_e2e_<8hex>`. | §10.1 step 1 | Use `workflows_test_e2e_<8hex>` (via `TEST_DB_NAME`), and keep the guard. |
| F2 | major | **The isolation leaks.** The spawned engine (cwd `apps/workflow-engine`) reads `.env` through pydantic (`engine:core/config.py:13`), so a developer `.env` (DB defaults, real LLM keys, broker paths) flows in. Inherited shell env vars (`ANTHROPIC_API_KEY`, `WORKFLOW_*`) also leak. Guards check only the port and DB name. | §10.1 step 3 | Spawn with a **clean env**: an explicit allowlist (`PATH`, `HOME`, the listed `WORKFLOW_*`), every provider key unset, and `WORKFLOW_ENV_FILE`/cwd that has no `.env`. Point sandbox/storage/socket dirs at a temp dir. Guard: `GET <engine>/api/_test/fake-llm/calls` must answer (proves fake mode) before any spec runs. |
| F3 | major | **Tests can pass while the UI is broken.** The `scrollWidth <= innerWidth` check (E25) does not catch panel-internal clipping, and `backend-1024.png` already shows the **Send button clipped** at the inspector edge while page scrollWidth is fine. There is no network-error policy, so unexpected 4xx/5xx (e.g. a 500 on `api-usage`) pass. `console.warn('[App Builder] …')` on a failed save passes, because the policy only catches warnings starting with `Warning:` (`studio:features/app-builder/hooks/useAppBuilderChat.ts:110`). | §10.5, §10.6 | Add (1) per-region overflow checks (`el.scrollWidth <= el.clientWidth` for `[data-testid$="-panel"]`, and every `button` has a non-zero visible bounding box within its panel). (2) A `page.on('response')` policy: any 5xx fails, and any 4xx not declared by the step (`expectStatus(422)`) fails. (3) Fail on all `console.warn` from studio code, with an explicit allow-list. |
| F4 | major | **Scenario assertions are wrong or ambiguous.** E3: "self-test 1/1" but `refunds.py` has two param-free GETs → 2/2 (mockup shows 2/2). E6: "200/201" (pick one; check the dispatcher). E10: "detail `HttpRequest`" contradicts §4.6 (detail = signature, type = node type). E23: expects `manual`, but the engine stores `webhook` (C2). E25: body bg is `--background #111111` (`studio:index.css:272`), not `--canvas #0a0a0a`. E14 only checks the request body, not that the server used `route_ids` (assert the fake call log's system prompt marks the 3 routes "← focus"). | §10.3 | Fix each assertion as noted. |
| F5 | major | **Missing scenarios** for flows the owner named or the design specifies: environment switch (Runs filter, Secrets highlight, client.ts prod tab); Settings (slug rename + republish, API key create/show-once/revoke, delete project 409); delete module and unpublish (B2); `llm_unavailable` with the provider unset (run a second engine, or toggle via the test endpoint); sandbox unavailable; concurrent edit 409 (B4); table auto-create (B1); published-app token refresh (D3); traceback link (D16); redaction of secrets in error message and headers (D4). | — | Add them, or record each as an explicit, accepted omission. |
| F6 | major | **Many components need fixing to meet the console policy.** Nested-button warnings come from shared cards too, not only the two listed sites: `studio:features/projects/components/AppListRow.tsx:55-56`, `AppCard.tsx:53-54`, `WorkflowCard.tsx:66-67` (and check `WorkflowListRow.tsx:94`). | §1.1, §10.6 | List and fix all of them in slice 1. |
| F7 | minor | `/usr/bin/chromium` is system Chromium 151, not the build pinned by Playwright 1.62.1, so CDP drift can flake. `esbuild` resolves only via the mise shim on PATH. | §10.1 | Prefer `npx playwright install chromium` (pinned), falling back to the system browser. Resolve `esbuild` to an absolute path in global-setup and pass it to the engine. |
| F8 | minor | Serial spec files that "build on each other" make one failure cascade, and `-g E3` cannot run alone (it needs E1/E2 state). | §10.1, §10.4 | Give each scenario an API-seeded fixture (`seedProjectWithSecret`, `seedRefundsModule`) so any scenario runs alone. |

## G. Studio-side claims and scope

| # | Sev | Finding | Evidence | Required change |
|---|---|---|---|---|
| G1 | major | **Hidden scope.** "Still valid: UI-INTEGRATION §3 data layer (`ApiError`, query keys, invalidations)" refers to code that does **not exist**. `studio:shared/lib/api.ts:79` throws a plain `Error`. There are no `['project',…]` keys, and `queryClient` is created in a component's `useState` (`studio:app/providers/QueryProvider.tsx:14-29`), so the §2.3 redirects' "hoisted `queryClient`" doesn't exist either. | §0 intro, §2.3 | Add an explicit slice-0 task: hoist the `queryClient` into router context, add `ApiError` (code/status/details from `detail`), query keys, and invalidation helpers. Estimate it. |
| G2 | minor | Several "missing" primitives already exist. Base UI 1.3.0 ships `tabs`, `toggle-group`, `checkbox` and `switch`. `PillTabs` already implements the segmented look (`studio:features/app-builder/components/ApiTesterPanel.tsx:924`). `ConversationEmptyState` exists (`studio:shared/components/ai-elements/conversation.tsx:37-50`). `ui/button-group.tsx` exists. | §1.1, §6.2 | Promote `PillTabs` to `ui/segmented.tsx` and migrate its call sites. Wrap the Base UI primitives. Don't build parallel ones. |
| G3 | minor | `useAppBuilderChat` saves `api/client.ts` today (`:84`, `:96-102`), as the design notes. `envProfilesStore` is global with key `workflow-studio:active-environment` and default `'default'` (not `dev`). | — | Migrate the store key per project, with a default of `project.default_environment`. |
| G4 | minor | `tokens.css` is described as a "verbatim copy" of `index.css`. It omits the 92 `--node-*`/`--sticky-*` tokens (shared values match). | `design-mockups/assets/tokens.css` | Fix the wording. |

## H. Visual quality

| # | Sev | Finding | Evidence | Required change |
|---|---|---|---|---|
| H1 | major | **About half the UI has no mockup:** Data view, Secrets matrix, Runs list + run sheet, Settings sheet, Release sheet (checks, diff, rollback), Publish dialog with pre-flight, BreakingChanges dialog, New module/table dialogs, ⌘K palette, Workflows list, all error and streaming states, Create UI and Builder in dark mode and at 1280/1024. An overnight implementer will invent these, and they carry the trickiest states (B6, D10). | Appendix A | Mock at minimum: Secrets matrix, Runs + sheet, Release sheet (pass and blocked), Publish pre-flight, the streaming and error composer, and Builder at 1024. |
| H2 | major | **The 1024 and 1280 layouts clip essential content, despite §7.3's claim.** At 1024 the **Send** button runs past the inspector edge. The response JSON is clipped horizontally at both widths (`"late": 2…`). The trace detail wraps "auth / ***" onto a separate line. The composer result row loses Diff and self-test at 1280. | `backend-1024.png`, `backend-1280.png` | Below 1360: the request tab bar and Send go on two rows, the response JSON wraps (`white-space: pre-wrap`) or scrolls inside its box with a visible scrollbar, and the trace detail truncates with a tooltip. Keep **Diff** at all widths (move it into an overflow `⋯`). |
| H3 | major | **Method badges fail the contrast claim.** On their 10% tints, light-mode method colours measure GET 3.54, POST 3.51, PUT/PATCH 3.29, DELETE 3.97 (all < 4.5 at 10px mono). §6.3 claims "AA contrast". `--syn-comment #9aa1a9` on `--code-bg` is 2.52:1. Gutter numbers are 1.89:1. | §6.3; computed from the token values | Darken the light method colours (e.g. GET `#0d7a41`, POST `#0a6fb8`, PUT `#94600a`, DELETE `#b8300f`, ≥ 4.5:1 on tint). Comment ≥ `#7d848c` (≥ 3.5, italic). Gutter ≥ 3:1. Pin with a token-contrast unit test. |
| H4 | minor | **The colour budget breaks its own rule.** Violet is "only AI", but `--syn-keyword` is the same `#7b61ff`, so every `def`/`import` reads as AI. PUT/PATCH amber equals the warning/stub amber, and the GET green equals the success dot, so `stub · 501` and PUT rows look alike. | §1.3, §6.3; `backend.png` | Move keywords to a non-violet hue (e.g. `#c2185b`/dark `#ff7ab6`, or primary blue). Give PATCH its own hue, or make stub pills dashed-outline. |
| H5 | minor | **The 1024 icon rail is ambiguous.** Two identical file icons (modules) and two identical table icons, no labels, and routes hidden. | `backend-1024.png` | Use a rail of view icons only (Overview, Backend, Data, Secrets, Runs). Module and table pickers appear as hover flyouts with names. |
| H6 | minor | **Mock data is inconsistent across screens.** The builder lists `listRefunds`/`listRegions` for Payments Ops, but the Overview says 2 routes and Home says 5. `createOrder` shows a green ✓ next to "not called yet". "7 today" (outline) vs "Runs · 24h". "331ms" vs "331 ms". First-run omits "API keys" in the outline while Overview has it. | `builder.png`, `project.png`, `home.png`, `project-first-run.png` | Use one dataset for all mockups. A ✓ appears only when there is an observed 2xx. Use one unit format (`331 ms`) and one time window (24h). |
| H7 | minor | **Duplicate primary actions on Overview.** "Create UI from backend" is the hero primary *and* the Apps empty-state button *and* "New app" in the Apps header, which conflicts with "one primary per region" in spirit. | `project.png` | Keep the hero primary. Make the Apps empty state a text link, and drop "New app" while the list is empty. |
| H8 | minor | The Home "Default project" card uses a pseudo-canvas with saturated node squares (`#1aabe0`, red) unlike the other cards' route lists. Cards have no ⋯ menu (rename or delete project). | `home.png`, `home.html` | Show "3 workflows" as a list like the other cards. Add a ⋯ menu (Settings, Delete). |
| H9 | minor | Dark mode is shown only for Overview and Backend, and both look correct (contrast fine; method colours 5.7–7.4:1). The builder preview's black "Create order" button and indigo bars are generated-app content, which is fine. | `project-dark.png`, `backend-dark.png` | Add dark screenshots for the Create UI dialog and the Builder (H1). |

---

## Backend fixes required before/with the UI build

**Before slice 1 (security and correctness of core paths):**
1. D1: reject non-literal decorator `auth`/`name`/path with `422 invalid_decorator`.
2. D2: scope `/api/variables/{id}` to team and exclude project secrets.
3. D4: route every outward string (bodies, headers, error messages, traces, SSE) through the redactor, with a test where the upstream echoes the secret.
4. D7: honour `dry_run` on `POST …/modules` (via `_plan()`) and reject unsupported `dry_run`.
5. C1: CORS `expose_headers` for `X-Next-Cursor`, `X-Execution-Id`, `ETag`.

**With the backend slice (3–4):**

6. B1: `analysis.tables` with inferred columns, `table_not_found` error code, and the create-table-on-save path.
7. B4: optimistic concurrency on module PUT (`base_updated_at`/hash → `409 module_changed`).
8. C7: `route_conflict` payload `{existing_route_id, handler, line}`, plus documented `name_taken`/`invalid_decorator`/null column.
9. C8: module save preserves route-level description and schema.
10. C9: one dry-run shape for route and module.
11. D9: `DELETE …/modules/{M}?dry_run=true`.
12. D16: structured `error {type,message,traceback,line,module}` (redacted) from run-draft and `/test`.
13. C14: a single source for the SDK reference (`Response(status, body, headers)`).
14. G-UI-4: inline `trace` in `/test`. G-UI-10: `handler.line`. Rescoped G-UI-5 (C16).
15. §8.1 generate and §8.2 run-draft endpoints with E1–E5 precision (framing, codes, limits, cancellation, secret scrub).
16. D5: the `read_only` broker claim, enforced in dispatch (or drop self-test).
17. E6–E10: fake provider restricted to `WORKFLOW_LLM_PROVIDER` from os.environ only, refuses non-test DB and non-loopback, `/api/_test/*` token-guarded, retryable raises, `nth`, strict default, adapter contract tests.

**With the runs, builder and publish slices (5–7):**

18. C2: `trigger.type` taxonomy and the correct mode for workflow runs.
19. C3: redacted, size-capped request/response stored in run metadata (or remove from the design).
20. D6: retention on all insert paths, `broker_calls` cascade, and a correct cursor for pruned ids.
21. C5: app-builder SSE always emits `error` + `done`.
22. C6: per-call stub creation, tool offered every turn, client regenerated in-turn.
23. C11: api-usage detects `api.<op>(` and adds a `stub` status.
24. C4: publish `409 slug_taken`; `access` explicit.
25. D3: app tokens bound to the app (not the version), plus a refresh endpoint.
26. C13: `expires_at` in UTC.
27. B7: slug rename confirm + bulk republish.
28. D8: PythonStep loads release-snapshot modules.
29. D10: documented release codes, blocked-release visibility, diff filtering, unversioned apps.
30. D11: auth loosening classified as breaking.
31. Minor: D12, D13, D14, D15, D17, C18, C19.

---

## Verdict

**Needs revision.**

The page structure, visual direction and endpoint selection are sound and meet the owner's brief. Before
the overnight build starts, five things must change:
- Fix the seven blockers: B1, D1, D2, D3, D4, D5 and E6.
- Pin the generate, run-draft and fake-LLM contracts to the precision in §E.
- Correct the E2E harness: DB name, clean env, the network and console policy, and the wrong assertions.
- Add mockups for the Secrets, Runs, Release, Publish, streaming and error states.
- Re-number the build order so the backend security fixes land first.

---

# Re-review (v2)

Reviewed 2026-10-15 against `UI-DESIGN.md` v2 (1216 lines), all 24 PNGs in `screenshots/ui-design/`, and spot checks of the engine (`schemas/app.py`, `services/app_service.py`, `services/data_table_service.py:23`, `services/vars_substitution.py:71`). I did every check myself. I recomputed the contrast numbers in §7.3 from the token values and they hold: light method badges are 4.60–5.17, comment 4.20, gutter 3.52. Engine defects that the backend agent is fixing are checked only for whether §9 states the corrected behaviour.

**Status of the 81 findings: 73 verified, 8 partially resolved, 0 not resolved.**
**New findings: 0 blockers, 4 major, 8 minor.**

## Per-finding status

| # | Status | Evidence / remaining gap |
|---|---|---|
| A1 | verified | §2.1 |
| A2 | verified | §5.1 step 2, §5.2 route picker, T22, `builder.png`/`builder-1024.png`. See new N4: the stored field can be overwritten by other saves. |
| A3 | verified | §5.3 "Published runtime", §9 D3 |
| A4 | verified | §4.5 rule text, §4.6 single source |
| B1 | verified | §4.1 tables row, §4.2 steps 5–6, §10.1 `emit_module.tables` + `analysis.tables`, §10.2 virtual tables, T3/T6/T7, `backend.png`, `states.png` |
| B2 | verified | §6 table (6.1–6.7), T28/T33/T36 |
| B3 | verified | D11; copy in §2.4, §3.4, §4.7, §5.1, §5.3; visible in `backend.png`, `data.png`, `create-ui-dark.png`, `publish.png` |
| B4 | verified | §4.2 steps 6.4 and 7, §9 B4 (`base_sha` → `409 module_changed`), `dialogs.png` (right), T17 |
| B5 | verified | §4.2 step 3 (one transaction, `userEvent:"ai.draft"`, no remount), §7.1 CodeEditor row (Compartments, new props) |
| B6 | verified | §8.1 state table with test ids; `states.png`, `dialogs.png` |
| B7 | verified | §3.7 General, `settings.png`, T36a |
| B8 | verified | §3.3 (gated, with the reason), `project-first-run.png`, T43 |
| B9 | verified | §2.2 (`~new`, reserved names), `dialogs.png` |
| B10 | **partial** | The spec is correct (D12, §3.1, §3.2, §3.5). The mockups still disagree. `backend.png`/`backend-dark.png` show the outline "Secrets 2" with no "1 missing" while the widget says PAYMENTS_TOKEN is missing in prod. `home.png` shows Payments Ops as "Healthy" with no missing-secret footer, while Support Inbox shows "Degraded · 1 missing secret" for the same condition. The health rule (when a missing referenced secret makes a project Degraded) is never defined. **Fix**: define `summary.health` in §3.2 and align those three PNGs. |
| C1 | verified | §9 C1, §3.6, T37 |
| C2 | **partial** | §9 C2 taxonomy, §3.6 filter, T8/T40. But `runs.png`/`runs-dark.png` label two rows with trigger type `api`, which is not in the taxonomy. **Fix**: relabel to `app`/`api_key`/`public`. |
| C3 | verified | §3.6 run sheet, §9 C3, `runs.png` |
| C4 | verified | §5.3 steps 1 and 3, §9 C4, `publish.png`, T26 |
| C5 | verified | §5.2 SSE row, §9 C5, T24 |
| C6 | verified | §5.2 Chat bullet, §9 C6, `builder.png` (no first-turn stub) |
| C7 | verified | §4.4 table (full payloads), §9 C7, T13 |
| C8 | verified | §9 C8, T34 |
| C9 | verified | §9 C9 (exact shape), §4.2 step 6.2, §4.3 |
| C10 | verified | §4.7, §9 C10, T9 |
| C11 | verified | §5.2 Operations, §9 C11, T21 |
| C12 | verified | §5.2 "Preview base URL", §11.4, T23 |
| C13 | verified | §5.2 Preview token row, §9 C13, T23 |
| C14 | verified | §4.6 (`/api/sdk/reference`, correct `Response` signature), §10.1 context step 1 |
| C15 | verified | §4.5 rule text, §10.1 context step 1 |
| C16 | verified | §4.6, §9 C14/C16 |
| C17 | verified | §3.1 data sources |
| C18 | verified | §5.1 step 1, T20 |
| C19 | verified | §9 C19 |
| D1 | verified | §9 D1, §4.4 Invalid-decorator row, T13, slice B0 |
| D2 | verified | §9 D2, §3.5, T36b, slice B0 |
| D3 | **partial** | §9 D3 states app-bound tokens and `POST /a/{slug}/_token`. It does not say what authorises the refresh for **password** and **private** apps. See new N3. |
| D4 | verified | §9 D4 (enumerates every outward channel), T38, T2/T5 value-absence checks |
| D5 | verified | §9 D5 (exact deny list, error shape, `virtual_tables`), §10.1 step 4 |
| D6 | verified | §9 D6, §3.6 pruned copy, T37 |
| D7 | verified | §9 D7 (`422 dry_run_unsupported`), §4.3, T11 |
| D8 | verified | §9 D8 |
| D9 | verified | §9 D9, §6.1, `dialogs.png`, T33 |
| D10 | **partial** | §3.8 documents every code and the rule "Roll back is offered on release environments only". But `release.png` offers "Roll back to" on the **dev** (draft) history row. **Fix**: remove it from the dev row in the mockup. |
| D11 | verified | §9 D11 (`auth_loosened`, breaking), `dialogs.png`, T32 |
| D12 | verified | §9 D12–D17 row, §3.7 Environments |
| D13 | verified | §9 D12–D17 row |
| D14 | verified | §9 D12–D17 row |
| D15 | verified | §9 D12–D17 row |
| D16 | verified | §4.4 Runtime-error row, §9 D16, §10.2 `error`, `runs.png`, `states.png`, T18 |
| D17 | verified | §9 D12–D17 row |
| E1 | verified | §10.1 SSE framing (one terminal event + `done`, `seq`), JSON-mode codes, `llm_unavailable` only pre-stream |
| E2 | verified | §10.1 Limits + cancellation (step 5) + engine tests. Minor residue in N7. |
| E3 | verified | §10.1 analysis `conflicts{kind:"module_name"}`, repair line, §4.1 name chip |
| E4 | verified | §10.1 "Secret-value scrub" (uses the existing `expand_secret_variants`, `vars_substitution.py:71`), T4 |
| E5 | **partial** | §10.2 covers route resolution, other modules, environment, virtual tables, limits, the error shape and the trigger. Still ambiguous: the timeout says `status:504` next to "always 200", and it is not stated that route `auth` is **not enforced** for drafts (incoming `authorization` is stripped). See N6. |
| E6 | verified | §10.3 activation (6 conditions, `os.environ` only, dotenv off), refusal tests, token + loopback on `/api/_test/*`. Minor residue in N7. |
| E7 | verified | §10.3 Tool normalisation + adapter contract tests |
| E8 | verified | §10.3 retry testing; the T15 call log asserts `flaky, flaky, flaky-ok`, which matches `_MAX_RETRIES=3` (`llm_provider.py:770`) |
| E9 | **partial** | The repair rule and `@file:` interpolation are pinned. But the rule set is not deterministic across scenarios (N1, N2). |
| E10 | verified | §10.3 default `unmatched`, §12.1 step 7 |
| F1 | verified | §12.1 step 1 |
| F2 | verified | §12.1 step 3 (allowlisted env, tmp cwd, no provider keys), guard step 5 |
| F3 | verified | §12.3 network/console/layout policy. `backend-1024.png` now shows Send full-width and unclipped. |
| F4 | verified | T3 (2/2), T8 (200), T12, T40 (`manual`), T44 (`--background`), T20 (focus) |
| F5 | verified | T17, T27, T28, T33, T35, T36, T38, T41, T42 |
| F6 | verified | §1.1 lists all sites + a grep audit |
| F7 | verified | §12.1 step 6 |
| F8 | verified | §12.2 seed helpers |
| G1 | verified | Header + §13 slice 0 |
| G2 | verified | §7.1 |
| G3 | verified | §6.6 per-project env store key; §5.2 strip |
| G4 | verified | Appendix A "Shared" line |
| H1 | **partial** | All 14 new PNGs exist and cover the requested screens. But `workflows.png` hides the Workflows list under the ⌘K palette: one row is visible, and the Last run, Delete-blocked and empty states are not shown. See N12. |
| H2 | verified | §4.1, §4.7, §8.3; `backend-1280.png`, `backend-1024.png` (Send full width, JSON in its box, trace truncated with an ellipsis, Diff in ⋯) |
| H3 | verified | §7.2/§7.3. Recomputed: light 4.60/4.60/4.66/4.75/5.17, dark ≥ 5.4, keyword 5.68, comment 4.20, gutter 3.52, warning text 5.06, AI text 4.86. |
| H4 | verified | §1.3, §7.2. Keywords are magenta in `backend.png`, and PATCH is teal. |
| H5 | verified | §3.1, `backend-1024.png` (view icons only) |
| H6 | **partial** | Most is fixed: one dataset, `331 ms`, "· 24h", ✓ only after an observed 2xx (`builder.png`: createOrder "not called yet" with a grey dot). Remaining: B10's inconsistencies; `data.png` row ids `row_9c21` while row ids are integers (`db/models.py` `DataTableRowModel.id` BigInteger); a user column `created_at datetime` in the `backend.png` tables row and `data.png`, while the fixture declares only reason/amount and every row already has `created_at` metadata; and `backend.png` "Use as schema" enabled for a draft route, against §4.7 "disabled unless … the route is saved". |
| H7 | verified | §3.2 item 3, `project.png` |
| H8 | verified | `home.png` (Default lists workflows; ⋯ on every card) |
| H9 | verified | `create-ui-dark.png`, `builder-dark.png`, `secrets-dark.png`, `runs-dark.png` |

## New findings introduced or exposed by v2

| # | Sev | Finding | Evidence | Required change |
|---|---|---|---|---|
| N1 | major | **The fake LLM's matching depends on a message layout that is never pinned.** Rules match on `last_user_matches`, but §10.1 "Context" never says which message carries the prompt and which carries `current_source`. If the source is in the last user message, any Refine of `refunds.py` matches `(?i)refund` from the code itself. T4's Refine prompt is not specified at all, so its rule is undetermined. Every refine scenario (T4, T10, T14) depends on this. | §10.1 Context items 1–4; §10.3 pinned script; T4 | Pin the layout in §10.1: `system` = reference + catalogue + project facts; then, if there is source, a `user` message beginning `Current module <name>.py:` with the fenced source; then the conversation; then the **last** `user` message = the prompt verbatim. Give T4 a literal prompt and a matching rule (e.g. `__scrub__`). Add an engine test asserting the layout. |
| N2 | major | **The script's rules can cross-contaminate.** The `repair` rule matches **any** repair message and always returns `broken_fixed.py` (module "broken"). `gen-secret-missing` and `gen-broken` declare no `tables`. If a fixture uses a table that the seeded project lacks, analysis raises `missing_table` → `repair` fires → T10 receives the wrong module. In T14, if `broken_fixed.py` uses an undeclared table, the loop runs to attempt 3 and ends `generation_invalid`, not `attempts:2`. The outcome depends on fixture contents and seeds the spec doesn't give. | §10.3 rules; §10.1 loop step 3; T10, T14 | Scope `repair` with `any_user_matches: "(?i)\\bbroken\\b"`. Give every `emit_module` fixture explicit `tables` (or state that it uses none). State each fixture's table use and each scenario's seed (T10 seeds `refunds`). Assert `attempts` and the full `rule_id` sequence in T10 too. |
| N3 | major | **`POST /a/{slug}/_token` has no authorisation rule.** It mints app tokens. For `password` apps it must require the unlock cookie (`/a/{slug}/_unlock`, `public_apps.py:329`). For `private` or unpublished apps it must 404. Rate limiting is unspecified. As written, anyone could mint a token for a password-protected app and call its routes. | §5.3 Published runtime; §9 D3 | Add to §9 D3: public → issue; password → require a valid unlock cookie, else `401 unlock_required`; private/unpublished → 404; per-IP rate limit; same TTL/rotation rules. Add an E2E: a password app's `_token` without the cookie → 401. |
| N4 | major | **`definition.studio.route_ids` can be silently overwritten.** `PUT /api/apps/{A}` replaces `draft_definition` wholesale whenever `definition` is sent (`services/app_service.py:173-174`). The builder's manual save and AI auto-save send the client's `definition`, so unless every writer carries `studio`, the route selection is lost. T22's "survives reload" doesn't cover a save in between. | §5.1 step 2, §5.2 route picker; `engine:services/app_service.py:173-174` | Either the engine merges `definition.studio` when the incoming definition omits it, or the studio's single `saveApp()` always merges the current `studio` block. T22: change routes → AI turn + manual save → reload → the selection persists. |
| N5 | minor | **"Cancel it" has no backend action.** `generation_in_progress` offers `Wait`/`Cancel it`, but only a client disconnect cancels, so another tab's generation can't be cancelled. | §4.4 Generation errors; §10.1 409 row | Drop "Cancel it", or add `DELETE …/modules/generate/{generation_id}` (owner-only) and return `generation_id` in the 409. |
| N6 | minor | **run-draft details are ambiguous.** The timeout says "`status:504`" next to "always 200 once the handler ran" (inner or HTTP?). Route `auth` is not enforced for drafts (incoming `authorization` is stripped), and this is never stated. | §10.2 Timeout, Environment | State: on timeout, HTTP 200 with inner `status:504` and `error.code:"timeout"`. Drafts bypass route auth; the Test panel notes it for `app_token`/`api_key` routes. |
| N7 | minor | **Some test gates are weaker than they look.** `WORKFLOW_BIND_HOST` is self-reported (uvicorn's `--host` is separate), so condition 4 is advisory; the loopback middleware is the real guard. `WORKFLOW_TEST_MODE=1` alone enables `token_ttl_s` without the DB-name guard. The generation lock keys on the client-supplied `X-User-Id`. | §10.3 activation 2/4; §12.1; §10.1 409 row | Apply the `workflows_test_` DB guard to `WORKFLOW_TEST_MODE` too. Document that the loopback middleware is the enforcement. Note the lock is best-effort per client. |
| N8 | minor | **T27 will fail the network policy.** The expected `401 token_expired` from the published app is not declared, and §12.3 fails any undeclared 4xx. | T27; §12.3 | Declare `expectStatus({url:/\/api\/p\//, status:401})` in T27 and assert exactly one 401 followed by the refresh. |
| N9 | minor | **Wrong cross-reference.** §3.4 says the filter bar "builds the §3.7 filter grammar"; §3.7 is Settings. | §3.4 | Inline the grammar (`$eq $ne $gt $gte $lt $lte $in $contains $exists`, `$and/$or`, `data_table_service.py:3-4`). |
| N10 | minor | **The delete-module copy is wrong when prod serves a release.** "These routes stop working immediately (prod too, until you release)" is false if prod serves a pinned release. | `dialogs.png` centre; §6.1 | Make it conditional: "…in dev and in prod (prod serves your latest saved code)" vs "…in dev; prod keeps serving release #N until you promote". |
| N11 | minor | **Leftover mockup data errors** (listed under B10, C2, D10, H6): trigger `api`, dev rollback, `row_` ids, the `created_at` user column, "Use as schema" on a draft, Secrets "2", the health badge. | screenshots above | Fix in one pass of `shell.js` and the affected HTML. |
| N12 | minor | **The Workflows list is barely mocked.** `workflows.png` puts ⌘K over it; the list's columns, the Delete-blocked state (`409 workflow_in_use`) and the empty state aren't visible. | `workflows.png`, §6.6 | Add a separate `workflows.png` without the palette (populated + empty) and keep ⌘K as its own PNG. |

## Verdict (v2)

**Needs revision (narrow).** Every v1 blocker is resolved in the spec, and the contracts in §9/§10 are precise enough to implement and test. The rest is a short spec pass:
- Fix the four new majors, all small: pin the LLM message layout and deterministic script rules (N1/N2), add authorisation for the token-refresh endpoint (N3), and preserve `definition.studio` (N4).
- Correct the leftover mockup inconsistencies (B10, C2, D10, H6, N11).

Slices B0, 0, 1 and 2 (§13) don't depend on N1–N4 and can start now. Slices 3 and later should wait for the N1–N4 edits.

---

# Re-review (v2.1)

Scope: only the 8 v2 partials and N1–N12, checked against the `UI-DESIGN.md` v2.1 text (§3.1, §3.2, §3.4, §4.4, §6.1, §9, §10.1–10.3, §12.4, §14.1–14.2) and the re-rendered PNGs. I also checked two points in the engine: the builder puts the "## Project API" text in the **system** message (`app_builder_ai_service.py:862-868`), so `builder-write`'s `system_matches` works; and the unlock cookie is path-scoped to `/a/{slug}` (`public_apps.py:348-352`), so `_token` receives it.

| # | Status | Evidence |
|---|---|---|
| B10 | verified | §3.2 defines `summary.health` (failing > 50%, degraded > 5% or a referenced secret missing in live, ok, unknown), and the dot, card and badge share that one value. §3.1 outline count. `home.png`/`project.png`/`release.png` show "Degraded · secret missing in prod". `backend.png` shows "Secrets · 1 missing". |
| C2 | verified | `runs.png` router rows are `app`; no `api` trigger type appears anywhere. |
| D3 | verified | §9 D3: public → issue; password → unlock cookie else `401 unlock_required`; private/unpublished → 404; 30/min → 429. T27b. See residual R2. |
| D10 | verified | `release.png` dev rows read "snapshot" only; there is no rollback on a draft environment. |
| E5 | verified | §10.2 Timeout (HTTP 200, inner 504, `error.code:"timeout"`; the only non-200s are pre-handler) and Auth (not enforced; Test-panel note). |
| E9 | verified | §10.1 pinned layout + §10.3 markers, scoped `repair`, `tables` in every response, fixture/seed tables; T10 `[gen-secret-missing]`, T14 `[gen-broken, repair]`. See residual R1. |
| H1 | verified | `workflows.png` shows the populated list (Trigger, Handles route, Last run, Status), the ⋯ menu with Delete disabled ("used by POST /orders/legacy"), and the empty state. |
| H6 | verified | `data.png` integer ids (101–103) and "created · row metadata"; the `backend.png` tables row has reason/amount only; "Use as schema" is disabled on the draft. |
| N1 | verified | §10.1 "Message layout (pinned)": `system` → `Current module <name>.py:` + `OK.` → conversation → prompt verbatim last; repair turns end with the fixed line; an engine test asserts it. T4 has a literal prompt and a `gen-scrub` rule. |
| N2 | verified | `repair` needs `any_user_matches: "__broken__"` **and** the repair line, and comes before `gen-broken`. Every `emit_module` response declares `tables`. `broken_fixed.py`/`hello.py` use no tables. Unmatched repairs hit `default: unmatched`, which fails the run loudly. Deterministic. See residual R1. |
| N3 | verified | As D3. See residual R2. |
| N4 | verified | §9 N4 (the engine keeps `draft_definition.studio` when it is omitted; `studio:null` clears it) + one merging `saveApp()` (§5.1). T22 covers auto-save + manual save + reload. |
| N5 | verified | §4.4: Retry only, "no remote cancel". |
| N6 | verified | §10.2 Timeout + Auth bullets. |
| N7 | verified | §10.3 items 2–4: test mode refused without a `workflows_test_` DB (this also gates `token_ttl_s`); bind host is advisory and the loopback middleware enforces; the lock is best-effort (§10.1 409 row). |
| N8 | verified | T27 declares the `/api/p/` 401 and asserts exactly one, then `_token` 200, then the retry 200. |
| N9 | verified | §3.4 inlines the engine grammar. |
| N10 | **partial (mockup only)** | The §6.1 text is correct and conditional. `dialogs.png` (centre) prints **both** variants in one paragraph, quoting the alternative as an annotation. **Fix**: render the no-release variant only. |
| N11 | **partial (mockup only)** | The data errors are fixed. `command-palette.png` still has the **old** Workflows list behind the palette: 1 row and the leftover "⋯ menu: Open · Duplicate…" annotation, while the tab says "Workflows 2". **Fix**: re-render its background from `workflows.html`. |
| N12 | verified | `workflows.png` and `command-palette.png` are separate. |

**Residuals (minor; fix during the build, they don't block implementation):**
- **R1. Seeded secrets need hosts.** The T3/T10 seeds (§10.3) give `PAYMENTS_TOKEN (dev)` but not `allowed_hosts`. Without `["127.0.0.1"]` the self-test returns `secret_not_bound` and T3's "self-test 2/2" fails. State `hosts:["127.0.0.1"]` in those seeds.
- **R2. Pin the token endpoint's cross-origin behaviour.** The unlock cookie relies on Starlette's default `SameSite=Lax`, and the engine's CORS is `*` with credentials. Pin `samesite="lax"` (or strict) on the cookie in §9 D3. State that `/a/{slug}/_token` sends no CORS credential headers, so another site can't mint a token for a password app the visitor has unlocked. Add the assertion to T27b.

**Status:** 18 verified, 2 partial (mockup-only), 0 not resolved. No new blocker or major findings.

## Verdict (v2.1)

**Ready to implement.** The two remaining partials are rendering fixes. R1 and R2 are one-line spec additions to make during slices B4/3. None of them changes a contract or the build order.
