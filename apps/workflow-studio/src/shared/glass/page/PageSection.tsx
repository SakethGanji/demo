import { cx } from '../cx'

export interface PageSectionProps {
  id?: string
  /** Section number, shown as `§ 2`. */
  num?: React.ReactNode
  title?: React.ReactNode
  /** Quiet note after the title (e.g. `3 steps · 1.2 s`). */
  note?: React.ReactNode
  actions?: React.ReactNode
  className?: string
  'data-testid'?: string
  /** `PageStep`s (and Branch / Loop / Parallel / ForEach): they join into one rail. */
  children?: React.ReactNode
}

/** A § section of a page. Its children sit on one rail. */
export function PageSection({ id, num, title, note, actions, className, children, ...rest }: PageSectionProps) {
  const headingId = id ? `${id}-title` : undefined
  return (
    <section id={id} className={cx('gl-section', className)} aria-labelledby={title ? headingId : undefined} data-testid={rest['data-testid']}>
      {(title || num !== undefined || actions) && (
        <div className="gl-section-head">
          {num !== undefined && <span className="gl-section-num">§ {num}</span>}
          {title && <h2 id={headingId}>{title}</h2>}
          {note && <span className="gl-section-note">{note}</span>}
          <span className="gl-sp" />
          {actions}
        </div>
      )}
      <div className="rail">{children}</div>
    </section>
  )
}
