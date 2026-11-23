import { StatusMark } from '../StatusMark'
import type { StepStatus } from '../cx'
import { PageStep, type PageStepProps } from './PageStep'

export interface ParallelLane {
  id: string
  label: React.ReactNode
  status?: StepStatus
  /** The lane's steps. */
  children?: React.ReactNode
}

export interface PageParallelProps extends Omit<PageStepProps, 'children' | 'kind'> {
  kind?: string
  lanes: ParallelLane[]
  /** Line under the lanes, e.g. `All 3 lanes join here`. */
  join?: React.ReactNode
}

/** A fan-out on a page: lanes side by side (stacked on narrow screens), each with its own compact rail. */
export function PageParallel({ lanes, join, kind = 'parallel', ...step }: PageParallelProps) {
  return (
    <PageStep kind={kind} {...step}>
      <div className="gl-lanes">
        {lanes.map((l) => (
          <div key={l.id} className="gl-lane" data-lane={l.id} data-status={l.status}>
            <div className="gl-lane-head">
              {l.status && <StatusMark status={l.status} size="sm" />}
              <span className="min-w-0 truncate">{l.label}</span>
            </div>
            {l.children && <div className="rail gl-sub">{l.children}</div>}
          </div>
        ))}
      </div>
      {join && <div className="gl-join">{join}</div>}
    </PageStep>
  )
}
