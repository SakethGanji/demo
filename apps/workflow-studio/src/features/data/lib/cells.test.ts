import { beforeAll, describe, expect, it, vi } from 'vitest'

// cells.ts reaches shared/lib/config, which reads window.location at import time
vi.stubGlobal('window', { location: { hostname: 'localhost' } })
let cells: typeof import('./cells')
beforeAll(async () => {
  cells = await import('./cells')
})

describe('parseEdit', () => {
  it('turns empty text into null', () => {
    expect(cells.parseEdit('  ', 'string')).toEqual({ value: null })
  })
  it('converts numbers and booleans, passing unparseable text through for the engine to reject', () => {
    expect(cells.parseEdit(' 12.5 ', 'number')).toEqual({ value: 12.5 })
    expect(cells.parseEdit('abc', 'integer')).toEqual({ value: 'abc' })
    expect(cells.parseEdit('true', 'boolean')).toEqual({ value: true })
    expect(cells.parseEdit('false', 'boolean')).toEqual({ value: false })
    expect(cells.parseEdit('yes', 'boolean')).toEqual({ value: 'yes' })
  })
  it('validates JSON on the client and leaves strings untouched', () => {
    expect(cells.parseEdit('{"a": [1]}', 'json')).toEqual({ value: { a: [1] } })
    expect(cells.parseEdit('{', 'json')).toEqual({ error: 'not valid JSON' })
    expect(cells.parseEdit('  padded ', 'string')).toEqual({ value: '  padded ' })
  })
})

describe('display and edit text', () => {
  it('collapses objects and arrays', () => {
    expect(cells.jsonSummary({ a: 1, b: 2 })).toBe('{…} 2 keys')
    expect(cells.jsonSummary({ a: 1 })).toBe('{…} 1 key')
    expect(cells.jsonSummary([1, 2, 3])).toBe('[…] 3 items')
    expect(cells.jsonSummary([1])).toBe('[…] 1 item')
    expect(cells.jsonSummary('x')).toBe('"x"')
  })
  it('displays empty, scalar and structured values', () => {
    expect(cells.displayValue(null, 'string')).toBe('')
    expect(cells.displayValue(undefined, 'number')).toBe('')
    expect(cells.displayValue(84.5, 'number')).toBe('84.5')
    expect(cells.displayValue(true, 'boolean')).toBe('true')
    expect(cells.displayValue({ a: 1 }, 'json')).toBe('{…} 1 key')
  })
  it('edits JSON as text and everything else as its string', () => {
    expect(cells.editText({ a: 1 }, 'json')).toBe('{"a":1}')
    expect(cells.editText(12, 'number')).toBe('12')
    expect(cells.editText(null, 'string')).toBe('')
  })
  it('formats nothing for a missing or invalid timestamp', () => {
    expect(cells.formatStamp(null)).toBe('')
    expect(cells.formatStamp('not a date')).toBe('')
    expect(cells.formatStamp('2026-10-15T09:12:00')).toMatch(/Oct 15/)
  })
})

describe('isNumeric', () => {
  it('is true for number and integer only', () => {
    expect(cells.isNumeric('number')).toBe(true)
    expect(cells.isNumeric('integer')).toBe(true)
    expect(cells.isNumeric('string')).toBe(false)
  })
})
