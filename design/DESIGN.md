# Command Studio design system

**Final, 2026-10-16 (refine pass).** This single spec replaces the earlier layered version, which is kept in `_archive/DESIGN-history.md` for history only.

**The idea:** a quiet workspace in space. Every card sits on frosted glass over a starfield. **Light and dark are one design**: the same sky, the same glass and the same layout, with only the palette changed. Colour, light and motion appear only when they mean something.

**Sources of truth:**
- **Values:** `handoff/tokens.css`. If a number here and a token there disagree, the token wins.
- **Mockups:** `demo/design/*.html` (open with `?theme=dark` or `?theme=light`).
- **Final CSS decisions:** `assets/lean.css`, then `assets/refine.css` (loaded last, and it wins). Read each in order; later sections win. `?refine=0` shows a page without the refine pass.
- **Projects sky:** `assets/home-sky.js`, ported to `handoff/starfield.js` as `paintHomeSky`.

---

## 1. Layout

- **Linear-style shell.** The top bar (48px) and the sidebar (240px) sit directly on the background. All content lives in **one sheet** to the right of the sidebar and below the top bar. The sheet has a 10px radius, an 8px gap to the window edge, and is transparent in both themes, so the glass inside it has something to frost.
- **Page structure:**
  - Projects home: the greeting and a three-figure stats strip (Runs, Apps, Needs attention), then project cards, then templates. **No warning banner**: a missing secret shows in the line under the greeting and in the Needs attention count. No beam, grid, sparkles or workspace diagram.
  - Project workspace: Overview, Backend, Data and Runs, plus Workflows as a tab in the top bar.
  - Floating sheets: Release and Settings.
- **Overview is a bento grid:** 12 columns with 16px gaps.

  | Row | Tiles (columns) |
  |---|---|
  | 1 | Hero (8): title, snippet and system diagram. Activity (4), the focal tile: hero number, 24h bars and 30-day grid |
  | 2 | Four stat tiles (3 each) |
  | 3 | Backend (7) and Recent runs (5) |
  | 4 | Secrets, Releases and Apps (4 each) |

  Below about 1100px it falls back to 6 columns.
- **Editor pages** (Backend, Workflows) split the sheet into panes: editor and test panel, or canvas and inspector.

## 2. One system, two themes

Light and dark share every layer below. Only the values change.

| Layer | Dark | Light |
|---|---|---|
| **Canvas** | `#050507`. Mostly black is the point. | `#ffffff`. Pure white, never grey. |
| **Sky** (inside a project) | The starfield (§6) at **16% opacity**: a faint hint, never behind what you read | The same starfield in navy ink at **40%** |
| **Sky** (Projects home) | The full sky from `paintHomeSky` (§6): denser stars, a faint Milky Way band, a slight `#0c1120` tint at the top | The same composition in navy stars on white |
| **Glow** (the logo's two colours) | Steel blue from the top left and aqua from the right, very faint (8% and 5%) | The same blooms, stronger (34%), so they read as a dawn on white |
| **Sheet** | Transparent, no outline | Transparent, no outline |
| **Glass** (tiles, cards, panes, stats, table wells) | Fill `rgb(255 255 255 / .06)` | Fill `rgb(255 255 255 / .5)` |
| | Blur `blur(20px) saturate(160%)` | Same |
| | Edge `inset 0 0 0 1px rgb(255 255 255 / .10)` | Edge `inset 0 0 0 1px rgb(255 255 255 / .8)` plus an outer hairline `rgb(30 50 110 / .10)` |
| | Top light `inset 0 1px 0 rgb(255 255 255 / .10)` | Top light `inset 0 1px 0 #fff` |
| | Shadow `0 10px 40px rgb(0 0 0 / .5)` | Shadow `0 1px 2px rgb(20 30 60 / .05), 0 12px 32px rgb(30 50 100 / .09)` |
| | Hover `.09`; editor panes `.045` | Hover `.72`; editor panes `.62` |
| **Glass chrome** (top bar) | The glass fill at 60%, same blur | Same |
| **Glass overlays** (⌘K, menus, toasts, sheets) | A denser frost, about `.72` slate, `blur(24px)` | White `.72`, `blur(22px)` |
| **Recesses for text** (tables, lists, code, wells) | `rgb(0 0 0 / .35)` | `rgb(255 255 255 / .6)` |

- **Glass needs something behind it.** The stars and the two-colour glow are what the blur catches. Glass over a flat colour reads as a flat card, which failed twice before.
- **Text never sits on bare frost.** Tables, lists and code sit in a recess inside the glass.
- **Gradients fade to their own colour.** A glow fades to the same colour at 0 alpha, never to transparent black, which turns grey on white.

## 3. Colour

**Brand: calm steel-blue.**

| Token | Light | Dark |
|---|---|---|
| Primary (buttons, links, selection) | `#2a5fc4` | `#3567c8` |
| Ring / focus | `#4a7fd6` | `#7ea3e0` |
| Brand gradient (logo and project avatars **only**) | `#3a6fd0 → #4aa9bf` | `#5b8ee0 → #6cc3d6` |

- **Logo:** a white `>_` prompt on a steel-blue → cyan tile (`assets/logo.svg`, `handoff/logo.svg`).
- **Wordmark:** "**Command** Studio", with "Studio" in a lighter weight.
- **Purple and indigo are retired** as brand colours. Violet survives only as the AI hue, on the sparkle icon.

**Entity hues.** Each kind of thing keeps one hue everywhere it appears, used **only on icons and small tints**, never as a large fill.

| Entity | Light | Dark |
|---|---|---|
| backend | `#3a67b8` | `#86a8e0` |
| data | `#2f7d62` | `#6cc4a2` |
| runs | `#2f7f93` | `#74c4d4` |
| apps | `#a8643a` | `#dca27a` |
| secrets | `#8f7426` | `#d6bf6a` |
| ai | `#7a62b0` | `#ad9ad9` |
| workflows | `#a2557d` | `#d495b6` |
| release | `#5a7a2a` | `#a9c870` |

The hues apply to:
- Sidebar icons. Inactive ones sit at 60% opacity.
- Section-header icon tiles, as a 10% tint of the hue.
- Card footers.
- Workflow node icons.

**Status and method colours:**
- **Status colours mean status only:** green for success, amber for a warning or something missing, red for an error.
- **HTTP methods** use colour as text on a 13% tint: GET green, POST blue, PUT amber, DELETE red.

## 4. Surfaces and depth

1. **No borders, except the glass edge.**
   - Boxes separate by frost, a 1px glass edge, top light and shadow (§2). The edge is part of the glass, not a drawn border.
   - The only other lines are faint row dividers (`--hairline`).
   - The "New project" slot is a quiet glass card, not a dashed outline.
2. **Flat inside.**
   - No gradients and no rings inside boxes.
   - Buttons are flat fills. Primary is the brand blue; outline buttons are a recessed fill; AI buttons use the primary.
3. **Inset rule** (light always comes from above):
   - **Recessed** means it *holds a value*: inputs, search, segmented tracks, kbd, value chips, progress tracks.
   - **Raised** means it *marks a choice or an action*: the selected segment, the active sidebar item, buttons, avatars, the highlighted menu item.
   - Never put a recess inside a recess. A control inside a well is raised.
4. **Corners:**

   | Element | Radius |
   |---|---|
   | Sheet | 10 |
   | Panels, tiles, cards, menus | 8 |
   | Wells, controls | 6 |
   | Chips and badges | 4 |
   | kbd | 3 |
   | Pills and dots | 999 |

5. **One focal element per page.** It stands out through size and content, not an edge or a wash. On Overview it's the Activity tile.

## 5. Type
- **Fonts:**
  - Inter v4 for the UI: variable, with the optical-size axis, and `cv01` and `ss03` switched on.
  - JetBrains Mono only for code, paths, IDs and env names.
  - Tabular figures everywhere.
- **Scale:**

  | Role | Size / weight |
  |---|---|
  | Greeting | 30 / 650, −.03em |
  | Page title (Overview, Data, Runs) | 26 / 650 |
  | Section | 15 / 600 |
  | Panel | 13.5 / 600 |
  | Body | 13 |
  | Meta | 12 |
  | Mono | 11–12 |
  | Hero numbers | 28–48 / 600, −.03em |

- **Labels are sentence case.** No uppercase eyebrows.

## 6. Space, vectors and art

**Starfield** (`assets/accent.js`, ported to `handoff/starfield.js`):
- **Where:** inside a project, a fixed sky behind the app at 16% (dark) or 40% (light). Projects home paints its own fuller sky (below).
- **Rendering:** painted on a canvas with a seeded random sequence, so the sky is the same every time. It redraws on resize and theme change.
- **Density and size:** about 1 star per 2,400 px². Sizes follow a power law: mostly 0.3px pinpricks, up to about 2.2px.
- **Halos and spikes:** stars larger than 1px get a soft halo (about 1 in 60). Stars larger than 2.45px get thin diffraction spikes (about 1 in 300), roughly 2–3 per screen.
- **Colour temperature (dark):** white 55%, blue-white 25%, warm 14%, orange 6%.
- **Light:** the same stars in navy ink (`rgb(28 52 118)`), so the sky stays visible on white.
- **Star dust:** empty fields use a generated star-dust tile instead of a grid.
- **Sparkles:** at most 3 small four-point glints at focal points, at 50% opacity. None on Projects home.
- **Variants:** `?accent=scaffold` and `?accent=none` exist for comparison only, and their switcher appears only with `?compare` (or `?accent=` / `?layout=`). **Stars is the design.**

**Projects home sky** (`assets/home-sky.js`, ported as `paintHomeSky`). One composition in both themes, painted on a canvas the height of the page:
1. A slight gradient: `#0c1120` at the top settling to `#050507` by about 760px (dark); flat white (light).
2. The logo's two colours as blooms: steel blue `rgb(74 118 222)` from the top left, aqua `rgb(56 186 204)` from the right, a brighter meeting point behind the greeting.
3. Stars at about 1 per 1,100 px², full strength at the top and thinning by 65% toward the cards.
4. A faint Milky Way band rising to the left across the top, with its own fine star dust.
- **Never** a planet, horizon, or other large illustration. It was tried and rejected as too much.

**Line art** (`assets/art.js`, `LunaArt`):
- **Drawn from real data:** the system diagram (routes → table → app, plus the secret), request path, table lineage, release path, app thumbnail, project glyphs, template sketches, run timeline and workflow canvas.
- **Line colour:** lines are neutral (`--art-line`). **One focal node per drawing** gets the brand stroke and a 4px glow.
- **Packets** (dots moving along wires) fire **once per real event**, through `LunaArt.pulse` / `pulseWire`. Wires are still when idle. **Nothing loops.**

## 7. Motion
- **Tokens:**
  - Durations: `--d-instant` 80, `--d-fast` 140, `--d-base` 200, `--d-slow` 280.
  - Easing: out is `cubic-bezier(.16,1,.3,1)`, in-out is `(.65,0,.35,1)`.
  - **No bounce and no springs.**
- **Motion appears only when state changes:**
  - **Navigation:** the sidebar highlight slides between pages, and tab underlines and segmented thumbs slide.
  - **Live updates:** digits roll, staggered 20ms right to left. New rows open smoothly, and a running dot rings while something is in flight.
  - **Feedback:** copy turns into a check. Menus scale in from their anchor, sheets slide in and toasts rise.
  - **Release:** a light beam runs around the Release sheet only while promoting.
- **Reduced motion:** fades only. No packets and no beam.

## 8. Components (`assets/kit.js` and page-level builds)
All of these were rebuilt in this style from shadcn, Origin, Kibo and AI Elements patterns. None were imported as-is.

| Component | Where | What it does |
|---|---|---|
| **Toasts** | All pages | Bottom-right, at most 3, tone shown with a dot. A busy toast updates to ok or error; Undo actions |
| **Release sheet** | Top bar Release, ⇧R | A 4-step stepper: Check code → Secrets (fix inline) → Review routes → Promote. Beam while promoting, then "v1 is live" |
| **Project switcher** | Top bar, ⌘P | Searchable menu |
| **Settings sheet** | Sidebar | General, Environments, Danger zone |
| **⌘K palette** | All pages | Glass overlay for jumping and actions |
| **AI change flow** | Backend | Shimmering status, a step log, a streamed explanation and a diff. Apply really edits the code; also Discard and Undo |
| **Route picker** | Backend test panel | Combobox grouped by module, with method chips |
| **Trace waterfall and request path** | Backend, Runs | Timed to the real trace |
| **Stack trace** | Runs, failed run | Your frames first, library frames folded; click a frame to open that line |
| **Table tools** | Data | Columns menu; filter chips that update rows, total and profiles; row actions with Undo; CSV import with dropzone, preview and mapping |
| **30-day activity and snippet** | Overview | Calendar grid with failures marked; copy as URL, curl or Python |
| **Workflow editor** | Workflows | Recessed canvas with raised nodes and entity-hued icons. Inspector, minimap, glass toolbar and an animated Test run |

## 9. Small details
- **Icons:** Lucide at stroke 1.75; 14px in controls, 15px in navigation.
- **Tooltips:** appear after 500ms, then instantly for the next 300ms. They include `kbd` hints.
- **Timestamps:** relative, with the exact time on hover (`data-ts`).
- **Empty values:** show "—", with the reason in a tooltip.
- **Pressed state:** clickable cards and buttons press to `scale(.98)`.
- **Hit targets:** nothing clickable under 24px; sidebar items 30px; icon buttons 28px.
- **Hover:** sidebar items and table rows get a soft fill (`foreground` at 6% and 4%).
- **Focus ring:** 2px with a 2px offset.
- **Truncation:** an ellipsis plus a tooltip with the full text.
- **Selection:** uses the accent tint.
- **Scrollbars:** thin, and appear on hover.
- **Never reuse these class names:** `.k .s .n .f .c .d .sep .empty .table .grid .hidden .collapse`. They collide with syntax colours or Tailwind.

## 10. Guardrails (rejected; don't reintroduce)
- Different UIs for light and dark. The themes differ only in palette.
- Solid cards in one theme and glass in the other.
- Grey light-mode backgrounds. Light is pure white.
- Large illustrations on Projects home (a planet horizon was tried and rejected).
- A warning banner on Projects home.
- Prototype controls (the accent and layout switchers) visible in the product.
- Drawn borders on boxes, other than the glass edge.
- Gradients or washes inside tiles.
- Rings on chips or wells.
- Purple or indigo as the brand.
- Warm-grey cards.
- A navy or blue veil over the background (it stops reading as black).
- Heavy stars or glow that hurt legibility.
- Glass over a flat background, with nothing behind it to blur.
- Pastel or Arc gradients.
- Glossy multicolour icon tiles.
- Rainbow node categories.
- Bouncy or springy motion.
- Decorative looping animation.
- Floating-card layouts (replaced by the Linear sheet).
- Gradients that fade to transparent black (they turn grey on white).
- Radical changes to the page structure.

**When in doubt, favour legibility.** Reduce the decoration, never the content's contrast.
