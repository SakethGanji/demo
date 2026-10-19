import { describe, expect, it } from 'vitest'
import {
  cellText,
  datasetsPageUrl,
  formatBytes,
  formatCount,
  isExpressionValue,
  isUuid,
  missingDatasetMessage,
  resolveDatasetValue,
  shapeLabel,
  uploadProblem,
} from './datasets'
import type { DatasetItem } from './types'

const item = (over: Partial<DatasetItem> = {}): DatasetItem => ({
  id: 'ds_1',
  dataset_id: '0f8fad5b-d9cb-469f-a165-70867728950e',
  name: 'sales',
  file_name: 'sales.csv',
  format: 'csv',
  row_count: 1204,
  column_count: 7,
  size_bytes: 52_000,
  columns: [],
  created_at: '2026-10-18T10:00:00',
  updated_at: '2026-10-18T10:00:00',
  ...over,
})

describe('formatting', () => {
  it('formats bytes with binary units', () => {
    expect(formatBytes(null)).toBe('—')
    expect(formatBytes(-1)).toBe('—')
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1023)).toBe('1023 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB')
    expect(formatBytes(300 * 1024 * 1024)).toBe('300 MB')
  })
  it('labels the shape, with unknown counts as ?', () => {
    expect(shapeLabel(item())).toBe('1,204 rows × 7 cols')
    expect(shapeLabel(item({ row_count: 1, column_count: 1 }))).toBe('1 row × 1 col')
    expect(shapeLabel(item({ row_count: null, column_count: null }))).toBe('? rows × ? cols')
    expect(formatCount(undefined)).toBe('?')
  })
  it('renders preview cells as text', () => {
    expect(cellText(null)).toBe('')
    expect(cellText(undefined)).toBe('')
    expect(cellText(0)).toBe('0')
    expect(cellText(false)).toBe('false')
    expect(cellText({ a: 1 })).toBe('{"a":1}')
  })
})

describe('uploadProblem', () => {
  it('accepts csv, parquet and excel in any case', () => {
    for (const f of ['a.csv', 'b.PARQUET', 'c.xlsx', 'd.Xls', 'my.data.csv']) expect(uploadProblem(f), f).toBeNull()
  })
  it('rejects other types and extension-less names', () => {
    for (const f of ['a.json', 'b.txt', 'csv', 'a.csv.zip']) expect(uploadProblem(f), f).toMatch(/CSV, Parquet or Excel/)
  })
})

describe('expression and uuid detection', () => {
  it('treats {{ }} values as expressions', () => {
    expect(isExpressionValue('{{ $json.dataset }}')).toBe(true)
    expect(isExpressionValue('sales')).toBe(false)
    expect(isExpressionValue(undefined)).toBe(false)
    expect(isExpressionValue(3)).toBe(false)
  })
  it('recognises analytics UUIDs', () => {
    expect(isUuid('0f8fad5b-d9cb-469f-a165-70867728950e')).toBe(true)
    expect(isUuid('sales')).toBe(false)
  })
})

describe('resolveDatasetValue (node picker)', () => {
  const items = [item(), item({ id: 'ds_2', dataset_id: '7c9e6679-7425-40de-944b-e07fc1f90ae7', name: 'customers' })]

  it('is empty for no value', () => {
    expect(resolveDatasetValue('', items)).toEqual({ item: null, unknown: null })
    expect(resolveDatasetValue(undefined, items)).toEqual({ item: null, unknown: null })
  })
  it('matches by link id (the stored form)', () => {
    expect(resolveDatasetValue('ds_2', items).item?.name).toBe('customers')
  })
  it('matches by name (expressions, the AI agent)', () => {
    expect(resolveDatasetValue('customers', items).item?.id).toBe('ds_2')
  })
  it('shows a legacy analytics UUID as that dataset', () => {
    const r = resolveDatasetValue('7c9e6679-7425-40de-944b-e07fc1f90ae7', items)
    expect(r.item?.name).toBe('customers')
    expect(r.unknown).toBeNull()
  })
  it('prefers an id match over a name match, like the engine', () => {
    const tricky = [item({ id: 'ds_9', name: 'ds_2' }), ...items]
    expect(resolveDatasetValue('ds_2', tricky).item?.id).toBe('ds_2')
  })
  it('keeps a value that names nothing as unknown', () => {
    expect(resolveDatasetValue('gone', items)).toEqual({ item: null, unknown: 'gone' })
    expect(resolveDatasetValue('sales', [])).toEqual({ item: null, unknown: 'sales' })
  })
})

describe('missingDatasetMessage', () => {
  it('says deleted for an id and not found for a name', () => {
    expect(missingDatasetMessage('7c9e6679-7425-40de-944b-e07fc1f90ae7')).toMatch(/deleted/)
    expect(missingDatasetMessage('gone')).toMatch(/No dataset named “gone”/)
  })
})

describe('datasetsPageUrl', () => {
  it('builds the project datasets URL', () => {
    expect(datasetsPageUrl('my-proj')).toBe('/p/my-proj/datasets')
    expect(datasetsPageUrl('a b')).toBe('/p/a%20b/datasets')
  })
})
