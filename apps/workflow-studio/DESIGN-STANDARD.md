# Design standard (for feature code)

The coding version of `demo/design/DESIGN.md`. Read that for the why; this page says **which component or utility to use**. The values live in `src/index.css` (tokens from `demo/design/handoff/tokens.css`); `npm run design:check` enforces the rules below.

## The idea in one line
Frosted glass over a starfield, one design in light and dark. The page itself is transparent (the sky shows), every box is glass, text sits in recesses inside the glass, nothing has a drawn border, and colour means something.

## Surfaces: what to use

| You need | Use | Notes |
|---|---|---|
| A page | nothing: render into the workspace sheet | The sheet (10px radius, 8px from the window) is drawn by `WorkspaceLayout`. Never give a page a background. Page padding: `px-8 py-7` (32/28px). |
| Page title row | `<PageHeader title meta actions icon hue>` (`ui/surface`) | 26px / 650 title, 12.5px meta row. |
| A card, bento tile, pane, stat strip, table container | `<Tile>` (or the `tile` class) | Glass: fill + blur + 1px glass edge + top light + shadow. `interactive` for clickable cards (hover frost, press .98). `editor` for split-view editor panes (`glass-editor`). Radius 8. |
| Text area inside a tile (table, list, code, log) | `<Well>` (or `well`) | A recess: dark 35% / white 60%. **Never a well in a well**; a control inside a well is raised. Radius 6. |
| Tile header | `<SectionHeader title sub icon hue actions>` | Icon in a 10% tint of its entity hue (`IconTile`). 13.5px / 600. |
| A control that **holds a value** (input, search, select, seg track, chip, kbd, progress track) | `Input`, `Textarea`, `Select`, `InputGroup`, `Segmented`, or `field` / `bg-recess` | Recessed fill, no border, 2px focus ring (`focus-visible:shadow-focus`). |
| The **chosen** thing in a group (seg thumb, active item, highlighted menu item) | `bg-raise shadow-raise-sm` (`raise`) | Raised. Segmented controls and tabs already slide their thumb/underline. |
| A button | `<Button variant>` | `default` (primary flat), `outline` (recess), `secondary` (grey fill), `ghost`, `destructive` (soft red), `ai` (primary + sparkle icon), `link`. Sizes `xs sm default lg icon-xs icon-sm icon icon-lg`. |
| Menu, popover, combobox, ⌘K, dialog, sheet, toast | `DropdownMenu`, `Popover`, `SearchMenuList` / `RoutePicker`, `Dialog`, `Sheet`, `toast()` | All `glass-overlay` already. Don't restyle their containers. |
| Status | `<StatusDot tone>`, `<Badge variant="success|warning|destructive|error|ai|neutral|outline|primary|stub">` | Status colours mean status only. |
| HTTP method | `<MethodChip method>` (= `MethodBadge`) | Keeps `data-method`. |
| Tooltip | `<Tip label kbd side>` around one element | One provider (500ms delay, 300ms warm) is in `__root.tsx`. Truncated text gets a Tip with the full text. |
| Numbers that change | `<Roll value>` | Digits roll right to left. Plain text at rest (e2e-safe). |
| A short series | `<Sparkline values labels>` | Last bar is the focal one. |
| 30-day calendar | `<ActivityGrid days>` + `<ActivityLegend>` | |
| URL / curl / Python with copy | `<Snippet forms>`; a lone copy button: `<CopyButton text what>` | |
| Release-style steps | `<Stepper steps>`; while in flight: `<Beam on>` inside a `relative` rounded parent | |
| AI code change | `<DiffView prev next onApply onDiscard>`, then `<DiffApplied … onUndo>` | Diff via `diffLines` (same LCS as `cm/lineDiff.ts`). |
| Python traceback | `<StackTrace traceback onOpenFrame>` | Your frames first, library frames folded. `parseTraceback` is exported. |
| A flat note line | `<Callout tone="warn|ai|info|error|ok" icon>` | No ring. |
| Empty state | `<EmptyState icon title actions>` | (`dust` is ignored: working pages have no star texture.) |
| Code | `CodeEditor` / `JsonViewer` | One CodeMirror theme (`ui/code-theme.ts`: `buildCodeTheme`, `watchCodeTheme`). Don't write `EditorView.theme` colour blocks; pass `buildCodeTheme()` to custom views (e.g. MergeView). |
| Chat | `shared/components/chat/*`, `shared/components/ai-elements/*` | One look: user bubble raised, assistant text on the panel, composer a recessed field with the primary send. |
| Top bar | `TopBarFrame`, `Brand`, `CrumbSep`, `SearchTrigger`, `ThemeToggle` (`features/project/components/TopBar.tsx`) | Every page's 48px bar uses these. |

## Colour
- **Neutrals are neutral (Linear-like, owner 2026-10-16):** greys with no blue or navy cast in either theme; the background has no coloured bloom. Blue is only for meaning: primary actions, focus, selection, the active indicator, the focal value of a chart.
- **Charts and line art are flat:** no gradients, glows or fades. Where a fill needs texture, use the crossed-line hatch pattern (see `shared/components/art/SystemDiagram.tsx`). Scales are neutral greys (`bg-foreground/…`); the latest bar / today / the focal node takes the primary.
- **Sky:** working pages have no stars or star texture. Only Projects home and the landing use `HomeSky` (coloured SVG sparkles, `--star-*` tokens, kept out of the reading column).
- **Glass:** cards and tiles are quiet neutral panels (a 3% fill, a 1px edge, a top light); real frost is for floating layers (menus, ⌘K, sheets, dialogs, toasts, top bar). Liquid glass (refraction, `shared/lib/liquid-glass.ts`, `useLiquidGlass`) is for small or fixed-size glass only: the sliding thumbs, the active sidebar lens, and the edges of dropdown menus and popovers. Chromium only, off under reduced motion. The sidebar and working panels stay flat (no glass with nothing behind it). The sticky top bar (Projects home, landing) frosts only once content scrolls under it.
- **Brand / primary:** `primary` (steel blue). Focus/selection: `ring`, `brand-soft`.
- **Entity hues** (icons and small tints only, never large fills): `text-hue-backend`, `-data`, `-runs`, `-apps`, `-secrets`, `-ai`, `-flow` (workflows), `-release`, `-home`. Tint: `bg-hue-data/10` or `IconTile`.
- **Status:** `success`, `warning`, `destructive` (+ `-text`, `-soft`). **Methods:** `text-method-get|post|put|patch|delete` on `bg-(--method-tint)`.
- **Text:** `foreground`, `muted-foreground`, `faint` (decorative only).
- **Lines:** `border-hairline` (`hairline-b` utility, or `shadow-[inset_0_-1px_0_var(--hairline)]` for a split).
- **Art lines:** `--art-line`; one focal node per drawing gets the brand stroke and `--glow`.

## Banned in feature code (`design:check` fails on these)
- Raw palette colours: `zinc-*`, `slate-*`, `blue-500`, `text-red-600`, …
- Hex / `rgb()` / `hsl()` / `oklch()` literals in class names or styles.
- `bg-white`, `bg-black`, `text-white`, `text-black` (use `bg-raise`, `text-primary-foreground` …).
- `border` / `border-border` / `border-panel-border` / `border-input` / `ring-1` / `ring-foreground/…` as a box edge. **Allowed:** `border-b border-hairline` for row dividers.
- `rounded-2xl|3xl|4xl` and pixel radii (`rounded-[6px]`). Use `rounded-sheet` (10), `rounded-panel` / `rounded-xl` (8), `rounded-well` / `rounded-ctl` / `rounded-lg` / `rounded-md` (6), `rounded-chip` / `rounded-sm` (4), `rounded-kbd` / `rounded-xs` (3), `rounded-full`.
- Gradients inside boxes (`bg-gradient-*`, `bg-linear-*`, `linear-gradient(`). Only `brand-fill` (logo, avatars, the AI node chip).
- Solid legacy surfaces `bg-card`, `bg-background`, `bg-popover`, `bg-canvas` (pages are transparent; boxes are `Tile`/`Well`).
- Uppercase eyebrows (`uppercase tracking-…`): labels are sentence case.

A line that must break a rule (generated-app colours, canvas painting, a third-party theme object) ends with `// design-ok: <reason>`.

**Allow-list:** `src/index.css`, `src/shared/lib/starfield.ts`, `src/features/app-builder/sandbox/**` (the generated app's own runtime), `src/app/routes/design.tsx`.

## Type, space, motion
- Inter v4 (`cv01`, `ss03`, tabular numbers) is the body font already; mono (`font-mono`) only for code, paths, IDs and env names.
- Scale: greeting 30/650 · page title 26/650 (`text-title`) · section 15/600 (`text-section`) · panel 13.5/600 (`text-panel-title`) · body 13 · meta 12 · mono 11–12 · hero numbers 28–48/600 `tracking-[-0.03em]`.
- 4pt spacing; bento gap 16 (`gap-4`). Hit targets ≥ 24px; sidebar items 30px; icon buttons 28px (`icon-sm`).
- Icons: Lucide at `stroke-[1.75]`, 14px in controls (the Button default), 15px in navigation.
- Motion only on state change: `duration-(--d-fast|base|slow)` (140/200/280ms), `ease-(--ease-out|io)`. No bounce, no loops. `animate-page-in`, `animate-row-in`, `animate-live-ring` exist. Respect reduced motion (global rule in `index.css`).

## Test ids and behaviour
- **Every new interactive element gets a `data-testid`. Never rename or remove an existing one**, nor the `data-*` attributes the specs read (`data-status`, `data-method`, `data-pressed`, `data-checked`, `data-state`, `data-done`, `data-trigger`, …), nor the class `text-warning-text` on the status pill.
- Elements whose `data-testid` ends in `-panel` must not scroll horizontally and must contain their buttons (`e2e/fixtures/layout.ts`). Clip decorations inside them; don't name new wrappers `*-panel`.
- No console warnings or errors (the e2e console policy fails on any). Common traps: missing `key`s, duplicate SVG ids (use `useId()`), refs on function components (wrap with `forwardRef`), Base UI `nativeButton` when `render` swaps the element.
- Restyle, don't re-architect: keep data flow, hooks and handlers.

## Checks before you finish
`npx tsc -b`, `npm run test:unit`, `npm run design:check -- <your files>` (clean), and the e2e specs that cover your pages.
