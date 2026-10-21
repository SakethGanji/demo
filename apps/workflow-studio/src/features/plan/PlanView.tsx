/**
 * The Plan page (`/p/$project/plan`): one requirements conversation for the whole project, then a reviewed, versioned
 * plan every builder builds against. It reads as a requirements document first and a technical plan second.
 *
 *   steps        Describe → Questions → Review → Approved → Building across the top (planStep)
 *   describe     a roomy prompt with a few hints and an example to insert; the model drafts the plan and asks
 *   questions    one card per question grouped by topic, suggested answers prefilled, at most two rounds
 *   review       the requirements document (requirements, acceptance, assumptions, roles), what we'll build in plain
 *                language, then the technical plan in collapsible sections with inline edits (saved with PUT); issues
 *                inline and summarised; a sticky approval panel (Approve disabled while there are errors; a breaking
 *                change asks first). A revision shows its diff against the approved version first.
 *   approved     build progress from the conformance check ("unknown" until it exists), a Build button per module,
 *                each app with its screens, and Revise (a prompt, or a plain copy to edit)
 */
import { useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import {
  BookOpen,
  CheckCircle2,
  ChevronDown,
  Circle,
  CircleDashed,
  CircleHelp,
  ClipboardList,
  Hammer,
  PencilLine,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TextQuote,
} from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { ApiError, errorMessage } from '@/shared/api/http'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Spinner } from '@/shared/components/ui/spinner'
import { Textarea } from '@/shared/components/ui/textarea'
import { cn } from '@/shared/lib/utils'
import { Callout, Dot, Page, PageHeader, Progress, SectionHeader, Tile, WellList, WellRow } from '@/shared/components/ui/surface'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/components/ui/alert-dialog'
import { activeBuild, buildAction, pickAppKey } from './buildLogic'
import { BuildHistory } from './BuildTimeline'
import { PlanDiff } from './PlanDiff'
import { IssueSummary, RequirementsDoc, WhatWeBuild } from './PlanDocument'
import { PlanQuestions } from './PlanQuestions'
import { PlanSections } from './PlanSections'
import { PlanColumns, PlanStepper } from './PlanSteps'
import { ProjectDocs } from './ProjectDocs'
import {
  approveGate,
  breakingSummary,
  buildModule,
  buildProgress,
  buildSearch,
  errorsOf,
  humanize,
  moduleStates,
  planStep,
  smokeProven,
  stepStates,
  type ApproveGate,
} from './planLogic'
import type { ModuleBuildState, PlanApp, PlanBody, PlanDiffResult, PlanIssue, PlanVersion } from './planTypes'
import { useBuildMutations, useBuilds } from './useBuild'
import { usePlan, usePlanCheck, usePlanDiff, usePlanMutations } from './usePlan'

const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }) : ''

/** A check's preflight item: something to set up before building (a missing secret, say). */
interface Preflight {
  code: string
  severity: string
  where?: string
  message: string
  fix?: string
}

const orderOf = (body: PlanBody) => (body.build_order.length ? body.build_order : body.modules.map((x) => x.key))

export function PlanView() {
  const { pid } = useProjectCtx()
  const plan = usePlan(pid)
  const draft = plan.data?.draft ?? null
  const approved = plan.data?.approved ?? null
  return (
    <div className="h-full" data-testid="plan-view">
      <Page className="@container/plan px-2 pt-5 pb-4 sm:px-8 sm:pt-7">
        <div className="mx-auto flex w-full max-w-[1180px] flex-col">
          <PageHeader
            title="Plan"
            icon={<ClipboardList />}
            hue="flow"
            meta={
              <>
                <span>
                  {approved ? `v${approved.version} approved` : 'Nothing approved yet'}
                  {draft ? ` · draft v${draft.version}` : ''}
                </span>
                <Dot />
                <span>Agree what to build once; every builder follows it.</span>
              </>
            }
          />
          <PlanWorkspace />
        </div>
      </Page>
    </div>
  )
}

/**
 * The whole plan flow (steps, then describe / questions / review / approved), on the Plan page and the project home.
 * `compact` (the home): once approved, the long requirements document folds behind "Show the full plan" so the
 * widgets and apps stay in reach; build progress and the approved panel (build, revise) stay open.
 */
export function PlanWorkspace({ compact = false }: { compact?: boolean } = {}) {
  const { pid, slug } = useProjectCtx()
  const plan = usePlan(pid)
  const m = usePlanMutations(pid)
  const draft = plan.data?.draft ?? null
  const approved = plan.data?.approved ?? null
  const check = usePlanCheck(pid, !!approved)
  const states = useMemo(
    () => (approved ? moduleStates(approved.body, check.isError ? null : check.data) : {}),
    [approved, check.data, check.isError],
  )
  const progress = approved ? buildProgress(orderOf(approved.body), states) : { built: 0, total: 0 }
  const step = planStep({ draft, approved, states })
  const steps = stepStates(step, { buildDone: progress.total > 0 && progress.built === progress.total })

  if (plan.isPending)
    return (
      <Tile className="mt-5 p-4">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="mt-3 h-24 w-full" />
      </Tile>
    )
  if (plan.isError)
    return (
      <Tile className="mt-5">
        <EmptyState
          title="Couldn't load the plan"
          data-testid="plan-error"
          actions={<Button size="sm" variant="outline" onClick={() => void plan.refetch()}>Retry</Button>}
        >
          {errorMessage(plan.error)}
        </EmptyState>
      </Tile>
    )
  const approvedView = approved ? (
    <ApprovedPlan
      slug={slug}
      approved={approved}
      compact={compact}
      mutations={m}
      states={states}
      progress={progress}
      checking={check.isPending}
      checkKnown={!!check.data?.has_plan}
      smokePassed={smokeProven(check.data?.smoke, approved.body.integrations.map((i) => i.key))}
      preflight={(check.data?.preflight as Preflight[] | undefined) ?? []}
    />
  ) : null
  // the Overview, once the plan is approved and nothing is known to be left to build: the project's docs, no stepper
  const built = !!approved && !draft && !(check.data?.has_plan && progress.built < progress.total)
  if (compact && approved && built)
    return (
      <div className="@container/plan flex flex-col" data-testid="plan-workspace">
        <ProjectDocs slug={slug} body={approved.body} version={approved.version} approvedBy={approved.approved_by ?? null} approvedAt={approved.approved_at ?? null}>
          {approvedView}
        </ProjectDocs>
      </div>
    )
  return (
    <div className="@container/plan flex flex-col" data-testid="plan-workspace">
      <div className="mt-4">
        <PlanStepper steps={steps} />
      </div>
      {draft ? (
        draft.questions.length ? (
          <PlanQuestions
            key={`${draft.version}-${draft.round}-${draft.questions.map((q) => q.id).join(',')}`}
            questions={draft.questions}
            round={draft.round}
            pending={m.answer.isPending || m.accept.isPending}
            onAcceptAll={() =>
              m.accept.mutate(
                { version: draft.version },
                {
                  onSuccess: (r) => {
                    const next = r.plan as PlanVersion
                    if (!next.questions.length && !errorsOf(next.issues).length) m.approve.mutate({ version: draft.version, acceptBreaking: false })
                  },
                  onError: (e) => toast.error('Suggestions not applied', { description: errorMessage(e) }),
                },
              )
            }
            onSubmit={(answers) =>
              m.answer.mutate(
                { version: draft.version, answers },
                { onError: (e) => toast.error('Answers not sent', { description: errorMessage(e) }) },
              )
            }
          />
        ) : (
          <DraftReview key={`${draft.version}`} pid={pid} draft={draft} mutations={m} />
        )
      ) : approved ? (
        approvedView
      ) : (
        <PromptBox
          pending={m.draft.isPending}
          onSubmit={(p) => m.draft.mutate({ prompt: p }, { onError: (e) => toast.error('Plan not drafted', { description: errorMessage(e) }) })}
        />
      )}
    </div>
  )
}

/* ── describe ─────────────────────────────────────────────────────────────────────────────────────────────────── */

const HINTS: Array<{ label: string; hint: string }> = [
  { label: 'Who uses it', hint: 'testers start runs; team leads approve fixes' },
  { label: 'What it stores', hint: 'tests, their runs and results, failure notes' },
  { label: 'What it connects to', hint: 'our bank app in dev and UAT, the Jobs service' },
  { label: 'What must never happen', hint: 'no payments or changes in production' },
]

export const EXAMPLE_PROMPT = `A QA workbench for our online banking app.

Who uses it: testers keep a library of test cases and start runs; team leads review failures and approve fixes.
What it stores: the tests, every run with its result and evidence, and notes on failures.
What it connects to: the Jobs service runs the tests against the bank app in dev and UAT, using a test login.
What must never happen: nothing runs against production that moves money or changes customer data.`

function PromptBox({ pending, onSubmit }: { pending: boolean; onSubmit: (prompt: string) => void }) {
  const [prompt, setPrompt] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)
  const add = (label: string) => {
    setPrompt((p) => `${p.trimEnd()}${p.trim() ? '\n' : ''}${label}: `)
    requestAnimationFrame(() => {
      const el = ref.current
      if (el) {
        el.focus()
        el.setSelectionRange(el.value.length, el.value.length)
      }
    })
  }
  return (
    <div className="mx-auto mt-6 flex w-full max-w-[780px] flex-col gap-4">
      <Tile className="flex flex-col gap-4 p-5 @min-[640px]/plan:p-7" data-testid="plan-prompt">
        <div className="flex flex-col gap-1">
          <h2 className="text-[22px] leading-tight font-[650] tracking-[-0.03em]">What do you need built?</h2>
          <p className="text-[13.5px] leading-[21px] text-muted-foreground">
            Describe it in your own words. A few sentences is enough; we'll ask about anything that's missing.
          </p>
        </div>
        <Textarea
          ref={ref}
          className="min-h-44 text-[14px] leading-[22px]"
          rows={8}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="A place for our testers to keep test cases, run them against the bank app and follow up on failures…"
          data-testid="plan-prompt-input"
        />
        <div className="grid grid-cols-1 gap-x-4 gap-y-1 @min-[560px]/plan:grid-cols-2" data-testid="plan-prompt-hints">
          {HINTS.map((h) => (
            <button
              key={h.label}
              type="button"
              onClick={() => add(h.label)}
              className="group/h flex min-w-0 items-baseline gap-2 rounded-ctl px-2 py-1.5 text-left outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus"
            >
              <span className="shrink-0 text-[12.5px] font-medium text-foreground">{h.label}</span>
              <span className="min-w-0 truncate text-[12px] text-muted-foreground">e.g. {h.hint}</span>
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!prompt.trim() ? (
            <Button size="sm" variant="ghost" onClick={() => setPrompt(EXAMPLE_PROMPT)} data-testid="plan-prompt-example">
              <TextQuote /> Insert an example
            </Button>
          ) : null}
          <div className="flex-1 @min-[960px]/plan:flex-none" />
          <Button disabled={pending || !prompt.trim()} onClick={() => onSubmit(prompt.trim())} data-testid="plan-prompt-submit">
            {pending ? <Spinner /> : <Sparkles />} {pending ? 'Drafting your plan…' : 'Draft the plan'}
          </Button>
        </div>
      </Tile>
      <ol className="grid grid-cols-1 gap-3 px-1 @min-[640px]/plan:grid-cols-3">
        {[
          ['Answer a few questions', 'At most two short rounds, with suggested answers.'],
          ['Review and approve', 'Read the plan as requirements; approve it once.'],
          ['Build', 'Each part is built to the plan, without asking again.'],
        ].map(([t, d], n) => (
          <li key={t} className="flex gap-2.5">
            <span className="inline-flex size-5 shrink-0 items-center justify-center rounded-full glass-orb-clear text-[10.5px] font-semibold text-muted-foreground">{n + 2}</span>
            <div className="min-w-0">
              <div className="text-[12.5px] font-medium">{t}</div>
              <div className="text-[12px] text-muted-foreground">{d}</div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}

/* ── review ───────────────────────────────────────────────────────────────────────────────────────────────────── */

/**
 * The approval panel (sticky; a bottom bar on a phone): status, error and warning counts, what approving means, Save
 * (while there are unsaved edits) and Approve, which is disabled with the reason shown.
 */
export function ApproveBar({
  gate,
  dirty,
  saving,
  approving,
  errors,
  warnings = 0,
  version,
  revises,
  onSave,
  onApprove,
}: {
  gate: ApproveGate
  dirty: boolean
  saving: boolean
  approving: boolean
  errors: number
  warnings?: number
  version?: number
  revises?: number | null
  onSave: () => void
  onApprove: () => void
}) {
  return (
    <Tile className="flex flex-col gap-3 p-4" data-testid="plan-approve-bar">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13.5px] font-semibold">
          {version != null ? `Draft v${version}` : 'Draft'} <span className="font-normal text-muted-foreground">· in review</span>
        </span>
        <div className="flex-1 @min-[960px]/plan:flex-none" />
        {errors ? (
          <Badge variant="error" data-testid="plan-error-count">{errors} error{errors === 1 ? '' : 's'}</Badge>
        ) : (
          <Badge variant="success">no errors</Badge>
        )}
        {warnings ? <Badge variant="warning">{warnings} warning{warnings === 1 ? '' : 's'}</Badge> : null}
      </div>
      {gate.reason ? <span className="text-[12.5px] text-muted-foreground" data-testid="plan-approve-reason">{gate.reason}</span> : null}
      <p className="hidden text-[12.5px] leading-[19px] text-muted-foreground @min-[960px]/plan:block">
        <ShieldCheck className="mr-1 inline size-3.5 align-[-2px] text-primary" aria-hidden />
        Approving signs off this plan{revises != null ? ` as the successor to v${revises}` : ''}. Builders will follow it as written;
        changes after this need a revision, which you review and approve again.
      </p>
      <div className="flex flex-wrap gap-2 @min-[960px]/plan:flex-col">
        <Button size="sm" variant="outline" className="flex-1 @min-[960px]/plan:flex-none" disabled={!dirty || saving} onClick={onSave} data-testid="plan-save">
          {saving ? <Spinner /> : null} Save edits
        </Button>
        <Button size="sm" className="flex-1 @min-[960px]/plan:flex-none" disabled={!gate.allowed || approving} onClick={onApprove} data-testid="plan-approve">
          {approving ? <Spinner /> : <CheckCircle2 />} Approve plan
        </Button>
      </div>
    </Tile>
  )
}

/** The body of the breaking-change confirmation. */
export function BreakingConfirmBody({ diff }: { diff: PlanDiffResult }) {
  const breaking = diff.changes.filter((c) => c.breaking)
  return (
    <div className="flex flex-col gap-2 text-[12.5px]" data-testid="plan-breaking-body">
      <span>{breakingSummary(diff)}.</span>
      <ul className="ml-4 list-disc">
        {breaking.map((c, n) => (
          <li key={n}>
            {c.change} {c.kind} <span className="font-mono">{c.key}</span>
          </li>
        ))}
      </ul>
      <span className="text-muted-foreground">Builders rebuild what it affects; nothing in the tables is dropped.</span>
    </div>
  )
}

function TechnicalPlan({ children }: { children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3" data-testid="plan-technical">
      <div className="flex flex-col gap-0.5 px-1 pt-2">
        <h3 className="text-[17px] font-semibold tracking-[-0.02em]">Technical plan</h3>
        <span className="text-[12.5px] text-muted-foreground">For the builders: the data, the services and how they fit. Open a section to check or edit it.</span>
      </div>
      {children}
    </section>
  )
}

type Mutations = ReturnType<typeof usePlanMutations>

function DraftReview({ pid, draft, mutations: m }: { pid: string; draft: PlanVersion; mutations: Mutations }) {
  const [body, setBody] = useState<PlanBody>(draft.body)
  const [dirty, setDirty] = useState(false)
  const [breaking, setBreaking] = useState<PlanDiffResult | null>(null)
  const [issues, setIssues] = useState<PlanIssue[] | undefined>(draft.issues)
  // Confirming an assumption is a review aid on this screen; the plan body has no field for it.
  const [confirmed, setConfirmed] = useState<Set<string>>(() => new Set())
  const diff = usePlanDiff(pid, draft.base_version != null ? draft.version : null)

  // A newer server draft replaces the local copy unless there are unsaved edits (adjusted during render, not in an effect).
  const [seen, setSeen] = useState(draft)
  if (seen !== draft && !dirty) {
    setSeen(draft)
    setBody(draft.body)
    setIssues(draft.issues)
  }

  const gate = approveGate({ status: draft.status, questions: draft.questions, issues, dirty })
  const errors = errorsOf(issues).length
  const change = (b: PlanBody) => {
    setBody(b)
    setDirty(true)
  }

  const save = () =>
    m.save.mutate(
      { version: draft.version, body, baseUpdatedAt: draft.updated_at },
      {
        onSuccess: (v) => {
          setDirty(false)
          setIssues(v.issues)
          toast.success(`Plan v${v.version} saved`)
        },
        onError: (e) =>
          toast.error(e instanceof ApiError && e.code === 'plan_conflict' ? 'Someone else changed this plan; reload it' : 'Not saved', {
            description: errorMessage(e),
          }),
      },
    )

  const approve = (acceptBreaking = false) =>
    m.approve.mutate(
      { version: draft.version, acceptBreaking },
      {
        onSuccess: (r) => {
          setBreaking(null)
          const created = r.migrations.length
          toast.success(`Plan v${r.version} approved`, {
            description: created ? `${created} table change${created === 1 ? '' : 's'} recorded` : undefined,
          })
        },
        onError: (e) => {
          if (e instanceof ApiError && e.code === 'breaking_change') setBreaking((e.details as { diff: PlanDiffResult }).diff)
          else if (e instanceof ApiError && e.code === 'plan_invalid') {
            setIssues((e.details as { issues?: PlanIssue[] }).issues ?? issues)
            toast.error('Not approved: the plan has errors')
          } else toast.error('Not approved', { description: errorMessage(e) })
        },
      },
    )

  return (
    <>
      <PlanColumns
        main={
          <>
            {draft.base_version != null ? (
              <>
                <Callout tone="info" icon={<PencilLine />} data-testid="plan-revision-note">
                  This is a revision of v{draft.base_version}. Check what changes below, then approve it to replace v{draft.base_version}.
                </Callout>
                {diff.data ? <PlanDiff diff={diff.data} against={diff.data.against} /> : null}
              </>
            ) : null}
            <IssueSummary issues={issues ?? []} />
            <RequirementsDoc
              body={body}
              issues={issues}
              editable
              onChange={change}
              confirmed={confirmed}
              onConfirm={(a) => setConfirmed((c) => new Set(c).add(a))}
              meta={
                <>
                  <span>Draft v{draft.version}</span>
                  <Dot />
                  <span>updated {when(draft.updated_at)}</span>
                  {draft.round ? (
                    <>
                      <Dot />
                      <span>{draft.round} round{draft.round === 1 ? '' : 's'} of questions</span>
                    </>
                  ) : null}
                </>
              }
            />
            <WhatWeBuild body={body} issues={issues} />
            <TechnicalPlan>
              <PlanSections body={body} issues={issues} editable onChange={change} />
            </TechnicalPlan>
          </>
        }
        aside={
          <ApproveBar
            gate={gate}
            dirty={dirty}
            saving={m.save.isPending}
            approving={m.approve.isPending}
            errors={errors}
            warnings={(issues ?? []).length - errors}
            version={draft.version}
            revises={draft.base_version}
            onSave={save}
            onApprove={() => approve(false)}
          />
        }
      />
      <AlertDialog open={!!breaking} onOpenChange={(o) => !o && setBreaking(null)}>
        <AlertDialogContent data-testid="plan-breaking-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>Approve a breaking change?</AlertDialogTitle>
            <AlertDialogDescription render={<div />}>{breaking ? <BreakingConfirmBody diff={breaking} /> : null}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => approve(true)} data-testid="plan-breaking-confirm">
              Approve anyway
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

/* ── approved / building ──────────────────────────────────────────────────────────────────────────────────────── */

const STATE_BADGE: Record<ModuleBuildState, 'success' | 'warning' | 'error' | 'neutral'> = {
  built: 'success',
  partial: 'warning',
  stale: 'warning',
  'not built': 'neutral',
  unknown: 'neutral',
}

const STATE_ICON: Record<ModuleBuildState, ReactNode> = {
  built: <CheckCircle2 className="text-success" />,
  partial: <CircleDashed className="text-warning-text" />,
  stale: <RefreshCw className="text-warning-text" />,
  'not built': <Circle className="text-muted-foreground" />,
  unknown: <CircleHelp className="text-muted-foreground" />,
}

/** The build order as a progress list: each module with its state and a Build button; apps with their screens. */
export function BuildOrderList({
  slug,
  order,
  states,
  apps = [],
}: {
  slug: string
  order: string[]
  states: Record<string, ModuleBuildState>
  apps?: PlanApp[]
}) {
  return (
    <WellList className="mt-3" data-testid="plan-build-order">
      {order.map((k, n) => {
        const isApp = k.startsWith('app:')
        const app = isApp ? apps.find((a) => a.key === k.slice(4)) : undefined
        const state = isApp ? null : (states[k] ?? 'unknown')
        return (
          <WellRow key={`${n}-${k}`} className="gap-3 py-2.5" data-testid={`plan-build-${k}`}>
            <span className="w-4 shrink-0 text-right font-mono text-[11.5px] text-muted-foreground">{n + 1}</span>
            <span className="shrink-0 [&_svg]:size-4" aria-hidden>
              {state ? STATE_ICON[state] : app?.app_id ? STATE_ICON.built : STATE_ICON['not built']}
            </span>
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="truncate text-[13px] font-medium">{isApp ? (app?.name ?? k.slice(4)) : humanize(k)}</span>
              <span className="truncate text-[11.5px] text-muted-foreground">
                {isApp ? `app · ${app?.screens.map((s) => s.name).join(', ') || 'no screens'}` : <span className="font-mono">{k}</span>}
              </span>
            </span>
            {isApp ? (
              <Badge variant={app?.app_id ? 'success' : 'neutral'} data-testid={`plan-app-state-${k.slice(4)}`}>
                {app?.app_id ? 'built' : 'not built'}
              </Badge>
            ) : (
              <Badge variant={STATE_BADGE[state as ModuleBuildState]} data-testid={`plan-state-${k}`}>{state}</Badge>
            )}
            {!isApp ? (
              <Button
                size="xs"
                variant={state === 'built' ? 'ghost' : 'secondary'}
                render={<Link to="/p/$project/backend/$module" params={{ project: slug, module: buildModule(k, state ?? 'unknown') }} search={buildSearch(k)} />}
                data-testid={`plan-build-btn-${k}`}
              >
                <Hammer /> {state === 'built' ? 'Rebuild' : 'Build'}
              </Button>
            ) : null}
          </WellRow>
        )
      })}
    </WellList>
  )
}

/** Build the approved plan in one go (backend modules first, then the UI), or follow the build that is running. */
function PlanBuildPanel({ slug, approved, progress }: { slug: string; approved: PlanVersion; progress: { built: number; total: number } }) {
  const { pid } = useProjectCtx()
  const navigate = useNavigate()
  const builds = useBuilds(pid)
  const m = useBuildMutations(pid)
  const action = buildAction(progress, activeBuild(builds.data))
  const open = (id: string) => navigate({ to: '/p/$project/build/$build', params: { project: slug, build: id } })
  const start = () => {
    const appKey = pickAppKey(approved.body.apps)
    m.start.mutate(
      { ...(appKey ? { app_key: appKey } : {}), ...(action.label === 'Build again' ? { rebuild: true } : {}) },
      { onSuccess: (b) => open(b.id), onError: (e) => toast.error('Build not started', { description: errorMessage(e) }) },
    )
  }
  return (
    <div className="flex flex-col gap-2.5" data-testid="plan-build-panel">
      <Button
        size="sm"
        disabled={m.start.isPending}
        onClick={() => (action.kind === 'open' ? open(action.id) : start())}
        data-testid="plan-build-start"
      >
        {m.start.isPending ? <Spinner /> : <Hammer />} {action.label}
      </Button>
      {builds.data?.length ? (
        <div className="flex flex-col gap-1.5">
          <span className="text-[12px] font-medium text-muted-foreground">Builds</span>
          <BuildHistory builds={builds.data} slug={slug} />
        </div>
      ) : null}
    </div>
  )
}

function ApprovedPlan({
  slug,
  approved,
  compact,
  mutations: m,
  states,
  progress,
  checking,
  checkKnown,
  smokePassed,
  preflight,
}: {
  slug: string
  approved: PlanVersion
  compact: boolean
  mutations: Mutations
  states: Record<string, ModuleBuildState>
  progress: { built: number; total: number }
  checking: boolean
  checkKnown: boolean
  smokePassed: boolean
  preflight: Preflight[]
}) {
  const [reviseOpen, setReviseOpen] = useState(false)
  const [prompt, setPrompt] = useState('')
  const [docOpen, setDocOpen] = useState(!compact)
  const order = orderOf(approved.body)

  const revise = (p?: string) =>
    m.revise.mutate({ prompt: p }, {
      onSuccess: (v) => {
        setReviseOpen(false)
        setPrompt('')
        toast.success(`Draft v${v.version} created`, {
          description: v.diff.changes.length ? breakingSummary(v.diff) : 'A copy of the approved plan to edit',
        })
      },
      onError: (e) => toast.error('Not revised', { description: errorMessage(e) }),
    })

  const signed = (
    <>
      <span>v{approved.version}</span>
      <Dot />
      <span data-testid="plan-approved-by">
        approved{approved.approved_by ? ` by ${approved.approved_by}` : ''}
        {approved.approved_at ? ` on ${when(approved.approved_at)}` : ''}
      </span>
    </>
  )

  return (
    <PlanColumns
      main={
        <>
          <Tile className="p-4" data-testid="plan-approved">
            <SectionHeader
              title="Build progress"
              sub={checking ? 'checking…' : checkKnown ? `${progress.built} of ${progress.total} modules built` : 'state unknown until the first check'}
              icon={<Hammer />}
              hue="backend"
            />
            {approved.body.integrations.length && !smokePassed ? (
              <Callout tone="warn" className="mt-3">Integrations not proven yet: no smoke run has passed.</Callout>
            ) : null}
            {preflight.length ? (
              <Callout tone="warn" className="mt-3 items-start" data-testid="plan-preflight">
                <span className="font-medium">To set up before building: </span>
                {preflight.map((p) => p.where ?? p.message).join(', ')}.{' '}
                <Link to="/p/$project/secrets" params={{ project: slug }} className="underline underline-offset-2">
                  Open secrets
                </Link>
              </Callout>
            ) : null}
            <BuildOrderList slug={slug} order={order} states={states} apps={approved.body.apps} />
          </Tile>
          {reviseOpen ? (
            <Tile className="p-4" data-testid="plan-revise">
              <SectionHeader title="Revise the plan" sub="You'll see what changes before approving again" icon={<PencilLine />} hue="flow" />
              <Textarea
                className="mt-3"
                rows={3}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="What should change? Leave empty to edit a copy by hand."
                data-testid="plan-revise-input"
              />
              <div className="mt-3 flex justify-end gap-2">
                <Button size="sm" variant="ghost" onClick={() => setReviseOpen(false)}>Cancel</Button>
                <Button size="sm" disabled={m.revise.isPending} onClick={() => revise(prompt)} data-testid="plan-revise-submit">
                  {m.revise.isPending ? <Spinner /> : null} {prompt.trim() ? 'Revise' : 'Edit a copy'}
                </Button>
              </div>
            </Tile>
          ) : null}
          {compact ? (
            <Button
              variant="ghost"
              size="sm"
              className="self-start text-muted-foreground"
              aria-expanded={docOpen}
              onClick={() => setDocOpen((o) => !o)}
              data-testid="plan-doc-toggle"
            >
              <ChevronDown className={cn('transition-transform', docOpen && 'rotate-180')} />
              {docOpen ? 'Hide the full plan' : 'Show the full plan: requirements, what we build, technical plan'}
            </Button>
          ) : null}
          {docOpen ? (
            <>
              <RequirementsDoc body={approved.body} issues={approved.issues} editable={false} onChange={() => undefined} meta={signed} />
              <WhatWeBuild body={approved.body} issues={approved.issues} />
              <TechnicalPlan>
                <PlanSections body={approved.body} issues={approved.issues} editable={false} onChange={() => undefined} />
              </TechnicalPlan>
            </>
          ) : null}
        </>
      }
      aside={
        <Tile className="flex flex-col gap-3 p-4" data-testid="plan-approved-panel">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[13.5px] font-semibold">Plan v{approved.version}</span>
            <Badge variant="success"><CheckCircle2 /> approved</Badge>
          </div>
          <span className="hidden text-[12.5px] text-muted-foreground @min-[960px]/plan:block">
            {approved.approved_by ? `By ${approved.approved_by}` : 'Approved'}
            {approved.approved_at ? ` · ${when(approved.approved_at)}` : ''}
          </span>
          <div className="flex flex-col gap-1.5">
            <span className="text-[12.5px]" data-testid="plan-built-count">
              {checkKnown ? `${progress.built} of ${progress.total} modules built` : `${progress.total} modules to build`}
            </span>
            <Progress value={progress.total ? (progress.built / progress.total) * 100 : 0} />
          </div>
          <PlanBuildPanel slug={slug} approved={approved} progress={progress} />
          <p className="hidden text-[12.5px] leading-[19px] text-muted-foreground @min-[960px]/plan:block">
            Builders follow this plan as written. To change it, revise: you see exactly what changes before approving again.
          </p>
          <div className="flex flex-wrap gap-2 @min-[960px]/plan:flex-col">
            <Button
              size="sm"
              variant={reviseOpen ? 'secondary' : 'outline'}
              className="flex-1 @min-[960px]/plan:flex-none"
              onClick={() => setReviseOpen((o) => !o)}
              data-testid="plan-revise-toggle"
            >
              <PencilLine /> Revise plan
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="flex-1 @min-[960px]/plan:flex-none"
              render={<Link to="/p/$project/knowledge" params={{ project: slug }} />}
            >
              <BookOpen /> Knowledge
            </Button>
          </div>
        </Tile>
      }
    />
  )
}
