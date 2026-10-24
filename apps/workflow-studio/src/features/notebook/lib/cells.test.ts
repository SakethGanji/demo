import { describe, expect, it } from 'vitest'
import { countOnly, refLabel, valueText } from './cells'
import type { Cell } from '../types'

const cell = (p: Partial<Cell>) => ({ name: 'c1', kind: 'query', title: null, spec: {}, children: [], latest: null, ...p }) as unknown as Cell

describe('refLabel', () => {
  const cells = [
    cell({ name: 'c2', kind: 'data', title: 'Settlement window', spec: { rows: [{ max_age_days: 1 }] } as never }),
    cell({ name: 'c3', kind: 'query', title: 'Stuck wires' }),
    cell({ name: 'c4', kind: 'data', title: 'Two rows', spec: { rows: [{ a: 1 }, { a: 2 }] } as never }),
    cell({ name: 'c5', kind: 'for_each', title: 'Each', children: [cell({ name: 'c6', title: 'Inner' })] }),
  ]
  it('names the referenced cell by its title', () => {
    expect(refLabel('{{cells.c3.rows}}', cells)).toBe('Stuck wires · rows')
    expect(refLabel('{{cells.c6.count}}', cells)).toBe('Inner · count')
  })
  it('drops rows[0]. only for a single-row data cell', () => {
    expect(refLabel('{{cells.c2.rows[0].max_age_days}}', cells)).toBe('Settlement window · max_age_days')
    expect(refLabel('{{cells.c4.rows[0].a}}', cells)).toBe('Two rows · rows[0].a')
    expect(refLabel('{{cells.c3.rows[0].id}}', cells)).toBe('Stuck wires · rows[0].id')
  })
  it('falls back to the raw id without a title or cell list', () => {
    expect(refLabel('{{cells.c9.rows}}', cells)).toBe('c9 · rows')
    expect(refLabel('{{cells.c3.rows[0].id}}')).toBe('c3 · rows[0].id')
    expect(refLabel('{{row.id}}', cells)).toBe('row · id')
    expect(valueText('{{cells.c3.rows}}', [cell({ name: 'c3', title: '  ' })])).toBe('c3 · rows')
  })
})

describe('countOnly', () => {
  const latest = (output: unknown) => ({ output }) as never
  it('returns the count of a rule with a count and no rows', () => {
    expect(countOnly(cell({ kind: 'rule', latest: latest({ rows: [], columns: [], count: 2 }) }))).toBe(2)
    expect(countOnly(cell({ kind: 'rule', latest: latest({ count: 0 }) }))).toBe(0)
  })
  it('is null for rows, non-rules or no count', () => {
    expect(countOnly(cell({ kind: 'rule', latest: latest({ rows: [{ a: 1 }], count: 1 }) }))).toBeNull()
    expect(countOnly(cell({ kind: 'query', latest: latest({ rows: [], count: 2 }) }))).toBeNull()
    expect(countOnly(cell({ kind: 'rule', latest: latest({ rows: [] }) }))).toBeNull()
  })
})
