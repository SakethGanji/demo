import { describe, expect, it } from 'vitest'
import {
  edgeKey, opsFromDiff, opsFromRoundShape, opsFromTested, reverse, snapshotFromDefinition,
  type BuildOp, type RoundShape, type ShapeSnapshot,
} from './buildOps'
import type { ProposedDefinition } from '../types'

const n = (name: string, x: number, y = 0, type = 'Set') => ({ name, type, position: { x, y } })
const kinds = (ops: BuildOp[]) => ops.map((o) => o.kind)

describe('edgeKey', () => {
  it('matches fromBackendWorkflow ids, special characters included', () => {
    expect(edgeKey('Over limit?', 'true', 'Send to manager', 'main')).toBe('edge-Over_limit_-true-Send_to_manager-main')
  })
})

describe('opsFromRoundShape', () => {
  const prev: ShapeSnapshot = {
    nodes: [n('Keep', 0), n('Move', 100), n('Gone', 200)],
    connections: [['Keep', 'main', 'Gone', 'main'], ['Keep', 'main', 'Move', 'main']],
  }
  const shape: RoundShape = {
    round: 1,
    nodes: [n('Keep', 0), n('Move', 140, 20), n('Late', 600), n('Early', 300, 40), n('Same x lower', 300, 0)],
    connections: [['Keep', 'main', 'Move', 'main'], ['Move', 'main', 'Late', 'main'], ['Move', 'main', 'Early', 'main']],
    changed: [{ name: 'Keep', fields: ['url'] }, { name: 'Late', fields: ['new node is not a change'] }],
    removed: ['Gone'],
  }
  const ops = opsFromRoundShape(prev, shape)

  it('orders removes, moves, adds by layer, changes, edge removes, edge adds', () => {
    expect(kinds(ops)).toEqual([
      'roundStart', 'removeNode', 'moveNode', 'addNode', 'addNode', 'addNode', 'changeNode', 'removeEdge', 'addEdge', 'addEdge', 'roundEnd',
    ])
  })
  it('adds go left to right, then top to bottom', () => {
    expect(ops.filter((o) => o.kind === 'addNode').map((o) => (o as { node: string }).node)).toEqual(['Same x lower', 'Early', 'Late'])
  })
  it('keeps what reverse needs', () => {
    expect(ops.find((o) => o.kind === 'moveNode')).toMatchObject({ node: 'Move', from: { x: 100, y: 0 }, to: { x: 140, y: 20 } })
    expect(ops.find((o) => o.kind === 'removeNode')).toMatchObject({ node: 'Gone', type: 'Set', position: { x: 200, y: 0 } })
  })
  it('only changes nodes that already existed', () => {
    expect(ops.filter((o) => o.kind === 'changeNode')).toEqual([{ kind: 'changeNode', node: 'Keep', fields: ['url'] }])
  })
  it('edge ops come after every node op and name both endpoints', () => {
    const lastNode = ops.map((o) => o.kind).lastIndexOf('changeNode')
    const firstEdge = ops.findIndex((o) => o.kind === 'removeEdge' || o.kind === 'addEdge')
    expect(firstEdge).toBeGreaterThan(lastNode)
    expect(ops.find((o) => o.kind === 'removeEdge')).toMatchObject({ src: 'Keep', tgt: 'Gone' })
  })
  it('a new workflow adds everything', () => {
    const o = opsFromRoundShape(null, { round: 0, nodes: [n('A', 0), n('B', 280)], connections: [['A', 'main', 'B', 'main']], changed: [], removed: [] })
    expect(kinds(o)).toEqual(['roundStart', 'addNode', 'addNode', 'addEdge', 'roundEnd'])
  })
})

describe('reverse', () => {
  const shape: RoundShape = {
    round: 0, nodes: [n('A', 0), n('B', 280, 10)], connections: [['A', 'main', 'B', 'main']],
    changed: [{ name: 'A', fields: ['x'] }], removed: [],
  }
  const prev: ShapeSnapshot = { nodes: [n('A', 0), n('B', 200), n('Old', 500)], connections: [['A', 'main', 'Old', 'main']] }
  const ops = [
    ...opsFromRoundShape(prev, shape),
    ...opsFromTested([{ test_id: 't1', name: 'T', status: 'failed', path: ['A', 'B'], failed_nodes: ['B'] }]),
  ]
  it('is an involution', () => {
    expect(reverse(reverse(ops))).toEqual(ops)
    expect(reverse(reverse([]))).toEqual([])
  })
  it('puts removals back and takes additions away, last first', () => {
    const r = reverse(opsFromRoundShape(prev, shape))
    expect(r[0]).toMatchObject({ kind: 'roundStart' })
    expect(r.find((o) => o.kind === 'addNode')).toMatchObject({ node: 'Old', type: 'Set', position: { x: 500, y: 0 } })
    expect(r.find((o) => o.kind === 'removeEdge')).toMatchObject({ src: 'A', tgt: 'B' })
    expect(r.find((o) => o.kind === 'moveNode')).toMatchObject({ node: 'B', to: { x: 200, y: 0 }, from: { x: 280, y: 10 } })
    expect(kinds(r).indexOf('removeEdge')).toBeLessThan(kinds(r).indexOf('addNode'))
  })
})

describe('opsFromTested', () => {
  it('one testRun per result, in order, and one fail per newly failing node set', () => {
    const ops = opsFromTested([
      { test_id: 'a', name: 'A', status: 'passed', path: ['T', 'X'], failed_nodes: [] },
      { test_id: 'b', name: 'B', status: 'failed', path: ['T', 'Y'], failed_nodes: ['Y'] },
      { test_id: 'c', name: 'C', status: 'errored', path: ['T', 'Y'], failed_nodes: ['Y'] },
    ])
    expect(kinds(ops)).toEqual(['testRun', 'testRun', 'fail', 'testRun'])
    expect(ops[2]).toEqual({ kind: 'fail', nodes: ['Y'] })
    expect(ops[1]).toMatchObject({ testId: 'b', path: ['T', 'Y'], status: 'failed', failedNodes: ['Y'] })
  })
})

describe('opsFromDiff (older proposals without round_shape)', () => {
  const def = (names: string[], conns: [string, string][] = []) => ({
    nodes: names.map((x, i) => ({ name: x, type: 'Set', position: { x: i * 100, y: 0 } })),
    connections: conns.map(([s, t]) => ({ source_node: s, source_output: 'main', target_node: t, target_input: 'main' })),
  }) as unknown as ProposedDefinition
  it('plays the whole change as one round', () => {
    const ops = opsFromDiff(def(['A', 'Old']), def(['A', 'B'], [['A', 'B']]))
    expect(kinds(ops)).toEqual(['roundStart', 'removeNode', 'addNode', 'addEdge', 'roundEnd'])
    expect(snapshotFromDefinition(null).nodes).toEqual([])
  })
})
