import { PageStep, type PageStepProps } from './PageStep'
import type { StepStatus } from '../cx'

export interface LoopIteration {
  status?: StepStatus
  /** Tooltip / accessible name, e.g. the item key. */
  label?: string
}

export interface PageLoopProps extends Omit<PageStepProps, 'children' | 'kind'> {
  kind?: string
  /** Iteration count, or one entry per iteration (a failed one is drawn red). */
  iterations: number | LoopIteration[]
  /** Selected iteration, 0-based. */
  value: number
  onChange: (index: number) => void
  /** Group header title, e.g. `Each wire`. */
  title?: React.ReactNode
  /** The selected iteration's nested steps. */
  children?: React.ReactNode
}

const MAX_CHIPS = 12

/** A Loop on a page: the loop sentence, then a glass group with an iteration picker over the inner steps. */
export function PageLoop({ iterations, value, onChange, title, children, kind = 'loop', ...step }: PageLoopProps) {
  const list: LoopIteration[] = typeof iterations === 'number' ? Array.from({ length: iterations }, () => ({})) : iterations
  const n = list.length
  const clamp = (i: number) => Math.max(0, Math.min(n - 1, i))
  return (
    <PageStep kind={kind} {...step}>
      <div className="gl-group" data-glass-loop="">
        <div className="gl-group-head">
          <span className="gl-group-title">{title ?? 'Each item'}</span>
          <span className="kind-label">{n} {n === 1 ? 'iteration' : 'iterations'}</span>
          <span className="gl-sp" />
          {n > 0 && (
            <div className="gl-iter" role="group" aria-label="Iteration">
              {n <= MAX_CHIPS ? (
                list.map((it, i) => (
                  <button
                    key={i}
                    type="button"
                    aria-pressed={i === value}
                    aria-label={it.label ? `Iteration ${i + 1}: ${it.label}` : `Iteration ${i + 1}`}
                    title={it.label}
                    data-r={it.status === 'fail' ? 'fail' : undefined}
                    onClick={() => onChange(i)}
                  >
                    {i + 1}
                  </button>
                ))
              ) : (
                <>
                  <button type="button" aria-label="Previous iteration" disabled={value <= 0} onClick={() => onChange(clamp(value - 1))}>‹</button>
                  <button type="button" aria-pressed="true" aria-label={`Iteration ${value + 1} of ${n}`} data-r={list[value]?.status === 'fail' ? 'fail' : undefined}>
                    {value + 1} / {n}
                  </button>
                  <button type="button" aria-label="Next iteration" disabled={value >= n - 1} onClick={() => onChange(clamp(value + 1))}>›</button>
                </>
              )}
            </div>
          )}
        </div>
        {children && <div className="gl-group-body rail gl-sub">{children}</div>}
      </div>
    </PageStep>
  )
}
