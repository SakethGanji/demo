# Porting guide: the one-shot app builder

2026-10-22. This is guidance for the agent doing the work. It ports the prototype in `experiments/oneshot-builder/` into the engine and the studio.

It **replaces the app-builder parts of `AI-BUILDERS-PLAN.md`**: "How apps get data" and the "App builder track" under "Order". Everything else in that plan still holds:
- the two builders stay fully separate;
- the "What this retires" list;
- the gates;
- the out-of-scope list.

**Order of the three guides:** `CUT-GUIDE.md` first, then this guide, then
`handoff/workflow-builder/WORKFLOW-BUILDER-GUIDE.md` (the workflow chat) last.

**The owner's rule: simple and fast.** When something fails, fix the prompt or the platform. Don't add a stage, a planner or a question round.

> **These guides were written on a different branch.** Your code may differ: file names, line numbers, sizes and test
> counts are pointers, not exact targets. Find the same thing in your copy by name and adapt. The prompts and the ideas
> stay the same.

**Before you start:**
- **Grep for importers before deleting anything.** File and symbol names are reliable; line numbers are hints.
- **You need `experiments/oneshot-builder/`** and `AI-BUILDERS-PLAN.md`. If either is missing from your copy, stop and
  ask the owner. Nothing here works without the prototype. It is code and prompts only (about 2k lines), plus:
  - `fixture/vendor-payments/`: one known-good app, for testing the runtime with no model call (Phase 1);
  - `ui/example-journey.json`: part of the journey prompt (`{{EXAMPLE}}`).
  Generated apps are not kept; Phase 0 builds them.
- **The exact prompts are the files:** `PROMPT.md` (backend), `ui/UI_PROMPT.md` + `ui/example-App.tsx` (page),
  `ui/JOURNEY_PROMPT.md` + `ui/example-journey.json` (test). The edit and fix-round wording is in `build.py`,
  `ui_build.py` and `journey_build.py`. Copy all of it verbatim.
- **This guide is only about apps.** The workflow chat is the third guide.

## Contents

1. [What was proven](#1-what-was-proven)
2. [The design in one page](#2-the-design-in-one-page)
3. [Lessons: don't undo these](#3-lessons-dont-undo-these)
4. [Phase 0: reproduce the numbers on this machine](#4-phase-0-reproduce-the-numbers-on-this-machine)
5. [Phase 1: the app runtime in the engine (no AI yet)](#5-phase-1-the-app-runtime-in-the-engine-no-ai-yet)
6. [Phase 2: the build in the engine](#6-phase-2-the-build-in-the-engine)
7. [Phase 3: the studio](#7-phase-3-the-studio)
8. [Phase 4: switch over and delete](#8-phase-4-switch-over-and-delete)
9. [Open decisions for the owner](#9-open-decisions-for-the-owner)
10. [Known gaps (fine for now)](#10-known-gaps-fine-for-now)

---

## 1. What was proven

Everything below ran for real:
- a real model;
- real HTTP with the same headers;
- real Chromium;
- data checked through the API after every change.

`experiments/oneshot-builder/README.md` has the details.

| Stage | One model call writes | Checked by | First-call pass | Time |
|---|---|---|---|---|
| Backend | `schema.json` + `actions.py` (with `test_*` functions) | tables build, saved queries run, every test passes on fresh data | 16/16 (8 single-table, 4 multi-table, 4 edits) | under 1 min |
| Page | `App.tsx`, one React page on a fixed kit | type-check, bundle, render in Chromium as every role, click every tab, fail on any console or API error | 4/4 multi-table apps | ~30s |
| Test | `journey.json`, a click-through for a browser robot | the robot runs it on fresh data and checks stored values over the API | 5/5 (after two prompt fixes) | ~30s, plus ~30s to run |

How much the journeys covered:
- Every action in every app ran successfully from the page: 6/6, 5/5, 6/6, 6/6, 9/9.
- Server errors: 0.
- Direct table editing (add, edit, delete, delete refused while rows still point at it, buttons hidden from other roles): 21/21.

Edits use the same three calls. The two edits tested passed their backend and page calls first time. Each old journey also reran as a regression check:
- one still passed;
- the other stopped where the change required it to.

A build or an edit takes 2–3 minutes and 3 model calls. The role-split pipeline took about 50 calls and 27 minutes.

## 2. The design in one page

**What the model writes, per app:**
- `schema.json`:
  - tables, with columns and types, sample rows, `edit_roles` and `locked` columns;
  - named saved queries in SQL.
- `actions.py`: `@action(roles=[...])` functions, which hold the business rules, plus `test_*` functions.
- `App.tsx`: imports only from `react` and `./kit`.
- `journey.json`: the click-through test.

**What the platform owns:**
- validation, casting and defaults;
- roles: on actions, and on direct edits through `edit_roles`;
- locked columns, which only actions can change;
- the change history (who, when, what);
- refs, including refusing to delete a row that others point to;
- add-only migrations;
- one transaction per request;
- the outbox (emails);
- generic CRUD and the HTTP API;
- the UI kit:
  - hooks;
  - `ActionButton` (it builds its own form, hides itself from roles that can't run it, shows errors and refreshes everything);
  - `RecordTable`, `DataTable`, `Stats`, `Tabs`, `Modal`, `History`;
- the checker and the robot.

**The build loop for each stage:** one call → deterministic parse and clean-up → run the checks → at most 2 fix calls. The journey stage is different: no fix loop, but one judge call. It must answer "Test wrong: …" (and send a corrected journey) or "App bug: …" (stop for a person).

**Where the files map:**

| Prototype | Becomes | Notes |
|---|---|---|
| `kit.py` (DB, `load_actions`, `T`, `check`) | `services/app_runtime/` (engine) | the `DB` class is rebuilt over data tables (Phase 1); `check`, `T` and `load_actions` port almost as-is |
| `server.py` | `routes/app_runtime.py` | the same endpoints, under one prefix per app |
| `build.py`, `ui_build.py`, `journey_build.py`, `edit_e2e.py` | `services/app_ai_build.py` | the 3 stages and the edit path; `call_model` becomes `call_llm` |
| `PROMPT.md`, `ui/UI_PROMPT.md`, `ui/JOURNEY_PROMPT.md`, `ui/example-App.tsx` | prompt files next to `app_ai_build.py` | copy verbatim; every line was earned by a failure (section 3) |
| `ui/kit.tsx`, `ui/main.tsx` | the studio's virtual `./kit` module for generated apps | its `api()` changes to go through the iframe bridge (Phase 3) |
| `ui/check.mjs`, `ui/journey.mjs`, `ui/package.json` | a small node tool the engine shells out to | the same way `app_bundler.py` already shells out to esbuild |
| `eval.py`, `eval2.py`, `edit_e2e.py` | the eval set | rerun after every prompt or kit change; outputs go to `out/`, `out2/`, `apps/` (git-ignored) |
| `fixture/vendor-payments/`, `api_test.py` | the Phase 1 gate | a fixed app, so the runtime is tested without a model |

## 3. Lessons: don't undo these

Each of these was a first-call failure, fixed in the contract rather than with a new stage. Removing one brings its failure back.

- **Queries get only `:me` and `:today`.** People are always identified by **email**, lowercased.
- **Money can be negative.** Booleans are real booleans, not 0/1.
- **`/api/app` lists each saved query's real columns.** Without that, pages show columns a query never returns.
- **Business-rule columns are `locked`.** Only actions change them; direct edits get a 400.
- **`ActionButton` opens a form only when a required input is missing.** The journey prompt must say so.
- **Reference inputs are recognised even when qualified.** Inputs named like `new_branch_id` or `from_account_id`, and plural tables (`branches`, `companies`), get a dropdown, not a number box.
- **Journeys find new rows by a value they typed, never by a guessed id.** They expect only what `actions.py` does: read the defaults, such as "lead defaults to the creator".
- **The robot:**
  - an exact tab label wins over a prefix ("Waivers" is not "Waivers to decide");
  - `close` is a no-op when no dialog is open;
  - every wait times out after 5s.
- **`T.run(name, /, ...)` is positional-only,** because actions have inputs called `name`.

## 4. Phase 0: reproduce the numbers on this machine

Don't touch app code yet.

1. Install and run:
   ```
   cd experiments/oneshot-builder/ui && npm ci && npx playwright install chromium
   cd ..
   ```
   - The UI versions are pinned to the studio's: React 18.3.1, esbuild 0.25.12, playwright 1.62.1, TypeScript 5.9.3.
   - The checker and robot need Chromium. Skip `playwright install` if a Chromium is already set up for
     Playwright on the machine.
   - The Python side needs Python 3.11+ and `uv`, since the backend server runs with
     `uv run --no-project --with fastapi --with uvicorn`. Without `uv`, `pip install fastapi uvicorn` in a venv and run
     `server.py` with that venv.
   - `node_modules` is not git-ignored at the repo root. Don't commit `experiments/oneshot-builder/ui/node_modules`.
2. The model:
   - `build.call_model` uses the `claude` CLI if there is no key. The CLI must be installed and logged in for the user
     running the scripts. Check with `claude -p "say ok"`.
   - With `ANTHROPIC_API_KEY` and `LLM_MODEL` set, it calls the API.
   - **Use the model the engine will use in production.** The numbers in section 1 came from a Sonnet-class model.
   - This costs real model calls: about 3 calls per app, roughly 40–60 calls for the whole Phase 0 set. Tell the
     owner before running it.
3. Run:
   - `python api_test.py` (no model; the fixture must pass 31/31 before anything else);
   - `python eval.py` (8 single-table backends, into `out/`), then `python eval2.py` (4 multi-table backends into
     `out2/`, plus 4 edits that start from `eval.py`'s apps in `out/`);
   - `python ui_build.py out2/<app> "<request>"` then `python journey_build.py out2/<app> "<request>"` for the 4
     multi-table apps. Requests are in `eval2.MULTI`;
   - `python edit_e2e.py` (builds `apps/vendor-payments` and `apps/audit-program` first if missing, then edits them).

   The backend server runs with `uv run --no-project --with fastapi --with uvicorn`.
4. **Gate:** first-call pass rates within one case of section 1. If they're worse, fix the prompts here before porting anything.

## 5. Phase 1: the app runtime in the engine (no AI yet)

The riskiest part of the port. Prove it with the fixture apps before any model is involved.

**Data lives in the project's data tables,** so app data shows up in the Data area (data is the centre of the product). The service is `services/data_table_service.py`; rows are JSONB in `data_table_rows`.

Column type mapping:

| Kit type | Data-table type | Runtime does |
|---|---|---|
| `text` | `string` | |
| `email`, `person` | `string` | lowercase |
| `choice` | `string` + enum | check the value |
| `number`, `money` | `number` | `money` may be negative |
| `integer` | `integer` | |
| `boolean` | `boolean` | |
| `date` | `string` `YYYY-MM-DD` | sorts and compares correctly as text |
| `datetime` | `datetime` | |
| `ref` | `integer` + `ref: "<table>.id"` | check the row exists; refuse delete while referenced |

What `DataTableService` accepts today (`services/data_table_service.py`, near the top):
- `ref` must look like `table.column` (`REF_RE`), so write `"<table>.id"`, not just the table name;
- `required` and `default` are core column keys; they're already supported;
- `META_KEYS` is `unique`, `index`, `deprecated`, `enum`, `ref` and `description`. **There's no `locked`**;
- the table itself has no `edit_roles`;
- migrations allow only the `ADDITIVE_OPS` list.

So:
- add `locked` to the column flags (`FLAG_KEYS`);
- add a table-level `edit_roles`: find where the table row stores its settings (the `data_tables` model), and add it there
  with an additive migration if there's no JSONB settings field;
- add `set_locked` and `set_edit_roles` to `ADDITIVE_OPS` if edits need to change them.

Extend `DataTableService`; don't keep a side store. Grep for its tests (`tests/unit/test_*data_table*`) and add cases
for the new keys.

**The `DB` facade** (`services/app_runtime/db.py`):
- Same methods as `kit.DB`: `insert`, `get`, `update`, `delete`, `find`, `query`, `history`, `notify`, `now`, `today`, with the same casting and errors (`ActionError` → 400).
- **Actions are synchronous Python; the service is async.** Run the action with `asyncio.to_thread`. The facade's methods call into the async service with `asyncio.run_coroutine_threadsafe(..., loop).result()`.
- **One request = one session = one transaction.** Commit at the end; roll back on any error. The prototype's `api_test.py` checks this (an overpay refusal must save nothing).
- **History:** one row per insert, update or delete: table, row id, when, who, and changed fields as JSONB. Check whether the engine already records row history before adding a table.
- **Outbox:** one table, written by `notify`. Real email comes later.
- **Saved queries:** data tables have no SQL. Keep the contract unchanged:
  - load the rows of the app's tables into an in-memory SQLite with the app's schema (the code is already in `kit.DB`);
  - run the query read-only, with `:me` and `:today`.

  This is fine for internal apps up to tens of thousands of rows. Models write 3–5 queries per app and pages use them for reports, so don't drop them.
- **Migrations on edits:** use the existing `POST /{tid}/migrate`. It's additive-only, which matches the contract. Keep `existing_data_fits` from `kit.py` as the pre-check.

**Actions run in-process,** with the prototype's import allowlist: datetime, math, decimal, re, json, kit. The engine's
bwrap sandbox (`apps/workflow-engine/src/sandbox/`, not the studio's preview sandbox) can't reach the DB. Moving actions
into it is security work the owner has deferred.

**Identity:**
- There's no login. The preview's "view as" headers (`X-Preview-User-*`, see `services/route_dispatcher.py`) carry the email and the **app role**.
- App roles are app-specific (manager, teller), not team roles.
- Published apps need a way to give people roles; see section 9.

**Routes** (`routes/app_runtime.py`): the endpoints of `server.py`, under `/apps/{app_id}/rt`.
- Mount it on `api_router` in `routes/__init__.py`. That router has `prefix="/api"`, so the full path is
  `/api/apps/{app_id}/rt/...`.
- `routes/apps.py` already has `prefix="/apps"`, so make sure no existing `/apps/{app_id}/...` route captures `rt` first.
- Register it before `apps.py`, or use distinct paths.

The endpoints:
- `GET app`;
- `GET|POST tables/{t}`;
- `GET|PATCH|DELETE tables/{t}/{id}`;
- `GET queries/{name}`;
- `POST actions/{name}`;
- `GET outbox`;
- `GET /` and `GET app.js` for the built page, used by the checker.

Keep the response shapes exactly. The kit and the robot depend on them.

**Gate:**
1. Load `experiments/oneshot-builder/fixture/vendor-payments` (`schema.json`, `actions.py`, `App.tsx`) into a project with a small script.
2. Point `api_test.py` at the engine route (change the base URL and headers): it must pass.
3. Run `journey.json` and `crud.json` there with `ui/journey.mjs` against the engine: they must pass. Then do the same for the apps Phase 0 built.
4. Run the engine unit tests.

## 6. Phase 2: the build in the engine

`services/app_ai_build.py`, behind a flag, beside the old path:

```
build(request, project, current=None) -> Result
  backend = stage(BACKEND_PROMPT, request, current)  # schema + actions; checks = kit.check
  apply   = create or migrate tables, save files     # deterministic
  page    = stage(UI_PROMPT, ...)                    # App.tsx; checks = check.mjs
  journey = write_journey(...)                       # one call; robot runs it; one judge call on failure
```

Each `stage` is one call → parse → checks → at most 2 fix calls.

- **Model:** `call_llm` in `engine/llm_provider.py`, one user message, no tools. Reply parsing is the prototype's regexes, plus trailing-comma stripping and adding the kit import.
- **Files:** `schema.json`, `actions.py`, `App.tsx` and `journey.json` are `app_files` rows on the version (`AppFileModel`).
- **Edits:** the same three calls.
  1. The backend gets the current schema and actions and the change. It's add-only, and `existing_data_fits` runs before anything else.
  2. The page gets the current `App.tsx` and the change.
  3. The old journey runs first as a regression check. Then a new journey is written.
- **Chat:**
  - Keep `POST /ai/app-builder/chat` (SSE, ends with `done`) so `useAppBuilderChat.ts` keeps working.
  - Emit one event per stage start and end, the `Assumed:` / `Built:` / `Covers:` lines, and each stage's check list.
- **Checker and robot:** the engine shells out to `node check.mjs` and `node journey.mjs` (a small package with playwright and chromium) against the Phase 1 routes, on a fresh copy of the sample data.
- **Gate:** the eval set (`eval.py`, `eval2.py`, plus the UI and journey runs) through `app_ai_build.py` gives the same first-call pass rates as Phase 0.

## 7. Phase 3: the studio

All `sandbox/` paths here are the studio's: `apps/workflow-studio/src/features/app-builder/sandbox/`.

- **The kit is a fixed module.** Add the prototype's `ui/kit.tsx` as a virtual `./kit` file in `sandbox/esbuild-bundler.ts`.
  The model's files never include it.
  - The engine already has a different app kit (`services/app_kit/`) for the current builder. Leave it alone until Phase 4.
  - Don't merge the two kits.
- **Transport:**
  - The preview iframe is sandboxed, so change the kit's `api()` to send `apiRequest` over `sandbox/bridge.ts`. The parent
    already does the fetch (`IframeSandbox.tsx`).
  - `sandbox/requestPolicy.ts` (`previewRequestUrl`) today only lets requests under `/api/p/{slug}/` through, for
    both `preview` and `published` scope. Add a second allowed prefix, `/api/apps/{this app's id}/rt/`, for this app only.
    Pass the app id in where `slug` is passed.
  - Add vitest cases: this app's runtime is allowed; another app's runtime and other paths are refused.
- **The role switcher** sends the "view as" headers through the bridge.
  - It lives in the prototype's `AppShell` in `ui/kit.tsx` (around line 185), which comes into the studio with
    the kit.
  - There's no studio `AppShell`. The engine's `app_kit/resource.tsx` has an unrelated `AppShell`; don't confuse them.
  - `previewUserHeaders` and `withViewAs` in `requestPolicy.ts` already build the `X-Preview-User*` headers, so reuse them.
- **One screen:** chat on the left, live preview on the right, from the first message through every edit (the mockup: https://claude.ai/artifact/Lr3bfcDGd4fs7tGdm5NCoD).
  - The chat shows the three stages, each stage's checks, and on failure the error with a **Fix** button.
  - Optional tabs: Data (the app's tables), Actions (try one), Checks.
  - No plan page, no questions, no BuildStudio second screen.
- **Gate:**
  - `tsc -b` with 0 errors, vitest, lint, the design check and `npm run test:ui`;
  - plus one browser test that builds the vendor-payments request through the studio chat and runs its journey in the preview.

## 8. Phase 4: switch over and delete

1. Switch the default once Phase 2's evals hold and Phase 3's gate passes.
   - Today apps with `definition.studio.oneshot` go down the current one-shot path (`_is_oneshot_app` in
     `app_builder_ai_service.py`); others use the agent loop.
   - Keep that flag working until the switch. Then new apps use `app_ai_build.py`. Existing apps keep opening: their files
     are still `app_files` rows.
2. Delete the retired app-builder code from the "What this retires" list in `AI-BUILDERS-PLAN.md`, one area per change.
   Map importers first (`grep -rn "<module>" src tests`).
   - **Sizes:**
     - the engine app-builder code that goes is about 14k lines across about 20 files;
     - with the studio side (chat, plan, BuildStudio), about 22k;
     - the prototype that replaces it is about 2k.
     - Measure on your copy.
   - **Already gone if CUT-GUIDE was done:** `app_composer.py`, `app_plan.py` and `app_bundle.py` (CUT-GUIDE
     A2), and the role-split pipeline (A6).
   - **`build_coordinator.py`:** it's on the retire list, but the current one-shot app build runs inside it
     (`oneshot_builder.py`, `app_autotest.py`, `app_builder_ai_service.py`, `routes/builds.py` and `main.py` import it).
     Delete it only together with those, after `grep -rn build_coordinator src tests` shows no other user.
   - **Keep the chat endpoint** (`POST /ai/app-builder/chat`) and the studio hook that calls it. Grep for
     `ai/app-builder/chat` in `apps/workflow-studio/src` to find the hook (it was `useAppBuilderChat.ts`); it may have moved.
   - **If your copy has newer app-builder work** (a bigger `oneshot_builder.py`, `app_kit/board.tsx`,
     `display.tsx`, `layout.tsx`, `overlay.tsx`, `plots.tsx`, `theme.css`, or `app_bundler_tailwind.mjs`), it's the
     current builder's improvements. It retires with the rest at this step. Don't port it into the new path.
3. **Also delete `tailwindcss-v3`** from the studio's `package.json` (`npm uninstall tailwindcss-v3`), together with
   the old `app_bundler.py` CSS step that runs it. Do this once nothing else uses them: grep for `tailwindcss-v3` in
   `apps/workflow-engine/src` and `apps/workflow-studio`.
4. **Keep the eval set and rerun it after every prompt or kit change.** A change that lowers a first-call pass rate gets reverted.

## 9. Open decisions for the owner

- **Who has which app role in a published app.** The preview just switches roles. Suggestion: a small per-app list of email → role, edited in the app's settings.
- **Table names that already exist in the project.** Suggestion for v1: the backend check fails with "table exists", and the fix call renames. Using existing tables ("bring your own data") comes later.

## 10. Known gaps (fine for now)

- Actions run in-process, not sandboxed (security is deferred).
- Email goes to the outbox only.
- Two small kit flaws:
  - a long action description runs off the edge of its form dialog;
  - ref dropdowns still list rows the action will refuse (such as a blocked vendor).
- The journey judge call hasn't yet been triggered by a real failure.
