/**
 * The slim "Next steps" strip for the Overview, once the full guide has handed over (first success, or "Go to the
 * Overview"). It shows the first few open steps with their action, Later and Not for this project; the close
 * button defers everything open. "All steps" reopens the full guide (`?setup=1`). Renders nothing when there is
 * nothing to do, when the project was not started through the guide, or while the full guide is showing.
 */
import { Link, useNavigate } from '@tanstack/react-router'
import { KeyRound, X } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { useSetup } from '../hooks/useSetup'
import { useStepAction } from '../hooks/useSetupActions'
import { Button } from '@/shared/components/ui/button'
import { Tile } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { Segments, StepControls, StepMarker } from './setup/parts'

const SHOWN = 3

export function NextSteps({ className }: { className?: string }) {
  const { pid, slug } = useProjectCtx()
  const navigate = useNavigate()
  const setup = useSetup(pid, false)
  const actionFor = useStepAction(setup)
  if (setup.loading || setup.show || !setup.tracked || setup.openCount === 0) return null
  const open = setup.steps.filter((s) => s.state === 'open')
  const P = { project: slug }
  return (
    <Tile className={cn('p-3', className)} data-testid="setup-next-steps">
      <div className="flex items-center gap-2 px-1">
        <span className="text-[13px] font-semibold tracking-[-0.01em]">Next steps</span>
        <span className="text-[12px] text-muted-foreground">
          {setup.done} of {setup.total}
        </span>
        <Segments done={setup.done} total={setup.total} />
        <div className="flex-1" />
        <Link to="/p/$project" params={P} search={{ setup: 1 } as never} className="text-xs text-primary hover:underline" data-testid="setup-next-steps-all">
          All steps
        </Link>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label="Hide next steps"
          onClick={() => setup.defer(open.map((s) => s.id))}
          data-testid="setup-next-steps-close"
        >
          <X />
        </Button>
      </div>
      <div className="mt-2 flex flex-col">
        {open.slice(0, SHOWN).map((s, i) => {
          const action = actionFor(s)
          return (
            <div
              key={s.id}
              className="flex flex-wrap items-center gap-3 px-1 py-2 shadow-[inset_0_-1px_0_var(--hairline)] last:shadow-none"
              data-testid={s.secret ? `setup-needs-secret-${s.secret}` : `setup-step-${s.id}`}
            >
              <StepMarker n={i + 1} done={false} current={i === 0} />
              <div className="min-w-0 flex-1">
                <div className={cn('flex items-center gap-1.5 truncate text-[13px] font-medium', s.secret && 'font-mono')}>
                  {s.secret && <KeyRound className="size-3.5 shrink-0 text-hue-secrets" />}
                  {s.title}
                </div>
                <div className="truncate text-[12px] text-muted-foreground">{s.body}</div>
              </div>
              <StepControls id={s.id} onLater={() => setup.defer(s.id)} onDismiss={() => setup.dismiss(s.id)} onRestore={() => setup.restore(s.id)} />
              {s.secret ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => navigate({ to: '/p/$project/secrets', params: P, search: { create: s.secret } })}
                  data-testid={`setup-step-go-${s.id}`}
                >
                  Add {s.secret}
                </Button>
              ) : (
                action && (
                  <Button size="sm" variant={i === 0 ? 'default' : 'outline'} disabled={action.busy} onClick={action.run} data-testid={`setup-step-go-${s.id}`}>
                    {action.label}
                  </Button>
                )
              )}
            </div>
          )
        })}
      </div>
    </Tile>
  )
}
