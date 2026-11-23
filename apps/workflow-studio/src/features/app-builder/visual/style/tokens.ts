/**
 * The style panel's token tables: every value the panel can write is a Tailwind utility built from the app kit's theme
 * (app_kit/theme.css) or Tailwind's own scales. No arbitrary values (`p-[13px]`, `bg-[#…]`) — the token lock.
 */

export type SectionId = 'layout' | 'spacing' | 'size' | 'typography' | 'fill'

export interface Option {
  /** the class written (an empty string never appears; "default" means no class from the group) */
  cls: string
  label: string
  /** a CSS colour for the swatch (colour controls only) */
  swatch?: string
}

export interface Control {
  id: string
  label: string
  section: SectionId
  options: Option[]
  /** a colour control renders swatches */
  color?: boolean
}

/** Tailwind's spacing steps the panel offers. */
export const SPACE = ['0', '0.5', '1', '1.5', '2', '3', '4', '5', '6', '8', '10', '12', '16'] as const

const spaced = (prefix: string): Option[] => SPACE.map((s) => ({ cls: `${prefix}-${s}`, label: s }))

/** Text colours from the theme (`text-<token>`). */
export const TEXT_TOKENS = [
  'foreground', 'card-foreground', 'muted-foreground', 'soft-foreground', 'faint-foreground', 'primary', 'primary-foreground',
  'info', 'success', 'warning', 'danger', 'success-foreground', 'warning-foreground', 'danger-foreground',
  'chart-1', 'chart-2', 'chart-3', 'chart-4', 'chart-5', 'chart-6', 'chart-7', 'chart-8',
] as const

/** Fill colours from the theme (`bg-<token>`). */
export const BG_TOKENS = [
  'background', 'card', 'muted', 'subtle', 'primary', 'primary-soft', 'info-soft', 'success', 'success-soft', 'warning', 'warning-soft',
  'danger', 'danger-soft', 'transparent',
  'chart-1', 'chart-2', 'chart-3', 'chart-4', 'chart-5', 'chart-6', 'chart-7', 'chart-8',
] as const

/** Border colours from the theme (`border-<token>`). */
export const BORDER_TOKENS = ['border', 'input', 'primary', 'success-border', 'warning-border', 'danger-border', 'transparent'] as const // design-ok: the generated app's border tokens

/** The kit's default (light) token values, for swatches: the studio's own CSS variables are not the app's. */
const APP_DEFAULTS = 'background:f8fafc foreground:0f172a card:ffffff card-foreground:0f172a muted:f1f5f9 muted-foreground:64748b soft-foreground:475569 faint-foreground:94a3b8 subtle:f8fafc border:e2e8f0 input:cbd5e1 ring:dbeafe primary:2563eb primary-foreground:ffffff primary-soft:eff6ff info:1d4ed8 info-soft:eff6ff success:047857 success-foreground:ffffff success-soft:ecfdf5 success-border:a7f3d0 warning:b45309 warning-foreground:ffffff warning-soft:fffbeb warning-border:fde68a danger:b91c1c danger-foreground:ffffff danger-soft:fef2f2 danger-border:fecaca chart-1:2563eb chart-2:059669 chart-3:d97706 chart-4:7c3aed chart-5:db2777 chart-6:0891b2 chart-7:65a30d chart-8:64748b' // design-ok: generated-app token defaults (app_kit/theme.css)
const DEFAULT_HEX = new Map(APP_DEFAULTS.split(' ').map((p) => p.split(':') as [string, string]))

/** A swatch colour: the app's own variable inside the app, the kit default here in the studio. */
const swatch = (token: string) => (token === 'transparent' ? 'transparent' : `#${DEFAULT_HEX.get(token) ?? 'ffffff'}`)

const colors = (prefix: string, tokens: readonly string[]): Option[] => tokens.map((t) => ({ cls: `${prefix}-${t}`, label: t, swatch: swatch(t) }))

const opts = (pairs: [string, string][]): Option[] => pairs.map(([cls, label]) => ({ cls, label }))

export const CONTROLS: Control[] = [
  // Layout
  { id: 'display', label: 'Display', section: 'layout', options: opts([['block', 'block'], ['flex', 'flex'], ['inline-flex', 'inline-flex'], ['grid', 'grid'], ['inline-block', 'inline'], ['hidden', 'hidden']]) },
  { id: 'direction', label: 'Direction', section: 'layout', options: opts([['flex-row', 'row'], ['flex-col', 'column'], ['flex-row-reverse', 'row reverse'], ['flex-col-reverse', 'column reverse']]) },
  { id: 'wrap', label: 'Wrap', section: 'layout', options: opts([['flex-wrap', 'wrap'], ['flex-nowrap', 'no wrap']]) },
  { id: 'gap', label: 'Gap', section: 'layout', options: spaced('gap') },
  { id: 'align', label: 'Align items', section: 'layout', options: opts([['items-start', 'start'], ['items-center', 'center'], ['items-end', 'end'], ['items-stretch', 'stretch'], ['items-baseline', 'baseline']]) },
  { id: 'justify', label: 'Justify', section: 'layout', options: opts([['justify-start', 'start'], ['justify-center', 'center'], ['justify-end', 'end'], ['justify-between', 'between'], ['justify-around', 'around']]) },
  { id: 'cols', label: 'Columns', section: 'layout', options: ['1', '2', '3', '4', '5', '6', '12'].map((n) => ({ cls: `grid-cols-${n}`, label: n })) },
  // Spacing
  { id: 'p', label: 'Padding', section: 'spacing', options: spaced('p') },
  { id: 'px', label: 'Padding X', section: 'spacing', options: spaced('px') },
  { id: 'py', label: 'Padding Y', section: 'spacing', options: spaced('py') },
  { id: 'm', label: 'Margin', section: 'spacing', options: spaced('m') },
  { id: 'mx', label: 'Margin X', section: 'spacing', options: [...spaced('mx'), { cls: 'mx-auto', label: 'auto' }] },
  { id: 'my', label: 'Margin Y', section: 'spacing', options: spaced('my') },
  // Size
  { id: 'w', label: 'Width', section: 'size', options: opts([['w-full', 'full'], ['w-auto', 'auto'], ['w-fit', 'fit'], ['w-1/2', '1/2'], ['w-1/3', '1/3'], ['w-2/3', '2/3'], ['w-1/4', '1/4'], ['w-3/4', '3/4'], ['w-screen', 'screen']]) },
  { id: 'h', label: 'Height', section: 'size', options: opts([['h-full', 'full'], ['h-auto', 'auto'], ['h-fit', 'fit'], ['h-screen', 'screen'], ['h-8', '8'], ['h-10', '10'], ['h-12', '12'], ['h-16', '16'], ['h-24', '24'], ['h-32', '32'], ['h-48', '48'], ['h-64', '64']]) },
  { id: 'maxw', label: 'Max width', section: 'size', options: ['xs', 'sm', 'md', 'lg', 'xl', '2xl', '3xl', '4xl', '5xl', '6xl', '7xl', 'full', 'none'].map((s) => ({ cls: `max-w-${s}`, label: s })) },
  // Typography
  { id: 'text-size', label: 'Text size', section: 'typography', options: ['xs', 'sm', 'base', 'lg', 'xl', '2xl', '3xl', '4xl'].map((s) => ({ cls: `text-${s}`, label: s })) },
  { id: 'weight', label: 'Weight', section: 'typography', options: opts([['font-normal', 'normal'], ['font-medium', 'medium'], ['font-semibold', 'semibold'], ['font-bold', 'bold']]) },
  { id: 'text-align', label: 'Align text', section: 'typography', options: opts([['text-left', 'left'], ['text-center', 'center'], ['text-right', 'right']]) },
  { id: 'text-color', label: 'Text colour', section: 'typography', color: true, options: colors('text', TEXT_TOKENS) },
  // Fill & border
  { id: 'bg', label: 'Fill', section: 'fill', color: true, options: colors('bg', BG_TOKENS) },
  { id: 'border-width', label: 'Border', section: 'fill', options: opts([['border-0', 'none'], ['border', '1'], ['border-2', '2'], ['border-4', '4']]) }, // design-ok: generated-app classes
  { id: 'border-color', label: 'Border colour', section: 'fill', color: true, options: colors('border', BORDER_TOKENS) }, // design-ok: generated-app classes
  { id: 'radius', label: 'Radius', section: 'fill', options: ['none', 'sm', 'md', 'lg', 'xl', '2xl', 'full'].map((s) => ({ cls: `rounded-${s}`, label: s })) },
  { id: 'shadow', label: 'Shadow', section: 'fill', options: ['none', 'sm', 'md', 'lg', 'xl'].map((s) => ({ cls: `shadow-${s}`, label: s })) },
]

export const SECTIONS: { id: SectionId; label: string }[] = [
  { id: 'layout', label: 'Layout' },
  { id: 'spacing', label: 'Spacing' },
  { id: 'size', label: 'Size' },
  { id: 'typography', label: 'Typography' },
  { id: 'fill', label: 'Fill & borders' },
]

/** Every class the panel can write (the token lock: a write outside this set is refused). */
export const ALLOWED: ReadonlySet<string> = new Set(CONTROLS.flatMap((c) => c.options.map((o) => o.cls)))

export function controlById(id: string): Control | undefined {
  return CONTROLS.find((c) => c.id === id)
}

/**
 * Component props that already style the element (layout primitives' enum props): when the component has one, the class
 * controls it covers are hidden and the prop is offered instead — design lives in props first.
 */
export const STYLE_PROPS: Record<string, string[]> = {
  direction: ['direction'],
  wrap: ['wrap'],
  gap: ['gap'],
  align: ['align', 'text-align'],
  justify: ['justify'],
  cols: ['cols'],
  colsMd: [],
  colsLg: [],
  padding: ['p', 'px', 'py'],
  spacing: ['my', 'mx'],
  surface: ['bg'],
  border: ['border-width'],
  radius: ['radius'],
  shadow: ['shadow'],
  tone: ['text-color'],
  size: ['text-size'],
  weight: ['weight'],
  level: ['text-size', 'weight'],
}
