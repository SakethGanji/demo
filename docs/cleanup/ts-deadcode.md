# workflow-studio + app_kit dead-code report

Tools: knip@5 (CLI config in scratch dir, entry = src/app/main.tsx + tests + e2e + scripts; routes are code-registered via TanStack `createRoute` in src/app/routes/*.tsx, all reachable from main.tsx, so no route false positives), plus hand grep/python import-graph checks. A scratch copy of src (not the repo) was used to simulate deletions. Repo untouched. visual/ was ignored except where noted.
Raw outputs: scratchpad/cleanup/{k.txt, classified.txt, kA.txt, kB.txt, api.py, kit.py}.

## Summary

| Check | Count | Removable lines |
|---|---|---|
| Unused files | 33 (all verified by import-path grep) | 3,849 |
| Truly dead exports (not even used in own file) | ~60 exports + 9 types | ~400 (est.; prompt-input.tsx alone ~350) |
| Exports/types that only need `export` dropped (used in own file) | ~170 exports + ~195 types | 0 lines (cosmetic) |
| Unused deps | 5 (+1 devDep alias) , plus 2 false positives | package.json only |
| Dead API-client methods | 11 methods + 7 query-key/invalidate entries (+1 more after dead hook removal) | ~90 |
| Unused shared components | 2 files (task.tsx, tool.tsx) + many unused sub-parts of vendored ai-elements/ui | ~260 files-level, ~500 sub-parts |
| Duplicates | 8 clusters | ~60 |
| Kit (app_kit) | 1 dead function (`fetchBlob`), rest used or documented | 8 |
| Only-visual-used (what remains if visual/ is deleted) | small, see section 7 | ~20 + 2 files of glue |

Headline: about 3,850 lines are in files nothing imports (the old project "overview tiles", "story" river/map, and legacy projects hooks). That is the big safe win.

## 1. Unused files (33, 3,849 lines) - confidence HIGH

Verified: knip + `grep "from .*<path>"` finds no importer outside the cluster; no `lazy()`/dynamic import references them (only lazyRouteComponent in indexRoute.ts -> landing and 4 lazy in WorkflowEditorPage, all unrelated). Removing all 33 together leaves no new unused files (checked in scratch copy).

Project overview tiles (old design), lines in brackets:
- src/features/project/overview/ActivityTile.tsx (87), BackendTile.tsx (142), BuildingBlocks.tsx (198), HeroTile.tsx (147), RecentRunsTile.tsx (73), ReleasePath.tsx (64), ReleasesTile.tsx (54), SecretsTile.tsx (94), StatTiles.tsx (117), tiles.tsx (40), lib/useWidth.ts (17)
- src/features/project/hooks/useMissingCount.ts (11)
Project "story" view (all only reference each other; StoryOverview.tsx is the root and nobody imports it):
- src/features/project/story/components/{KindIcon 11, StoryFocus 406, StoryHeader 103, StoryMap 198, StoryOverview 185, StoryRiver 193, StoryShip 106}.tsx
- src/features/project/story/hooks/{useMapWires 151, useStoryActions 56, useStoryData 106}.ts
- src/features/project/story/lib/{model 371, sentence 108}.ts
Legacy projects page hooks:
- src/features/projects/hooks/{useAppActions 262, useApps 31, useLatestExecutions 34, useWorkflowActions 111}.ts
- src/features/projects/lib/formatDate.ts (19)
Other:
- src/features/knowledge/components/KnowledgeHomeCard.tsx (63)
- src/features/workflows-list/workflowMeta.ts (31; only imported by story files)
- src/shared/components/ai-elements/task.tsx (87), tool.tsx (173)

Second-order (dead once the above go, no new files but exports): `useRunStats` (project/hooks/useRunStats.ts, only story/overview used it; `useRunDaily` in same file stays), `byRecent` (project/lib/format.ts), `groupRoutes`/`AUTH_LABEL` (project/lib/routes.ts), `durations/niceCeil/scheduleTicks/decadeTicks/logBins/attentionSize/shortMs` (overview/lib/stats.ts, ~48 lines), `workflowsApi.runAdhoc` after `useExecuteWorkflow` is removed.

## 2. Unused exports / types

knip: 259 unused exports, 240 unused exported types (before dead-file removal). Classification by own-file reference count:
- DEAD (zero uses anywhere, including its own file): 60 exports, 9 types - safe to delete the code (confidence HIGH, knip + word grep over src/e2e).
- LOCAL (used only inside its own file): ~170 exports, ~195 types - just drop the `export` keyword (cosmetic, low value). Includes all `*Route` consts in src/app/routes/project.tsx (the routes are wired through the route tree, not by name), constants like POLL_*_MS, starfield SKY/SHEET/DUST/TEMPS, shadcn sub-parts in ui/*.
- OTHERFILE (63): name matches an unrelated symbol elsewhere (e.g. `SLUG_RE`, `Rect`, `clean`, `median`); these are really unused exports too. Real exceptions: none found that are runtime-used.

Truly dead code (delete body), approx lines:
- src/features/workflow-editor/hooks/useWorkflowApi.ts:188 `useExecuteWorkflow` (~106) -> then `workflowsApi.runAdhoc` also dead
- src/features/workflow-editor/hooks/useSubworkflows.ts:81 `usePublishWithImpact` (~18)
- src/features/workflow-editor/lib/tests.ts:348 `failureCountsByNode`, :422 `EMPTY_DEFAULTS` (~8)
- src/features/backend/docStore.ts:114 `persistedNewModules` (~16)
- src/features/interfaces/lib/interfaceDraft.ts:97 `hasComponents`, :107 `interfaceSignature` (~8); types.ts `InputSchema`
- src/features/project/components/TopBar.tsx:71 `CrumbSep`; WorkspaceLayout.tsx:17 `panelCls`; project/ProjectContext.tsx:24 `useOptionalProjectCtx`; project/home/art.tsx:157 `TemplateArt`
- src/features/runs/lib/runLabels.ts:21 `RUN_STATUSES`
- src/shared/lib/starfield.ts:127 `starDustDataURL`
- Types: workflow-editor/lib/shapes.ts `NodeContract`, subworkflowsApi.ts `WorkflowInUseDetail`, shared/api/types.ts `ReleaseDryRun`
- Vendored shadcn "ai-elements" parts never used: prompt-input.tsx (~28 exports: ActionAddAttachments/Screenshot, Body, Header, ActionMenu*, Select*, HoverCard*, Tab*, Command* -> roughly lines 417-470 and 945-1450, ~350 lines), code-block.tsx CodeBlockLanguageSelector* (7 pieces, ~60 lines), message.tsx MessageAction + MessageBranch* (~110 lines), conversation.tsx ConversationDownload, file-tree.tsx FileTreeActions. Confidence MEDIUM: vendored library files; deleting is safe but diverges from upstream.
- shadcn ui unused sub-parts (LOCAL/dead, ~0 impact): AlertDialog{Media,Overlay,Portal,Trigger}, DropdownMenu{Portal,RadioGroup,RadioItem,Shortcut,Sub*}, Select{ScrollUp/Down,Separator}, Sheet{Trigger,Close,Footer}, Dialog{Close,Overlay,Portal}, InputGroupText, ButtonGroupSeparator, buttonGroupVariants, PopoverClose, DiffStat, hueText, ToolbarGroup.

Duplicate re-export aliases (knip "Duplicate exports"): `MethodChip = MethodBadge` (shared/components/api/MethodBadge.tsx:41; MethodChip is used in 4 files, MethodBadge elsewhere - pick one name), `suggestedAnswers = initialAnswers` (features/plan/planLogic.ts:103; only checklist.test.ts uses the alias).

## 3. Dependencies (apps/workflow-studio/package.json)

Unused, no import/config reference anywhere in src, e2e, scripts, vite/vitest/eslint config, index.html (HIGH):
- @unocss/core (:39), @unocss/preset-wind (:40)
- react-markdown (:54), remark-gfm (:56) (markdown is rendered by streamdown)
- tailwindcss-v3 (alias of tailwindcss@3, :79). Note engine's app_bundler_tailwind.mjs resolves `tailwindcss`, `@tailwindcss/node`, `@tailwindcss/oxide` from studio node_modules (transitives of @tailwindcss/vite): keep @tailwindcss/vite.
- immer (:42) - no import anywhere (grep -w immer in src only hits "shimmer"); lock shows it only as a peer of zustand. MEDIUM-HIGH (knip did not flag it).
False positives (keep): tw-animate-css (`@import` in src/index.css:2), @tailwindcss/typography (`@plugin` in src/index.css:3).
Single-use deps that could be dropped with a few lines of code (LOW value):
- @radix-ui/react-visually-hidden: NodeDetailsModal.tsx:46 (replace with `sr-only` class)
- nanoid: ai-elements/prompt-input.tsx:52 (2 calls; crypto.randomUUID) and that file's dead parts
- use-stick-to-bottom: ai-elements/conversation.tsx only
- react-resizable-panels: NodeDetailsModal.tsx only; @codemirror/merge: backend/DiffDialog.tsx only; @lezer/highlight: ui/code-theme.ts only; `ai`: type-only import `ChatStatus` in chat/ChatInput.tsx:2 (heavy package for one type)
- @streamdown/{cjk,code,math,mermaid}: all four used, only in ai-elements/message.tsx (bundle weight, not dead)
Not declared but imported: @babel/types, @babel/parser (all inside app-builder/visual/ only; undeclared transitive deps) - vanish with visual/.

## 4. Unused API client methods (src/shared/lib/api.ts, src/shared/api/endpoints.ts, queryKeys.ts) - HIGH
Checked with `obj\s*.\s*method` regex across src+e2e (handles multi-line chains); false hits removed (apiSpecsApi.mocks, appApiApi.previewToken, modulesApi.codeMap, workflowsApi.check are used).
- api.ts:209 `workflowsApi.importN8n`; :368 `appsApi.getVersion`; :372 `appsApi.createVersion`; :434 `nodesApi.get`
- endpoints.ts:77 `projectsApi.drift`; :139 `routesApi.get`; :144 `routesApi.usedBy`; :154 `specApi.diff`; :181 `tablesApi.get`; :296 `releasesApi.diff`
- queryKeys.ts: `qk.route` (:32), `qk.usedBy` (:33), `qk.apiMocks` (:41), `qk.projectDrift` (:67), `qk.previewToken` (:70), `invalidate.appSaved` (:110), `invalidate.tasksChanged` (:136), `invalidate.interfaceChanged` (:137)
- Becomes dead after removing useExecuteWorkflow: `workflowsApi.runAdhoc` (api.ts)
- Unused API types may follow once those are gone (e.g. ImportReport). No api method is used only by visual/.

## 5. Unused components (shared/components, ui)
- Whole-file: ai-elements/task.tsx (87), ai-elements/tool.tsx (173): zero importers.
- ui primitives: every file in shared/components/ui has at least one importer. button-group, hover-card, scroll-area, separator are only imported by ai-elements files (message.tsx, prompt-input.tsx, suggestion.tsx, button-group) - they stay alive only through the vendored chat library; if the dead prompt-input/message parts are trimmed, hover-card and button-group may become removable. MEDIUM.
- Sub-part exports: see section 2.
- No shared/ui primitive depends solely on visual/ (all have non-visual importers).

## 6. Duplicate implementations
- `slugify`: app-builder/lib/appModel.ts:37 vs interfaces/lib/interfaceDraft.ts:14 (identical regex; second returns '' if <2 chars) plus two different `SLUG_RE`s (appModel:45 `^[a-z0-9](?:-?[a-z0-9])*$`, interfaceDraft:5 `^[a-z0-9][a-z0-9-]{1,62}$`). Merge into one shared slug util. MEDIUM.
- `median`: identical in project/hooks/useRunStats.ts:29 (private), overview/lib/pulse.ts:122, story/lib/model.ts:89 (dead file). Keep pulse.ts.
- `formatBytes` x4: app-builder/ApiTesterPanel.tsx:160, workflow-editor/execution-logs/ExecutionLogsPanel.tsx:182, datasets/lib/datasets.ts:8, story/lib/model.ts:171 (dead).
- `statusTone` x3 (different semantics: TestPanel:88 http status, RunSheet:88, connectionsLogic:6 - not true duplicates).
- Time/relative formatting: `timeAgo` project/lib/format.ts:18, `relativeTime` code/codeLogic.ts:136, `formatRelative` projects/lib/formatDate.ts (dead file), `formatTime` in VersionHistory.tsx:29 and ExecutionLogsPanel.tsx:188 - consolidate.
- `plural` helper x5 (ai-builder/components/BuildProgress.tsx:16, lib/buildLog.ts:38, lib/understanding.ts:16, apps/lib/appState.ts:144, workflow-editor/ai-chat/ChatCards.tsx:11), `sleep` x2 (ai-builder stageBridge.ts:48, useAppBuild.ts:55), `reduced` (prefers-reduced-motion) x5 (backend/ui/flowHooks.ts:3, RequestPath.tsx:24, ai-builder TypedText.tsx:6, ui/roll.tsx:10, art/pulse.ts:11), `METHOD_VAR` map x5 (project/home/CardDiagram.tsx:17, home/art.tsx:8, backend/ui/RequestPath.tsx:13, data/LineageArt.tsx:14, shared/components/art/SystemDiagram.tsx:26).
- `useWidth` (dead) vs shared/hooks/useViewport - n/a once dead file removed.
- `PlanColumn` type defined in ai-builder/apps/appTypes.ts and plan/planTypes.ts; `CodeMapResult` in backend/code-map/types.ts and shared/api/codeMapTypes.ts; `BaseChatMessage`/Render* types re-declared in chat/index.ts and chat/types.ts. LOW-MEDIUM.

## 7. What else becomes unused when app-builder/visual/ is deleted
Scratch copy with visual/ removed (after dead-file removal): no further unused FILES; unresolved imports to visual from 6 non-visual files that must be edited in the same change:
- components/AppBuilderChatPanel.tsx:41 (askAi), AppBuilderShell.tsx:64-66 (flag, history, askAi), AppPreviewPanel.tsx:31-36 (useVisualEdit, history, VisualEditPanel, CanvasToolbar, ThemePanel, deviceStore), hooks/useAppBuilderChat.ts:7,20 (askAi, history), sandbox/visual-edit-runtime.ts:12 (selection)
Becomes dead as a result:
- src/features/app-builder/sandbox/visual-edit-runtime.ts (whole file, visual-only runtime), hooks/visualSave.ts, hooks/flushBeforeSend.test.ts partly (mocks visual)
- exports: `Sides` (sandbox/bridge.ts), `VisualEditBridge`, `VisualEditPort` (sandbox/IframeSandbox.tsx); `JSX_DEV_SHIM`, `THEME_PATH` and the jsxDEV source-tagging option in sandbox/esbuild-bundler.ts:86-98,184 (only the visual editor maps source locations)
- `@babel/types`/`@babel/parser` imports (undeclared deps) and ~11 vitest suites inside visual/
- Engine side: app_kit has 91 `data-vcomp` attributes and ~268 `@control/@min/@max/@options` JSDoc tags consumed only by visual/registry/extract.ts; engine `editability_lint.py`, theme.css "visual theme panel" loading (app_bundler.py:148) and the editable-shape prompt block (oneshot_builder.py:467-480, :1995 "editability (visual-editing trial)") are visual-only. Not code-dead today (no importer break) but dead weight. MEDIUM, owner decision.
- No shared ui primitive or api method is orphaned.

## 8. Generated-app kit (apps/workflow-engine/src/services/app_kit/*.tsx, 4,266 lines)
Method: all `export` names compared against engine src/tests/templates/scripts/knowledge plus studio src (platform_index, oneshot prompts, docs).
- Only `fetchBlob` (hooks.ts:340, ~8 lines) has zero references anywhere, including in the kit and prompts - dead (HIGH).
- 28 other exports have no mention outside the kit but are used inside it or are props/types users import (e.g. JsonEditor documented in ui.tsx/index.ts header, fmtClock/TERMINAL_STATUSES used by live.tsx/hooks.ts, *Props/ChartSeries/CalendarEvent/KanbanColumn types). Not dead; at most unexported-able. JsonEditor is not named in platform_index/oneshot prompts beyond the index header comment - verify it is in the kit prompt catalog before keeping.
- Kit imports only `react`; it needs no studio npm dependencies other than what the bundler provides.
