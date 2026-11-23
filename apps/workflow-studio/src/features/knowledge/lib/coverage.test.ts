import { describe, expect, it } from 'vitest'
import { coverageColumns, eventConcepts, groupByDay, heatLevel, sortRows, triggerLabel } from './coverage'
import type { KgLearnEvent, KgMatrixRow } from './types'

const row = (label: string, by: Record<string, number>, total: number, gap: boolean): KgMatrixRow => ({
  concept: { id: label, key: `concept:${label}`, label, kind: 'concept', subkind: null },
  implemented_by: by, implemented_total: total, patterns: 0, validated: 0, apps: 0, teams: [], roles: [], gap,
})
const rows = [
  row('Report', { node_type: 2, template: 1 }, 5, false),
  row('Approval', {}, 0, true),
  row('Schedule', { node_type: 1 }, 1, false),
]

describe('coverage', () => {
  it('orders columns by use', () => {
    expect(coverageColumns(rows)).toEqual(['node_type', 'template'])
  })

  it('maps counts to 0..4 heat', () => {
    expect(heatLevel(0, 5)).toBe(0)
    expect(heatLevel(1, 100)).toBe(1)
    expect(heatLevel(5, 5)).toBe(4)
    expect(heatLevel(3, 0)).toBe(0)
  })

  it('sorts gaps first, by coverage or by name, without mutating', () => {
    expect(sortRows(rows, 'gaps').map((r) => r.concept.label)).toEqual(['Approval', 'Schedule', 'Report'])
    expect(sortRows(rows, 'coverage').map((r) => r.concept.label)).toEqual(['Report', 'Schedule', 'Approval'])
    expect(sortRows(rows, 'name').map((r) => r.concept.label)).toEqual(['Approval', 'Report', 'Schedule'])
    expect(rows[0].concept.label).toBe('Report')
  })
})

const ev = (id: number, at: string, payload: Record<string, unknown> = {}): KgLearnEvent => ({
  id, at, project_id: 'p', team_id: null, actor: null, trigger: 'rebuild', proposal_id: null, summary: '', payload,
})

describe('memory', () => {
  it('labels the engine triggers and de-snakes unknown ones', () => {
    expect(triggerLabel('kept_workflow')).toBe('Kept a workflow')
    expect(triggerLabel('seed')).toBe('Seeded')
    expect(triggerLabel('auto_merge')).toBe('Auto merge')
    expect(triggerLabel('')).toBe('Event')
  })

  it('reads concept keys from the payload', () => {
    expect(eventConcepts(ev(1, '', { concepts: ['a', 'a', 3, 'b'] }))).toEqual(['a', 'b'])
    expect(eventConcepts(ev(1, ''))).toEqual([])
  })

  it('groups consecutive events by day', () => {
    const now = new Date(2026, 9, 18, 15).getTime()
    const g = groupByDay([
      ev(3, new Date(2026, 9, 18, 9).toISOString()),
      ev(2, new Date(2026, 9, 18, 1).toISOString()),
      ev(1, new Date(2026, 9, 17, 20).toISOString()),
      ev(0, new Date(2026, 9, 10, 20).toISOString()),
    ], now)
    expect(g.map((d) => [d.day.length > 0, d.items.length])).toEqual([[true, 2], [true, 1], [true, 1]])
    expect(g[0].day).toBe('Today')
    expect(g[1].day).toBe('Yesterday')
  })
})
