# Implementation plan: Command Studio design in the studio app

**Written 2026-10-16, for the next session.** This plan says how to roll the approved design (`DESIGN.md`, commit 5040b42) out to every page of `demo/apps/workflow-studio`. It builds on `HANDOFF.md` (the phases, data gaps and guardrails) and replaces its §2 ordering with the session shape below. Facts here come from three surveys of the app on 2026-10-16; file paths are relative to `apps/workflow-studio/src/`.

## What the owner asked for
- **Update the whole UI**, every page, to the new look: glass on a starfield, one design in light and dark.
- **Do it at the core first.** The design standard, `index.css`, the Tailwind recipes, the shared components and the glass styling are built once, centrally. Pages then use them; they don't restyle themselves.
- **The basic UI components and the chat components are part of the core**, not left to each page.
- **Pages that have a mockup match it exactly** (`design/*.html`), including the features added during the redesign (release stepper, AI change flow, route picker, stack trace, table tools, 30-day activity, snippet and so on).
- **Workflow editor: keep the nodes.** Keep the current 64×64 square nodes with an icon chip, the label underneath, left/right handles and all the logic. Only give them the new look (surfaces, colours, edges, handles, selection and run states). Don't adopt the mockup's 220px horizontal cards.
- **Session shape:** the lead session (Opus) writes the standard and the core files. **Sonnet agents** then implement the pages, and separate Sonnet agents validate each page against its mockup.
- **Aim:** the most modern look, without losing usability.

## Session shape

| Stage | Who | What | Ends when |
|---|---|---|---|
| 0. Baseline | Lead | Run the type check, unit tests and e2e; record what's already red | Known baseline written to this file |
| A. Standard and core | Lead (Opus), sequential | Tokens, recipes, fonts, sky, shared components, chat layer, shell, the design check | `/__design` page matches `design/index.html` in both themes; all green |
| B. Pages | Sonnet agents, one per work package, in git worktrees, at most 5 at a time | Each package restyles its pages using only the core | Each package's own checks pass |
| C. Validation | Sonnet agents, one per package, read-only | Screenshot the page and its mockup in both themes and list every difference | No high-severity differences left, at most 2 fix rounds |
| D. Integration | Lead | Merge worktrees, run everything, update the e2e colour checks, final screenshots | Everything green; gallery of before/after |

**Why the lead does the core alone:** every page depends on it, and parallel agents editing `index.css` or `ui/*` would conflict. Page agents may **not** edit core files; if a page needs something new from the core, the agent asks the lead in its report and the lead adds it.

**Running the agents.** Use the Agent tool with `model: "sonnet"` and `isolation: "worktree"` for implementers, and `model: "sonnet"` with no worktree for validators. If you'd rather run them as one orchestrated workflow, say "use a workflow" in the kickoff prompt; the default workflow size is under 10 agents, so run stage B in two batches of five, or raise the size in `/config`.

---

## Stage 0. Baseline
- Commands: `npx tsc -b`, `npm run test:unit`, `npm run test:e2e` (Playwright, 1 worker, light scheme, 1440×900) in `apps/workflow-studio`.
- There are 10 e2e specs with 50 tests. `HANDOFF-UI-FINISH-2.md` lists 05, 06 and 07 as red; that note is older than the code map and preview work, so re-run rather than trust it.
- Record the results here. Don't restyle on top of red tests you don't know about.

**Baseline recorded 2026-10-16 (before any restyle):**
- `npx tsc -b`: clean. `npm run test:unit`: 4 files, 37 tests, all pass.
- First e2e attempt: every test red because `@tailwindcss/browser` (in package.json) wasn't installed; `npm install` fixed it.
- `npm run test:e2e`: 51 tests, **49 passed, 2 failed**:
  - T22 (`05-builder.spec.ts:91`): after reload the route picker shows `refundStats` unchecked although the stored `route_ids` are right.
  - T40 (`07-workflows-responsive.spec.ts:9`): the UI shows success, but `GET /runs` still reports the run as `running` when read immediately (a race between the stream and the DB write).

## Stage A. Standard and core (lead)

### A1. The design standard
Write `apps/workflow-studio/DESIGN-STANDARD.md`, short and for agents. It is the coding version of `DESIGN.md`:
- Which component or utility to use for each surface: page → `Sheet`, card/tile → `Tile` (`tile`), recessed text area → `Well` (`well`), control that holds a value → `field` / `recess`, chosen segment → `raise`, overlay → `glass-overlay`.
- Banned in feature code: raw palette colours (`zinc-*`, `slate-*`, `blue-500` and so on), hex/rgb literals, `bg-white` / `bg-black`, `border` or `ring-1` as a box edge, `rounded-xl` / `rounded-4xl` outside the radius tokens, gradients inside boxes.
- Allowed: `border-b border-hairline` for row dividers, status and method colours through their tokens, `--hue-*` on icons and small tints.
- Every new interactive element gets a `data-testid`; never rename an existing one.

Add a check that enforces it: `scripts/design-check.mjs`, run as `npm run design:check`. It greps `src/features/**` and `src/shared/**` for the banned patterns and prints file:line. Allow-list `index.css`, `landing.tsx` until stage B reaches it, and the sandbox runtime (the generated app's own colours, see the e2e note in D). Page agents must leave their package's files clean.

### A2. Tokens, recipes, fonts
- Replace the `:root` and `.dark` token blocks in `index.css` (L7–251) with `design/handoff/tokens.css`. Keep the literal `:root {` and `.dark {` blocks; `shared/theme/__tests__/contrast.test.ts` parses them. Update that test if a token it reads moves.
- **Keep, but restyle,** `--node-*` (L83–108 light, L203–228 dark), `--sticky-*` and `--chart-*`. They aren't in `tokens.css`. `--node-bg` becomes the glass fill, the borders become the glass edge, the icon chips become raised (AI gets the brand gradient), and the group accent colours move to the calmer `--hue-*` values.
- Append `design/handoff/tailwind-theme.css` (the `@theme inline` block and the `@utility` recipes). It already compiles with this Tailwind (checked with `@tailwindcss/node`). Merge with the existing `@theme inline` (L253–327) rather than duplicating names.
- Fonts: self-host Inter v4 (`Inter Var`, opsz) and JetBrains Mono from `design/handoff/fonts/` in `public/fonts/`, and drop the Google Fonts link in `index.html:10`. Body features become `cv01`/`ss03` (today `cv02/03/04/11`).
- Theme switching: `shared/components/theme-provider.tsx` already toggles `.dark` on `<html>`; keep it.

### A3. Space
- `shared/components/space/SpaceBackground.tsx`: the fixed sky canvas (`paintStarfield(SKY)`, opacity `var(--sky-opacity)`), the `nebula` utility on `<body>`, repaint on resize and theme change, `aria-hidden`, `pointer-events: none`. Mount it in `app/routes/__root.tsx`.
- `shared/components/space/HomeSky.tsx`: `paintHomeSky` on an absolute canvas the height of the page, for Projects home (and the landing page if it's in scope). It hides the SKY canvas and the nebula on that page.
- Port `design/handoff/starfield.js` to `shared/lib/starfield.ts` unchanged apart from types.
- Every full-page background in the app must become transparent so the sky shows: `WorkflowCanvas.tsx` sets `className="bg-background"` on React Flow, `WorkspaceLayout`'s sheet, `landing.tsx`'s hard-coded `#050505`.

### A4. Shared components (`shared/components/ui/*`, 33 files, Base UI)
Restyle in place, keeping every export, prop and `data-testid`:

| Group | Files | Change |
|---|---|---|
| Overlays | dialog, alert-dialog, sheet, popover, dropdown-menu, select (popup), hover-card, command, tooltip | `glass-overlay`; drop `ring-1 ring-foreground/10` and `border`; backdrop `bg-black/10` → `--scrim`. Sheets 12px from the viewport edge. Tooltip: glass, not inverted |
| Fields | input, textarea, input-group, chips-input, select trigger, checkbox, switch | `field` (recessed, no border, 2px focus ring) |
| Controls | button (7 variants), badge (12 variants), button-group, segmented, tabs, editor-tabs, toolbar, kbd | Buttons flat fills, outline = recess; badges flat tints at `--r-chip`, no `rounded-4xl`; segmented = recessed track + raised sliding thumb; tabs with a sliding indicator; kbd recessed (remove `bg-white/15`) |
| Data | table, skeleton, separator, scroll-area, status-dot, empty-state | Row dividers `--hairline`, row hover fill, thin scrollbars |
| Code | code-editor, json-viewer | One shared CodeMirror theme from `--code-bg`, `--code-gutter`, `--syn-*` with no hex fallbacks; json-viewer stops calling `--chart-2` "purple". Replace the six local `EditorView.theme` blocks (BackendView, DiffDialog, TestPanel, ApiTesterPanel, ReleaseSheet, RunSheet) with it |

New shared components (each with a story on `/__design`):
- `Tile`, `Well`, `PageHeader` (the 26px title, meta row and actions; there's no shared header today), `SectionHeader` (lift it from `features/settings/common.tsx`).
- `Tip` and one `TooltipProvider delay={500} timeout={300}` in `__root.tsx` (there's no global provider today).
- `Roll` (rolling digits, 20ms right-to-left stagger), `Sparkline`, `ActivityGrid` (30 days).
- `Snippet` (URL / curl / Python with copy), `MethodChip` (wrap `shared/components/api/MethodBadge.tsx`), `RoutePicker` combobox (searchable, grouped by module).
- `Toaster`: sonner in `__root.tsx` with `toastOptions` for glass, a tone dot, at most 3, bottom-right.
- `Stepper` (for the Release sheet), `DiffView` with Apply / Discard / Undo (from `backend/cm/lineDiff.ts`), `StackTrace` (parsed frames, library frames folded).

### A5. Chat layer
One chat look everywhere, built once:
- `shared/components/ai-elements/*` (conversation, message, prompt-input, shimmer, suggestion) and `shared/components/chat/*` (ChatPanel, ChatMessageList, ChatInput): glass panel, user bubble as a raised fill, assistant text on the panel, composer as a `field` with the AI send button, suggestion pills recessed.
- Move the duplicate `components/ai-elements/` (code-block, file-tree, task, tool) under `shared/` and tokenise them: tool status `yellow-600`/`blue-600`/… → status tokens; file-tree `blue-500` → `--primary`.
- Make `features/app-builder/components/AppBuilderChatPanel.tsx` (456 lines, its own react-markdown + `prose` + bordered composer) and `features/backend/components/PromptComposer.tsx` use the shared composer and message components. Keep their behaviour and testids.

### A6. Shell
- `features/project/components/`: `WorkspaceLayout` (sheet, transparent), `ProjectTopBar` (glass chrome, `Brand` → the Command Studio logo and wordmark; it still says "luna" at L51), `ProjectOutline` (entity-hue icons, sliding active fill, method chips, counts), `ProjectSwitcher` (searchable), `CommandPalette`, `WorkspaceOverlays`.
- Rename "luna" everywhere it shows: `ProjectTopBar.tsx:51`, `WorkspaceLayout.tsx:75` (the narrow banner), `AppBuilderShell.tsx`, `landing.tsx`.
- `WorkflowsListPage` and `AppBuilderShell` render their own top bars; switch them to the shared one.

### A7. Acceptance for stage A
- A dev-only route `/__design` that recreates `design/index.html` (the component reference) from the real components.
- Screenshot it at 1440×900 in both themes next to the mockup. Headless Chromium renders the blur; use `--force-prefers-reduced-motion` to see rolling digits at rest.
- `npx tsc -b`, `npm run test:unit`, `npm run design:check` (core files clean) and the shell e2e spec all pass.

---

## Stage B. Pages (Sonnet agents)

Each package lists its mockup, the files it owns, and what to build. Agents edit **only** their files. All of them read `DESIGN.md`, `DESIGN-STANDARD.md`, the mockup in both themes, and `design/handoff/MAPPING.md` for that page (MAPPING's file paths and risks still hold; where it describes visuals, `DESIGN.md` wins; its spec count is stale: there are 10 specs and 50 tests).

| # | Package | Mockup | Owns (under `features/`) | Build |
|---|---|---|---|---|
| 1 | Projects home | `projects.html` | `project/components/ProjectsHome.tsx`, `NewProjectDialog.tsx` | Exact. `HomeSky`, greeting + sub-line, stats strip, glass project cards with system diagrams, "New project" glass tile, templates, "Describe it instead". No banner |
| 2 | Overview | `overview.html` | `project/components/OverviewView.tsx`, `SetupGuide.tsx`, `useRunStats.ts` | Exact bento. Hero with `Snippet` and system diagram, Activity with `ActivityGrid` (30 days), stat tiles, Backend and Recent runs. Keep the `overview-panel` testid |
| 3 | Backend | `backend.html` | `backend/BackendView.tsx`, `TestPanel.tsx`, `PromptComposer.tsx` (layout only, chat parts come from core), `DiffDialog.tsx`, `ReferencePanel`, `ClientTsPanel`, `RouteSettings`, `ModuleDialogs.tsx` | Exact. Inline AI change flow with `DiffView` + Undo, `RoutePicker`, request path, trace waterfall. Keep the three modes (Visual map / Visual + code / Code) |
| 3b | Code map | none; follow the Backend look | `backend/code-map/*` incl. `code-map.css` (411 lines) | Restyle to tokens and glass; nodes as small raised tiles, edges neutral with one focal edge. Keep `10-code-map.spec.ts` selectors (`.react-flow__edge`) |
| 4 | Data | `data.html` | `data/*` (`DataView`, `RowsGrid`, `FilterBar`, `TableMenu`, `ColumnsDialog`, `NewTableDialog`) | Exact. Lineage hero, total, profile row, filter chips (exist), **new**: Columns show/hide menu, row-delete Undo, CSV import (dropzone, preview, mapping; needs bulk insert, mock behind a flag), star dust below the last row |
| 5 | Runs | `runs.html` | `runs/*` (`RunsView`, `RunSheet`, `TraceWaterfall`, `RunErrorBox`, `BrokerFix`) | Exact. 24h timeline hero, `StackTrace` instead of the raw traceback, smooth row arrival, j/k/↵, range presets. Keep the `/runs/$executionId` route and `run-sheet` testid unless the owner picks inline (decision 3) |
| 6 | Secrets | none | `secrets/*` | Follow Data's table look: PageHeader, glass table, amber dot for missing, `CreateSecretPopover` as a glass overlay |
| 7 | Workflows list | `workflows.html` (list) | `workflows-list/WorkflowsListPage.tsx` | Glass rows or cards like Projects; shared top bar. Keep `workflow-row-${id}`, `workflows-page`, `tab-workflows` |
| 8 | Workflow editor | `workflows.html` (surfaces only) | `workflow-editor/**` | See "Workflow editor rules" below |
| 9 | App builder | none | `app-builder/**` | Shell, panels and dialogs to the system. `ApiTesterPanel.tsx` (2,003 lines, 47 raw colours, 112 border lines) is the biggest single job: give it its own agent if batch size allows. Don't touch `sandbox/*` behaviour |
| 10 | Release and Settings | Release sheet in the kit | `releases/*`, `settings/*` | Release as a `Stepper`: Check code → Secrets (fix inline) → Review routes → Promote, with the beam only while promoting. Settings sections on the shared form components |
| 11 | Landing `/` | none | `app/routes/landing.tsx` (360 lines, 87 raw colours) | Only if the owner wants it now (decision 4). `HomeSky`, brand, tokens |

**Batches** (at most 5 at a time): batch 1 = 1, 2, 3, 4, 5 (the mocked pages); batch 2 = 3b, 6, 7, 8, 9, 10 (and 11 if in scope).

**Every implementer's report** lists: files changed, new testids, features that need backend data (mocked behind which flag), anything it needed from the core, and its own check results (`npx tsc -b`, `npm run design:check` for its files, the e2e specs that cover its pages).

### Workflow editor rules (package 8)
The owner wants the current nodes kept. From the survey:
- **Keep exactly:** the 64×64 square with a 12px radius, the 36px round icon chip, the label under the box with double-click rename, the left input and right output handles with the hover "+", `calculateNodeDimensions` and `calculateHandlePositions` (used by `WorkflowCanvas.findNearestConnectableNode` and `WorkflowSVG`), the canvas handlers, `snapGrid`, `connectionRadius`, `isValidConnection`, the stores and `lib/nodeConfig.ts`. `e2e/capture.spec.ts` expects exactly two `.react-flow__node`s, so add no extra nodes.
- **Restyle only:**
  - `index.css`: the `--node-*` palette (done in A2), the React Flow overrides (L400–432) and the run-state keyframes (L435–463), retuned to `--ring` and `--glow`.
  - `lib/nodeStyles.ts`: colour values only.
  - `canvas/nodes/WorkflowNode.tsx`: swap the inline `rgba(0,0,0,…)` shadows and the selection ring for the glass elevation; selected = `0 0 0 1px var(--ring), 0 0 0 4px` ring at 16%; running = a `--glow`; skipped = 50% opacity.
  - `canvas/edges/WorkflowEdge.tsx`: `EDGE_COLORS`, the marker fill, label and button classes. `canvas/ConnectionLine.tsx`: replace `#fff`.
  - `canvas/WorkflowCanvas.tsx`: React Flow `bg-background` → transparent so the sky shows; dot background quieter; minimap as glass.
  - `WorkflowEditorPage.tsx`: `floatingPanel` → the `glass` recipe. `WorkflowNavbar`, `NodeCreatorPanel`, `BottomPanel`, `ExecutionLogsPanel` (replace its VS Code hex colours with `--syn-*`), `NodeContextMenu`, `StickyNote`, `AddNodesButton`, `Controls`.
  - `ndv/*` (the node details modal: `DynamicNodeForm`, `NodeSettings`, `InputPanel`, `OutputPanel`, `FilePathField`, `RunDataDisplay`, `SchemaDisplay`): glass overlay, shared fields, no `ring-1`.
- Whether the details stay a modal or become an inspector pane is decision 2.

## Stage C. Validation (Sonnet agents, read-only)
One validator per package, after its implementer finishes. Each one:
1. Starts the app (or reuses the lead's dev server), opens each page of the package at 1440×900 in light and dark, and screenshots it. Opens the matching `design/*.html?theme=…` and screenshots that too.
2. Crops and zooms where small text matters, and lists every difference as high (wrong surface, border, colour, layout, missing feature), medium (spacing, size, alignment) or low.
3. Checks: `npm run design:check` on the package's files, no `data-testid` removed (`git diff` for `data-testid=`), no console warnings, both themes the same design.
4. For pages without a mockup, judges against `DESIGN.md` and the nearest mocked page.

The lead sends high and medium findings back to the implementer (same agent, via SendMessage) for at most two rounds, then fixes the rest itself.

## Stage D. Integration (lead)
- Merge the worktrees one package at a time, running `npx tsc -b` and the unit tests after each.
- Update the e2e colour checks:
  - `01-shell.spec.ts:115/117` hard-code the old dark `rgb(17,17,17)` and `rgb(28,28,28)`: change them to the new canvas `rgb(5, 5, 7)` and the glass/sheet values, read from the tokens.
  - `02-backend-manual.spec.ts:27` expects the class `text-warning-text`: keep that class on the pill.
  - `09-preview-boundary.spec.ts:30` checks `oklch(0.546 0.245 262.881)`. That looks like the generated app's own colour inside the sandbox, not the studio's; confirm and leave it.
- Run the whole e2e suite, `npm run design:check` with no allow-list left except `index.css` and the sandbox runtime, and take a final light and dark screenshot set of every page next to the mockups.
- Update `MAPPING.md` (paths still say `ui-redo/` and `overview-bento.html`; add the code map, specs 08–10 and the new shared components).

## Data the mockups show that the backend doesn't have
Mock these behind a flag or build the endpoints (`HANDOFF.md` §3 has the list): 30-day run counts, run time ranges, per-project 24h buckets, column aggregates, the route → table map, bulk insert for CSV, per-step AI output and token streaming, sandbox start time, a structured traceback, a push channel for new runs.

## Decisions for the owner (ask at the start of the session)
1. **Push:** commit 5040b42 isn't pushed. Push `revert-to-5684ba5` before starting?
2. **Workflow node details:** keep `NodeDetailsModal` (restyled), or move to an inspector pane in the shell as in the mockup? Nodes stay the same either way.
3. **Run details:** keep the separate `/runs/$executionId` sheet, or switch to inline expansion as in the mockup?
4. **Landing page and app builder:** in scope for this pass? They have no mockups and are the two biggest raw-colour files.
5. **Refund triage double write** (`HANDOFF.md` §4.3): not a design question, but it affects the demo data.

## Kickoff prompt for the next session
Paste this into a new Opus session in `~/Projects/playground/work/demo`:

> Read `design/IMPLEMENTATION-PLAN.md`, then `design/DESIGN.md` and `design/HANDOFF.md`. We're rolling the approved Command Studio design out to `apps/workflow-studio`. Ask me the "Decisions for the owner" first. Then run stage 0 and stage A yourself: write the design standard and update the core (index.css, recipes, fonts, sky, shared ui and chat components, shell), and show me the `/__design` page in both themes before any page work. After I approve the core, use Sonnet agents (model: sonnet, in worktrees, at most five at a time) to implement the page packages in stage B, and separate Sonnet agents to validate each page against its mockup in both themes (stage C). Keep the workflow nodes' shape and logic; only restyle them. Don't commit until I say so.
