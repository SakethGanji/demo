/**
 * Release sheet (UI-DESIGN §3.8, `release.png`, §9 D10). Environment cards (draft → release),
 * a dry-run plan (snapshot the draft environment, or promote its latest snapshot) with checks and
 * the filtered diff, then the real write; history per environment with rollback on release envs.
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Rocket, RotateCcw, TriangleAlert } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useProjectApps, useProjectWorkflows } from '@/features/project/hooks/useProjectData'
import { pluralize, timeAgo } from '@/features/project/lib/format'
import { releasesApi } from '@/shared/api/endpoints'
import { ApiError, errorMessage, isApiError } from '@/shared/api/http'
import { invalidate, qk } from '@/shared/api/queryKeys'
import type { Project, Release, ReleaseCheck, ReleaseDiff } from '@/shared/api/types'
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
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Segmented } from '@/shared/components/ui/segmented'
import { Beam, Stepper, type Step as FlowStep, type StepState } from '@/shared/components/ui/stepper'
import { Callout, Well } from '@/shared/components/ui/surface'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/shared/components/ui/sheet'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Spinner } from '@/shared/components/ui/spinner'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/components/ui/tooltip'
import { floatingSheetCls } from '@/features/settings/SettingsSheet'
import { cn } from '@/shared/lib/utils'
import { ChecksList } from './ChecksList'
import { SubworkflowEdges } from './SubworkflowEdges'
import { groupChecks, groupStatus, type CheckGroup } from './checks'

interface DryRun {
  checks: ReleaseCheck[]
  diff?: ReleaseDiff
}

/** What the last real write returned when it did not succeed (D10 codes). */
type WriteResult =
  | { kind: 'blocked'; checks: ReleaseCheck[]; number?: number; env: string }
  | { kind: 'blocked-source'; message: string }

type Step = 'snapshot' | `promote:${string}`

const CHECK_REASON: Record<string, string> = {
  secrets_present: 'secrets',
  app_calls_resolve: 'apps',
  handlers_exist: 'handlers',
  modules_parse: 'syntax',
}

export function ReleaseSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" showCloseButton className={`${floatingSheetCls} data-[side=right]:w-[520px]`} data-testid="release-sheet">
        {open && <ReleaseBody />}
      </SheetContent>
    </Sheet>
  )
}

/** The draft environment snapshots are taken from, and the environments they are promoted to. */
export function releasePipeline(project: Project) {
  const envs = project.environments
  const source =
    envs.find((e) => e.name === project.default_environment && e.kind === 'draft') ?? envs.find((e) => e.kind === 'draft') ?? envs[0]
  return { source: source?.name ?? project.default_environment, targets: envs.filter((e) => e.name !== source?.name) }
}

function ReleaseBody() {
  const { project, pid } = useProjectCtx()
  const qc = useQueryClient()
  const { source, targets } = releasePipeline(project)
  const releases = useQuery({ queryKey: qk.releases(pid), queryFn: () => releasesApi.list(pid) })
  const all = useMemo(() => releases.data ?? [], [releases.data])
  const latestSnap = useMemo(
    () => all.filter((r) => r.environment === source && r.status !== 'blocked').sort((a, b) => b.number - a.number)[0],
    [all, source],
  )

  const [stepPick, setStepPick] = useState<Step | null>(null)
  const step: Step = stepPick ?? (latestSnap && targets[0] ? `promote:${targets[0].name}` : 'snapshot')
  const promoteTo = step.startsWith('promote:') ? step.slice('promote:'.length) : null
  const targetEnv = promoteTo ?? source
  const [notes, setNotes] = useState('')
  const [writing, setWriting] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [result, setResult] = useState<WriteResult | null>(null)
  const [promoted, setPromoted] = useState<number | null>(null)
  useEffect(() => {
    setResult(null)
    setPromoted(null)
  }, [step])

  const canDry = releases.isSuccess && (!promoteTo || !!latestSnap)
  const dry = useQuery({
    queryKey: [...qk.releasesPrefix(pid), 'dry-run', step, promoteTo ? latestSnap?.id : null],
    queryFn: async (): Promise<DryRun> =>
      (promoteTo
        ? await releasesApi.promote(pid, latestSnap!.id, { to_environment: promoteTo }, true)
        : await releasesApi.create(pid, { environment: source }, true)) as unknown as DryRun,
    enabled: canDry,
    retry: false,
    staleTime: 0,
    gcTime: 0,
  })

  const rerun = () => {
    setResult(null)
    void dry.refetch()
  }

  const shownChecks = result?.kind === 'blocked' ? result.checks : (dry.data?.checks ?? [])
  const failed = shownChecks.filter((c) => c.status === 'fail').length
  const warned = shownChecks.filter((c) => c.status === 'warn').length
  const ready = canDry && dry.isSuccess && !dry.isFetching && failed === 0 && !result

  const write = async () => {
    setWriting(true)
    setResult(null)
    const toastId = promoteTo
      ? toast.loading(`Promoting to ${promoteTo}…`, { description: `snapshot #${latestSnap?.number} · ${source} → ${promoteTo}` })
      : undefined
    try {
      const rel = (promoteTo
        ? await releasesApi.promote(pid, latestSnap!.id, { to_environment: promoteTo, ...(notes.trim() ? { notes: notes.trim() } : {}) })
        : await releasesApi.create(pid, { environment: source, ...(notes.trim() ? { notes: notes.trim() } : {}) })) as Release
      setNotes('')
      await invalidate.releasesChanged(qc, pid)
      if (promoteTo) {
        setPromoted(rel.number)
        toast.success(`${promoteTo} now serves release #${rel.number}`, {
          id: toastId,
          description:
            rel.environment === project.live_environment ? `v${rel.number} is live. Published apps were re-published from it.` : `v${rel.number} is live.`,
        })
      } else if (targets[0]) {
        // In-sheet effect (the card and history update): no toast, move on to promoting it.
        setStepPick(`promote:${targets[0].name}`)
      }
    } catch (e) {
      if (toastId !== undefined && (isApiError(e, 'checks_failed') || isApiError(e, 'release_blocked'))) toast.dismiss(toastId)
      if (isApiError(e, 'checks_failed')) {
        const d = (e as ApiError).details as { checks?: ReleaseCheck[]; number?: number }
        setResult({ kind: 'blocked', checks: d.checks ?? [], number: d.number, env: targetEnv })
        await invalidate.releasesChanged(qc, pid)
      } else if (isApiError(e, 'release_blocked')) {
        setResult({ kind: 'blocked-source', message: 'This snapshot was blocked; create a new one.' })
      } else {
        toast.error(errorMessage(e), { id: toastId })
      }
    } finally {
      setWriting(false)
    }
  }

  const stepOptions = [
    { key: 'snapshot' as Step, label: `Snapshot ${source}`, testId: 'release-step-snapshot' },
    ...targets.map((t) => ({ key: `promote:${t.name}` as Step, label: `Promote → ${t.name}`, testId: `release-step-promote-${t.name}` })),
  ]
  const title = promoteTo
    ? latestSnap
      ? `Promote snapshot #${latestSnap.number} → ${promoteTo}`
      : `Promote → ${promoteTo}`
    : `Snapshot ${source}${latestSnap ? ` (#${latestSnap.number + 1})` : ''}`
  const primaryLabel = promoteTo ? `Promote to ${promoteTo}` : 'Create snapshot'

  const groups: Record<CheckGroup, ReleaseCheck[]> = {
    code: groupChecks(shownChecks, 'code'),
    secrets: groupChecks(shownChecks, 'secrets'),
    routes: groupChecks(shownChecks, 'routes'),
  }
  const order: CheckGroup[] = ['code', 'secrets', 'routes']
  const firstFail = order.findIndex((g) => groupStatus(groups[g]) === 'fail')
  const gateState = (i: number): StepState => (firstFail < 0 || i < firstFail ? 'done' : i === firstFail ? 'current' : 'pending')
  const blockedBadge = (
    <Badge variant="warning" data-testid="release-flow-blocked">
      blocked
    </Badge>
  )
  const diff = canDry && dry.data?.diff && !result ? dry.data.diff : null
  const flow: FlowStep[] = [
    {
      key: 'code',
      testId: 'release-flow-code',
      title: <>Check code {gateState(0) === 'current' && blockedBadge}</>,
      state: gateState(0),
      open: true,
      content: <ChecksList checks={groups.code} targetEnv={targetEnv} onRerun={rerun} testId="release-checks-code" />,
    },
    {
      key: 'secrets',
      testId: 'release-flow-secrets',
      title: <>Secrets {gateState(1) === 'current' && blockedBadge}</>,
      state: gateState(1),
      open: true,
      content: <ChecksList checks={groups.secrets} targetEnv={targetEnv} onRerun={rerun} testId="release-checks-secrets" />,
    },
    {
      key: 'routes',
      testId: 'release-flow-routes',
      title: <>Review routes {gateState(2) === 'current' && blockedBadge}</>,
      state: gateState(2),
      open: true,
      content: (
        <div className="flex flex-col gap-2.5">
          <ChecksList checks={groups.routes} targetEnv={targetEnv} onRerun={rerun} testId="release-checks-routes" />
          {diff && <DiffChips diff={diff} env={targetEnv} />}
          {promoteTo && latestSnap && <SubworkflowEdges releaseId={latestSnap.id} />}
        </div>
      ),
    },
    {
      key: 'promote',
      testId: 'release-flow-promote',
      title: promoteTo ? `Promote to ${promoteTo}` : `Snapshot ${source}`,
      state: promoted != null ? 'done' : firstFail >= 0 ? 'pending' : 'current',
      meta:
        promoted != null ? (
          <>Release #{promoted} is live in {promoteTo} · just now</>
        ) : writing ? (
          <>Promoting to {promoteTo}…</>
        ) : promoteTo ? (
          <>
            snapshot #{latestSnap?.number} · {source} → {promoteTo}
          </>
        ) : (
          <>freezes what {source} serves now</>
        ),
      content: !promoteTo && canDry ? (
        <div className="rounded-ctl bg-raise p-1.5 shadow-raise-sm">
          <Input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Notes (optional), e.g. refunds endpoint"
            className="h-7 text-[12.5px]"
            data-testid="release-notes"
          />
        </div>
      ) : undefined,
    },
  ]

  return (
    <>
      <div className="flex h-[52px] shrink-0 items-center gap-2 pl-5 pr-14">
        <SheetTitle className="min-w-0 truncate text-[15px] font-semibold tracking-[-0.012em]">Release {project.name}</SheetTitle>
      </div>
      <SheetDescription className="px-5 pb-3 text-[13px] leading-[1.5] text-muted-foreground">
        A release freezes routes, modules and app versions.{' '}
        {targets.some((t) => t.kind === 'release')
          ? `${targets.filter((t) => t.kind === 'release').map((t) => t.name).join(', ')} serves the active release; `
          : ''}
        {source} always serves your latest saved code.
      </SheetDescription>

      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden" data-testid="release-panel">
        <div className="flex flex-col gap-4 px-5 pb-5 pt-1">
          <EnvCards
            project={project}
            source={source}
            releases={all}
            loading={releases.isLoading}
            targetEnv={targetEnv}
            gate={failed ? 'blocked' : ready ? 'ready' : 'idle'}
          />
          {releases.isError && (
            <Callout tone="error" role="alert" data-testid="release-error">
              Couldn't load releases · {errorMessage(releases.error)}{' '}
              <Button type="button" variant="link" size="xs" className="h-auto px-0 align-baseline" onClick={() => releases.refetch()}>
                Retry
              </Button>
            </Callout>
          )}

          {targets.length > 0 && (
            <Segmented<Step>
              value={step}
              onChange={setStepPick}
              aria-label="Release step"
              data-testid="release-steps"
              options={stepOptions.map((option) => ({ ...option, disabled: !releases.isSuccess || writing }))}
              className="self-start"
            />
          )}

          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-[13.5px] font-semibold" data-testid="release-plan-title">
              {title}
            </span>
            {canDry && dry.isSuccess && !dry.isFetching && (
              <Badge
                variant={failed ? 'destructive' : warned ? 'warning' : 'success'}
                data-testid="release-check-summary"
              >
                {failed
                  ? `${pluralize(failed, 'check')} failed`
                  : warned
                    ? `checks pass · ${pluralize(warned, 'warning')}`
                    : 'checks pass'}
              </Badge>
            )}
            <div className="flex-1" />
            {canDry && <span className="shrink-0 text-xs text-muted-foreground">dry run · nothing stored</span>}
          </div>

          {promoteTo && releases.isSuccess && !latestSnap ? (
            <Well className="px-4 py-6 text-center text-[12.5px] text-muted-foreground" data-testid="release-empty">
              No snapshots yet. Create one from {source}.
              <div className="mt-2.5">
                <Button size="sm" variant="secondary" onClick={() => setStepPick('snapshot')} data-testid="release-empty-snapshot">
                  Snapshot {source}
                </Button>
              </div>
            </Well>
          ) : result?.kind === 'blocked-source' ? (
            <Callout
              tone="warn"
              icon={<TriangleAlert />}
              role="alert"
              data-testid="release-409-blocked-source"
              className="py-2.5"
            >
              <div className="flex items-center gap-2">
                <span className="flex-1">{result.message}</span>
                <Button size="xs" variant="secondary" onClick={() => setStepPick('snapshot')}>
                  Snapshot {source}
                </Button>
              </div>
            </Callout>
          ) : !canDry || dry.isLoading || (dry.isFetching && !dry.data) ? (
            <Well className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground" data-testid="release-loading">
              <Spinner className="size-3.5" /> Running checks…
            </Well>
          ) : dry.isError ? (
            <Callout tone="error" role="alert" data-testid="release-dry-error" className="py-2.5">
              {isApiError(dry.error, 'release_blocked') ? 'This snapshot was blocked; create a new one.' : errorMessage(dry.error)}{' '}
              <Button type="button" variant="link" size="xs" className="h-auto px-0 align-baseline" onClick={rerun}>
                Retry
              </Button>
            </Callout>
          ) : (
            <div
              className={cn('flex flex-col gap-2 transition-opacity duration-(--d-base)', dry.isFetching && 'opacity-60')}
              data-testid={failed ? 'release-blocked' : 'release-ready'}
              data-stored={result?.kind === 'blocked' ? 'true' : undefined}
            >
              {result?.kind === 'blocked' && (
                <Callout tone="warn" icon={<TriangleAlert />} role="alert" data-testid="release-stored-blocked">
                  {result.number ? `Release #${result.number} of ${result.env} was stored as blocked and is not served.` : 'The checks failed.'} Fix
                  the failed check, then re-run.
                </Callout>
              )}
              <Stepper steps={flow} testId="release-stepper" />
            </div>
          )}

          <History releases={all} project={project} loading={releases.isLoading} source={source} />
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2 px-5 pb-4 pt-3">
        <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground" data-testid="release-footer-hint">
          {failed
            ? 'Fix the failed check, then re-run.'
            : promoteTo && !latestSnap
              ? `Snapshot ${source} first.`
              : warned
                ? 'Warnings never block a release.'
                : promoteTo
                  ? `${promoteTo} switches to this snapshot.`
                  : `Freezes what ${source} serves now.`}
        </span>
        <Button variant="ghost" size="sm" disabled={!canDry || dry.isFetching} onClick={rerun} data-testid="release-rerun">
          Re-run checks
        </Button>
        <Button
          size="sm"
          disabled={!ready || writing}
          onClick={() => (promoteTo ? setConfirmOpen(true) : void write())}
          data-testid={promoteTo ? 'release-promote' : 'release-create'}
        >
          {writing ? <Spinner className="size-3.5" /> : <Rocket />}
          {primaryLabel}
        </Button>
      </div>

      <Beam on={writing && !!promoteTo} />

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent data-testid="release-promote-confirm-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>
              Promote snapshot #{latestSnap?.number} to {promoteTo}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              {promoteTo} starts serving this snapshot's routes and modules
              {promoteTo === project.live_environment ? ', and published apps are re-published from it' : ''}. You can roll back afterwards.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel size="sm">Cancel</AlertDialogCancel>
            <AlertDialogAction
              size="sm"
              onClick={() => {
                setConfirmOpen(false)
                void write()
              }}
              data-testid="release-promote-confirm"
            >
              Promote to {promoteTo}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

// ── environment cards ───────────────────────────────────────────────────────

function EnvCards({
  project,
  source,
  releases,
  loading,
  targetEnv,
  gate,
}: {
  project: Project
  source: string
  releases: Release[]
  loading: boolean
  targetEnv: string
  gate: 'blocked' | 'ready' | 'idle'
}) {
  const { pid } = useProjectCtx()
  const qc = useQueryClient()
  const [rolling, setRolling] = useState<string | null>(null)
  const [noPrevious, setNoPrevious] = useState<string[]>([])
  const ordered = [...project.environments].sort((a, b) => Number(b.name === source) - Number(a.name === source))

  const rollback = async (env: string, toId: string) => {
    setRolling(env)
    try {
      const rel = (await releasesApi.rollback(pid, env, { release_id: toId })) as Release
      await invalidate.releasesChanged(qc, pid)
      const warns = (rel.checks ?? []).filter((c) => c.status === 'warn')
      toast.success(`${env} rolled back to release #${rel.number}`, {
        description: warns.length ? `Warnings: ${warns.map((c) => c.message ?? c.id.replace(/_/g, ' ')).join(' · ')}` : undefined,
      })
    } catch (e) {
      if (isApiError(e, 'no_previous_release')) setNoPrevious((n) => [...n, env])
      else toast.error(errorMessage(e))
    } finally {
      setRolling(null)
    }
  }

  return (
    <div className="flex items-stretch" data-testid="release-envs">
      {ordered.map((e, i) => {
        const mine = releases.filter((r) => r.environment === e.name)
        const active = mine.find((r) => r.is_active)
        const snap = mine.filter((r) => r.status !== 'blocked').sort((a, b) => b.number - a.number)[0]
        const prev =
          e.kind === 'release'
            ? mine
                .filter((r) => (r.status === 'superseded' || r.status === 'rolled_back') && (!active || r.number < active.number))
                .sort((a, b) => b.number - a.number)[0]
            : undefined
        const blockedRollback = e.kind === 'release' && (!prev || noPrevious.includes(e.name))
        return (
          <div key={e.name} className="flex min-w-0 flex-1 items-stretch">
            {i > 0 && <Gate tone={e.name === targetEnv ? gate : 'idle'} />}
            <Well
              className="flex min-w-0 flex-1 flex-col p-3"
              data-testid={`release-env-${e.name}`}
              data-active-release={active?.number}
            >
              <div className="flex items-center gap-2">
                <Badge variant="neutral" className="font-mono">
                  {e.name}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  {e.kind}
                  {e.name === project.live_environment ? ' · live' : ''}
                </span>
              </div>
              {loading ? (
                <Skeleton className="mt-2.5 h-8 w-3/4" />
              ) : e.kind === 'draft' ? (
                <>
                  <div className="mt-2 font-medium">Latest saved code</div>
                  <div className="truncate text-xs text-muted-foreground">
                    {snap ? `snapshot #${snap.number} taken ${timeAgo(snap.created_at)}` : 'no snapshots yet'}
                  </div>
                </>
              ) : (
                <>
                  <div className="mt-2 font-medium" data-testid={`release-env-${e.name}-serving`}>
                    {active ? `Release #${active.number}` : 'No release yet'}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    {active
                      ? `${active.source_release_id ? `from ${sourceLabel(releases, active.source_release_id)} · ` : ''}created ${timeAgo(active.created_at)}`
                      : 'serving latest saved code'}
                  </div>
                  {active && (
                    <div className="mt-2">
                      <Tooltip>
                        <TooltipTrigger render={<span className="inline-flex" tabIndex={blockedRollback ? 0 : -1} />}>
                          <Button
                            variant="outline"
                            size="xs"
                            disabled={blockedRollback || rolling === e.name}
                            onClick={() => prev && void rollback(e.name, prev.id)}
                            data-testid={`release-rollback-${e.name}`}
                            data-disabled-reason={blockedRollback ? 'no_previous_release' : undefined}
                          >
                            <RotateCcw /> {prev ? `Roll back to #${prev.number}` : 'Roll back'}
                          </Button>
                        </TooltipTrigger>
                        {blockedRollback && <TooltipContent>No previous release in {e.name} to roll back to</TooltipContent>}
                      </Tooltip>
                    </div>
                  )}
                </>
              )}
            </Well>
          </div>
        )
      })}
    </div>
  )
}

/** The path between two environments: a line with one node that shows the gate (blocked, ready, or idle). */
function Gate({ tone }: { tone: 'blocked' | 'ready' | 'idle' }) {
  return (
    <span className="relative flex w-9 shrink-0 items-center justify-center" aria-hidden data-testid="release-gate" data-gate={tone}>
      <span className="absolute inset-x-0 h-px bg-hairline" />
      <span
        className={cn(
          'relative size-2 rounded-full transition-colors duration-(--d-base)',
          tone === 'blocked' && 'bg-warning shadow-[0_0_0_3px_var(--warning-soft)]',
          tone === 'ready' && 'bg-primary shadow-[0_0_8px_var(--glow)]',
          tone === 'idle' && 'bg-faint',
        )}
      />
    </span>
  )
}

// ── diff ────────────────────────────────────────────────────────────────────

function DiffChips({ diff, env }: { diff: ReleaseDiff; env: string }) {
  const { pid } = useProjectCtx()
  const apps = useProjectApps(pid)
  const wfs = useProjectWorkflows(pid)
  const routes = (diff.routes ?? []) as Array<{ kind: string; route: string; breaking?: boolean }>
  const added = routes.filter((r) => r.kind === 'route_added').length
  const removed = routes.filter((r) => r.kind === 'route_removed').length
  const changed = new Set(routes.filter((r) => r.kind !== 'route_added' && r.kind !== 'route_removed').map((r) => r.route)).size
  const breaking = routes.filter((r) => r.breaking).length
  const appName = (id: string) => apps.data?.find((a) => a.id === id)?.name ?? id
  const wfName = (id: string) => wfs.data?.find((w) => w.id === id)?.name ?? id
  const chips: Array<{ label: string; variant: 'success' | 'destructive' | 'warning' | 'secondary'; title?: string }> = []
  if (added) chips.push({ label: `+${pluralize(added, 'route')}`, variant: 'success' })
  if (removed) chips.push({ label: `−${pluralize(removed, 'route')}`, variant: 'destructive' })
  if (changed)
    chips.push({
      label: `${pluralize(changed, 'route')} changed`,
      variant: breaking ? 'warning' : 'secondary',
      title: routes.map((r) => `${r.route} ${r.kind.replace(/_/g, ' ')}`).join('\n'),
    })
  for (const a of diff.apps ?? [])
    chips.push({
      label: a.to_version == null ? `${appName(a.app_id)} removed` : a.from_version == null ? `${appName(a.app_id)} added` : `${appName(a.app_id)} → new version`,
      variant: 'secondary',
    })
  for (const w of diff.workflows ?? [])
    chips.push({ label: w.to_version == null ? `${wfName(w.workflow_id)} removed` : `${wfName(w.workflow_id)} updated`, variant: 'secondary' })
  for (const s of diff.secrets ?? [])
    chips.push({ label: `${s.key} ${s.change.replace(/_/g, ' ')}`, variant: s.change === 'removed' ? 'warning' : 'secondary' })

  return (
    <div className="flex flex-col gap-1.5" data-testid="release-diff">
      <span className="text-[12px] text-muted-foreground">What changes in {env}</span>
      <div className="flex flex-wrap items-center gap-1.5 text-[12.5px]">
        {chips.length === 0 ? (
          <span className="text-muted-foreground" data-testid="release-diff-empty">
            Nothing changes — same as what {env} has now.
          </span>
        ) : (
          <>
            {chips.map((c, i) => (
              <Badge key={i} variant={c.variant} title={c.title} className="max-w-full truncate">
                {c.label}
              </Badge>
            ))}
            <span className="text-muted-foreground">unchanged items hidden</span>
          </>
        )}
      </div>
    </div>
  )
}

// ── history ─────────────────────────────────────────────────────────────────

function History({ releases, project, loading, source }: { releases: Release[]; project: Project; loading: boolean; source: string }) {
  const rows = [...releases].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.number - a.number)
  return (
    <div className="flex flex-col gap-2" data-testid="release-history">
      <span className="text-[13.5px] font-semibold tracking-[-0.01em]">History</span>
      <Well className="px-4 py-1">
      {loading ? (
        <Skeleton className="h-8 w-full" />
      ) : rows.length === 0 ? (
        <div className="py-2 text-[12.5px] text-muted-foreground" data-testid="release-history-empty">
          No snapshots yet. Create one from {source}.
        </div>
      ) : (
        rows.map((r) => (
          <div
            key={r.id}
            className="flex min-h-9 items-center gap-2 border-b border-hairline py-1.5 text-[12.5px] last:border-0"
            data-testid={`release-row-${r.environment}-${r.number}`}
            data-status={r.status}
          >
            <Badge variant="neutral" className="font-mono">
              {r.environment}
            </Badge>
            <span className="font-mono">#{r.number}</span>
            <span className="min-w-0 flex-1 truncate text-muted-foreground">
              {timeAgo(r.created_at)}
              {r.notes ? ` · "${r.notes}"` : ''}
              {r.source_release_id ? ` · from ${sourceLabel(releases, r.source_release_id)}` : ''}
            </span>
            <StatusPill release={r} project={project} />
          </div>
        ))
      )}
      </Well>
    </div>
  )
}

function sourceLabel(all: Release[], id: string) {
  const s = all.find((r) => r.id === id)
  return s ? `${s.environment} #${s.number}` : 'a snapshot'
}

function StatusPill({ release: r, project }: { release: Release; project: Project }) {
  const status = r.status as string
  if (status === 'blocked') {
    const reasons = [...new Set((r.checks ?? []).filter((c) => c.status === 'fail').map((c) => CHECK_REASON[c.id] ?? c.id.replace(/_/g, ' ')))]
    return (
      <Badge variant="stub" data-testid="release-blocked-pill">
        blocked{reasons.length ? ` · ${reasons.join(', ')}` : ''}
      </Badge>
    )
  }
  const kind = project.environments.find((e) => e.name === r.environment)?.kind
  if (r.is_active && kind === 'release') return <Badge variant="success">active</Badge>
  return <span className="text-xs text-muted-foreground">{status === 'rolled_back' ? 'rolled back' : status === 'superseded' ? 'superseded' : 'snapshot'}</span>
}
