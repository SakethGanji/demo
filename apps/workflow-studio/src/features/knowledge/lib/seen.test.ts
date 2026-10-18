import { afterEach, describe, expect, it, vi } from 'vitest'
import { diffSeen, readSeen, writeSeen, type Seen } from './seen'
import type { MatrixTotals } from '../types'

const totals = (o: Partial<MatrixTotals> = {}): MatrixTotals => ({ concepts: 22, implemented: 14, gaps: 8, patterns: 5, validated: 7, apps: 3, subworkflows: 9, ...o })
const prev: Seen = { totals: totals(), lastMemoryId: 10, gapKeys: ['concept:escalation', 'concept:sla'] }

describe('seen', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows nothing on a first visit', () => {
    expect(diffSeen(null, { totals: totals(), memoryIds: [11], gapKeys: [] })).toBeNull()
  })

  it('names a gap that closed, with the concept label', () => {
    const d = diffSeen(prev, { totals: totals({ gaps: 7, implemented: 15 }), memoryIds: [10], gapKeys: ['concept:sla'] }, () => 'Escalation')
    expect(d?.text).toBe('Escalation is no longer a gap')
    expect(d?.closedGaps).toEqual(['concept:escalation'])
    expect(d?.changed.gaps).toBe(true)
    expect(d?.changed.apps).toBe(false)
  })

  it('prefers a closed gap, then patterns, then apps, then new memories', () => {
    expect(diffSeen(prev, { totals: totals({ patterns: 6 }), memoryIds: [11, 12], gapKeys: prev.gapKeys })?.text).toBe('+1 pattern')
    expect(diffSeen(prev, { totals: totals({ apps: 5 }), memoryIds: [], gapKeys: prev.gapKeys })?.text).toBe('+2 apps')
    expect(diffSeen(prev, { totals: totals(), memoryIds: [11, 12, 9], gapKeys: prev.gapKeys })?.text).toBe('2 new memories')
    expect(diffSeen(prev, { totals: totals(), memoryIds: [9], gapKeys: prev.gapKeys })?.text).toBeNull()
  })

  it('counts several closed gaps together', () => {
    expect(diffSeen(prev, { totals: totals(), memoryIds: [], gapKeys: [] })?.text).toBe('2 gaps closed')
  })

  it('round-trips through storage and survives a broken or blocked one', () => {
    const store = new Map<string, string>()
    vi.stubGlobal('localStorage', { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) })
    writeSeen('p1', prev)
    expect(readSeen('p1')).toEqual(prev)
    store.set('kg:seen:p2', '{not json')
    expect(readSeen('p2')).toBeNull()
    vi.stubGlobal('localStorage', { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') } })
    expect(readSeen('p1')).toBeNull()
    expect(() => writeSeen('p1', prev)).not.toThrow()
  })
})
