import { describe, expect, it } from 'vitest'
import type { Column } from '@/shared/api/types'
import { buildFilter, clauseLabel, columnType, nextSort, opsFor, parseClauseValue, parseFilter, parseScalar, parseSort, serializeFilter, sortLabel } from './filters'

const cols = [
  { name: 'reason', type: 'string' },
  { name: 'amount', type: 'number' },
  { name: 'paid', type: 'boolean' },
] as unknown as Column[]

describe('parseScalar / parseClauseValue', () => {
  it('parses numbers, booleans and ids; keeps unparseable text for the engine to reject', () => {
    expect(parseScalar('number', ' 12.5 ')).toBe(12.5)
    expect(parseScalar('number', 'abc')).toBe('abc')
    expect(parseScalar('number', '')).toBe('')
    expect(parseScalar('boolean', 'true')).toBe(true)
    expect(parseScalar('boolean', 'false')).toBe(false)
    expect(parseScalar('boolean', 'maybe')).toBe('maybe')
    expect(parseScalar('id', '-3')).toBe(-3)
    expect(parseScalar('id', '3.5')).toBe('3.5')
    expect(parseScalar('json', '{"a":1}')).toEqual({ a: 1 })
    expect(parseScalar('json', '{')).toBe('{')
    expect(parseScalar('string', '  keep spaces ')).toBe('  keep spaces ')
  })
  it('handles the operator-specific shapes', () => {
    expect(parseClauseValue('number', '$in', '1, 2,,3')).toEqual([1, 2, 3])
    expect(parseClauseValue('string', '$in', 'a, b')).toEqual(['a', 'b'])
    expect(parseClauseValue('string', '$contains', ' late ')).toBe(' late ')
    expect(parseClauseValue('string', '$exists', 'false')).toBe(false)
    expect(parseClauseValue('string', '$exists', '')).toBe(true)
  })
})

describe('column types and operators', () => {
  it('knows the id column and defaults unknown columns to string', () => {
    expect(columnType(cols, 'id')).toBe('id')
    expect(columnType(cols, 'amount')).toBe('number')
    expect(columnType(cols, 'nope')).toBe('string')
  })
  it('offers contains first for strings and no ordering for booleans', () => {
    expect(opsFor('string')[0]).toBe('$contains')
    expect(opsFor('boolean')).not.toContain('$gt')
    expect(opsFor('id')).not.toContain('$exists')
  })
})

describe('filter model round trip', () => {
  it('one clause is a bare column filter; several are joined', () => {
    const one = { join: '$and' as const, raw: null, clauses: [{ column: 'reason', op: '$eq' as const, value: 'late' }] }
    expect(buildFilter(one)).toEqual({ reason: { $eq: 'late' } })
    const two = { join: '$or' as const, raw: null, clauses: [...one.clauses, { column: 'amount', op: '$gt' as const, value: 5 }] }
    expect(buildFilter(two)).toEqual({ $or: [{ reason: { $eq: 'late' } }, { amount: { $gt: 5 } }] })
    expect(buildFilter({ join: '$and', raw: null, clauses: [] })).toBeNull()
    expect(serializeFilter({ join: '$and', raw: null, clauses: [] })).toBeUndefined()
  })
  it('parses what it serialised', () => {
    const m = { join: '$or' as const, raw: null, clauses: [{ column: 'reason', op: '$contains' as const, value: 'x' }, { column: 'amount', op: '$in' as const, value: [1, 2] }] }
    expect(parseFilter(serializeFilter(m))).toEqual(m)
  })
  it('reads a plain {col: value} filter as equality', () => {
    expect(parseFilter('{"reason":"late"}').clauses).toEqual([{ column: 'reason', op: '$eq', value: 'late' }])
  })
  it('keeps anything it cannot show as a clause verbatim', () => {
    const odd = '{"$and":[{"$or":[{"a":1}]}]}'
    expect(parseFilter(odd).raw).toEqual(JSON.parse(odd))
    expect(buildFilter(parseFilter(odd))).toEqual(JSON.parse(odd))
    expect(parseFilter('{"a":{"$regex":"x"}}').raw).toEqual({ a: { $regex: 'x' } })
  })
  it('ignores empty or broken input', () => {
    for (const bad of [undefined, '', 'not json', '[1]', 'null']) expect(parseFilter(bad)).toEqual({ join: '$and', clauses: [], raw: null })
  })
})

describe('clauseLabel', () => {
  it('reads like a sentence', () => {
    expect(clauseLabel({ column: 'reason', op: '$eq', value: 'late' })).toBe('reason = late')
    expect(clauseLabel({ column: 'amount', op: '$gte', value: 5 })).toBe('amount ≥ 5')
    expect(clauseLabel({ column: 'amount', op: '$in', value: [1, 2] })).toBe('amount in 1, 2')
    expect(clauseLabel({ column: 'note', op: '$exists', value: true })).toBe('note exists')
    expect(clauseLabel({ column: 'note', op: '$exists', value: false })).toBe('note is absent')
  })
})

describe('sorting', () => {
  it('defaults to newest first', () => {
    expect(parseSort(undefined)).toEqual({ key: 'created_at', desc: true })
    expect(sortLabel(undefined)).toBe('Newest first')
    expect(sortLabel('created_at')).toBe('Oldest first')
    expect(sortLabel('-amount')).toBe('amount ↓')
  })
  it('flips the same column and starts a new one ascending (timestamps newest first)', () => {
    expect(nextSort('amount', 'amount')).toBe('-amount')
    expect(nextSort('-amount', 'amount')).toBe('amount')
    expect(nextSort('amount', 'reason')).toBe('reason')
    expect(nextSort('amount', 'created_at')).toBe('-created_at')
    expect(nextSort(undefined, 'created_at')).toBe('created_at')
  })
})
