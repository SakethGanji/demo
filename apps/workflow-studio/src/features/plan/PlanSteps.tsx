/**
 * The step indicator across the top of the Plan page: Describe → Questions → Review → Approved → Building, with the
 * current step marked. On a phone only the current step keeps its label; the others are their markers.
 */
import type * as React from 'react'
import { Check } from 'lucide-react'
import { cn } from '@/shared/lib/utils'
import type { PlanStepState, stepStates } from './planLogic'

export function PlanStepper({ steps }: { steps: ReturnType<typeof stepStates> }) {
  return (
    <ol className="glass-chrome flex min-w-0 items-center gap-1 rounded-full px-2 py-1.5" aria-label="Plan progress" data-testid="plan-steps">
      {steps.map((s, n) => (
        <li
          key={s.key}
          className={cn('flex min-w-0 items-center gap-1', n < steps.length - 1 && 'flex-1')}
          data-state={s.state}
          aria-current={s.state === 'current' ? 'step' : undefined}
          data-testid={`plan-step-${s.key}`}
        >
          <span className="flex min-w-0 items-center gap-1.5 rounded-full px-1.5 py-0.5">
            <Marker state={s.state} n={n + 1} />
            <span
              className={cn(
                'truncate text-[12.5px]',
                s.state === 'current' ? 'font-semibold text-foreground' : 'hidden text-muted-foreground @min-[640px]/plan:inline',
              )}
            >
              {s.label}
            </span>
          </span>
          {n < steps.length - 1 ? (
            <span aria-hidden className={cn('h-px min-w-3 flex-1 rounded-full', s.state === 'done' ? 'bg-success/50' : 'bg-hairline')} />
          ) : null}
        </li>
      ))}
    </ol>
  )
}

function Marker({ state, n }: { state: PlanStepState; n: number }) {
  return (
    <span
      aria-hidden
      className={cn(
        'inline-flex size-5 shrink-0 items-center justify-center rounded-full text-[10.5px] font-semibold tabular-nums [&_svg]:size-3 [&_svg]:stroke-[2.5]',
        state === 'done' && 'glass-orb bg-success text-primary-foreground',
        state === 'current' && 'glass-orb bg-primary text-primary-foreground [--orb-ring:0_0_0_3px_var(--brand-soft)]',
        state === 'pending' && 'glass-orb-clear text-muted-foreground',
      )}
    >
      {state === 'done' ? <Check /> : n}
    </span>
  )
}

/**
 * The page's two columns: the document on the left and the sticky panel (approval, progress) on the right; on a
 * phone the panel follows the document and sticks to the bottom of the screen as a bar.
 */
export function PlanColumns({ main, aside }: { main: React.ReactNode; aside: React.ReactNode }) {
  return (
    <div className="mt-5 grid grid-cols-1 gap-5 @min-[960px]/plan:grid-cols-[minmax(0,1fr)_300px] @min-[960px]/plan:items-start">
      <div className="flex min-w-0 flex-col gap-5">{main}</div>
      <aside className="sticky bottom-3 z-10 min-w-0 @min-[960px]/plan:top-4 @min-[960px]/plan:bottom-auto">{aside}</aside>
    </div>
  )
}
