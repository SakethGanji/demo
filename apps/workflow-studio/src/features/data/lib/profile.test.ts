import { beforeAll, describe, expect, it, vi } from 'vitest'
import type { Column, Row } from '@/shared/api/types'

// profile.ts → cells.ts reaches shared/lib/config, which reads window.location at import time
vi.stubGlobal('window', { location: { hostname: 'localhost' } })
let fmtNum: typeof import('./profile').fmtNum
let profileColumn: typeof import('./profile').profileColumn
let profileCreated: typeof import('./profile').profileCreated
let profileIds: typeof import('./profile').profileIds
beforeAll(async () => {
  ;({ fmtNum, profileColumn, profileCreated, profileIds } = await import('./profile'))
})

const row = (id: number, data: Record<string, unknown>, created_at = '2026-10-15T10:00:00Z') => ({ id, data, created_at }) as unknown as Row
const col = (name: string, type: string) => ({ name, type }) as unknown as Column

describe('profileIds', () => {
  it('counts unique ids', () => {
    expect(profileIds([row(1, {}), row(2, {}), row(2, {})])).toEqual({ kind: 'unique', caption: '2 unique' })
  })
})

describe('profileColumn', () => {
  it('shows a dash for an empty table and "no values" when every cell is empty', () => {
    expect(profileColumn(col('a', 'string'), [])).toEqual({ kind: 'none', caption: '—' })
    expect(profileColumn(col('a', 'string'), [row(1, { a: null }), row(2, { a: '' })])).toEqual({ kind: 'none', caption: 'no values' })
  })
  it('bins numbers into five and captions the range', () => {
    const p = profileColumn(col('n', 'number'), [0, 1, 2, 3, 4, 10].map((n, i) => row(i, { n })))
    expect(p.kind).toBe('hist')
    if (p.kind !== 'hist') return
    expect(p.bins).toHaveLength(5)
    expect(p.bins.reduce((a, b) => a + b, 0)).toBe(6)
    expect(p.bins[4]).toBe(1) // the maximum lands in the last bin
    expect(p.caption).toBe('0 – 10')
  })
  it('a constant numeric column is one value', () => {
    const p = profileColumn(col('n', 'integer'), [row(1, { n: 7 }), row(2, { n: 7 })])
    expect(p).toMatchObject({ kind: 'hist', caption: '7', bins: [2, 0, 0, 0, 0] })
  })
  it('summarises a low-cardinality string column as a bar led by the most common value', () => {
    const p = profileColumn(col('reason', 'string'), [row(1, { reason: 'late' }), row(2, { reason: 'late' }), row(3, { reason: 'damaged' })])
    expect(p).toEqual({
      kind: 'bar',
      segments: [
        { label: 'late', count: 2 },
        { label: 'damaged', count: 1 },
      ],
      caption: '2 values · late 67%',
    })
  })
  it('calls an all-distinct string column unique (but keeps tiny ones as bars)', () => {
    expect(profileColumn(col('s', 'string'), ['a', 'b', 'c', 'd'].map((s, i) => row(i, { s })))).toEqual({ kind: 'unique', caption: '4 unique' })
    expect(profileColumn(col('s', 'string'), ['a', 'b'].map((s, i) => row(i, { s }))).kind).toBe('bar')
    expect(profileColumn(col('s', 'string'), Array.from({ length: 14 }, (_, i) => row(i, { s: `v${i % 13}` }))).kind).toBe('unique')
  })
  it('counts JSON values without looking inside', () => {
    expect(profileColumn(col('j', 'json'), [row(1, { j: { a: 1 } })])).toEqual({ kind: 'none', caption: '1 value' })
  })
})

describe('profileCreated', () => {
  const now = Date.parse('2026-10-15T18:00:00Z')
  it('puts same-day rows on the day strip', () => {
    const p = profileCreated([row(1, {}, '2026-10-15T06:00:00Z'), row(2, {}, '2026-10-15T12:00:00Z')], now)
    expect(p).toMatchObject({ kind: 'ticks', caption: 'all today' })
    if (p.kind === 'ticks') expect(p.marks).toEqual([0.25, 0.5])
  })
  it('names the day when the rows are older', () => {
    expect(profileCreated([row(1, {}, '2026-10-12T06:00:00Z')], now)).toMatchObject({ caption: 'all Oct 12' })
  })
  it('spreads rows across the span otherwise', () => {
    const p = profileCreated([row(1, {}, '2026-10-10T00:00:00Z'), row(2, {}, '2026-10-12T00:00:00Z'), row(3, {}, '2026-10-14T00:00:00Z')], now)
    expect(p).toMatchObject({ kind: 'ticks', caption: 'Oct 10 – Oct 14' })
    if (p.kind === 'ticks') expect(p.marks).toEqual([0, 0.5, 1])
  })
  it('treats zone-less engine timestamps as UTC and ignores unparseable ones', () => {
    expect(profileCreated([row(1, {}, '2026-10-15T06:00:00')], now)).toMatchObject({ caption: 'all today' })
    expect(profileCreated([row(1, {}, 'garbage')], now)).toEqual({ kind: 'none', caption: '—' })
    expect(profileCreated([], now)).toEqual({ kind: 'none', caption: '—' })
  })
})

describe('fmtNum', () => {
  it('rounds to two decimals without trailing zeros', () => {
    expect(fmtNum(1.005)).toBe('1')
    expect(fmtNum(2.5)).toBe('2.5')
    expect(fmtNum(3.14159)).toBe('3.14')
  })
})
