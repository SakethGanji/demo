import { describe, expect, it } from 'vitest'
import type { RunListItem } from '@/shared/api/types'
import { DAY_MS, entityKey, entityStatuses, initials, urgency, type ProjectEntity } from './entities'

const NOW = Date.parse('2026-10-20T12:00:00')
const iso = (msAgo: number) => new Date(NOW - msAgo).toISOString()

function run(p: Partial<RunListItem> & { id: string; workflow_id: string; status: string; msAgo: number }): RunListItem {
  const { msAgo, ...rest } = p
  return {
    workflow_name: 'x',
    start_time: iso(msAgo),
    end_time: null,
    error_count: 0,
    environment: 'dev',
    trigger: 'manual',
    route_id: null,
    route: null,
    app_id: null,
    duration_ms: 100,
    status_code: null,
    ...rest,
  } as RunListItem
}

const wf = (id: string, updatedAt = '2026-10-01T00:00:00'): ProjectEntity => ({ kind: 'workflow', id, name: `Flow ${id}`, updatedAt })
const app = (id: string, updatedAt = '2026-10-01T00:00:00'): ProjectEntity => ({ kind: 'app', id, name: `App ${id}`, updatedAt })

describe('entityStatuses', () => {
  it('a workflow owns its runs, an app the runs it triggered; failed = the newest run today errored', () => {
    const runs = [
      run({ id: 'r1', workflow_id: 'w1', status: 'error', msAgo: 60_000 }),
      run({ id: 'r2', workflow_id: 'w1', status: 'success', msAgo: 120_000 }),
      run({ id: 'r3', workflow_id: 'w2', status: 'success', msAgo: 60_000 }),
      run({ id: 'r4', workflow_id: 'w2', status: 'error', msAgo: 120_000 }),
      run({ id: 'r5', workflow_id: 'route-wf', app_id: 'a1', status: 'running', msAgo: 5_000 }),
      run({ id: 'r6', workflow_id: 'w3', status: 'error', msAgo: DAY_MS + 1000 }),
    ]
    const st = entityStatuses([wf('w1'), wf('w2'), wf('w3'), app('a1')], runs, NOW, { w2: 2 })
    expect(st.get(entityKey('workflow', 'w1'))).toMatchObject({ failed: true, running: false })
    expect(st.get(entityKey('workflow', 'w1'))!.last!.id).toBe('r1')
    expect(st.get(entityKey('workflow', 'w2'))).toMatchObject({ failed: false, approvals: 2 })
    expect(st.get(entityKey('workflow', 'w2'))!.runs24h.map((r) => r.id)).toEqual(['r3', 'r4'])
    expect(st.get(entityKey('app', 'a1'))).toMatchObject({ running: true, approvals: 0 })
    // an old failure is not "failed" now, but it is still the last run
    expect(st.get(entityKey('workflow', 'w3'))).toMatchObject({ failed: false, runs24h: [] })
    expect(st.get(entityKey('workflow', 'w3'))!.last!.id).toBe('r6')
  })

  it('urgency orders failed > running > approvals > ran today > quiet', () => {
    const base = { running: false, failed: false, approvals: 0, runs24h: [], last: null }
    expect(urgency({ ...base, failed: true })).toBe(4)
    expect(urgency({ ...base, running: true })).toBe(3)
    expect(urgency({ ...base, approvals: 1 })).toBe(2)
    expect(urgency({ ...base, runs24h: [{ id: 'x', at: NOW, tone: 'ok', durationMs: 1 }] })).toBe(1)
    expect(urgency(base)).toBe(0)
    expect(urgency(undefined)).toBe(0)
  })
})

describe('initials', () => {
  it('use the first and last words', () => {
    expect(initials('Refund approval flow')).toBe('RF')
    expect(initials('qa-proof-final-3')).toBe('Q3')
    expect(initials('orders')).toBe('Or')
    expect(initials('  ')).toBe('?')
  })
})
