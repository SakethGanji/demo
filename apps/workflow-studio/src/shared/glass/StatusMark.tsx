import { cx, STATUS_LABEL, type StepStatus } from './cx'

export interface StatusMarkProps {
  status: StepStatus
  size?: 'sm' | 'md' | 'lg'
  /** Overrides the accessible label (default: the status name). */
  label?: string
  className?: string
}

/** The round status mark: pending ring, running spinner, ok check, need !, skip dashed, stale dot, fail ×. */
export function StatusMark({ status, size = 'md', label, className }: StatusMarkProps) {
  return (
    <span
      role="img"
      aria-label={label ?? STATUS_LABEL[status]}
      data-status={status}
      data-size={size === 'md' ? undefined : size}
      className={cx('gl-mk', className)}
    >
      {GLYPH[status]}
    </span>
  )
}

const svg = (d: string) => (
  <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d={d} />
  </svg>
)

const GLYPH: Record<StepStatus, React.ReactNode> = {
  pending: null,
  running: null,
  ok: svg('M2.5 6.2 5 8.5l4.5-5'),
  need: svg('M6 2.5v4M6 9.2v.1'),
  skip: null,
  stale: svg('M6 3.2V6l1.8 1.2'),
  fail: svg('M3.5 3.5l5 5M8.5 3.5l-5 5'),
}
