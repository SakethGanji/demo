/**
 * Release sheet (UI-DESIGN §3.8, `release.png`, §9 D10). Environment cards (draft → release),
 * a dry-run plan (snapshot the draft environment, or promote its latest snapshot) with checks and
 * the filtered diff, then the real write; history per environment with rollback on release envs.
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Rocket, RotateCcw, TriangleAlert } from 'lucide-react'
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
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/shared/components/ui/sheet'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Spinner } from '@/shared/components/ui/spinner'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/components/ui/tooltip'
import { floatingSheetCls } from '@/features/settings/SettingsSheet'
import { cn } from '@/shared/lib/utils'
import { ChecksList } from './ChecksList'

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
      <SheetContent side="right" showCloseButton className={`${floatingSheetCls} data-[side=right]:w-[600px]`} data-testid="release-sheet">
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
  useEffect(() => setResult(null), [step])

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
    try {
      const rel = (promoteTo
        ? await releasesApi.promote(pid, latestSnap!.id, { to_environment: promoteTo, ...(notes.trim() ? { notes: notes.trim() } : {}) })
        : await releasesApi.create(pid, { environment: source, ...(notes.trim() ? { notes: notes.trim() } : {}) })) as Release
      setNotes('')
      await invalidate.releasesChanged(qc, pid)
      if (promoteTo) {
        toast.success(`${promoteTo} now serves release #${rel.number}`, {
          description: rel.environment === project.live_environment ? 'Published apps were re-published from it.' : undefined,
        })
      } else if (targets[0]) {
        // In-sheet effect (the card and history update): no toast, move on to promoting it.
        setStepPick(`promote:${targets[0].name}`)
      }
    } catch (e) {
      if (isApiError(e, 'checks_failed')) {
        const d = (e as ApiError).details as { checks?: ReleaseCheck[]; number?: number }
        setResult({ kind: 'blocked', checks: d.checks ?? [], number: d.number, env: targetEnv })
        await invalidate.releasesChanged(qc, pid)
      } else if (isApiError(e, 'release_blocked')) {
        setResult({ kind: 'blocked-source', message: 'This snapshot was blocked; create a new one.' })
      } else {
        toast.error(errorMessage(e))
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

  return (
    <>
      <div className="border-b border-border px-[18px] pb-3 pt-4">
        <div className="flex items-center gap-2 pr-8">
          <Rocket className="size-3.5" />
          <SheetTitle className="text-[15px] font-semibold tracking-[-0.01em]">Release</SheetTitle>
        </div>
        <SheetDescription className="mt-1 text-[13px] text-muted-foreground">
          A release freezes routes, modules and app versions.{' '}
          {targets.some((t) => t.kind === 'release')
            ? `${targets.filter((t) => t.kind === 'release').map((t) => t.name).join(', ')} serves the active release; `
            : ''}
          {source} always serves your latest saved code.
        </SheetDescription>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden" data-testid="release-panel">
        <div className="flex flex-col gap-4 px-[18px] pb-6 pt-4">
          <EnvCards project={project} source={source} releases={all} loading={releases.isLoading} />
          {releases.isError && (
            <div className="text-[12.5px] text-destructive" role="alert" data-testid="release-error">
              Couldn't load releases · {errorMessage(releases.error)}{' '}
              <button type="button" className="underline" onClick={() => releases.refetch()}>
                Retry
              </button>
            </div>
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
            <span className="truncate font-semibold" data-testid="release-plan-title">
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
            <div
              className="rounded-[10px] border border-dashed border-border px-4 py-6 text-center text-[12.5px] text-muted-foreground"
              data-testid="release-empty"
            >
              No snapshots yet. Create one from {source}.
              <div className="mt-2.5">
                <Button size="sm" variant="outline" onClick={() => setStepPick('snapshot')} data-testid="release-empty-snapshot">
                  Snapshot {source}
                </Button>
              </div>
            </div>
          ) : result?.kind === 'blocked-source' ? (
            <div
              className="flex items-center gap-2 rounded-[10px] border border-[color-mix(in_srgb,var(--warning)_35%,var(--border))] bg-[color-mix(in_srgb,var(--warning)_5%,var(--card))] px-3 py-2.5 text-[12.5px]"
              role="alert"
              data-testid="release-409-blocked-source"
            >
              <TriangleAlert className="size-3.5 shrink-0 text-warning" />
              <span className="flex-1">{result.message}</span>
              <Button size="xs" variant="outline" onClick={() => setStepPick('snapshot')}>
                Snapshot {source}
              </Button>
            </div>
          ) : !canDry || dry.isLoading || (dry.isFetching && !dry.data) ? (
            <div className="flex items-center justify-center gap-2 rounded-xl border border-border py-8 text-xs text-muted-foreground" data-testid="release-loading">
              <Spinner className="size-3.5" /> Running checks…
            </div>
          ) : dry.isError ? (
            <div className="rounded-[10px] border border-destructive/35 px-3 py-2.5 text-[12.5px] text-destructive" role="alert" data-testid="release-dry-error">
              {isApiError(dry.error, 'release_blocked') ? 'This snapshot was blocked; create a new one.' : errorMessage(dry.error)}{' '}
              <button type="button" className="underline" onClick={rerun}>
                Retry
              </button>
            </div>
          ) : (
            <div
              className={cn('flex flex-col gap-2', dry.isFetching && 'opacity-60')}
              data-testid={failed ? 'release-blocked' : 'release-ready'}
              data-stored={result?.kind === 'blocked' ? 'true' : undefined}
            >
              {result?.kind === 'blocked' && (
                <div className="flex items-center gap-2 rounded-lg bg-warning/13 px-3 py-2 text-[12.5px] text-warning-text" role="alert" data-testid="release-stored-blocked">
                  <TriangleAlert className="size-3.5 shrink-0" />
                  {result.number ? `Release #${result.number} of ${result.env} was stored as blocked and is not served.` : 'The checks failed.'} Fix
                  the failed check, then re-run.
                </div>
              )}
              <ChecksList checks={shownChecks} targetEnv={targetEnv} onRerun={rerun} />
            </div>
          )}

          {canDry && dry.data?.diff && !result && <DiffChips diff={dry.data.diff} env={targetEnv} />}

          {!promoteTo && canDry && (
            <Input
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Notes (optional), e.g. refunds endpoint"
              className="h-8 text-[12.5px]"
              data-testid="release-notes"
            />
          )}

          <History releases={all} project={project} loading={releases.isLoading} source={source} />
        </div>
      </div>

      <div className="flex items-center gap-2 border-t border-border bg-[color-mix(in_srgb,var(--muted)_40%,var(--card))] px-[18px] py-3">
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
          {writing && <Spinner className="size-3.5" />}
          {primaryLabel}
        </Button>
      </div>

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

function EnvCards({ project, source, releases, loading }: { project: Project; source: string; releases: Release[]; loading: boolean }) {
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
    <div className="flex items-stretch gap-2.5" data-testid="release-envs">
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
          <div key={e.name} className="flex min-w-0 flex-1 items-stretch gap-2.5">
            {i > 0 && <ArrowRight className="size-3.5 shrink-0 self-center text-muted-foreground" />}
            <div
              className="flex min-w-0 flex-1 flex-col rounded-xl border border-border bg-card p-3"
              data-testid={`release-env-${e.name}`}
              data-active-release={active?.number}
            >
              <div className="flex items-center gap-2">
                <Badge variant="secondary" className="font-mono">
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
            </div>
          </div>
        )
      })}
    </div>
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
      <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">What changes in {env}</span>
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
    <div className="flex flex-col gap-1" data-testid="release-history">
      <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">History</span>
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
            className="flex min-h-9 items-center gap-2 border-b border-border/65 py-1.5 text-[12.5px] last:border-0"
            data-testid={`release-row-${r.environment}-${r.number}`}
            data-status={r.status}
          >
            <Badge variant="secondary" className="font-mono">
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
