import { useState } from 'react'
import { cx } from '../cx'
import { PageStep, type PageStepProps } from './PageStep'

export interface BranchCase {
  id: string
  /** The case sentence: `Amount over 10,000`, `Otherwise`. */
  label: React.ReactNode
  /** Short condition pill (`> 10k`, `true`, `else`). */
  cond?: React.ReactNode
  /** This case is the one the picked run took (green elbow, open by default). */
  taken?: boolean
  /** Right side of the case header, e.g. `2 steps`. */
  summary?: React.ReactNode
  /** The case's nested steps (`PageStep`s). Rendered on a compact rail. */
  children?: React.ReactNode
}

export interface PageBranchProps extends Omit<PageStepProps, 'children' | 'kind'> {
  kind?: string
  cases: BranchCase[]
  /** Which cases start open. Default `taken` (untaken branches are folded). */
  defaultOpen?: 'taken' | 'all' | 'none'
  /** Controlled open set (case ids). */
  open?: string[]
  onOpenChange?: (open: string[]) => void
  /** Line under the cases, e.g. `Joins back here`. */
  join?: React.ReactNode
}

/** If / Switch on a page: one spine with soft elbows; the taken case is green and open, others folded. */
export function PageBranch({ cases, defaultOpen = 'taken', open, onOpenChange, join, kind = 'if', ...step }: PageBranchProps) {
  const [inner, setInner] = useState<string[]>(() =>
    defaultOpen === 'all' ? cases.map((c) => c.id) : defaultOpen === 'none' ? [] : cases.filter((c) => c.taken).map((c) => c.id),
  )
  const openIds = open ?? inner
  const toggle = (id: string) => {
    const next = openIds.includes(id) ? openIds.filter((x) => x !== id) : [...openIds, id]
    if (!open) setInner(next)
    onOpenChange?.(next)
  }
  const takenIx = cases.findIndex((c) => c.taken)
  return (
    <PageStep kind={kind} {...step}>
      <div className="gl-cases" role="list">
        {cases.map((c, i) => {
          const isOpen = openIds.includes(c.id)
          const bodyId = `branch-${c.id}`
          return (
            <div
              key={c.id}
              role="listitem"
              className="gl-case"
              data-case={c.id}
              data-taken={c.taken ? '' : undefined}
              data-pass={takenIx > i ? '' : undefined}
              data-open={isOpen ? '' : undefined}
            >
              <button type="button" className="gl-case-head" aria-expanded={isOpen} aria-controls={bodyId} onClick={() => toggle(c.id)}>
                {c.cond !== undefined && <span className="gl-cond">{c.cond}</span>}
                <span className="gl-case-label">{c.label}</span>
                {c.summary && <span className="kind-label">{c.summary}</span>}
                <span className="gl-chev" aria-hidden>›</span>
              </button>
              {isOpen && c.children && (
                <div id={bodyId} className={cx('gl-case-body', 'rail', 'gl-sub')}>
                  {c.children}
                </div>
              )}
            </div>
          )
        })}
      </div>
      {join && <div className="gl-join">{join}</div>}
    </PageStep>
  )
}
