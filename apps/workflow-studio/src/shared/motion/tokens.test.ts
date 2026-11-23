import { describe, expect, it } from 'vitest'
import { beat, roundCap, dur, ease, opDuration, round, spring, stagger } from './tokens'

describe('motion tokens', () => {
  it('mirror the CSS durations and the plan', () => {
    expect([dur.instant, dur.fast, dur.base, dur.slow]).toEqual([0.08, 0.14, 0.2, 0.28])
    expect(round.budget).toBe(3.5)
    expect(stagger.op).toBe(0.09)
    expect(ease.out).toEqual([0.16, 1, 0.3, 1])
    expect(ease.in).toEqual([0.4, 0, 1, 1])
    expect(spring.firm.damping).toBeGreaterThan(spring.snap.damping)
  })
  it('every op kind has a duration that fits a round', () => {
    for (const v of Object.values(opDuration)) expect(v).toBeLessThan(round.budget)
  })
  it('roundCap scales with the node count and is clamped', () => {
    expect(roundCap(7)).toBeCloseTo(6.68, 2)
    expect(roundCap(15)).toBe(12)
    expect(roundCap(0)).toBe(3.5)
    expect(beat.lockAt).toBeLessThan(beat.period)
  })
})
