/**
 * Screen-spec bindings (DESIGN-STAGE5-UIS §6.3). There is no expression engine and no eval: a binding is a
 * plain path lookup under one of five closed roots, and templates substitute `{{path}}` with that lookup.
 *   input.*   node.<Node name>.<path>   task.*   result.*   run.*
 * Prototype-reaching segments never resolve; lookups only follow own properties.
 */
import type { ScreenComponent, ScreenSpec } from './types'

export const BINDING_ROOTS = ['input', 'node', 'task', 'result', 'run'] as const
export type BindingRoot = (typeof BINDING_ROOTS)[number]

export const COMPONENT_TYPES = [
  'heading', 'text', 'field', 'amount', 'status', 'callout', 'list', 'table', 'link', 'divider', 'form', 'actions', 'result', 'input_form',
] as const
export type ComponentType = (typeof COMPONENT_TYPES)[number]

export const MAX_COMPONENTS = 200

export function isKnownType(t: unknown): t is ComponentType {
  return typeof t === 'string' && (COMPONENT_TYPES as readonly string[]).includes(t)
}

export interface ScreenData {
  input?: Record<string, unknown>
  /** node name -> first item's json */
  node?: Record<string, unknown>
  task?: Record<string, unknown>
  result?: Record<string, unknown> | null
  run?: Record<string, unknown>
}

const FORBIDDEN = new Set(['__proto__', 'constructor', 'prototype'])

function isRoot(s: string): s is BindingRoot {
  return (BINDING_ROOTS as readonly string[]).includes(s)
}

/** Reads own properties along `segments`; arrays accept numeric segments. */
export function walk(value: unknown, segments: string[]): unknown {
  let cur: unknown = value
  for (const seg of segments) {
    if (cur === null || cur === undefined || FORBIDDEN.has(seg)) return undefined
    if (Array.isArray(cur)) {
      if (!/^\d+$/.test(seg)) return undefined
      cur = cur[Number(seg)]
    } else if (typeof cur === 'object') {
      if (!Object.prototype.hasOwnProperty.call(cur, seg)) return undefined
      cur = (cur as Record<string, unknown>)[seg]
    } else return undefined
  }
  return cur
}

/** `a.b[0].c` -> ['a','b','0','c'] */
export function splitPath(path: string): string[] {
  return path
    .replace(/\[(\d+)\]/g, '.$1')
    .split('.')
    .map((s) => s.trim())
    .filter((s) => s !== '')
}

export interface ParsedBinding {
  root: BindingRoot
  /** only for `node.` bindings */
  node?: string
  path: string[]
}

/**
 * `node.Lookup history.orders` -> {root:'node', node:'Lookup history', path:['orders']}.
 * ASSUMED: a node name contains no dot (the first segment after `node.` is the whole name).
 */
export function parseBinding(bind: unknown): ParsedBinding | null {
  if (typeof bind !== 'string') return null
  const segs = splitPath(bind)
  if (segs.length === 0 || !isRoot(segs[0])) return null
  const root = segs[0]
  if (root === 'node') {
    if (segs.length < 2) return null
    return { root, node: segs[1], path: segs.slice(2) }
  }
  return { root, path: segs.slice(1) }
}

export function resolveBinding(bind: unknown, data: ScreenData): unknown {
  const p = parseBinding(bind)
  if (!p) return undefined
  if (p.root === 'node') {
    const nodes = data.node
    if (!nodes || !Object.prototype.hasOwnProperty.call(nodes, p.node!)) return undefined
    return walk(nodes[p.node!], p.path)
  }
  return walk(data[p.root], p.path)
}

/** Display text for a resolved value. Never HTML; React escapes it. */
export function display(v: unknown, max = 2000): string {
  if (v === undefined || v === null) return ''
  if (typeof v === 'string') return v.length > max ? `${v.slice(0, max)}…` : v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  try {
    const s = JSON.stringify(v)
    return s.length > max ? `${s.slice(0, max)}…` : s
  } catch {
    return ''
  }
}

const TEMPLATE = /\{\{\s*([^{}]+?)\s*\}\}/g

/**
 * Substitutes `{{path}}`. `{{value}}` is the component's own bound value; a path under an allowed root reads
 * the screen data; any other path reads `scope` (the list item). Unresolvable paths become ''.
 */
export function interpolate(template: string, data: ScreenData, opts: { value?: unknown; scope?: unknown } = {}): string {
  return template.replace(TEMPLATE, (_m, expr: string) => {
    const e = expr.trim()
    if (e === 'value') return display(opts.value)
    if (parseBinding(e)) return display(resolveBinding(e, data))
    if (opts.scope !== undefined) return display(walk(opts.scope, splitPath(e)))
    return ''
  })
}

/** Only absolute https URLs are rendered as links (§6.3: restricted to https:). */
export function safeHref(v: unknown): string | null {
  if (typeof v !== 'string') return null
  try {
    const u = new URL(v.trim())
    return u.protocol === 'https:' ? u.toString() : null
  } catch {
    return null
  }
}

export function currencyFormat(n: number, currency = 'USD'): string {
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(n)
  } catch {
    return String(n)
  }
}

export function asComponents(spec: unknown): ScreenComponent[] | null {
  if (!spec || typeof spec !== 'object') return null
  const c = (spec as ScreenSpec).components
  return Array.isArray(c) ? (c.slice(0, MAX_COMPONENTS) as ScreenComponent[]) : null
}

export function screenHas(spec: ScreenSpec | null | undefined, type: ComponentType): boolean {
  return (asComponents(spec) ?? []).some((c) => c && typeof c === 'object' && c.type === type)
}

/** Screens listed in the preview picker: always `start`; `task:<node>` and `result` as stored. */
export function screenIds(screens: Record<string, ScreenSpec> | undefined): string[] {
  const keys = Object.keys(screens ?? {})
  const rank = (k: string) => (k === 'start' ? 0 : k.startsWith('task:') ? 1 : 2)
  const base = keys.length ? keys : ['start']
  return [...base].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
}

export function taskNodeOfScreen(id: string): string | null {
  return id.startsWith('task:') ? id.slice(5) : null
}
