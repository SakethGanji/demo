import { describe, expect, it } from 'vitest'
import { DAY_MS, entityKey, emptyStatus, type ProjectEntity, type EntityStatus } from '@/features/project/lib/entities'
import { attentionItems, byStatus, clockX, headline, median } from './pulse'

const NOW = 1_000_000_000_000
const ents: ProjectEntity[] = [
  { kind: 'workflow', id: 'w1', name: 'Billing', updatedAt: '2026-10-01' },
  { kind: 'workflow', id: 'w2', name: 'Sync', updatedAt: '2026-10-03' },
  { kind: 'app', id: 'a1', name: 'Desk', updatedAt: '2026-10-02' },
]
function statuses(patch: Record<string, Partial<EntityStatus>>) {
  const m = new Map<string, EntityStatus>()
  for (const e of ents) m.set(entityKey(e.kind, e.id), { ...emptyStatus(), ...(patch[entityKey(e.kind, e.id)] ?? {}) })
  return m
}

describe('attentionItems', () => {
  it('lists failures (workflow → its run in the editor, app → the run page), approvals, secrets, stubs, then running', () => {
    const st = statuses({
      'workflow:w1': { failed: true, last: { id: 'e1', at: NOW, tone: 'err', durationMs: 5 }, runs24h: [{ id: 'e1', at: NOW, tone: 'err', durationMs: 5 }] },
      'app:a1': { failed: true, last: { id: 'e2', at: NOW, tone: 'err', durationMs: 5 }, runs24h: [{ id: 'e2', at: NOW, tone: 'err', durationMs: 5 }] },
      'workflow:w2': { running: true, last: { id: 'e3', at: NOW, tone: 'running', durationMs: null }, runs24h: [{ id: 'e3', at: NOW, tone: 'running', durationMs: null }] },
    })
    const items = attentionItems({ entities: ents, status: st, openTasks: 2, missingSecrets: 1, stubRoutes: 3, liveEnv: 'prod' })
    expect(items.map((i) => i.key)).toEqual(['failed:workflow:w1', 'failed:app:a1', 'approvals', 'secrets', 'stubs', 'running:workflow:w2'])
    expect(items[0].target).toEqual({ kind: 'workflow', id: 'w1', execution: 'e1' })
    expect(items[1].target).toEqual({ kind: 'run', id: 'e2' })
    expect(items[2].title).toBe('2 approvals waiting')
    expect(items[3].title).toBe('1 secret missing')
    expect(items[4].title).toBe('3 routes are stubs')
  })

  it('is empty when nothing needs anyone', () => {
    expect(attentionItems({ entities: ents, status: statuses({}), openTasks: 0, missingSecrets: 0, stubRoutes: 0, liveEnv: 'prod' })).toEqual([])
  })
})

describe('headline', () => {
  it('counts what needs you, not what is merely running', () => {
    expect(headline([], 0)).toEqual({ lead: 'All quiet.', tail: 'Nothing has run in the last 24 hours.' })
    expect(headline([], 4).tail).toBe('Everything that ran today succeeded.')
    const running = { key: 'r', tone: 'running' as const, title: '', sub: '', action: '', target: { kind: 'approvals' as const } }
    const warn = { ...running, key: 'w', tone: 'warn' as const }
    expect(headline([running], 1)).toEqual({ lead: 'Running.', tail: '1 live now, nothing needs you.' })
    expect(headline([warn], 1)).toEqual({ lead: '1 thing needs you.', tail: '' })
    expect(headline([warn, { ...warn, key: 'x' }, running], 1)).toEqual({ lead: '2 things need you.', tail: '1 more running.' })
  })
})

describe('byStatus, clockX, median', () => {
  it('sorts loudest first, then by newest run, then by newest edit', () => {
    const st = statuses({ 'app:a1': { approvals: 0, failed: true } })
    expect(byStatus(ents, st).map((e) => e.id)).toEqual(['a1', 'w2', 'w1'])
  })
  it('places moments on the 24h clock, clamped', () => {
    expect(clockX(NOW, NOW)).toBe(1)
    expect(clockX(NOW - DAY_MS / 2, NOW)).toBeCloseTo(0.5)
    expect(clockX(NOW - 2 * DAY_MS, NOW)).toBe(0)
    expect(clockX(NOW + 1000, NOW)).toBe(1)
  })
  it('median handles empty, odd and even', () => {
    expect(median([])).toBeNull()
    expect(median([3, 1, 2])).toBe(2)
    expect(median([4, 1, 2, 3])).toBe(2.5)
  })
})
