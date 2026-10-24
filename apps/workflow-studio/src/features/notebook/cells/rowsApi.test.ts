import { describe, expect, it, vi } from 'vitest'
import type { Cell, HttpSpec } from '../types'

// these helpers live beside their components, whose imports read window.location at load (no DOM in this suite)
vi.stubGlobal('window', { location: { hostname: 'localhost', origin: 'http://localhost' } })
const { guessKey, guessTable, toCsv } = await import('./RowsTable')
const { assertionText, cleanSpec, parseValue } = await import('./Http')
const { suiteSummary } = await import('./ApiSuite')
const { lineDiff } = await import('../workspace/ApiResponse')

describe('acting on rows', () => {
  it('picks a key column that names each row', () => {
    const rows = [{ status: 'stuck', wire_id: 'W1' }, { status: 'stuck', wire_id: 'W2' }]
    expect(guessKey(['status', 'wire_id'], rows)).toBe('wire_id')
    // a named key that repeats is not a key; the first all-distinct column is
    expect(guessKey(['id', 'name'], [{ id: 1, name: 'a' }, { id: 1, name: 'b' }])).toBe('name')
    expect(guessKey(['a'], [])).toBe('a')
  })
  it('guesses the table a change most likely writes from the query', () => {
    expect(guessTable("SELECT wire_id FROM bank.wires w WHERE status = 'stuck'")).toBe('bank.wires')
    expect(guessTable('select 1')).toBe('')
    expect(guessTable(undefined)).toBe('')
  })
  it('writes CSV with quoting', () => {
    expect(toCsv(['a', 'b'], [{ a: 'x,y', b: null }, { a: 'say "hi"', b: { k: 1 } }])).toBe('a,b\n"x,y",\n"say ""hi""","{""k"":1}"')
  })
})

describe('API testing', () => {
  it('reads typed values the way a person types them', () => {
    expect(parseValue('200')).toBe(200)
    expect(parseValue('true')).toBe(true)
    expect(parseValue('null')).toBe(null)
    expect(parseValue('["a"]')).toEqual(['a'])
    expect(parseValue('{bad')).toBe('{bad')
    expect(parseValue('POST')).toBe('POST')
  })
  it('says each check as a sentence', () => {
    expect(assertionText({ kind: 'status', op: 'eq', value: 200 })).toBe('Status is 200')
    expect(assertionText({ kind: 'time', op: 'lt', value: 500 })).toBe('Responds in under 500 ms')
    expect(assertionText({ kind: 'header', op: 'exists', name: 'Location' })).toBe('Header Location is present')
  })
  it('drops empty defaults before saving', () => {
    const s = cleanSpec({ method: 'GET', url: 'u', params: {}, headers: {}, auth: { type: 'none' }, assertions: [], extract: [{ name: ' ', source: 'json_path' }], body: '' } as HttpSpec)
    expect(s).toEqual({ method: 'GET', url: 'u' })
  })
  it('sums the suite from the latest current results (children of a loop count too)', () => {
    const run = (status: string, passed: boolean[]) => ({ status, output: { assertions: passed.map((p) => ({ passed: p })) } })
    const cells = [
      { kind: 'http', position: 1, stale: false, latest: run('success', [true, true]), children: [] },
      { kind: 'for_each', position: 2, stale: false, latest: null, children: [{ kind: 'http', position: 0, stale: false, latest: run('error', [true, false]) }] },
      { kind: 'http', position: 3, stale: true, latest: run('success', [true]), children: [] },
    ] as unknown as Cell[]
    expect(suiteSummary(cells)).toEqual({ total: 3, ran: 2, ok: 1, bad: 1, checks: 4, checksFailed: 1 })
  })
  it('diffs two bodies line by line', () => {
    expect(lineDiff('a\nb\nc', 'a\nc\nd')).toEqual([
      { t: 'same', s: 'a' }, { t: 'del', s: 'b' }, { t: 'same', s: 'c' }, { t: 'add', s: 'd' },
    ])
  })
})
