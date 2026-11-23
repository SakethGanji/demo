/**
 * A small deterministic force-directed layout for the knowledge canvas. Pure: no DOM, no React, no clock.
 *
 * Forces per tick (d3-force style, cooled by `alpha` from 1 to 0.001 over a fixed number of ticks):
 *   - many-body repulsion between every pair (stronger across kinds, so regions keep a gap between them),
 *   - link springs whose rest length grows with both radii; low-degree ends move more than hubs,
 *   - a pull toward the node's kind anchor (concepts in the middle, capabilities right, patterns left, roles below,
 *     apps above), so kinds form readable regions,
 *   - collision (radius + padding) and re-centring on 0,0.
 *
 * Clusters (optional): when any node carries a `cluster` key (the team page groups by project), nodes without one
 * (shared knowledge) keep the kind regions at the centre, and each cluster gathers around its own anchor on a ring
 * around them, with the same kind regions in miniature. Without a cluster key the layout is exactly the plain one.
 *
 * Determinism: nodes are processed in id order and the only randomness is a PRNG seeded from the ids, so the same
 * graph always lands in the same place. `initial` warm-starts known nodes (a rebuild or filter keeps the map
 * stable) and new nodes start beside their placed neighbours. Repulsion is O(n²); 300 nodes × 300 ticks runs in
 * about 120 ms on a laptop and 90 nodes in about 10 ms (see layout.test.ts).
 */
import type { KindGroup } from './kinds'

export interface Point {
  x: number
  y: number
}
export type Positions = Map<string, Point>

export interface LayoutNode {
  id: string
  group: KindGroup
  /** drawn radius: collision and spring rest length use it */
  r: number
  /** optional group (a project id): clustered nodes gather around their cluster's anchor; null/absent = the centre */
  cluster?: string | null
}
export interface LayoutEdge {
  src: string
  dst: string
}
export interface LayoutOptions {
  ticks?: number
  /** positions to start from (by id); nodes without one are seeded near their neighbours or their anchor */
  initial?: Positions
}

export const DEFAULT_TICKS = 300

/** FNV-1a, 32 bit. */
export function hashString(s: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return h >>> 0
}

/** mulberry32: a tiny seeded PRNG in [0, 1). */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** Direction of each kind's region from the centre (unit-ish vectors; concepts sit in the middle). */
const ANCHOR_DIR: Record<KindGroup, Point> = {
  concept: { x: 0, y: 0 },
  capability: { x: 1, y: 0.08 },
  pattern: { x: -1, y: -0.08 },
  role: { x: -0.1, y: 0.95 },
  app: { x: 0.1, y: -0.95 },
  other: { x: 0.72, y: -0.72 },
}

/** The spread of the regions for `n` nodes: grows like √n so density stays even. */
export const spreadFor = (n: number) => 60 + 30 * Math.sqrt(n)

/** Where each present kind's region is centred. A single kind sits at the origin. */
export function clusterAnchors(groups: Iterable<KindGroup>, n: number): Record<KindGroup, Point> {
  const present = new Set(groups)
  const s = spreadFor(n) * 0.95
  const out = {} as Record<KindGroup, Point>
  for (const g of Object.keys(ANCHOR_DIR) as KindGroup[]) {
    const d = present.size <= 1 ? { x: 0, y: 0 } : ANCHOR_DIR[g]
    out[g] = { x: d.x * s, y: d.y * s }
  }
  return out
}

/** The sorted, distinct cluster keys of `nodes` (empty: the layout is not clustered). */
export function clusterKeys(nodes: Pick<LayoutNode, 'cluster'>[]): string[] {
  const set = new Set<string>()
  for (const n of nodes) if (n.cluster) set.add(n.cluster)
  return [...set].sort()
}

/**
 * Where each cluster gathers: evenly round a ring outside the shared centre, in key order, starting at the top. The
 * ring is wide enough that the shared region and the biggest cluster do not overlap, and that neighbouring
 * clusters fit side by side. A lone cluster with nothing shared sits at the origin.
 */
export function clusterCentres(nodes: Pick<LayoutNode, 'cluster'>[]): Map<string, Point> {
  const keys = clusterKeys(nodes)
  const out = new Map<string, Point>()
  if (!keys.length) return out
  const size = new Map<string, number>()
  let shared = 0
  for (const n of nodes) {
    if (n.cluster) size.set(n.cluster, (size.get(n.cluster) ?? 0) + 1)
    else shared++
  }
  if (keys.length === 1 && shared === 0) {
    out.set(keys[0], { x: 0, y: 0 })
    return out
  }
  const reach = (m: number) => spreadFor(m) * 0.55
  const biggest = Math.max(...keys.map((k) => reach(size.get(k) ?? 1)))
  const centre = shared ? spreadFor(shared) * 0.75 : 0
  const fit = keys.length > 1 ? (keys.length * biggest * 2.3) / (2 * Math.PI) : 0
  const R = Math.max(centre + biggest + 40, fit)
  keys.forEach((k, i) => {
    const a = -Math.PI / 2 + (2 * Math.PI * i) / keys.length
    out.set(k, { x: Math.round(Math.cos(a) * R * 100) / 100, y: Math.round(Math.sin(a) * R * 100) / 100 })
  })
  return out
}

/**
 * Each node's anchor. Plain: its kind's region. Clustered: shared nodes use kind regions sized for the shared set,
 * clustered ones their cluster's centre plus a compact kind offset sized for the cluster.
 */
export function nodeAnchors(nodes: LayoutNode[]): Map<string, Point> {
  const out = new Map<string, Point>()
  const centres = clusterCentres(nodes)
  if (!centres.size) {
    const anchors = clusterAnchors(nodes.map((n) => n.group), nodes.length)
    for (const n of nodes) out.set(n.id, anchors[n.group])
    return out
  }
  const byCluster = new Map<string, LayoutNode[]>()
  for (const n of nodes) {
    const k = n.cluster || ''
    byCluster.set(k, [...(byCluster.get(k) ?? []), n])
  }
  for (const [k, members] of byCluster) {
    const local = clusterAnchors(members.map((n) => n.group), members.length)
    const c = k ? (centres.get(k) as Point) : { x: 0, y: 0 }
    const f = k ? 0.42 : 0.7
    for (const n of members) out.set(n.id, { x: c.x + local[n.group].x * f, y: c.y + local[n.group].y * f })
  }
  return out
}

/** The seed of a graph: its sorted ids, hashed. */
export const seedOf = (ids: string[]) => hashString([...ids].sort().join('|'))

/** Starting points: `initial` where known, else the mean of placed neighbours, else a random spot in the kind's region. */
export function seedPositions(nodes: LayoutNode[], edges: LayoutEdge[], initial?: Positions): Positions {
  const sorted = [...nodes].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
  const rand = mulberry32(seedOf(sorted.map((n) => n.id)))
  const anchors = nodeAnchors(sorted)
  const clustered = sorted.some((n) => n.cluster)
  const spread = spreadFor(sorted.length) * (clustered ? 0.45 : 1)
  const out: Positions = new Map()
  const nbrs = new Map<string, string[]>()
  for (const e of edges) {
    if (e.src === e.dst) continue
    nbrs.set(e.src, [...(nbrs.get(e.src) ?? []), e.dst])
    nbrs.set(e.dst, [...(nbrs.get(e.dst) ?? []), e.src])
  }
  // the PRNG advances twice per node in id order whether or not it is placed, so adding a known node never reshuffles the rest
  const draw = new Map(sorted.map((n) => [n.id, { a: rand() * Math.PI * 2, d: Math.sqrt(rand()) }]))
  const pending: LayoutNode[] = []
  for (const n of sorted) {
    const p = initial?.get(n.id)
    if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) out.set(n.id, { x: p.x, y: p.y })
    else pending.push(n)
  }
  for (const n of pending) {
    const { a, d } = draw.get(n.id) as { a: number; d: number }
    const placed = (nbrs.get(n.id) ?? []).map((id) => out.get(id)).filter((p): p is Point => !!p)
    if (placed.length && initial) {
      const mx = placed.reduce((s, p) => s + p.x, 0) / placed.length
      const my = placed.reduce((s, p) => s + p.y, 0) / placed.length
      out.set(n.id, { x: mx + Math.cos(a) * 18 * (0.5 + d), y: my + Math.sin(a) * 18 * (0.5 + d) })
    } else {
      const c = anchors.get(n.id) as Point
      const rad = spread * 0.38 * d
      out.set(n.id, { x: c.x + Math.cos(a) * rad, y: c.y + Math.sin(a) * rad })
    }
  }
  return out
}

/** Runs the simulation to the end and returns the settled positions (rounded to 0.01). */
export function runLayout(nodes: LayoutNode[], edges: LayoutEdge[], opts: LayoutOptions = {}): Positions {
  const ticks = Math.max(1, Math.floor(opts.ticks ?? DEFAULT_TICKS))
  const sorted = [...nodes].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
  const n = sorted.length
  const out: Positions = new Map()
  if (n === 0) return out

  const start = seedPositions(sorted, edges, opts.initial)
  const index = new Map(sorted.map((nd, i) => [nd.id, i]))
  const anchors = nodeAnchors(sorted)
  const clustered = sorted.some((nd) => nd.cluster)
  const x = new Float64Array(n)
  const y = new Float64Array(n)
  const vx = new Float64Array(n)
  const vy = new Float64Array(n)
  const r = new Float64Array(n)
  const ax = new Float64Array(n)
  const ay = new Float64Array(n)
  const grp = new Int32Array(n)
  // repulsion and springs treat a (cluster, kind) pair as one region, so clusters keep a gap between them too
  const groupIds = new Map<string, number>()
  sorted.forEach((nd, i) => {
    const p = start.get(nd.id) as Point
    const a = anchors.get(nd.id) as Point
    const g = clustered ? `${nd.cluster ?? ''}|${nd.group}` : nd.group
    x[i] = p.x
    y[i] = p.y
    r[i] = nd.r
    ax[i] = a.x
    ay[i] = a.y
    if (!groupIds.has(g)) groupIds.set(g, groupIds.size)
    grp[i] = groupIds.get(g) as number
  })

  // links: drop self-loops, unknown ends and duplicates (a pair is one spring), in index order so the input's edge
  // order never changes the result
  const pairs = new Map<number, [number, number]>()
  for (const e of edges) {
    const a = index.get(e.src)
    const b = index.get(e.dst)
    if (a === undefined || b === undefined || a === b) continue
    const s = Math.min(a, b)
    const t = Math.max(a, b)
    pairs.set(s * n + t, [s, t])
  }
  const ls: number[] = []
  const lt: number[] = []
  const deg = new Float64Array(n)
  for (const [, [s, t]] of [...pairs].sort((p, q) => p[0] - q[0])) {
    ls.push(s)
    lt.push(t)
    deg[s]++
    deg[t]++
  }
  const m = ls.length
  const rest = new Float64Array(m)
  const stiff = new Float64Array(m)
  const bias = new Float64Array(m)
  for (let j = 0; j < m; j++) {
    const s = ls[j]
    const t = lt[j]
    rest[j] = 26 + r[s] + r[t] + (grp[s] !== grp[t] ? 22 : 0)
    stiff[j] = 0.7 / Math.min(deg[s], deg[t])
    bias[j] = deg[s] / (deg[s] + deg[t])
  }

  const CHARGE = 340
  const CROSS = 1.7
  const MAX_D2 = 900 * 900
  const PAD = 6
  const CLUSTER = n > 1 && groupIds.size > 1 ? (clustered ? 0.06 : 0.045) : 0.01
  const FRICTION = 0.58
  const alphaMin = 0.001
  const decay = 1 - Math.pow(alphaMin, 1 / ticks)
  let alpha = 1

  for (let tick = 0; tick < ticks; tick++) {
    // springs
    for (let j = 0; j < m; j++) {
      const s = ls[j]
      const t = lt[j]
      let dx = x[t] + vx[t] - x[s] - vx[s]
      let dy = y[t] + vy[t] - y[s] - vy[s]
      let l = Math.sqrt(dx * dx + dy * dy)
      if (l < 1e-6) {
        dx = 1e-3 * ((j % 7) - 3)
        dy = 1e-3 * ((j % 5) - 2)
        l = Math.sqrt(dx * dx + dy * dy)
      }
      const k = ((l - rest[j]) / l) * alpha * stiff[j]
      dx *= k
      dy *= k
      vx[t] -= dx * bias[j]
      vy[t] -= dy * bias[j]
      vx[s] += dx * (1 - bias[j])
      vy[s] += dy * (1 - bias[j])
    }
    // repulsion + collision, every pair once
    for (let i = 0; i < n; i++) {
      const xi = x[i]
      const yi = y[i]
      for (let j = i + 1; j < n; j++) {
        let dx = x[j] - xi
        let dy = y[j] - yi
        let d2 = dx * dx + dy * dy
        if (d2 > MAX_D2) continue
        if (d2 < 1e-6) {
          // coincident: nudge apart along a fixed, index-derived direction (still deterministic)
          dx = ((i * 31 + j * 17) % 13) / 13 - 0.5 || 0.3
          dy = ((i * 7 + j * 29) % 11) / 11 - 0.5 || -0.3
          d2 = dx * dx + dy * dy
        }
        const d = Math.sqrt(d2)
        const f = (CHARGE * (grp[i] === grp[j] ? 1 : CROSS) * alpha) / d2
        let fx = dx * f
        let fy = dy * f
        const min = r[i] + r[j] + PAD
        if (d < min) {
          const push = ((min - d) / d) * 0.35
          fx += dx * push
          fy += dy * push
        }
        vx[i] -= fx
        vy[i] -= fy
        vx[j] += fx
        vy[j] += fy
      }
    }
    // regions, then integrate
    let mx = 0
    let my = 0
    for (let i = 0; i < n; i++) {
      vx[i] += (ax[i] - x[i]) * CLUSTER * alpha
      vy[i] += (ay[i] - y[i]) * CLUSTER * alpha
      vx[i] *= FRICTION
      vy[i] *= FRICTION
      x[i] += vx[i]
      y[i] += vy[i]
      mx += x[i]
      my += y[i]
    }
    mx /= n
    my /= n
    // clustered anchors are absolute (shared knowledge at the origin), so only the plain layout re-centres
    if (!clustered) {
      for (let i = 0; i < n; i++) {
        x[i] -= mx
        y[i] -= my
      }
    }
    alpha += (0 - alpha) * decay
  }

  const round = (v: number) => Math.round(v * 100) / 100
  sorted.forEach((nd, i) => out.set(nd.id, { x: round(x[i]), y: round(y[i]) }))
  return out
}

export interface Bounds {
  minX: number
  minY: number
  maxX: number
  maxY: number
}

/** The box around `ids` (or every position), each point grown by its radius. Null when nothing is placed. */
export function boundsOf(pos: Positions, ids?: Iterable<string>, radius: (id: string) => number = () => 0): Bounds | null {
  let b: Bounds | null = null
  for (const id of ids ?? pos.keys()) {
    const p = pos.get(id)
    if (!p) continue
    const rr = radius(id)
    if (!b) b = { minX: p.x - rr, minY: p.y - rr, maxX: p.x + rr, maxY: p.y + rr }
    else {
      b.minX = Math.min(b.minX, p.x - rr)
      b.minY = Math.min(b.minY, p.y - rr)
      b.maxX = Math.max(b.maxX, p.x + rr)
      b.maxY = Math.max(b.maxY, p.y + rr)
    }
  }
  return b
}

/** Positions part-way from `from` to `to` (t in 0..1). Nodes missing from `from` sit at their target. */
export function lerpPositions(from: Positions, to: Positions, t: number): Positions {
  const out: Positions = new Map()
  for (const [id, b] of to) {
    const a = from.get(id)
    out.set(id, a ? { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t } : b)
  }
  return out
}
