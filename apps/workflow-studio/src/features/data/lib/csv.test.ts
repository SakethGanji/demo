import { beforeAll, describe, expect, it, vi } from 'vitest'
import type { Column } from '@/shared/api/types'

// csv.ts → cells.ts reaches shared/lib/config, which reads window.location at import time
vi.stubGlobal('window', { location: { hostname: 'localhost' } })
let badRowIndexes: typeof import('./csv').badRowIndexes
let chunk: typeof import('./csv').chunk
let IMPORT_CHUNK: number
let planImport: typeof import('./csv').planImport
beforeAll(async () => {
  ;({ badRowIndexes, chunk, IMPORT_CHUNK, planImport } = await import('./csv'))
})

const cols = [
  { name: 'email', type: 'string', required: true },
  { name: 'n', type: 'integer' },
  { name: 'meta', type: 'json' },
] as unknown as Column[]

describe('chunk', () => {
  it('splits into fixed-size parts and keeps order', () => {
    const parts = chunk(Array.from({ length: 1201 }, (_, i) => i))
    expect(IMPORT_CHUNK).toBe(500)
    expect(parts.map((p) => p.length)).toEqual([500, 500, 201])
    expect(parts.flat()[1200]).toBe(1200)
  })
  it('returns nothing for nothing and one part when it fits', () => {
    expect(chunk([])).toEqual([])
    expect(chunk([1, 2, 3], 10)).toEqual([[1, 2, 3]])
  })
})

describe('planImport', () => {
  it('separates convertible rows from local failures, numbering CSV lines from the header = 1', () => {
    const plan = planImport([['a@x.io', '1', '{}'], ['b@x.io', '2', '{oops'], ['c@x.io', '', '']], ['email', 'n', 'meta'], cols)
    expect(plan.valid.map((r) => [r.line, r.data])).toEqual([
      [2, { email: 'a@x.io', n: 1, meta: {} }],
      [4, { email: 'c@x.io' }],
    ])
    expect(plan.failed).toHaveLength(1)
    expect(plan.failed[0].line).toBe(3)
    expect(plan.failed[0].error).toMatch(/^meta:/)
  })
})

describe('badRowIndexes', () => {
  it('dedupes by row, ignores out-of-range and malformed entries', () => {
    const bad = badRowIndexes(
      [
        { row: 1, column: 'n', error: 'expected integer' },
        { row: 1, column: 'email', error: 'is required' },
        { row: 9, column: 'n', error: 'x' },
        { column: 'n' },
      ],
      3,
    )
    expect(bad).toEqual([{ index: 1, error: 'n expected integer' }])
    expect(badRowIndexes(undefined, 3)).toEqual([])
  })
})
