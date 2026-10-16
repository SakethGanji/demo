# Command Studio design system

**Final, 2026-10-16.** This single spec replaces the earlier layered version, which is kept in `_archive/DESIGN-history.md` for history only.

**The idea:** a quiet, near-black workspace in deep space. The work sits on frosted glass. Colour, light and motion appear only when they mean something.

**Sources of truth:**
- **Values:** `handoff/tokens.css`. If a number here and a token there disagree, the token wins.
- **Mockups:** `ui-redo/*.html`.
- **Final CSS decisions:** `assets/lean.css`, read in order; later sections win.

---

## 1. Layout

- **Linear-style shell.** The top bar (48px) and the sidebar (240px) sit directly on the background. All content lives in **one sheet** to the right of the sidebar and below the top bar. The sheet has a 10px radius, an 8px gap to the window edge, and is transparent in dark mode.
- **Page structure:**
  - Projects home.
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

## 2. Dark mode (the primary experience)

| Layer | Recipe |
|---|---|
| **Canvas** | `#050507`. Mostly black is the point. |
| **Sky** | A canvas starfield behind the app at **40% opacity** (§7) |
| **Nebula** | A barely-there bloom behind the content: radial glows of `rgb(70 110 200 / .09)` and `rgb(70 140 200 / .06)` |
| **Sheet** | Transparent, with no outline |
| **Glass** (tiles, cards, panes, pulse, alert) | `rgba(255,255,255,.06)`, `backdrop-filter: blur(11.6px)`, a top light of `inset 0 1px 0 rgba(255,255,255,.07)` and a shadow of `0 10px 40px rgb(0 0 0 / .5)`. **No outline.** Editor panes use `.045`. |
| **Glass chrome** (top bar, sidebar) | `rgba(255,255,255,.025)` with the same blur and no outline |
| **Glass overlays** (⌘K, menus, toasts, sheets) | A denser frost, about `.72` slate, with `blur(24px)` |
| **Recesses for text** (tables, lists, code, wells) | `rgb(0 0 0 / .35)`. Text never sits on bare frost. |

Glass only reads when something real is behind it, so the stars and the nebula are what the blur catches. Black stays the base, and the light lives on the glass.

## 3. Light mode
- **Pure white and cool slate:** canvas `#f8f9fb`, tiles `#f4f6fa`, white wells inside tiles, and a sheet with a hairline edge.
- **Glass on the top bar only:** `rgb(248 249 251 / .7)` with a 16px blur. Content stays solid.
- **Sky:** faint ink stars at very low strength. No nebula, apart from a 5% wash in one corner.
- **Never warm grey.** Cool slate matches the brand.

## 4. Colour

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

## 5. Surfaces and depth

1. **No borders.**
   - Boxes separate by fill, top light and shadow.
   - The only lines left are faint row dividers (`--hairline`) and the dashed edge of an empty slot.
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

## 6. Type
- **Fonts:**
  - Inter v4 for the UI: variable, with the optical-size axis, and `cv01` and `ss03` switched on.
  - JetBrains Mono only for code, paths, IDs and env names.
  - Tabular figures everywhere.
- **Scale:**

  | Role | Size / weight |
  |---|---|
  | Greeting | 30 / 650, −.03em |
  | Page title | 26 / 650 |
  | Section | 15 / 600 |
  | Panel | 13.5 / 600 |
  | Body | 13 |
  | Meta | 12 |
  | Mono | 11–12 |
  | Hero numbers | 28–48 / 600, −.03em |

- **Labels are sentence case.** No uppercase eyebrows.

## 7. Space, vectors and art

**Starfield** (`assets/accent.js`, ported to `handoff/starfield.js`):
- **Rendering:** painted on a canvas with a seeded random sequence, so the sky is the same every time. It redraws on resize and theme change.
- **Density and size:** about 1 star per 2,400 px². Sizes follow a power law: mostly 0.3px pinpricks, up to about 2.2px.
- **Halos and spikes:** stars larger than 1px get a soft halo (about 1 in 60). Stars larger than 2.45px get thin diffraction spikes (about 1 in 300), roughly 2–3 per screen.
- **Colour temperature:** white 55%, blue-white 25%, warm 14%, orange 6%.
- **Star dust:** empty fields use a generated star-dust tile instead of a grid.
- **Sparkles:** at most 3 small four-point glints at focal points, at 50% opacity.
- **Variants:** `?accent=scaffold` (rulers, numbered callouts, UTC status line) and `?accent=none` exist for comparison only. **Stars is the design.**

**Line art** (`assets/art.js`, `LunaArt`):
- **Drawn from real data:** the system diagram (routes → table → app, plus the secret), request path, table lineage, release path, app thumbnail, project glyphs, template sketches, run timeline and workflow canvas.
- **Line colour:** lines are neutral (`--art-line`). **One focal node per drawing** gets the brand stroke and a 4px glow.
- **Packets** (dots moving along wires) fire **once per real event**, through `LunaArt.pulse` / `pulseWire`. Wires are still when idle. **Nothing loops.**

## 8. Motion
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

## 9. Components (`assets/kit.js` and page-level builds)
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

## 10. Small details
- **Icons:** Lucide at stroke 1.75; 14px in controls, 15px in navigation.
- **Tooltips:** appear after 500ms, then instantly for the next 300ms. They include `kbd` hints.
- **Timestamps:** relative, with the exact time on hover (`data-ts`).
- **Empty values:** show "—", with the reason in a tooltip.
- **Pressed state:** clickable cards and buttons press to `scale(.98)`.
- **Focus ring:** 2px with a 2px offset.
- **Truncation:** an ellipsis plus a tooltip with the full text.
- **Selection:** uses the accent tint.
- **Scrollbars:** thin, and appear on hover.
- **Never reuse these class names:** `.k .s .n .f .c .d .sep .empty .table .grid .hidden .collapse`. They collide with syntax colours or Tailwind.

## 11. Guardrails (rejected; don't reintroduce)
- Borders or outlines on boxes.
- Gradients or washes inside tiles.
- Rings on chips or wells.
- Purple or indigo as the brand.
- Warm-grey cards.
- A navy or blue veil over the background (it stops reading as black).
- Heavy stars or glow that hurt legibility.
- Glass over a flat background.
- Pastel or Arc gradients.
- Glossy multicolour icon tiles.
- Rainbow node categories.
- Bouncy or springy motion.
- Decorative looping animation.
- Floating-card layouts (replaced by the Linear sheet).
- Radical changes to the page structure.

**When in doubt, favour legibility.** Reduce the decoration, never the content's contrast.
