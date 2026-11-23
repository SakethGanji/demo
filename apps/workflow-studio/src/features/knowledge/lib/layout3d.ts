/**
 * The 2D layout (`layout.ts`) lifted into three dimensions for the 3D view. Pure: no DOM, no React, no clock, no
 * three.js. Same forces, same determinism, same warm start:
 *   - many-body repulsion between every pair (stronger across kinds),
 *   - link springs whose rest length grows with both radii; low-degree ends move more than hubs,
 *   - a pull toward the node's anchor: the 2D kind regions in the x/y plane (concepts in the middle, capabilities
 *     right, patterns left, roles below, apps above), with depth left free so the graph becomes a cloud,
 *   - collision (radius + padding) and re-centring on the origin.
 *
 * Clusters (the team page grouped by project): shared knowledge keeps its kind regions at the centre, and each
 * cluster gathers round its own centre on a sphere around it (a Fibonacci sphere, so any number of projects spread
 * evenly), with the kind regions in miniature. Each project becomes its own constellation and the shared concepts
 * they all link to hang in the space between them.
 *
 * Repulsion is O(n²): 300 nodes × 300 ticks is ~13 M pair visits, a few hundred ms at worst, run once per graph
 * shape (the "warm-up": the view never animates a live simulation, it tweens to the settled one).
 */
import { clusterAnchors, clusterCentres, clusterKeys, mulberry32, seedOf, spreadFor, type LayoutEdge, type LayoutNode } from './layout'

export interface Point3 {
  x: number
  y: number
  z: number
}
export type Positions3 = Map<string, Point3>

export interface Layout3Options {
  ticks?: number
  /** positions to start from (by id); nodes without one are seeded near their neighbours or their anchor */
  initial?: Positions3
}

export const DEFAULT_TICKS_3D = 300

/**
 * Where each cluster gathers: `n` points on a sphere round the shared centre, in key order. The radius matches the
 * 2D ring's (wide enough that the shared region and the biggest cluster never overlap). A lone cluster with nothing
 * shared sits at the origin.
 */
export function clusterCentres3d(nodes: Pick<LayoutNode, 'cluster'>[]): Map<string, Point3> {
  const flat = clusterCentres(nodes)
  const keys = clusterKeys(nodes)
  const out = new Map<string, Point3>()
  if (!keys.length) return out
  let R = 0
  for (const p of flat.values()) R = Math.max(R, Math.hypot(p.x, p.y))
  if (R === 0) {
    out.set(keys[0], { x: 0, y: 0, z: 0 })
    return out
  }
  const n = keys.length
  const golden = Math.PI * (3 - Math.sqrt(5))
  keys.forEach((k, i) => {
    // two clusters: left and right rather than top and bottom, so they read side by side on screen
    if (n === 2) {
      out.set(k, { x: i === 0 ? -R : R, y: 0, z: 0 })
      return
    }
    const y = 1 - (2 * (i + 0.5)) / n
    const rr = Math.sqrt(Math.max(0, 1 - y * y))
    const a = golden * i
    out.set(k, { x: round(Math.cos(a) * rr * R), y: round(y * R), z: round(Math.sin(a) * rr * R) })
  })
  return out
}

/** Each node's anchor in 3D: its kind region in the x/y plane (round its cluster's centre when clustered), z = 0. */
export function nodeAnchors3d(nodes: LayoutNode[]): Map<string, Point3> {
  const out = new Map<string, Point3>()
  const centres = clusterCentres3d(nodes)
  if (!centres.size) {
    const anchors = clusterAnchors(nodes.map((n) => n.group), nodes.length)
    for (const n of nodes) out.set(n.id, { x: anchors[n.group].x, y: anchors[n.group].y, z: 0 })
    return out
  }
  const byCluster = new Map<string, LayoutNode[]>()
  for (const n of nodes) {
    const k = n.cluster || ''
    const list = byCluster.get(k)
    if (list) list.push(n)
    else byCluster.set(k, [n])
  }
  for (const [k, members] of byCluster) {
    const local = clusterAnchors(members.map((n) => n.group), members.length)
    const c = k ? (centres.get(k) as Point3) : { x: 0, y: 0, z: 0 }
    const f = k ? 0.42 : 0.7
    for (const n of members) out.set(n.id, { x: c.x + local[n.group].x * f, y: c.y + local[n.group].y * f, z: c.z })
  }
  return out
}

/** Starting points: `initial` where known, else beside placed neighbours, else a seeded spot in a ball round the anchor. */
export function seedPositions3d(nodes: LayoutNode[], edges: LayoutEdge[], initial?: Positions3): Positions3 {
  const sorted = [...nodes].sort(byId)
  const rand = mulberry32(seedOf(sorted.map((n) => n.id)) ^ 0x3d3d3d)
  const anchors = nodeAnchors3d(sorted)
  const clustered = sorted.some((n) => n.cluster)
  const spread = spreadFor(sorted.length) * (clustered ? 0.45 : 1)
  const nbrs = new Map<string, string[]>()
  for (const e of edges) {
    if (e.src === e.dst) continue
    push(nbrs, e.src, e.dst)
    push(nbrs, e.dst, e.src)
  }
  // three draws per node in id order whether or not it is placed, so a known node never reshuffles the rest
  const draw = new Map(sorted.map((n) => [n.id, unitBall(rand)]))
  const out: Positions3 = new Map()
  const pending: LayoutNode[] = []
  for (const n of sorted) {
    const p = initial?.get(n.id)
    if (p && Number.isFinite(p.x) && Number.isFinite(p.y) && Number.isFinite(p.z)) out.set(n.id, { x: p.x, y: p.y, z: p.z })
    else pending.push(n)
  }
  for (const n of pending) {
    const d = draw.get(n.id) as Point3
    const placed = (nbrs.get(n.id) ?? []).map((id) => out.get(id)).filter((p): p is Point3 => !!p)
    if (placed.length && initial) {
      const m = mean(placed)
      out.set(n.id, { x: m.x + d.x * 22, y: m.y + d.y * 22, z: m.z + d.z * 22 })
    } else {
      const c = anchors.get(n.id) as Point3
      const rad = spread * 0.4
      out.set(n.id, { x: c.x + d.x * rad, y: c.y + d.y * rad, z: c.z + d.z * rad })
    }
  }
  return out
}

/** Runs the simulation to the end and returns the settled positions (rounded to 0.01). */
export function runLayout3d(nodes: LayoutNode[], edges: LayoutEdge[], opts: Layout3Options = {}): Positions3 {
  const ticks = Math.max(1, Math.floor(opts.ticks ?? DEFAULT_TICKS_3D))
  const sorted = [...nodes].sort(byId)
  const n = sorted.length
  const out: Positions3 = new Map()
  if (n === 0) return out

  const start = seedPositions3d(sorted, edges, opts.initial)
  const index = new Map(sorted.map((nd, i) => [nd.id, i]))
  const anchors = nodeAnchors3d(sorted)
  const clustered = sorted.some((nd) => nd.cluster)
  const x = new Float64Array(n)
  const y = new Float64Array(n)
  const z = new Float64Array(n)
  const vx = new Float64Array(n)
  const vy = new Float64Array(n)
  const vz = new Float64Array(n)
  const r = new Float64Array(n)
  const ax = new Float64Array(n)
  const ay = new Float64Array(n)
  const az = new Float64Array(n)
  const grp = new Int32Array(n)
  const groupIds = new Map<string, number>()
  sorted.forEach((nd, i) => {
    const p = start.get(nd.id) as Point3
    const a = anchors.get(nd.id) as Point3
    const g = clustered ? `${nd.cluster ?? ''}|${nd.group}` : nd.group
    x[i] = p.x
    y[i] = p.y
    z[i] = p.z
    r[i] = nd.r
    ax[i] = a.x
    ay[i] = a.y
    az[i] = a.z
    if (!groupIds.has(g)) groupIds.set(g, groupIds.size)
    grp[i] = groupIds.get(g) as number
  })

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
    rest[j] = 30 + r[s] + r[t] + (grp[s] !== grp[t] ? 24 : 0)
    stiff[j] = 0.7 / Math.min(deg[s], deg[t])
    bias[j] = deg[s] / (deg[s] + deg[t])
  }

  // charge is a little higher than in 2D: in 3D the same force spreads over a sphere, not a circle
  const CHARGE = 520
  const CROSS = 1.7
  const MAX_D2 = 1000 * 1000
  const PAD = 7
  const PULL = n > 1 && groupIds.size > 1 ? (clustered ? 0.06 : 0.04) : 0.01
  // depth is held more loosely than the kind plane, so the regions read from the front and the cloud has volume
  const PULL_Z = PULL * 0.35
  const FRICTION = 0.58
  const decay = 1 - Math.pow(0.001, 1 / ticks)
  let alpha = 1

  for (let tick = 0; tick < ticks; tick++) {
    for (let j = 0; j < m; j++) {
      const s = ls[j]
      const t = lt[j]
      let dx = x[t] + vx[t] - x[s] - vx[s]
      let dy = y[t] + vy[t] - y[s] - vy[s]
      let dz = z[t] + vz[t] - z[s] - vz[s]
      let l = Math.sqrt(dx * dx + dy * dy + dz * dz)
      if (l < 1e-6) {
        dx = 1e-3 * ((j % 7) - 3)
        dy = 1e-3 * ((j % 5) - 2)
        dz = 1e-3 * ((j % 3) - 1)
        l = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1e-3
      }
      const k = ((l - rest[j]) / l) * alpha * stiff[j]
      dx *= k
      dy *= k
      dz *= k
      vx[t] -= dx * bias[j]
      vy[t] -= dy * bias[j]
      vz[t] -= dz * bias[j]
      vx[s] += dx * (1 - bias[j])
      vy[s] += dy * (1 - bias[j])
      vz[s] += dz * (1 - bias[j])
    }
    for (let i = 0; i < n; i++) {
      const xi = x[i]
      const yi = y[i]
      const zi = z[i]
      for (let j = i + 1; j < n; j++) {
        let dx = x[j] - xi
        let dy = y[j] - yi
        let dz = z[j] - zi
        let d2 = dx * dx + dy * dy + dz * dz
        if (d2 > MAX_D2) continue
        if (d2 < 1e-6) {
          dx = ((i * 31 + j * 17) % 13) / 13 - 0.5 || 0.3
          dy = ((i * 7 + j * 29) % 11) / 11 - 0.5 || -0.3
          dz = ((i * 13 + j * 5) % 9) / 9 - 0.5 || 0.2
          d2 = dx * dx + dy * dy + dz * dz
        }
        const d = Math.sqrt(d2)
        const f = (CHARGE * (grp[i] === grp[j] ? 1 : CROSS) * alpha) / d2
        let fx = dx * f
        let fy = dy * f
        let fz = dz * f
        const min = r[i] + r[j] + PAD
        if (d < min) {
          const p = ((min - d) / d) * 0.35
          fx += dx * p
          fy += dy * p
          fz += dz * p
        }
        vx[i] -= fx
        vy[i] -= fy
        vz[i] -= fz
        vx[j] += fx
        vy[j] += fy
        vz[j] += fz
      }
    }
    let mx = 0
    let my = 0
    let mz = 0
    for (let i = 0; i < n; i++) {
      vx[i] += (ax[i] - x[i]) * PULL * alpha
      vy[i] += (ay[i] - y[i]) * PULL * alpha
      vz[i] += (az[i] - z[i]) * (clustered ? PULL : PULL_Z) * alpha
      vx[i] *= FRICTION
      vy[i] *= FRICTION
      vz[i] *= FRICTION
      x[i] += vx[i]
      y[i] += vy[i]
      z[i] += vz[i]
      mx += x[i]
      my += y[i]
      mz += z[i]
    }
    if (!clustered) {
      mx /= n
      my /= n
      mz /= n
      for (let i = 0; i < n; i++) {
        x[i] -= mx
        y[i] -= my
        z[i] -= mz
      }
    }
    alpha += (0 - alpha) * decay
  }

  sorted.forEach((nd, i) => out.set(nd.id, { x: round(x[i]), y: round(y[i]), z: round(z[i]) }))
  return out
}

/** The sphere round `ids` (or every position), each point grown by its radius. Null when nothing is placed. */
export function boundingSphere(pos: Positions3, ids?: Iterable<string>, radius: (id: string) => number = () => 0): { c: Point3; r: number } | null {
  const pts: [Point3, number][] = []
  for (const id of ids ?? pos.keys()) {
    const p = pos.get(id)
    if (p) pts.push([p, radius(id)])
  }
  if (!pts.length) return null
  const c = mean(pts.map((e) => e[0]))
  let r = 0
  for (const [p, rr] of pts) r = Math.max(r, Math.hypot(p.x - c.x, p.y - c.y, p.z - c.z) + rr)
  return { c, r }
}

/** Positions part-way from `from` to `to` (t in 0..1). Nodes missing from `from` sit at their target. */
export function lerpPositions3(from: Positions3, to: Positions3, t: number): Positions3 {
  const out: Positions3 = new Map()
  for (const [id, b] of to) {
    const a = from.get(id)
    out.set(id, a ? { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, z: a.z + (b.z - a.z) * t } : b)
  }
  return out
}

// ── helpers ──────────────────────────────────────────────────────────────────────────────────────

const round = (v: number) => Math.round(v * 100) / 100
const byId = (a: { id: string }, b: { id: string }) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)

function push(m: Map<string, string[]>, k: string, v: string) {
  const list = m.get(k)
  if (list) list.push(v)
  else m.set(k, [v])
}

function mean(ps: Point3[]): Point3 {
  let x = 0
  let y = 0
  let z = 0
  for (const p of ps) {
    x += p.x
    y += p.y
    z += p.z
  }
  const n = ps.length || 1
  return { x: x / n, y: y / n, z: z / n }
}

/** A seeded point in the unit ball (uniform by volume). */
function unitBall(rand: () => number): Point3 {
  const u = rand() * 2 - 1
  const a = rand() * Math.PI * 2
  const d = Math.cbrt(rand())
  const s = Math.sqrt(1 - u * u)
  return { x: s * Math.cos(a) * d, y: s * Math.sin(a) * d, z: u * d }
}
