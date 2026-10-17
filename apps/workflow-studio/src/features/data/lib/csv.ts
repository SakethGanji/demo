/** A small CSV reader and the header → column mapping for the import sheet. */
import type { Column } from '@/shared/api/types'
import { parseEdit } from './cells'

export interface ParsedCsv {
  headers: string[]
  rows: string[][]
}

/** RFC 4180-ish: quoted fields, doubled quotes, CRLF, embedded newlines. Delimiter is sniffed from the header. */
export function parseCsv(text: string): ParsedCsv | { error: string } {
  const src = text.replace(/^\uFEFF/, '')
  const firstLine = src.split(/\r?\n/, 1)[0] ?? ''
  const delim = [',', ';', '\t'].map((d) => [d, firstLine.split(d).length] as const).sort((a, b) => b[1] - a[1])[0][0]
  const out: string[][] = []
  let row: string[] = []
  let cell = ''
  let q = false
  for (let i = 0; i < src.length; i++) {
    const c = src[i]
    if (q) {
      if (c === '"' && src[i + 1] === '"') {
        cell += '"'
        i++
      } else if (c === '"') q = false
      else cell += c
    } else if (c === '"') q = true
    else if (c === delim) {
      row.push(cell)
      cell = ''
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && src[i + 1] === '\n') i++
      row.push(cell)
      cell = ''
      out.push(row)
      row = []
    } else cell += c
  }
  if (cell !== '' || row.length) {
    row.push(cell)
    out.push(row)
  }
  const rows = out.filter((r) => r.some((x) => x.trim() !== ''))
  if (!rows.length) return { error: 'The file is empty.' }
  const [headers, ...body] = rows
  if (!body.length) return { error: 'The file has a header row but no rows to import.' }
  return { headers: headers.map((h) => h.trim()), rows: body }
}

/** Header index → table column name (or null = skip), matched by name, case and punctuation aside. */
export function autoMap(headers: string[], columns: Column[]): (string | null)[] {
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '')
  const used = new Set<string>()
  return headers.map((h) => {
    const c = columns.find((x) => norm(x.name) === norm(h) && !used.has(x.name))
    if (c) used.add(c.name)
    return c?.name ?? null
  })
}

/** One CSV row → the insert body, or the first conversion error. Empty cells are omitted (defaults apply). */
export function rowToData(row: string[], map: (string | null)[], columns: Column[]): { data: Record<string, unknown> } | { error: string } {
  const data: Record<string, unknown> = {}
  for (let i = 0; i < map.length; i++) {
    const name = map[i]
    if (!name) continue
    const raw = (row[i] ?? '').trim()
    if (raw === '') continue
    const col = columns.find((c) => c.name === name)
    if (!col) continue
    const p = parseEdit(raw, col.type)
    if ('error' in p) return { error: `${name}: ${p.error}` }
    data[name] = p.value
  }
  return { data }
}

export function toCsv(columns: string[], rows: Record<string, unknown>[]): string {
  const q = (v: unknown) => {
    const s = v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v)
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [columns.map(q).join(','), ...rows.map((r) => columns.map((c) => q(r[c])).join(','))].join('\n') + '\n'
}
