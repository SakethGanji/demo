# Simplification plan

2026-10-22. A plan only: nothing has been removed yet. The working tree is exactly as it was before the cleanup agents ran; it was restored from the snapshot and verified identical.

Evidence for every item is in the review reports:
`/tmp/claude-1000/-home-saketh-Projects-playground-work-demo/3b980f2a-bdc6-4187-b5dc-d2b1e0bdc78d/scratchpad/cleanup/`
- `engine-appbuilder.md`
- `engine-platform.md`
- `studio-builder.md`
- `studio-editor-shared.md`
- `python-deadcode.md`
- `ts-deadcode.md`
- `v0-research.md`

## 1. What we keep and what we cut

**Keep, untouched:**
- the workflow editor, runner, and clone
- the n8n-style main features: n8n import, credentials, agent memory and tools
- the landing page
- the backend builder (the Backend page: module generation, code map, test panel)
- the core app builder
- the look and feel

**Cut:**
- knowledge (graph, list, coverage, memory, learning; UI and backend)
- the visual editor
- the hidden second app builder in the workflow editor's AI panel
- the legacy builder pipeline
- dead code
- releases and environments
- interfaces and AskBar
- the app builder's API tester panel

**Rule from here on:**
- fix what doesn't work
- simplify what's over-built
- add features slowly, by hand-reviewed steps
- don't delete code only to lower the line count

**Out of scope for now:** security fixes, which the owner deferred.

## 2. Where the code is today

The total is about 213k lines, not counting tests:
- engine: about 105k
- studio: about 108k

Roughly:

| Area | Lines | Fate |
|---|---|---|
| Workflow core: nodes, executor, editor | ~50k | keep |
| App builder: engine services and kit, studio | ~45k | simplify (sections 4 and 5) |
| Platform: routes, DB, project pages, shared UI | ~65k | keep, minus cut pages |
| AI workflow builder | ~16k | simplify (section 6) |
| Knowledge | ~11k | cut |
| Visual editor (never committed) | ~11k | cut |
| Other cut features | ~10k | cut |

After the cuts in section 3, about 175k remain. After the simplifications in sections 5 and 6, about 160k remain. Removing the confirmed dead and duplicate code later (section 7) would take it to about 150k.

## 3. Phase 1: cut features

These run in this order, one step at a time. Each step must pass the gates in section 8 before the next one starts.

### 3.1 Knowledge (~11k)
- **Studio:**
  - delete `features/knowledge/` (3D graph, list, coverage, memory views, ask box)
  - delete `app/routes/knowledge.tsx`, the nav entries and `KnowledgeHomeCard`
  - delete the knowledge API functions
  - in `ai-builder`, delete the "Understanding" step UI: `UnderstandingCard`, `LiveConcepts`, `understanding.ts`
- **Engine:**
  - delete `services/kg_*` (extract, learn, understand, slots, ontology, …) and the knowledge routes, repositories and schemas
  - remove the knowledge-context sections from the app-builder and workflow-AI-builder prompts
- **Database:** stop using the knowledge tables. Do not drop them; dropping the data is a separate yes/no.
- **Watch out for:** `kg_understand` feeds the AI workflow builder's Understand step, so that step goes in the same change.

### 3.2 Visual editor (~11k, uncommitted)
- **Studio:**
  - delete `features/app-builder/visual/`, `sandbox/visual-edit-runtime.ts` and `hooks/visualSave.ts`
  - delete the visual-only bridge messages (`vrect`, `vtextstart`, `vlive`, `vhover`, `vscreen`, `vpatch`)
  - edit the 6 builder files that import it: `AppBuilderChatPanel`, `AppBuilderShell`, `AppPreviewPanel`, `useAppBuilderChat`, `esbuild-bundler`, `IframeSandbox` and `bridge`
  - keep the plain phone, tablet and desktop width switch
- **Engine (visual-trial leftovers, about 1.85k):**
  - delete `editability_lint.py` and `editability/*.mjs`; the lint runs a node subprocess on every build, and nothing reads its result
  - delete the editable-shape, registry and `WORKFLOW_EDITABLE_RULES` code in `oneshot_builder.py`
  - leave the kit's `data-vcomp` attributes alone; they are harmless

### 3.3 The hidden second app builder (~3k)
- **Studio:** delete `features/ai-builder/apps/`, the "AI app composer" inside the workflow editor's AI panel, along with its button and API functions.
- **Engine:** delete `app_composer.py`, `app_plan.py`, `app_bundle.py` and `routes/ai_apps.py`, plus their tests. Check `app_build_repository` for other users first.

### 3.4 Legacy pipeline and dead code (~5k)
- **Engine:** delete everything reachable only with `WORKFLOW_BUILDER_MODE=pipeline` (parts of `build_coordinator.py`, `backend_templates/scaffold.py`), and the flag itself.
- **Studio:** delete the 33 files nothing imports:
  - the old overview tiles
  - `project/story/`
  - `projects/hooks` and `projects/lib`
  - `workflowMeta.ts`
  - `ai-elements/task.tsx` and `tool.tsx`
  
  Removing them together orphans nothing else.

### 3.5 Releases and environments (~2k studio, plus engine)
- **Studio:** delete `features/releases`, the environment settings and the release/promote UI.
- **Engine:** delete the releases and environments routes, services and schemas.
- **Risk:** the workflow editor's publish flow (`usePublishWithImpact`, activation) may depend on releases. Map it first, so that activating and running a workflow still works without releases.

### 3.6 Interfaces and AskBar (~2.5k studio, plus engine)
- **Studio:** delete `features/interfaces` and the editor's Interface and AskBar components.
- **Engine:** delete the interfaces routes and service.
- **Risk:** `workflow-editor/lib/followRun.ts` imports a polling helper from `interfaces`, so move that helper to `shared` first. This is the only edit inside the editor, and it changes nothing about how the editor behaves.

### 3.7 The app builder's API tester (~1k)
- delete `ApiTesterPanel`, the attach-API popover and `RoutePicker`
- keep `BackendPanel` and all of `features/backend`; the backend builder stays

## 4. Phase 2: fix the app builder (it doesn't work reliably today)

What the review found, in order of how much it hurts:

1. **Three builders behind one chat.**
   - The one-shot builder runs only for apps carrying a hidden `studio.oneshot` flag.
   - Every other app goes to an older agent-loop builder (`_run_agent_loop` in `app_builder_ai_service.py` plus `app_scaffold.py`, about 3k lines).
   - The legacy pipeline is a third.
   
   **Fix:** route every chat message to one path. Add a first-message path for an empty app, then retire the agent loop.
2. **Two editing screens.** `/apps/new` renders BuildStudio, and "Open app" switches to AppBuilderShell. That shell rebuilds the chat from saved prompts (`restoreChat`), so the user sees a different conversation than the one they had.
   
   **Fix:** one screen, AppBuilderShell, with the conversation persisted as it actually happened.
3. **Chat state in 5 places:**
   - the plan query
   - `planFlowStore`
   - the build query
   - the global chat store
   - the doc store
   
   Known stuck states:
   - a `partial` build keeps the user in the plan chat
   - BuildStudio never clears the global chat store, so the previous app's messages leak into the next app
   - a failed autosave makes the next message fail with a 409, telling the user to reload the app
   - two hidden auto-senders (the auto-fix loop and `?prompt`) send messages the user didn't type
   - one flow per project means two apps in a project share a conversation
   
   **Fix:** one store per app, holding messages and build status, and nothing else global.
4. **Checks that fail good builds:**
   - The smoke check calls create, update and delete routes with an empty body. The resulting 5xx counts as a build error and uses up the fix attempt. **Fix:** send a body built from the schema, or skip non-GET routes.
   - The `literal_issues` regex flags text such as "100%" or `SAMPLE_USERS`. **Fix:** narrow it or drop it.
   - `clarify_enabled()` also switches the TypeScript check, so turning clarify off silently turns off `tsc`. **Fix:** decouple them.
   - A single-module plan that goes over the token limit has no fallback. **Fix:** stream the call and use `_write_staged` as the one fallback.
5. **"At most 1 fix" is really up to 5 calls,** each resending the full brief. **Fix:** one fix call, given the exact errors and the failing files.
6. **Three test runners per build** (module self-test, route smoke, scenario autotest), **5 scanners for API calls**, and **`_chain` copied 4 times**. **Fix:** one runner (self-test plus smoke), one scanner and one helper.
7. **Every version stores and re-parses about 4.4k lines of kit files.** **Fix:** keep the kit out of the per-version file set.

## 5. Phase 3: simplify the app builder flow (modelled on v0)

The research behind this is in `v0-research.md`. v0 and bolt make one generation call per message: no plan step and no question round. They get reliable through deterministic post-processing (v0 goes from 64.7% to 93.9% error-free builds) and through a fixed, preloaded stack that the prompt lists exactly.

The target flow, which keeps the backend builder:

1. **The user describes the app.** If the request is vague, use sensible defaults and state the assumptions in one line.
2. **One streaming call writes:**
   - the backend: tables plus modules, through the existing backend builder
   - the React page(s) against the kit
   
   No separate plan or clarify round. The plan/clarify UI stays in the code, switched off, until we decide to delete it.
3. **A deterministic post-processor runs before bundling.** It:
   - adds missing kit imports
   - maps unknown icons to allowed ones
   - strips unknown imports
   - ensures a default export
4. **Each piece is checked by running it:**
   - the backend's routes are smoke-called with valid bodies
   - the page is bundled and rendered
5. **At most 2 automatic fix calls,** each given the exact error and the failing files. After that, show the error and a "Fix" button.
6. **Edits rewrite only the files they touch,** with the other files kept verbatim in the prompt.
7. **Measure it:** keep about 20 bank-style regression prompts and track the percentage of first builds that render error-free.

**Optional, not approved:** the "easier Retool" variant, where the backend is a set of named SQL queries the user can see and run in a Queries panel, instead of generated modules. Decide this after section 4 is green.

## 6. Phase 4: simplify the AI workflow builder

The editor works and stays. The *AI* workflow builder is about 8.1k engine lines plus about 7.8k studio lines:
- spec
- clarify questions
- rounds
- repairs
- an Understand step backed by `kg_*`
- fuzz and drift

That is the same role-split design that failed for apps.

The target:
1. "Build a workflow that does X" makes one call.
2. The call writes workflow JSON from the node catalog.
3. The existing workflow checker validates it.
4. At most 1 fix call follows.
5. The result opens on the canvas.

Proposals, history and undo stay as they are, and anything user-visible in the editor stays the same. The spec, questions and rounds code is switched off first and deleted only once the simple path is proven.

## 7. Later, optional: dead and duplicate code (~10k)

Do this only after the phases above, and only if it helps:
- **Dead exports and functions (studio, about 400 lines):** `useExecuteWorkflow` and `workflowsApi.runAdhoc`, plus 10 unused API methods.
- **Vendored `ai-elements` (about 1.25k):** the unused parts.
- **Unused npm packages:** `@unocss/core`, `@unocss/preset-wind`, `react-markdown`, `remark-gfm`, `tailwindcss-v3`, `immer`.
- **Unused Python packages:** `aiosqlite`, `openpyxl`.
- **Helpers duplicated 3–5 times:** `formatBytes`, `plural`, `median`, relative time, reduced motion.
- **Duplicate run and trace views:** `RunInline`/`RunSheet` and `TraceView`/`TraceWaterfall`.
- **Engine dead functions (17) and unused modules (3).**

## 8. How each step is verified

After every step, all of these must pass:
- **Studio:**
  - `npx tsc -b` gives 0 errors
  - `npx vitest run` is green
  - lint errors don't go above 93
  - `npm run design:check` is clean
  - `npm run build` succeeds
- **Engine:**
  - the unit tests pass (the known `test_stage3a_api_catalog` failure predates this work)
  - the app imports cleanly
  - no import points at a deleted file
- **Journeys that must keep working,** checked in the browser:
  - create, edit, run and clone a workflow
  - build an app from the chat
  - generate a backend module on the Backend page

Rules for the work itself:
- one step per change, so each can be reverted on its own
- nothing is committed until the owner says "commit"

## 9. Order and size

| Step | Removes | Risk |
|---|---|---|
| 3.1 Knowledge | ~11k | medium (prompt wiring) |
| 3.2 Visual editor | ~13k | low |
| 3.3 Hidden app builder | ~3k | low |
| 3.4 Legacy pipeline and dead code | ~5k | low |
| 3.5 Releases and environments | ~3k | medium (editor publish) |
| 3.6 Interfaces and AskBar | ~3k | low-medium |
| 3.7 API tester | ~1k | low |
| 4 Fix the app builder | net −3k | medium |
| 5 v0-style flow | net −2k | medium |
| 6 AI workflow builder | net −6k | medium |

## 10. Open questions

1. Should the knowledge tables, and later the release and interface tables, be dropped from the database, or only left unused?
2. Should the plan/clarify UI be deleted now or switched off first? The plan above switches it off first.
3. Should the app backend stay as generated modules, or move to the Retool-style named queries? Decide after phase 2.
