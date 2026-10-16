# HANDOFF: implement the Command Studio design in the studio app

**Final, 2026-10-16.**
- **Target:** `demo/apps/workflow-studio` (React 18, Vite, Tailwind v4, shadcn-style on Base UI, `@xyflow/react`, `motion`, `sonner`).
- **Repo:** `demo/`, branch `revert-to-5684ba5`. The design lives in `demo/design/`; it was `ui-redo/` while being built.

The design is finished and approved: **frosted glass over a near-black space, a Linear-style sheet, steel-blue brand, and no borders**. Rebuild it in the real app one phase at a time, with tests green at every step.

**Translate the mockups; don't paste them.** Their CSS is a stack of experimental layers, the final decisions sit in `assets/lean.css` (later sections win), and their class names collide with Tailwind.

## 0. Read first, in this order

| File | What it is |
|---|---|
| `DESIGN.md` | **The single design spec**: layout, dark and light, colour, surfaces, type, space and art, motion, components, guardrails. Read all of it. |
| `handoff/tokens.css` | **The values.** 149 tokens. Light values sit on `:root` as hex; dark values on `.dark`. Every value was computed in a browser from the final mockups. |
| `handoff/tailwind-theme.css` | Tailwind v4 `@theme inline` plus `@utility` recipes: `glass`, `tile`, `glass-editor`, `glass-chrome`, `glass-overlay`, `sheet`, `well`, `recess`, `raise`, `slot`, `field`, `stardust`, `nebula`. Compiled cleanly with the studio's Tailwind 4.1.17. |
| `handoff/starfield.js` | The canvas starfield as a framework-free module: `paintStarfield(canvas, opts)`, `starDustDataURL(opts)`, and the presets `SKY`, `SHEET`, `DUST`. The usage comment shows a React `useEffect`. |
| `handoff/MAPPING.md` | Each mockup piece mapped to a studio component or file, plus the E2E couplings, risks and data gaps. **Read §6 before writing code.** Where it describes visuals (borders, indigo, floating panels), **`DESIGN.md` wins**. The file paths and risks still hold. |
| `handoff/fonts/` | `inter-v4.woff2` (variable, opsz; family "Inter Var") and `jbmono.woff2` |
| `handoff/logo.svg` | The mark: a white `>_` on a steel-blue → cyan tile. Use it for the favicon and the top-bar brand. |
| `_archive/` | The superseded layered DESIGN and HANDOFF, kept for history only. **Don't build from them.** |

**Reference pages:** open these in a browser with `?theme=dark`; dark is the primary experience.

| Mockup | Studio surface |
|---|---|
| `projects.html` | Projects home |
| `overview.html` | Project overview (bento) |
| `backend.html` | Backend editor and test panel |
| `data.html` | Data table |
| `runs.html` | Runs |
| `workflows.html` | Workflow editor |
| `index.html` | Component reference |

`overview-classic.html` is the earlier layout, kept for reference only. `?layout=floating|docked` and `?accent=scaffold|none` are comparison variants, **not** the design.

## 1. The design on one page
Full rules are in `DESIGN.md`.

- **Layout.** The top bar and the sidebar sit on the background. All content lives in **one sheet**: 10px radius, 8px from the window edge, transparent in dark. Overview is a **bento grid**.
- **Dark mode, the hero.**
  - The canvas is near-black `#050507`.
  - A faint starfield sits behind everything at 40% opacity, with a barely visible blue nebula bloom.
  - Every box is **frosted glass**: `rgba(255,255,255,.06)`, `blur(11.6px)`, a whisper of top light, and a soft `0 10px 40px` shadow. **No outlines.**
  - Text sits in darker recesses, never directly on the frost.
- **Light mode.** Pure white and cool slate. Only the top bar is frosted. Never warm grey.
- **Colour.**
  - A calm steel-blue primary: `#2a5fc4` light, `#3567c8` dark.
  - Each kind of thing has its own hue, used only on icons and small tints: backend, data, runs, apps, secrets, ai, workflows, release.
  - Status colours are for status only.
  - **No purple or indigo** as the brand.
- **Surfaces.** No borders, and nothing inside a box has a gradient. **Recessed** means the control holds a value; **raised** means it marks a choice or an action.
- **Corners.** 10 sheet · 8 tiles and menus · 6 controls and wells · 4 chips · 3 kbd.
- **Type.** Inter v4 (opsz, `cv01`/`ss03`) with JetBrains Mono for code. Tabular numerals everywhere. Sentence-case labels.
- **Art.** Line art drawn from real data, with one glowing focal node per drawing. Packets fire **once per real event**. Nothing loops.
- **Motion.** 80 / 140 / 200 / 280ms, no bounce, only when state changes. Respect reduced motion.

## 2. Build phases
Each phase ends green: `npx tsc`, `vitest`, the E2E specs it touches, and no console warnings. `e2e/console-allow.ts` must stay empty.

### Phase 0: known baseline (blocking)
Get the existing E2E specs green before restyling. Specs 05 (T22, T24), 06 and 07 are red or haven't been run yet; see `demo/HANDOFF-UI-FINISH-2.md`.

### Phase 1: tokens, fonts, space, primitives
1. **Tokens.** Replace the token blocks in `src/index.css` with `handoff/tokens.css`.
   - Light values go **directly in `:root`, as hex**. `contrast.test.ts` parses the literal `:root {` and `.dark {` blocks; all 20 checks pass with the new tokens.
   - Keep `--node-*`, `--sticky-*` and `--chart-2` in `index.css` until the workflow canvas and the JSON viewer are restyled.
2. **Tailwind.** Append `handoff/tailwind-theme.css`.
   - Recipes sort after plain colour utilities. Where the top bar must stay transparent, write `glass-chrome bg-transparent!`.
   - Check that the `backdrop-filter` fallback (`@supports`) works.
3. **Fonts.** Serve both fonts from `public/fonts/` and fix the `@font-face` URLs.
4. **Space.** Add a `<SpaceBackground/>` to the root layout:
   - one fixed full-screen `<canvas>` painted with `paintStarfield(SKY)`, at 40% opacity in dark;
   - the `nebula` utility on the body;
   - a repaint on resize and on theme change;
   - `aria-hidden` and `pointer-events: none`.
5. **Primitives** (`src/shared/components/ui/*`, cva variants against the recipes; the full table is in MAPPING §3):
   - **Buttons:** flat fills. Primary is steel-blue, outline is a recess fill, AI uses the primary. A pressed button scales to `.98`.
   - **Inputs:** `field`, a recessed fill with no ring and a 2px focus ring.
   - **Badges and method chips:** a flat tint of their own colour, with no ring.
   - **Segmented control:** a recessed track and a raised thumb that slides.
   - **Tabs:** use `Tabs.Indicator` for the sliding underline.
   - **Tooltips:** one `TooltipProvider delay={500} timeout={300}` in `__root.tsx`, plus a `Tip` wrapper with an optional kbd hint.
   - **Menus, combobox and ⌘K:** `glass-overlay`, with a recessed search box and a raised highlighted item.
   - **Sheets:** `glass-overlay`, 12px from the viewport edges, with a dim scrim.
   - **Toasts:** keep sonner, restyled to `glass-overlay`. Show the tone as a dot, and stack at most 3.
   - **Rolling digits:** a `<Roll value>` component that rolls per digit, staggered 20ms from right to left.
6. **Update T44** (`e2e/01-shell.spec.ts:107`). It hard-codes the old dark colours `rgb(17,17,17)` and `rgb(28,28,28)`.
7. **Acceptance:** recreate `index.html` as a dev-only route and compare it with the mockup in both themes.

### Phase 2: shell
- **Files:** `ProjectTopBar`, `ProjectOutline` and the workspace layout (MAPPING §2).
- **Brand:** the logo tile, the "**Command** Studio" wordmark and the favicon.
- **Top bar and sidebar:** `glass-chrome`, sitting on the canvas with no outlines. The sheet uses the `sheet` utility.
- **Sidebar:**
  - The active item is a soft fill, and its highlight slides between pages.
  - Icons take the entity hue; inactive icons are at 60% opacity.
  - Group heads are sentence case, with a `+` that appears on hover.
  - Routes show method chips.
  - The tails show counts, plus an amber dot for missing secrets.
- **Layout policy:** any decoration inside elements whose `data-testid` ends in `-panel` must be clipped (MAPPING §6.5).

### Phase 3: Kit flows
- **Release sheet:**
  - A stepper: Check code → Secrets (fix inline) → Review routes → Promote.
  - A beam runs around the sheet only while promoting. A busy toast becomes "v1 is live".
  - Wire it to `features/releases/ReleaseSheet.tsx`.
- **Project switcher** and **Settings sheet**.
- **Event bus:** `secret-added`, `run-arrived`, `table-write` and `release-done`. Mutations and the runs refetch emit these events; art components subscribe to them (MAPPING §5).

### Phase 4: pages, in this order
1. **Overview (bento).**
   - Tiles use `glass` / `tile`.
   - Hero: the system diagram and a URL/curl/Python snippet.
   - Activity is the focal tile: hero number, 24h bars and the 30-day grid.
   - Then the stat tiles, Backend, Recent runs, Secrets, Releases and Apps.
   - Keep the `overview-panel` testid on the scrolling container.
2. **Backend.**
   - Editor and test panes use `glass-editor`.
   - The AI flow: shimmer status, a step log, a streamed explanation, a diff with Apply/Discard/Undo. It maps onto the `useGenerate` stages and uses `lineDiff.ts`.
   - The request path, the trace waterfall and the route-picker combobox.
   - Toasts.
3. **Runs.**
   - The 24h timeline hero.
   - Inline expandable rows with a mini trace and a **stack trace**: the user's frames first, library frames folded, and a click opens that line.
   - Smooth row arrival, j/k/↵, and range presets.
   - Keep the `/runs/$executionId` route and the `run-sheet` testid, or update their 3 call sites.
4. **Data.**
   - The lineage hero, the hero total and the column profile row.
   - The Columns menu, filter chips, and row actions with Undo.
   - CSV import: dropzone, preview and column mapping.
   - Star dust (`stardust`) below the last row.
5. **Projects home.**
   - The greeting, the workspace pulse panel and the attention alert, whose one-click fix cascades through the page.
   - Glass project cards with system diagrams and activity strips.
   - Templates with line art, and "Describe it instead".
6. **Workflows.**
   - The canvas sits in a recessed well. Nodes are raised glass, and their icons take the entity hue. **Drop the rainbow `--node-*` palette.**
   - An inspector pane (decision §4.2).
   - Test run animates node state, with one packet per wire.
   - A minimap and a glass toolbar.

### Phase 5: art and motion
- **Port `art.js` to React components** that take props from real queries: `<SystemDiagram>`, `<AppThumb>`, `<ReleasePath>`, `<RequestPath>`, `<Lineage>`, `<RunTimeline>`, `<ProjectGlyph>`, `<TemplateArt>`.
  - Prefix SVG ids with `useId()`.
  - Give exactly one focal node per drawing the brand stroke and a 4px glow.
- **`usePulse(pathRef)`** keeps the contract of `LunaArt.pulse`: one packet, 560ms, at most one every 280ms, and a stroke flash instead under reduced motion.
- **Sparkles:** at most 3 per page at focal points, at 50% opacity.
- **Don't port the demo timers.** `liveRun`, the auto AI run and the auto Send exist only so the mockups look alive.

## 3. Data the mockups show that the backend doesn't have yet
Either mock these behind a flag or build the endpoints first. MAPPING §6.8 has the details.
- 30-day daily run counts.
- A time range on runs, and retention beyond the last 100 runs.
- Per-project 24h buckets.
- Column aggregates and sums. Today only the 50 loaded rows can be summarised.
- The route → table read/write map. It could be derived from the module source, the way `useSecretReferences` works.
- Bulk insert for CSV import.
- Per-step AI input and output, and token streaming.
- The sandbox start time, needed for an exact waterfall.
- A structured traceback.
- A push channel for new runs. Polling works meanwhile.

## 4. Decisions for the owner
1. **Overview:** decided. Bento.
2. **Workflows:** should the editor sit inside the shell with an inspector pane, as in the mockup, instead of today's full-screen canvas with `NodeDetailsModal`?
3. **Refund triage double write:** `createRefund` already inserts into the refunds table, then "Log to refunds" inserts again. Which one should stay?
4. **Run details:** keep the separate `/runs/$executionId` sheet, or switch to inline expansion?

## 5. Known token caveats
These come from the token pass.
- **Light canvas:** the token is `#f8f9fb`, but the mockup renders `#fafaf9` because of a higher-specificity rule in `layout.css`. Use the token.
- **Code gutter:** raised to `#878e9e` (light) and `#676d7c` (dark) to pass the 3:1 contrast test. The mockup's gutter fails it.
- **Warm values still left:** a few light-mode values haven't had the cool pass yet: `--faint`, `--code-bg`, `--syn-comment`, `--border`, `--input`, and the `--raise-*` and `--shadow-*` layers. Cool them if they ever show warm.
- **AI colour:** `--ai` and `--ai-text` keep the violet for AI components. `--hue-ai` is the calmer entity hue.

## 6. Guardrails
Never reintroduce these (full list in DESIGN §11):
- Borders or outlines on boxes.
- Gradients inside tiles.
- Purple as the brand.
- Warm-grey cards.
- A navy veil over the background.
- Heavy stars or glow.
- Glass with nothing behind it.
- Floating-card layouts.
- Springy motion.
- Looping decorations.
- Rainbow node colours.

Other rules:
- **Don't port these class names:** `.k .s .n .f .c .d .sep .empty .table .grid .hidden .collapse`. Syntax colouring goes through CodeMirror and `--syn-*`.
- **Never put a recess inside a recess.** A control inside a well is raised.
- **Every new interactive element gets a `data-testid`.** Keep all existing testids and the `data-*` attributes the specs read (MAPPING §6.4).
- **Verify visually.** Take headless Chromium screenshots at 1440×900 in both themes and compare them with the mockups.
  - Headless doesn't render `backdrop-filter` blur and freezes transitions, so check the blur in a real browser.
- **When in doubt, favour legibility.**
