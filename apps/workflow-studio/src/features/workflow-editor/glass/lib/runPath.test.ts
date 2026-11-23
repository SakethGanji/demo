import { describe, expect, it } from 'vitest'
import type { ApiExecutionDetail } from '@/shared/lib/api'
import { formatMs, outcomeOf, portTaken, runPath, statusOf } from './runPath'

const conns = [
  { source_node: 'Refund request', source_output: 'main', target_node: 'Normalize request' },
  { source_node: 'Normalize request', source_output: 'main', target_node: 'Amount > 500?' },
  { source_node: 'Amount > 500?', source_output: 'true', target_node: 'Ask risk API' },
  { source_node: 'Ask risk API', source_output: 'main', target_node: 'Log escalated refund' },
  { source_node: 'Amount > 500?', source_output: 'false', target_node: 'Log auto-approved refund' },
]
const names = ['Refund request', 'Normalize request', 'Amount > 500?', 'Ask risk API', 'Log escalated refund', 'Log auto-approved refund']

/** Shaped like the seeded failed refund run (amount 900, fails at the risk API). */
const failed: ApiExecutionDetail = {
  id: 'ex1',
  workflow_id: 'wf',
  workflow_name: 'Refund triage',
  status: 'failed',
  mode: 'webhook',
  start_time: '2026-10-24T10:00:00.000Z',
  end_time: '2026-10-24T10:00:01.500Z',
  errors: [{ node_name: 'Ask risk API', error: 'HTTP 500' }],
  node_data: {
    'Refund request': [{ json: { amount: 900 } }],
    'Normalize request': [{ json: { amount: 900 } }],
    'Amount > 500?': [{ json: { amount: 900 } }],
  },
  node_metrics: {
    'Refund request': { executionOrder: 0, executionTimeMs: 1, activeOutputs: ['main'] },
    'Normalize request': { executionOrder: 1, executionTimeMs: 2, activeOutputs: ['main'] },
    'Amount > 500?': { executionOrder: 2, executionTimeMs: 1, activeOutputs: ['true'], branchDecision: 'true' },
    'Ask risk API': { executionOrder: 3, executionTimeMs: 40, status: 'error' },
  },
}

describe('runPath', () => {
  it('marks what ran, what failed and what the run never reached', () => {
    const p = runPath(conns, names, failed)!
    expect(names.map((n) => statusOf(p, n))).toEqual(['ok', 'ok', 'ok', 'fail', 'skip', 'skip'])
    expect(p.ran).toEqual(['Refund request', 'Normalize request', 'Amount > 500?', 'Ask risk API'])
    expect(p.byNode['Ask risk API'].error).toBe('HTTP 500')
    expect(p.tookMs).toBe(1500)
    expect(outcomeOf(p)).toEqual({ label: 'Failed at Ask risk API', tone: 'bad' })
  })

  it('knows which port a branch took', () => {
    const p = runPath(conns, names, failed)
    expect(portTaken(p, 'Amount > 500?', 'true')).toBe(true)
    expect(portTaken(p, 'Amount > 500?', 'false')).toBe(false)
    expect(p!.byNode['Amount > 500?'].decision).toBe('true')
  })

  it('infers ports from the targets that ran when metrics have no activeOutputs', () => {
    const old: ApiExecutionDetail = { ...failed, status: 'success', errors: [], node_metrics: undefined, node_data: { ...failed.node_data, 'Log auto-approved refund': [{ json: {} }] } }
    const p = runPath(conns, names, old)
    expect(portTaken(p, 'Amount > 500?', 'false')).toBe(true)
    expect(portTaken(p, 'Amount > 500?', 'true')).toBe(false)
    expect(outcomeOf(p!).tone).toBe('ok')
  })

  it('a run still going leaves unreached steps pending; a waiting step needs you', () => {
    const p = runPath(conns, names, { ...failed, status: 'waiting', errors: [], end_time: null, node_metrics: { ...failed.node_metrics, 'Ask risk API': { status: 'waiting' } } })!
    expect(statusOf(p, 'Ask risk API')).toBe('need')
    expect(statusOf(p, 'Log escalated refund')).toBe('pending')
    expect(p.tookMs).toBeNull()
  })

  it('no run: everything pending and no ports', () => {
    expect(runPath(conns, names, null)).toBeNull()
    expect(statusOf(null, 'Amount > 500?')).toBe('pending')
    expect(portTaken(null, 'Amount > 500?', 'true')).toBeNull()
  })

  it('reads the error port on an error-routed step', () => {
    const p = runPath(conns, names, { ...failed, errors: [], node_metrics: { 'Ask risk API': { activeOutputs: [], errorRouted: true, error: 'boom' } } })!
    expect(p.byNode['Ask risk API'].ports).toContain('error')
  })
})

describe('formatMs', () => {
  it('formats durations', () => {
    expect(formatMs(84)).toBe('84 ms')
    expect(formatMs(1234)).toBe('1.2 s')
    expect(formatMs(75_000)).toBe('1m 15s')
    expect(formatMs(2 * 3600_000 + 3 * 60_000)).toBe('2h 03m')
    expect(formatMs(null)).toBe('')
  })
})
