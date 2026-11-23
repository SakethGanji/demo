/** Studio UI kit — layout and type primitives (provided by Studio, refreshed every builder turn; do not edit).
 * Box, Stack, Grid, Text, Heading, Field, Divider, Spacer: build page layout from these instead of `<div className="flex ...">`.
 * Spacing, alignment, columns, size, weight and tone are enum props on the theme's scale, so the visual editor can change them;
 * colours come from the theme tokens (kit/theme.css, overridable in the app's index.css). Also the colour-scheme switch
 * (light / dark / system) that AppShell shows. All class names are literal so both Tailwind builds (preview + publish) see them. */
import React, { useEffect, useState } from 'react'
import { cx } from './ui'

type Space = '0' | '1' | '2' | '3' | '4' | '5' | '6' | '8' | '10' | '12'
type Align = 'start' | 'center' | 'end' | 'stretch' | 'baseline'
type Justify = 'start' | 'center' | 'end' | 'between' | 'around'
type TextTone = 'default' | 'muted' | 'soft' | 'faint' | 'primary' | 'success' | 'warning' | 'danger'
type Base = Omit<React.HTMLAttributes<HTMLElement>, 'color'> & { className?: string; children?: React.ReactNode }

const GAP: Record<Space, string> = { 0: 'gap-0', 1: 'gap-1', 2: 'gap-2', 3: 'gap-3', 4: 'gap-4', 5: 'gap-5', 6: 'gap-6', 8: 'gap-8', 10: 'gap-10', 12: 'gap-12' }
const PAD: Record<Space, string> = { 0: 'p-0', 1: 'p-1', 2: 'p-2', 3: 'p-3', 4: 'p-4', 5: 'p-5', 6: 'p-6', 8: 'p-8', 10: 'p-10', 12: 'p-12' }
const ALIGN: Record<Align, string> = { start: 'items-start', center: 'items-center', end: 'items-end', stretch: 'items-stretch', baseline: 'items-baseline' }
const JUSTIFY: Record<Justify, string> = { start: 'justify-start', center: 'justify-center', end: 'justify-end', between: 'justify-between', around: 'justify-around' }
const TONE: Record<TextTone, string> = {
  default: 'text-foreground', muted: 'text-muted-foreground', soft: 'text-soft-foreground', faint: 'text-faint-foreground',
  primary: 'text-primary', success: 'text-success', warning: 'text-warning', danger: 'text-danger',
}
const SURFACE = { none: '', card: 'bg-card', muted: 'bg-muted', subtle: 'bg-subtle', primary: 'bg-primary-soft', success: 'bg-success-soft', warning: 'bg-warning-soft', danger: 'bg-danger-soft' }
const RADIUS = { none: 'rounded-none', sm: 'rounded-sm', md: 'rounded-md', lg: 'rounded-lg', xl: 'rounded-xl', full: 'rounded-full' }
const SHADOW = { none: '', sm: 'shadow-sm', md: 'shadow-md', lg: 'shadow-lg' }

/** A plain container with optional padding, surface, border, radius and shadow (a panel without a header; use Card for one with a title).
 * @category layout
 * @example <Box padding="4" surface="card" border radius="xl">Content</Box> */
export function Box({ as = 'div', padding = '0', surface = 'none', border = false, radius = 'none', shadow = 'none', className, children, ...rest }: Base & {
  /** HTML element to render.
   * @control select @options div|section|article|aside|header|footer|main|nav @group Behavior @default div */
  as?: 'div' | 'section' | 'article' | 'aside' | 'header' | 'footer' | 'main' | 'nav'
  /** Inner spacing on the theme scale.
   * @control spacing @options 0|1|2|3|4|5|6|8|10|12 @group Layout @default 0 */
  padding?: Space
  /** Background fill from the theme.
   * @control select @options none|card|muted|subtle|primary|success|warning|danger @group Style @default none */
  surface?: keyof typeof SURFACE
  /** Draw the theme border around it.
   * @control boolean @group Style @default false */
  border?: boolean
  /** Corner rounding.
   * @control select @options none|sm|md|lg|xl|full @group Style @default none */
  radius?: keyof typeof RADIUS
  /** Drop shadow.
   * @control select @options none|sm|md|lg @group Style @default none */
  shadow?: keyof typeof SHADOW
}) {
  return React.createElement(as, {
    'data-vcomp': 'Box', ...rest,
    className: cx('min-w-0', PAD[padding], SURFACE[surface], border && 'border border-border', radius !== 'none' && RADIUS[radius], SHADOW[shadow], className),
  }, children)
}

/** Lays its children out in a column (default) or a row with even spacing: the replacement for `<div className="flex ...">`.
 * @category layout
 * @example <Stack direction="row" gap="2" align="center" justify="between"><Heading level={2}>Requests</Heading><Button>New</Button></Stack> */
export function Stack({ as = 'div', direction = 'col', gap = '3', align, justify, wrap = false, padding = '0', grow = false, className, children, ...rest }: Base & {
  /** HTML element to render.
   * @control select @options div|section|header|footer|nav|ul|ol|form @group Behavior @default div */
  as?: 'div' | 'section' | 'header' | 'footer' | 'nav' | 'ul' | 'ol' | 'form'
  /** Column (top to bottom) or row (left to right).
   * @control select @options col|row @group Layout @default col */
  direction?: 'col' | 'row'
  /** Space between children on the theme scale.
   * @control spacing @options 0|1|2|3|4|5|6|8|10|12 @group Layout @default 3 */
  gap?: Space
  /** Cross-axis alignment of the children.
   * @control select @options start|center|end|stretch|baseline @group Layout */
  align?: Align
  /** Main-axis distribution of the children.
   * @control select @options start|center|end|between|around @group Layout */
  justify?: Justify
  /** Let a row wrap onto more lines when it runs out of room.
   * @control boolean @group Layout @default false */
  wrap?: boolean
  /** Inner spacing on the theme scale.
   * @control spacing @options 0|1|2|3|4|5|6|8|10|12 @group Layout @default 0 */
  padding?: Space
  /** Fill the remaining space of the parent stack (a scroll area or main column).
   * @control boolean @group Layout @default false */
  grow?: boolean
}) {
  return React.createElement(as, {
    'data-vcomp': 'Stack', ...rest,
    className: cx('flex min-w-0', direction === 'row' ? 'flex-row' : 'flex-col', GAP[gap], align && ALIGN[align], justify && JUSTIFY[justify],
      wrap && 'flex-wrap', PAD[padding], grow && 'min-h-0 flex-1', className),
  }, children)
}

type Cols = 1 | 2 | 3 | 4 | 5 | 6 | 12
const COLS: Record<Cols, string> = { 1: 'grid-cols-1', 2: 'grid-cols-2', 3: 'grid-cols-3', 4: 'grid-cols-4', 5: 'grid-cols-5', 6: 'grid-cols-6', 12: 'grid-cols-12' }
const COLS_MD: Record<Cols, string> = { 1: 'md:grid-cols-1', 2: 'md:grid-cols-2', 3: 'md:grid-cols-3', 4: 'md:grid-cols-4', 5: 'md:grid-cols-5', 6: 'md:grid-cols-6', 12: 'md:grid-cols-12' }
const COLS_LG: Record<Cols, string> = { 1: 'lg:grid-cols-1', 2: 'lg:grid-cols-2', 3: 'lg:grid-cols-3', 4: 'lg:grid-cols-4', 5: 'lg:grid-cols-5', 6: 'lg:grid-cols-6', 12: 'lg:grid-cols-12' }

/** Equal-width columns (tiles, cards, form fields side by side); responsive with colsMd / colsLg.
 * @category layout
 * @example <Grid cols={1} colsMd={3} gap="4"><Stat label="Open" value={12} /><Stat label="Approved" value={30} /><Stat label="Rejected" value={2} /></Grid> */
export function Grid({ as = 'div', cols = 1, colsMd, colsLg, gap = '4', align, className, children, ...rest }: Base & {
  /** HTML element to render.
   * @control select @options div|section|ul @group Behavior @default div */
  as?: 'div' | 'section' | 'ul'
  /** Columns on small screens.
   * @control select @options 1|2|3|4|5|6|12 @group Layout @default 1 */
  cols?: Cols
  /** Columns from the md breakpoint (tablet) up.
   * @control select @options 1|2|3|4|5|6|12 @group Layout */
  colsMd?: Cols
  /** Columns from the lg breakpoint (desktop) up.
   * @control select @options 1|2|3|4|5|6|12 @group Layout */
  colsLg?: Cols
  /** Space between cells on the theme scale.
   * @control spacing @options 0|1|2|3|4|5|6|8|10|12 @group Layout @default 4 */
  gap?: Space
  /** Vertical alignment of the cells.
   * @control select @options start|center|end|stretch|baseline @group Layout */
  align?: Align
}) {
  return React.createElement(as, {
    'data-vcomp': 'Grid', ...rest,
    className: cx('grid min-w-0', COLS[cols], colsMd && COLS_MD[colsMd], colsLg && COLS_LG[colsLg], GAP[gap], align && ALIGN[align], className),
  }, children)
}

const SIZE = { xs: 'text-xs', sm: 'text-sm', base: 'text-base', lg: 'text-lg', xl: 'text-xl', '2xl': 'text-2xl' }
const WEIGHT = { normal: 'font-normal', medium: 'font-medium', semibold: 'font-semibold', bold: 'font-bold' }
const TEXT_ALIGN = { left: 'text-left', center: 'text-center', right: 'text-right' }

/** Body text: a paragraph, a label, a caption or a number, styled from the theme scale.
 * @category display
 * @example <Text size="sm" tone="muted">Submitted 2 days ago</Text> */
export function Text({ as = 'p', size = 'sm', weight = 'normal', tone = 'default', align, truncate = false, mono = false, className, children, ...rest }: Base & {
  /** HTML element to render.
   * @control select @options p|span|div|label|small|strong @group Behavior @default p */
  as?: 'p' | 'span' | 'div' | 'label' | 'small' | 'strong'
  /** Font size on the theme scale.
   * @control select @options xs|sm|base|lg|xl|2xl @group Style @default sm */
  size?: keyof typeof SIZE
  /** Font weight.
   * @control select @options normal|medium|semibold|bold @group Style @default normal */
  weight?: keyof typeof WEIGHT
  /** Text colour from the theme.
   * @control select @options default|muted|soft|faint|primary|success|warning|danger @group Style @default default */
  tone?: TextTone
  /** Horizontal alignment.
   * @control select @options left|center|right @group Layout */
  align?: keyof typeof TEXT_ALIGN
  /** Cut to one line with an ellipsis.
   * @control boolean @group Style @default false */
  truncate?: boolean
  /** Monospaced digits and letters (ids, codes, amounts).
   * @control boolean @group Style @default false */
  mono?: boolean
  /** The text.
   * @control text @group Content */
  children?: React.ReactNode
}) {
  return React.createElement(as, {
    'data-vcomp': 'Text', ...rest,
    className: cx('m-0', SIZE[size], WEIGHT[weight], TONE[tone], align && TEXT_ALIGN[align], truncate && 'min-w-0 truncate', mono && 'font-mono', className),
  }, children)
}

const HEADING = { 1: 'text-2xl font-semibold tracking-tight', 2: 'text-xl font-semibold', 3: 'text-base font-semibold', 4: 'text-sm font-semibold' }
const HEADING_TAG = { 1: 'h1', 2: 'h2', 3: 'h3', 4: 'h4' } as const

/** A section or page heading (h1-h4) in the theme's type scale.
 * @category display
 * @example <Heading level={2}>Pending approvals</Heading> */
export function Heading({ level = 2, tone = 'default', truncate = false, className, children, ...rest }: Base & {
  /** Heading level: 1 page title, 2 section, 3 card, 4 group label.
   * @control select @options 1|2|3|4 @group Style @default 2 */
  level?: 1 | 2 | 3 | 4
  /** Text colour from the theme.
   * @control select @options default|muted|soft|faint|primary|success|warning|danger @group Style @default default */
  tone?: TextTone
  /** Cut to one line with an ellipsis.
   * @control boolean @group Style @default false */
  truncate?: boolean
  /** The heading text.
   * @control text @group Content */
  children?: React.ReactNode
}) {
  return React.createElement(HEADING_TAG[level], {
    'data-vcomp': 'Heading', ...rest,
    className: cx('m-0', HEADING[level], TONE[tone], truncate && 'min-w-0 truncate', className),
  }, children)
}

/** A form field wrapper: label, the control (an input, Select, textarea...), a help line and the field's error message.
 * @category form
 * @example <Field label="Amount" required hint="In euros" error={errors.amount}><input className="w-full rounded-lg border border-input bg-card px-2.5 py-1.5 text-sm" value={amount} onChange={(e) => setAmount(e.target.value)} /></Field> */
export function Field({ label, hint, error, required = false, htmlFor, className, children, ...rest }: Omit<Base, 'title'> & {
  /** Label shown above the control.
   * @control text @group Content */
  label?: React.ReactNode
  /** Help line under the control (hidden while there is an error).
   * @control text @group Content */
  hint?: React.ReactNode
  /** Error message for this field (e.g. ApiError.body.message when body.field matches).
   * @control hidden @group Data */
  error?: React.ReactNode
  /** Mark the label with a required asterisk.
   * @control boolean @group Behavior @default false */
  required?: boolean
  /** id of the control the label points at.
   * @control text @group Behavior */
  htmlFor?: string
  /** The control.
   * @control slot @group Content */
  children?: React.ReactNode
}) {
  return (
    <div data-vcomp="Field" {...rest} className={cx('flex min-w-0 flex-col gap-1', className)}>
      {label && (
        <label htmlFor={htmlFor} className="text-xs font-semibold text-soft-foreground">
          {label}{required && <span className="ml-0.5 text-danger" aria-hidden>*</span>}
        </label>
      )}
      {children}
      {error ? <span role="alert" className="text-xs text-danger">{error}</span> : hint ? <span className="text-xs text-muted-foreground">{hint}</span> : null}
    </div>
  )
}

const DIVIDER_SPACE = { 0: '', 2: 'my-2', 3: 'my-3', 4: 'my-4', 6: 'my-6' }
const DIVIDER_SPACE_X = { 0: '', 2: 'mx-2', 3: 'mx-3', 4: 'mx-4', 6: 'mx-6' }

/** A thin theme-coloured rule between sections (horizontal) or between items of a row (vertical).
 * @category layout
 * @example <Divider spacing="4" /> */
export function Divider({ orientation = 'horizontal', spacing = '0', className, ...rest }: Omit<Base, 'children'> & {
  /** Horizontal rule across, or a vertical bar inside a row Stack.
   * @control select @options horizontal|vertical @group Layout @default horizontal */
  orientation?: 'horizontal' | 'vertical'
  /** Space on both sides of the rule.
   * @control spacing @options 0|2|3|4|6 @group Layout @default 0 */
  spacing?: '0' | '2' | '3' | '4' | '6'
}) {
  const vertical = orientation === 'vertical'
  return (
    <div data-vcomp="Divider" role="separator" aria-orientation={orientation} {...rest}
      className={cx('shrink-0 bg-border', vertical ? 'w-px self-stretch' : 'h-px w-full', vertical ? DIVIDER_SPACE_X[spacing] : DIVIDER_SPACE[spacing], className)} />
  )
}

const SPACER = { 1: 'h-1 w-1', 2: 'h-2 w-2', 3: 'h-3 w-3', 4: 'h-4 w-4', 6: 'h-6 w-6', 8: 'h-8 w-8' }

/** Empty space: without `size` it pushes the following items of a Stack to the far end; with `size` a fixed gap.
 * @category layout
 * @example <Stack direction="row"><Text>Total</Text><Spacer /><Text weight="semibold">€120</Text></Stack> */
export function Spacer({ size, className, ...rest }: Omit<Base, 'children'> & {
  /** Fixed size on the theme scale; leave unset to fill the remaining space.
   * @control select @options 1|2|3|4|6|8 @group Layout */
  size?: '1' | '2' | '3' | '4' | '6' | '8'
}) {
  return <div data-vcomp="Spacer" aria-hidden {...rest} className={cx('shrink-0', size ? SPACER[size] : 'flex-1', className)} />
}

// ── Colour scheme (light / dark / system) ─────────────────────────────────────

export type ColorScheme = 'light' | 'dark' | 'system'
const SCHEME_KEY = 'studio-color-scheme'
let scheme: ColorScheme = 'system'
const listeners = new Set<(s: ColorScheme) => void>()

function systemDark(): boolean {
  try { return window.matchMedia('(prefers-color-scheme: dark)').matches } catch { return false }
}

/** Set the app's colour scheme: puts or removes the `dark` class on <html> (theme tokens and `dark:` utilities follow it) and
 * remembers the choice for the next visit. `system` follows the OS setting. */
export function setColorScheme(next: ColorScheme): void {
  scheme = next
  try { localStorage.setItem(SCHEME_KEY, next) } catch { /* storage unavailable: the choice lasts for this page only */ }
  if (typeof document !== 'undefined') document.documentElement.classList.toggle('dark', next === 'dark' || (next === 'system' && systemDark()))
  listeners.forEach((l) => l(next))
}

if (typeof document !== 'undefined') {
  let saved: string | null = null
  try { saved = localStorage.getItem(SCHEME_KEY) } catch { /* storage unavailable */ }
  setColorScheme(saved === 'light' || saved === 'dark' ? saved : 'system')
  try { window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (scheme === 'system') setColorScheme('system') }) } catch { /* no matchMedia */ }
}

/** The current colour scheme and its setter, re-rendering when it changes anywhere in the app. */
export function useColorScheme(): [ColorScheme, (s: ColorScheme) => void] {
  const [s, set] = useState<ColorScheme>(scheme)
  useEffect(() => { listeners.add(set); return () => { listeners.delete(set) } }, [])
  return [s, setColorScheme]
}

const SCHEME_NEXT: Record<ColorScheme, ColorScheme> = { system: 'light', light: 'dark', dark: 'system' }
const SCHEME_LABEL: Record<ColorScheme, string> = { system: 'Theme: system', light: 'Theme: light', dark: 'Theme: dark' }
const SCHEME_ICON: Record<ColorScheme, string> = { system: '◐', light: '☀', dark: '☾' }

/** A small button that cycles the colour scheme system → light → dark (AppShell shows one in its navigation).
 * @category navigation
 * @example <ColorSchemeToggle /> */
export function ColorSchemeToggle({ className, ...rest }: Omit<React.HTMLAttributes<HTMLButtonElement>, 'children' | 'onClick' | 'color'> & {
  /** @control hidden */
  className?: string
}) {
  const [s, set] = useColorScheme()
  return (
    <button type="button" data-vcomp="ColorSchemeToggle" title={SCHEME_LABEL[s]} aria-label={SCHEME_LABEL[s]} {...rest} onClick={() => set(SCHEME_NEXT[s])}
      className={cx('inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted', className)}>
      <span aria-hidden>{SCHEME_ICON[s]}</span><span className="hidden md:inline">{SCHEME_LABEL[s].slice(7)}</span>
    </button>
  )
}
