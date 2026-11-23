/**
 * Stepper (DESIGN §8, Release sheet): a vertical list of steps in a well. Each step has a state dot
 * (done ✓ / current ● / pending ○ / error), a title, a meta line and optional content under the current
 * step. `Beam` is the light that runs around a container while something is in flight.
 */
import { Check, X } from 'lucide-react'
import { cn } from '@/shared/lib/utils'

export type StepState = 'done' | 'current' | 'pending' | 'error'

export interface Step {
  key: string
  title: React.ReactNode
  meta?: React.ReactNode
  state: StepState
  /** shown under the step while it is current (or always, with `open`) */
  content?: React.ReactNode
  open?: boolean
  testId?: string
}

function Marker({ state }: { state: StepState }) {
  return (
    <span
      className={cn(
        'relative z-[1] inline-flex size-5 shrink-0 items-center justify-center rounded-full text-[10px] [&_svg]:size-3 [&_svg]:stroke-[2.5]',
        state === 'done' && 'glass-orb bg-success text-primary-foreground',
        state === 'current' && 'glass-orb bg-primary text-primary-foreground [--orb-ring:0_0_0_4px_var(--brand-soft)]',
        state === 'pending' && 'glass-orb-clear text-muted-foreground',
        state === 'error' && 'glass-orb bg-destructive text-primary-foreground',
      )}
      aria-hidden
    >
      {state === 'done' && <Check />}
      {state === 'error' && <X />}
      {state === 'current' && <span className="size-1.5 rounded-full bg-primary-foreground" />}
    </span>
  )
}

export function Stepper({ steps, className, testId }: { steps: Step[]; className?: string; testId?: string }) {
  return (
    <ol className={cn('well flex flex-col p-1.5', className)} data-testid={testId}>
      {steps.map((s, i) => {
        const last = i === steps.length - 1
        const showContent = s.content && (s.open || s.state === 'current' || s.state === 'error')
        return (
          <li
            key={s.key}
            className="relative flex gap-3 rounded-ctl px-2.5 py-2"
            data-state={s.state}
            data-done={s.state === 'done' ? '' : undefined}
            data-testid={s.testId}
          >
            {!last && (
              <span
                aria-hidden
                className={cn('absolute top-7 bottom-[-6px] left-[19.5px] w-px', s.state === 'done' ? 'bg-success/50' : 'bg-hairline')}
              />
            )}
            <Marker state={s.state} />
            <div className="min-w-0 flex-1">
              <div className={cn('text-[13px] leading-5 font-medium', s.state === 'pending' && 'text-muted-foreground')}>{s.title}</div>
              {s.meta && <div className="text-[12px] leading-[18px] text-muted-foreground">{s.meta}</div>}
              {showContent && <div className="mt-2">{s.content}</div>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

/** A light that runs around its (relative, rounded) parent while `on`. Reduced motion shows a static ring. */
export function Beam({ on }: { on: boolean }) {
  if (!on) return null
  return <span className="beam-ring" aria-hidden data-testid="beam" />
}
