/**
 * The pure rules of the 3D view: which shape each kind of knowledge takes, what colour each relation flows in,
 * which links are tentative, where the particles go, which labels show, and how far the camera stands to frame a
 * sphere. No three.js and no DOM here, so the scene module (`three/KgScene.ts`) stays a thin painter and these stay
 * testable.
 */
import { hashString } from './layout'
import type { KgEdge, KgNode } from './types'

// ── shapes ───────────────────────────────────────────────────────────────────────────────────────

/**
 * One geometry per broad kind, so shape carries meaning as well as colour:
 * concept = sphere, node type = cube, template = octahedron, pattern = hexagonal prism, project = torus,
 * app = capsule, role = tetrahedron, any other capability (workflow, API operation, screen component …) = a
 * dodecahedron "gem", anything else = a small sphere.
 */
export type Shape3D = 'sphere' | 'cube' | 'octahedron' | 'hexprism' | 'torus' | 'capsule' | 'tetra' | 'gem' | 'dot'

export const SHAPE3D_ORDER: Shape3D[] = ['sphere', 'cube', 'octahedron', 'hexprism', 'torus', 'capsule', 'tetra', 'gem', 'dot']

export function shapeOf(kind: string, subkind?: string | null): Shape3D {
  switch (kind) {
    case 'concept':
      return 'sphere'
    case 'pattern':
      return 'hexprism'
    case 'project':
      return 'torus'
    case 'app':
      return 'capsule'
    case 'role':
      return 'tetra'
    case 'capability':
      if (subkind === 'node_type') return 'cube'
      if (subkind === 'template') return 'octahedron'
      return 'gem'
    default:
      return 'dot'
  }
}

// ── relations ────────────────────────────────────────────────────────────────────────────────────

const REL_COLOR: Record<string, string> = {
  implements: 'var(--viz-2)',
  composes: 'var(--viz-6)',
  requires: 'var(--viz-7)',
  derived_from: 'var(--viz-3)',
  produces: 'var(--viz-5)',
  consumes: 'var(--viz-4)',
  is_a: 'var(--viz-1)',
  renders_with: 'var(--viz-5)',
  validated_by: 'var(--viz-3)',
}

/** The colour (a `var(--viz-n)` expression) particles flow in along a relation. */
export const relColor = (rel: string): string => REL_COLOR[rel] ?? 'var(--viz-8)'

/** Every distinct colour expression `relColor` can return. */
export const REL_COLORS: string[] = [...new Set([...Object.values(REL_COLOR), 'var(--viz-8)'])]

/**
 * A link the graph is not sure of yet: learned from builds, or touching a proposed (or stale) node. Drawn dashed
 * and dim until someone accepts it.
 */
export function isTentative(e: Pick<KgEdge, 'source' | 'src_id' | 'dst_id'>, byId: ReadonlyMap<string, Pick<KgNode, 'status'>>): boolean {
  if (e.source === 'learned') return true
  const pending = (id: string) => {
    const s = byId.get(id)?.status
    return s === 'proposed' || s === 'stale'
  }
  return pending(e.src_id) || pending(e.dst_id)
}

// ── particles ────────────────────────────────────────────────────────────────────────────────────

export interface ParticleSlot {
  edgeId: number
  /** where on the link (0..1) the particle starts, stable per edge */
  phase: number
  /** true when it rides a lit (focused / asked) link: brighter and bigger */
  lit: boolean
}

/**
 * Which links carry particles, capped at `cap` in all. Lit links first (up to `perLit` each, evenly spaced), then
 * at rest one per link in id order while the budget lasts; while something is lit, unlit links carry none (the
 * emphasis reads by motion as well as colour). Phases are hashed from the edge id, so a link's particles do not
 * jump when the set around it changes.
 */
export function particlePlan(
  edges: Iterable<number>,
  lit: ReadonlySet<number> | null,
  opts: { cap?: number; perLit?: number } = {},
): ParticleSlot[] {
  const cap = opts.cap ?? 600
  const perLit = opts.perLit ?? 3
  const out: ParticleSlot[] = []
  const ids = [...edges].sort((a, b) => a - b)
  const base = (id: number) => (hashString(String(id)) % 1000) / 1000
  if (lit && lit.size) {
    for (const id of ids) {
      if (!lit.has(id)) continue
      for (let k = 0; k < perLit && out.length < cap; k++) out.push({ edgeId: id, phase: (base(id) + k / perLit) % 1, lit: true })
    }
    return out
  }
  for (const id of ids) {
    if (out.length >= cap) break
    out.push({ edgeId: id, phase: base(id), lit: false })
  }
  return out
}

// ── labels ───────────────────────────────────────────────────────────────────────────────────────

/**
 * Which nodes carry a sprite label (never all of them): the hovered and selected ones always; then the lit ones,
 * biggest hubs first (capped at `maxLit`); then the visible nodes nearest the camera that are closer than `near`
 * (capped at `maxNear`); at rest, a few of the biggest hubs so the overview is not anonymous.
 */
export function pickLabels(s: {
  hovered?: string | null
  selected?: string | null
  lit: ReadonlySet<string> | null
  visible: ReadonlySet<string>
  degree: ReadonlyMap<string, number>
  /** visible node id -> distance from the camera */
  distance: ReadonlyMap<string, number>
  near: number
  maxLit?: number
  maxNear?: number
  hubs?: number
}): Set<string> {
  const out = new Set<string>()
  for (const id of [s.hovered, s.selected]) if (id && s.visible.has(id)) out.add(id)
  const deg = (id: string) => s.degree.get(id) ?? 0
  const byDeg = (a: string, b: string) => deg(b) - deg(a) || (a < b ? -1 : 1)
  if (s.lit) {
    for (const id of [...s.lit].filter((i) => s.visible.has(i)).sort(byDeg).slice(0, s.maxLit ?? 14)) out.add(id)
  } else {
    for (const id of [...s.visible].sort(byDeg).slice(0, s.hubs ?? 5)) out.add(id)
  }
  const near = [...s.distance].filter(([id, d]) => d < s.near && s.visible.has(id) && (!s.lit || s.lit.has(id))).sort((a, b) => a[1] - b[1])
  for (const [id] of near.slice(0, s.maxNear ?? 8)) out.add(id)
  return out
}

// ── camera ───────────────────────────────────────────────────────────────────────────────────────

/** How far a perspective camera stands from a sphere's centre so the sphere fills the view with `margin` to spare. */
export function fitDistance(radius: number, fovDeg: number, aspect: number, margin = 1.15): number {
  const v = (fovDeg * Math.PI) / 180
  const h = 2 * Math.atan(Math.tan(v / 2) * Math.max(0.1, aspect))
  const half = Math.min(v, h) / 2
  return (Math.max(1, radius) * margin) / Math.sin(half)
}

export const easeInOutCubic = (t: number) => {
  const u = Math.min(1, Math.max(0, t))
  return u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2
}

/** `var(--viz-1)` -> `--viz-1`; anything else -> null. */
export function cssVarName(expr: string): string | null {
  const m = /^var\((--[\w-]+)\)$/.exec(expr.trim())
  return m ? m[1] : null
}

// ── the stage <-> scene contract ─────────────────────────────────────────────────────────────────

/** What the page can ask of the 3D scene. */
export interface Graph3DApi {
  /** frame these nodes (default: every visible one) */
  fit: (ids?: Iterable<string>) => void
  /** dolly in (>1) or out (<1) */
  zoom: (factor: number) => void
}
