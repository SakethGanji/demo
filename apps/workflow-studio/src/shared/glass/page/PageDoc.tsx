import { cx } from '../cx'

export interface PageDocProps {
  title?: React.ReactNode
  /** Small line above the title (breadcrumb, `vN · Live` chip). */
  eyebrow?: React.ReactNode
  /** Line under the title: trigger sentence · runs this week · used by N. */
  byline?: React.ReactNode
  /** A paragraph under the byline (≤ 62ch). */
  lede?: React.ReactNode
  actions?: React.ReactNode
  /** `read` = 900px reading column (default); `wide` lets the column fill its parent. */
  width?: 'read' | 'wide'
  className?: string
  'data-testid'?: string
  children?: React.ReactNode
}

/** The document column for a page: title block, then sections. Sentences inside keep to ~62ch. */
export function PageDoc({ title, eyebrow, byline, lede, actions, width = 'read', className, children, ...rest }: PageDocProps) {
  return (
    <article className={cx('gl-doc', className)} data-width={width} data-testid={rest['data-testid']} style={width === 'wide' ? { maxWidth: 'none' } : undefined}>
      {(eyebrow || title || byline || lede || actions) && (
        <header>
          {eyebrow && <div className="gl-doc-eyebrow">{eyebrow}</div>}
          {title && <h1>{title}</h1>}
          {byline && <div className="gl-doc-byline">{byline}</div>}
          {lede && <p className="gl-doc-lede">{lede}</p>}
          {actions && <div className="gl-doc-actions">{actions}</div>}
        </header>
      )}
      <div className="gl-doc-body">{children}</div>
    </article>
  )
}
