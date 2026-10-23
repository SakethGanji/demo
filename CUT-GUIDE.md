# Cut guide: unneeded features and dead code

Status, 2026-10-23: a guide only. Nothing has been removed yet.

**Revised 2026-10-23.** The first version was checked against GitHub (`revert-to-5684ba5` at `74e8575`), one agent per guide.
Every path in it existed, but several deletions had importers it didn't name, and one package it removed is still needed.
This version fixes those. Changes from the first version are marked **(fixed)**.

## Contents

- [Read this first: your copy is not exactly this one](#read-this-first-your-copy-is-not-exactly-this-one)
- [What this pass does](#what-this-pass-does)
- [Where this gets us](#where-this-gets-us)
- [Rules for doing the cuts](#rules-for-doing-the-cuts)
- [Order of the steps](#order-of-the-steps)
- [Part A: cut features](#part-a-cut-features)
  - [A2. Hidden second app builder](#a2-hidden-second-app-builder-the-ai-app-composer-30k-low-risk)
  - [B1a. Dead studio files that knowledge code uses](#b1a-dead-studio-files-that-import-knowledge-code-do-before-a1)
  - [A1. Knowledge](#a1-knowledge-10k-medium-risk)
  - [A4. Interface builder, public interfaces and AskBar](#a4-interface-builder-public-interfaces-and-askbar-25k-medium-risk)
  - [A3. Releases UI](#a3-releases-ui-15k-medium-risk)
  - [A5. API tester](#a5-the-app-builders-api-tester-24k-low-risk)
  - [A6. Legacy builder pipeline](#a6-legacy-app-builder-pipeline-10k-low-medium-risk)
- [Part B: remove dead code](#part-b-remove-dead-code-6k)
- [If your copy has the newer local work](#if-your-copy-has-the-newer-local-work)
- [Not cut in this pass](#not-cut-in-this-pass)
- [After Part A and Part B](#after-part-a-and-part-b)
- [Open decisions](#open-decisions)

## Read this first: your copy is not exactly this one

This guide was measured on GitHub `revert-to-5684ba5` at `74e8575` (the code is identical to `87989ea`; only docs differ).
The copy you're working on was downloaded separately and may differ slightly.

- **Line numbers are hints.** Find code by the file and symbol name (function, class, component, route path), then
  confirm the line.
- **Line counts are approximate.** Don't stop because a count is off by a few hundred.
- **Never trust "nothing else uses it".** Before every deletion, grep your copy for importers (the commands are in rule 4).
  This guide lists every importer found on `74e8575`. Your copy may have more; treat any extra importer as a reason to
  look, not to delete it too.
- **If a file named here doesn't exist** in your copy, search for the symbol. If it's gone entirely, skip that line and note
  it.
- **If your copy has files this guide doesn't mention** (for example `services/workflow_oneshot.py`), read
  [If your copy has the newer local work](#if-your-copy-has-the-newer-local-work) before starting.

## What this pass does

This pass cuts the features we don't need and removes dead code.

The **workflow editor, runner and clone** are not touched, apart from small import moves where a cut feature shares a helper
with them. Simplifying the two builders comes later; see `ONESHOT-BUILDER-PORTING-GUIDE.md` (app builder).

The evidence for each item is in `docs/cleanup/`, if your copy has it. Each report gives file:line, importer counts and a
confidence level: `engine-appbuilder.md`, `engine-platform.md`, `studio-builder.md`, `studio-editor-shared.md`,
`python-deadcode.md`, `ts-deadcode.md`. They were made on a different tree, so re-check everything on your copy.

## Where this gets us

| | Lines, not counting tests |
|---|---|
| GitHub `74e8575`: studio 100.4k + engine 102.1k | **~202k** |
| After Part A (feature cuts) | ~181k |
| After Part B (dead code) | **~175k** |

Part A is smaller than the first version said (~21k, not ~24k). Releases' engine service, environments and the
approval-screen parts of interfaces stay, because live code uses them **(fixed)**.

About 10–12k test lines also go with the removed code. They are not counted above.

## Rules for doing the cuts

1. **Keep a copy of the starting state.** Commit or copy the tree before starting, so each step can be compared and undone.
2. **One step per change,** in the [order below](#order-of-the-steps), so each step can be reverted on its own. Nothing is
   committed to the shared repo until the owner says "commit".
3. **Measure the baseline first,** before any cut:
   - studio (`apps/workflow-studio`): `npx tsc -b` error count, `npx vitest run` failures, `npm run lint` error count;
   - engine (`apps/workflow-engine`): the unit-test failures (`pytest tests/unit -q`).

   Write them down. A gate below means "no worse than the baseline". On `74e8575`, these engine tests already fail:
   `test_stage8_b5_install_pg.py` (30 tests), `test_kg_extract`, `test_kg_team`. These are order-dependent and pass when run alone:
   `test_platform_data_layer::test_studio_db_values_are_stored_and_read_back_literally` and
   `test_oneshot_builder::test_the_default_build_is_one_model_call`.
4. **Run the gates after every step.**
   - **Studio:** `npx tsc -b` (0 errors, or no worse than the baseline), `npx vitest run`, lint no worse than the baseline,
     `npm run design:check`, `npm run build`.
   - **Engine:**
     - the unit tests, no new failures;
     - `python -c "import src.main"` imports cleanly;
     - grep finds no import of a deleted module;
     - `tests/integration/test_zz_endpoint_coverage.py` is updated for the removed routes.
   - **Journeys** (in the browser):
     - create, edit, run and clone a workflow;
     - decide an approval task in Approvals;
     - build an app from the chat;
     - generate a module on the Backend page;
     - the editor's AI chat builds a workflow.
5. **Check before deleting.** For each file or symbol, grep the whole repo (the source, tests and e2e):
   ```bash
   # studio: who imports a folder or file
   grep -rn "features/knowledge" apps/workflow-studio/src apps/workflow-studio/e2e
   # engine: who imports a module
   grep -rn "kg_extract\|from \.kg_\|services\.kg_" apps/workflow-engine/src apps/workflow-engine/tests
   ```
   Also check:
   - route registrations: engine `src/routes/__init__.py` and `src/main.py`; studio `src/app/routes/`;
   - dynamic loading: `importlib`, `getattr`, node registries;
   - prompt text: `platform_index.py`, `backend_templates/prompts.py`, the one-shot prompts.
6. **Database.** Keep the tables and write no destructive migrations. Dropping data is a separate decision.
7. **Tests.** Delete only the tests of deleted code. Never skip or weaken any other test. Each step below lists the tests found
   on `74e8575`; grep for more.

## Order of the steps

**(fixed)** The first version's order (A1 → A6, then B) left the build broken between steps, because `ai-builder/apps/` and
`app_composer` import knowledge code, `project/story` imports knowledge helpers, and interfaces import releases. Do this:

1. **A2:** the hidden app composer. It imports knowledge, so it goes first.
2. **B1a:** delete `features/project/story/`. It imports knowledge helpers.
3. **A1:** knowledge.
4. **A4:** the interface builder, public interfaces and AskBar. `interface_service` imports `release_service`.
5. **A3:** the releases UI.
6. **A5:** the API tester.
7. **A6:** the legacy app-builder pipeline.
8. **B1–B6:** the rest of the dead code.

---

## Part A: cut features

### A2. Hidden second app builder, the "AI app composer" (~3.0k, low risk)
- **Studio:**
  - delete `features/ai-builder/apps/` (1.4k);
  - in `features/ai-builder/components/BuilderPanel.tsx`, remove the `AppPlanPanel` import and the button or tab that mounts
    it (around lines 23–24 and 239) **(fixed: it's in `BuilderPanel`)**;
  - remove the composer's functions from `features/ai-builder/api.ts`, and any query keys only they use.
- **Engine (1.6k):**
  - delete `services/app_composer.py`, `services/app_plan.py`, `services/app_bundle.py`, `routes/ai_apps.py` and
    `schemas/ai_apps.py`;
  - remove `ai_apps_router` from `routes/__init__.py`;
  - in `services/ai_builder.py`, remove the branch that lazily imports `app_composer.part_seam` (search `part_seam`,
    around line 1764) **(fixed)**;
  - delete `repositories/app_build_repository.py`. Its only other user is `services/kg_learn.py`, which A1 deletes. If you do
    A2 before A1 as ordered, leave the repository until A1 removes `kg_learn.py`, then delete it **(fixed)**.
- **Tests:** delete the composer's tests (`grep -rln "app_composer\|app_plan\|ai_apps" tests`), including
  `tests/unit/test_ai_apps_http.py` if present.
- **Check:** the workflow editor's AI panel still opens and builds a workflow.

### B1a. Dead studio files that import knowledge code (do before A1)
- Delete `features/project/story/` (~2.4k). Nothing imports it, and it imports `features/knowledge/lib/layout` and
  `useMotionOff`, so it must go before A1 **(fixed)**.
- Check with `grep -rn "project/story" apps/workflow-studio/src`: no hits outside the folder.

### A1. Knowledge (~10k, medium risk)

**What stays (fixed):**
- `services/project_knowledge.py` stays. The kept backend builder and app builder call it:
  - `module_generator.py`, around lines 1559, 1601 and 1626;
  - `builder_gates.py`, around 594 and 1102;
  - `platform_index.py`, around 378, 394 and 508;
  - `project_plan.py`, around 1082;
  - `build_coordinator.py`, around 104 and 220;
  - `app_builder_ai_service.py`, around 1237–1289.
- The `search_project_knowledge` tool also stays. It's named in `module_generator.py` (~900), `platform_index.py` (~450),
  `app_builder_ai_service.py` (~72) and `backend_templates/prompts.py` (~97).
- Keep `routes/project_knowledge.py` (the per-project store those builders use) **unless** a grep shows the studio is its
  only caller. In that case delete only the route.
- This cut is the capability graph (`kg_*`), not the builders' project notes.
- `nodes/ai/memory/knowledge_graph_memory.py` stays. It's the n8n-style agent memory node, not the capability graph.

**Studio:**
- **Move first:**
  - `features/knowledge/hooks/useMotionOff` → `shared/hooks/useMotionOff`. It's used by the kept landing page
    (`LandingPage.tsx`, `BuildMock.tsx`) and by `shared/components/backgrounds/SignalField.tsx`.
  - Then fix those imports.
- **Delete:**
  - `features/knowledge/` (6.3k without tests), including `KnowledgeHomeCard` (nothing imports it);
  - `app/routes/knowledge.tsx`.
- **Remove the references (fixed: the first version named only the nav):**
  - `app/routes/project.tsx`: the second `knowledgeRoute` (around lines 122 and 287) and its imports (around 17–18);
  - `app/routes/index.ts` (around lines 7 and 19);
  - `shared/api/endpoints.ts` (around 10 and 230) and `shared/api/queryKeys.ts` (around 8 and 52–56);
  - `TopBar.tsx` (around 176–184) and `places.ts` (around 28): the nav entries;
  - `PlanView.tsx` (around 812) and `ProjectDocs.tsx` (around 72): links;
  - `LandingPage.tsx` (around 318): a link to `/knowledge`;
  - `features/ai-builder/api.ts` (around 191).
- **The AI workflow builder's Understand step** uses knowledge on the studio side:
  - delete `features/ai-builder/components/UnderstandingCard`, `LiveConcepts` and `lib/understanding.ts`;
  - remove the Understand stage from `useStageDriver` and `buildState`;
  - `ClarifyQuestions.tsx` and `QuestionControl` import `lib/understanding.ts` and `features/knowledge/kit/*`
    (`Construct`, `ConceptChip`, `beats`). `BuilderPanel` imports all of these.
  - Move the small kit pieces they use into `ai-builder/components/`, so the clarify UI keeps working. Don't delete the
    clarify UI or the staged builder here: the old workflow chat must keep working until the workflow builder guide
    (`handoff/workflow-builder/WORKFLOW-BUILDER-GUIDE.md`, done last) replaces it and retires it.

**Engine:**
- **Delete:**
  - `services/kg_*.py` (3.5k);
  - `routes/knowledge.py`;
  - `repositories/kg_repository.py` (0.5k), `schemas/knowledge.py` (0.16k) and `db/knowledge_models.py`. Keep the tables;
    only the model module goes. If Alembic or migrate reads the models, keep the file.
  - `engine/knowledge/ontology/` (`bank_ops.yaml`), once nothing reads it.
- **Edit (fixed: none of these were listed):**
  - `src/main.py`, around lines 71–73: remove the `kg_extract.seed_global()` startup call;
  - `routes/__init__.py`: remove `knowledge_router` (around lines 42 and 73);
  - `services/ai_builder.py`: the `kg_*` imports and calls, around lines 329–429, 934, 1569 and 2097. These are the
    Understand stage, grounding and learning. Remove the stage and keep the rest of the builder working;
  - `services/ai_spec.py`, around 396 (`kg_understand`);
  - `services/app_plan.py` and `app_composer.py` are already gone after A2.
- **Prompts:** remove every capability-graph section from the AI-workflow-builder prompts. Leave no empty headings. The
  project-knowledge sections of the app and backend builders stay.
- **Tests:**
  - delete `test_kg_*.py`, `test_kg_repository.py` and `test_ai_builder_graph_http.py` (it imports `kg_learn`);
  - `grep -rln "kg_\|knowledge" tests`, then delete only the tests of deleted code.

**Check:**
- the editor's AI chat still builds a workflow from a request;
- app builds work;
- the Backend page generates a module;
- nothing in the nav points to knowledge;
- `grep -rn "kg_\|features/knowledge" src` leaves no hits.

### A4. Interface builder, public interfaces and AskBar (~2.5k, medium risk)

**(fixed)** The first version said the only editor edit was one polling helper. In fact Approvals (kept), the editor and
the engine's approval node all use pieces of `interfaces`. Cut the interface *builder*, the public interface pages and
AskBar; keep what Approvals and task screens use.

**What stays and where it moves.** Approvals uses these from `features/interfaces`:
- `lib/format`, `lib/binding`, `lib/types`, `lib/keys`, `lib/validate` and `lib/formFields`;
- `renderer/ScreenRenderer`.

The users are `ApprovalsPage`, `TaskDetailPage`, `TaskParts`, `useTaskDecision`, `approvals/lib/clock.ts` and
`approvals/lib/tasks.ts`.
1. Move those files to `features/approvals/lib/` and `features/approvals/renderer/` (or to `shared/`), and update the imports.
2. Also move `lib/polling` (`pollDelayMs`, used by `workflow-editor/lib/followRun.ts`) to `shared/`.
3. Run `grep -rn "features/interfaces" src`. Only files you're about to delete may still match.

**Studio, delete:**
- the rest of `features/interfaces` (the builder, chat, form, public pages, `useInterfaceState`, `interfaceDraft`,
  `chatTransport`, `askErrors`);
- `workflow-editor/components/interface/` (AskBar, `InterfacePanel`, `InterfaceDock`, `InterfacePreview`; ~0.8k).

**Studio, edit:**
- `workflow-editor/WorkflowEditorPage.tsx` (around lines 31–32): the lazy `InterfacePanel` and `InterfaceDock`;
- `workflow-editor/components/WorkflowNavbar.tsx` (around 60–61): `useInterfaceState` and `interfaceDraft`;
- `workflow-editor/stores/workflowStore.ts` (around 9): it imports `formFields`. Point it at the moved file;
- `app/routes/public.tsx` (around 7–8): drop `PublicInterfacePage` and `PublicRunPage`;
- `shared/api/endpoints.ts` (around 14): the interface endpoints;
- the `invalidate.interfaceChanged` query helper.

**Engine:**
- **Keep `schemas/interface.py` and `repositories/interface_repository.py`.** They're used by:
  - `nodes/flow/approval.py` (~141);
  - `workflow_service.py` and `workflow_checker.py`;
  - `template_bundle.py` and `workflow_toolkit.py`;
  - `workflow_repository.py` (~173);
  - `ai_builder.py` (~1326).

  The local `workflow_oneshot.py` uses `generate_interface` and `slugify` from `schemas/interface.py`.
- **Delete:** `routes/interfaces.py` and `routes/public_interfaces.py`. Remove their routers from `routes/__init__.py` (around
  lines 12–13 and 61–62).
- **`services/interface_service.py`:**
  - `chat_service.py` imports it, and `main.py` (~102) calls `chat_service.register_hooks()`;
  - check what `register_hooks` wires;
  - if it's only interface chat, remove the hook call and the chat-interface parts;
  - if Approvals or the run path needs it, keep `interface_service.py` and delete only the routes.
- **Tests:** the interface route tests, and the e2e specs that open the interface builder or public pages.

**Check:**
- the editor opens and runs are followed live;
- Approvals lists and decides a task, and its screen renders;
- an Approval node still runs.

### A3. Releases UI (~1.5k, medium risk)

**(fixed)** Live runtime code calls `ReleaseService`:
- `broker/workflows.py` (~110);
- `routes/public_apps.py` (~483);
- `services/route_dispatcher.py` (~155 and ~371, `pins_from_snapshot`);
- `interface_service.py` (~251).

Environments run through the whole project:
- `project.environments`, `default_environment` and `live_environment`;
- `ProjectLayout`, `ProjectTopBar`, `PublishDialog`, `BackendView`, `ModuleDialogs` and `TestPanel`;
- the engine's variable and secret scoping.

So this step cuts **only the releases UI**. The engine's `release_service.py`, `routes/releases.py` and all environment
code stay.

**Studio:**
- **Delete:** `features/releases` (1.4k).
- **Edit:**
  - `features/runs/components/RunSheet.tsx` (around 17 and 115): it reads `releasesApi.get` and `qk.release` to show which
    release a run used. Drop that line from the sheet, or keep a minimal call;
  - `WorkspaceOverlays.tsx` (around 8): the `ReleaseSheet` import;
  - remove the nav entries and links to releases or promote.
- **Keep** `PublishImpactDialog` and `usePublishWithImpact` (`useSubworkflows.ts` ~81). They're the editor's publish flow.
- **Keep** every environment selector and setting.

**Engine:** no changes.

**Tests:** delete the studio tests and e2e specs for the releases page only. The engine `*release*` tests stay, because
the service stays.

**Check:**
- activate, trigger and run a workflow;
- publish from the editor;
- open a run's sheet.

### A5. The app builder's API tester (~2.4k, low risk)
- **Studio:**
  - **Delete:** `app-builder/components/ApiTesterPanel.tsx` (1.9k) and the attach-API popover.
  - **`RoutePicker.tsx`:** it exports `RouteChecklist`, which `AppSettingsSheet.tsx` (~23) imports. Keep
    `RouteChecklist` (move it into `AppSettingsSheet` or its own file), then delete the rest **(fixed)**.
  - **`apiTesterApi`:** it's used by `AppBuilderChatPanel.tsx` (~219) and `AppSettingsSheet.tsx` (~74). Remove those uses,
    then the API object **(fixed)**.
  - **`AppBuilderShell.tsx`** (around 65 and 698–703): it hosts the tester panels. Remove the tab and the panel.
- **Engine:**
  - **Delete:** `routes/api_tester.py`, and remove `api_tester_router` from `routes/__init__.py` (around 30 and 97).
  - **Keep `routes/test_mocks.py` (fixed).** It serves `POST /workflows/{id}/nodes/{n}/mock/generate`, which the
    editor's Tests tab calls (`testActions.ts` ~161, `api.ts` ~557).
- **Keep** `BackendPanel` and all of `features/backend`. The backend builder stays.
- **Check:**
  - the app builder opens without a tester tab;
  - app settings still list routes;
  - the Backend page works;
  - the editor's Tests tab still generates a mock.

### A6. Legacy app-builder pipeline (~1.0k, low-medium risk)

This is the **app** builder's `WORKFLOW_BUILDER_MODE=pipeline` switch. It's unrelated to the workflow chat's
`WF_CHAT_BUILDER` switch (see below).

**(fixed)** `services/build_coordinator.py` **cannot be deleted.** Its users:
- `oneshot_builder.py` imports it at module level (~38) and uses about 20 helpers: `emit`, `emit_file`, `_factory`,
  `_app_service`, `concurrency`, `route_statuses`, `_emit_tables` and others;
- `app_autotest.py` (~255), `app_builder_ai_service.py` (~1951, 1961 and 2008), `routes/builds.py` (~24) and `main.py`
  (~95) also import it;
- the default one-shot build runs inside `Coordinator.execute` (~773).

**Engine:**
- In `build_coordinator.py`:
  - remove the role-split pipeline only: class `Builders` (~96–372) and the pipeline branch inside `class Coordinator`
    (~688–1380);
  - keep every helper the files above use, and grep each one before removing it.
- **`backend_templates/scaffold.py` (0.35k):**
  - `build_coordinator.py` imports `leftovers` and `todo_message` from it (~53) and the module itself (~159);
  - if the one-shot path still calls `leftovers`/`todo_message` after the pipeline is gone, keep those two functions and
    delete the rest;
  - otherwise delete the file.
- **Remove the flag:** `oneshot_builder.py` (~56–57), the mode function, and its callers.

**Tests:**
- the four tests that set `WORKFLOW_BUILDER_MODE=pipeline`: `tests/integration/test_build_end_to_end.py`,
  `tests/unit/test_build_coordinator.py`, `test_build_fast_path.py` and `test_build_live_stream.py`;
- if a file tests only the pipeline, delete it. If a file also tests shared helpers, delete only the pipeline tests;
- `test_oneshot_builder.py` (~292) unsets the flag. Remove that line.

**Check:** an app build from the chat works, and all its stream events still arrive.

---

## Part B: remove dead code (~6k)

None of this changes anything a user sees. Re-run the import-graph check on your copy first: list every `src` file, and
mark the ones no other file imports.

### B1. Unreachable studio files (~1.7k after B1a, high confidence)
- **Old overview tiles plus `useWidth`** (~1.0k) in `features/project/overview`:
  - **keep** `HealthBadge` (used by `OverviewView`), `overview/home/`, `lib/pulse` and `lib/stats`;
  - delete only the tiles nothing imports **(fixed)**.
- **`features/projects/hooks/`:**
  - delete `useAppActions`, `useApps`, `useLatestExecutions` and `useWorkflowActions` together;
  - **keep `useWorkflows.ts` (fixed).** It's live: `overview/home/Builds.tsx` and the editor's
    `WorkflowSelectorField`, `versionDiffView`, `workflowDiff` and `workflowTransform` import it.
- `features/projects/lib`, if nothing imports it.
- `workflows-list/workflowMeta.ts`.
- `project/hooks/useMissingCount.ts`.
- `shared/components/ai-elements/task.tsx` and `tool.tsx` (0.26k).

### B2. Dead exports and API methods in the studio (~0.5k, high confidence)
- **Functions:** `useExecuteWorkflow` (only defined, at `useWorkflowApi.ts` ~188), and `workflowsApi.runAdhoc`, which becomes
  dead with it.
- **API methods:**
  - `workflowsApi.importN8n`: the UI never calls it; the engine route stays;
  - `appsApi.getVersion` and `createVersion`;
  - `nodesApi.get`;
  - `projectsApi.drift`;
  - `routesApi.get` and `usedBy`;
  - `specApi.diff`;
  - `tablesApi.get`;
  - `releasesApi.diff`, unless A3 already removed it.
- **Query keys and invalidators:**
  - `qk.route`, `qk.usedBy`, `qk.apiMocks`, `qk.projectDrift` and `qk.previewToken`;
  - `invalidate.appSaved` and `invalidate.tasksChanged`; `invalidate.interfaceChanged` goes in A4.
- **Everything else:**
  - about 60 zero-use exports and 9 unused types;
  - dead helpers in `tests.ts` and `shapes.ts`, and in `workflow-editor/components/canvas/stageGeometry.ts` **(fixed: it's
    under `components/canvas/`, not `lib/`)**.
- Grep each name before removing it.

### B3. The copied-in `ai-elements` chat UI kit, unused parts (~1.25k, medium-high confidence)
- **`prompt-input.tsx`** is 1.45k lines, and only 5 of its exports are used; about 28 are unused.
- **Also trim** the unused parts of `message`, `code-block`, `conversation` and `file-tree`.
- **Check** that the chat input looks the same.

### B4. Engine dead code (~0.7k, high confidence)
- **Unused modules:** `services/schema_analyzer.py`, `nodes/data/object_store.py` and `nodes/ai/memory/utils.py` (0.3k).
  Nothing imports them, not even tests, and no registry loads them.
- **Dead functions:** the 17 confirmed by grep (~130 lines; listed in `docs/cleanup/python-deadcode.md`), and `fetchBlob` in
  `app_kit/hooks.ts`.
- **Kept on purpose:**
  - n8n import, credentials and folders;
  - agent memory and agent tools (n8n main features);
  - the PromptLab seed.

### B5. Unused packages (low risk)
- **Studio:** `npm uninstall` `@unocss/core`, `@unocss/preset-wind`, `react-markdown`, `remark-gfm` and `immer`.
  - **Keep `tailwindcss-v3` (fixed).** The engine's `services/app_bundler.py` (~167) runs
    `workflow-studio/node_modules/tailwindcss-v3/lib/cli.js` and fails every app publish without it. Only remove it if your
    copy's `app_bundler.py` no longer mentions `tailwindcss-v3` (the newer local bundler uses Tailwind v4 via
    `@tailwindcss/node`).
  - Keep `tw-animate-css`, `@tailwindcss/typography` and `@tailwindcss/vite`.
  - Before each uninstall, `grep -rn "<package>" src` plus the config files (`vite.config`, `postcss`, `tailwind`) and the
    engine (`apps/workflow-engine/src`).
- **Engine:**
  - remove `aiosqlite`;
  - remove `openpyxl` only together with `apps/workflow-engine/dummy_excel_api.py`, which imports it (~23). That file is a
    demo helper; keep both if anyone still runs it **(fixed)**;
  - keep `python-multipart`.

### B6. Copy-pasted helpers (~0.4k, medium confidence)
- Merge each into one shared util, only where the copies behave identically:
  - `formatBytes` (4 copies);
  - `plural` (5);
  - `median` (3);
  - the reduced-motion helper (5; after A1 one of them is `shared/hooks/useMotionOff`);
  - the relative-time formatters.
- **Engine (fixed):** `_chain` is defined in 2 files, `backend_templates/checks.py` and `backend_templates/selftest.py`.
  Merge them only if they're identical. `_attr_chain` in `project_knowledge.py` and `module_generator.py` is a different
  helper; leave it.
- **Leave `slugify`/`SLUG_RE` alone.** The copies differ.

---

## If your copy has the newer local work

The owner's main machine has work that wasn't on GitHub at `74e8575`. Your copy may have some of it.

The workflow part of that work comes as patches in `handoff/workflow-builder/`: engine fixes, the one-shot workflow chat
and the read view. **Don't apply them now.** The order is this cut → `ONESHOT-BUILDER-PORTING-GUIDE.md` →
`handoff/workflow-builder/WORKFLOW-BUILDER-GUIDE.md`. That guide applies the patches last and retires the old chat.

Check for each:

| If your copy has… | Then… |
|---|---|
| `services/workflow_oneshot.py` and `oneshot_prompt.md` (the editor's AI chat builds workflows in one call; `WF_CHAT_BUILDER` switch) | It uses `schemas/interface.py` (`generate_interface`, `slugify`; kept by A4), `ai_spec.SPEC_VERSION`, `proposal_sandbox` and many `ai_builder` helpers (`_finish`, `load_ctx`, `check_all`, `Ctx`, `_sf`, `_sha`, `get_view`). A1 edits `ai_builder.py`: keep every helper `workflow_oneshot.py` calls, and keep `revise()`'s branch to `workflow_oneshot.revise`. A1 removes only the Understand stage; the legacy chat (`WF_CHAT_BUILDER=legacy`) keeps working without it. Retiring it is step 3 of the workflow builder guide, done last. |
| `app_bundler.py` using `@tailwindcss/node` (Tailwind v4) and `app_bundler_tailwind.mjs` | `tailwindcss-v3` can go in B5. |
| `app_kit/board.tsx`, `display.tsx`, `layout.tsx`, `overlay.tsx`, `plots.tsx`, `theme.css` | The newer app kit. Keep it; nothing here cuts it. |
| `features/app-builder/visual/`, `visual-edit-runtime.ts`, `visualSave.ts`, `editability_lint.py`, `editability/` | The visual editor. The owner dropped it. Delete it as its own step (about 10k lines) after A5, with its tests, and grep for importers the same way. |
| `workflow-editor/components/read-view/`, `readView.ts` | A read-only view of a workflow. Keep it. |
| Migration `20261030120000_human_task_items.sql`, `per_item` approval | Keep it. A4 must not touch the approval node or task repository. |
| `apps/workflow-engine/prototypes/workflow-oneshot/` | The workflow builder's eval kit. Keep it. |

Expect conflicts in `oneshot_builder.py`, `app_builder_ai_service.py`, `platform_index.py` and `WorkflowNavbar.tsx`, which
the newer work and these cuts both edit. Resolve by keeping the newer work's behaviour and removing only the cut feature.

## Not cut in this pass

| Item | Why it stays now |
|---|---|
| Landing page | owner: keep |
| n8n import, credentials, folders, agent memory and tools, PromptLab seed | owner: keep the n8n main features |
| Backend builder (`features/backend`, module generator, `project_knowledge.py`) | owner: keep |
| Workflow editor, including its Tests tab (fuzz, drift, smoke, `test_mocks`) | owner: don't touch the editor |
| Approvals, task screens, the approval node's interface schema | live; only the interface *builder* goes |
| Release service, environments | live runtime uses them; only the releases UI goes |
| `build_coordinator.py` helpers | the one-shot app build runs on them |
| Plan step and clarify (app builder) | builds start from them; the porting guide replaces them |
| The agent-loop app builder | used by apps without the `studio.oneshot` flag; the porting guide retires it |
| `RunInline`/`RunSheet` and `TraceView`/`TraceWaterfall` | inside the editor and the backend builder; merge them later |
| jobs-service and test-builder | the engine calls jobs-service: `broker/jobs.py`, `core/config.py` (`JOBS_SERVICE_*`), `sdk/static/jobs.py`, `backend_templates/checks.py` and `selftest.py` (jobs wiring in generated backends), `tests/unit/test_broker_jobs.py`. jobs-service links `@playwright/test` from `../test-builder/node_modules`. Removing them needs all of that cut too. |
| analytics-service | the Datasets pages (`features/datasets`) and `project_dataset_service.py` (`settings.analytics_service_url`) call it |

## After Part A and Part B

| Area | GitHub `74e8575` | After |
|---|---|---|
| Studio | 100.4k | ~82k |
| Engine | 102.1k | ~93k |
| **Total** | **~202k** | **~175k** |

Next is `ONESHOT-BUILDER-PORTING-GUIDE.md`: one app-builder path, with no plan step. It retires the agent loop, the plan
step and the old app-builder checks. Last is `handoff/workflow-builder/WORKFLOW-BUILDER-GUIDE.md`: the one-shot workflow
chat, then retiring the old staged chat.

## Later simplifications

These stay through this pass only because live code still uses them. They aren't features we want. Simplify them
after the cut and the app-builder port, one at a time, with the same gates:

| Item | Simplification | When |
|---|---|---|
| `tailwindcss-v3` (studio package) and the old `app_bundler.py` CSS step | Delete them. The new app builder's kit doesn't use Tailwind. | `ONESHOT-BUILDER-PORTING-GUIDE.md` Phase 4, with the old app builder |
| Release service (`release_service.py`, `routes/releases.py`, version pins in `route_dispatcher.py`, `public_apps.py` and `broker/workflows.py`) | Replace it with "run the latest published version", then delete the service and its tables' code. | After the port. It's a redesign, so ask the owner first. |
| Environments (`project.environments`, per-environment variables and secrets) | Fold them into one environment if the owner doesn't need dev/prod. | With the release-service change |
| `routes/test_mocks.py` and the editor Tests tab's "generate mock" button | Delete both if the owner doesn't want AI-generated mocks in the editor. | Any time; ask the owner (it touches the editor) |
| What's left of interfaces after A4 (the approval-screen files moved to `approvals/`, `schemas/interface.py`, `interface_repository.py`, `interface_service.py`) | Rename and slim them down to what approvals use: task screens and the form schema. | After A4 |

## Open decisions

1. **Database tables** for knowledge, releases and interfaces: leave them unused (the default), or drop the data?
2. *(settled)* The old staged workflow chat keeps working through this cut (A1 removes only its Understand step). The
   workflow builder guide, done last, retires it.
