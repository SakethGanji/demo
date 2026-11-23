/** Column drafts for the New table and Columns dialogs (UI-DESIGN §3.4). */
import type { Column, ColumnType } from '@/shared/api/types'
import { editText, parseEdit } from './cells'

export const COLUMN_TYPES: ColumnType[] = ['string', 'number', 'integer', 'boolean', 'datetime', 'json']
const RESERVED = new Set(['id', 'created_at', 'updated_at'])
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/

export interface ColumnDraft {
  key: string
  name: string
  type: ColumnType
  required: boolean
  /** default as typed text ('' = none) */
  def: string
}

let seq = 0
export function newColumnDraft(c?: Column): ColumnDraft {
  return {
    key: `c${++seq}`,
    name: c?.name ?? '',
    type: c?.type ?? 'string',
    required: c?.required ?? false,
    def: c && c.default !== undefined && c.default !== null ? editText(c.default, c.type) : '',
  }
}

/** Client-side name check (the engine re-validates: `422 invalid_column`). */
export function columnNameError(d: ColumnDraft, all: ColumnDraft[]): string | null {
  if (!d.name) return null
  if (!NAME_RE.test(d.name)) return 'letters, digits and _ (not starting with a digit)'
  if (RESERVED.has(d.name)) return `${d.name} is row metadata`
  if (all.filter((x) => x.name === d.name).length > 1) return 'duplicate column'
  return null
}

/** Drafts → engine columns (empty names dropped). Defaults that don't parse are sent as typed text. */
export function toColumns(drafts: ColumnDraft[]): Column[] {
  return drafts
    .filter((d) => d.name.trim())
    .map((d) => {
      const col: Column = { name: d.name.trim(), type: d.type, required: d.required }
      if (d.def.trim() !== '') {
        const p = parseEdit(d.def, d.type)
        col.default = 'value' in p ? p.value : d.def
      }
      return col
    })
}

