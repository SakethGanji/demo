# Command Studio design system

This applies to every page. When in doubt, follow this file over what a page currently does.
Implemented in `assets/system.css`. Pages use these classes and tokens; pages never invent their own colours.

## 1. Surfaces: three levels, and no borders between them
| Level | What | How it looks |
|---|---|---|
| **Canvas** | The page background | `--canvas`, with a faint brand glow at the top |
| **Panel** | Floating cards: top bar, sidebar, main panel, cards | `--card`, a 1px alpha **ring** made with a shadow (not a border), and a soft shadow. In dark mode a light-catching gradient edge runs along the top |
| **Well** | Inset areas inside a panel: previews, art, code, tables, stat strips, empty states | `.well`: `--well` fill, an inner ring, `--r-well` radius, sitting 8px in from the panel edge |

- **Panel headers have no bottom border.** Spacing separates the header from the content.
- **Lines are allowed only** as row dividers in lists and tables (`--hairline`, about 6% alpha) and as dividers between cells in a stat strip.
- **No boxes nested inside other boxes beyond a well.** Never put a well inside a well.

## 1b. Inset scale: the same three levels, at control size
Implemented in `assets/inset.css`. Every small control is either **recessed** (a mini-well that *holds a value*) or **raised** (a mini-panel that *marks a choice or an action*). Light comes from above everywhere: a recess has a shadowed top edge, a raised thing has a lit top edge.

| Token | Recipe | Used for |
|---|---|---|
| `--inset-sm` | inner ring + 1px top inner shadow, on `--recess-fill` | kbd, check box, switch track, segmented track, progress tracks, `.url` / `.file-chip` / `.rows-chip`, search field, `.method` (ringed in its own colour) |
| `--inset-md` | inner ring + 2px soft top inner shadow, on `--recess-fill` | `.input`, `textarea`, dialog fields |
| `--inset-lg` | = the well (`.well`, `pre.json`) | wells and code blocks |
| `--raise-sm` | outer ring + soft 1–2px drop + top highlight, on `--raise-fill` | segmented thumb, active sidebar item / `.m-pill`, `.btn.outline`, active tab chip, menu/palette item on hover, badges on a well, the stat focus tile, the kbd inside the search field |
| `--raise-md` | `--raise-sm` + a 3–8px soft drop | free-standing tiles (reserved; not applied yet) |
| (panel) | the panel material in `system.css` | panels |

- **One ring.** `--edge-ring` is the only 1px ring alpha (≈8.5% ink in light, 8% white in dark), used by both families. Recesses draw it inside, raised things outside. The one exception is `--ctl-ring` (`--input`) on checkboxes and switches, which must be findable.
- **Dark mode** uses edges, not shadows: a recess gets a darkened top line, a raised thing a 7.5% white top line. Drop shadows stay small.
- **Fills:** `--recess-fill` is a little darker than its surface (3% ink / 20% black). `--raise-fill` is white in light and 7% white in dark.
- **Nesting:** never put a recess in a recess. A control inside a recess flips to raised (the search kbd). Anything raised that sits on a well takes `--raise-sm`, never more. Table rows in a well lift faintly on hover (`--row-raise`).
- **Tints:** tinted chips (status badges, methods) get a ring in `currentColor`, not the neutral ring.
- **Radii:** concentric, so inner = outer − padding. The track is `--r-ctl`, the thumb `--r-chip`, the check `--r-chip` − 2, and the kbd/method `--r-chip` − 1.
- **States:** a raised thing pressed goes flat (`--raise-press`). Focus is an `--ring` 1px ring plus the 3px soft ring.
- **Utilities:** `.recess`, `.raise`, and `.slot` (a dashed empty slot on a half-strength recess fill, `--slot-line`).

## 2. Spacing: a 4pt scale
`--s1` 4 · `--s2` 8 · `--s3` 12 · `--s4` 16 · `--s5` 20 · `--s6` 24 · `--s8` 32 · `--s10` 40

| What | Value |
|---|---|
| Shell gutters (top bar, sidebar, main) | 12 |
| Gap between panels | 16 |
| Panel padding | 20 |
| Panel header | 48 tall, 20 side padding |
| Well inset from the panel edge | 8 |
| Well padding | 12–16 |
| Page padding in the main panel | 28 vertical, 32 horizontal |
| Gap between sections | 24 |

## 3. Radii
| Token | Value | Used for |
|---|---|---|
| `--r-panel` | 12 | Panels |
| `--r-well` | 8 | Wells |
| `--r-ctl` | 8 | Buttons, inputs, segmented controls |
| `--r-chip` | 6 | Badges, method chips |
| `--r-kbd` | 4 | kbd |
| (full) | 999 | Pills and dots |

## 4. Colour
- **One accent:** indigo, `--primary`. The brand gradient (`--brand-1` → `--brand-2`, indigo to violet) is reserved for the brand mark, the project avatar, AI actions and the focal stroke in art.
- **Status colours only for status:** green, amber, red. AI uses the brand gradient, not a separate violet.
- **Light mode:** pure white canvas and panels with warm-ink lines (see §9; "Mist" is retired).
- **Dark mode, "Graphite":** near-black, panels lifted by a top highlight, and a faint grain.
- **Text:** `--foreground`, `--muted-foreground` (at least 4.5:1), and `--faint` for decorative text only.

## 5. Type
| Role | Size / weight |
|---|---|
| Page title | 26–30 / 650, -.03em |
| Section title | 15 / 600 |
| Panel title | 13.5 / 600 |
| Body | 13 |
| Meta | 12 |
| Mono labels | 11–12 |

- Numbers use tabular figures. Hero numbers are 28 / 600, -.03em.
- Mono is for code, paths, IDs and env names only.

## 6. Vectors (Linear-style)
Precise line art in the brand gradient, drawn from real data. Use these shared primitives (in `system.css` / `art.js`):
- `.vx-grid`: a hairline grid behind a hero, fading out with a radial mask.
- `.vx-beam`: a soft light beam from the top edge, behind a hero title. Use one per page at most.
- `.vx-edge`: a gradient light edge on a featured panel (the focal one only).
- `.vx-orbit`: concentric dashed rings, for empty states and onboarding.
- Art focal node: one element per drawing gets the brand glow (`.a-glow`). Everything else is neutral line work.
- At most **one hero vector per page**, plus small inline vectors where they carry data.

## 6b. Edges and empty space
The page is a drafting sheet: the hero grid is the sheet showing through, and edges carry quiet construction marks. This is structure, not decoration. Implemented in `assets/edges.css` and `assets/edges.js`.
| Piece | What it is | Where |
|---|---|---|
| **Registration mark** `.eg-reg` | A 9px `+` in `--art-line` | Wherever two shell gutters cross (auto, every shell page); in both margins level with a section title (`data-edge="reg"`) |
| **Field** `.eg-field` | The 32px hero grid continuing into empty space after content, fading down and out, with a **ruler** (ticks every 8px, longer every 32px) hanging from the edge where the content ends, and two `+` on the first major row | Sidebar between nav and footer (auto); below the runs list, below the data table, under the overview left column, under the projects templates (`data-edge="field"`) |
| **Leader** `.eg-leader` | The dotted rule from the data end-of-table line, used to lead a section title to its controls or to the column edge | Projects section titles (`data-edge="leader"`) |

- **Hooks:** `data-edge="field"` with `data-edge-from="<selector>"` (the content it starts below), plus optional `data-edge-until`, `data-edge-gap` (12), `data-edge-inset` (8) and `data-edge-quiet` (for fields on a panel rather than in a well). `data-edge="reg"` and `data-edge="leader"` take no options. `Edges.refresh()` re-measures.
- **Fields measure themselves.** They follow the content as rows are added or expanded, and hide when the gap is under 40px. They never sit behind content.
- **Neutral only:** `--art-line`, `--art-faint`. No brand colour on edges; the page's one accent stays in its hero art.
- **Inert and static:** `aria-hidden`, `pointer-events: none`, no motion.
- **Don't:** add coordinates or labels, put a field inside a hero (the hero already has `.vx-grid`), or put a field in a code editor.

## 7. Motion
Use the existing tokens (`--d-instant` 80 / `--d-fast` 140 / `--d-base` 200 / `--d-slow` 280; `--ease-out`, `--ease-io`). Motion is only for state changes and interaction. No bounce.

## 8. Small things, everywhere
- **Icons:** Lucide at stroke 1.75, sized 14 in controls and 15 in navigation.
- **Focus:** a 2px focus ring offset by 2.
- **Selection and scrollbars:** selection uses the accent tint. Scrollbars are thin and appear on hover.
- **Keyboard hints:** `kbd` hints on primary actions, via `data-kbd` tooltips.
- **Timestamps:** relative time with the exact time in a tooltip (`data-ts`).
- **Empty values:** use "—" with a tooltip that explains why.
- **Clickable things:** they get a hover state and a pressed `scale(.98)`.
- **Truncation:** long text truncates with an ellipsis and shows a tooltip.
- **Class names:** never reuse the global syntax-colour class names `.k .s .n .f .c .d`, or `.sep` / `.empty`.

## 9. "Paper & Graphite" (2025–26 refresh, `assets/paper.css`)
Based on research into current product UIs: Linear, Vercel Geist, GitHub Primer, Cursor, Raycast, Resend, Attio, Notion and Claude.
- **Light, "Paper":** warm stone canvas `#efeeea` (top `#f4f3f0`), pure white panels, wells `#f7f6f3`. Every line and shadow is warm ink `rgb(38 34 28 / α)`, never blue-grey. Leaders have moved away from cool, blue-cast greys.
- **Dark, "Graphite":** unchanged (it already matches Linear's `#08090a` / `#0f1011`). Grids in dark are a **dot lattice** on the 32px module (`--grid-dot` 13% white) with a radial fade, never a solid line mesh. Lines stay for data guides only.
- **Corners:** panel 12, well 8, control 8, chip 6, kbd 4. The consensus is not sharper; coziness comes from colour and shadow, not from rounder shapes.
- **Type:** Inter v4 with the optical-size axis (headings get Inter Display forms automatically) and `cv01` / `ss03`.
- **Accent:** indigo nudged slightly toward violet in light (`#5753c6`).

## 10. Kit: interactive components (`assets/kit.css`, `assets/kit.js`)
Shared floating layers. Every class is `kit-*`. The API is the contract comment at the top of `kit.js`.
| Piece | API | Look and motion |
|---|---|---|
| **Toast** | `Kit.toast({ title, desc?, tone?, action?, duration? })` → `{ update, close }` | Bottom-right stack, at most 3. Solid `--popover` + `--raise-md`. Tone is a `.dot` only (never a tinted fill); `busy` = the live dot. Title 13/500, muted desc, ghost-sm action. Enter: fade + 8px rise, `--d-base` `--ease-out`. Exit: fade, `--d-fast`. 4s, paused on hover; `busy` stays until `update()`. Moves left of an open sheet |
| **Sheet** | `Kit.sheet({ title, body, footer?, width? })` → `{ el, body, setFooter, close }` | Right-hand panel floating 12px from the viewport edges, panel material, dimming scrim (no blur). Slides in over `--d-slow` `--ease-out`. Header (15/600 + close), scrolling body, footer with no rule. Esc / scrim close; focus moves in and back |
| **Menu** | `Kit.menu(anchor, { items, search?, placeholder?, width?, align? })` → `{ close }` | `.menu` glass. Recessed search field, group labels, hairline separators. Items: icon, status dot, method chip (`method` or `icon:'GET'`), sub, kbd, check for `checked`. Highlight is raised (`--raise-sm`), like ⌘K. ↑↓ ↵ Esc, outside click. Enter: opacity + scale .97 from the anchor side, `--d-fast` |
| **Beam** | `Kit.beam(el, true \| false \| 'once')` | A 64px `--brand-1` → `--brand-2` light travelling the element's rounded border (SVG dash on the exact border, soft `--glow`). `true`: 3s linear laps, then fades over `--d-slow` on `false`. `'once'`: one 560ms lap, `--ease-io`. Inert and absolute: never changes layout |

- **Content helpers** for sheets: `.kit-lead` (intro line), `.kit-sec` (section head), `.kit-kv` (key/value rows in a `.well`), `.kit-steps` (vertical stepper in a `.well`).
- **Stepper dots:** done = raised neutral with a status check; current = raised `--primary`; pending = recessed. The rail is a hairline.
- **Nesting:** a field inside a stepper sits on a raised `.kit-fix` tile, so it is never a recess in a recess.
- **One beam at a time**, only for a state that is in flight (a promote, a deploy). Never decorative.
- **Reduced motion:** no slide or rise (fades only), and no beam: `Kit.beam` sets a static `.vx-edge` instead.
- **Wiring** (in `kit.js`, delegated, after the page's own handlers): `[data-kit="switcher"]` → project menu, `[data-kit="release"]` / "Create release" / ⇧R → Release sheet, `[data-kit="settings"]` → Settings sheet, ⌘P → switcher. `Kit.open.{release,settings,switcher}()` opens them from code.

## 11. Final direction: "lean in" (`assets/lean.css`, supersedes conflicting rules above)
- **Layout: Linear.** The top bar and sidebar sit on the canvas, and the content is one inset sheet (10px radius). Floating and Docked remain available via `?layout=` but aren't the design.
- **Corners:** panel and tile 8 · well 6 · control 6 · chip and badge 4 · kbd 3.
- **Flat inside:** only the outer sheet and the tiles have an edge (a 1px hairline ring). Everything inside separates by **fill and spacing alone**: wells, inputs, search, segmented tracks, kbd, method chips, badges, code blocks. No inner rings, no inner gradients.
  - Buttons are flat fills. Primary is indigo; AI is flat violet.
  - The brand gradient appears only on the logo, project avatars and the focal stroke in art.
  - The focal tile gets a solid 1px indigo edge, with no wash.
- **Entity colour:** one hue per kind of thing, everywhere it appears (sidebar icons, section icon tiles, card footers, workflow nodes). It is used for icons and small tints only, never for large fills.

  | Entity | Dark | Light |
  |---|---|---|
  | backend | `#8b93ff` | `#4f58d6` |
  | data | `#2dd4bf` | `#0d8a7c` |
  | runs | `#38bdf8` | `#0b7fc0` |
  | apps | `#fb923c` | `#c45a12` |
  | secrets | `#facc15` | `#a66a00` |
  | ai | `#c084fc` | `#8a4fd8` |
  | workflows | `#f472b6` | `#c03a85` |
  | release | `#34d399` | `#0a8a5c` |

- **Space (dark):** a nebula of four low-alpha radial glows (indigo, violet, teal, sky) and a sparse starfield on the canvas only. The sheet catches a faint indigo glow along its top edge. Light mode uses two faint corner washes on `#fbfbfa`.

## 12. Space theme and azure brand (supersedes the colour notes in §4, §9 and §11)
- **Brand: electric azure → cyan.** Indigo and violet are retired as brand colours.
  - Primary: `#1663f0` light, `#2a74f0` dark. Ring and links: `#2f7cff` / `#5ea3ff`.
  - Brand gradient: `#3b8bff → #22d3ee`, used on the logo and avatars only.
  - AI buttons use the primary; the AI identity lives in the violet sparkle icon only.
- **Entity hues, updated:**

  | Entity | Dark | Light |
  |---|---|---|
  | backend | `#5ea3ff` | `#1663f0` |
  | data | `#34d399` | `#0b8a5a` |
  | runs | `#22d3ee` | `#0b8aa8` |
  | release | `#a3e635` | `#4d7c0f` |

  Apps, secrets, ai and workflows keep their §11 hues.
- **Tiles and cards inside the sheet:** flat fills (`--tile`) with **no border, no shadow, no gradient**. The sheet itself keeps one hairline.
- **Space (`assets/accent.js`, default accent `stars`):**
  - A canvas starfield sits behind the app (always on in dark), plus a masked one inside the sheet margins.
  - Sizes follow a power law: about 1 in 60 stars gets a halo, about 1 in 300 gets diffraction spikes.
  - Colour temperatures: white 55%, blue-white 25%, warm 14%, orange 6%.
  - A seeded RNG keeps the sky stable; it redraws on resize and theme change.
  - Empty fields use a generated star-dust tile (`--star-tile`).
  - The nebula is azure, cyan, teal and sky glows.
  - Light mode draws faint ink stars.
- **Variants:** `?accent=scaffold` (rulers, numbered tile callouts, corner ticks, UTC status line) and `?accent=none` stay available.

## 13. Neutral pass (latest; supersedes the colour values in §12)
- **Brand: calm steel-blue.** Primary `#2a5fc4` light / `#3567c8` dark. Brand gradient `#5b8ee0 → #6cc3d6` (logo `#3a6fd0 → #5fb3c6`).
- **Entity hues, desaturated about 30%** (dark): backend `#86a8e0`, data `#6cc4a2`, runs `#74c4d4`, apps `#dca27a`, secrets `#d6bf6a`, ai `#ad9ad9`, workflows `#d495b6`, release `#a9c870`.
- **Cool slate neutrals, never warm grey,** so the surfaces match the sky:

  | Token | Dark | Light |
  |---|---|---|
  | Sheet | `#0e1016` | — |
  | Tile | `#131722` | `#f4f6fa` |
  | Popover | `#151925` | — |
  | Muted text | `#8b93a7` | `#5d6475` |
  | Hairline | `rgb(170 190 255 / .07)` | — |
  | Canvas | — | `#f8f9fb` |
  | Wells | — | white, inside the tiles |

- **Space is dimmer:** the nebula is about 60% of its §12 strength, the sky canvas is at .8 opacity and the sheet stars at .7. Inactive sidebar icons are at .6.

## 14. Frosted glass over space (dark; supersedes the "no glass on panels" rule)
- **Owner's call:** the top bar, the sidebar and every box are frosted glass, so the sky shows through, softly blurred.
- **Chrome** (top bar, sidebar): `rgb(16 20 30 / .40)`, `blur(22px) saturate(170%)`, with a single hairline edge.
- **Boxes** (tiles, cards, templates, pulse, alert, editor panels): `rgb(40 52 80 / .30)`, the same blur, an inset 1px edge `rgb(190 210 255 / .12)`, and a lit top line `rgb(210 228 255 / .22)`. Editor panels use a denser `.62` fill.
- **The sheet** is only a light veil (`rgb(10 12 18 / .22)`) with **no blur**, so each box blurs the real sky behind the app.
- **Text** never sits on bare frost. Wells, tables, code and lists are deeper recesses (`rgb(8 10 16 / .5)`).
- **The nebula bloom** sits behind the content (`body::after`) so the glass has colour to catch. The workspace layer must stay transparent.
- **Overlays** (palette, menus, toasts, sheets) use a stronger frost, `.72`, with `blur(24px)`.
- **Light mode:** only the top bar is frosted; the content stays solid.
- **Fallback:** without `backdrop-filter`, boxes are solid `#131722`.

## 15. Blue glass (latest; refines §14)
The owner wants the glass bluer and denser: the sky should only hint through, and everything must stay easy to read.

| Surface | Fill (dark) |
|---|---|
| Boxes | `rgb(24 38 70 / .58)` |
| Editor panels | `rgb(18 28 52 / .74)` |
| Top bar and sidebar | `rgb(14 22 42 / .62)` |
| Sheet veil | `rgb(12 20 38 / .45)` |
| Recesses for text | `rgb(8 14 28 / .55)` |

- **Blur:** 26px, saturate 140%.
- **Edge:** `rgb(150 185 255 / .13)`. **Top highlight:** `/ .16`.
- **Quieter background:** sky canvas at .55, nebula bloom about 60% of §14, sparkles at .5, art glow cut to one 4px drop-shadow.

## 16. css.glass recipe (latest; replaces the §15 box fill)
Owner-supplied, from css.glass:
- **Boxes:** `rgba(255,255,255,.07)` fill, `blur(11.6px)`, and `box-shadow: 0 4px 30px rgba(0,0,0,.1)`. On top of that, an inset 1px `rgba(255,255,255,.10)` edge and a `.14` top highlight.
- **Editor panels:** `.05`. **Chrome:** `.04`.
- **The blue comes from behind,** not from the glass: a navy sheet veil `rgb(12 22 44 / .55)` plus the nebula. Text recesses are `rgb(6 12 26 / .45)`.
- **Corners stay at 8px.** The recipe's 16px goes against the earlier "tighter corners" decision; use `--r-panel: 16px` to try it.

## 17. Mostly black, glass pops (latest; supersedes §15–16 backgrounds)
The owner wants the page mostly black, the space barely there, and the glass standing out.
- **Background:** canvas `#050507`. The sheet is transparent, with only a 1px `rgb(255 255 255 / .06)` outline. The nebula bloom is barely there (alpha .06–.09), and the sky canvas is at .4.
- **Glass boxes:** `rgba(255,255,255,.055)` with a 11.6px blur. The edge is inset 1px `rgba(255,255,255,.11)`, the top highlight is a brighter inset 1px `rgba(255,255,255,.24)`, and the drop shadow is `0 8px 32px rgb(0 0 0 / .55)`. It's the highlight and the shadow that make the glass stand out on black.
- **Text recesses:** `rgb(0 0 0 / .35)`.

## 18. Edgeless glass (latest; refines §17)
- **No outlines anywhere in dark mode:** not on boxes, the sheet, the top bar or the sidebar.
- **A box is defined by:**
  - **Frost:** `rgba(255,255,255,.06)` plus the 11.6px blur. Editor panels use `.045`.
  - **A whisper of top light:** inset `0 1px 0 rgba(255,255,255,.07)`.
  - **A soft shadow:** `0 10px 40px rgb(0 0 0 / .5)`.
- **The New project slot** keeps a faint `.07` dashed edge, because it's an empty slot.
