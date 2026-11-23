import { cx } from './cx'

export interface KindLabelProps {
  /** The machine kind, e.g. `query`, `http`, `if`. Rendered in mono. */
  kind: string
  /** Extra mono parts after the kind, joined with a middle dot (`bank-prod`, `GET`). */
  parts?: React.ReactNode[]
  icon?: React.ReactNode
  className?: string
  children?: React.ReactNode
}

/** The mono kind label that heads a step: `query · bank-prod`. */
export function KindLabel({ kind, parts, icon, className, children }: KindLabelProps) {
  return (
    <span className={cx('kind-label', className)} data-kind={kind}>
      {icon}
      <span>{kind}</span>
      {parts?.filter((p) => p !== null && p !== undefined && p !== false && p !== '').map((p, i) => (
        <span key={i} className="inline-flex items-center gap-2">
          <span aria-hidden className="opacity-55">·</span>
          {p}
        </span>
      ))}
      {children}
    </span>
  )
}
