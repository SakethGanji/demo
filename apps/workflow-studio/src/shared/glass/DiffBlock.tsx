import { cx } from './cx'

export interface DiffLine {
  kind: 'add' | 'del' | 'ctx'
  /** Optional key column (field / column name). */
  k?: React.ReactNode
  text: React.ReactNode
}

export interface DiffBlockProps {
  lines: DiffLine[]
  title?: React.ReactNode
  /** Right side of the header, e.g. `+2 −1`. Default: the computed counts. */
  meta?: React.ReactNode
  className?: string
  'data-testid'?: string
}

/** A mono diff: red removed lines, green added lines, quiet context. */
export function DiffBlock({ lines, title, meta, className, ...rest }: DiffBlockProps) {
  const add = lines.filter((l) => l.kind === 'add').length
  const del = lines.filter((l) => l.kind === 'del').length
  const keyed = lines.some((l) => l.k !== undefined)
  return (
    <div className={cx('gl-diff', className)} data-nokeys={keyed ? undefined : ''} data-testid={rest['data-testid']}>
      {(title || meta !== null) && (
        <div className="gl-diff-head">
          <span>{title}</span>
          <span>{meta ?? `+${add} −${del}`}</span>
        </div>
      )}
      {lines.map((l, i) => (
        <div key={i} className={cx('gl-diff-line', l.kind === 'add' && 'diff-line-add', l.kind === 'del' && 'diff-line-del')} data-kind={l.kind}>
          <span aria-hidden>{l.kind === 'add' ? '+' : l.kind === 'del' ? '−' : ' '}</span>
          {keyed && <span className="gl-diff-k">{l.k}</span>}
          <span>
            <span className="sr-only">{l.kind === 'add' ? 'added: ' : l.kind === 'del' ? 'removed: ' : ''}</span>
            {l.text}
          </span>
        </div>
      ))}
    </div>
  )
}
