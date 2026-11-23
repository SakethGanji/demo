/**
 * The setup guide (UI-DESIGN §3.3, reworked): a project can start from anything. Step 1 asks where to start
 * (interface, backend, workflow, dataset, template, or describe it) and opens that card's first action in place.
 * The steps after it follow from that start (`nextSteps`); none is locked, and each can wait ("Later") or go away
 * ("Not for this project"). A secret shows up only when something needs one, and never blocks anything.
 */
import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { ArrowRight, KeyRound } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import type { SetupState } from '../hooks/useSetup'
import { useStepAction } from '../hooks/useSetupActions'
import type { NextStep, StartKind } from '../lib/nextSteps'
import { Button } from '@/shared/components/ui/button'
import { Page, Tile, Well } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { DatabasesSection } from '@/features/connections/DatabasesSection'
import { START_CARDS } from './setup/startCards'
import { StartPicker } from './setup/StartPicker'
import { GuideStartPanel } from './setup/StartPanels'
import { SecretForm } from './setup/SecretForm'
import { Segments, StepControls, StepMarker } from './setup/parts'

export function SetupGuide({ setup }: { setup: SetupState }) {
  const { pid, project, slug } = useProjectCtx()
  const navigate = useNavigate()
  const started = !setup.empty || !!setup.start
  const firstOpen = setup.steps.find((s) => s.state === 'open')?.id
  const hidden = setup.onboarding.dismissed.length
  return (
    <Page data-testid="setup-guide">
      <div className="mx-auto max-w-[760px]">
        <div className="flex items-center justify-between text-[12.5px] text-muted-foreground">
          <span>New project · {slug}</span>
          <span className="flex items-center gap-2" data-testid="setup-progress">
            {started ? `${setup.done} of ${setup.total}` : 'Pick a start'}
            <Segments done={setup.done} total={Math.max(setup.total, 1)} />
          </span>
        </div>
        <h1 className="mt-3 text-title leading-[1.1] font-[650] tracking-[-0.03em]">Set up {project.name}</h1>
        <p className="mt-1.5 text-[13px] text-muted-foreground">
          Start from whatever you have: a page, an API, a workflow, a file, a template or a sentence. Every step after that can wait.
        </p>

        <Tile className="mt-6 p-2">
          <Well className="flex flex-col p-1.5">
            <StartStep setup={setup} />
            {setup.steps.map((s, i) => (
              <StepRow key={s.id} setup={setup} step={s} n={i + 2} current={s.id === firstOpen} />
            ))}
          </Well>
        </Tile>

        {/* an existing database can be the start: connect it before anything else exists */}
        <div className="mt-6">
          <DatabasesSection pid={pid} />
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          {hidden > 0 && (
            <button
              type="button"
              className="transition-colors duration-(--d-fast) hover:text-foreground"
              onClick={() => setup.restore(setup.onboarding.dismissed)}
              data-testid="setup-restore-hidden"
            >
              {hidden} hidden · show again
            </button>
          )}
          <div className="flex-1" />
          {!setup.empty && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setup.closeGuide()
                navigate({ to: '/p/$project', params: { project: slug }, search: {} })
              }}
              data-testid="setup-close"
            >
              Go to the Overview <ArrowRight />
            </Button>
          )}
        </div>
      </div>
    </Page>
  )
}

/** Step 1: where to start. Open while the project is empty; folded to one line after, with "Change". */
function StartStep({ setup }: { setup: SetupState }) {
  const [open, setOpen] = useState<boolean>(setup.empty)
  const [kind, setKind] = useState<StartKind | null>(setup.start)
  const done = !setup.empty
  const title = START_CARDS.find((c) => c.kind === setup.start)?.title
  const expanded = open || setup.empty
  return (
    <section className="rounded-ctl px-3 py-3.5" data-testid="setup-step-start">
      <div className="flex gap-3">
        <StepMarker n={1} done={done} current={!done} className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[13.5px] font-semibold tracking-[-0.01em]">Where do you want to start?</span>
            <div className="flex-1" />
            {done && (
              <button
                type="button"
                className="text-xs text-muted-foreground transition-colors duration-(--d-fast) hover:text-foreground"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={expanded}
                data-testid="setup-start-change"
              >
                {expanded ? 'Close' : 'Change'}
              </button>
            )}
          </div>
          {!expanded ? (
            <p className="mt-1 text-[13px] text-muted-foreground">{title ? `Started from ${title.toLowerCase()}.` : 'Something is already here.'} Pick another start any time.</p>
          ) : (
            <>
              <p className="mt-1 text-[13px] text-muted-foreground">Pick one. Nothing else is required first, not even a secret.</p>
              <div className="mt-3">
                <StartPicker
                  variant="guide"
                  value={kind}
                  onPick={(k) => setKind((cur) => (cur === k ? null : k))}
                  panel={kind && <GuideStartPanel key={kind} setup={setup} kind={kind} />}
                />
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  )
}

function StepRow({ setup, step, n, current }: { setup: SetupState; step: NextStep; n: number; current: boolean }) {
  const { slug } = useProjectCtx()
  const navigate = useNavigate()
  const actionFor = useStepAction(setup)
  const action = actionFor(step)
  const done = step.state === 'done'
  const deferred = step.state === 'deferred'
  return (
    <section
      className={cn('rounded-ctl px-3 py-3.5 transition-opacity duration-(--d-base) motion-reduce:transition-none', deferred && 'opacity-70')}
      data-testid={step.secret ? `setup-needs-secret-${step.secret}` : `setup-step-${step.id}`}
      data-state={step.state}
    >
      <div className="flex gap-3">
        <StepMarker n={n} done={done} current={current} className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {step.secret && <KeyRound className="size-3.5 text-hue-secrets" />}
            <span className={cn('text-[13.5px] font-semibold tracking-[-0.01em]', step.secret && 'font-mono')}>{step.title}</span>
            {deferred && <span className="text-[11.5px] text-muted-foreground">later</span>}
            <div className="flex-1" />
            {!done && (
              <StepControls
                id={step.id}
                deferred={deferred}
                onLater={() => setup.defer(step.id)}
                onDismiss={() => setup.dismiss(step.id)}
                onRestore={() => setup.restore(step.id)}
              />
            )}
            {!done && action && (
              <Button size="sm" variant={current ? 'default' : 'outline'} disabled={action.busy} onClick={action.run} data-testid={action.testId ?? `setup-step-go-${step.id}`}>
                {action.label}
              </Button>
            )}
          </div>
          <p className="mt-1 text-[13px] text-muted-foreground">{step.body}</p>
          {step.secret && !done && !deferred && <SecretForm initialName={step.secret} hint="Optional. Nothing waits on it." />}
          {step.secret && deferred && (
            <button
              type="button"
              className="mt-1 text-xs text-primary hover:underline"
              onClick={() => navigate({ to: '/p/$project/secrets', params: { project: slug }, search: { create: step.secret } })}
            >
              Add it in Secrets
            </button>
          )}
        </div>
      </div>
    </section>
  )
}
