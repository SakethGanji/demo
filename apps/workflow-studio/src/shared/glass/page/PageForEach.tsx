import { useState } from 'react'
import { StatusMark } from '../StatusMark'
import type { StepStatus } from '../cx'
import { PageStep, type PageStepProps } from './PageStep'

export interface ForEachRow {
  /** Row index or key shown in mono. */
  index: React.ReactNode
  label: React.ReactNode
  status: StepStatus
  /** Why it failed / what happened. */
  why?: React.ReactNode
}

export interface PageForEachProps extends Omit<PageStepProps, 'children' | 'kind'> {
  kind?: string
  total: number
  ok: number
  failed: number
  /** Rows still running (shown in the summary while > 0). */
  running?: number
  /** Rows to list. By default only failed rows are shown (`show="failed"`). */
  rows?: ForEachRow[]
  show?: 'failed' | 'all'
  /** Rows shown before "N more". */
  limit?: number
  onRowClick?: (row: ForEachRow, i: number) => void
  /** Extra content under the rows (e.g. a "Retry failed" action). */
  footer?: React.ReactNode
}

/** For-each on a page: `148 ✓ · 2 ✗`, a progress bar, then the failed rows. */
export function PageForEach({ total, ok, failed, running = 0, rows = [], show = 'failed', limit = 5, onRowClick, footer, kind = 'for each', ...step }: PageForEachProps) {
  const [expanded, setExpanded] = useState(false)
  const listed = show === 'failed' ? rows.filter((r) => r.status === 'fail') : rows
  const visible = expanded ? listed : listed.slice(0, limit)
  const pct = (x: number) => (total > 0 ? `${(x / total) * 100}%` : '0%')
  return (
    <PageStep kind={kind} {...step}>
      <div className="gl-group" data-glass-foreach="">
        <div className="gl-group-head">
          <span className="gl-fe-sum" aria-label={`${ok} of ${total} succeeded, ${failed} failed${running ? `, ${running} running` : ''}`}>
            <span data-r="ok">{ok} ✓</span>
            <span aria-hidden className="opacity-55">·</span>
            <span data-r={failed ? 'fail' : undefined}>{failed} ✗</span>
            {running > 0 && (
              <>
                <span aria-hidden className="opacity-55">·</span>
                <span data-r="run">{running} running</span>
              </>
            )}
          </span>
          <span className="gl-sp" />
          <span className="kind-label">{total} rows</span>
        </div>
        <div className="gl-febar" aria-hidden>
          <i data-r="ok" style={{ width: pct(ok) }} />
          <i data-r="fail" style={{ width: pct(failed) }} />
        </div>
        {visible.map((r, i) => (
          <div
            key={i}
            className="gl-ferow"
            data-status={r.status}
            role={onRowClick ? 'button' : undefined}
            tabIndex={onRowClick ? 0 : undefined}
            onClick={onRowClick ? () => onRowClick(r, i) : undefined}
            onKeyDown={onRowClick ? (e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onRowClick(r, i)) : undefined}
            style={onRowClick ? { cursor: 'pointer' } : undefined}
          >
            <StatusMark status={r.status} size="sm" />
            <span className="gl-ix">{r.index}</span>
            <span className="min-w-0 truncate">{r.label}</span>
            {r.why && <span className="gl-why">{r.why}</span>}
          </div>
        ))}
        {listed.length > limit && (
          <button type="button" className="gl-table-more" onClick={() => setExpanded((x) => !x)}>
            {expanded ? 'Show fewer' : `${listed.length - limit} more ▸`}
          </button>
        )}
        {footer && <div className="px-3 pt-1 pb-2.5">{footer}</div>}
      </div>
    </PageStep>
  )
}
