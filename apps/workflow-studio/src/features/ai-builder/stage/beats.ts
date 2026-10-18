/**
 * Beats (PLAN-MOTION-V2 §5.1): a round's ops regrouped into "one node at a time" beats, and the pure schedule that
 * says when each beat starts. No store, no timers: `useBuildPlayer` turns this into writes and time.
 */
import { beat, dur, roundCap } from '@/shared/motion/tokens'
import type { BuildOp, XY } from './buildOps'

type AddEdge = Extract<BuildOp, { kind: 'addEdge' }>

export type Beat =
  | { kind: 'retract'; edges: Extract<BuildOp, { kind: 'removeEdge' }>[] }
  | { kind: 'deconstruct'; node: string; type: string; position: XY }
  | { kind: 'move'; moves: Extract<BuildOp, { kind: 'moveNode' }>[] }
  | { kind: 'morph'; node: string; fields: string[] }
  | { kind: 'construct'; node: string; type: string; position: XY; edges: AddEdge[] }
  | { kind: 'link'; edges: AddEdge[] }
  | { kind: 'test'; run: Extract<BuildOp, { kind: 'testRun' }>; failAt: string | null }

/** Order: retract, deconstruct, move, morph, construct (op order = by layer), link, tests. */
export function beatsFromOps(ops: BuildOp[]): Beat[] {
  const retract = ops.filter((o): o is Extract<BuildOp, { kind: 'removeEdge' }> => o.kind === 'removeEdge')
  const moves = ops.filter((o): o is Extract<BuildOp, { kind: 'moveNode' }> => o.kind === 'moveNode')
  const adds = ops.filter((o): o is Extract<BuildOp, { kind: 'addNode' }> => o.kind === 'addNode')
  const order = new Map(adds.map((o, i) => [o.node, i]))
  const construct = adds.map((o) => ({ kind: 'construct' as const, node: o.node, type: o.type, position: o.position, edges: [] as AddEdge[] }))
  const link: AddEdge[] = []
  for (const e of ops) {
    if (e.kind !== 'addEdge') continue
    const a = order.get(e.src)
    const b = order.get(e.tgt)
    if (a === undefined && b === undefined) link.push(e)
    else construct[a === undefined ? b! : b === undefined ? a : Math.max(a, b)].edges.push(e)
  }

  const out: Beat[] = []
  if (retract.length) out.push({ kind: 'retract', edges: retract })
  for (const o of ops) if (o.kind === 'removeNode') out.push({ kind: 'deconstruct', node: o.node, type: o.type, position: o.position })
  if (moves.length) out.push({ kind: 'move', moves })
  for (const o of ops) if (o.kind === 'changeNode') out.push({ kind: 'morph', node: o.node, fields: o.fields })
  out.push(...construct)
  if (link.length) out.push({ kind: 'link', edges: link })
  for (const o of ops) {
    if (o.kind !== 'testRun') continue
    out.push({ kind: 'test', run: o, failAt: o.path.find((n) => o.failedNodes.includes(n)) ?? null })
  }
  return out
}

export interface ScheduledBeat {
  beat: Beat
  /** seconds from the segment start to the beat body (the orb's lead began `beat.lead * u` earlier) */
  at: number
  /** test beats: seconds per probe hop (already divided by speed) */
  hop?: number
  /** test beats: false = no probe, the badges just tick */
  probe?: boolean
}

export interface BeatSchedule { items: ScheduledBeat[]; total: number; k: number; compact: boolean }

const linkTime = (m: number) => beat.edgeDraw + beat.linkGap * Math.max(0, m - 1)

function periodOf(b: Beat): number {
  switch (b.kind) {
    case 'construct': return beat.period
    case 'deconstruct': return beat.exitPeriod
    case 'morph': return beat.morphPeriod
    case 'move': return dur.move
    case 'retract': return beat.retract
    case 'link': return linkTime(b.edges.length)
    case 'test': return 0
  }
}

/** Start times (seconds) for a segment's beats; every value divided by `speed` (catch-up). */
export function scheduleBeats(beats: Beat[], opts: { speed?: number } = {}): BeatSchedule {
  const speed = opts.speed ?? 1
  const shape = beats.filter((b) => b.kind !== 'test')
  const tests = beats.filter((b): b is Extract<Beat, { kind: 'test' }> => b.kind === 'test')
  const items: ScheduledBeat[] = []
  let k = 1
  let compact = false
  let cursor = 0

  if (shape.length) {
    const n = shape.filter((b) => b.kind === 'construct' || b.kind === 'morph' || b.kind === 'deconstruct').length
    const periodic = shape.reduce((t, b) => t + (b.kind === 'construct' || b.kind === 'morph' || b.kind === 'deconstruct' ? periodOf(b) : 0), 0)
    const fixed = beat.roundLead + beat.tail + shape.reduce((t, b) => t + (b.kind === 'construct' || b.kind === 'morph' || b.kind === 'deconstruct' ? 0 : periodOf(b)), 0)
    if (periodic > 0) k = Math.min(1, (roundCap(n) - fixed) / periodic)
    k = Math.max(beat.minK, k)                       // the period never drops below ~220 ms: the cap stretches instead
    compact = k < beat.compactBelow
    cursor = beat.roundLead * k
    for (const b of shape) {
      items.push({ beat: b, at: cursor / speed })
      const p = periodOf(b)
      cursor += p * k
    }
    cursor += beat.tail * k
  }

  if (tests.length) {
    const hops = tests.reduce((t, b) => t + Math.max(0, b.run.path.length - 1), 0)
    const hop = hops ? Math.max(beat.minHop, Math.min(beat.hop, beat.testCap / hops)) : beat.hop
    const probeOf = tests.map(() => true)
    const lenOf = (b: Extract<Beat, { kind: 'test' }>) => Math.max(0, b.run.path.length - 1) * hop + (b.failAt ? beat.failHold : 0)
    let span = tests.reduce((t, b, i) => t + lenOf(b) + (i ? beat.testGap : 0), 0)
    if (span > beat.testCap + beat.failHold) {
      // too long even at the minimum hop: only the first failing and the first passing test get a probe
      const firstFail = tests.findIndex((b) => b.failAt)
      const firstPass = tests.findIndex((b) => !b.failAt)
      tests.forEach((_, i) => { probeOf[i] = i === firstFail || i === firstPass })
      span = tests.reduce((t, b, i) => (probeOf[i] ? t + (t ? beat.testGap : 0) + lenOf(b) : t), 0)
    }
    const base = shape.length ? cursor - beat.tail * k : 0
    let t = base
    tests.forEach((b, i) => {
      if (!probeOf[i]) { items.push({ beat: b, at: base / speed, hop: 0, probe: false }); return }
      items.push({ beat: b, at: t / speed, hop: hop / speed, probe: true })
      t += lenOf(b) + beat.testGap
    })
    cursor = base + Math.max(0, span) + beat.tail
  }
  return { items, total: cursor / speed, k, compact }
}
