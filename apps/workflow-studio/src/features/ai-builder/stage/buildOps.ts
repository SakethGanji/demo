/**
 * The build vocabulary (PLAN-MOTION-UI §4.1): what the canvas does while the engine builds, as plain data.
 * Everything here is pure; the player (`useBuildPlayer`) turns ops into store writes and time.
 */
import type { ProposedDefinition } from '../types'

export interface XY { x: number; y: number }
/** `edge-${src}-${out}-${tgt}-${in}`, sanitised exactly like `fromBackendWorkflow` ids. */
export type EdgeKey = string
export type TestStatus = 'passed' | 'failed' | 'errored'

/** `data.anim` values a node or edge can carry on the canvas. */
export type AnimState = 'enter' | 'lock' | 'exit' | 'change' | 'fail' | 'idle'

export type BuildOp =
  | { kind: 'roundStart'; round: number }
  | { kind: 'addNode'; node: string; type: string; position: XY }
  /** `from` is the position before the move, so `reverse` is exact */
  | { kind: 'moveNode'; node: string; to: XY; from: XY }
  | { kind: 'changeNode'; node: string; fields: string[] }
  /** `type` and `position` are what the node was, so `reverse` can put it back */
  | { kind: 'removeNode'; node: string; type: string; position: XY }
  | { kind: 'addEdge'; key: EdgeKey; src: string; out: string; tgt: string; inp: string }
  | { kind: 'removeEdge'; key: EdgeKey; src: string; out: string; tgt: string; inp: string }
  | { kind: 'fail'; nodes: string[] }
  | { kind: 'testRun'; testId: string; path: string[]; status: TestStatus; failedNodes: string[] }
  | { kind: 'roundEnd'; round: number }

/** Connection as the engine sends it in `round_shape`: [source, output, target, input]. */
export type ShapeConnection = [string, string, string, string]

/** Payload of the engine's `round_shape` event. It never carries parameter values. */
export interface RoundShape {
  round: number
  nodes: { name: string; type: string; position: XY }[]
  connections: ShapeConnection[]
  changed: { name: string; fields: string[] }[]
  removed: string[]
}

/** One entry of `tested.results` (engine event payload). */
export interface TestResult {
  test_id: string
  name: string
  status: TestStatus
  /** node names that ran, in run order, trigger first */
  path: string[]
  failed_nodes: string[]
}

/** What the graph looked like before a round: the base for the diff a round's ops describe. */
export interface ShapeSnapshot {
  nodes: { name: string; type: string; position: XY }[]
  connections: ShapeConnection[]
}

export const EMPTY_SNAPSHOT: ShapeSnapshot = { nodes: [], connections: [] }

const sanitize = (s: string) => s.replace(/[^a-zA-Z0-9_-]/g, '_')

export function edgeKey(src: string, out: string, tgt: string, inp: string): EdgeKey {
  return `edge-${sanitize(src)}-${sanitize(out)}-${sanitize(tgt)}-${sanitize(inp)}`
}

export function snapshotFromShape(shape: RoundShape): ShapeSnapshot {
  return { nodes: shape.nodes.map((n) => ({ ...n, position: { ...n.position } })), connections: shape.connections.map((c) => [...c] as ShapeConnection) }
}

/** A snapshot of a stored definition (the base draft, or the server's proposed one). */
export function snapshotFromDefinition(def: Pick<ProposedDefinition, 'nodes' | 'connections'> | null | undefined): ShapeSnapshot {
  if (!def) return EMPTY_SNAPSHOT
  return {
    nodes: (def.nodes ?? []).map((n) => ({ name: n.name, type: n.type, position: { x: n.position?.x ?? 0, y: n.position?.y ?? 0 } })),
    connections: (def.connections ?? []).map((c) => [
      String(c.source_node), String(c.source_output ?? 'main'), String(c.target_node), String(c.target_input ?? 'main'),
    ] as ShapeConnection),
  }
}

const samePos = (a: XY, b: XY) => Math.abs(a.x - b.x) < 0.5 && Math.abs(a.y - b.y) < 0.5
const byLayer = <T extends { position: XY }>(a: T, b: T) => a.position.x - b.position.x || a.position.y - b.position.y

function edgeOp(kind: 'addEdge' | 'removeEdge', c: ShapeConnection): BuildOp {
  const [src, out, tgt, inp] = c
  return { kind, key: edgeKey(src, out, tgt, inp), src, out, tgt, inp }
}

/**
 * The ops that turn `prev` into the round described by `shape`. Order: removes, moves, adds by layer (x, then y),
 * changes, edge removes, edge adds (every endpoint exists by then). Wrapped in `roundStart` / `roundEnd`.
 */
export function opsFromRoundShape(prev: ShapeSnapshot | null, shape: RoundShape): BuildOp[] {
  const before = prev ?? EMPTY_SNAPSHOT
  const had = new Map(before.nodes.map((n) => [n.name, n]))
  const has = new Set(shape.nodes.map((n) => n.name))
  const ops: BuildOp[] = [{ kind: 'roundStart', round: shape.round }]

  for (const n of before.nodes) {
    if (!has.has(n.name)) ops.push({ kind: 'removeNode', node: n.name, type: n.type, position: n.position })
  }
  for (const n of shape.nodes) {
    const old = had.get(n.name)
    if (old && !samePos(old.position, n.position)) ops.push({ kind: 'moveNode', node: n.name, to: n.position, from: old.position })
  }
  for (const n of shape.nodes.filter((x) => !had.has(x.name)).sort(byLayer)) {
    ops.push({ kind: 'addNode', node: n.name, type: n.type, position: n.position })
  }
  for (const c of shape.changed) {
    if (has.has(c.name) && had.has(c.name)) ops.push({ kind: 'changeNode', node: c.name, fields: [...c.fields] })
  }

  const key = (c: ShapeConnection) => edgeKey(...c)
  const prevKeys = new Set(before.connections.map(key))
  const nextKeys = new Set(shape.connections.map(key))
  for (const c of before.connections) if (!nextKeys.has(key(c))) ops.push(edgeOp('removeEdge', c))
  const x = new Map(shape.nodes.map((n) => [n.name, n.position.x]))
  const added = shape.connections.filter((c) => !prevKeys.has(key(c)))
    .sort((a, b) => (x.get(a[2]) ?? 0) - (x.get(b[2]) ?? 0) || (x.get(a[0]) ?? 0) - (x.get(b[0]) ?? 0))
  for (const c of added) ops.push(edgeOp('addEdge', c))
  ops.push({ kind: 'roundEnd', round: shape.round })
  return ops
}

/** `testRun` ops in result order; a failing test is followed by one `fail` for the nodes not yet flagged. */
export function opsFromTested(results: TestResult[]): BuildOp[] {
  const ops: BuildOp[] = []
  const flagged = new Set<string>()
  for (const r of results) {
    ops.push({ kind: 'testRun', testId: r.test_id, path: [...r.path], status: r.status, failedNodes: [...r.failed_nodes] })
    const fresh = r.failed_nodes.filter((n) => !flagged.has(n))
    if (r.status !== 'passed' && fresh.length) {
      fresh.forEach((n) => flagged.add(n))
      ops.push({ kind: 'fail', nodes: fresh })
    }
  }
  return ops
}

/** Fallback for a proposal that has no `round_shape` events: the whole change as one round. */
export function opsFromDiff(base: ProposedDefinition | null, proposed: ProposedDefinition, changed: { name: string; fields: string[] }[] = []): BuildOp[] {
  const next = snapshotFromDefinition(proposed)
  return opsFromRoundShape(snapshotFromDefinition(base), { round: 0, nodes: next.nodes, connections: next.connections, changed, removed: [] })
}

/**
 * The inverse list: run it to undo the ops. `reverse(reverse(x))` equals `x`. Test pulses and failures have no
 * inverse, so they stay as they are (the player skips them when it plays backwards).
 */
export function reverse(ops: BuildOp[]): BuildOp[] {
  return [...ops].reverse().map((op): BuildOp => {
    switch (op.kind) {
      case 'roundStart': return { kind: 'roundEnd', round: op.round }
      case 'roundEnd': return { kind: 'roundStart', round: op.round }
      case 'addNode': return { kind: 'removeNode', node: op.node, type: op.type, position: op.position }
      case 'removeNode': return { kind: 'addNode', node: op.node, type: op.type, position: op.position }
      case 'moveNode': return { ...op, to: op.from, from: op.to }
      case 'addEdge': return { ...op, kind: 'removeEdge' }
      case 'removeEdge': return { ...op, kind: 'addEdge' }
      default: return op
    }
  })
}
