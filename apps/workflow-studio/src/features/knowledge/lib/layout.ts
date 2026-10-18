/**
 * Pure layouts of the knowledge graph (PLAN-CAPGRAPH-UI §2.2). Nothing here touches the DOM, and the same input always gives the
 * same output. Boxes use fixed sizes, so there is no measuring pass: edge paths are computed here and drawn by the page.
 *
 *   overviewLayout   columns: roles | concepts (grouped, 2 sub-columns) | capabilities (blocks by subkind); patterns and apps in a top band
 *   egoLayout        the focus at (0, 0) with its neighbours on an ellipse, sectors in the same order as the overview
 */
import { asKind, type KgKind } from '../kit/kinds'
import type { EdgeView, NodeView } from '../types'

export interface Box { x: number; y: number; w: number; h: number }
export interface Pt { x: number; y: number }

export const SIZE: Record<KgKind, { w: number; h: number }> = {
  concept: { w: 168, h: 40 },
  role: { w: 120, h: 62 },
  capability: { w: 152, h: 30 },
  pattern: { w: 208, h: 56 },
  app: { w: 208, h: 56 },
}
export const sizeOf = (n: Pick<NodeView, 'kind'>) => SIZE[asKind(n.kind)]

export interface LaidEdge {
  id: string
  src: string
  dst: string
  rel: string
  props: Record<string, unknown>
  d: string
  labelAt: Pt
}
export interface GroupCaption { key: string; label: string; x: number; y: number }

export interface OverviewLayout {
  positions: Map<string, Box>
  edges: Map<string, LaidEdge>
  groups: GroupCaption[]
  bounds: Box | null
  /** the concepts column box (the plate of the assembly) */
  conceptBounds: Box | null
}

export interface OverviewOpts { kinds: ReadonlySet<KgKind>; nodeTypes: boolean }

const COL = { role: 0, concept: 320, capability: 760 }
const CONCEPT_PITCH = 52
const CONCEPT_SUBCOL = 188
const GROUP_GAP = 28
const CAP_PITCH = 40
const CAP_COL_W = 168
const CAP_MAX_ROWS = 14
const ROLE_PITCH = 80
const BAND_Y = -140
const BAND_PER_ROW = 5
const BAND_PITCH_X = 224
const BAND_PITCH_Y = 72

export const CONCEPT_GROUPS: { key: string; label: string }[] = [
  { key: 'process', label: 'Processes' },
  { key: 'entity', label: 'Entities' },
  { key: 'control', label: 'Controls' },
  { key: 'artifact', label: 'Artifacts' },
]
const CAP_BLOCKS: { key: string; label: string }[] = [
  { key: 'subworkflow', label: 'Sub-workflows' },
  { key: 'workflow', label: 'Workflows' },
  { key: 'api_operation', label: 'API operations' },
  { key: 'template', label: 'Templates' },
  { key: 'interface', label: 'Interfaces' },
  { key: 'screen_component', label: 'Screen components' },
  { key: 'node_type', label: 'Node types' },
]

const byLabel = (a: NodeView, b: NodeView) => a.label.localeCompare(b.label) || a.key.localeCompare(b.key)
const conceptGroup = (n: NodeView) => (typeof n.props?.kind === 'string' ? (n.props.kind as string) : n.subkind ?? 'other')
const cy = (b: Box) => b.y + b.h / 2
const cx = (b: Box) => b.x + b.w / 2

export function isVisible(n: NodeView, opts: OverviewOpts): boolean {
  const kind = asKind(n.kind)
  if (kind === 'capability' && n.subkind === 'node_type') return opts.nodeTypes
  return opts.kinds.has(kind)
}

function cubicMid(p0: Pt, p1: Pt, p2: Pt, p3: Pt): Pt {
  const t = 0.5, u = 1 - t
  return {
    x: u * u * u * p0.x + 3 * u * u * t * p1.x + 3 * u * t * t * p2.x + t * t * t * p3.x,
    y: u * u * u * p0.y + 3 * u * u * t * p1.y + 3 * u * t * t * p2.y + t * t * t * p3.y,
  }
}

const f = (n: number) => Math.round(n * 10) / 10
const cubic = (p0: Pt, p1: Pt, p2: Pt, p3: Pt) => `M${f(p0.x)},${f(p0.y)} C${f(p1.x)},${f(p1.y)} ${f(p2.x)},${f(p2.y)} ${f(p3.x)},${f(p3.y)}`

const COL_INDEX: Record<KgKind, number> = { role: 0, concept: 1, capability: 2, pattern: -1, app: -1 }

/** The path between two overview boxes: facing sides left to right, a vertical cubic for the top band, a bulge inside a column. */
export function routeOverview(a: Box, ak: KgKind, b: Box, bk: KgKind): { d: string; labelAt: Pt } {
  const aTop = COL_INDEX[ak] < 0
  const bTop = COL_INDEX[bk] < 0
  if (aTop !== bTop) {
    const [top, low] = aTop ? [a, b] : [b, a]
    const p0 = { x: cx(top), y: top.y + top.h }
    const p3 = { x: cx(low), y: low.y }
    const dy = Math.max(30, (p3.y - p0.y) / 2)
    const p1 = { x: p0.x, y: p0.y + dy }
    const p2 = { x: p3.x, y: p3.y - dy }
    return { d: cubic(p0, p1, p2, p3), labelAt: cubicMid(p0, p1, p2, p3) }
  }
  const ca = COL_INDEX[ak]
  const cb = COL_INDEX[bk]
  if (ca === cb) {
    const p0 = { x: a.x + a.w, y: cy(a) }
    const p3 = { x: b.x + b.w, y: cy(b) }
    const k = 40 + Math.abs(p3.y - p0.y) * 0.12
    const p1 = { x: p0.x + k, y: p0.y }
    const p2 = { x: p3.x + k, y: p3.y }
    return { d: cubic(p0, p1, p2, p3), labelAt: cubicMid(p0, p1, p2, p3) }
  }
  const [l, r] = ca < cb ? [a, b] : [b, a]
  const p0 = { x: l.x + l.w, y: cy(l) }
  const p3 = { x: r.x, y: cy(r) }
  const dx = Math.max(40, (p3.x - p0.x) / 2)
  const p1 = { x: p0.x + dx, y: p0.y }
  const p2 = { x: p3.x - dx, y: p3.y }
  return { d: cubic(p0, p1, p2, p3), labelAt: cubicMid(p0, p1, p2, p3) }
}

export function overviewLayout(nodes: readonly NodeView[], edges: readonly EdgeView[], opts: OverviewOpts): OverviewLayout {
  const vis = nodes.filter((n) => isVisible(n, opts))
  const positions = new Map<string, Box>()
  const groups: GroupCaption[] = []
  const kindOf = new Map(vis.map((n) => [n.id, asKind(n.kind)]))

  // concepts: grouped, two sub-columns, row-major inside a group
  const concepts = vis.filter((n) => kindOf.get(n.id) === 'concept').sort(byLabel)
  const order = [...CONCEPT_GROUPS.map((g) => g.key), ...[...new Set(concepts.map(conceptGroup))].filter((k) => !CONCEPT_GROUPS.some((g) => g.key === k)).sort()]
  let yCursor = 0
  for (const key of order) {
    const items = concepts.filter((c) => conceptGroup(c) === key)
    if (!items.length) continue
    groups.push({ key: `concept:${key}`, label: CONCEPT_GROUPS.find((g) => g.key === key)?.label ?? 'Other concepts', x: COL.concept, y: yCursor })
    yCursor += 22
    items.forEach((n, i) => {
      const s = SIZE.concept
      positions.set(n.id, { x: COL.concept + (i % 2) * CONCEPT_SUBCOL, y: yCursor + Math.floor(i / 2) * CONCEPT_PITCH, w: s.w, h: s.h })
    })
    yCursor += Math.ceil(items.length / 2) * CONCEPT_PITCH + GROUP_GAP
  }

  // barycentre of the concepts each node connects to
  const bary = new Map<string, number>()
  {
    const sums = new Map<string, { s: number; n: number }>()
    for (const e of edges) {
      const sk = kindOf.get(e.src_id)
      const dk = kindOf.get(e.dst_id)
      if (!sk || !dk) continue
      const [c, o] = sk === 'concept' ? [e.src_id, e.dst_id] : dk === 'concept' ? [e.dst_id, e.src_id] : [null, null]
      if (!c || !o) continue
      const box = positions.get(c)
      if (!box) continue
      const cur = sums.get(o) ?? { s: 0, n: 0 }
      cur.s += cy(box)
      cur.n += 1
      sums.set(o, cur)
    }
    for (const [id, v] of sums) bary.set(id, v.s / v.n)
  }
  const byBary = (a: NodeView, b: NodeView) => {
    const x = bary.get(a.id), y = bary.get(b.id)
    if (x === undefined && y === undefined) return byLabel(a, b)
    if (x === undefined) return 1
    if (y === undefined) return -1
    return x - y || byLabel(a, b)
  }

  // roles
  const roles = vis.filter((n) => kindOf.get(n.id) === 'role').sort(byBary)
  if (roles.length) groups.push({ key: 'roles', label: 'Roles', x: COL.role, y: -22 })
  roles.forEach((n, i) => positions.set(n.id, { x: COL.role, y: i * ROLE_PITCH, w: SIZE.role.w, h: SIZE.role.h }))

  // capabilities: a block per subkind, columns of at most CAP_MAX_ROWS
  const caps = vis.filter((n) => kindOf.get(n.id) === 'capability')
  const subkinds = [...CAP_BLOCKS.map((b) => b.key), ...[...new Set(caps.map((c) => c.subkind ?? 'other'))].filter((k) => !CAP_BLOCKS.some((b) => b.key === k)).sort()]
  let col = 0
  for (const sub of subkinds) {
    const items = caps.filter((c) => (c.subkind ?? 'other') === sub).sort(byBary)
    if (!items.length) continue
    groups.push({ key: `cap:${sub}`, label: CAP_BLOCKS.find((b) => b.key === sub)?.label ?? 'Other', x: COL.capability + col * CAP_COL_W, y: -22 })
    items.forEach((n, i) => {
      positions.set(n.id, { x: COL.capability + (col + Math.floor(i / CAP_MAX_ROWS)) * CAP_COL_W, y: (i % CAP_MAX_ROWS) * CAP_PITCH, w: SIZE.capability.w, h: SIZE.capability.h })
    })
    col += Math.ceil(items.length / CAP_MAX_ROWS)
  }

  // top band: patterns (most validated first), then apps
  const band = [
    ...vis.filter((n) => kindOf.get(n.id) === 'pattern').sort((a, b) => b.validated_count - a.validated_count || byLabel(a, b)),
    ...vis.filter((n) => kindOf.get(n.id) === 'app').sort(byLabel),
  ]
  const rows = Math.ceil(band.length / BAND_PER_ROW)
  const centre = COL.concept + (CONCEPT_SUBCOL + SIZE.concept.w) / 2
  band.forEach((n, i) => {
    const row = Math.floor(i / BAND_PER_ROW)
    const inRow = Math.min(BAND_PER_ROW, band.length - row * BAND_PER_ROW)
    const x0 = centre - (inRow * BAND_PITCH_X - (BAND_PITCH_X - SIZE.pattern.w)) / 2
    positions.set(n.id, { x: x0 + (i % BAND_PER_ROW) * BAND_PITCH_X, y: BAND_Y - (rows - 1 - row) * BAND_PITCH_Y, w: SIZE.pattern.w, h: SIZE.pattern.h })
  })

  // edges: those that touch a concept, plus escalation between roles
  const laid = new Map<string, LaidEdge>()
  for (const e of edges) {
    const a = positions.get(e.src_id)
    const b = positions.get(e.dst_id)
    const ak = kindOf.get(e.src_id)
    const bk = kindOf.get(e.dst_id)
    if (!a || !b || !ak || !bk) continue
    const keep = ak === 'concept' || bk === 'concept' || (e.rel === 'escalates_to' && ak === 'role' && bk === 'role')
    if (!keep) continue
    const { d, labelAt } = routeOverview(a, ak, b, bk)
    const id = `e${e.id}`
    laid.set(id, { id, src: e.src_id, dst: e.dst_id, rel: e.rel, props: e.props ?? {}, d, labelAt })
  }

  const conceptBoxes = concepts.map((c) => positions.get(c.id)).filter((b): b is Box => !!b)
  return { positions, edges: laid, groups, bounds: unionBoxes([...positions.values()]), conceptBounds: unionBoxes(conceptBoxes) }
}

export function unionBoxes(boxes: readonly Box[]): Box | null {
  if (!boxes.length) return null
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
  for (const b of boxes) {
    x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y)
    x1 = Math.max(x1, b.x + b.w); y1 = Math.max(y1, b.y + b.h)
  }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}

export function overlaps(a: Box, b: Box, margin = 0): boolean {
  return a.x < b.x + b.w + margin && b.x < a.x + a.w + margin && a.y < b.y + b.h + margin && b.y < a.y + a.h + margin
}

// ── ego view ───────────────────────────────────────────────────────────────────────────────────

export interface EgoEdge extends LaidEdge {
  /** "out": the focus (or the parent) is the source */
  direction: 'out' | 'in'
  hop: 1 | 2
  /** the node it connects to the focus (or parent) */
  other: string
}
export interface EgoSector { key: string; kind: KgKind; rel: string; direction: 'out' | 'in'; count: number; at: Pt }
export interface EgoMore { kind: KgKind; count: number; box: Box }
export interface EgoLayout {
  focus: string
  positions: Map<string, Box>
  edges: Map<string, EgoEdge>
  hop: Map<string, 1 | 2>
  /** hop-1 ids clockwise from 12 o'clock */
  clockwise: string[]
  angle: Map<string, number>
  sectors: EgoSector[]
  more: EgoMore[]
  ring: { rx: number; ry: number }
  bounds: Box
}

const SECTOR_ORDER: KgKind[] = ['role', 'concept', 'pattern', 'capability']
const sectorOf = (k: KgKind): KgKind => (k === 'app' ? 'pattern' : k)
const MAX_HOP2 = 40

function rayExit(center: Pt, toward: Pt, box: Box): Pt {
  const dx = toward.x - center.x
  const dy = toward.y - center.y
  if (dx === 0 && dy === 0) return center
  const tx = dx === 0 ? Infinity : box.w / 2 / Math.abs(dx)
  const ty = dy === 0 ? Infinity : box.h / 2 / Math.abs(dy)
  const t = Math.min(tx, ty)
  return { x: center.x + dx * t, y: center.y + dy * t }
}

function line(a: Box, b: Box, labelT = 0.58): { d: string; labelAt: Pt } {
  const ca = { x: cx(a), y: cy(a) }
  const cb = { x: cx(b), y: cy(b) }
  const s = rayExit(ca, cb, a)
  const e = rayExit(cb, ca, b)
  return { d: `M${f(s.x)},${f(s.y)} L${f(e.x)},${f(e.y)}`, labelAt: { x: s.x + (e.x - s.x) * labelT, y: s.y + (e.y - s.y) * labelT } }
}

function relax(box: Box, placed: Box[], dirX: number, dirY: number): Box {
  const len = Math.hypot(dirX, dirY) || 1
  let b = box
  for (let i = 0; i < 80 && placed.some((p) => overlaps(b, p, 8)); i++) b = { ...b, x: b.x + (dirX / len) * 12, y: b.y + (dirY / len) * 12 }
  return b
}

export function egoLayout(focusId: string, nodes: readonly NodeView[], edges: readonly EdgeView[], depth: 1 | 2 = 1): EgoLayout | null {
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const focus = byId.get(focusId)
  if (!focus) return null
  const fsize = sizeOf(focus)
  const focusBox: Box = { x: -fsize.w / 2, y: -fsize.h / 2, w: fsize.w, h: fsize.h }
  const positions = new Map<string, Box>([[focusId, focusBox]])
  const hop = new Map<string, 1 | 2>()
  const angle = new Map<string, number>()
  const laid = new Map<string, EgoEdge>()

  // depth-1 neighbours: edges incident to the focus
  const incident = edges.filter((e) => (e.src_id === focusId || e.dst_id === focusId) && e.src_id !== e.dst_id && byId.has(e.src_id) && byId.has(e.dst_id))
  const nbrIds: string[] = []
  const nbrRel = new Map<string, string>()
  for (const e of incident) {
    const o = e.src_id === focusId ? e.dst_id : e.src_id
    if (!nbrRel.has(o)) { nbrRel.set(o, e.rel); nbrIds.push(o) }
  }
  const groups = SECTOR_ORDER.map((k) =>
    nbrIds
      .map((id) => byId.get(id) as NodeView)
      .filter((n) => sectorOf(asKind(n.kind)) === k)
      .sort((a, b) => (nbrRel.get(a.id) as string).localeCompare(nbrRel.get(b.id) as string) || byLabel(a, b)),
  )
  const ordered = groups.flat()
  const n = ordered.length
  const rx = Math.max(300, 30 * n)
  const ry = Math.max(190, 18 * n)
  const slot = n ? 360 / n : 360
  const roleSpan = groups[0].length * slot
  const start = 180 - roleSpan / 2
  const placed: Box[] = [focusBox]
  ordered.forEach((node, i) => {
    const theta = start + (i + 0.5) * slot
    const rad = (theta * Math.PI) / 180
    const outer = n > 28 && i % 2 === 1 ? 90 : 0
    const px = (rx + outer) * Math.cos(rad)
    const py = (ry + outer) * Math.sin(rad)
    const s = sizeOf(node)
    const box = relax({ x: px - s.w / 2, y: py - s.h / 2, w: s.w, h: s.h }, placed, px, py)
    placed.push(box)
    positions.set(node.id, box)
    hop.set(node.id, 1)
    angle.set(node.id, ((theta % 360) + 360) % 360)
  })
  const clockwise = [...ordered].sort((a, b) => ((angle.get(a.id) as number) - 270 + 360) % 360 - ((angle.get(b.id) as number) - 270 + 360) % 360).map((x) => x.id)

  const addEdge = (e: EdgeView, parent: string, child: string, h: 1 | 2) => {
    const a = positions.get(parent)
    const b = positions.get(child)
    if (!a || !b) return
    const { d, labelAt } = line(a, b)
    const id = `e${e.id}`
    laid.set(id, { id, src: e.src_id, dst: e.dst_id, rel: e.rel, props: e.props ?? {}, d, labelAt, direction: e.src_id === parent ? 'out' : 'in', hop: h, other: child })
  }
  for (const e of incident) addEdge(e, focusId, e.src_id === focusId ? e.dst_id : e.src_id, 1)
  // several relations between the same pair: stagger their labels so they do not sit on each other
  {
    const seen = new Map<string, number>()
    for (const e of laid.values()) {
      const k = `${e.other}`
      const i = seen.get(k) ?? 0
      seen.set(k, i + 1)
      if (i) e.labelAt = { x: e.labelAt.x, y: e.labelAt.y + i * 18 }
    }
  }

  // sector captions for crowded views
  const sectors: EgoSector[] = []
  {
    const acc = new Map<string, { kind: KgKind; rel: string; direction: 'out' | 'in'; ids: string[] }>()
    for (const e of laid.values()) {
      if (e.hop !== 1) continue
      const kind = sectorOf(asKind((byId.get(e.other) as NodeView).kind))
      const key = `${kind}|${e.rel}|${e.direction}`
      const cur = acc.get(key) ?? { kind, rel: e.rel, direction: e.direction, ids: [] }
      cur.ids.push(e.other)
      acc.set(key, cur)
    }
    for (const [key, v] of [...acc].sort((a, b) => a[0].localeCompare(b[0]))) {
      const bs = v.ids.map((id) => positions.get(id) as Box)
      const mx = bs.reduce((s, b) => s + cx(b), 0) / bs.length
      const my = bs.reduce((s, b) => s + cy(b), 0) / bs.length
      const k = 0.62
      sectors.push({ key, kind: v.kind, rel: v.rel, direction: v.direction, count: v.ids.length, at: { x: mx * k, y: my * k } })
    }
  }

  // depth 2
  const more: EgoMore[] = []
  if (depth === 2) {
    const taken = new Set<string>([focusId, ...ordered.map((x) => x.id)])
    const cand: { e: EdgeView; parent: string; child: string }[] = []
    for (const parent of ordered) {
      for (const e of edges) {
        if (e.src_id !== parent.id && e.dst_id !== parent.id) continue
        const child = e.src_id === parent.id ? e.dst_id : e.src_id
        if (taken.has(child) || !byId.has(child)) continue
        taken.add(child)
        cand.push({ e, parent: parent.id, child })
      }
    }
    const shown = cand.slice(0, MAX_HOP2)
    const rest = cand.slice(MAX_HOP2)
    const perParent = new Map<string, number>()
    const seenIdx = new Map<string, number>()
    for (const c of shown) perParent.set(c.parent, (perParent.get(c.parent) ?? 0) + 1)
    const rx2 = rx + 220
    const ry2 = ry + 160
    for (const c of shown) {
      const m = perParent.get(c.parent) as number
      const j = seenIdx.get(c.parent) ?? 0
      seenIdx.set(c.parent, j + 1)
      const theta = (angle.get(c.parent) as number) + (j - (m - 1) / 2) * 7
      const rad = (theta * Math.PI) / 180
      const px = rx2 * Math.cos(rad)
      const py = ry2 * Math.sin(rad)
      const s = sizeOf(byId.get(c.child) as NodeView)
      const box = relax({ x: px - s.w / 2, y: py - s.h / 2, w: s.w, h: s.h }, placed, px, py)
      placed.push(box)
      positions.set(c.child, box)
      hop.set(c.child, 2)
      angle.set(c.child, ((theta % 360) + 360) % 360)
      addEdge(c.e, c.parent, c.child, 2)
    }
    const counts = new Map<KgKind, number>()
    for (const c of rest) {
      const k = sectorOf(asKind((byId.get(c.child) as NodeView).kind))
      counts.set(k, (counts.get(k) ?? 0) + 1)
    }
    for (const k of SECTOR_ORDER) {
      const count = counts.get(k)
      if (!count) continue
      const theta = k === 'role' ? 180 : k === 'concept' ? 270 : k === 'pattern' ? 315 : 45
      const rad = (theta * Math.PI) / 180
      const px = (rx2 + 120) * Math.cos(rad)
      const py = (ry2 + 80) * Math.sin(rad)
      const box = relax({ x: px - 50, y: py - 14, w: 100, h: 28 }, placed, px, py)
      placed.push(box)
      more.push({ kind: k, count, box })
    }
  }

  const bounds = unionBoxes([...positions.values(), ...more.map((m) => m.box)]) as Box
  return { focus: focusId, positions, edges: laid, hop, clockwise, angle, sectors, more, ring: { rx, ry }, bounds }
}

/** Node ids that touch `id` in the laid edges (for hover). */
export function adjacency(edges: Iterable<{ id: string; src: string; dst: string }>): Map<string, { nodes: Set<string>; edges: Set<string> }> {
  const m = new Map<string, { nodes: Set<string>; edges: Set<string> }>()
  const get = (id: string) => {
    let v = m.get(id)
    if (!v) { v = { nodes: new Set([id]), edges: new Set() }; m.set(id, v) }
    return v
  }
  for (const e of edges) {
    const a = get(e.src), b = get(e.dst)
    a.nodes.add(e.dst); b.nodes.add(e.src); a.edges.add(e.id); b.edges.add(e.id)
  }
  return m
}
