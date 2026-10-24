import { cx } from './cx'

export type CheckResult = 'pass' | 'fail' | 'skip' | 'none'

export interface CheckStripItem {
  result: CheckResult
  /** Tooltip, e.g. `09:42 · passed`. */
  label?: string
}

export interface CheckStripProps {
  /** Oldest first. */
  results: (CheckResult | CheckStripItem)[]
  /** Keep only the last N. Default 20. */
  max?: number
  /** Show `7/8` after the dots. */
  summary?: boolean
  className?: string
  'data-testid'?: string
}

export function summarizeChecks(results: (CheckResult | CheckStripItem)[]) {
  const rs = results.map((r) => (typeof r === 'string' ? r : r.result))
  const pass = rs.filter((r) => r === 'pass').length
  const fail = rs.filter((r) => r === 'fail').length
  return { pass, fail, ran: pass + fail }
}

/** A sparkline of pass/fail dots for a kept check or a test history. */
export function CheckStrip({ results, max = 20, summary, className, ...rest }: CheckStripProps) {
  const items = results.slice(-max).map((r) => (typeof r === 'string' ? { result: r } : r))
  const s = summarizeChecks(items)
  return (
    <span
      className={cx('check-strip', className)}
      role="img"
      aria-label={`${s.pass} passed, ${s.fail} failed of the last ${items.length}`}
      data-testid={rest['data-testid']}
    >
      {items.map((r, i) => (
        <span key={i} className="gl-check" data-r={r.result === 'none' ? undefined : r.result} title={r.label} />
      ))}
      {summary && <span className="gl-check-sum">{s.pass}/{s.ran}</span>}
    </span>
  )
}
