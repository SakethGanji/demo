# HANDOFF: implement the Command Studio design in the studio app

Written 2026-10-15. **Target:** `demo/apps/workflow-studio` (React 18, Vite, Tailwind v4, shadcn-style on Base UI, `@xyflow/react`, `motion`, `sonner`). **Repo:** `demo/`, branch `revert-to-5684ba5`.

The design is finished and approved as static HTML in this folder (`ui-redo/`). Your job is to rebuild it in the real app, one phase at a time, with tests green at every step. **Translate the mockups; don't paste them.** Their CSS is a stack of ten experimental layers and their class names collide with Tailwind.

> **Final direction (read first):** DESIGN.md §12 (space theme + azure brand) and §11 ("lean in") override earlier sections. The brand is electric azure → cyan, not indigo. Tiles are flat with no borders or shadows. The starfield is a canvas renderer to port from `assets/accent.js` (`paint()`). **Linear layout** (one inset sheet), **8/6/6/4 corners**, **flat inside** (no inner rings or gradients), **entity colours** and the **space** canvas. `handoff/tokens.css` radii are updated. Add the entity-hue tokens and the §11 rules from `assets/lean.css` on top of `handoff/tailwind-theme.css`, and drop the panel-material gradients for panels inside the sheet.

## 0. Read first, in this order

| File | What it is |
|---|---|
| `ui-redo/DESIGN.md` | The design system: rules, not values. §1 surfaces, §1b inset scale, §2 spacing, §3 radii, §5 type, §6/6b vectors and edges, §7 motion, §8 small things, §9 Paper & Graphite, §10 Kit |
| `ui-redo/handoff/tokens.css` | **The single source of truth for values.** 147 tokens, light on `:root` and dark on `.dark`. Every value was computed in a browser from the mockups, not guessed. |
| `ui-redo/handoff/tailwind-theme.css` | A Tailwind v4 `@theme inline` mapping plus `@utility` recipes: `panel`, `well`, `recess`, `raise`, `slot`, `field`, `vx-grid`, `vx-edge`, `vx-host`. It compiles with the studio's Tailwind. |
| `ui-redo/handoff/MAPPING.md` | Each mockup piece mapped to a studio component or file, page by page, plus risks, E2E couplings and data gaps. **Read §6 before writing code.** |
| `ui-redo/handoff/fonts/` | `inter-v4.woff2` (variable, with an optical-size axis; family "Inter Var") and `jbmono.woff2` |
| `ui-redo/handoff/logo.svg` | The Command Studio mark: `>_` on an indigo→violet tile. Use it for the favicon and the top-bar brand. |
| The mockup pages | The visual reference. Open them in a browser, click around, and toggle theme with the sun/moon button. |

**Reference pages (open these, don't read their CSS):**

| Mockup | Studio surface |
|---|---|
| `projects.html` | Projects home |
| `overview.html` | Project overview: the bento version, now the main one (`overview-classic.html` is the earlier layout) |
| `backend.html` | Backend editor and test panel |
| `data.html` | Data table |
| `runs.html` | Runs |
| `workflows.html` | Workflow editor |
| `index.html` | Component reference |

## 1. The design in one page

- **Floating workbench.** The canvas holds floating **panels**. Panels hold recessed **wells**. Controls follow an **inset scale**: things that *hold a value* are recessed (inputs, search, segmented track, kbd, chips with values, progress tracks); things that *mark a choice or an action* are raised (segmented thumb, active nav item, buttons, avatars, highlighted menu item). Light always comes from above.
- **No borders between surfaces.** Panels separate by a 1px ring plus shadow. Panel headers have no rule underneath. Lines appear only as hairlines between rows.
- **Themes.**
  - **Light** is pure white, with warm-ink lines (`rgb(38 34 28 / α)`). It is never blue-grey.
  - **Dark ("Graphite")** is near-black `#08080a` with `#111114` panels, a light-catching top edge, a 3.5% grain, and **dot lattices instead of grid lines**.
- **Colour.** There is **one accent**: indigo `--primary`. The brand gradient (indigo→violet) is used only for the logo, project avatars, AI actions, and the focal stroke in art. Green, amber and red are used **only for status**.
- **Corners.** Panel 12 · well 8 · control 8 · chip 6 · kbd 4.
- **Type.** Inter v4 with the `opsz` axis plus `cv01`/`ss03`. Mono is used for code, paths, IDs and env names only. Use tabular numerals everywhere.
- **Vectors.** Linear-style line art drawn **from real data**: the system diagram, the request path, table lineage, the run timeline and the workflow canvas.
  - Exactly one glowing focal node per drawing.
  - Each page also carries edge vocabulary: registration `+` marks, grid fields with rulers in empty space, and dotted leaders.
- **Motion** happens only on real state change. **Nothing loops.**
  - Durations: 80 / 140 / 200 / 280 ms. Easing `cubic-bezier(.16,1,.3,1)` for out and `(.65,0,.35,1)` for in-out. No springs, no bounce.
  - Packets on wires fire once per real event.
  - Respect `prefers-reduced-motion`.
- **Glass** is used only on overlays: menus, the ⌘K palette, tooltips, and the sticky top bar where content scrolls under it. Never on panels.

## 2. Build phases

Each phase ends green: `npx tsc`, `vitest`, the E2E specs it touches, and no console warnings (`e2e/console-allow.ts` must stay empty).

### Phase 0: get to a known baseline (blocking)
- **Get the existing E2E specs green before restyling anything.** Specs 05 (T22, T24), 06 and 07 are red or haven't run yet (`demo/HANDOFF-UI-FINISH-2.md`). Without a green baseline you can't tell regressions from known failures.

### Phase 1: tokens, fonts, primitives
1. **Tokens.**
   - Replace the token blocks in `src/index.css` with `handoff/tokens.css`.
   - Put the light values **directly in `:root`**. Don't use `html:not(.dark)`: `contrast.test.ts` parses the literal `:root {` and `.dark {` blocks and needs hex values for the method, syntax and status tokens.
   - Keep the `--node-*` and `--sticky-*` tokens for now; the canvas still uses them.
2. **Tailwind.** Append `handoff/tailwind-theme.css`.
   - `@property` rules go outside `@layer`.
   - Check that the `mask-composite` rules survive Lightning CSS, in both Chromium and WebKit.
3. **Fonts.** Serve the fonts from `public/fonts/` and fix the `@font-face` URLs.
4. **Restyle the primitives** in `src/shared/components/ui/*`, using cva variants against the recipes. MAPPING §3 has the full table. The highlights:
   - **Buttons:** primary is an indigo gradient with an inner highlight. Outline buttons are raised. AI buttons use the brand gradient.
   - **Inputs:** use the `field` recipe (recessed).
   - **Badges:** 6px radius, with the tone shown by colour and a ring, never a solid fill.
   - **Method chips:** a tiny recessed chip ringed in the method colour.
   - **Segmented control:** a recessed track with a sliding raised thumb.
   - **Tabs:** `Tabs.Indicator` gives the sliding underline.
   - **Tooltips:** one `TooltipProvider delay={500} timeout={300}` in `__root.tsx`, plus a `Tip` wrapper with an optional kbd hint.
   - **Menus, combobox, ⌘K:** glass, a recessed search, and a raised highlighted item.
   - **Sheets:** float 12px from the viewport edges, with a dim scrim and no blur.
   - **Toasts:** keep sonner, restyled to show tone with a `.dot` only and to stack at most 3.
   - **Rolling digits:** a `<Roll value>` component that rolls per digit, staggered 20ms right-to-left.
5. **Update T44** (`e2e/01-shell.spec.ts:107`). It hard-codes the old dark colours `rgb(17,17,17)` and `rgb(28,28,28)`.
6. **Acceptance.** `index.html`'s component reference recreated as a dev-only route or story. Compare it side by side with the mockup in light and dark.

### Phase 2: shell
- **Files:** `ProjectTopBar` and `ProjectOutline` (MAPPING §2).
- **Brand:** the logo tile plus the "**Command** Studio" wordmark, and the favicon.
- **Top bar:** keep the existing controls (switcher, Project/Workflows tabs, search trigger, env segmented control, Release, theme toggle) and restyle them to `shell.js`.
- **Sidebar:**
  - the active pill slides between items;
  - group heads are sentence case, with a `+` button that appears on hover;
  - route rows carry method chips;
  - tails show counts, plus an amber dot for missing secrets.
- **Panel material** on the top bar, sidebar and main area, with the dark top-edge highlight.
- **Edges:** registration marks in the gaps between panels (an `<EdgeMarks/>` component). Clip any decoration inside elements whose `data-testid` ends in `-panel`; that's the layout policy in MAPPING §6.5.

### Phase 3: Kit flows (shared)
- **Release sheet.** A vertical stepper: Check code → Secrets (an inline fix for missing secrets) → Review routes → Promote. A **beam** runs around the sheet while promoting, and a busy toast becomes "v1 is live". Wire it to the real releases API (`features/releases/ReleaseSheet.tsx`).
- **Project switcher combobox** and **Settings sheet.** Sections are General, Environments and Danger.
- **Event bus.** `secret-added`, `run-arrived`, `table-write` and `release-done`. Mutations and the runs refetch emit; art components subscribe (MAPPING §5).

### Phase 4: pages, in this order
1. **Overview (bento).**
   - **Tiles:** the hero with the system diagram and the URL/curl/Python snippet; Activity, the focal tile (`vx-edge`), with the 24h bars and the 30-day grid; the stat tiles; Backend; Recent runs; Secrets; Releases; Apps.
   - **Keep the `overview-panel` testid on the scrolling container.**
2. **Backend.**
   - The AI flow: shimmer status text while generating, a task log, a streamed explanation, a diff with Apply/Discard/Undo, and a confirmation bar. It maps onto `useGenerate` stages; use `lineDiff.ts`.
   - The request-path drawing; packets are timed to the trace.
   - The trace waterfall.
   - The route-picker combobox.
   - Toasts for Save and Send.
3. **Runs.**
   - The 24h timeline hero: dot lattice, trend line, the current hour glowing, failure nodes.
   - Inline expandable rows with a mini trace and a **stack trace** (your frames first, library frames folded, click a frame to open the line).
   - Smooth row arrival, j/k/↵ keys, and range presets.
   - Keep the `/runs/$executionId` route and the `run-sheet` testid, or update their 3 call sites.
4. **Data.**
   - The lineage hero with event packets, the hero total and the column profile row.
   - The Columns menu, filter chips, and row actions with Undo.
   - Import CSV through a dropzone sheet with preview and mapping.
   - The end-of-table marker with a grid field below it.
5. **Projects home.**
   - The greeting, the workspace pulse panel (`vx-edge`) and the attention alert, whose one-click fix cascades through the page.
   - Project cards with system diagrams and activity strips.
   - Templates with line art, and "Describe it instead".
6. **Workflows.**
   - The canvas in a recessed well. Nodes are raised mini-panels with neutral type labels; **drop the rainbow `--node-*` palette**.
   - The inspector **panel** replaces `NodeDetailsModal` (product call; see §4).
   - Test run animates node state; there is one packet per wire.
   - The minimap and the glass toolbar.

### Phase 5: art and motion components
- **Art.** Port `art.js` into React components that take props from real queries: `<SystemDiagram>`, `<AppThumb>`, `<ReleasePath>`, `<RequestPath>`, `<Lineage>`, `<RunTimeline>`, `<ProjectGlyph>`, `<TemplateArt>`.
  - Use a `useId()` prefix to avoid duplicate SVG ids.
  - Give `usePulse(pathRef)` the same contract as `LunaArt.pulse`: one packet, 560ms, at most one every 280ms, and a stroke flash under reduced motion.
- **Don't port the demo timers.** `liveRun`, the auto AI run and the auto Send exist only to make the mockups look alive.

## 3. Data the mockups show that the backend doesn't have yet
Either mock these behind a flag or build the endpoints first; MAPPING §6.8 has the details.
- 30-day daily run counts.
- A time range on runs, and retention beyond the last 100 runs.
- Per-project 24h buckets on the home page.
- Table column aggregates and sums (today only the loaded 50 rows can be summarised).
- Which routes read or write which tables. You could derive it from module source, the way `useSecretReferences` does.
- A bulk insert for CSV import.
- Per-step input and output for the AI task log, plus token streaming.
- The sandbox start time, needed for an exact waterfall.
- A structured traceback (today it's a redacted string).
- A push channel for new runs (polling works meanwhile).

## 4. Decisions for the owner (confirm before Phase 4)
1. **Overview:** decided, bento (`overview.html`).
2. **Workflows:** should the editor sit inside the shell with an inspector panel, as in the mockup, instead of today's full-screen canvas plus `NodeDetailsModal`?
3. **Refund triage double write:** the flow calls `createRefund`, which already inserts into `refunds`, and then "Log to refunds" inserts again. That's a product modelling question.
4. **Runs detail:** keep the separate `/runs/$executionId` sheet, or switch to inline expansion?

## 5. Guardrails
- **Rejected in earlier rounds, never reintroduce:**
  - glassmorphism on panels
  - pastel or Arc gradients
  - glossy multicolour icon tiles
  - flat chrome without floating panels
  - springy motion
  - looping decorative animation
  - blue-grey light neutrals
  - rainbow node categories
- **Don't port these mockup class names:** `.k .s .n .f .c .d .sep .empty .table .grid .hidden .collapse`. Syntax colouring goes through CodeMirror and `--syn-*`.
- **No recess inside a recess.** A control inside a well becomes raised.
- **Every new interactive element gets a `data-testid`.** Keep all existing testids and the `data-*` state attributes the specs read (MAPPING §6.4).
- **Verify visually.** Run headless Chromium screenshots at 1440×900 in light and dark for each page, compared with the mockup. Headless freezes CSS transitions, so check end states with transitions disabled.
