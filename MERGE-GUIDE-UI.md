# Merge guide: UI

This branch (`revert-to-5684ba5`) has the latest studio UI updates. This guide lists what changed and where, so they can
be merged into our code. The backend side is in `MERGE-GUIDE-BACKEND.md`. Tests are not covered.

- **From**: `0fc9039`, "Studio: real frosted glass on every app page and the workflow editor"
- **To**: `4f776cb`, "Studio + builder: one-app overview, plan in the app builder, icy frost backdrop"

All paths are under `apps/workflow-studio/src/`.

## How to merge

1. **Added files (A)**: bring them over as they are: `git checkout 4f776cb -- <path>`.
2. **Modified files (M)**: merge only the changes, keeping ours: `git diff 0fc9039 4f776cb -- <path>`, then apply the hunks
   (`git apply -3` first, by hand where it fails). The notes below say what each change does.
3. **Deleted files (D)**: delete them, then fix any imports of them. The replacement for each is named below.
4. Go in section order: API layer → feature folders → app builder → routes and shell → styling.
5. Check: `npx tsc -b` reports 0 errors.

## 1. API layer

- **M** `shared/api/endpoints.ts`: adds `schedulesApi`; modules `create`/`update` accept `tables`.
- **M** `shared/api/types.ts` (+22): `ModuleSchedule`, `DeclaredTable`, and related types.
- **M** `shared/api/queryKeys.ts` (+1).

## 2. New feature folders (all A; bring over whole)

- `features/plan/`: the plan flow (questions → document → approval), build timeline, build view, project docs.
- `features/build-studio/`: the live build view (code pane, preview pane, ready card).
- `features/code/`: the project file tree, viewer/editor, history, change log.
- `features/connections/`: connect a database, schema browser, sources picker.
- `features/project/overview/home/` and `overview/lib/{pulse,stats,useWidth}.ts`: the new one-app Overview. It has:
  - a launcher row of app and workflow preview cards (`AppSketch`);
  - a Usage band (`Widgets`);
  - a Data section;
  - Builds.
- Shell pieces in `features/project/`:
  - `components/HomeNav.tsx`, `WorkbenchIndex.tsx`, `WorkbenchSwitcher.tsx`
  - `hooks/useMissingCount.ts`, `useProjectPulse.ts`
  - `lib/entities.ts`, `lib/places.ts`
- `features/backend/BackendIndex.tsx`, `features/workflows-list/workflowMeta.ts`.
- `features/workflow-editor/components/ndv/extensions/PostgresConnectionExtension.tsx`.

## 3. App builder (`features/app-builder/`)

- **A** components: `ChecksChip.tsx`, `NewAppBuilder.tsx`, `ViewAsMenu.tsx` (preview the app as a given user).
- **A** lib: `autotest.ts`, `chatEvents.ts`, `clientGate.ts`, `previewErrors.ts` (collects preview errors for "Fix with AI"), `previewUsers.ts`, `restoreChat.ts`.
- **M** `components/AppBuilderShell.tsx` (+151): Plan tab (`?view=plan` deep link), auto-fix of preview errors, checks chip.
- **M** `components/AppPreviewPanel.tsx` (+70): View-as menu, and an errors bar with "Fix with AI".
- **M** `components/AppBuilderChatPanel.tsx`, `hooks/useAppBuilderChat.ts`, `stores/appBuilderChatStore.ts`: chat events and chat restore.
- **M** `sandbox/IframeSandbox.tsx`, `sandbox-runtime.ts`, `requestPolicy.ts`, `esbuild-bundler.ts`, `bridge.ts`: error
  reporting from the iframe, kit bundling, and the request policy for the preview.
- **M** small: `ProjectAppBuilder.tsx`, `ApiTesterPanel.tsx`, `PublishDialog.tsx`, `VersionHistory.tsx`, `panels.tsx`.

## 4. Routes and project shell (no sidebar any more)

- **M** `app/routes/project.tsx`:
  - new routes `backend` (BackendIndex), `build/$build` (BuildView) and `code` (CodeView);
  - `plan` now redirects home;
  - the old `apps` and `workflows` list URLs redirect to the Overview.
- **D** `features/project/components/ProjectOutline.tsx`: replaced by `HomeNav` (the way home and the jump menu, in the top bar).
- **D** `features/workflows-list/WorkflowsListPage.tsx`, `WorkflowCard.tsx`: workflows are listed on the Overview now; the helpers moved to `workflowMeta.ts`.
- **M** `ProjectLayout.tsx`: mounts `WorkspaceOverlays` (moved here from WorkspaceLayout), and fixes a bug where the slug redirect sent deep links to the Overview.
- **M** `WorkspaceLayout.tsx`:
  - the outline/rail is gone; views sit in a full-window sheet 8px from the window edges;
  - shortcut `g h` goes home;
  - the `[` and `g w` shortcuts are gone.
- **M** these files are updated to fit the new shell: `ProjectTopBar.tsx`, `OverviewView.tsx`, `SetupGuide.tsx`,
  `setup/parts.tsx`, `overview/{Backend,Releases}Tile.tsx`, `BuildingBlocks.tsx`, `story/*`.
- **M** small edits elsewhere:
  - `backend/{BackendView,NewModuleDialog,useSaveModule}`, `data/DataView`, `knowledge/*`;
  - `api-catalog/ApiCatalogView`, `ai-builder/*`;
  - `releases/ReleaseSheet`, `runs/RunSheet`, `settings/SettingsSheet`;
  - `workflow-editor/`: the page, bottom panel, proposal pill, env modal, logs, history, node creator, navbar, NodeExtensions.

## 5. Styling: light background, glass, icons

All of it is in **M** `index.css` (+110/−31), plus a few one-line changes to shared components. The glass panes themselves are unchanged.

- **Icy frost background**: the light background gradient (the block under "Every app page but the landing … icy frost")
  replaces the blue/violet colour patches with a near-neutral, cool off-white that has a soft grey middle.
- **White cards inside glass**: the `:root` tokens `--well`, `--inner-fill-hover`, `--raised-fill`, `--raised-ring`,
  `--raised-contact` and `--inner-elevation` become near-solid white (light mode) or faint white (dark mode). The old
  `--glass-fill*`, `--chrome-*` and `--glass-blur` overrides in that block are removed, so they fall back to the base glass values.
- **Icons**: `@utility icon-tile` becomes a small glass "gem" (tinted gradient, top gleam, lit rim, hue glow).
  `shared/components/ui/surface.tsx` adds `icon-tile` to header icons that have no hue.
- **Glass icon buttons**: a new `@utility glass-bubble` is applied automatically to ghost icon buttons in headers and
  strips (the selector right after the utility). In `ui/button.tsx`, the `outline` and `secondary` variants become
  `glass-bubble text-foreground`, and the button now sets `data-size`, which that selector needs.
- **Stepper**: new `@utility glass-orb` / `glass-orb-clear`; `ui/stepper.tsx` uses them for the done, current, pending and error states.
- **Primary buttons**: `.brand-fill` gets a flat brand gradient and a glow.
- **Badges**: a hairline edge in their own hue.
- **Etched text on glass**: a `text-shadow` on text inside `.glass, .tile, .glass-editor, .glass-chrome, .glass-strip,
  .glass-overlay, header`, turned off for solid fills, inputs and code.
- Small: `ui/sheet.tsx`, `ui/code-editor.tsx`, `ai-elements/message.tsx` (one-line changes that follow the above).

## Not included yet

The visual editor for generated apps (`features/app-builder/visual/`, in-place editing in the preview, the theme panel)
isn't on this branch yet.
