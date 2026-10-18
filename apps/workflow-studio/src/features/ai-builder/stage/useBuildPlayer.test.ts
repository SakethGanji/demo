import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BuildPlayer, type PlayerInput } from './useBuildPlayer'
import { useStageStore } from './stageStore'
import type { AiEvent, ProposedDefinition } from '../types'

const st = () => useStageStore.getState()
const ev = (seq: number, event: string, payload: Record<string, unknown>): AiEvent => ({ seq, at: '', event, payload, actor: null })

const node = (name: string, x: number, y = 0, type = 'Set') => ({ name, type, parameters: { secret: `v-${name}` }, position: { x, y } })
const conn = (s: string, t: string, out = 'main') => ({ source_node: s, source_output: out, target_node: t, target_input: 'main' })

// round 0 builds A -> B -> C(wrong place); round 1 moves C, adds D, wires C -> D
const PROPOSED = {
  name: 'wf', nodes: [node('A', 0, 0, 'Webhook'), node('B', 280), node('C', 600, 40), node('D', 900)],
  connections: [conn('A', 'B'), conn('B', 'C'), conn('C', 'D')],
} as unknown as ProposedDefinition

const r0 = ev(1, 'round_shape', {
  round: 0, changed: [], removed: [],
  nodes: [{ name: 'A', type: 'Webhook', position: { x: 0, y: 0 } }, { name: 'B', type: 'Set', position: { x: 280, y: 0 } }, { name: 'C', type: 'Set', position: { x: 560, y: 0 } }],
  connections: [['A', 'main', 'B', 'main'], ['B', 'main', 'C', 'main']],
})
const t0 = ev(2, 'tested', { round: 0, results: [{ test_id: 't1', name: 'T', status: 'failed', path: ['A', 'B', 'C'], failed_nodes: ['C'] }] })
const r1 = ev(3, 'round_shape', {
  round: 1, changed: [{ name: 'C', fields: ['condition'] }], removed: [],
  nodes: [{ name: 'A', type: 'Webhook', position: { x: 0, y: 0 } }, { name: 'B', type: 'Set', position: { x: 280, y: 0 } }, { name: 'C', type: 'Set', position: { x: 600, y: 40 } }, { name: 'D', type: 'Set', position: { x: 900, y: 0 } }],
  connections: [['A', 'main', 'B', 'main'], ['B', 'main', 'C', 'main'], ['C', 'main', 'D', 'main']],
})
const t1 = ev(4, 'tested', { round: 1, results: [{ test_id: 't1', name: 'T', status: 'passed', path: ['A', 'B', 'C', 'D'], failed_nodes: [] }] })

const input = (events: AiEvent[], finished = false): PlayerInput => ({ events, base: null, proposed: finished ? PROPOSED : null, finished })

/** The graph the stage shows, reduced to what the server's definition decides. */
function shown() {
  const nodes = st().nodes.filter((n) => st().marks[n.id] !== 'removed')
  return {
    nodes: nodes.map((n) => [n.id, n.type, Math.round(n.position.x), Math.round(n.position.y)]),
    edges: st().edges.filter((e) => st().edgeMarks[e.id] !== 'removed').map((e) => e.id),
  }
}
const expected = {
  nodes: [['A', 'workflowNode', 0, 0], ['B', 'workflowNode', 280, 0], ['C', 'workflowNode', 600, 40], ['D', 'workflowNode', 900, 0]],
  edges: ['edge-A-main-B-main', 'edge-B-main-C-main', 'edge-C-main-D-main'],
}

beforeEach(() => { vi.useFakeTimers(); st().end(); st().begin('p1', 'building') })
afterEach(() => { vi.useRealTimers(); st().end() })

describe('BuildPlayer, instant (the test flag)', () => {
  it('applies every round in one tick and ends on the proposed definition, in review', () => {
    const p = new BuildPlayer(() => true)
    p.sync(input([r0, t0, r1, t1], true))
    expect(shown()).toEqual(expected)           // no timers advanced
    expect(st()).toMatchObject({ mode: 'review', playback: 'done', presence: { visible: false } })
    expect(st().marks).toEqual({ A: 'added', B: 'added', C: 'added', D: 'added' })
    expect(st().nodes.every((n) => (n.data as { anim?: string }).anim !== 'enter')).toBe(true)
    expect(st().badges).toEqual({ A: 'passed', B: 'passed', C: 'passed', D: 'passed' })
  })

  it('builds live as events arrive, then lands on the proposal when the build finishes', () => {
    const p = new BuildPlayer(() => true)
    p.sync(input([r0]))
    expect(st().nodes.map((n) => n.id)).toEqual(['A', 'B', 'C'])
    expect(st()).toMatchObject({ mode: 'building', playback: 'playing' })
    p.sync(input([r0, t0]))
    expect(st().badges.C).toBe('failed')
    p.sync(input([r0, t0, r1]))
    expect(st().nodes.find((n) => n.id === 'C')?.position).toEqual({ x: 600, y: 40 })
    expect(st().badges).toEqual({})                             // a new round starts with clean badges
    p.sync(input([r0, t0, r1, t1], true))
    expect(shown()).toEqual(expected)
    expect(st().mode).toBe('review')
  })

  it('replays an older proposal without round_shape as one round from the diff', () => {
    const p = new BuildPlayer(() => true)
    p.sync(input([ev(1, 'round_started', { round: 0 }), ev(2, 'build_finished', {})], true))
    expect(shown()).toEqual(expected)
  })

  it('a proposal opened for review is shown, not replayed', () => {
    st().begin('p2', 'review')
    const p = new BuildPlayer(() => false)
    p.sync(input([r0, t0, r1, t1], false))
    expect(st().nodes).toHaveLength(0)
    p.sync(input([r0, t0, r1, t1], true))
    expect(shown()).toEqual(expected)
    const nodes = st().nodes
    p.sync(input([r0, t0, r1, t1], true))
    expect(st().nodes).toBe(nodes)               // landing happens once
  })

  it('keeps removed steps as ghosts marked removed', () => {
    const base = { name: 'wf', nodes: [node('A', 0, 0, 'Webhook'), node('Old', 280)], connections: [conn('A', 'Old')] } as unknown as ProposedDefinition
    const proposed = { name: 'wf', nodes: [node('A', 0, 0, 'Webhook'), node('New', 280)], connections: [conn('A', 'New')] } as unknown as ProposedDefinition
    const p = new BuildPlayer(() => true)
    p.sync({ events: [], base, proposed, finished: true })
    expect(st().marks).toEqual({ New: 'added', Old: 'removed' })
    expect(st().nodes.map((n) => n.id).sort()).toEqual(['A', 'New', 'Old'])
    expect(st().edgeMarks).toMatchObject({ 'edge-A-main-Old-main': 'removed', 'edge-A-main-New-main': 'added' })
  })
})

describe('BuildPlayer, with time', () => {
  const animOf = (id: string) => (st().nodes.find((n) => n.id === id)?.data as { anim?: string } | undefined)?.anim
  const building = () => st().nodes.filter((n) => ['enter', 'exit', 'change'].includes((n.data as { anim?: string }).anim ?? '')).length

  it('builds one node at a time: at any instant at most one node is entering, exiting or changing', () => {
    const p = new BuildPlayer(() => false)
    p.sync(input([r0]))
    expect(st().nodes).toHaveLength(0)                         // the round lead: nothing builds yet
    vi.advanceTimersByTime(499)
    expect(st().nodes).toHaveLength(0)
    vi.advanceTimersByTime(2)
    expect(st().nodes.map((n) => n.id)).toEqual(['A'])
    expect(animOf('A')).toBe('enter')
    let max = 0
    const seen = new Set<string>()
    for (let t = 0; t < 4000; t += 10) {
      vi.advanceTimersByTime(10)
      max = Math.max(max, building())
      for (const n of st().nodes) seen.add(n.id)
    }
    expect(max).toBe(1)
    expect([...seen]).toEqual(['A', 'B', 'C'])
    expect(st().nodes.every((n) => animOf(n.id) === 'idle')).toBe(true)
    expect(st().edges.map((e) => e.id)).toEqual(['edge-A-main-B-main', 'edge-B-main-C-main'])
    expect(st().playback).toBe('playing')                      // waiting for the build, not done
  })

  it('locks before the edge: an edge appears only after its later endpoint reached lock, never while it enters', () => {
    const p = new BuildPlayer(() => false)
    const bad: string[] = []
    const lockedAt: Record<string, number> = {}
    const edgeAt: Record<string, number> = {}
    const un = useStageStore.subscribe((s) => {
      const t = Date.now()
      for (const n of s.nodes) if ((n.data as { anim?: string }).anim === 'lock') lockedAt[n.id] ??= t
      for (const e of s.edges) {
        edgeAt[e.id] ??= t
        const tgt = s.nodes.find((n) => n.id === e.target)
        if ((tgt?.data as { anim?: string } | undefined)?.anim === 'enter') bad.push(e.id)
      }
    })
    p.sync(input([r0]))
    vi.advanceTimersByTime(4000)
    un()
    expect(bad).toEqual([])
    expect(edgeAt['edge-A-main-B-main']).toBeGreaterThan(lockedAt.B)
    expect(edgeAt['edge-B-main-C-main']).toBeGreaterThan(lockedAt.C)
    expect(edgeAt['edge-B-main-C-main'] - lockedAt.C).toBeLessThanOrEqual(60)
  })

  it('log.built follows the locks, and the progress counter with it', () => {
    const p = new BuildPlayer(() => false)
    p.sync(input([r0]))
    expect(st().progress).toEqual({ done: 0, total: 3 })
    expect(st().log).toEqual({ round: 0, built: {}, linked: false, tests: 0 })
    vi.advanceTimersByTime(520)
    expect(animOf('A')).toBe('enter')
    expect(st().log.built).toEqual({})
    vi.advanceTimersByTime(60)                                  // t = 580: A locked (lock at 500 + 560)
    expect(animOf('A')).toBe('enter')
    vi.advanceTimersByTime(500)
    expect(animOf('A')).toBe('lock')
    expect(st().log.built).toEqual({ A: true })
    expect(st().progress?.done).toBe(1)
    expect(st().buildHead).toMatchObject({ node: 'A', kind: 'construct' })
    vi.advanceTimersByTime(4000)
    expect(st().log).toMatchObject({ built: { A: true, B: true, C: true }, linked: true })
    expect(st().progress).toEqual({ done: 3, total: 3 })
  })

  it('the canvas trails the engine instead of squeezing: queued rounds start sooner by the catch-up speed only', () => {
    const one = new BuildPlayer(() => false)
    one.sync(input([r0, t0]))                                   // 1 segment waiting -> 1.4x
    vi.advanceTimersByTime(340)
    expect(st().nodes).toHaveLength(0)
    vi.advanceTimersByTime(30)                                  // 500 / 1.4 = 357 ms
    expect(st().nodes.map((n) => n.id)).toEqual(['A'])
    st().end(); st().begin('p', 'building')
    const two = new BuildPlayer(() => false)
    two.sync(input([r0, t0, r1]))                               // 2 waiting -> 2.2x
    vi.advanceTimersByTime(215)
    expect(st().nodes).toHaveLength(0)
    vi.advanceTimersByTime(20)                                  // 500 / 2.2 = 227 ms
    expect(st().nodes.map((n) => n.id)).toEqual(['A'])
  })

  it('skip() applies the current round and every queued one at once, and later segments are instant', () => {
    const p = new BuildPlayer(() => false)
    p.sync(input([r0, t0, r1]))
    vi.advanceTimersByTime(700)
    p.skip()
    expect(st().nodes.map((n) => n.id).sort()).toEqual(['A', 'B', 'C', 'D'])
    expect(st().edges.map((e) => e.id)).toHaveLength(3)
    expect(st().nodes.find((n) => n.id === 'C')?.position).toEqual({ x: 600, y: 40 })
    expect(st().nodes.every((n) => animOf(n.id) === 'idle')).toBe(true)
    expect(st().edges.every((e) => e.data?.anim === 'idle')).toBe(true)
    expect(st().log).toMatchObject({ round: 1, built: { A: true, B: true, C: true, D: true }, linked: true })
    expect(st().progress?.done).toBe(st().progress?.total)
    expect(st().buildHead).toBeNull()
    expect(st().mode).toBe('building')                          // the build has not finished yet
    p.sync(input([r0, t0, r1, t1], true))                       // a later segment and the end arrive after the skip
    expect(shown()).toEqual(expected)                           // with no timer advanced
    expect(st()).toMatchObject({ mode: 'review', playback: 'done' })
    expect(st().sweep).toBe(0)                                  // a skipped build gets no review sweep
  })

  it('cancel() snaps to the proposed definition, whatever is in flight', () => {
    const p = new BuildPlayer(() => false)
    p.sync(input([r0, t0, r1, t1], true))
    vi.advanceTimersByTime(250)
    p.cancel()
    expect(shown()).toEqual(expected)
    expect(st()).toMatchObject({ mode: 'review', playback: 'done' })
    vi.advanceTimersByTime(10_000)                             // nothing left to fire
    expect(shown()).toEqual(expected)
  })

  it('skip() and cancel() both end on the proposed definition after a full run', () => {
    const p = new BuildPlayer(() => false)
    p.sync(input([r0, t0, r1, t1], true))
    for (let i = 0; i < 6; i++) { p.skip(); vi.advanceTimersByTime(30) }
    p.cancel()
    expect(shown()).toEqual(expected)
  })

  it('left alone it reaches the same final state in at most a few rounds of budget', () => {
    const p = new BuildPlayer(() => false)
    p.sync(input([r0, t0, r1, t1], true))
    vi.advanceTimersByTime(4 * 3500)
    expect(shown()).toEqual(expected)
    expect(st().mode).toBe('review')
  })

  it('tweens a node that moves between rounds, one frame at a time, and lands exactly', () => {
    const p = new BuildPlayer(() => false)
    p.sync(input([r0, r1]))
    const c = () => st().nodes.find((n) => n.id === 'C')!.position
    vi.advanceTimersByTime(2900)                                // round 0 (at 1.4x) is over; the move beat of round 1 is 225 ms in
    const mid = c()
    expect(mid.x).toBeGreaterThan(560)
    expect(mid.x).toBeLessThan(600)
    vi.advanceTimersByTime(8000)
    expect(c()).toEqual({ x: 600, y: 40 })
  })

  it('a real node lands where the round puts it (not where its outline is), the outline dissolves under it and is removed', () => {
    st().begin('p', 'blueprint', [{ id: 'bp:A', type: 'blueprintNode', position: { x: -100, y: 0 }, data: {} as never }])
    const p = new BuildPlayer(() => false)
    p.sync(input([r0]))
    vi.advanceTimersByTime(510)
    const a = st().nodes.find((n) => n.id === 'A')!
    expect(a.position).toEqual({ x: 0, y: 0 })
    expect(st().buildHead).toMatchObject({ node: 'A', kind: 'construct', at: { x: 0, y: 0 } })
    const bp = st().nodes.find((n) => n.id === 'bp:A')
    expect((bp?.data as { dissolve?: boolean } | undefined)?.dissolve).toBe(true)
    vi.advanceTimersByTime(300)
    expect(st().nodes.find((n) => n.id === 'bp:A')).toBeUndefined()
    vi.advanceTimersByTime(1000)
    expect(st().nodes.find((n) => n.id === 'A')!.position).toEqual({ x: 0, y: 0 })
  })

  it('the build head sits at the node\'s round_shape position even when its outline is somewhere else', () => {
    st().begin('p', 'blueprint', [
      { id: 'bp:A', type: 'blueprintNode', position: { x: 900, y: 0 }, data: {} as never },
      { id: 'bp:B', type: 'blueprintNode', position: { x: 900, y: 0 }, data: {} as never },
    ])
    const p = new BuildPlayer(() => false)
    p.sync(input([r0]))
    for (let i = 0; i < 40; i++) {
      vi.advanceTimersByTime(100)
      const head = st().buildHead
      const node = head && st().nodes.find((n) => n.id === head.node)
      if (head && node && head.kind === 'construct') expect(node.position).toEqual(head.at)
    }
  })

  it('a failing test sends a probe and stops it at the failing node', () => {
    const p = new BuildPlayer(() => false)
    p.sync(input([r0, t0]))
    vi.advanceTimersByTime(3400)                                // round 0 (1.4x) done, the test segment is running
    expect(st().testPath).toMatchObject({ nodes: ['A', 'B', 'C'], status: 'failed', failAt: 'C' })
    expect(st().log.tests).toBe(1)
    vi.advanceTimersByTime(600)
    expect(st().badges.C).toBe('failed')
    expect(st().buildHead).toMatchObject({ node: 'C', kind: 'fail' })
  })

  it('does nothing while no stage is active', () => {
    st().end()
    const p = new BuildPlayer(() => true)
    p.sync(input([r0, t0, r1, t1], true))
    expect(st().nodes).toHaveLength(0)
    expect(st().active).toBe(false)
  })
})
