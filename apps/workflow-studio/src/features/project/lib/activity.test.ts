import { describe, expect, it } from 'vitest'
import { toActivity } from './activity'

describe('toActivity', () => {
  it('maps per-day rows to grid cells, oldest first, and sums failures', () => {
    const a = toActivity([
      { date: '2026-10-16', total: 4, failed: 1 },
      { date: '2026-10-14', total: 0, failed: 0 },
      { date: '2026-10-15', total: 7, failed: 2 },
    ])
    expect(a.days).toEqual([
      { date: '2026-10-14', count: 0, failed: 0 },
      { date: '2026-10-15', count: 7, failed: 2 },
      { date: '2026-10-16', count: 4, failed: 1 },
    ])
    expect(a.failed).toBe(3)
    expect(a.total).toBe(11)
  })
  it('handles an empty response', () => {
    expect(toActivity([])).toEqual({ days: [], failed: 0, total: 0 })
  })
})
