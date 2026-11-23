/**
 * How each kind of knowledge looks: colour (`--viz-*`), shape, region on the canvas, and how each relation is drawn
 * and read aloud. Concepts take the violet of the AI builder's concept chips, so a concept looks the same everywhere.
 */

export type KindGroup = 'concept' | 'capability' | 'pattern' | 'role' | 'app' | 'other'
export type Shape = 'circle' | 'square' | 'hexagon' | 'diamond' | 'pill'

export interface KindStyle {
  label: string
  plural: string
  /** a CSS colour expression (`var(--viz-n)`) */
  color: string
  shape: Shape
}

export const KIND_ORDER: KindGroup[] = ['concept', 'capability', 'pattern', 'role', 'app', 'other']

export const KIND_STYLE: Record<KindGroup, KindStyle> = {
  concept: { label: 'Concept', plural: 'Concepts', color: 'var(--viz-1)', shape: 'circle' },
  capability: { label: 'Capability', plural: 'Capabilities', color: 'var(--viz-2)', shape: 'square' },
  pattern: { label: 'Pattern', plural: 'Patterns', color: 'var(--viz-3)', shape: 'hexagon' },
  role: { label: 'Role', plural: 'Roles', color: 'var(--viz-4)', shape: 'diamond' },
  app: { label: 'App', plural: 'Apps', color: 'var(--viz-5)', shape: 'pill' },
  other: { label: 'Other', plural: 'Other', color: 'var(--viz-8)', shape: 'circle' },
}

export function kindGroup(kind: string): KindGroup {
  return (KIND_ORDER as string[]).includes(kind) ? (kind as KindGroup) : 'other'
}

export const kindStyle = (kind: string): KindStyle => KIND_STYLE[kindGroup(kind)]

const SUBKIND_WORDS: Record<string, string> = {
  node_type: 'Node type',
  template: 'Template',
  screen_component: 'Screen component',
  api_operation: 'API operation',
  subworkflow: 'Sub-workflow',
  workflow: 'Workflow',
  process: 'Process',
  control: 'Control',
  entity: 'Entity',
  artifact: 'Artifact',
}

/** `node_type` -> `Node type`; unknown subkinds are de-snaked. */
export function subkindLabel(subkind: string | null | undefined): string {
  if (!subkind) return ''
  return SUBKIND_WORDS[subkind] ?? sentence(subkind)
}

/** `Capability · Node type` */
export function kindLabel(kind: string, subkind?: string | null): string {
  const base = kindGroup(kind) === 'other' ? sentence(kind) : kindStyle(kind).label
  const sub = subkindLabel(subkind)
  return sub ? `${base} · ${sub}` : base
}

function sentence(s: string): string {
  const t = s.replaceAll('_', ' ').trim()
  return t ? t[0].toUpperCase() + t.slice(1) : t
}

// ── relations ────────────────────────────────────────────────────────────────────────────────────

export interface RelStyle {
  label: string
  /** SVG stroke-dasharray at rest ('' = solid) */
  dash: string
  width: number
}

const REL_STYLE: Record<string, RelStyle> = {
  composes: { label: 'Composes', dash: '', width: 1 },
  implements: { label: 'Implements', dash: '', width: 1.25 },
  requires: { label: 'Requires', dash: '5 3', width: 1 },
  derived_from: { label: 'Derived from', dash: '1.5 3', width: 1.1 },
  produces: { label: 'Produces', dash: '8 3', width: 1 },
  consumes: { label: 'Consumes', dash: '8 3 2 3', width: 1 },
  is_a: { label: 'Is a', dash: '2 2', width: 1 },
  renders_with: { label: 'Renders with', dash: '6 2 1 2', width: 1 },
}

export function relStyle(rel: string): RelStyle {
  return REL_STYLE[rel] ?? { label: sentence(rel), dash: '3 3', width: 1 }
}

const PHRASE: Record<string, [out: string, inn: string]> = {
  composes: ['Uses', 'Used by'],
  implements: ['Implements', 'Implemented by'],
  requires: ['Requires', 'Required by'],
  derived_from: ['Derived from', 'Source of'],
  produces: ['Produces', 'Produced by'],
  consumes: ['Consumes', 'Consumed by'],
  is_a: ['Is a kind of', 'Kinds'],
  renders_with: ['Renders with', 'Rendered by'],
  validated_by: ['Validated by', 'Validates'],
}

/** How a neighbour reads from this node's side: `implements` + `in` = "Implemented by". */
export function relPhrase(rel: string, direction: 'in' | 'out'): string {
  const p = PHRASE[rel]
  if (p) return direction === 'out' ? p[0] : p[1]
  const base = sentence(rel)
  return direction === 'out' ? base : `${base} (incoming)`
}

// ── shapes ───────────────────────────────────────────────────────────────────────────────────────

const f = (n: number) => Math.round(n * 100) / 100

/** An SVG path for `shape` centred on 0,0 whose visual weight matches a circle of radius `r`. */
export function shapePath(shape: Shape, r: number): string {
  switch (shape) {
    case 'circle':
      return `M${f(-r)},0a${f(r)},${f(r)} 0 1,0 ${f(2 * r)},0a${f(r)},${f(r)} 0 1,0 ${f(-2 * r)},0Z`
    case 'square': {
      const h = r * 0.9
      const c = r * 0.32
      return `M${f(-h + c)},${f(-h)}H${f(h - c)}Q${f(h)},${f(-h)} ${f(h)},${f(-h + c)}V${f(h - c)}Q${f(h)},${f(h)} ${f(h - c)},${f(h)}H${f(-h + c)}Q${f(-h)},${f(h)} ${f(-h)},${f(h - c)}V${f(-h + c)}Q${f(-h)},${f(-h)} ${f(-h + c)},${f(-h)}Z`
    }
    case 'hexagon': {
      const R = r * 1.08
      const pts = Array.from({ length: 6 }, (_, i) => {
        const a = (Math.PI / 3) * i - Math.PI / 2
        return `${f(R * Math.cos(a))},${f(R * Math.sin(a))}`
      })
      return `M${pts.join('L')}Z`
    }
    case 'diamond': {
      const R = r * 1.22
      return `M0,${f(-R)}L${f(R)},0L0,${f(R)}L${f(-R)},0Z`
    }
    case 'pill': {
      const w = r * 1.25
      const h = r * 0.78
      return `M${f(-w + h)},${f(-h)}H${f(w - h)}A${f(h)},${f(h)} 0 0,1 ${f(w - h)},${f(h)}H${f(-w + h)}A${f(h)},${f(h)} 0 0,1 ${f(-w + h)},${f(-h)}Z`
    }
  }
}
