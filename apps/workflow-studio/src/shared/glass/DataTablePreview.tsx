import { cx } from './cx'

export interface DataTablePreviewProps {
  /** Column names. Default: the keys of the first row. */
  columns?: string[]
  rows: Record<string, unknown>[]
  /** Total row count (may exceed rows.length when the caller only has a page). */
  total?: number
  /** Rows shown. Default 3. */
  preview?: number
  /** "Show all N rows" opens the full result (workspace / sheet). */
  onMore?: () => void
  className?: string
  'data-testid'?: string
  /** Custom cell content (e.g. a coloured status pill); return undefined to keep the plain text. */
  renderCell?: (column: string, value: unknown, row: Record<string, unknown>) => React.ReactNode
  /** Row emphasis, e.g. `warn` for a mismatch row (an amber wash with a left bar). */
  rowTone?: (row: Record<string, unknown>) => 'warn' | 'bad' | undefined
}

export function formatCell(v: unknown): { text: string; num: boolean; nil: boolean } {
  if (v === null || v === undefined) return { text: 'null', num: false, nil: true }
  if (typeof v === 'number') return { text: Number.isInteger(v) ? v.toLocaleString('en-US') : v.toLocaleString('en-US', { maximumFractionDigits: 4 }), num: true, nil: false }
  if (typeof v === 'boolean') return { text: String(v), num: false, nil: false }
  if (typeof v === 'object') return { text: JSON.stringify(v), num: false, nil: false }
  return { text: String(v), num: false, nil: false }
}

/** Three rows of a result, then "Show all N rows ▾" (v3 `.rowsb`). */
export function DataTablePreview({ columns, rows, total, preview = 3, onMore, className, renderCell, rowTone, ...rest }: DataTablePreviewProps) {
  const cols = columns ?? Object.keys(rows[0] ?? {})
  const shown = rows.slice(0, preview)
  const count = total ?? rows.length
  const numeric = cols.map((c) => shown.length > 0 && shown.every((r) => r[c] === null || r[c] === undefined || typeof r[c] === 'number'))
  return (
    <div className={cx('gl-table', className)} data-testid={rest['data-testid']}>
      <div className="gl-table-scroll">
        <table>
          <thead>
            <tr>
              {cols.map((c, i) => (
                <th key={c} data-num={numeric[i] ? '' : undefined} scope="col">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, ri) => (
              <tr key={ri} data-tone={rowTone?.(r)}>
                {cols.map((c) => {
                  const f = formatCell(r[c])
                  const custom = renderCell?.(c, r[c], r)
                  return (
                    <td key={c} data-num={f.num ? '' : undefined} data-null={f.nil ? '' : undefined} title={f.text}>
                      {custom ?? f.text}
                    </td>
                  )
                })}
              </tr>
            ))}
            {shown.length === 0 && (
              <tr>
                <td colSpan={Math.max(cols.length, 1)} data-null="">No rows</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {count > shown.length || onMore ? (
        <button type="button" className="gl-table-more" onClick={onMore} disabled={!onMore}>
          Show all {count.toLocaleString('en-US')} {count === 1 ? 'row' : 'rows'} ▾
        </button>
      ) : null}
    </div>
  )
}
