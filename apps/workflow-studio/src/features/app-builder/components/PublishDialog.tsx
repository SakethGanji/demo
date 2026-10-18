/**
 * Publish (UI-DESIGN §5.3, `publish.png`): address `/a/<slug>`, Access (Public default / Password /
 * Private, always sent, review C4), a pre-flight that never blocks, inline `slug_taken` and the
 * esbuild stderr, then the success state with Copy/Open and Unpublish (§6.3).
 */
import { useEffect, useMemo, useState } from 'react'
import { Link } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ChevronRight,
  CircleCheck,
  CircleX,
  Copy,
  Database,
  ExternalLink,
  Globe,
  Info,
  Lock,
  Send,
  TriangleAlert,
} from 'lucide-react'
import { toast } from 'sonner'
import { appsApi, type ApiAppAccess, type ApiAppDetail } from '@/shared/lib/api'
import { secretsApi } from '@/shared/api/endpoints'
import { errorMessage, isApiError } from '@/shared/api/http'
import { invalidate, qk } from '@/shared/api/queryKeys'
import type { ApiUsage, MissingSecret, Project, ProjectSummary, Route } from '@/shared/api/types'
import { Button } from '@/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/shared/components/ui/dialog'
import { Input } from '@/shared/components/ui/input'
import { Segmented } from '@/shared/components/ui/segmented'
import { Spinner } from '@/shared/components/ui/spinner'
import { Beam } from '@/shared/components/ui/stepper'
import { WellList, WellRow, Callout } from '@/shared/components/ui/surface'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/components/ui/collapsible'
import { CreateSecretPopover } from '@/features/secrets/CreateSecretPopover'
import { cn } from '@/shared/lib/utils'
import { useAppDocumentStore } from '../stores'
import { slugError, slugify } from '../lib/appModel'
import { CreateStubPopover } from './BackendPanel'
import { UnpublishDialog } from './AppDialogs'
import { backends } from '@/shared/lib/config'

interface PublishDialogProps {
  open: boolean
  onClose: () => void
  app: ApiAppDetail
  project: Project
  summary: ProjectSummary | undefined
  routes: Route[] | undefined
  usage: ApiUsage[] | undefined
  usageLoading: boolean
  /** the address chosen in Create UI (`?slug=`) */
  slugHint?: string
}

function publicUrl(slug: string): string {
  const base = backends.workflow || window.location.origin
  return `${base.replace(/\/+$/, '')}/a/${slug}`
}

const pfRow = 'items-start gap-2.5 py-2.5 text-[13px] leading-5 [&>svg]:mt-[3px] [&>svg]:size-3.5 [&>svg]:shrink-0'

function joinOps(ops: string[]) {
  return ops.map((o, i) => (
    <span key={o}>
      {i > 0 && ', '}
      <span className="font-mono text-[12px]">{o}</span>
    </span>
  ))
}

export function PublishDialog({ open, onClose, app, project, summary, routes, usage, usageLoading, slugHint }: PublishDialogProps) {
  const qc = useQueryClient()
  const pid = project.id
  const live = project.live_environment
  const liveKind = project.environments.find((e) => e.name === live)?.kind ?? 'draft'
  const [slug, setSlug] = useState('')
  const [access, setAccess] = useState<ApiAppAccess>('public')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [slugTaken, setSlugTaken] = useState<string | null>(null)
  const [buildError, setBuildError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [done, setDone] = useState<{ url: string; version: number | null } | null>(null)
  const [unpublishOpen, setUnpublishOpen] = useState(false)
  const [secretOpen, setSecretOpen] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setSlug(app.slug ?? slugHint ?? slugify(app.name))
    setAccess(app.active ? app.access : 'public')
    setPassword('')
    setSlugTaken(null)
    setBuildError(null)
    setFormError(null)
    setDone(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  // ── pre-flight ──
  const missing = useQuery({
    queryKey: qk.missing(pid, live),
    queryFn: () => secretsApi.missing(pid, live),
    enabled: open,
  })
  const calls = useMemo(() => usage ?? [], [usage])
  const ops = useMemo(() => [...new Set(calls.map((u) => u.operation).filter(Boolean))] as string[], [calls])
  const missingOps = calls.filter((u) => u.status === 'missing')
  const stubOps = [...new Set(calls.filter((u) => u.status === 'stub').map((u) => u.operation))]
  const usedRoutes = useMemo(() => {
    const ids = new Set(calls.map((u) => u.route_id).filter(Boolean))
    return (routes ?? []).filter((r) => ids.has(r.id))
  }, [calls, routes])
  const secretRows = useMemo(() => {
    const out: Array<{ m: MissingSecret; ops: string[] }> = []
    for (const m of missing.data ?? []) {
      if (!m.missing_in.includes(live)) continue
      const hit = usedRoutes.filter((r) =>
        m.referenced_by.some(
          (ref) =>
            (ref.kind === 'module' && r.handler.type === 'python' && r.handler.module_id === ref.id) ||
            (ref.kind === 'workflow' && r.handler.type === 'workflow' && r.handler.workflow_id === ref.id),
        ),
      )
      if (hit.length) out.push({ m, ops: hit.map((r) => r.name) })
    }
    return out
  }, [missing.data, usedRoutes, live])
  const activeRelease = summary?.active_releases?.[live] ?? null
  const warnings = missingOps.length + stubOps.length + secretRows.length

  const sErr = slugError(slug)
  const needsPassword = access === 'password' && !password && !app.access_password_set
  const canPublish = !busy && !!slug && !sErr && !needsPassword && (access !== 'password' || !password || password.length >= 4)

  const publish = async () => {
    setBusy(true)
    setSlugTaken(null)
    setBuildError(null)
    setFormError(null)
    try {
      const res = await appsApi.publish(app.id, {
        slug,
        access,
        access_password: access === 'password' && password ? password : undefined,
      })
      const fresh = await appsApi.get(app.id)
      qc.setQueryData(qk.app(app.id), fresh)
      if (fresh.current_version) useAppDocumentStore.getState().setCurrentVersion(fresh.current_version)
      await invalidate.appPublished(qc, pid, app.id)
      setDone({ url: res.public_url ?? publicUrl(res.slug ?? slug), version: fresh.published_version?.version_number ?? null })
    } catch (e) {
      if (isApiError(e, 'slug_taken') || (isApiError(e) && e.status === 409)) setSlugTaken(`/a/${slug} is taken by another app. Pick another address.`)
      else if (isApiError(e) && /^bundle failed/i.test(e.message)) setBuildError(e.message.replace(/^bundle failed:\s*/i, ''))
      else setFormError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const copy = (url: string) =>
    navigator.clipboard
      .writeText(url)
      .then(() => toast.success('Link copied'))
      .catch(() => toast.error("Couldn't copy"))

  return (
    <>
      <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
        <DialogContent className="gap-0 p-0 sm:max-w-[600px]" data-testid="publish-dialog">
          <Beam on={busy} />
          <div className="px-[22px] pb-0 pr-12 pt-5">
            <DialogTitle className="text-[16px] font-semibold leading-[22px] tracking-[-0.01em]">
              {done ? `Published · ${done.version != null ? `v${done.version}` : app.name}` : `Publish ${app.name}`}
            </DialogTitle>
            <DialogDescription className="mt-0.5 text-[13px] text-muted-foreground">
              {access === 'private'
                ? 'Only reachable from the studio preview.'
                : access === 'password'
                  ? 'Visitors enter a password to open it.'
                  : 'Anyone with the link can open it.'}{' '}
              It calls your Python backend in <b className="font-medium text-foreground">{live}</b>.
            </DialogDescription>
          </div>

          {done ? (
            <div className="flex flex-col gap-2 px-[22px] pb-5 pt-4" data-testid="publish-success">
              <div className="flex items-center gap-2">
                <CircleCheck className="size-3.5 text-success" />
                <b className="font-semibold">Live</b>
                <span className="text-muted-foreground">
                  · {access}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <Input readOnly value={done.url} className="h-8 flex-1 font-mono text-xs" data-testid="publish-url" onFocus={(e) => e.currentTarget.select()} />
                <Button variant="outline" size="sm" onClick={() => copy(done.url)} data-testid="publish-copy">
                  <Copy /> Copy
                </Button>
                <Button size="sm" render={<a href={done.url} target="_blank" rel="noreferrer noopener" />} data-testid="publish-open">
                  <ExternalLink /> Open
                </Button>
              </div>
              <div className="flex items-center gap-2 text-[11.5px] leading-4 text-muted-foreground">
                <span>
                  Calls <span className="font-mono">/api/p/{project.slug}/…</span> with an app token that refreshes itself.
                </span>
                <div className="flex-1" />
                <button
                  type="button"
                  className="font-medium text-destructive hover:underline"
                  onClick={() => setUnpublishOpen(true)}
                  data-testid="publish-unpublish"
                >
                  Unpublish
                </button>
              </div>
            </div>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault()
                // forms in portaled popovers (Add to prod) bubble through React: ignore them
                if (e.target !== e.currentTarget) return
                if (canPublish) void publish()
              }}
              onKeyDown={(e) => {
                if (!e.currentTarget.contains(e.target as Node)) return // portaled popovers
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && canPublish) {
                  e.preventDefault()
                  void publish()
                }
              }}
            >
              <div className="flex flex-col gap-4 px-[22px] py-4">
                <div className="flex flex-wrap items-start gap-3">
                  <div className="flex min-w-[220px] flex-1 flex-col gap-1.5">
                    <label htmlFor="publish-slug" className="text-xs font-medium">
                      Address
                    </label>
                    <div
                      className={cn(
                        'flex h-8 items-center rounded-ctl bg-recess pl-2.5 focus-within:shadow-focus',
                        (slugTaken || sErr) && 'shadow-[0_0_0_2px_var(--destructive-soft)]',
                      )}
                      data-recess
                    >
                      <span className="font-mono text-xs text-muted-foreground">/a/</span>
                      <input
                        id="publish-slug"
                        value={slug}
                        maxLength={63}
                        onChange={(e) => {
                          setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))
                          setSlugTaken(null)
                        }}
                        className="h-full min-w-0 flex-1 bg-transparent px-1.5 font-mono text-xs outline-none"
                        data-testid="publish-slug"
                        autoComplete="off"
                      />
                    </div>
                    {slugTaken ? (
                      <div className="text-[11.5px] leading-4 text-destructive" role="alert" data-testid="publish-slug-taken">
                        {slugTaken}
                      </div>
                    ) : sErr ? (
                      <div className="text-[11.5px] leading-4 text-destructive" role="alert" data-testid="publish-slug-invalid">
                        {sErr}
                      </div>
                    ) : null}
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <span className="text-xs font-medium">Access</span>
                    <Segmented<ApiAppAccess>
                      value={access}
                      onChange={setAccess}
                      aria-label="Access"
                      className="h-8"
                      data-testid="publish-access"
                      options={[
                        { key: 'public', label: 'Public', icon: <Globe className="size-3" />, testId: 'publish-access-public' },
                        { key: 'password', label: 'Password', icon: <Lock className="size-3" />, testId: 'publish-access-password' },
                        { key: 'private', label: 'Private', testId: 'publish-access-private' },
                      ]}
                    />
                  </div>
                </div>
                {access === 'password' && (
                  <div className="flex flex-col gap-1.5">
                    <label htmlFor="publish-password" className="text-xs font-medium">
                      Password {app.access_password_set && <span className="font-normal text-muted-foreground">(leave blank to keep the current one)</span>}
                    </label>
                    <Input
                      id="publish-password"
                      type="password"
                      autoComplete="new-password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder={app.access_password_set ? '••••••••' : 'At least 4 characters'}
                      className="h-8"
                      data-testid="publish-password"
                    />
                  </div>
                )}
                {access === 'private' && (
                  <Callout tone="info" icon={<Lock />} className="text-xs text-muted-foreground" data-testid="publish-private-note">
                    Only reachable from the studio preview; <span className="font-mono">/a/{slug || '<slug>'}</span> returns 404.
                  </Callout>
                )}

                <div className="flex flex-col gap-1.5">
                  <span className="text-xs font-medium">Pre-flight</span>
                  <WellList className="overflow-hidden" data-testid="publish-preflight">
                    {usageLoading && !usage ? (
                      <WellRow className={pfRow}>
                        <Spinner className="mt-[3px] size-3.5" />
                        <span className="text-muted-foreground">Checking the operations it calls…</span>
                      </WellRow>
                    ) : missingOps.length > 0 ? (
                      missingOps.map((u, i) => (
                        <WellRow key={`${u.operation}-${i}`} className={pfRow} data-testid="publish-pf-missing">
                          <CircleX className="text-destructive" />
                          <span className="min-w-0 flex-1">
                            <span className="font-mono text-[12px]">{u.operation}</span> ({u.file}:{u.line}) has no route; it will fail.
                          </span>
                          <CreateStubPopover pid={pid} appId={app.id} row={{ name: u.operation, method: u.method ?? 'GET', path: u.path }} testIdPrefix="publish-pf" />
                        </WellRow>
                      ))
                    ) : ops.length > 0 ? (
                      <WellRow className={pfRow} data-testid="publish-pf-ops">
                        <CircleCheck className="text-success" />
                        <span className="min-w-0 flex-1">
                          All {ops.length} {ops.length === 1 ? 'operation' : 'operations'} it calls exist: {joinOps(ops)}
                        </span>
                      </WellRow>
                    ) : (
                      <WellRow className={pfRow} data-testid="publish-pf-ops">
                        <Info className="text-muted-foreground" />
                        <span className="min-w-0 flex-1 text-muted-foreground">The app doesn't call your backend yet.</span>
                      </WellRow>
                    )}
                    {stubOps.length > 0 && (
                      <WellRow className={pfRow} data-testid="publish-pf-stub">
                        <TriangleAlert className="text-warning-text" />
                        <span className="min-w-0 flex-1">{joinOps(stubOps as string[])} {stubOps.length === 1 ? 'is a stub and answers' : 'are stubs and answer'} 501 until implemented.</span>
                      </WellRow>
                    )}
                    {missing.isLoading && (
                      <WellRow className={pfRow}>
                        <Spinner className="mt-[3px] size-3.5" />
                        <span className="text-muted-foreground">Checking {live} secrets…</span>
                      </WellRow>
                    )}
                    {secretRows.map(({ m, ops: needOps }) => (
                      <WellRow key={m.key} className={cn(pfRow, 'items-center [&>svg]:mt-0')} data-testid={`publish-pf-secret-${m.key}`}>
                        <TriangleAlert className="text-warning-text" />
                        <span className="min-w-0 flex-1">
                          <b className="font-medium">{m.key}</b> isn't set in {live}; {joinOps(needOps)} {needOps.length === 1 ? 'needs' : 'need'} it and will fail.
                        </span>
                        <CreateSecretPopover
                          open={secretOpen === m.key}
                          onOpenChange={(o) => setSecretOpen(o ? m.key : null)}
                          secretKey={m.key}
                          keyReadOnly
                          environments={[live]}
                          testId={`publish-secret-form-${m.key}`}
                          trigger={
                            <Button variant="outline" size="xs" type="button" data-testid={`publish-add-secret-${m.key}`}>
                              Add to {live}
                            </Button>
                          }
                        />
                      </WellRow>
                    ))}
                    {liveKind === 'release' && (
                      <WellRow className={pfRow} data-testid="publish-pf-release">
                        <Info className="text-muted-foreground" />
                        <span className="min-w-0 flex-1">
                          {activeRelease ? (
                            <>
                              {live} serves release #{activeRelease.number}; changes saved since then aren't live.
                            </>
                          ) : (
                            <>
                              {live} has no release, so it serves your <b className="font-medium">latest saved</b> backend.{' '}
                              <Link
                                to="/p/$project"
                                params={{ project: project.slug }}
                                search={{ sheet: 'release' }}
                                className="text-primary hover:underline"
                              >
                                Create a release
                              </Link>{' '}
                              to pin it.
                            </>
                          )}
                        </span>
                      </WellRow>
                    )}
                    <WellRow className={pfRow} data-testid="publish-pf-tables">
                      <Database className="text-muted-foreground" />
                      <span className="min-w-0 flex-1">The published app reads and writes the same tables as {project.default_environment}.</span>
                    </WellRow>
                  </WellList>
                </div>

                {buildError && (
                  <Collapsible className="rounded-well bg-destructive-soft" data-testid="publish-build-failed">
                    <CollapsibleTrigger className="group flex w-full items-center gap-2 px-3 py-2 text-left text-[13px]">
                      <CircleX className="size-3.5 shrink-0 text-destructive" />
                      <span className="flex-1 font-medium">The build failed: {buildError.split('\n')[0].slice(0, 120)}</span>
                      <ChevronRight className="size-3.5 text-muted-foreground transition-transform group-data-[panel-open]:rotate-90" />
                    </CollapsibleTrigger>
                    <CollapsibleContent>
                      <pre className="max-h-48 overflow-auto whitespace-pre-wrap px-3 py-2 shadow-[inset_0_1px_0_var(--hairline)] font-mono text-[11px] leading-4 text-foreground" data-scroll-x="allowed">
                        {buildError}
                      </pre>
                    </CollapsibleContent>
                  </Collapsible>
                )}
                {formError && (
                  <div className="text-[11.5px] text-destructive" role="alert" data-testid="publish-error">
                    {formError}
                  </div>
                )}
              </div>
              <div className="flex items-center gap-2 px-[22px] py-3.5 shadow-[inset_0_1px_0_var(--hairline)]">
                <span className="text-xs text-muted-foreground" data-testid="publish-warning-count">
                  {warnings > 0 ? `${warnings} ${warnings === 1 ? 'warning' : 'warnings'} · you can publish anyway` : 'Ready to publish'}
                </span>
                <div className="flex-1" />
                {app.active && (
                  <Button variant="ghost" size="sm" type="button" className="text-destructive" onClick={() => setUnpublishOpen(true)} data-testid="publish-unpublish">
                    Unpublish
                  </Button>
                )}
                <Button variant="ghost" size="sm" type="button" onClick={onClose}>
                  Cancel
                </Button>
                <Button size="sm" type="submit" disabled={!canPublish} data-testid="publish-submit">
                  {busy ? <Spinner className="size-3.5" /> : <Send />}
                  {app.active ? 'Republish' : 'Publish'}
                </Button>
              </div>
            </form>
          )}
        </DialogContent>
      </Dialog>
      <UnpublishDialog
        app={app}
        pid={pid}
        open={unpublishOpen}
        onOpenChange={setUnpublishOpen}
        onDone={() => {
          setDone(null)
          onClose()
        }}
      />
    </>
  )
}
