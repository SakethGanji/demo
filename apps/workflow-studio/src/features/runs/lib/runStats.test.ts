import { describe, expect, it } from 'vitest'
import type { RunListItem } from '@/shared/api/types'
import { HOUR, RANGES, dayStats, exactTime, isFailed, smoothPath } from './runStats'

const NOW = Date.parse('2026-10-15T12:00:00Z')
const iso = (msAgo: number) => new Date(NOW - msAgo).toISOString()
const run = (agoMs: number, status = 'success', duration_ms: number | null = 100) => ({ id: `r${agoMs}-${status}`, status, start_time: iso(agoMs), duration_ms }) as unknown as RunListItem

describe('isFailed', () => {
  it('treats failed, error, crashed and canceled as failures', () => {
    for (const s of ['failed', 'error', 'crashed', 'canceled']) expect(isFailed(s)).toBe(true)
    for (const s of ['success', 'running', 'waiting', null, undefined]) expect(isFailed(s)).toBe(false)
  })
})

describe('RANGES', () => {
  it('are ordered from shortest to unbounded', () => {
    expect(RANGES.map((r) => r.key)).toEqual(['1h', '24h', '7d', 'all'])
    expect(RANGES[0].ms).toBe(HOUR)
    expect(RANGES[3].ms).toBe(Infinity)
  })
})

describe('dayStats', () => {
  it('buckets the last 24 hours by hour, newest in the last bucket', () => {
    const s = dayStats([run(10 * 60_000), run(30 * 60_000, 'failed'), run(5 * HOUR + 60_000)], NOW, false)
    expect(s.buckets).toHaveLength(24)
    expect(s.buckets[23]).toMatchObject({ count: 2, failed: 1 })
    expect(s.buckets[18].count).toBe(1)
    expect(s.count).toBe(3)
    expect(s.failed).toBe(1)
  })
  it('ignores runs older than 24h or in the future', () => {
    const s = dayStats([run(25 * HOUR), run(24 * HOUR), run(-5 * 60_000)], NOW, false)
    expect(s.count).toBe(0)
  })
  it('takes the lower median and the most recent failure', () => {
    const s = dayStats([run(60_000, 'success', 10), run(120_000, 'failed', 40), run(180_000, 'failed', 20), run(240_000, 'success', 30), run(300_000, 'success', null)], NOW, false)
    expect(s.p50).toBe(20) // [10,20,30,40] → index 1
    expect(s.lastFailed?.id).toBe(run(120_000, 'failed').id)
    expect(dayStats([], NOW, false).p50).toBeNull()
  })
  it('is capped when more pages exist, or when 100 runs all sit inside the window', () => {
    expect(dayStats([run(60_000)], NOW, true).capped).toBe(true)
    expect(dayStats([run(60_000)], NOW, false).capped).toBe(false)
    expect(dayStats(Array.from({ length: 100 }, (_, i) => run((i + 1) * 60_000)), NOW, false).capped).toBe(true)
    expect(dayStats([...Array.from({ length: 99 }, (_, i) => run((i + 1) * 60_000)), run(30 * HOUR)], NOW, false).capped).toBe(false)
  })
})

describe('smoothPath', () => {
  it('is empty for no points and starts at the first point otherwise', () => {
    expect(smoothPath([])).toBe('')
    expect(smoothPath([[0, 5]])).toBe('M0 5.0')
    const d = smoothPath([[0, 0], [10, 10], [20, 0]])
    expect(d.startsWith('M0 0.0 C')).toBe(true)
    expect(d.match(/C/g)).toHaveLength(2)
    expect(d.endsWith('20 0.0')).toBe(true)
  })
})

describe('exactTime', () => {
  it('is a dash without a timestamp and a 24-hour stamp with seconds otherwise', () => {
    expect(exactTime(null)).toBe('—')
    expect(exactTime('2026-10-15T09:23:10')).toMatch(/^Oct 15, 09:23:10$/)
  })
})
