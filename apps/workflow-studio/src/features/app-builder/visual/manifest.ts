/**
 * How the property panel edits a prop: the editor for each prop of a component, derived from the component registry
 * (registry/extract.ts reads the TS prop types and JSDoc tags of the kit AND the app's own components). This file only
 * maps a schema prop to a panel editor and holds the small override table for what the extractor can't express.
 * Behaviour and data props are shown as bound / event, never edited.
 */
import type { ComponentSchema, PropSchema } from './registry/extract'

export type PropEditor =
  | { kind: 'text' }
  | { kind: 'textarea' }
  | { kind: 'color' }
  | { kind: 'enum'; options: readonly string[]; default?: string }
  | { kind: 'bool'; default?: boolean }
  | { kind: 'number'; default?: number; min?: number; max?: number; step?: number }
  /** a string → string|number|boolean object, e.g. a tile's `filter` */
  | { kind: 'map' }
  /** an array of strings, e.g. a screen's `roles` */
  | { kind: 'tags' }
  | { kind: 'list'; item: ListItem }
  | { kind: 'bound' }
  | { kind: 'event' }
  | { kind: 'slot' }

export type ListItem =
  | { kind: 'string'; noun: string }
  | {
      kind: 'object'
      noun: string
      /** the field shown as the item's name in the list */
      label: string
      fields: Record<string, PropEditor>
      /** a new item: `$` is replaced with a unique slug */
      template: Record<string, string | number | boolean>
      /** the item can be hidden without removing it (`hidden: true`) */
      hideable?: boolean
    }

/** Spacing steps offered by `@control spacing` when the prop gives no `@options`. */
export const SPACING = ['0', '0.5', '1', '1.5', '2', '3', '4', '5', '6', '8', '10', '12'] as const

/**
 * Overrides the extractor can't express from types + JSDoc: state a prop is normally wired to (shown as bound, not as a
 * switch or text box). Keyed by kit component; app components get none.
 */
const BOUND: Record<string, readonly string[]> = {
  Button: ['loading', 'disabled'],
  Drawer: ['open'],
  Select: ['value', 'disabled'],
  Tabs: ['value'],
  Stat: ['value'],
  StatusBadge: ['status'],
  DataTable: ['loading'],
  ConfidenceBar: ['value'],
  CommandPalette: ['open'],
  // series and run records come from the API; a literal array of them is not something to edit by hand
  Sparkline: ['values'],
  ResultStrip: ['results'],
  StepList: ['steps'],
  NetworkList: ['rows'],
}

/** `recentColumns` → `column`, `options` → `option`. */
export function nounOf(prop: string): string {
  const last = /[A-Z][a-z0-9]*$/.exec(prop)?.[0] ?? prop
  const w = last.toLowerCase()
  return w.endsWith('ies') ? `${w.slice(0, -3)}y` : w.endsWith('s') && !w.endsWith('ss') ? w.slice(0, -1) : w
}

const LABEL_FIELDS = ['label', 'header', 'title', 'name', 'id', 'key']
const SCALAR = new Set(['text', 'textarea', 'color', 'icon', 'select', 'spacing', 'number', 'boolean'])

function itemOf(p: PropSchema): ListItem | null {
  const it = p.item
  if (!it) return null
  if (it.kind !== 'object') return { kind: 'string', noun: nounOf(p.name) }
  const fields: Record<string, PropEditor> = {}
  for (const f of it.fields) {
    // inside an item, a list of strings is edited as tags, and a ReactNode field as text: an item is config data, so a
    // field holding JSX is not a literal and shows as custom anyway (this also covers kit copies without `@control text`)
    const e = f.control === 'list' && f.item?.kind === 'string' ? ({ kind: 'tags' } as const) : f.control === 'slot' ? ({ kind: 'text' } as const) : editorOf(f)
    if (e && e.kind !== 'event' && e.kind !== 'bound' && e.kind !== 'slot' && e.kind !== 'list') fields[f.name] = e
  }
  const names = it.fields.map((f) => f.name)
  const label = LABEL_FIELDS.find((n) => n in fields) ?? Object.keys(fields)[0] ?? 'label'
  // a new item fills its required fields with a slug; one that needs code (a render function) can't be added here
  const template: Record<string, string | number | boolean> = {}
  let needsCode = false
  for (const f of it.fields) {
    if (!f.required) continue
    if (f.control === 'text' || f.control === 'textarea' || f.control === 'icon' || f.control === 'slot') template[f.name] = '$'
    else if (f.control === 'select' && f.options?.length) template[f.name] = f.options[0]
    else if (f.control === 'number') template[f.name] = 0
    else if (f.control === 'boolean') template[f.name] = false
    else needsCode = true
  }
  const out: ListItem = { kind: 'object', noun: nounOf(p.name), label, fields, template: needsCode ? {} : template }
  if (names.includes('hidden') && fields.hidden?.kind === 'bool') out.hideable = true
  return out
}

/** The panel editor for one schema prop, or null when the prop is hidden from the panel. */
export function editorOf(p: PropSchema, component?: string, kit = false): PropEditor | null {
  if (kit && component && BOUND[component]?.includes(p.name)) return { kind: 'bound' }
  const def = p.default
  switch (p.control) {
    case 'hidden':
      return null
    case 'text':
    case 'icon':
      return { kind: 'text' }
    case 'textarea':
      return { kind: 'textarea' }
    case 'color':
      return { kind: 'color' }
    case 'spacing':
    case 'select': {
      const options = p.options?.length ? p.options : p.control === 'spacing' ? [...SPACING] : []
      if (!options.length) return { kind: 'text' }
      return typeof def === 'string' ? { kind: 'enum', options, default: def } : { kind: 'enum', options }
    }
    case 'boolean':
      return typeof def === 'boolean' ? { kind: 'bool', default: def } : { kind: 'bool' }
    case 'number': {
      const e: PropEditor = { kind: 'number' }
      if (typeof def === 'number') e.default = def
      if (p.min !== undefined) e.min = p.min
      if (p.max !== undefined) e.max = p.max
      if (p.step !== undefined) e.step = p.step
      return e
    }
    case 'slot':
      return { kind: 'slot' }
    case 'event':
      return { kind: 'event' }
    case 'map':
      return { kind: 'map' }
    case 'list': {
      const item = itemOf(p)
      return item ? { kind: 'list', item } : { kind: 'bound' }
    }
    case 'code':
      return { kind: 'bound' }
  }
}

/** Every panel editor of a component, in declaration order (hidden props left out). */
export function editorsOf(comp: ComponentSchema, kit: boolean): Record<string, PropEditor> {
  const out: Record<string, PropEditor> = {}
  for (const p of comp.props) {
    const e = editorOf(p, comp.name, kit)
    if (e) out[p.name] = e
  }
  return out
}

/** Whether a prop's value can be typed as text in the panel (text-like editors, and slots holding a string). */
export function isScalarControl(c: string): boolean {
  return SCALAR.has(c)
}

/** The prop a `data-vpart` (e.g. `columns.3`) refers to, given the component's editors, with the item index. */
export function partOf(editors: Record<string, PropEditor> | null | undefined, component: string, vpart: string | null | undefined): { prop: string; index: number } | null {
  const m = vpart ? /^(\w+)\.(\d+)$/.exec(vpart) : null
  if (!m || !editors) return null
  const spec = editors[m[1]]
  // DashboardLayout hands its recent columns to an inner table that marks them `columns.i`
  if (!spec && component === 'DashboardLayout' && m[1] === 'columns') return { prop: 'recentColumns', index: Number(m[2]) }
  return spec?.kind === 'list' ? { prop: m[1], index: Number(m[2]) } : null
}

/**
 * A new list item from the item spec, with `$` in its template replaced by a slug no existing item uses (`new_column`,
 * `new_column_2` …). Null when the item can't be made from literals alone (a screen needs a render function).
 */
export function newItem(item: ListItem, existing: unknown[]): string | Record<string, string | number | boolean> | null {
  const taken = new Set(existing.flatMap((e) => (e && typeof e === 'object' ? Object.values(e as object) : [e])).map(String))
  let slug = `new_${item.noun.replace(/\W+/g, '_')}`
  for (let n = 2; taken.has(slug); n++) slug = `new_${item.noun.replace(/\W+/g, '_')}_${n}`
  if (item.kind === 'string') return slug
  if (!Object.keys(item.template).length) return null
  return Object.fromEntries(Object.entries(item.template).map(([k, v]) => [k, v === '$' ? slug : v]))
}

/**
 * The prop that styles a named kit part: a non-list `data-vpart` (`header`, `body`, `footer`, `toolbar`, `nav`, `card`)
 * maps to `<part>ClassName` when the component declares it (Card `headerClassName`, Drawer `footerClassName`, DataTable
 * `toolbarClassName`). Null for list parts (`columns.2`, edited through `partOf`) and for parts without such a prop.
 */
export function partClassProp(comp: ComponentSchema | null | undefined, vpart: string | null | undefined): string | null {
  if (!comp || !vpart || !/^[a-z]\w*$/.test(vpart)) return null
  const name = `${vpart}ClassName`
  return comp.props.some((p) => p.name === name && p.type.trim() === 'string') ? name : null
}

/** Every `<part>ClassName` a component declares, keyed by part (`{ header: 'headerClassName', body: 'bodyClassName' }`). */
export function partClassProps(comp: ComponentSchema | null | undefined): Record<string, string> {
  const out: Record<string, string> = {}
  for (const p of comp?.props ?? []) {
    const m = /^([a-z]\w*)ClassName$/.exec(p.name)
    if (m && p.type.trim() === 'string') out[m[1]] = p.name   // not `rowClassName?: (row) => string`
  }
  return out
}
