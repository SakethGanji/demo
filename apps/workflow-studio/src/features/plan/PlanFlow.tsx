/**
 * The app builder's plan-and-build conversation: the user says what app they want and the chat takes it from there,
 * every step a card in the message stream.
 *
 *   describe   the user's first message is the description: a new plan draft, or a revision of the approved plan
 *              (the new app is added to it)
 *   questions  the draft's question round as a card (suggested answers prefilled), at most two rounds
 *   review     the plan as a review card, requirements first ("View full plan" opens the Plan screen), with Approve
 *   building   approving starts the build at once (backend modules first, then the UI): the live timeline as a card,
 *              with Cancel and "Open build details"
 *   input      a build that waits for input (secrets, starting data) shows what it needs, set inline, and Continue
 *   finished   the build's report is the last message
 *
 * `usePlanFlow` (usePlanFlow.ts) holds the state and the actions (the builder's composer sends the description through
 * it); `PlanFlowThread` renders the conversation.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from '@tanstack/react-router'
import { Ban, Check, CheckCircle2, ClipboardList, CornerDownRight, ExternalLink, Hammer, KeyRound, Play, Plus, Sparkles, Table2, WandSparkles } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useCreateSecrets } from '@/features/secrets/useSecretMutations'
import { tablesApi } from '@/shared/api/endpoints'
import { errorMessage } from '@/shared/api/http'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/components/ui/collapsible'
import { Input } from '@/shared/components/ui/input'
import { Spinner } from '@/shared/components/ui/spinner'
import { Callout, IconTile, Progress } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { buildCounts, isActive, pickAppKey, waitingStep } from './buildLogic'
import type { Build, BuildStep } from './buildTypes'
import { BuildReportCard, BuildStatusBadge, BuildTimeline } from './BuildTimeline'
import { RequirementsDoc, WhatWeBuild } from './PlanDocument'
import { QuestionCard } from './PlanQuestions'
import { approveGate, breakingSummary, confirmSuggested, errorsOf, initialAnswers, questionProgress, requirementGroups, unanswered } from './planLogic'
import type { PlanVersion } from './planTypes'
import { useBuildMutations, useLiveBuild, useNow, type LiveBuild } from './useBuild'
import type { PlanFlowState } from './usePlanFlow'

/* ── chat pieces ──────────────────────────────────────────────────────────────────────────────────────────────── */

export function UserBubble({ children }: { children: ReactNode }) {
  return (
    <div className="ml-auto max-w-[88%] rounded-panel bg-raise px-3.5 py-2.5 text-[13px] leading-[19px] whitespace-pre-wrap shadow-raise-sm" data-testid="flow-user-message">
      {children}
    </div>
  )
}

export function Say({ children, testId }: { children: ReactNode; testId?: string }) {
  return (
    <div className="flex items-start gap-2 text-[13px] leading-[19px]" data-testid={testId}>
      <Sparkles className="mt-[3px] size-3 shrink-0 text-ai" aria-hidden />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

/** A card in the message stream: a recessed panel under the chat's glass. */
export function FlowCard({ children, className, testId }: { children: ReactNode; className?: string; testId?: string }) {
  return (
    <div className={cn('@container/plan flex min-w-0 flex-col gap-3 rounded-panel bg-recess p-3', className)} data-testid={testId}>
      {children}
    </div>
  )
}

/* ── questions ────────────────────────────────────────────────────────────────────────────────────────────────── */

export function FlowQuestions({
  draft,
  pending,
  onSubmit,
  onAcceptAll,
}: {
  draft: PlanVersion
  pending: boolean
  onSubmit: (a: Record<string, string>) => void
  onAcceptAll?: () => void
}) {
  const qs = draft.questions
  const [answers, setAnswers] = useState<Record<string, string>>(() => initialAnswers(qs))
  const [confirmed, setConfirmed] = useState<Set<string>>(() => new Set())
  const open = unanswered(qs, answers)
  const progress = questionProgress(qs, answers, confirmed)
  const confirm = (id: string) => setConfirmed((c) => new Set(c).add(id))
  return (
    <FlowCard testId="flow-questions">
      <div className="flex items-baseline gap-2">
        <span className="text-[13px] font-semibold">{draft.round > 1 ? 'One more thing' : 'Requirements checklist'}</span>
        <span className="text-[12px] text-muted-foreground" data-testid="flow-question-progress">
          {progress.answered} of {progress.total} answered{progress.suggested ? ` · ${progress.suggested} suggested` : ''}
        </span>
      </div>
      {qs.map((q, n) => (
        <QuestionCard
          key={q.id}
          q={q}
          n={n + 1}
          answer={answers[q.id] ?? ''}
          confirmed={confirmed.has(q.id)}
          onAnswer={(v) => {
            setAnswers((a) => ({ ...a, [q.id]: v }))
            confirm(q.id)
          }}
          onKeep={() => confirm(q.id)}
        />
      ))}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" className="flex-1" disabled={pending || !qs.length} onClick={() => (onAcceptAll ? onAcceptAll() : onSubmit(initialAnswers(qs)))} data-testid="flow-accept-all">
          {pending ? <Spinner /> : <WandSparkles />} {pending ? 'Applying…' : 'Accept all suggestions'}
        </Button>
        <Button size="sm" variant="outline" className="flex-1" disabled={!progress.suggested} onClick={() => setConfirmed(confirmSuggested(qs, answers, confirmed))} data-testid="flow-use-suggested">
          <Check /> Confirm suggested
        </Button>
        <Button size="sm" variant="outline" className="flex-1" disabled={pending || open.length > 0} onClick={() => onSubmit(answers)} data-testid="flow-answers-submit">
          {pending ? <Spinner /> : <CornerDownRight />} {pending ? 'Updating the plan…' : 'Send answers'}
        </Button>
      </div>
    </FlowCard>
  )
}

/* ── review ───────────────────────────────────────────────────────────────────────────────────────────────────── */

export function FlowReview({
  draft,
  slug,
  approving,
  onApprove,
}: {
  draft: PlanVersion
  slug: string
  approving: boolean
  onApprove: () => void
}) {
  const body = draft.body
  const gate = approveGate({ status: draft.status, questions: draft.questions, issues: draft.issues, dirty: false })
  const errors = errorsOf(draft.issues).length
  const warnings = (draft.issues ?? []).length - errors
  const reqs = requirementGroups(body).reduce((n, g) => n + g.items.length, 0)
  const screens = body.apps.reduce((n, a) => n + a.screens.length, 0)
  return (
    <FlowCard testId="flow-review">
      <div className="flex flex-col gap-1">
        <span className="text-[12px] text-muted-foreground">
          Draft v{draft.version}
          {draft.base_version != null ? ` · revises v${draft.base_version}` : ''}
        </span>
        <span className="text-[15px] leading-tight font-semibold tracking-[-0.02em]" data-testid="flow-plan-title">{body.title || 'Untitled plan'}</span>
        {body.summary ? <p className="text-[12.5px] leading-[18px] text-foreground/85">{body.summary}</p> : null}
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Badge variant="neutral">{reqs} requirement{reqs === 1 ? '' : 's'}</Badge>
        <Badge variant="neutral">{body.modules.length} module{body.modules.length === 1 ? '' : 's'}</Badge>
        <Badge variant="neutral">
          {body.apps.length} app{body.apps.length === 1 ? '' : 's'} · {screens} screen{screens === 1 ? '' : 's'}
        </Badge>
        {errors ? <Badge variant="error">{errors} error{errors === 1 ? '' : 's'}</Badge> : <Badge variant="success">no errors</Badge>}
        {warnings ? <Badge variant="warning">{warnings} warning{warnings === 1 ? '' : 's'}</Badge> : null}
      </div>
      <Collapsible>
        <CollapsibleTrigger className="text-[12.5px] font-medium text-primary hover:underline underline-offset-2" data-testid="flow-read-plan">
          Read the requirements
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="mt-2 flex flex-col gap-3">
            <RequirementsDoc body={body} issues={draft.issues} editable={false} onChange={() => undefined} />
            <WhatWeBuild body={body} issues={draft.issues} />
          </div>
        </CollapsibleContent>
      </Collapsible>
      {gate.reason ? <span className="text-[12px] text-muted-foreground" data-testid="flow-approve-reason">{gate.reason}</span> : null}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" className="flex-1" render={<Link to="/p/$project/plan" params={{ project: slug }} />} data-testid="flow-view-plan">
          <ClipboardList /> View full plan
        </Button>
        <Button size="sm" className="flex-1" disabled={!gate.allowed || approving} onClick={onApprove} data-testid="flow-approve">
          {approving ? <Spinner /> : <CheckCircle2 />} Approve and build
        </Button>
      </div>
      <span className="text-[11.5px] leading-4 text-muted-foreground">Approving builds it straight away: the backend first, then the app.</span>
    </FlowCard>
  )
}

/* ── input ────────────────────────────────────────────────────────────────────────────────────────────────────── */

function SecretInput({ name, purpose, set, environment }: { name: string; purpose: string; set: boolean; environment?: string }) {
  const { project, pid } = useProjectCtx()
  const create = useCreateSecrets(pid)
  const [value, setValue] = useState('')
  const [saved, setSaved] = useState(set)
  const save = () =>
    create.mutate(
      // Only the environment the build checks: a prod value is never copied from what was typed for dev.
      { key: name, value, environments: [environment ?? project.default_environment] },
      { onSuccess: () => setSaved(true), onError: (e) => toast.error(`${name} not saved`, { description: errorMessage(e) }) },
    )
  return (
    <div className="flex flex-col gap-1" data-testid={`flow-input-secret-${name}`}>
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[12px] font-medium">{name}</span>
        <span className="min-w-0 truncate text-[11.5px] text-muted-foreground">{purpose}</span>
        {saved ? <Badge variant="success" className="ml-auto"><Check /> set</Badge> : null}
      </div>
      {!saved ? (
        <div className="flex gap-1.5">
          <Input type="password" className="h-7 text-[12.5px]" value={value} onChange={(e) => setValue(e.target.value)} placeholder="Value" aria-label={`${name} value`} />
          <Button size="sm" variant="secondary" disabled={!value || create.isPending} onClick={save}>
            {create.isPending ? <Spinner /> : null} Save
          </Button>
        </div>
      ) : null}
    </div>
  )
}

function TableRows({ name, columns, rows, suggested }: { name: string; columns: string[]; rows: number; suggested: number }) {
  const { pid } = useProjectCtx()
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [added, setAdded] = useState(0)
  const [busy, setBusy] = useState(false)
  const add = async () => {
    setBusy(true)
    try {
      await tablesApi.insert(pid, name, Object.fromEntries(Object.entries(draft).filter(([, v]) => v.trim())))
      setAdded((n) => n + 1)
      setDraft({})
    } catch (e) {
      toast.error('Row not added', { description: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }
  const have = rows + added
  return (
    <div className="flex flex-col gap-1.5" data-testid={`flow-input-table-${name}`}>
      <div className="flex items-baseline gap-2">
        <Table2 className="size-3 shrink-0 self-center text-muted-foreground" aria-hidden />
        <span className="font-mono text-[12px] font-medium">{name}</span>
        <span className="text-[11.5px] text-muted-foreground tabular-nums">
          {have} row{have === 1 ? '' : 's'}
          {suggested > have ? ` · ${suggested} suggested` : ''}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        {columns.map((c) => (
          <Input key={c} className="h-7 text-[12.5px]" value={draft[c] ?? ''} onChange={(e) => setDraft((d) => ({ ...d, [c]: e.target.value }))} placeholder={c} aria-label={`${name} ${c}`} />
        ))}
      </div>
      <Button size="xs" variant="outline" className="self-start" disabled={busy || !Object.values(draft).some((v) => v.trim())} onClick={() => void add()}>
        {busy ? <Spinner /> : <Plus />} Add row
      </Button>
    </div>
  )
}

/** What a paused build needs: secrets to set and starting rows to add, then Continue (anything left out is skipped). */
export function BuildInputCard({ build, step, onContinued }: { build: Build; step: BuildStep; onContinued?: (b: Build) => void }) {
  const { pid } = useProjectCtx()
  const ref = useRef<HTMLDivElement>(null)
  // the build stopped to ask: bring the question into view
  useEffect(() => {
    ref.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [])
  const m = useBuildMutations(pid)
  const input = step.input ?? { secrets: [], tables: [], message: '' }
  const proceed = () =>
    m.proceed.mutate(build.id, {
      onSuccess: (b) => onContinued?.(b),
      onError: (e) => toast.error('Not continued', { description: errorMessage(e) }),
    })
  return (
    <div ref={ref} className="flex flex-col gap-3" data-testid="flow-input">
      <div className="flex items-center gap-2">
        <IconTile hue="secrets" className="size-6"><KeyRound /></IconTile>
        <span className="text-[13px] font-semibold">Before the app is built</span>
      </div>
      {input.message ? <p className="text-[12.5px] leading-[18px] text-foreground/85">{input.message}</p> : null}
      {input.secrets.length ? (
        <div className="flex flex-col gap-2">
          <span className="text-[12px] font-medium text-muted-foreground">Secrets</span>
          {input.secrets.map((s) => (
            <SecretInput key={s.name} name={s.name} purpose={s.purpose} set={s.set} environment={input.environment} />
          ))}
        </div>
      ) : null}
      {input.tables.length ? (
        <div className="flex flex-col gap-2.5">
          <span className="text-[12px] font-medium text-muted-foreground">Starting data</span>
          {input.tables.map((t) => (
            <TableRows key={t.name} name={t.name} columns={t.columns} rows={t.rows} suggested={t.suggested_min} />
          ))}
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={m.proceed.isPending} onClick={proceed} data-testid="flow-continue">
          {m.proceed.isPending ? <Spinner /> : <Play />} Continue
        </Button>
        <span className="text-[11.5px] text-muted-foreground">Anything left empty is skipped.</span>
      </div>
    </div>
  )
}

/* ── build ────────────────────────────────────────────────────────────────────────────────────────────────────── */

interface FlowBuildProps {
  id: string
  slug: string
  onAppReady?: (appId: string) => void
  onAppActivity?: (tick: string) => void
  /** the closing message for a finished build in place of the report (null: the report) */
  finish?: (b: Build) => ReactNode
}

/** The live build as a card, following its own event stream. */
function FlowBuild(props: FlowBuildProps) {
  const { pid } = useProjectCtx()
  const live = useLiveBuild(pid, props.id)
  return <FlowBuildCard {...props} live={live} />
}

/** The live build as a card; the report (or `finish`) follows as its own message once the build is over. */
function FlowBuildCard({ slug, onAppReady, onAppActivity, finish, live }: FlowBuildProps & { live: LiveBuild }) {
  const { pid } = useProjectCtx()
  const m = useBuildMutations(pid)
  const b = live.build
  const active = !!b && isActive(b.status)
  const now = useNow(active)
  const appStep = b?.steps.find((s) => s.kind === 'app' && s.app_id && s.status !== 'pending')
  const appTick = appStep ? `${appStep.app_id}:${appStep.status}:${appStep.attempt}:${appStep.log?.length ?? 0}` : null

  useEffect(() => {
    if (appStep?.app_id) onAppReady?.(appStep.app_id)
    // only when the app first appears
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appStep?.app_id])
  useEffect(() => {
    if (appTick) onAppActivity?.(appTick)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appTick])

  if (!b) {
    return (
      <FlowCard testId="flow-build">
        <span className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
          <Spinner /> Starting the build…
        </span>
      </FlowCard>
    )
  }
  const counts = buildCounts(b)
  const waiting = waitingStep(b)
  return (
    <>
      <FlowCard testId="flow-build">
        <div className="flex flex-wrap items-center gap-2">
          <IconTile hue="backend" className="size-6"><Hammer /></IconTile>
          <span className="text-[13px] font-semibold">Plan v{b.plan_version}</span>
          <BuildStatusBadge status={b.status} />
          <span className="ml-auto text-[12px] text-muted-foreground tabular-nums" data-testid="flow-build-count">
            {counts.finished} of {counts.total} steps
          </span>
        </div>
        <Progress value={counts.total ? (counts.finished / counts.total) * 100 : 0} />
        <BuildTimeline build={b} slug={slug} now={now} compact />
        <div className="flex flex-wrap gap-2">
          <Button size="xs" variant="ghost" render={<Link to="/p/$project/build/$build" params={{ project: slug, build: b.id }} />} data-testid="flow-build-details">
            <ExternalLink /> Open build details
          </Button>
          {active ? (
            <Button
              size="xs"
              variant="ghost"
              disabled={m.cancel.isPending}
              onClick={() => m.cancel.mutate(b.id, { onSuccess: live.put, onError: (e) => toast.error('Not cancelled', { description: errorMessage(e) }) })}
              data-testid="flow-build-cancel"
            >
              <Ban /> Cancel
            </Button>
          ) : null}
        </div>
      </FlowCard>
      {waiting ? (
        <FlowCard>
          <BuildInputCard build={b} step={waiting} onContinued={live.put} />
        </FlowCard>
      ) : null}
      {!active ? (
        (finish?.(b) ?? (
          <FlowCard testId="flow-report">
            <BuildReportCard build={b} slug={slug} compact />
          </FlowCard>
        ))
      ) : null}
    </>
  )
}

/* ── the thread ───────────────────────────────────────────────────────────────────────────────────────────────── */

export function PlanFlowThread({
  f,
  slug,
  onAppReady,
  onAppActivity,
  aside,
  live,
  finish,
  hidePrompt,
}: {
  f: PlanFlowState
  slug: string
  onAppReady?: (appId: string) => void
  onAppActivity?: (tick: string) => void
  /** shown under the opening message on New app (a way to the direct builder) */
  aside?: ReactNode
  /** the build followed by the page (one event stream for the timeline and the live code and preview) */
  live?: LiveBuild
  finish?: (b: Build) => ReactNode
  /** the page shows the user's description itself */
  hidePrompt?: boolean
}) {
  if (!f.visible || f.loading) return null
  // said once a build starts from this conversation (a build found on the server is introduced above instead)
  const approvedNote = f.approved && f.flow && (f.buildId || f.pending.start)
  return (
    <div className="flex flex-col gap-3.5" data-testid="plan-flow" data-phase={f.phase}>
      {!f.appId ? (
        <Say testId="flow-intro">
          {f.buildId && !f.flow ? (
            <>A build of your plan (v{f.approved?.version}) is under way; follow it here. Once it is done you can describe another app.</>
          ) : f.target === 'revise' ? (
            <>
              Tell me about the app you need. Your project has an approved plan (v{f.approved?.version}): I'll add the app to it,
              ask about anything missing, and build it once you approve.
            </>
          ) : f.target === 'draft' && !f.flow ? (
            <>Your project has a plan in progress. Finish it here, then I'll build it.</>
          ) : (
            <>
              Tell me about the app you need: who uses it, what it keeps, what it connects to. I'll ask a few questions, show you the
              plan, then build the backend and the app.
            </>
          )}
          {aside ? <div className="mt-1.5">{aside}</div> : null}
        </Say>
      ) : null}
      {f.flow?.prompt && !hidePrompt ? <UserBubble>{f.flow.prompt}</UserBubble> : null}
      {f.phase === 'drafting' ? (
        <Say testId="flow-drafting">
          <span className="flex items-center gap-2 text-muted-foreground">
            <Spinner /> {f.target === 'revise' ? 'Adding it to the plan…' : 'Drafting a plan…'}
          </span>
        </Say>
      ) : null}
      {f.draft && f.phase === 'questions' ? (
        <>
          <Say>Here is what a real deployment still needs. Each item has a suggested choice; accept them all or change what's wrong.</Say>
          <FlowQuestions key={`${f.draft.version}-${f.draft.round}`} draft={f.draft} pending={f.pending.answer} onSubmit={f.answer} onAcceptAll={f.acceptAll} />
        </>
      ) : null}
      {f.draft && f.phase === 'review' ? (
        <>
          <Say>Here is the plan. Read the requirements, then approve it and I'll build it.</Say>
          <FlowReview draft={f.draft} slug={slug} approving={f.pending.approve} onApprove={() => f.approve(false)} />
          {f.breaking ? (
            <FlowCard testId="flow-breaking">
              <Callout tone="warn">{breakingSummary(f.breaking)}. Builders rebuild what it affects; nothing in the tables is dropped.</Callout>
              <div className="flex gap-2">
                <Button size="sm" variant="ghost" onClick={f.dismissBreaking}>Cancel</Button>
                <Button size="sm" onClick={() => f.approve(true)} data-testid="flow-breaking-confirm">Approve anyway</Button>
              </div>
            </FlowCard>
          ) : null}
        </>
      ) : null}
      {approvedNote ? (
        <Say testId="flow-approved">
          Plan v{f.approved?.version} approved. Building it: the backend modules first, then the app.
        </Say>
      ) : null}
      {f.buildError ? (
        <FlowCard testId="flow-build-error">
          <Callout tone="error">The build didn't start: {f.buildError}</Callout>
          <Button size="sm" className="self-start" disabled={f.pending.start} onClick={() => f.startBuild(f.approved ? pickAppKey(f.approved.body.apps) : undefined)}>
            {f.pending.start ? <Spinner /> : <Hammer />} Build it
          </Button>
        </FlowCard>
      ) : null}
      {f.buildId ? (
        live ? (
          <FlowBuildCard key={f.buildId} id={f.buildId} slug={slug} onAppReady={onAppReady} onAppActivity={onAppActivity} finish={finish} live={live} />
        ) : (
          <FlowBuild key={f.buildId} id={f.buildId} slug={slug} onAppReady={onAppReady} onAppActivity={onAppActivity} finish={finish} />
        )
      ) : null}
    </div>
  )
}
