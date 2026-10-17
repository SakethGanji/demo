/**
 * "Tidy up": left-to-right layout by longest-path depth from the roots, siblings spaced vertically and centred.
 * Pure and deterministic (ties broken by input order). Cycles and disconnected nodes are handled.
 */
export interface LayoutNode {
  id: string
  width?: number
  height?: number
}
export interface LayoutEdge {
  source: string
  target: string
}

const DEFAULT_W = 220
const DEFAULT_H = 80
const GAP_X = 80
const GAP_Y = 40

export function autoLayout(nodes: LayoutNode[], edges: LayoutEdge[]): Record<string, { x: number; y: number }> {
  const ids = new Set(nodes.map((n) => n.id))
  const out = new Map<string, string[]>()
  const indeg = new Map<string, number>()
  for (const n of nodes) {
    out.set(n.id, [])
    indeg.set(n.id, 0)
  }
  const seen = new Set<string>()
  for (const e of edges) {
    const key = `${e.source}\u0000${e.target}`
    if (!ids.has(e.source) || !ids.has(e.target) || e.source === e.target || seen.has(key)) continue
    seen.add(key)
    out.get(e.source)!.push(e.target)
    indeg.set(e.target, indeg.get(e.target)! + 1)
  }

  // Longest-path depth via Kahn. Nodes left over are in (or behind) a cycle: release the one earliest in input
  // order at the depth reached so far and keep going, so every node gets placed.
  const depth = new Map<string, number>()
  const remaining = new Map(indeg)
  const done = new Set<string>()
  const queue: string[] = []
  const settle = (id: string) => {
    done.add(id)
    for (const t of out.get(id)!) {
      depth.set(t, Math.max(depth.get(t) ?? 0, depth.get(id)! + 1))
      remaining.set(t, remaining.get(t)! - 1)
      if (remaining.get(t) === 0 && !done.has(t)) queue.push(t)
    }
  }
  for (const n of nodes) if (indeg.get(n.id) === 0) { depth.set(n.id, 0); queue.push(n.id) }
  for (;;) {
    while (queue.length) settle(queue.shift()!)
    const stuck = nodes.find((n) => !done.has(n.id))
    if (!stuck) break
    if (!depth.has(stuck.id)) depth.set(stuck.id, 0)
    queue.push(stuck.id)
  }

  // Columns in input order, each column stacked and centred on y = 0.
  const cols = new Map<number, LayoutNode[]>()
  for (const n of nodes) {
    const d = depth.get(n.id) ?? 0
    if (!cols.has(d)) cols.set(d, [])
    cols.get(d)!.push(n)
  }
  const widthOf = (n: LayoutNode) => n.width ?? DEFAULT_W
  const heightOf = (n: LayoutNode) => n.height ?? DEFAULT_H
  const result: Record<string, { x: number; y: number }> = {}
  let x = 0
  for (const d of [...cols.keys()].sort((a, b) => a - b)) {
    const col = cols.get(d)!
    const total = col.reduce((s, n) => s + heightOf(n), 0) + GAP_Y * (col.length - 1)
    let y = -total / 2
    for (const n of col) {
      result[n.id] = { x, y }
      y += heightOf(n) + GAP_Y
    }
    x += Math.max(...col.map(widthOf)) + GAP_X
  }
  return result
}
