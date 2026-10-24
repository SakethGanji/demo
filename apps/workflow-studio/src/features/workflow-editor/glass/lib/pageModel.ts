/**
 * The Page view's structure: a workflow graph read as a document. Pure: nodes + connections in, sections of
 * blocks out. Walks from the trigger:
 *
 * - a node with two or more output ports in use is a **branch** (If yes/no, Switch rules, Approval outcomes, the
 *   `error` port); each case runs until the **join**, the first node every case can reach;
 * - a `Loop` is a **loop**: its body runs from the `loop` port and ends where it comes back; the page carries on
 *   from `done`. A loop over the items it is given (no fixed count) reads as **for each**, a fixed count as
 *   **repeat N times**. A `Poll` is a **repeat until**: its `loop` port is the re-check body, `done` carries on and
 *   `timeout` is an exit case under the frame;
 * - one port that fans out to several steps is **parallel** lanes, until their join;
 * - an edge back to a step already on the page is a **go back to step N** line (a retry loop).
 *
 * Steps before the first decision are **Intake**, each decision or loop opens a section named after it, the
 * steps after the last one are **Finish**, and an `ErrorTrigger` chain is **On failure**. Other triggers and steps
 * nothing reaches get their own sections, so every node appears exactly once.
 *
 * Numbers follow the document: top-level steps are 1, 2, 3 (the trigger is 1); steps inside a case or a loop are
 * N.1, N.2 (restarting in each case), one level further a, b, then i, ii; parallel lanes are Na, Nb.
 */
import { portLabel } from '../../lib/readView'
import type { WorkflowNodeData } from '../../types/workflow'
import { isTriggerNode } from './pageSentences'
import type { RunConn } from './runPath'

export interface PageNode {
  name: string
  type: string
  label?: string
  parameters: Record<string, unknown>
  disabled?: boolean
  notes?: string
  on_error?: string
  /** Engine retry policy: tries after the first failure, and the wait between them (ms). */
  retry_on_fail?: number
  retry_delay?: number
  continue_on_fail?: boolean
}

export interface PageWorkflow {
  id?: string
  name: string
  nodes: PageNode[]
  connections: RunConn[]
}

export type Block =
  | { t: 'step'; node: string }
  | { t: 'branch'; node: string; cases: Case[]; join?: string }
  | { t: 'loop'; node: string; body: Block[]; forEach: boolean; kind: LoopKind; exits: Case[] }
  | { t: 'parallel'; from: string; port: string; lanes: Block[][]; join?: string }
  | { t: 'goto'; target: string; from: string }

/** `each`: over the items it is given; `times`: a fixed count; `until`: a Poll, re-checked until its condition holds. */
export type LoopKind = 'each' | 'times' | 'until'

export interface Case {
  /** Fold key, `<branch node>:<port>`. */
  key: string
  port: string
  label: string
  blocks: Block[]
  /** The case has no steps of its own and goes straight to this node. */
  continues?: string
}

export type SectionKind = 'intake' | 'structure' | 'finish' | 'steps' | 'trigger' | 'failure' | 'unconnected'

export interface Section {
  id: string
  title: string
  kind: SectionKind
  blocks: Block[]
  /** Every node placed in the section, in page order. */
  nodes: string[]
  /** The decision or loop the section is named after. */
  head?: string
}

export interface OutlineItem {
  section: string
  title: string
  /** Absent on a section heading. On a parallel block: the first step of its first lane. */
  node?: string
  num?: string
  depth: number
  /** DOM id the item scrolls to. */
  dom?: string
}

export interface PageModel {
  /** The trigger the page opens with (its block is drawn above the sections). */
  trigger?: string
  sections: Section[]
  /** Step numbers (1, 2, 3.1, a, i, 3a…); `!` in On failure. Parallel blocks are keyed by `parKey`. */
  numbers: Record<string, string>
  /** Each node's top-level step number (the number of the outline item it sits under). */
  top: Record<string, string>
  /** Node names in page order. */
  order: string[]
  /** Top-level steps only (the rail's outline), plus a heading per extra section. */
  outline: OutlineItem[]
  /** The folds around a node (section id, then case keys from outside in): open them all to reveal it. */
  ancestors: Record<string, string[]>
  nodes: Record<string, PageNode>
}

const LOOP_TYPES = new Set(['Loop', 'SplitInBatches'])

/** Numbers and DOM ids of parallel blocks (they have no node of their own). */
export function parKey(b: { from: string; port: string }): string {
  return `par:${b.from}:${b.port}`
}
export function parDomId(b: { from: string; port: string }): string {
  return `gp-par-${slug(b.from)}-${slug(b.port)}`
}

/** Turns a backend-ish `{nodes, connections}` (an `ApiWorkflowDetail.definition`, a version, a draft) into a PageWorkflow. */
export function toPageWorkflow(src: {
  id?: string
  name?: string
  nodes: Array<{
    name: string
    type: string
    label?: string
    parameters?: Record<string, unknown>
    disabled?: boolean
    notes?: string
    on_error?: string
    retry_on_fail?: number
    retry_delay?: number
    continue_on_fail?: boolean
  }>
  connections: RunConn[]
}): PageWorkflow {
  return {
    id: src.id,
    name: src.name ?? 'Workflow',
    nodes: src.nodes.map((n) => ({
      name: n.name,
      type: n.type,
      label: n.label,
      parameters: n.parameters ?? {},
      disabled: n.disabled,
      notes: n.notes,
      on_error: n.on_error,
      retry_on_fail: n.retry_on_fail,
      retry_delay: n.retry_delay,
      continue_on_fail: n.continue_on_fail,
    })),
    connections: src.connections.map((c) => ({ source_node: c.source_node, source_output: c.source_output || 'main', target_node: c.target_node, target_input: c.target_input || 'main' })),
  }
}

export function caseLabel(port: string, node: PageNode): string {
  const l = portLabel(port, { name: node.name, type: node.type, parameters: node.parameters } as unknown as WorkflowNodeData)
  if (l) return l
  return node.type === 'If' ? 'yes' : 'then'
}

export function buildPageModel(wf: PageWorkflow): PageModel {
  const nodes: Record<string, PageNode> = {}
  for (const n of wf.nodes) nodes[n.name] = n
  const conns = wf.connections.filter((c) => nodes[c.source_node] && nodes[c.target_node])

  // outgoing: node → port → targets (ports in a stable order: definition order of first use)
  const out = new Map<string, Map<string, string[]>>()
  const incoming = new Map<string, number>()
  for (const c of conns) {
    const port = c.source_output || 'main'
    if (!out.has(c.source_node)) out.set(c.source_node, new Map())
    const ports = out.get(c.source_node)!
    if (!ports.has(port)) ports.set(port, [])
    if (!ports.get(port)!.includes(c.target_node)) ports.get(port)!.push(c.target_node)
    incoming.set(c.target_node, (incoming.get(c.target_node) ?? 0) + 1)
  }
  const portsOf = (n: string) => out.get(n) ?? new Map<string, string[]>()
  const succ = (n: string) => [...portsOf(n).values()].flat()

  const placed = new Set<string>()
  const order: string[] = []
  const place = (n: string) => {
    placed.add(n)
    order.push(n)
  }

  /** BFS distances from `start`, not entering `stop`. */
  const reach = (start: string, stop: Set<string>): Map<string, number> => {
    const d = new Map<string, number>([[start, 0]])
    const q = [start]
    while (q.length) {
      const n = q.shift()!
      if (stop.has(n) && n !== start) continue
      for (const s of succ(n)) {
        if (!d.has(s)) {
          d.set(s, d.get(n)! + 1)
          if (!stop.has(s)) q.push(s)
        }
      }
    }
    return d
  }

  /** The first node at least two of `starts` reach (the most of them, then the nearest); undefined when they never meet. */
  const findJoin = (starts: string[], stop: Set<string>): string | undefined => {
    if (starts.length < 2) return undefined
    const maps = starts.map((s) => reach(s, stop))
    let best: { n: string; count: number; sum: number } | undefined
    const seen = new Set<string>()
    for (const m of maps) {
      for (const n of m.keys()) {
        if (seen.has(n) || stop.has(n) || placed.has(n)) continue
        seen.add(n)
        const ds = maps.map((mm) => mm.get(n)).filter((x): x is number => x !== undefined)
        if (ds.length < 2) continue
        const cand = { n, count: ds.length, sum: ds.reduce((a, b) => a + b, 0) }
        if (!best || cand.count > best.count || (cand.count === best.count && cand.sum < best.sum)) best = cand
      }
    }
    return best?.n
  }

  const isLoop = (n: string) => (LOOP_TYPES.has(nodes[n]?.type) || nodes[n]?.type === 'Poll') && portsOf(n).has('loop')

  /** Steps from `targets` (one: a sequence; several: parallel lanes out of `from`/`port`). */
  const lanesOrSeq = (from: string, port: string, targets: string[], stop: Set<string>): { blocks: Block[]; next?: string } => {
    if (targets.length === 1) return { blocks: seq(targets[0], stop, from) }
    const join = findJoin(targets, stop)
    const inner = join ? new Set([...stop, join]) : stop
    const lanes = targets.map((t) => (t === join ? [] : seq(t, inner, from)))
    return { blocks: [{ t: 'parallel', from, port, lanes, join }], next: join }
  }

  function seq(start: string | undefined, stop: Set<string>, from = ''): Block[] {
    const blocks: Block[] = []
    let cur = start
    let prev = from
    while (cur && !stop.has(cur)) {
      if (placed.has(cur)) {
        blocks.push({ t: 'goto', target: cur, from: prev })
        break
      }
      place(cur)
      const ports = portsOf(cur)
      if (isLoop(cur)) {
        const node = cur
        const body = lanesOrSeq(node, 'loop', ports.get('loop')!, new Set([...stop, node]))
        const p = nodes[node].parameters
        const poll = nodes[node].type === 'Poll'
        const forEach = !poll && !(Number(p.maxIterations) > 0 || (typeof p.maxIterations === 'string' && p.maxIterations.includes('{{')))
        const done = ports.get('done') ?? []
        const exits: Case[] = []
        const timeout = poll ? (ports.get('timeout') ?? []) : []
        if (timeout.length) {
          // the timeout path, up to where it meets the done path (when it does)
          const join = done.length ? findJoin([done[0], ...timeout], stop) : undefined
          const inner = new Set([...stop, ...(join ? [join] : []), ...done.slice(0, 1)])
          const own = timeout.filter((t) => t !== join && t !== done[0])
          const r = own.length ? lanesOrSeq(node, 'timeout', own, inner) : { blocks: [] as Block[] }
          exits.push({ key: `${node}:timeout`, port: 'timeout', label: caseLabel('timeout', nodes[node]), blocks: r.blocks, continues: own.length < timeout.length ? (join ?? done[0]) : undefined })
        }
        blocks.push({ t: 'loop', node, body: body.blocks, forEach, kind: poll ? 'until' : forEach ? 'each' : 'times', exits })
        if (done.length > 1) {
          const r = lanesOrSeq(node, 'done', done, stop)
          blocks.push(...r.blocks)
          prev = node
          cur = r.next
        } else {
          prev = node
          cur = done[0]
        }
        continue
      }
      const used = [...ports.entries()].filter(([, t]) => t.length > 0)
      if (used.length >= 2) {
        const node = cur
        const starts = used.map(([, t]) => t[0])
        const join = findJoin([...new Set(used.flatMap(([, t]) => t))], stop) ?? findJoin(starts, stop)
        const inner = join ? new Set([...stop, join]) : stop
        const cases: Case[] = used.map(([port, targets]) => {
          const direct = targets.length === 1 && targets[0] === join
          const r = direct ? { blocks: [] as Block[] } : lanesOrSeq(node, port, targets.filter((t) => t !== join), inner)
          return { key: `${node}:${port}`, port, label: caseLabel(port, nodes[node]), blocks: r.blocks, continues: direct ? join : undefined }
        })
        blocks.push({ t: 'branch', node, cases, join })
        prev = node
        cur = join
        continue
      }
      blocks.push({ t: 'step', node: cur })
      if (used.length === 1) {
        const [port, targets] = used[0]
        if (targets.length === 1) {
          prev = cur
          cur = targets[0]
        } else {
          const r = lanesOrSeq(cur, port, targets, stop)
          blocks.push(...r.blocks)
          prev = cur
          cur = r.next
        }
      } else cur = undefined
    }
    return blocks
  }

  // ── the trigger, then the rest ──
  const roots = wf.nodes.filter((n) => !incoming.get(n.name)).map((n) => n.name)
  const errorRoots = roots.filter((r) => nodes[r].type === 'ErrorTrigger')
  const trigRoots = roots.filter((r) => nodes[r].type !== 'ErrorTrigger' && isTriggerNode(nodes[r].type))
  const trigger = trigRoots[0] ?? roots.find((r) => nodes[r].type !== 'ErrorTrigger')

  const sections: Section[] = []
  if (trigger) {
    place(trigger)
    const ports = portsOf(trigger)
    const targets = [...ports.values()].flat()
    const main = targets.length > 1 ? lanesOrSeq(trigger, [...ports.keys()][0], targets, new Set()) : { blocks: seq(targets[0], new Set(), trigger) }
    const tail = main.next ? seq(main.next, new Set()) : []
    sections.push(...chunk([...main.blocks, ...tail], nodes))
  }
  // other triggers (a Cron next to a Webhook, a form next to a chat)
  // (a non-trigger step nothing feeds is not a start: it lands in "Not connected yet")
  for (const r of trigRoots.filter((x) => x !== trigger)) {
    if (placed.has(r)) continue
    const blocks = seq(r, new Set())
    sections.push({ id: `sec-also-${slug(r)}`, title: `Also starts: ${nodes[r].label || r}`, kind: 'trigger', blocks, nodes: [], head: r })
  }
  for (const r of errorRoots) {
    if (placed.has(r)) continue
    const blocks = seq(r, new Set())
    sections.push({ id: errorRoots.length > 1 ? `sec-fail-${slug(r)}` : 'sec-fail', title: 'On failure', kind: 'failure', blocks, nodes: [], head: r })
  }
  // steps nothing reaches (a half-built branch, a node dropped on the canvas)
  const loose: Block[] = []
  for (const n of wf.nodes) if (!placed.has(n.name)) loose.push(...seq(n.name, new Set()))
  if (loose.length) sections.push({ id: 'sec-unconnected', title: 'Not connected yet', kind: 'unconnected', blocks: loose, nodes: [] })

  // ── numbers, node lists, outline, ancestors ──
  const numbers: Record<string, string> = {}
  const top: Record<string, string> = {}
  const ancestors: Record<string, string[]> = {}
  const outline: OutlineItem[] = []
  let k = 0
  if (trigger) {
    numbers[trigger] = String(++k)
    top[trigger] = numbers[trigger]
    ancestors[trigger] = []
    outline.push({ section: 'trigger', title: nodes[trigger].label || trigger, node: trigger, num: numbers[trigger], depth: 0, dom: stepDomId(trigger) })
  }
  for (const s of sections) {
    const main = s.kind === 'intake' || s.kind === 'structure' || s.kind === 'finish' || s.kind === 'steps'
    if (!main) outline.push({ section: s.id, title: s.title, depth: 0 })
    const fail = s.kind === 'failure'
    let curTop = ''
    /** Gives `b` the number `label`, then numbers what it holds one level down. */
    const assign = (b: Exclude<Block, { t: 'goto' }>, label: string, depth: number, folds: string[]) => {
      if (depth === 0) curTop = label
      if (b.t === 'parallel') {
        numbers[parKey(b)] = label
        const first = b.lanes.flat().find((x) => x.t !== 'goto' && x.t !== 'parallel') as { node: string } | undefined
        if (depth === 0) outline.push({ section: s.id, title: 'At the same time', node: first?.node, num: label, depth: 0, dom: parDomId(b) })
        b.lanes.forEach((lane, j) => {
          let idx = 0
          for (const x of lane) if (x.t !== 'goto') assign(x, fail ? '!' : `${label}${letter(j)}${idx++ ? `.${idx}` : ''}`, depth + 0.5, folds)
        })
        return
      }
      const n = b.node
      numbers[n] = label
      top[n] = curTop
      s.nodes.push(n)
      ancestors[n] = [s.id, ...folds]
      if (depth === 0) outline.push({ section: s.id, title: nodes[n].label || n, node: n, num: label, depth: 0, dom: stepDomId(n) })
      const d = Math.floor(depth) + 1
      if (b.t === 'branch') b.cases.forEach((c) => list(c.blocks, d, [...folds, c.key], label))
      if (b.t === 'loop') {
        const inner = list(b.body, d, [...folds, `${n}:loop`], label)
        b.exits.forEach((c) => list(c.blocks, d, [...folds, c.key], label, inner))
      }
    }
    /** Numbers a list of blocks at `depth` (0 top level, 1 → N.1, 2 → a, 3+ → i) under `parent`; returns the count used. */
    const list = (bs: Block[], depth: number, folds: string[], parent: string, from = 0): number => {
      let i = from
      for (const b of bs) {
        if (b.t === 'goto') continue
        const label = fail ? '!' : depth === 0 ? String(++k) : childNum(parent, depth, i)
        i++
        assign(b, label, depth, folds)
      }
      return i
    }
    list(s.blocks, 0, [], '')
  }
  return { trigger, sections, numbers, top, order, outline, ancestors, nodes }
}

function letter(i: number): string {
  return i < 26 ? 'abcdefghijklmnopqrstuvwxyz'[i] : `z${i - 25}`
}
const ROMAN = ['i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x', 'xi', 'xii']
function childNum(parent: string, depth: number, i: number): string {
  if (depth === 1) return `${parent}.${i + 1}`
  if (depth === 2) return letter(i)
  return ROMAN[i] ?? String(i + 1)
}

/** Splits the trigger's chain into Intake / one section per decision or loop / Finish. */
function chunk(blocks: Block[], nodes: Record<string, PageNode>): Section[] {
  const structural = (b: Block) => b.t === 'branch' || b.t === 'loop' || b.t === 'parallel'
  if (!blocks.some(structural)) return blocks.length ? [{ id: 'sec-steps', title: 'Steps', kind: 'steps', blocks, nodes: [] }] : []
  const runs: Block[][] = [[]]
  const structs: Block[] = []
  for (const b of blocks) {
    if (structural(b)) {
      structs.push(b)
      runs.push([])
    } else runs[runs.length - 1].push(b)
  }
  const out: Section[] = []
  if (runs[0].length) out.push({ id: 'sec-intake', title: 'Intake', kind: 'intake', blocks: runs[0], nodes: [] })
  const used = new Set<string>()
  structs.forEach((b, i) => {
    const last = i === structs.length - 1
    const head = b.t === 'parallel' || b.t === 'goto' ? undefined : b.node
    const title = head ? nodes[head].label || head : 'At the same time'
    let id = `sec-${slug(head ?? `parallel-${i + 1}`)}`
    while (used.has(id)) id += '-2'
    used.add(id)
    out.push({ id, title, kind: 'structure', blocks: last ? [b] : [b, ...runs[i + 1]], nodes: [], head })
  })
  const tail = runs[runs.length - 1]
  if (tail.length) out.push({ id: 'sec-finish', title: 'Finish', kind: 'finish', blocks: tail, nodes: [] })
  return out
}

export function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'x'
}

/** DOM id of a step on the page. */
export function stepDomId(node: string): string {
  return `gp-step-${slug(node)}`
}
