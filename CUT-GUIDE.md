# Cut guide: unneeded features and dead code

Status, 2026-10-22: a guide only. Nothing has been removed yet.

**Baseline: GitHub, branch `revert-to-5684ba5` at `87989ea`.** All line counts below are measured on that commit.

Local uncommitted work is not part of this plan:
- the visual editor
- the new kit files
- the last round of builder edits

The cuts are made on a fresh checkout of the GitHub branch.

## Contents

- [What this pass does](#what-this-pass-does)
- [Where this gets us](#where-this-gets-us)
- [Rules for doing the cuts](#rules-for-doing-the-cuts)
- [Part A: cut features (~24k)](#part-a-cut-features-24k)
  - [A1. Knowledge](#a1-knowledge-113k-medium-risk)
  - [A2. Hidden second app builder](#a2-hidden-second-app-builder-the-ai-app-composer-30k-low-risk)
  - [A3. Releases and environments](#a3-releases-and-environments-22k-medium-risk)
  - [A4. Interfaces and AskBar](#a4-interfaces-and-askbar-39k-low-medium-risk)
  - [A5. API tester](#a5-the-app-builders-api-tester-25k-low-risk)
  - [A6. Legacy builder pipeline](#a6-legacy-builder-pipeline-12k-low-risk)
- [Part B: remove dead code (~6.5k)](#part-b-remove-dead-code-65k)
  - [B1. Unreachable studio files](#b1-unreachable-studio-files-37k-high-confidence)
  - [B2. Dead exports and API methods](#b2-dead-exports-and-api-methods-in-the-studio-05k-high-confidence)
  - [B3. Unused ai-elements parts](#b3-the-copied-in-ai-elements-chat-ui-kit-unused-parts-125k-medium-high-confidence)
  - [B4. Engine dead code](#b4-engine-dead-code-07k-high-confidence)
  - [B5. Unused packages](#b5-unused-packages-low-risk)
  - [B6. Copy-pasted helpers](#b6-copy-pasted-helpers-04k-medium-confidence)
- [Not cut in this pass](#not-cut-in-this-pass)
- [After Part A and Part B](#after-part-a-and-part-b)
- [Open decisions](#open-decisions)

## What this pass does

This pass cuts the features we don't need and removes dead code.

The **workflow editor, runner and clone** are not touched, apart from two small import moves in A3 and A4. Simplifying the two builders comes later; see `SIMPLIFICATION-PLAN.md`.

The evidence for each item is in `docs/cleanup/`. Each report gives file:line, importer counts and a confidence level:
- `engine-appbuilder.md`
- `engine-platform.md`
- `studio-builder.md`
- `studio-editor-shared.md`
- `python-deadcode.md`
- `ts-deadcode.md`

Those reports were made on the local tree. Re-check each file on the GitHub checkout before deleting it.

## Where this gets us

| | Lines, not counting tests |
|---|---|
| GitHub today: studio 100.4k + engine 102.1k | **~202k** |
| After Part A (feature cuts) | ~178k |
| After Part B (dead code) | **~172k** |
| After the later builder simplification (`SIMPLIFICATION-PLAN.md`) | **~150k** |

Cuts and dead code alone reach about **172k**. The plan step can't simply be cut, because today's app builds start from it (`features/plan/useBuild.ts`).

The remaining ~22k comes from simplifying the builders:
- the plan step: ~7k
- the old agent-loop app builder: ~3.5k
- the AI workflow builder's spec, questions and rounds: ~8k
- duplicated checks in the app builder: ~4k

About 10–12k test lines also go with the removed code. They are not counted above.

## Rules for doing the cuts

1. **Start from GitHub.** Work on a fresh checkout of `origin/revert-to-5684ba5`. Put the local uncommitted work aside first, for example in a backup or a separate branch; never discard it.
2. **One step per change,** in the order below, so each step can be reverted on its own. Nothing is committed until the owner says "commit".
3. **Run the gates after every step.**
   - **Studio** (`apps/workflow-studio`):
     - `npx tsc -b` gives 0 errors
     - `npx vitest run` is green
     - lint errors do not go above the baseline (measure it on the checkout first)
     - `npm run design:check` is clean
     - `npm run build` succeeds
   - **Engine** (`apps/workflow-engine`):
     - the unit tests pass (except failures already present at the baseline; record them first)
     - the app imports cleanly
     - grep finds no import of a deleted module
     - `tests/integration/test_zz_endpoint_coverage.py` is updated for removed routes
   - **Journeys** (checked in the browser):
     - create, edit, run and clone a workflow
     - build an app from the chat
     - generate a module on the Backend page
4. **Check before deleting.** Grep for:
   - importers
   - route registrations (`routes/__init__.py`, `main.py`, studio `app/routes`)
   - dynamic registries (`importlib`/`getattr`, node registries)
   - prompt references (`platform_index.py`, the oneshot prompts)
5. **Database.** Keep the tables and write no destructive migrations. Dropping data is a separate decision.
6. **Tests.** Delete only the tests of deleted code. Never skip or weaken any other test.

---

## Part A: cut features (~24k)

### A1. Knowledge (~11.3k, medium risk)
- **Studio (6.9k):**
  - delete `features/knowledge/` (6.3k) and `app/routes/knowledge.tsx`
  - remove the nav entries, `KnowledgeHomeCard`, and the knowledge API functions and query keys
- **Engine (4.4k):**
  - delete `services/kg_*.py` (3.5k) and `services/project_knowledge.py` (0.6k)
  - delete `routes/knowledge.py` and `routes/project_knowledge.py`
  - delete their repositories and schemas
- **The AI workflow builder's Understand step** runs on `kg_understand`, so it goes in the same change:
  - studio: `UnderstandingCard`, `LiveConcepts` and `lib/understanding.ts` (0.55k), plus the Understand stage in `useStageDriver` and `buildState`
  - engine: the Understand stage in `ai_builder.py`
- **Prompts:** remove every knowledge-context section from the app-builder and AI-workflow-builder prompts. Leave no empty headings.
- **Check:**
  - the AI workflow builder still builds a workflow from a request
  - app builds work
  - nothing in the nav points to knowledge
  - `grep -ri "kg_\|knowledge"` leaves only unrelated hits

### A2. Hidden second app builder, the "AI app composer" (~3.0k, low risk)
- **Studio:** delete `features/ai-builder/apps/` (1.4k), its button in the workflow editor's AI panel, and its API functions.
- **Engine (1.6k):** delete:
  - `services/app_composer.py`, `app_plan.py` and `app_bundle.py`
  - `routes/ai_apps.py` and `schemas/ai_apps.py`
  - their tests
  
  Check `repositories/app_build_repository.py` for other users first.
- **Check:** the workflow editor's AI panel still builds workflows.

### A3. Releases and environments (~2.2k, medium risk)
- **Studio:** delete `features/releases` (1.4k), the environment settings, and the release/promote UI and links.
- **Engine:** delete `services/release_service.py`, `routes/releases.py`, and the environment routes and schemas (0.8k+).
- **Risk:** the editor's publish flow (`usePublishWithImpact`, workflow activation) may call releases.
  - Map that first. Activating and running a workflow must still work after the cut.
  - If publish can't be unhooked in a few lines, stop and report.
- **Check:** activate, trigger and run a workflow.

### A4. Interfaces and AskBar (~3.9k, low-medium risk)
- **Do this first:** `workflow-editor/lib/followRun.ts` imports a polling helper from `interfaces`. Move it to `shared/`. This is the only edit inside the editor, and it changes no behaviour.
- **Studio:** delete `features/interfaces` (1.95k) and the editor's interface/AskBar components (`workflow-editor/components/interface`, ~0.5k).
- **Engine:** delete `services/interface_*.py`, `routes/interfaces.py` and `routes/public_interfaces.py` (1.4k).
- **Check:** the editor opens, and runs are followed live.

### A5. The app builder's API tester (~2.5k, low risk)
- **Studio:** delete `app-builder/components/ApiTesterPanel.tsx` (1.9k), the attach-API popover and `RoutePicker` (0.15k).
- **Engine:** delete `routes/api_tester.py`. Also delete `routes/test_mocks.py` if only the tester uses it (0.4k).
- **Keep** `BackendPanel` and all of `features/backend`. The backend builder stays.
- **Check:**
  - the app builder opens without a tester tab
  - the Backend page works

### A6. Legacy builder pipeline (~1.2k, low risk)
- **Engine:** delete everything reachable only with `WORKFLOW_BUILDER_MODE=pipeline`:
  - the pipeline parts of `services/build_coordinator.py` (1.5k file)
  - `backend_templates/scaffold.py` (0.35k)
  - the flag itself
- **Check:** an app build from the chat works through the one-shot path.

---

## Part B: remove dead code (~6.5k)

None of this changes anything a user sees.

### B1. Unreachable studio files (~3.7k, high confidence)
These are the files nothing imports:
- `features/project/story/` (2.0k plus css)
- the old overview tiles plus `useWidth` (~1.0k), all in `features/project/overview`; the live overview stays
- `features/projects/hooks/*` and `projects/lib` (0.5k)
- `workflows-list/workflowMeta.ts`
- `project/hooks/useMissingCount.ts`
- `shared/components/ai-elements/task.tsx` and `tool.tsx` (0.26k)

Removing them together orphans nothing else. Re-run the import-graph check (`ts-deadcode.md` method) on the GitHub checkout first.

### B2. Dead exports and API methods in the studio (~0.5k, high confidence)
- **Functions:**
  - `useExecuteWorkflow` (~106 lines), plus `workflowsApi.runAdhoc`, which becomes dead with it
- **API methods:**
  - `workflowsApi.importN8n` (the UI never calls it; the engine route stays)
  - `appsApi.getVersion` and `createVersion`
  - `nodesApi.get`
  - `projectsApi.drift`
  - `routesApi.get` and `usedBy`
  - `specApi.diff`
  - `tablesApi.get`
  - `releasesApi.diff`
- **Query keys and invalidators:**
  - `qk.route`, `qk.usedBy`, `qk.apiMocks`, `qk.projectDrift` and `qk.previewToken`
  - `invalidate.appSaved`, `invalidate.tasksChanged` and `invalidate.interfaceChanged`
- **Everything else:**
  - about 60 zero-use exports and 9 unused types
  - dead editor helpers in `tests.ts`, `shapes.ts` and `stageGeometry.ts`

### B3. The copied-in `ai-elements` chat UI kit, unused parts (~1.25k, medium-high confidence)
- **`prompt-input.tsx`** is 1.45k lines, and only 5 of its exports are used; about 28 are unused.
- **Also trim** the unused parts of `message`, `code-block`, `conversation` and `file-tree`.
- **Check** that the chat input looks the same.

### B4. Engine dead code (~0.7k, high confidence)
- **Unused modules:** `services/schema_analyzer.py`, `nodes/data/object_store.py` and `nodes/ai/memory/utils.py` (0.3k). Nothing imports them, not even tests.
- **Dead functions:** the 17 confirmed by grep (~130 lines) and `app_kit/hooks.ts` `fetchBlob`.
- **Kept on purpose:**
  - n8n import, credentials and folders
  - agent memory and agent tools (n8n main features)
  - the PromptLab seed

### B5. Unused packages (low risk)
- **Studio:** run `npm uninstall` for `@unocss/core`, `@unocss/preset-wind`, `react-markdown`, `remark-gfm`, `tailwindcss-v3` and `immer`.
  - Keep `tw-animate-css`, `@tailwindcss/typography` and `@tailwindcss/vite`.
  - The engine's bundler resolves Tailwind from studio's `node_modules`.
- **Engine:** remove `aiosqlite` and `openpyxl`. Keep `python-multipart`.

### B6. Copy-pasted helpers (~0.4k, medium confidence)
- Merge each into one shared util, only where the copies behave identically:
  - `formatBytes` (4 copies)
  - `plural` (5)
  - `median` (3)
  - the reduced-motion helper (5)
  - the relative-time formatters
- **Engine:** `_chain` is copied byte-for-byte in 4 files.
- **Leave `slugify`/`SLUG_RE` alone.** The copies differ.

---

## Not cut in this pass

| Item | Why it stays now |
|---|---|
| Landing page | owner: keep |
| n8n import, credentials, folders, agent memory and tools, PromptLab seed | owner: keep the n8n main features |
| Backend builder (`features/backend`, module generator) | owner: keep |
| Workflow editor, including its Tests tab (fuzz, drift, smoke) | owner: don't touch the editor |
| Plan step and clarify | builds start from them; removing them is a builder simplification |
| The agent-loop app builder | used by apps without the `studio.oneshot` flag; retire it during simplification |
| `RunInline`/`RunSheet` and `TraceView`/`TraceWaterfall` | inside the editor and the backend builder; merge them later |
| jobs-service and test-builder | the engine's broker calls jobs-service; removing it needs that wiring cut too |

## After Part A and Part B

| Area | GitHub today | After |
|---|---|---|
| Studio | 100.4k | ~80k |
| Engine | 102.1k | ~92k |
| **Total** | **~202k** | **~172k** |

Next is `SIMPLIFICATION-PLAN.md` phases 2–4, which bring it to about 150k:
- one app-builder path, with no plan step
- a one-call AI workflow builder
- fixed checks

## Open decisions

1. **Database tables** for knowledge, releases and interfaces: leave them unused, or drop the data?
2. **A3 publish dependency:** if releases are wired deep into workflow publish, cut the releases UI only and keep the engine service until simplification?
