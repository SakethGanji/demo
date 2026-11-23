/**
 * The graph as the canvas reads it, plus the pure rules on top: filters, search, what lights up on hover or
 * selection, which labels show, what "Ask the graph" matches, and what a rebuild changed. No React here.
 */
import type { Understanding } from '@/features/ai-builder/types'
import { KIND_ORDER, kindGroup, type KindGroup } from './kinds'
import type { Point, Positions } from './layout'
import { projectKey } from './projects'
import type { KgEdge, KgGraph, KgNode } from './types'

export interface GraphModel {
  nodes: KgNode[]
  /** only edges whose both ends are in `nodes` */
  edges: KgEdge[]
  byId: Map<string, KgNode>
  byKey: Map<string, KgNode>
  degree: Map<string, number>
  /** id -> neighbour ids (both directions) */
  adj: Map<string, Set<string>>
  /** id -> edges touching it */
  incident: Map<string, KgEdge[]>
  radius: Map<string, number>
}

/** Radius by degree: 5 for a leaf, growing with √degree, capped so hubs never swallow their neighbours. */
export function nodeRadius(degree: number, kind?: string): number {
  const base = kind === 'concept' ? 6 : 4.5
  return Math.min(19, base + 2.4 * Math.sqrt(Math.max(0, degree)))
}

export function buildModel(g: Pick<KgGraph, 'nodes' | 'edges'>): GraphModel {
  const byId = new Map(g.nodes.map((n) => [n.id, n]))
  const byKey = new Map(g.nodes.map((n) => [n.key, n]))
  const edges = g.edges.filter((e) => byId.has(e.src_id) && byId.has(e.dst_id))
  const adj = new Map<string, Set<string>>(g.nodes.map((n) => [n.id, new Set()]))
  const incident = new Map<string, KgEdge[]>(g.nodes.map((n) => [n.id, []]))
  for (const e of edges) {
    if (e.src_id !== e.dst_id) {
      adj.get(e.src_id)?.add(e.dst_id)
      adj.get(e.dst_id)?.add(e.src_id)
    }
    incident.get(e.src_id)?.push(e)
    if (e.dst_id !== e.src_id) incident.get(e.dst_id)?.push(e)
  }
  const degree = new Map(g.nodes.map((n) => [n.id, adj.get(n.id)?.size ?? 0]))
  const radius = new Map(g.nodes.map((n) => [n.id, nodeRadius(degree.get(n.id) ?? 0, n.kind)]))
  return { nodes: g.nodes, edges, byId, byKey, degree, adj, incident, radius }
}

export function kindCounts(nodes: Pick<KgNode, 'kind'>[]): Record<KindGroup, number> {
  const out = Object.fromEntries(KIND_ORDER.map((k) => [k, 0])) as Record<KindGroup, number>
  for (const n of nodes) out[kindGroup(n.kind)]++
  return out
}

/** Relations with their counts, most frequent first (ties by name). */
export function relCounts(edges: Pick<KgEdge, 'rel'>[]): [string, number][] {
  const m = new Map<string, number>()
  for (const e of edges) m.set(e.rel, (m.get(e.rel) ?? 0) + 1)
  return [...m].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
}

export interface Filters {
  hiddenKinds: ReadonlySet<KindGroup>
  hiddenRels: ReadonlySet<string>
  /** team scope: project ids (or `'shared'` for nodes without one) to hide */
  hiddenProjects?: ReadonlySet<string>
}

export interface Visible {
  nodes: Set<string>
  edges: Set<number>
}

/** What survives the filters: nodes of a shown kind (and project), and shown-relation edges between two shown nodes. */
export function filterGraph(model: GraphModel, f: Filters): Visible {
  const hp = f.hiddenProjects
  const nodes = new Set(
    model.nodes.filter((n) => !f.hiddenKinds.has(kindGroup(n.kind)) && !(hp?.size && hp.has(projectKey(n)))).map((n) => n.id),
  )
  const edges = new Set(model.edges.filter((e) => !f.hiddenRels.has(e.rel) && nodes.has(e.src_id) && nodes.has(e.dst_id)).map((e) => e.id))
  return { nodes, edges }
}

const norm = (s: string) => s.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').trim()

/**
 * Nodes matching `query`, best first: exact label, label prefix, a word of the label, anywhere in the label, then
 * the key or an alias. Ties go to the bigger hub, then the label. Empty query: nothing.
 */
export function searchNodes(model: GraphModel, query: string, within?: ReadonlySet<string>): string[] {
  const q = norm(query)
  if (!q) return []
  const scored: { id: string; score: number; deg: number; label: string }[] = []
  for (const n of model.nodes) {
    if (within && !within.has(n.id)) continue
    const label = norm(n.label)
    let score = 0
    if (label === q) score = 100
    else if (label.startsWith(q)) score = 80
    else if (label.split(/[\s_\-:/]+/).some((w) => w.startsWith(q))) score = 60
    else if (label.includes(q)) score = 40
    else if (norm(n.key).includes(q) || n.aliases.some((a) => norm(a).includes(q))) score = 20
    else if (norm(n.summary).includes(q) && q.length >= 4) score = 5
    if (score) scored.push({ id: n.id, score, deg: model.degree.get(n.id) ?? 0, label })
  }
  scored.sort((a, b) => b.score - a.score || b.deg - a.deg || a.label.localeCompare(b.label))
  return scored.map((s) => s.id)
}

// ── emphasis ─────────────────────────────────────────────────────────────────────────────────────

export interface AskHits {
  /** matched concept node ids, in the engine's order */
  concepts: string[]
  /** capabilities / patterns / templates that implement them or that the engine retrieved */
  implementers: string[]
  nodes: Set<string>
  edges: Set<number>
}

/**
 * What the AI builder would "see" for a request: the concepts `/knowledge/understand` recognised, everything that
 * `implements` them, and what it retrieved (templates, patterns, node types by key). Edges are the implements links.
 */
export function askHits(model: GraphModel, u: Pick<Understanding, 'concepts' | 'retrieval'> | null | undefined): AskHits {
  const concepts: string[] = []
  const impl = new Set<string>()
  const edges = new Set<number>()
  for (const c of u?.concepts ?? []) {
    const n = model.byKey.get(c.key)
    if (n && !concepts.includes(n.id)) concepts.push(n.id)
  }
  const conceptSet = new Set(concepts)
  for (const e of model.edges) {
    if (e.rel === 'implements' && conceptSet.has(e.dst_id)) {
      impl.add(e.src_id)
      edges.add(e.id)
    }
  }
  const r = u?.retrieval
  const keys = [
    ...(r?.templates ?? []).map((t) => t.key),
    ...(r?.patterns ?? []).map((p) => p.key),
    ...(r?.subworkflows ?? []).map((s) => s.key),
    ...(r?.node_types ?? []).map((t) => (t.includes(':') ? t : `node:${t}`)),
    ...(r?.roles ?? []).map((x) => x.key),
  ]
  for (const k of keys) {
    const n = model.byKey.get(k)
    if (n && !conceptSet.has(n.id)) impl.add(n.id)
  }
  // retrieved nodes that link to a matched concept by any relation light that link too
  for (const id of impl) for (const e of model.incident.get(id) ?? []) if (conceptSet.has(e.src_id) || conceptSet.has(e.dst_id)) edges.add(e.id)
  const implementers = [...impl].filter((id) => !conceptSet.has(id))
  return { concepts, implementers, nodes: new Set([...concepts, ...implementers]), edges }
}

export interface Emphasis {
  /** null: nothing is emphasised, every visible node is at rest */
  lit: Set<string> | null
  /** edges that light up and flow */
  flow: Set<number>
  mode: 'focus' | 'ask' | 'search' | 'none'
}

/**
 * Focus (hover or selection) wins: the focused nodes, their visible neighbours and the edges between them. Without
 * focus, an Ask result lights its hits; without either, search matches light alone. Hidden things never light.
 */
export function emphasis(
  model: GraphModel,
  visible: Visible,
  s: { focus: (string | null | undefined)[]; ask?: AskHits | null; search?: string[] },
): Emphasis {
  const focus = s.focus.filter((id): id is string => !!id && visible.nodes.has(id))
  if (focus.length) {
    const lit = new Set(focus)
    const flow = new Set<number>()
    for (const id of focus) {
      for (const e of model.incident.get(id) ?? []) {
        if (!visible.edges.has(e.id)) continue
        flow.add(e.id)
        lit.add(e.src_id)
        lit.add(e.dst_id)
      }
    }
    return { lit, flow, mode: 'focus' }
  }
  if (s.ask && s.ask.nodes.size) {
    const lit = new Set([...s.ask.nodes].filter((id) => visible.nodes.has(id)))
    const flow = new Set([...s.ask.edges].filter((id) => visible.edges.has(id)))
    return { lit, flow, mode: 'ask' }
  }
  if (s.search && s.search.length) {
    return { lit: new Set(s.search.filter((id) => visible.nodes.has(id))), flow: new Set(), mode: 'search' }
  }
  return { lit: null, flow: new Set(), mode: 'none' }
}

/** How many of the biggest hubs keep a label at zoom `k` when nothing is focused. */
export function hubLabelBudget(k: number, n: number): number {
  const base = k < 0.6 ? 5 : k < 0.9 ? 9 : k < 1.4 ? 16 : k < 2.2 ? 30 : 60
  return Math.min(n, base)
}

/**
 * Which nodes carry a label (no label soup): focused nodes and the hovered one always; with emphasis, the lit
 * nodes (capped, biggest first); at rest, only the top hubs for the zoom. Hidden nodes never do.
 */
export function labelIds(
  model: GraphModel,
  visible: Visible,
  em: Emphasis,
  s: { k: number; selected?: string | null; hovered?: string | null; maxLit?: number },
): Set<string> {
  const out = new Set<string>()
  for (const id of [s.selected, s.hovered]) if (id && visible.nodes.has(id)) out.add(id)
  const byDeg = (ids: Iterable<string>) =>
    [...ids].filter((id) => visible.nodes.has(id)).sort((a, b) => (model.degree.get(b) ?? 0) - (model.degree.get(a) ?? 0) || (a < b ? -1 : 1))
  if (em.lit) {
    for (const id of byDeg(em.lit).slice(0, s.maxLit ?? 40)) out.add(id)
    return out
  }
  for (const id of byDeg(visible.nodes).slice(0, hubLabelBudget(s.k, visible.nodes.size))) out.add(id)
  return out
}

// ── rebuild ──────────────────────────────────────────────────────────────────────────────────────

/** Nodes a rebuild added, and nodes whose content changed (updated_at, status or label moved). */
export function diffGraph(prev: KgNode[] | null | undefined, next: KgNode[]): { added: string[]; changed: string[] } {
  if (!prev) return { added: [], changed: [] }
  const before = new Map(prev.map((n) => [n.id, n]))
  const added: string[] = []
  const changed: string[] = []
  for (const n of next) {
    const b = before.get(n.id)
    if (!b) added.push(n.id)
    else if (b.updated_at !== n.updated_at || b.status !== n.status || b.label !== n.label || b.validated_count !== n.validated_count) changed.push(n.id)
  }
  return { added, changed }
}

/** A wave out from the centre of the graph: each node's delay grows with its distance from the centroid (ms). */
export function rippleDelays(ids: Iterable<string>, pos: Positions, spanMs = 900): Map<string, number> {
  const pts = [...ids].map((id) => [id, pos.get(id)] as const).filter((e): e is readonly [string, Point] => !!e[1])
  const out = new Map<string, number>()
  if (!pts.length) return out
  let cx = 0
  let cy = 0
  for (const all of pos.values()) {
    cx += all.x
    cy += all.y
  }
  cx /= pos.size || 1
  cy /= pos.size || 1
  const dist = pts.map(([id, p]) => [id, Math.hypot(p.x - cx, p.y - cy)] as const)
  const max = Math.max(1, ...dist.map((d) => d[1]))
  for (const [id, d] of dist) out.set(id, Math.round((d / max) * spanMs))
  return out
}

// ── geometry ─────────────────────────────────────────────────────────────────────────────────────

const r2 = (v: number) => Math.round(v * 10) / 10

/**
 * A gentle quadratic curve from `a` to `b`, bowed to the left of travel by `bend` × length, trimmed so it starts and
 * ends at the node rims (`ra`, `rb`) instead of under the glass.
 */
export function edgePath(a: Point, b: Point, ra = 0, rb = 0, bend = 0.12): string {
  const dx = b.x - a.x
  const dy = b.y - a.y
  const len = Math.hypot(dx, dy)
  if (len < 1e-6) return `M${r2(a.x)},${r2(a.y)}`
  const cx = (a.x + b.x) / 2 - dy * bend
  const cy = (a.y + b.y) / 2 + dx * bend
  const trim = (p: Point, rr: number) => {
    const ux = cx - p.x
    const uy = cy - p.y
    const ul = Math.hypot(ux, uy) || 1
    const t = Math.min(rr, ul * 0.45)
    return { x: p.x + (ux / ul) * t, y: p.y + (uy / ul) * t }
  }
  const s = trim(a, ra)
  const e = trim(b, rb)
  return `M${r2(s.x)},${r2(s.y)}Q${r2(cx)},${r2(cy)} ${r2(e.x)},${r2(e.y)}`
}
