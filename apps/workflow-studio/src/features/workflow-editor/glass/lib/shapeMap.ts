/**
 * The rail's shape map: the Page document's main chain drawn as a small read-only tree, laid out automatically.
 * Pure: a PageModel (and the picked run) in, SVG primitives out, in a 200-wide viewBox.
 *
 * Steps are pills, choices diamonds, Stop a square, a loop a frame around its body, parallel lanes and the cases
 * of a choice sit side by side (each gets width by how wide its own contents are), and a dot marks where paths
 * meet. Everything the run went through is `on` (drawn green).
 */
import { failRule } from './pageDoc'
import { parDomId, stepDomId, type Block, type PageModel } from './pageModel'
import type { RunPath } from './runPath'

export interface MapNode {
  kind: 'pill' | 'diamond' | 'stop' | 'dot'
  /** The step it stands for (scroll target); a join dot has the join node, when there is one. */
  node?: string
  dom?: string
  x: number
  y: number
  w: number
  h: number
  title: string
  on: boolean
  failpip?: boolean
}
export interface MapEdge {
  d: string
  on: boolean
  dashed?: boolean
}
export interface MapLabel {
  x: number
  y: number
  w: number
  text: string
  on: boolean
  /** The fold key of the case it labels. */
  caseKey?: string
}
export interface MapFrame {
  x: number
  y: number
  w: number
  h: number
  text: string
  textX: number
  textY: number
  /** The loop it frames (click target). */
  node: string
  dom: string
  on: boolean
}
export interface ShapeMap {
  width: number
  height: number
  nodes: MapNode[]
  edges: MapEdge[]
  labels: MapLabel[]
  frames: MapFrame[]
}

interface Pt {
  x: number
  y: number
  on: boolean
}

const W = 200
const PAD = 8
const GAP = 20
const PILL_H = 12
const DIA = 14

const ranOn = (path: RunPath | null, n: string | undefined) => {
  if (!path || !n) return false
  const s = path.byNode[n]?.status
  return s === 'ok' || s === 'need' || s === 'fail'
}

function weight(bs: Block[]): number {
  let w = 1
  for (const b of bs) {
    if (b.t === 'branch') w = Math.max(w, b.cases.reduce((a, c) => a + weight(c.blocks), 0))
    else if (b.t === 'parallel') w = Math.max(w, b.lanes.reduce((a, l) => a + weight(l), 0))
    else if (b.t === 'loop') w = Math.max(w, weight(b.body), ...b.exits.map((c) => weight(c.blocks)))
  }
  return w
}

const curve = (a: Pt, x: number, y: number) => (Math.abs(a.x - x) < 0.5 ? `M${r(a.x)} ${r(a.y)} V${r(y)}` : `M${r(a.x)} ${r(a.y)} C${r(a.x)} ${r((a.y + y) / 2 + 4)} ${r(x)} ${r((a.y + y) / 2 - 4)} ${r(x)} ${r(y)}`)
const r = (v: number) => Math.round(v * 10) / 10

function shortLabel(s: string): string {
  const t = s.toUpperCase()
  return t.length > 9 ? `${t.slice(0, 8)}…` : t
}

export function buildShapeMap(model: PageModel, path: RunPath | null): ShapeMap {
  const nodes: MapNode[] = []
  const edges: MapEdge[] = []
  const labels: MapLabel[] = []
  const frames: MapFrame[] = []
  const title = (n: string) => `${model.numbers[n] ?? ''} · ${model.nodes[n]?.label || n}`.replace(/^ · /, '')

  const connect = (inc: Pt[], x: number, y: number, on: boolean) => {
    for (const p of inc) edges.push({ d: curve(p, x, y), on: p.on && on })
  }

  const pill = (n: string, x0: number, x1: number, y: number, inc: Pt[]): { y: number; out: Pt[] } => {
    const cx = (x0 + x1) / 2
    const on = ranOn(path, n)
    const stop = model.nodes[n]?.type === 'StopAndError'
    const w = stop ? 12 : Math.max(10, Math.min(28, x1 - x0 - 6))
    const h = stop ? 12 : PILL_H
    connect(inc, cx, y, on)
    nodes.push({ kind: stop ? 'stop' : 'pill', node: n, dom: stepDomId(n), x: r(cx - w / 2), y: r(y), w: r(w), h, title: title(n), on, failpip: !!model.nodes[n] && !!failRule(model.nodes[n]) })
    // a Stop ends its path: nothing continues from it
    return { y: y + h + GAP, out: stop ? [] : [{ x: cx, y: y + h, on }] }
  }

  const list = (bs: Block[], x0: number, x1: number, y: number, inc: Pt[]): { y: number; out: Pt[] } => {
    let cur = { y, out: inc }
    for (const b of bs) cur = block(b, x0, x1, cur.y, cur.out)
    return cur
  }

  /** Side-by-side columns (cases or lanes), each `lay` in its share of [x0, x1]; returns every column's end. */
  const columns = (groups: Block[][], x0: number, x1: number, y: number, lay: (i: number, a: number, b: number, y: number) => { y: number; out: Pt[] }) => {
    const ws = groups.map(weight)
    const total = ws.reduce((a, b) => a + b, 0) || 1
    let a = x0
    let bottom = y
    const outs: Pt[] = []
    groups.forEach((_, i) => {
      const b = a + ((x1 - x0) * ws[i]) / total
      const res = lay(i, a, b, y)
      bottom = Math.max(bottom, res.y)
      outs.push(...res.out)
      a = b
    })
    return { y: bottom, out: outs }
  }

  const meet = (outs: Pt[], cx: number, y: number, join: string | undefined, dom: string | undefined): { y: number; out: Pt[] } => {
    const on = join ? ranOn(path, join) : outs.some((o) => o.on)
    if (!outs.length) return { y, out: [] }
    const dy = y - GAP + 10
    connect(outs, cx, dy - 4, on)
    nodes.push({ kind: 'dot', node: join, dom, x: r(cx - 4), y: r(dy - 4), w: 8, h: 8, title: 'Paths meet', on })
    return { y: dy + 4 + GAP, out: [{ x: cx, y: dy + 4, on }] }
  }

  function block(b: Block, x0: number, x1: number, y: number, inc: Pt[]): { y: number; out: Pt[] } {
    const cx = (x0 + x1) / 2
    if (b.t === 'goto') {
      const on = ranOn(path, b.target)
      connect(inc, cx, y, on)
      labels.push({ x: r(cx - 14), y: r(y), w: 28, text: `↩ ${model.numbers[b.target] ?? ''}`, on })
      return { y: y + 11 + GAP, out: [] }
    }
    if (b.t === 'step') return pill(b.node, x0, x1, y, inc)
    if (b.t === 'branch') {
      const on = ranOn(path, b.node)
      connect(inc, cx, y, on)
      nodes.push({ kind: 'diamond', node: b.node, dom: stepDomId(b.node), x: r(cx - DIA / 2), y: r(y), w: DIA, h: DIA, title: title(b.node), on })
      const top = y + DIA
      const ly = top + 14
      const res = columns(
        b.cases.map((c) => c.blocks),
        x0,
        x1,
        ly,
        (i, a, bb, yy) => {
          const c = b.cases[i]
          const ccx = (a + bb) / 2
          const taken = !!path && (path.byNode[b.node]?.ports ?? []).includes(c.port) && on
          edges.push({ d: curve({ x: cx, y: top, on }, ccx, yy), on: taken })
          const lw = Math.min(bb - a - 2, 34)
          labels.push({ x: r(ccx - lw / 2), y: r(yy), w: r(lw), text: shortLabel(c.label), on: taken, caseKey: c.key })
          const start = yy + 11
          if (!c.blocks.length) return { y: start + 6, out: [{ x: ccx, y: start, on: taken }] }
          return list(c.blocks, a, bb, start + 12, [{ x: ccx, y: start, on: taken }])
        },
      )
      return b.join ? meet(res.out, cx, res.y, b.join, stepDomId(b.join)) : res
    }
    if (b.t === 'parallel') {
      const res = columns(b.lanes, x0, x1, y, (i, a, bb, yy) => list(b.lanes[i], a, bb, yy, inc.map((p) => ({ ...p }))))
      return meet(res.out, cx, res.y, b.join, parDomId(b))
    }
    // loop: a frame around its body (and the exit cases under it)
    const on = ranOn(path, b.node)
    const fy = y
    const inset = Math.min(8, (x1 - x0) / 8)
    connect(inc, cx, fy, on)
    const head = fy + 18
    const body = list(b.body, x0 + inset, x1 - inset, head, [{ x: cx, y: fy, on }])
    let end = body.y
    for (const c of b.exits) {
      const lw = 40
      labels.push({ x: r(cx - lw / 2), y: r(end - GAP + 6), w: lw, text: shortLabel(c.label), on: !!path && (path.byNode[b.node]?.ports ?? []).includes(c.port), caseKey: c.key })
      end = list(c.blocks, x0 + inset, x1 - inset, end + 4, []).y
    }
    const fh = end - GAP + 8 - fy
    const kind = b.kind === 'until' ? 'UNTIL' : b.kind === 'times' ? 'REPEAT' : 'FOR EACH'
    frames.push({ x: r(x0 + 1), y: r(fy - 4), w: r(x1 - x0 - 2), h: r(fh + 4), text: kind, textX: r(x0 + 8), textY: r(fy + 8), node: b.node, dom: stepDomId(b.node), on })
    return { y: fy + fh + GAP, out: [{ x: cx, y: fy + fh, on }] }
  }

  let cur: { y: number; out: Pt[] } = { y: PAD + 4, out: [] }
  if (model.trigger) cur = pill(model.trigger, PAD, W - PAD, cur.y, [])
  for (const s of model.sections) {
    if (s.kind === 'trigger' || s.kind === 'failure' || s.kind === 'unconnected') continue
    cur = list(s.blocks, PAD, W - PAD, cur.y, cur.out)
  }
  return { width: W, height: Math.max(60, Math.ceil(cur.y - GAP + PAD + 6)), nodes, edges, labels, frames }
}
