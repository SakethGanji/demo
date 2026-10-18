/**
 * The build player (PLAN-MOTION-V2 §5.2). It turns the engine's events into timed writes to the stage store.
 *
 * - Events are consumed by a `seq` cursor, so polling can deliver them a few at a time.
 * - One segment (a round's shape, or its test results) plays at a time, as a list of beats: one node at a time
 *   (`beats.ts`). A segment waiting behind the current one plays faster (catch-up); nothing is ever squeezed.
 * - `skip()` finishes the current segment, every queued one, and makes later ones instant.
 * - Whatever happens, the last frame is the server's proposed definition (`snapToFinal`).
 * - Under the test flag (or `instant`) everything is applied in one tick.
 * - It writes `stageStore` only, never `workflowStore`.
 */
import { useEffect, useMemo, useRef } from 'react'
import type { Edge } from '@xyflow/react'
import type { DiffMark } from '@/features/workflow-editor/stores/historyStore'
import { fromBackendWorkflow } from '@/features/workflow-editor/lib/workflowTransform'
import type { ApiWorkflowDetail } from '@/shared/lib/backendTypes'
import { beat as bt, dur, round as roundTokens } from '@/shared/motion/tokens'
import { motionOff } from '@/shared/motion/testFlag'
import { ghostNodesFor, marksFromDefinitions } from '../proposalMarks'
import { useAiProposalStore } from '../aiProposalStore'
import type { AiEvent, ProposedDefinition } from '../types'
import {
  edgeKey, opsFromDiff, opsFromRoundShape, opsFromTested, snapshotFromDefinition, snapshotFromShape,
  type AnimState, type BuildOp, type ShapeSnapshot, type TestResult, type XY,
} from './buildOps'
import { beatsFromOps, scheduleBeats, type Beat, type ScheduledBeat } from './beats'
import { blueprintId, isBlueprintId } from './blueprint'
import { enrichDefinition, enrichDefNode } from './enrichNodes'
import { carryMeasured } from './carryMeasured'
import { parseRoundShape, parseTestResults } from './opsFromEvents'
import { useStageStore, type StageEdge, type StageNode, type StageNodeData } from './stageStore'

export interface PlayerInput {
  events: AiEvent[]
  base: ProposedDefinition | null
  proposed: ProposedDefinition | null
  /** the build call has returned: `proposed` is the final frame */
  finished: boolean
}

interface Segment { round: number; ops: BuildOp[] }

type Timer = ReturnType<typeof setTimeout>

const nextFrame = (fn: () => void): Timer => setTimeout(fn, 16)
const smooth = (t: number) => (t < 0.5 ? 2 * t * t : 1 - 2 * (1 - t) * (1 - t))

function makeNode(name: string, type: string, position: XY): StageNode {
  const api = {
    id: '', name: '', active: false,
    definition: { nodes: [enrichDefNode({ name, type, parameters: {}, position })], connections: [] },
  } as unknown as ApiWorkflowDetail
  const n = fromBackendWorkflow(api).nodes[0]
  return n as StageNode
}

function graphOf(def: ProposedDefinition): { nodes: StageNode[]; edges: StageEdge[] } {
  const api = { id: '', name: '', active: false, definition: enrichDefinition(def) } as unknown as ApiWorkflowDetail
  const g = fromBackendWorkflow(api)
  return { nodes: g.nodes as StageNode[], edges: g.edges as StageEdge[] }
}

type AddEdgeOp = Extract<BuildOp, { kind: 'addEdge' }>
type RemoveEdgeOp = Extract<BuildOp, { kind: 'removeEdge' }>
type TestBeat = Extract<Beat, { kind: 'test' }>

const isStructural = (b: Beat) => b.kind === 'construct' || b.kind === 'morph' || b.kind === 'deconstruct'
const patchNode = (nodes: StageNode[], id: string, data: Partial<StageNodeData>) =>
  nodes.map((n) => (n.id === id ? { ...n, data: { ...n.data, ...data } } : n))
const nodeIds = (ns: StageNode[]) => ns.filter((n) => !isBlueprintId(n.id)).map((n) => n.id)
const bump = (p: { done: number; total: number } | null) => (p ? { ...p, done: Math.min(p.total, p.done + 1) } : p)

export class BuildPlayer {
  private cursor = 0
  private queue: Segment[] = []
  private current: { seg: Segment; total: number } | null = null
  private timers = new Set<Timer>()
  private tweens = new Map<string, { from: XY; to: XY; t0: number; ms: number }>()
  private tweenTimer: Timer | null = null
  private snap: ShapeSnapshot | null = null
  private sawShape = false
  private input: PlayerInput | null = null
  private finalized = false
  private lastResults: TestResult[] = []
  private pathSeq = 0
  private started = false
  /** skip() was pressed: everything known, and everything that arrives later, is applied at once */
  private skipAll = false
  /** a shape segment was played with time (so the review opens with the sweep) */
  private playedAny = false

  private readonly instant: () => boolean
  private snapped = false

  constructor(instant: () => boolean = motionOff) {
    this.instant = instant
  }

  private now(): boolean { return this.instant() || this.skipAll }

  /** Feed the latest events and proposal. Safe to call on every change. */
  sync(input: PlayerInput): void {
    const stage = useStageStore.getState()
    if (!stage.active) return
    this.input = input
    if (!this.started) {
      this.started = true
      this.snap = snapshotFromDefinition(input.base)
      if (stage.mode === 'review') this.finalized = true   // an existing proposal opened for review is shown, not replayed
    }
    for (const e of input.events) {
      if (e.seq <= this.cursor) continue
      this.cursor = e.seq
      if (this.finalized) continue
      if (e.event === 'round_shape') {
        const shape = parseRoundShape(e.payload)
        if (!shape) continue
        this.sawShape = true
        this.queue.push({ round: shape.round, ops: opsFromRoundShape(this.snap, shape) })
        this.snap = snapshotFromShape(shape)
      } else if (e.event === 'tested') {
        const results = parseTestResults(e.payload)
        if (results.length) {
          this.lastResults = results
          this.queue.push({ round: Number(e.payload.round) || 0, ops: opsFromTested(results) })
        }
      }
    }
    if (input.finished && !this.sawShape && !this.finalized && input.proposed && !this.queue.length && !this.current) {
      this.sawShape = true  // an older proposal without round_shape: the whole change as one round
      this.queue.push({ round: 0, ops: opsFromDiff(input.base, input.proposed) })
    }
    if (this.finalized && input.finished && !this.snapped) { this.snapToFinal(); return }
    this.pump()
  }

  /** Finish the current segment and every queued one now; later segments are applied instantly too. */
  skip(): void {
    if (!useStageStore.getState().active) return
    this.skipAll = true
    this.clearTimers()
    const segs = [...(this.current ? [this.current.seg] : []), ...this.queue]
    this.current = null
    this.queue = []
    for (const seg of segs) this.playNow(seg)
    this.snapTweens()
    useStageStore.setState((s) => {
      const names = nodeIds(s.nodes)
      return {
        nodes: s.nodes.map((n) => (isBlueprintId(n.id) ? n : { ...n, data: { ...n.data, anim: 'idle' as AnimState, animFields: undefined, animTone: undefined } })),
        edges: s.edges.map((e) => (e.data?.anim && e.data.anim !== 'idle' ? { ...e, data: { ...e.data, anim: 'idle' as AnimState } } : e)),
        buildHead: null,
        log: { ...s.log, built: Object.fromEntries(names.map((n) => [n, true as const])), linked: true, tests: 999 },
        progress: s.progress ? { ...s.progress, done: s.progress.total } : null,
      }
    })
    this.pump()
  }

  /** Stop everything and show the end state. */
  cancel(): void {
    this.skipAll = true
    this.clearTimers()
    this.current = null
    this.queue = []
    this.snapToFinal()
  }

  dispose(): void {
    this.clearTimers()
    this.current = null
    this.queue = []
  }

  private pump(): void {
    if (this.current || this.finalized) return
    const seg = this.queue.shift()
    if (!seg) {
      if (this.input?.finished && this.input.proposed) this.snapToFinal()
      return
    }
    const stage = useStageStore.getState()
    if (stage.playback !== 'playing') stage.setPlayback('playing')
    // the camera frames the round up front (it then follows the build head); constructs first, so points[0] is the first head
    const arriving = [
      ...seg.ops.flatMap((o) => (o.kind === 'addNode' ? [o.position] : [])),
      ...seg.ops.flatMap((o) => (o.kind === 'moveNode' ? [o.to] : [])),
    ]
    if (arriving.length) stage.requestFrame(arriving)
    // an outline whose real node lands elsewhere glides there now, so a layout difference never reads as a jump cut
    for (const o of seg.ops) {
      if (o.kind !== 'addNode') continue
      const bp = stage.nodes.find((n) => n.id === blueprintId(o.node))
      if (bp && (Math.abs(bp.position.x - o.position.x) > 0.5 || Math.abs(bp.position.y - o.position.y) > 0.5)) {
        this.tween(bp.id, bp.position, o.position, this.now())
      }
    }
    if (this.now()) {
      this.playNow(seg)
      this.pump()
      return
    }
    // catch-up: a segment starts faster when others wait behind it; a running segment is never rescheduled
    const speed = this.queue.length >= 2 ? bt.catchUp2 : this.queue.length === 1 ? bt.catchUp1 : 1
    const beats = beatsFromOps(seg.ops)
    const sched = scheduleBeats(beats, { speed })
    const shape = seg.ops.some((o) => o.kind === 'roundStart')
    if (shape) {
      this.playedAny = true
      this.startRound(seg.round, beats)
    }
    const u = sched.k / speed
    this.current = { seg, total: sched.total }
    for (const item of sched.items) this.playBeat(item, u, sched.compact)
    if (shape && !beats.some((b) => b.kind === 'link')) {
      const last = Math.max(0, ...sched.items.filter((i) => isStructural(i.beat)).map((i) => i.at))
      this.later(() => useStageStore.getState().patchLog({ linked: true }), last + bt.edgeAt * u + 0.02)
    }
    this.later(() => {
      if (shape) useStageStore.getState().setNodes((ns) => ns.filter((n) => !isBlueprintId(n.id)))
      this.current = null
      this.pump()
    }, sched.total + 0.02)
  }

  private later(fn: () => void, seconds: number): Timer {
    const t = setTimeout(() => { this.timers.delete(t); fn() }, Math.max(0, seconds) * 1000)
    this.timers.add(t)
    return t
  }

  private clearTimers(): void {
    for (const t of this.timers) clearTimeout(t)
    this.timers.clear()
    if (this.tweenTimer) clearTimeout(this.tweenTimer)
    this.tweenTimer = null
  }

  // ── round bookkeeping ──────────────────────────────────────────────────────────────────────

  /** Round start: building mode, a fresh log, the progress counter. Failed badges stay on nodes this round will fix. */
  private startRound(round: number, beats: Beat[]): void {
    const morphs = new Set(beats.flatMap((b) => (b.kind === 'morph' ? [b.node] : [])))
    const total = beats.filter(isStructural).length
    useStageStore.setState((s) => ({
      active: true, mode: 'building',
      presence: { ...s.presence, visible: true },
      badges: Object.fromEntries(Object.entries(s.badges).filter(([n]) => morphs.has(n))),
      testPath: null,
      log: { round, built: {}, linked: false, tests: 0 },
      progress: total > 0 ? { done: 0, total } : null,
    }))
  }

  /** A whole segment, instantly (the test flag, skip, or a segment that arrives after skip). */
  private playNow(seg: Segment): void {
    const beats = beatsFromOps(seg.ops)
    const shape = seg.ops.some((o) => o.kind === 'roundStart')
    if (shape) this.startRound(seg.round, beats)
    for (const b of beats) this.applyNow(b)
    const st = useStageStore.getState()
    if (shape) {
      st.setNodes((ns) => ns.filter((n) => !isBlueprintId(n.id)))
      st.patchLog({ linked: true })
      useStageStore.setState((s) => ({ progress: s.progress ? { ...s.progress, done: s.progress.total } : null }))
    }
    if (beats.some((b) => b.kind === 'test')) st.patchLog({ tests: 999 })
  }

  // ── pieces shared by the timed and the instant paths ───────────────────────────────────────

  private posOf(name: string, fallback: XY): XY {
    return useStageStore.getState().nodes.find((n) => n.id === name)?.position ?? fallback
  }

  /** One write: the node's anim, its sidebar row (`log.built`) and the progress counter. */
  private finish(id: string, anim: AnimState, extra: Partial<StageNodeData> = {}, clearBadge = false): void {
    useStageStore.setState((s) => {
      const badges = { ...s.badges }
      if (clearBadge) delete badges[id]
      return {
        nodes: patchNode(s.nodes, id, { anim, ...extra }),
        badges,
        log: { ...s.log, built: { ...s.log.built, [id]: true as const } },
        progress: bump(s.progress),
      }
    })
  }

  private addEdge(op: AddEdgeOp, anim: AnimState, drawScale: number): void {
    useStageStore.setState((s) => {
      if (s.edges.some((e) => e.id === op.key)) return s
      const edge: StageEdge = {
        id: op.key, source: op.src, target: op.tgt, sourceHandle: op.out, targetHandle: op.inp,
        type: 'workflowEdge', data: { anim, drawScale },
      }
      return { edges: [...s.edges, edge], edgeMarks: { ...s.edgeMarks, [op.key]: 'added' } }
    })
    if (anim === 'enter') {
      this.later(() => useStageStore.getState().setEdges((es) => es.map((e) => (e.id === op.key ? { ...e, data: { ...e.data, anim: 'idle' } } : e))),
        bt.edgeDraw * drawScale + 0.4)
    }
  }

  private retractEdge(op: RemoveEdgeOp, now: boolean): void {
    useStageStore.setState((s) => ({
      edgeMarks: { ...s.edgeMarks, [op.key]: 'removed' },
      edges: s.edges.map((e) => (e.id === op.key ? { ...e, data: { ...e.data, anim: now ? 'idle' : 'exit' } } : e)),
    }))
  }

  private tick(run: TestBeat['run'], n: string): void {
    const s = useStageStore.getState()
    if (s.badges[n] !== 'failed') s.setBadge(n, run.failedNodes.includes(n) ? run.status === 'errored' ? 'errored' : 'failed' : 'passed')
  }

  private pathUntilFail(b: TestBeat): string[] {
    const i = b.failAt ? b.run.path.indexOf(b.failAt) : -1
    return i >= 0 ? b.run.path.slice(0, i + 1) : b.run.path
  }

  // ── one beat, instantly ────────────────────────────────────────────────────────────────────

  private applyNow(b: Beat): void {
    const st = useStageStore.getState()
    switch (b.kind) {
      case 'retract':
        for (const e of b.edges) this.retractEdge(e, true)
        return
      case 'deconstruct':
        st.setMarks({ ...st.marks, [b.node]: 'removed' }, st.edgeMarks)
        this.finish(b.node, 'idle', { animFields: undefined })
        return
      case 'move':
        for (const m of b.moves) this.tween(m.node, m.from, m.to, true)
        return
      case 'morph':
        st.setMarks({ ...st.marks, [b.node]: st.marks[b.node] === 'added' ? 'added' : 'changed' }, st.edgeMarks)
        this.finish(b.node, 'idle', { animFields: undefined, animTone: undefined }, true)
        return
      case 'construct': {
        if (!st.nodes.some((n) => n.id === b.node)) {
          const real = makeNode(b.node, b.type, b.position)
          st.setNodes((ns) => [...ns.filter((n) => n.id !== blueprintId(b.node)), { ...real, data: { ...real.data, anim: 'idle' } }])
        } else {
          st.setNodes((ns) => ns.filter((n) => n.id !== blueprintId(b.node)))
        }
        const s = useStageStore.getState()
        s.setMarks({ ...s.marks, [b.node]: 'added' }, s.edgeMarks)
        this.finish(b.node, 'idle')
        for (const e of b.edges) this.addEdge(e, 'idle', 1)
        return
      }
      case 'link':
        for (const e of b.edges) this.addEdge(e, 'idle', 1)
        return
      case 'test': {
        for (const n of this.pathUntilFail(b)) this.tick(b.run, n)
        st.patchLog({ tests: st.log.tests + 1 })
        return
      }
    }
  }

  // ── one beat, over time ────────────────────────────────────────────────────────────────────

  /** `u` = k / speed: every beat-internal time is `token * u`. */
  private playBeat(item: ScheduledBeat, u: number, compact: boolean): void {
    const b = item.beat
    const at = item.at
    const lead = (label: string, node: string, position: XY, kind: 'construct' | 'deconstruct' | 'morph') => {
      this.later(() => {
        const s = useStageStore.getState()
        s.setPresence({ visible: true, node, label, at: position })
        s.setHead({ node, kind, at: position, compact })
      }, at - bt.lead * u)
    }
    switch (b.kind) {
      case 'retract':
        this.later(() => { for (const e of b.edges) this.retractEdge(e, false) }, at)
        return
      case 'move':
        this.later(() => {
          for (const m of b.moves) this.tween(m.node, m.from, m.to, false)
          const first = b.moves[0]
          if (first) useStageStore.getState().setPresence({ node: first.node, label: `moving ${first.node}` })
        }, at)
        return
      case 'deconstruct': {
        lead(`removing ${b.node}`, b.node, b.position, 'deconstruct')
        this.later(() => {
          useStageStore.setState((s) => ({
            nodes: patchNode(s.nodes, b.node, { anim: 'exit', animScale: u, animCompact: compact }),
            marks: { ...s.marks, [b.node]: 'removed' },
            presence: { ...s.presence, visible: true, node: b.node, label: `removing ${b.node}`, at: b.position },
            buildHead: { seq: (s.buildHead?.seq ?? 0) + 1, node: b.node, kind: 'deconstruct', at: b.position, compact },
          }))
        }, at)
        this.later(() => this.finish(b.node, 'idle'), at + bt.exitIdleAt * u)
        return
      }
      case 'morph': {
        const label = `fixing ${b.node}${b.fields.length ? `: ${b.fields.join(', ')}` : ''}`
        const position = this.posOf(b.node, { x: 0, y: 0 })
        lead(label, b.node, position, 'morph')
        this.later(() => {
          useStageStore.setState((s) => {
            const pos = s.nodes.find((n) => n.id === b.node)?.position ?? position
            return {
              nodes: patchNode(s.nodes, b.node, { anim: 'change', animFields: b.fields, animTone: 'warning', animScale: u, animCompact: false }),
              marks: { ...s.marks, [b.node]: s.marks[b.node] === 'added' ? 'added' : 'changed' },
              presence: { ...s.presence, visible: true, node: b.node, label, at: pos },
              buildHead: { seq: (s.buildHead?.seq ?? 0) + 1, node: b.node, kind: 'morph', at: pos, compact },
            }
          })
        }, at)
        this.later(() => this.finish(b.node, 'lock', {}, true), at + bt.morphLockAt * u)
        this.later(() => useStageStore.getState().patchNodeData(b.node, { anim: 'idle', animFields: undefined, animTone: undefined }),
          at + (bt.morphLockAt + bt.lockHold) * u)
        return
      }
      case 'construct': {
        lead(`building ${b.node}`, b.node, b.position, 'construct')
        this.later(() => {
          useStageStore.setState((s) => {
            if (s.nodes.some((n) => n.id === b.node)) return s
            const real = makeNode(b.node, b.type, b.position)
            const bpId = blueprintId(b.node)
            // the real node lands where the engine put it (round_shape), never where an outline happens to sit: the head, the
            // orb, the ripple and the camera all read this position. The outline has been gliding there since the round began.
            const node: StageNode = { ...real, position: b.position, data: { ...real.data, anim: 'enter', animScale: u, animCompact: compact, animTone: 'primary' } }
            return {
              nodes: [...s.nodes.map((n) => (n.id === bpId ? { ...n, data: { ...n.data, dissolve: true } } : n)), node] as StageNode[],
              marks: { ...s.marks, [b.node]: 'added' },
              presence: { ...s.presence, visible: true, node: b.node, label: `building ${b.node}`, at: b.position },
              buildHead: { seq: (s.buildHead?.seq ?? 0) + 1, node: b.node, kind: 'construct', at: b.position, compact },
            }
          })
        }, at)
        this.later(() => useStageStore.getState().setNodes((ns) => ns.filter((n) => n.id !== blueprintId(b.node))), at + 0.24 * u)
        this.later(() => this.finish(b.node, 'lock'), at + bt.lockAt * u)
        b.edges.forEach((e, i) => this.later(() => this.addEdge(e, 'enter', u), at + (bt.edgeAt + i * bt.edgeGap) * u))
        this.later(() => useStageStore.getState().patchNodeData(b.node, { anim: 'idle' }), at + (bt.lockAt + bt.lockHold) * u)
        return
      }
      case 'link': {
        b.edges.forEach((e, i) => this.later(() => this.addEdge(e, 'enter', u), at + i * bt.linkGap * u))
        this.later(() => useStageStore.getState().patchLog({ linked: true }), at + (bt.edgeDraw + bt.linkGap * Math.max(0, b.edges.length - 1)) * u)
        return
      }
      case 'test': {
        const name = this.lastResults.find((r) => r.test_id === b.run.testId)?.name ?? b.run.testId
        const hop = item.hop ?? 0
        const probe = item.probe !== false
        const target = b.failAt ?? b.run.path[b.run.path.length - 1]
        const path = b.run.path
        const failIdx = b.failAt ? path.indexOf(b.failAt) : -1
        this.later(() => useStageStore.getState().setPresence({ visible: true, label: `testing: ${name}` }), at - bt.lead * u)
        this.later(() => {
          useStageStore.setState((s) => {
            const pos = s.nodes.find((n) => n.id === target)?.position
            return {
              log: { ...s.log, tests: s.log.tests + 1 },
              ...(pos ? { buildHead: { seq: (s.buildHead?.seq ?? 0) + 1, node: target, kind: 'test' as const, at: pos } } : {}),
              ...(probe ? { testPath: { id: ++this.pathSeq, nodes: path, status: b.run.status, hopMs: hop * 1000, failAt: b.failAt ?? undefined } } : {}),
            }
          })
        }, at)
        const fail = () => {
          const n = b.failAt!
          const s = useStageStore.getState()
          const pos = s.nodes.find((x) => x.id === n)?.position
          s.patchNodeData(n, { anim: 'fail' })
          if (pos) useStageStore.setState((x) => ({ buildHead: { seq: (x.buildHead?.seq ?? 0) + 1, node: n, kind: 'fail', at: pos } }))
          this.later(() => useStageStore.getState().patchNodeData(n, { anim: 'idle' }), dur.shockwave * 2)
        }
        this.pathUntilFail(b).forEach((n, i) => this.later(() => {
          this.tick(b.run, n)
          if (i === failIdx) fail()
        }, at + (probe ? i * hop : 0)))
        return
      }
    }
  }

  // ── position tweens: one store write per frame for all moving nodes ───────────────────────────

  private tween(id: string, from: XY, to: XY, now: boolean): void {
    const store = useStageStore.getState()
    if (now || this.tweens.size >= roundTokens.maxTweened) {
      this.tweens.delete(id)
      store.setPositions({ [id]: to })
      return
    }
    this.tweens.set(id, { from, to, t0: Date.now(), ms: dur.move * 1000 })
    if (!this.tweenTimer) this.tweenTimer = nextFrame(() => this.frame())
  }

  private frame(): void {
    this.tweenTimer = null
    const t = Date.now()
    const pos: Record<string, XY> = {}
    for (const [id, tw] of this.tweens) {
      const k = Math.min(1, (t - tw.t0) / tw.ms)
      const e = smooth(k)
      pos[id] = { x: tw.from.x + (tw.to.x - tw.from.x) * e, y: tw.from.y + (tw.to.y - tw.from.y) * e }
      if (k >= 1) this.tweens.delete(id)
    }
    if (Object.keys(pos).length) useStageStore.getState().setPositions(pos)
    if (this.tweens.size) this.tweenTimer = nextFrame(() => this.frame())
  }

  private snapTweens(): void {
    if (!this.tweens.size) return
    const pos: Record<string, XY> = {}
    for (const [id, tw] of this.tweens) pos[id] = tw.to
    this.tweens.clear()
    if (this.tweenTimer) clearTimeout(this.tweenTimer)
    this.tweenTimer = null
    useStageStore.getState().setPositions(pos)
  }

  // ── the end state ───────────────────────────────────────────────────────────────────────────

  /** Replace the stage graph with the server's proposed definition (plus ghosts of removed steps), then go to review. */
  private snapToFinal(): void {
    const input = this.input
    this.clearTimers()
    this.tweens.clear()
    if (!input?.proposed) return
    this.finalized = true
    this.snapped = true
    const st = useStageStore.getState()
    const { nodes, edges } = graphOf(input.proposed)
    const marks: Record<string, DiffMark> = marksFromDefinitions(input.base as never, input.proposed)
    const removed = Object.keys(marks).filter((k) => marks[k] === 'removed')
    const ghosts = ghostNodesFor(removed, input.base as never) as StageNode[]
    const edgeMarks: Record<string, DiffMark> = {}
    const ghostEdges: StageEdge[] = []
    const have = new Set(edges.map((e) => e.id))
    for (const c of snapshotFromDefinition(input.base).connections) {
      const key = edgeKey(...c)
      if (have.has(key) || !removed.includes(c[0]) && !removed.includes(c[2])) continue
      ghostEdges.push({ id: key, source: c[0], target: c[2], sourceHandle: c[1], targetHandle: c[3], type: 'workflowEdge', data: { anim: 'idle' } })
      edgeMarks[key] = 'removed'
    }
    for (const e of edges) if (!snapshotFromDefinition(input.base).connections.some((c) => edgeKey(...c) === e.id)) edgeMarks[e.id] = 'added'
    st.setGraph(carryMeasured([...nodes, ...ghosts], st.nodes), [...edges, ...ghostEdges] as Edge[] as StageEdge[])
    st.setMarks(marks, edgeMarks)
    const badges: Record<string, 'passed' | 'failed' | 'errored'> = {}
    for (const r of this.lastResults) {
      for (const n of r.path) if (badges[n] !== 'failed') badges[n] = 'passed'
      for (const n of r.failed_nodes) badges[n] = r.status === 'errored' ? 'errored' : 'failed'
    }
    useStageStore.setState({ badges, testPath: null, buildHead: null, progress: null })
    st.setPresence({ visible: false, node: null, label: null })
    st.setPlayback('done')
    st.setMode('review')
    if (this.playedAny && !this.now()) useStageStore.getState().bumpSweep()
  }
}

/** Mounted once in WorkflowCanvas. Returns the controls the Skip button and Esc use. */
export function useBuildPlayer(): { skip: () => void; cancel: () => void } {
  const player = useRef<BuildPlayer | null>(null)
  const active = useStageStore((s) => s.active)
  const skipSeq = useStageStore((s) => s.skipSeq)
  const events = useAiProposalStore((s) => s.events)
  const proposal = useAiProposalStore((s) => s.proposal)
  const finished = !!proposal?.proposed_definition && ['ready', 'failed', 'kept'].includes(proposal.status)

  useEffect(() => {
    if (!active) return
    player.current ??= new BuildPlayer()
    player.current.sync({
      events, base: proposal?.base_definition ?? null, proposed: proposal?.proposed_definition ?? null, finished,
    })
  }, [active, events, proposal?.base_definition, proposal?.proposed_definition, finished])

  const lastSkip = useRef(skipSeq)
  useEffect(() => {
    if (skipSeq !== lastSkip.current) { lastSkip.current = skipSeq; player.current?.skip() }
  }, [skipSeq])

  useEffect(() => () => player.current?.dispose(), [])
  useEffect(() => { if (!active) { player.current?.dispose(); player.current = null } }, [active])

  return useMemo(() => ({ skip: () => player.current?.skip(), cancel: () => player.current?.cancel() }), [])
}

