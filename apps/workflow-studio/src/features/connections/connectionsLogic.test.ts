import { describe, expect, it } from 'vitest'
import {
  rowsAsArrays,
  buildSources, canConnect, cellText, mapQueryRows, rowEstimateLabel, showSourcesHint, sourceChipLabel, toggleConnection, toggleTable,
} from './connectionsLogic'
import type { Connection } from './connectionsTypes'

const conn = (key: string): Connection => ({
  id: key, key, name: key, kind: 'postgres', host: 'h', database: 'd', readonly: true, status: 'ok', table_count: 3,
})

describe('picker state and sources payload', () => {
  it('selecting a connection alone means all its tables', () => {
    const sel = toggleConnection({}, 'crm')
    expect(buildSources(sel)).toEqual([{ connection: 'crm' }])
  })
  it('tables narrow the source; unselecting the connection removes it', () => {
    let sel = toggleConnection({}, 'crm')
    sel = toggleTable(sel, 'crm', 'orders')
    sel = toggleTable(sel, 'crm', 'users')
    expect(buildSources(sel)).toEqual([{ connection: 'crm', tables: ['orders', 'users'] }])
    sel = toggleTable(sel, 'crm', 'orders')
    expect(sel.crm).toEqual(['users'])
    expect(buildSources(toggleConnection(sel, 'crm'))).toBeUndefined()
  })
  it('drops sources whose connection no longer exists', () => {
    expect(buildSources({ gone: [], crm: [] }, [conn('crm')])).toEqual([{ connection: 'crm' }])
  })
  it('chips and hint', () => {
    expect(sourceChipLabel({ connection: 'crm' })).toBe('crm: all tables')
    expect(sourceChipLabel({ connection: 'crm', tables: ['a', 'b'] })).toBe('crm: a, b')
    expect(sourceChipLabel({ connection: 'crm', tables: ['a', 'b', 'c'] })).toBe('crm: 3 tables')
    expect(showSourcesHint([conn('crm')], {})).toBe(true)
    expect(showSourcesHint([conn('crm')], { crm: [] })).toBe(false)
    expect(showSourcesHint([], {})).toBe(false)
    expect(showSourcesHint(undefined, {})).toBe(false)
  })
})

describe('query results and forms', () => {
  it('maps rows to objects by column', () => {
    expect(mapQueryRows({ columns: ['id', 'n'], rows: [[1, 'a'], [2, null]], truncated: false, ms: 3 })).toEqual([
      { id: 1, n: 'a' },
      { id: 2, n: null },
    ])
  })
  it('reads rows the engine keys by column in column order', () => {
    const rows = [{ name: 'Lamp', category: 'Home' }] as unknown as unknown[][]
    expect(rowsAsArrays({ columns: ['name', 'category'], rows, truncated: false, ms: 1 }).rows).toEqual([['Lamp', 'Home']])
    expect(rowsAsArrays({ columns: ['id'], rows: [[7]], truncated: false, ms: 1 }).rows).toEqual([[7]])
  })
  it('renders cells', () => {
    expect(cellText(null)).toBe('NULL')
    expect(cellText({ a: 1 })).toBe('{"a":1}')
    expect(cellText(5)).toBe('5')
  })
  it('labels row estimates', () => {
    expect(rowEstimateLabel(1)).toBe('1 row')
    expect(rowEstimateLabel(12_400)).toBe('~12k rows')
    expect(rowEstimateLabel(2_500_000)).toBe('~2.5M rows')
    expect(rowEstimateLabel(-1)).toBe('—')
  })
  it('validates the connect form', () => {
    expect(canConnect('x', 'postgresql://u:p@h/db')).toBe(true)
    expect(canConnect('', 'postgresql://u:p@h/db')).toBe(false)
    expect(canConnect('x', 'mysql://h/db')).toBe(false)
  })
})
