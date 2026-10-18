import { describe, expect, it } from 'vitest'
import { parseRoundShape, parseTestResults, roundPlansFromEvents } from './opsFromEvents'
import type { AiEvent } from '../types'

const ev = (seq: number, event: string, payload: Record<string, unknown>): AiEvent => ({ seq, at: '', event, payload, actor: null })

describe('parseRoundShape', () => {
  it('reads the engine payload', () => {
    const s = parseRoundShape({
      round: 1,
      nodes: [{ name: 'A', type: 'Webhook', position: { x: 1, y: 2 } }, { name: 'B', type: 'Set' }, { type: 'bad' }],
      connections: [['A', 'main', 'B', 'main'], ['short'], 'junk'],
      changed: [{ name: 'B', fields: ['condition', 3] }, { fields: [] }],
      removed: ['Old', 7],
    })
    expect(s).toEqual({
      round: 1,
      nodes: [{ name: 'A', type: 'Webhook', position: { x: 1, y: 2 } }, { name: 'B', type: 'Set', position: { x: 0, y: 0 } }],
      connections: [['A', 'main', 'B', 'main']],
      changed: [{ name: 'B', fields: ['condition'] }],
      removed: ['Old'],
    })
  })
  it('is null for anything that is not a shape', () => {
    expect(parseRoundShape({})).toBeNull()
    expect(parseRoundShape({ round: 0 })).toBeNull()
    expect(parseRoundShape({ round: 'x', nodes: [] })).toBeNull()
  })
})

describe('parseTestResults', () => {
  it('reads results and maps the engine\'s "error" to errored', () => {
    const r = parseTestResults({ round: 0, results: [
      { test_id: 't1', name: 'One', status: 'passed', path: ['A', 'B'], failed_nodes: [] },
      { test_id: 't2', name: 'Two', status: 'error', path: ['A'], failed_nodes: ['A'] },
      { name: 'no id' }, 'junk',
    ] })
    expect(r.map((x) => [x.test_id, x.status, x.path])).toEqual([['t1', 'passed', ['A', 'B']], ['t2', 'errored', ['A']]])
  })
  it('an older engine has no results', () => {
    expect(parseTestResults({ round: 0, passed: 3 })).toEqual([])
  })
})

describe('roundPlansFromEvents', () => {
  it('folds shapes and tests by round, ordered', () => {
    const plans = roundPlansFromEvents([
      ev(1, 'round_started', { round: 0 }),
      ev(2, 'round_shape', { round: 1, nodes: [], connections: [], changed: [], removed: [] }),
      ev(3, 'round_shape', { round: 0, nodes: [{ name: 'A', type: 'Set', position: { x: 0, y: 0 } }], connections: [], changed: [], removed: [] }),
      ev(4, 'tested', { round: 0, results: [{ test_id: 't', name: 'T', status: 'failed', path: ['A'], failed_nodes: ['A'] }] }),
      ev(5, 'checked', { round: 0 }),
    ])
    expect(plans.map((p) => [p.round, p.shape?.nodes.length ?? null, p.tests.length])).toEqual([[0, 1, 1], [1, 0, 0]])
  })
})
