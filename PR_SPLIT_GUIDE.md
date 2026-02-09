# PR Split Guide

The 102-file change on branch `new` (commit `7d0fa0f`) has been split into 5 stacked PRs. Each branch builds independently with `vite build`.

## Branches

The `new` branch has the complete unsplit commit and is the source of truth. The 5 PR branches below are stacked — each includes all changes from previous branches.

```
ed80ce7 (base: "add memory types")
  └── pr/backend-and-types   (42 files, +1623/-1781)
        └── pr/editor-layout      (6 files, +455/-28)
              └── pr/canvas-nodes     (16 files, +1675/-796)
                    └── pr/ndv-overhaul   (17 files, +1017/-713)
                          └── pr/workflows-and-ai (23 files, +885/-1300)
```

`pr/workflows-and-ai` is byte-for-byte identical to `7d0fa0f` on `new`.

---

## PR 1: `pr/backend-and-types`

**Backend refactor + frontend types & shared UI (42 files)**

Backend:
- New `utils/serialization.py` and `utils/memory.py` — centralized shared utilities
- New `base_memory.py` base class; all 11 memory nodes refactored to inherit from it
- Cleaned up duplicate serialization from mongodb, neo4j, postgres integration nodes
- Extended schemas with `label`, `waypoints`, `pinned_data` fields
- Updated workflow service, repository, routes for new fields

Frontend:
- New `backendTypes.ts` — canonical backend API type definitions
- Refactored `api.ts` to use centralized types
- New shared UI components: `alert-dialog.tsx`, `skeleton.tsx`
- Updated 10 existing shared UI components (button, badge, card, sidebar, etc.)
- CSS theme overhaul (`index.css`)
- Removed `use-mobile.ts` hook

---

## PR 2: `pr/editor-layout`

**Centralized editor layout system (6 files)**

- New `editorLayoutStore.ts` — unified panel management with localStorage persistence
- New `BottomPanel.tsx` — container for execution logs and UI preview tabs
- Updated `ndvStore.ts` with localStorage persistence for panel sizes
- Updated `useKeyboardShortcuts.ts` for new store
- Updated `__root.tsx` route and `index.html`

---

## PR 3: `pr/canvas-nodes`

**Canvas redesign & node creation (16 files)**

- New `createNodeData.ts` — centralized node data creation with proper defaults
- New `WorkflowSVG.tsx` — workflow preview rendering component
- Refactored `WorkflowCanvas.tsx` with ReactFlow v11 API and edge validation
- Overhauled `WorkflowEdge.tsx` with custom rendering and waypoint support
- Updated all node components (WorkflowNode, SubworkflowNode, StickyNote, SubnodeNode, AddNodesButton)
- Refactored `NodeCreatorPanel.tsx` and `NodeItem.tsx` to use `editorLayoutStore`
- Updated `workflowStore.ts` with new node/edge management logic
- Enriched `nodeConfig.ts` with `SUBNODE_SLOT_NAMES`
- Refactored `editor.tsx` route with new panel layout and node type loading
- Added trigger deletion confirmation in `NodeContextMenu`

---

## PR 4: `pr/ndv-overhaul`

**Node Details View + transform utilities (17 files)**

- Overhauled `DynamicNodeForm.tsx` with inline validation, debounced onChange, field errors
- Refactored `InputPanel.tsx` and `OutputPanel.tsx` with new display modes
- Updated `NodeDetailsModal`, `NodeSettings`, `ExpressionEditor`
- Updated `RunDataDisplay`, `SchemaDisplay`, `FilePathField`, `WorkflowSelectorField`
- Expanded `nodeIcons.ts` mappings and updated `nodeStyles.ts`
- Refactored `workflowTransform.ts` with new utilities (`findUpstreamNodeName`, `buildNameToIdMap`)
- Updated `useWorkflowApi.ts` and `useExecutionStream.ts` to use new transform utilities
- Updated `workflow.ts` types
- Deleted deprecated `graphUtils.ts`

---

## PR 5: `pr/workflows-and-ai`

**Workflows list overhaul + AI operations + cleanup (23 files)**

Workflows list:
- Redesigned `workflows.tsx` route with new layout and filtering
- New `WorkflowListRow.tsx` for list view
- Redesigned `WorkflowCard.tsx` with new actions
- New `useLatestExecutions.ts` and `useWorkflowActions.ts` hooks
- Simplified `WorkflowThumbnail.tsx` and `useWorkflows.ts`

Editor:
- Refactored `WorkflowNavbar.tsx` with updated toolbar
- Refactored `ExecutionLogsPanel.tsx` from floating button to bottom tab
- Refactored `aiOperationApplier.ts` to use `createNodeData`
- Updated `AIChatSidePanel`, `WorkflowPickerDialog`, `useAIChat`, `aiChat` types

Cleanup:
- Deleted `TestInputPanel.tsx`, `UIPreviewPanel.tsx`, `UIPreviewSidePanel.tsx`, `ui-preview/index.ts`
- Deleted `nodeCreatorStore.ts`
- Finalized `uiModeStore.ts` (removed old exports)
- Removed backward-compat stubs (`SidebarInset`, `getNodeGroupFromType`)

---

## Reviewing

To review a specific PR's incremental changes:
```bash
# See only what a specific PR adds (not cumulative)
git diff pr/backend-and-types~1..pr/backend-and-types
git diff pr/editor-layout~1..pr/editor-layout
git diff pr/canvas-nodes~1..pr/canvas-nodes
git diff pr/ndv-overhaul~1..pr/ndv-overhaul
git diff pr/workflows-and-ai~1..pr/workflows-and-ai
```

To build-test any branch:
```bash
git checkout pr/<branch-name>
cd apps/workflow-studio && npx vite build
```

## Notes

- Two backward-compat stubs were added in intermediate PRs to keep builds working:
  - `SidebarInset` in `sidebar.tsx` (PR1) — removed in PR5 when consumers update
  - `getNodeGroupFromType` in `nodeStyles.ts` (PR4) — removed in PR5 when consumers update
- Some files shifted between PRs vs the original plan to resolve import dependencies (e.g., `editor.tsx`, `WorkflowSVG.tsx`, `nodeConfig.ts` moved to PR3; `useWorkflowApi.ts`, `useExecutionStream.ts` moved to PR4)
