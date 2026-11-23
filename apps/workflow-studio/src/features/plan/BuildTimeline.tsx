/**
 * A build run's pieces, shared by the build screen and the app builder's chat:
 *
 *   BuildTimeline     every step in order: the builder (backend, UI, check, input), title, status, attempt, elapsed
 *                     time, a one-line summary, problems (collapsed), the last progress lines while it runs, and a link
 *                     to what it built. `compact` drops the per-step detail to fit the chat column.
 *   BuildReportCard   when the build is over: what was built, what failed, what the plan check still lists, and the
 *                     way on (open the app, revise the plan).
 *   BuildHistory      the last few builds of a project, each linking to its build screen.
 */
import type { ReactNode } from 'react'
import { Link } from '@tanstack/react-router'
import { motion } from 'motion/react'
import {
  AppWindow,
  ArrowRight,
  CheckCircle2,
  Circle,
  CircleDashed,
  CirclePause,
  CircleSlash,
  CircleX,
  ExternalLink,
  KeyRound,
  PencilLine,
  Server,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react'
import { Shimmer } from '@/shared/components/ai-elements/shimmer'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/components/ui/collapsible'
import { Spinner } from '@/shared/components/ui/spinner'
import { Callout, IconTile, WellList, WellRow, type EntityHue } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { BUILD_STATUS, formatElapsed, missingLines, stepElapsed, stepLink } from './buildLogic'
import type { Build, BuildStep, StepStatus } from './buildTypes'
import { useCalm } from './motion'
import { DrawCheck } from './motionKit'

const BUILDER: Record<string, { icon: ReactNode; hue: EntityHue; label: string }> = {
  backend: { icon: <Server />, hue: 'backend', label: 'Backend builder' },
  ui: { icon: <AppWindow />, hue: 'apps', label: 'UI builder' },
  check: { icon: <ShieldCheck />, hue: 'flow', label: 'Plan check' },
  input: { icon: <KeyRound />, hue: 'secrets', label: 'Your input' },
}

const STEP_ICON: Record<StepStatus, ReactNode> = {
  pending: <Circle className="text-muted-foreground" />,
  running: <Spinner className="text-primary" />,
  waiting_for_input: <CirclePause className="text-warning-text" />,
  passed: <CheckCircle2 className="text-success" />,
  partial: <CircleDashed className="text-warning-text" />,
  failed: <CircleX className="text-destructive" />,
  skipped: <CircleSlash className="text-muted-foreground" />,
}

const STEP_LABEL: Record<StepStatus, string> = {
  pending: 'waiting',
  running: 'running',
  waiting_for_input: 'needs your input',
  passed: 'passed',
  partial: 'partly built',
  failed: 'failed',
  skipped: 'skipped',
}

const STEP_BADGE: Record<StepStatus, 'success' | 'warning' | 'error' | 'neutral' | 'primary'> = {
  pending: 'neutral',
  running: 'primary',
  waiting_for_input: 'warning',
  passed: 'success',
  partial: 'warning',
  failed: 'error',
  skipped: 'neutral',
}

const builderOf = (s: BuildStep) => BUILDER[s.kind === 'input' ? 'input' : s.builder] ?? BUILDER.check

export function BuildStatusBadge({ status }: { status: Build['status'] }) {
  const s = BUILD_STATUS[status]
  return (
    <Badge variant={s.badge} data-testid="build-status" data-state={status}>
      {s.label}
    </Badge>
  )
}

function StepLink({ step, slug }: { step: BuildStep; slug: string }) {
  const to = stepLink(step)
  if (!to || step.status === 'pending') return null
  const cls = 'inline-flex items-center gap-1 text-[12px] font-medium text-primary hover:underline underline-offset-2'
  return to.kind === 'module' ? (
    <Link to="/p/$project/backend/$module" params={{ project: slug, module: to.name }} className={cls} data-testid={`build-step-link-${step.key}`}>
      Open the module <ArrowRight className="size-3" />
    </Link>
  ) : (
    <Link to="/p/$project/apps/$appId" params={{ project: slug, appId: to.id }} className={cls} data-testid={`build-step-link-${step.key}`}>
      Open the app <ArrowRight className="size-3" />
    </Link>
  )
}

function StepRow({ step, slug, now, compact, last, n }: { step: BuildStep; slug: string; now: number; compact?: boolean; last: boolean; n: number }) {
  const b = builderOf(step)
  const ms = stepElapsed(step, now)
  const live = step.status === 'running'
  const lines = live ? (step.log ?? []).slice(compact ? -2 : -4) : []
  const calm = useCalm()
  return (
    // steps stagger in as the timeline first shows (and as verify / fix rounds are added)
    <motion.li
      initial={{ opacity: 0, y: calm ? 0 : 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: calm ? 0.15 : 0.26, delay: calm ? 0 : Math.min(n, 8) * 0.05, ease: [0.16, 1, 0.3, 1] }}
      className="relative flex gap-3"
      data-testid={`build-step-${step.key}`}
      data-state={step.status}
    >
      {/* the rail between steps */}
      {!last ? <span aria-hidden className="absolute top-8 bottom-0 left-[13px] w-px bg-hairline" /> : null}
      <span title={b.label} className="shrink-0">
        <IconTile hue={b.hue} className="size-7">{b.icon}</IconTile>
      </span>
      <div className={cn('flex min-w-0 flex-1 flex-col gap-1', last ? 'pb-0' : 'pb-4')}>
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <span className="shrink-0 [&_svg]:size-3.5" aria-hidden>
            {step.status === 'passed' ? <DrawCheck key="passed" className="text-success" /> : STEP_ICON[step.status]}
          </span>
          {live && !calm ? (
            <Shimmer as="span" className="min-w-0 truncate text-[13px] font-medium" duration={1.6}>
              {step.title}
            </Shimmer>
          ) : (
            <span className={cn('min-w-0 truncate text-[13px] font-medium', step.status === 'pending' && 'text-muted-foreground')}>{step.title}</span>
          )}
          {!compact ? <Badge variant={STEP_BADGE[step.status]}>{STEP_LABEL[step.status]}</Badge> : null}
          <span className="ml-auto flex shrink-0 items-center gap-2 text-[11.5px] text-muted-foreground tabular-nums">
            {step.attempt > 1 ? <span data-testid={`build-step-attempt-${step.key}`}>try {step.attempt}</span> : null}
            {ms != null ? <span data-testid={`build-step-elapsed-${step.key}`}>{formatElapsed(ms)}</span> : null}
          </span>
        </div>
        {step.summary ? <p className="text-[12.5px] leading-[18px] text-foreground/80">{step.summary}</p> : null}
        {lines.length ? (
          <div className="flex flex-col gap-0.5 rounded-ctl bg-recess px-2 py-1.5" data-testid={`build-step-log-${step.key}`}>
            {lines.map((l, n) => (
              <span key={n} className={cn('truncate font-mono text-[11px] leading-4', n === lines.length - 1 ? 'text-foreground/80' : 'text-muted-foreground')}>
                {l}
              </span>
            ))}
          </div>
        ) : null}
        {step.problems.length ? (
          <Collapsible>
            <CollapsibleTrigger className="flex items-center gap-1.5 text-[12px] text-muted-foreground hover:text-foreground" data-testid={`build-step-problems-${step.key}`}>
              <TriangleAlert className="size-3 text-warning-text" />
              {step.problems.length} problem{step.problems.length === 1 ? '' : 's'}
              {step.status === 'passed' ? ' found and fixed' : ' found'}
            </CollapsibleTrigger>
            <CollapsibleContent>
              <ul className="mt-1 flex flex-col gap-0.5 pl-4.5">
                {step.problems.map((p, n) => (
                  <li key={n} className="text-[12px] leading-[17px] text-muted-foreground">
                    <span className="font-mono text-[11px]">{p.code}</span> {p.message}
                  </li>
                ))}
              </ul>
            </CollapsibleContent>
          </Collapsible>
        ) : null}
        <StepLink step={step} slug={slug} />
      </div>
    </motion.li>
  )
}

export function BuildTimeline({ build, slug, now, compact }: { build: Build; slug: string; now: number; compact?: boolean }) {
  if (!build.steps.length) return <p className="text-[12.5px] text-muted-foreground">Getting ready…</p>
  return (
    <ol className="flex flex-col" data-testid="build-timeline">
      {build.steps.map((s, n) => (
        <StepRow key={s.key} step={s} slug={slug} now={now} compact={compact} last={n === build.steps.length - 1} n={n} />
      ))}
    </ol>
  )
}

/** A reason in plain words for a build that stopped. */
function reasonText(b: Build): string | null {
  if (b.status === 'cancelled') return 'You cancelled this build. Start a new one to go on; finished modules are skipped.'
  if (b.status !== 'failed') return null
  if (b.reason === 'interrupted') return 'The engine restarted during this build. Start a new one to go on; finished modules are skipped.'
  return b.reason ? `The build stopped: ${b.reason}` : 'The build stopped.'
}

/** What a report entry names, as its step's title when the timeline has it. */
const nameOf = (b: Build, key: string) => b.steps.find((s) => s.key === key || s.key.endsWith(`:${key}`))?.title ?? key

export function BuildReportCard({ build, slug, compact }: { build: Build; slug: string; compact?: boolean }) {
  const r = build.report
  const gaps = missingLines(r?.plan_check.missing)
  const appNames = new Map(build.steps.filter((s) => s.app_id).map((s) => [s.app_id as string, s.title]))
  const reason = reasonText(build)
  return (
    <div className="flex flex-col gap-3" data-testid="build-report">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[14px] font-semibold tracking-[-0.01em]">
          {build.status === 'done' ? 'Built from' : build.status === 'partial' ? 'Mostly built from' : 'Build of'} plan v{build.plan_version}
        </span>
        <BuildStatusBadge status={build.status} />
      </div>
      {reason ? <Callout tone={build.status === 'failed' ? 'error' : 'info'}>{reason}</Callout> : null}
      {r ? (
        <>
          {r.built.length ? (
            <div className="flex flex-col gap-1">
              <span className="text-[12.5px] font-medium">Built</span>
              <div className="flex flex-wrap gap-1.5" data-testid="build-report-built">
                {r.built.map((k) => (
                  <Badge key={k} variant="success"><CheckCircle2 /> {nameOf(build, k)}</Badge>
                ))}
              </div>
            </div>
          ) : null}
          {r.partial?.length ? (
            <div className="flex flex-col gap-1">
              <span className="text-[12.5px] font-medium">Built, with problems left</span>
              <div className="flex flex-wrap gap-1.5" data-testid="build-report-partial">
                {r.partial.map((k) => (
                  <Badge key={k} variant="warning"><TriangleAlert /> {nameOf(build, k)}</Badge>
                ))}
              </div>
            </div>
          ) : null}
          {r.failed.length ? (
            <div className="flex flex-col gap-1">
              <span className="text-[12.5px] font-medium">Not built</span>
              <div className="flex flex-wrap gap-1.5" data-testid="build-report-failed">
                {r.failed.map((k) => (
                  <Badge key={k} variant="error"><CircleX /> {nameOf(build, k)}</Badge>
                ))}
              </div>
            </div>
          ) : null}
          {r.plan_check.ok ? (
            <Callout tone="ok" icon={<ShieldCheck />} data-testid="build-report-check">Everything the plan asks for is there.</Callout>
          ) : (
            <div className="flex flex-col gap-1.5" data-testid="build-report-check">
              <span className="text-[12.5px] font-medium">The plan check still lists</span>
              <WellList>
                {gaps.map((g) => (
                  <WellRow key={g.kind} className="flex-col items-stretch gap-0.5 py-2">
                    <span className="text-[12px] text-muted-foreground">{g.kind} · {g.items.length}</span>
                    <span className={cn('text-[12.5px]', compact ? 'line-clamp-3' : '')}>{g.items.join(', ')}</span>
                  </WellRow>
                ))}
                {!gaps.length ? <WellRow className="text-muted-foreground">Gaps the check could not name.</WellRow> : null}
              </WellList>
            </div>
          )}
        </>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {(r?.app_ids ?? []).map((id, n) => (
          <Button
            key={id}
            size="sm"
            variant={n === 0 ? 'default' : 'outline'}
            render={<Link to="/p/$project/apps/$appId" params={{ project: slug, appId: id }} />}
            data-testid={`build-report-open-app-${id}`}
          >
            <ExternalLink /> {n === 0 ? 'Open the app' : `Open ${appNames.get(id) ?? 'app'}`}
          </Button>
        ))}
        <Button size="sm" variant="outline" render={<Link to="/p/$project/plan" params={{ project: slug }} />} data-testid="build-report-revise">
          <PencilLine /> Revise plan
        </Button>
      </div>
    </div>
  )
}

const when = (iso: string) => new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

export function BuildHistory({ builds, slug, limit = 5 }: { builds: Build[]; slug: string; limit?: number }) {
  if (!builds.length) return null
  return (
    <WellList data-testid="build-history">
      {builds.slice(0, limit).map((b) => {
        const done = b.steps.filter((s) => s.status === 'passed').length
        return (
          <Link
            key={b.id}
            to="/p/$project/build/$build"
            params={{ project: slug, build: b.id }}
            className="flex items-center gap-2.5 px-3 py-2 text-[12.5px] outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus"
            data-testid={`build-history-${b.id}`}
          >
            <span className="min-w-0 flex-1 truncate">
              Plan v{b.plan_version}
              {b.app_key ? ` · ${b.app_key}` : ''} <span className="text-muted-foreground">· {when(b.created_at)}</span>
            </span>
            <span className="shrink-0 text-[11.5px] text-muted-foreground tabular-nums">
              {done}/{b.steps.length}
            </span>
            <BuildStatusBadge status={b.status} />
          </Link>
        )
      })}
    </WellList>
  )
}
