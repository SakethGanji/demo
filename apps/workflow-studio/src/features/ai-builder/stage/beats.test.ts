import { describe, expect, it } from 'vitest'
import { beat } from '@/shared/motion/tokens'
import { opsFromRoundShape, opsFromTested, type BuildOp, type RoundShape, type ShapeConnection, type TestResult } from './buildOps'
import { beatsFromOps, scheduleBeats, type Beat } from './beats'

const pos = (x: number, y = 0) => ({ x, y })
const conn = (s: string, t: string, out = 'main'): ShapeConnection => [s, out, t, 'main']

/** The showcase: Receive -> Normalise -> If -> (true) Send / (false) Pay -> Log -> Reply */
const showcase: RoundShape = {
  round: 0, changed: [], removed: [],
  nodes: [
    { name: 'Receive', type: 'Webhook', position: pos(0) },
    { name: 'Normalise', type: 'Set', position: pos(190) },
    { name: 'Over limit?', type: 'If', position: pos(380) },
    { name: 'Send to manager', type: 'Set', position: pos(570, -55) },
    { name: 'Pay refund', type: 'Set', position: pos(570, 55) },
    { name: 'Log decision', type: 'Set', position: pos(760) },
    { name: 'Reply', type: 'Set', position: pos(950) },
  ],
  connections: [
    conn('Receive', 'Normalise'), conn('Normalise', 'Over limit?'), conn('Over limit?', 'Send to manager', 'true'),
    conn('Over limit?', 'Pay refund', 'false'), conn('Send to manager', 'Log decision'), conn('Pay refund', 'Log decision'),
    conn('Log decision', 'Reply'),
  ],
}
const constructs = (bs: Beat[]) => bs.filter((b): b is Extract<Beat, { kind: 'construct' }> => b.kind === 'construct')

describe('beatsFromOps', () => {
  it('a showcase round yields 7 construct beats in layer order, each edge on the later endpoint', () => {
    const cs = constructs(beatsFromOps(opsFromRoundShape(null, showcase)))
    expect(cs.map((c) => c.node)).toEqual(['Receive', 'Normalise', 'Over limit?', 'Send to manager', 'Pay refund', 'Log decision', 'Reply'])
    expect(cs.map((c) => c.edges.length)).toEqual([0, 1, 1, 1, 1, 2, 1])
    expect(cs[5].edges.map((e) => e.src)).toEqual(['Send to manager', 'Pay refund'])
  })

  it('a fix round orders retract, deconstruct, move, morph, construct, link', () => {
    const prev = {
      nodes: [
        { name: 'A', type: 'Webhook', position: pos(0) }, { name: 'B', type: 'Set', position: pos(190) },
        { name: 'C', type: 'Set', position: pos(380) }, { name: 'Gone', type: 'Set', position: pos(570) },
      ],
      connections: [conn('A', 'B'), conn('B', 'C'), conn('C', 'Gone')],
    }
    const shape: RoundShape = {
      round: 1, removed: ['Gone'], changed: [{ name: 'B', fields: ['value'] }],
      nodes: [
        { name: 'A', type: 'Webhook', position: pos(0) }, { name: 'B', type: 'Set', position: pos(190) },
        { name: 'C', type: 'Set', position: pos(420) }, { name: 'New', type: 'Set', position: pos(610) },
      ],
      connections: [conn('A', 'B'), conn('B', 'C'), conn('C', 'New'), conn('A', 'C')],
    }
    const bs = beatsFromOps(opsFromRoundShape(prev, shape))
    expect(bs.map((b) => b.kind)).toEqual(['retract', 'deconstruct', 'move', 'morph', 'construct', 'link'])
  })

  it('an edge between two pre-existing nodes goes to the link beat', () => {
    const prev = { nodes: [{ name: 'A', type: 'Webhook', position: pos(0) }, { name: 'B', type: 'Set', position: pos(190) }], connections: [] }
    const shape: RoundShape = { round: 1, removed: [], changed: [], nodes: prev.nodes, connections: [conn('A', 'B')] }
    const bs = beatsFromOps(opsFromRoundShape(prev, shape))
    expect(bs.map((b) => b.kind)).toEqual(['link'])
  })

  it('a back edge goes to the later node', () => {
    const shape: RoundShape = {
      round: 0, removed: [], changed: [],
      nodes: [{ name: 'A', type: 'Webhook', position: pos(0) }, { name: 'B', type: 'Set', position: pos(190) }],
      connections: [conn('A', 'B'), conn('B', 'A')],
    }
    const cs = constructs(beatsFromOps(opsFromRoundShape(null, shape)))
    expect(cs.map((c) => c.edges.length)).toEqual([0, 2])
  })

  it('folds a fail into its test beat as the first failing node on the path', () => {
    const r: TestResult = { test_id: 't', name: 'T', status: 'failed', path: ['A', 'B', 'C'], failed_nodes: ['C', 'B'] }
    const bs = beatsFromOps(opsFromTested([r]))
    expect(bs).toHaveLength(1)
    expect(bs[0]).toMatchObject({ kind: 'test', failAt: 'B' })
  })
})

const flat = (n: number): BuildOp[] => opsFromRoundShape(null, {
  round: 0, removed: [], changed: [],
  nodes: Array.from({ length: n }, (_, i) => ({ name: `N${i}`, type: 'Set', position: pos(i * 190) })),
  connections: Array.from({ length: n - 1 }, (_, i) => conn(`N${i}`, `N${i + 1}`)),
})

describe('scheduleBeats', () => {
  it('an empty segment is instant', () => {
    expect(scheduleBeats([])).toEqual({ items: [], total: 0, k: 1, compact: false })
  })

  it('7 constructs run at full scale, one period apart', () => {
    const s = scheduleBeats(beatsFromOps(flat(7)))
    expect(s.k).toBe(1)
    expect(s.compact).toBe(false)
    expect(s.total).toBeCloseTo(0.5 + 7 * 0.74 + 0.3, 5)
    expect(s.items[0].at).toBeCloseTo(beat.roundLead, 5)
    expect(s.items[1].at - s.items[0].at).toBeCloseTo(beat.period, 5)
  })

  it('15 nodes fit the cap, 40 nodes go compact, and the period never drops under ~220 ms', () => {
    const s15 = scheduleBeats(beatsFromOps(flat(15)))
    expect(s15.total).toBeLessThanOrEqual(12 + beat.tail + 1e-9)
    expect(s15.k).toBeGreaterThan(0.9)
    const s40 = scheduleBeats(beatsFromOps(flat(40)))
    expect(s40.compact).toBe(true)
    expect(s40.items[1].at - s40.items[0].at).toBeGreaterThanOrEqual(0.22 - 1e-9)
    const s200 = scheduleBeats(beatsFromOps(flat(200)))
    expect(s200.items[1].at - s200.items[0].at).toBeGreaterThanOrEqual(0.22 - 1e-9)
  })

  it('speed divides every time', () => {
    const a = scheduleBeats(beatsFromOps(flat(7)))
    const b = scheduleBeats(beatsFromOps(flat(7)), { speed: 1.4 })
    expect(b.total).toBeCloseTo(a.total / 1.4, 5)
    expect(b.items[3].at).toBeCloseTo(a.items[3].at / 1.4, 5)
  })

  it('the test segment fits testCap + failHold and sets failAt to the first failing node', () => {
    const results: TestResult[] = Array.from({ length: 12 }, (_, i) => ({
      test_id: `t${i}`, name: `T${i}`, status: i === 3 ? 'failed' : 'passed',
      path: Array.from({ length: 8 }, (_, j) => `N${j}`), failed_nodes: i === 3 ? ['N5', 'N2'] : [],
    }))
    const bs = beatsFromOps(opsFromTested(results))
    const s = scheduleBeats(bs)
    const tests = s.items.filter((i) => i.beat.kind === 'test')
    expect(tests.find((i) => (i.beat as Extract<Beat, { kind: 'test' }>).failAt)).toBeDefined()
    expect((tests[3].beat as Extract<Beat, { kind: 'test' }>).failAt).toBe('N2')
    expect(s.total).toBeLessThanOrEqual(beat.testCap + beat.failHold + beat.testGap * 2 + beat.tail + 1e-9)
    expect(tests.filter((i) => i.probe).length).toBe(2)
    expect(tests.every((i) => (i.hop ?? 0) === 0 || i.hop! >= beat.minHop - 1e-9)).toBe(true)
  })

  it('a few short tests all get a probe', () => {
    const results: TestResult[] = [
      { test_id: 'a', name: 'A', status: 'passed', path: ['X', 'Y', 'Z'], failed_nodes: [] },
      { test_id: 'b', name: 'B', status: 'failed', path: ['X', 'Y'], failed_nodes: ['Y'] },
    ]
    const s = scheduleBeats(beatsFromOps(opsFromTested(results)))
    expect(s.items.map((i) => i.probe)).toEqual([true, true])
    expect(s.items[1].at).toBeGreaterThan(s.items[0].at)
  })
})
