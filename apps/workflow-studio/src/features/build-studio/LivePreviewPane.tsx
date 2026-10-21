/**
 * The build studio's Preview pane.
 *
 *   waiting    before the build: a quiet note of what will appear here
 *   api        until the app exists: the planned routes, each flipping stub → built → passing / failing as the backend
 *              builder writes its module and the self-test runs
 *   skeleton   the app exists but has no files yet: its layout, shimmering, crossfading into the app
 *   app        the live app preview; every version the UI builder saves reloads it with a short blur-to-sharp
 *
 * Once the app exists an App | API switch keeps the routes one click away.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, animate, motion } from 'motion/react'
import { AppWindow, Plug } from 'lucide-react'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useRoutes } from '@/features/project/hooks/useProjectData'
import { isActive } from '@/features/plan/buildLogic'
import type { Build, RouteStatus } from '@/features/plan/buildTypes'
import { liveRoutes, routeCounts, type LiveRoute, type LiveState } from '@/features/plan/liveBuild'
import type { PlanRoute } from '@/features/plan/planTypes'
import { FADE, SPRING, useCalm } from '@/features/plan/motion'
import { AppPreviewPanel } from '@/features/app-builder/components/AppPreviewPanel'
import { useClientTs } from '@/features/app-builder/hooks/useAppData'
import { usePreviewToken } from '@/features/app-builder/hooks/usePreviewToken'
import { CLIENT_PATH, matchRoute, routeIdsOf, stripClient } from '@/features/app-builder/lib/appModel'
import type { BridgeCall } from '@/features/app-builder/sandbox/IframeSandbox'
import { useAppDocumentStore, useConsoleStore } from '@/features/app-builder/stores'
import { useAppBuilderChatStore } from '@/features/app-builder/stores/appBuilderChatStore'
import { usePreviewSession } from '@/features/app-builder/stores/previewSessionStore'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { Segmented } from '@/shared/components/ui/segmented'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Progress, WellList } from '@/shared/components/ui/surface'
import { appsApi } from '@/shared/lib/api'
import { qk } from '@/shared/api/queryKeys'
import { cn } from '@/shared/lib/utils'
import { previewStage } from './studioLogic'

const STATUS: Record<RouteStatus, { label: string; text: string; dot: string }> = {
  stub: { label: 'stub', text: 'text-faint', dot: 'bg-foreground/20' },
  built: { label: 'built', text: 'text-hue-backend', dot: 'bg-hue-backend' },
  passing: { label: 'passing', text: 'text-success', dot: 'bg-success' },
  failing: { label: 'failing', text: 'text-destructive', dot: 'bg-destructive' },
}

/* ── the routes as they are built ─────────────────────────────────────────────────────────────────────────────── */

function RouteRow({ r, n }: { r: LiveRoute; n: number }) {
  const calm = useCalm()
  const s = STATUS[r.status]
  return (
    <motion.div
      initial={calm ? { opacity: 0 } : { opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={calm ? FADE : { ...FADE, duration: 0.24, delay: Math.min(n, 12) * 0.03 }}
      className="relative flex h-9 items-center gap-2.5 px-3"
      data-testid={`studio-route-${r.key}`}
      data-status={r.status}
    >
      {/* a sweep across the row each time its status moves on */}
      {r.status !== 'stub' && !calm ? (
        <motion.span
          key={r.status}
          aria-hidden
          className={cn('pointer-events-none absolute inset-0', r.status === 'failing' ? 'bg-destructive/10' : r.status === 'passing' ? 'bg-success/10' : 'bg-primary/10')}
          initial={{ opacity: 1, clipPath: 'inset(0 100% 0 0)' }}
          animate={{ opacity: 0, clipPath: 'inset(0 0% 0 0)' }}
          transition={{ clipPath: { duration: 0.35, ease: [0.16, 1, 0.3, 1] }, opacity: { duration: 0.7, delay: 0.3 } }}
        />
      ) : null}
      <MethodBadge method={r.method} size="sm" className="relative" />
      <span className={cn('relative min-w-0 flex-1 truncate font-mono text-[12px]', r.status === 'stub' && 'text-muted-foreground')}>{r.path}</span>
      {r.name ? <span className="relative hidden max-w-40 truncate text-[11.5px] text-faint @min-[460px]/api:inline">{r.name}</span> : null}
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={r.status}
          initial={calm ? { opacity: 0 } : { opacity: 0, scale: 0.85 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0 }}
          transition={calm ? FADE : SPRING}
          className={cn('relative inline-flex w-16 shrink-0 items-center justify-end gap-1.5 text-[11.5px] font-medium', s.text)}
        >
          <span className={cn('size-1.5 rounded-full', s.dot)} aria-hidden />
          {s.label}
        </motion.span>
      </AnimatePresence>
    </motion.div>
  )
}

export function ApiPanel({ build, live, planned }: { build: Build | null; live: LiveState; planned: PlanRoute[] }) {
  const routes = useMemo(() => liveRoutes(live, planned, build), [live, planned, build])
  const c = routeCounts(routes)
  const done = c.built + c.passing + c.failing
  return (
    <div className="@container/api flex h-full min-h-0 flex-col" data-testid="studio-api">
      <div className="flex shrink-0 flex-col gap-2 px-4 pt-4 pb-3">
        <div className="flex items-baseline gap-2">
          <span className="text-[14px] font-semibold tracking-[-0.01em]">The API, as it is built</span>
          <span className="ml-auto text-[12px] text-muted-foreground tabular-nums" data-testid="studio-api-count">
            {done} of {c.total} built{c.passing ? ` · ${c.passing} passing` : ''}
            {c.failing ? ` · ${c.failing} failing` : ''}
          </span>
        </div>
        <Progress value={c.total ? (done / c.total) * 100 : 0} />
      </div>
      {routes.length ? (
        <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
          <WellList>
            {routes.map((r, n) => (
              <RouteRow key={r.key} r={r} n={n} />
            ))}
          </WellList>
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center px-6 text-center text-[12.5px] text-muted-foreground">
          {build && isActive(build.status) ? 'The routes appear here once the build announces them.' : 'This build has no routes.'}
        </div>
      )}
    </div>
  )
}

/* ── the app ──────────────────────────────────────────────────────────────────────────────────────────────────── */

/** The shape of an app (nav, header, cards, a table) while its first files are written. */
function AppSkeleton() {
  return (
    <div className="flex h-full gap-4 p-5" data-testid="studio-preview-skeleton" aria-label="The app is being written">
      <div className="flex w-40 shrink-0 flex-col gap-2.5 max-md:hidden">
        <Skeleton className="mb-3 h-6 w-28" />
        {[70, 85, 60, 75].map((w, n) => (
          <Skeleton key={n} className="h-4" style={{ width: `${w}%` }} />
        ))}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <div className="flex items-center gap-3">
          <Skeleton className="h-7 w-48" />
          <div className="flex-1" />
          <Skeleton className="h-7 w-24" />
        </div>
        <div className="grid grid-cols-3 gap-3">
          {[0, 1, 2].map((n) => (
            <Skeleton key={n} className="h-20" />
          ))}
        </div>
        <div className="flex flex-col gap-2">
          {[0, 1, 2, 3, 4, 5].map((n) => (
            <Skeleton key={n} className="h-8" />
          ))}
        </div>
      </div>
    </div>
  )
}

/**
 * The app the build is making, previewed: the builder's document store holds its files (reset per app, then seeded from
 * each version the build saves, unless the user has unsaved edits), so the same preview keeps working when the app chat
 * edits it afterwards.
 */
function StudioAppPreview({ appId, previewTick }: { appId: string; previewTick: number }) {
  const { project, pid, slug } = useProjectCtx()
  const qc = useQueryClient()
  const calm = useCalm()
  const env = project.default_environment
  const appQ = useQuery({ queryKey: qk.app(appId), queryFn: () => appsApi.get(appId), staleTime: 30_000 })
  const app = appQ.data
  const routesQ = useRoutes(pid)
  const clientQ = useClientTs(pid, env)
  const token = usePreviewToken(pid, appId)
  const files = useAppDocumentStore((s) => s.files)
  const version = useAppDocumentStore((s) => s.currentVersion?.id ?? null)
  const seeded = useRef<number | null | undefined>(undefined)
  const frame = useRef<HTMLDivElement>(null)

  // a new app: a clean document, chat and preview session (as the app builder does per app)
  useEffect(() => {
    useAppDocumentStore.getState().reset()
    useAppBuilderChatStore.getState().clearHistory()
    usePreviewSession.getState().reset(appId)
    useConsoleStore.getState().clear()
  }, [appId])

  // each `preview` event is a version the UI builder saved: fetch it
  useEffect(() => {
    if (!previewTick) return
    void qc.invalidateQueries({ queryKey: qk.app(appId) })
    // the typed client is regenerated before each saved version; a cached copy would lack the new routes
    void qc.invalidateQueries({ queryKey: qk.clientTs(pid, env) })
  }, [qc, appId, pid, env, previewTick])

  const appVersion = app?.id === appId ? (app.current_version?.id ?? null) : undefined
  useEffect(() => {
    if (!app || appVersion === undefined || appVersion === seeded.current) return
    const doc = useAppDocumentStore.getState()
    if (!doc.dirty) {
      const own = stripClient(app.files)
      if (own.length) doc.setFiles(own)
      else if (app.source_code) doc.setSourceCode(app.source_code)
      doc.setCurrentVersion(app.current_version ?? null)
    }
    seeded.current = appVersion
  }, [app, appVersion])

  // a new version on screen: a soft blur-to-sharp (a plain fade when calm)
  useEffect(() => {
    const el = frame.current
    if (!el || version == null) return
    const run = calm
      ? animate(el, { opacity: [0.6, 1] }, { duration: 0.2 })
      : animate(el, { filter: ['blur(6px)', 'blur(0px)'], opacity: [0.7, 1], scale: [0.995, 1] }, { duration: 0.42, ease: [0.16, 1, 0.3, 1] })
    return () => run.stop()
  }, [version, calm])

  // never render new files against a client that is being refreshed: keep the last consistent set until it lands
  const nextPreview = useMemo(() => {
    if (!files?.length) return files
    const own = stripClient(files)
    return clientQ.data ? [...own, { path: CLIENT_PATH, content: clientQ.data }] : own
  }, [files, clientQ.data])
  const [settled, setSettled] = useState(nextPreview)
  if (!clientQ.isFetching && settled !== nextPreview) setSettled(nextPreview)
  const previewFiles = clientQ.isFetching && settled?.length ? settled : nextPreview
  const routes = routesQ.data
  const onApiCall = useCallback(
    (c: BridgeCall) => {
      const prefix = `/api/p/${slug}`
      const bare = c.path.split('?')[0]
      const rest = bare.startsWith(prefix) ? bare.slice(prefix.length) || '/' : null
      const r = rest != null ? matchRoute(routes ?? [], c.method, rest) : undefined
      usePreviewSession.getState().record({ ...c, at: Date.now(), routeId: r?.id })
    },
    [routes, slug],
  )
  const hasFiles = !!files?.length

  return (
    <div className="relative h-full" data-testid="studio-app-preview">
      <AnimatePresence initial={false}>
        {!hasFiles ? (
          <motion.div key="skeleton" className="absolute inset-0" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: calm ? 0.15 : 0.3 }}>
            <AppSkeleton />
          </motion.div>
        ) : null}
      </AnimatePresence>
      {hasFiles ? (
        <motion.div ref={frame} className="h-full" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: calm ? 0.15 : 0.3 }}>
          <AppPreviewPanel
            appId={appId}
            projectSlug={slug}
            workflowPaths={(app?.workflow_ids ?? []).map((id) => `/api/apps/${encodeURIComponent(appId)}/workflow/${encodeURIComponent(id)}/run`)}
            allowedRoutes={(routes ?? []).filter((r) => routeIdsOf(app).includes(r.id))}
            files={previewFiles}
            mode="preview"
            slug={app?.slug ?? ''}
            env={env}
            publishedUrl={null}
            showUrl={false}
            getToken={token.get}
            refreshToken={token.refresh}
            onApiCall={onApiCall}
          />
        </motion.div>
      ) : null}
    </div>
  )
}

/* ── the pane ─────────────────────────────────────────────────────────────────────────────────────────────────── */

export function LivePreviewPane({ build, live, appId, planned }: { build: Build | null; live: LiveState; appId: string | null; planned: PlanRoute[] }) {
  const calm = useCalm()
  const files = useAppDocumentStore((s) => s.files?.length ?? 0)
  const stage = previewStage({ build, appId, files: appId ? Math.max(files, 0) : 0 })
  const [view, setView] = useState<'app' | 'api'>('app')
  const showApi = !appId || view === 'api'
  const content = stage === 'waiting' ? 'waiting' : showApi ? 'api' : 'app'

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="studio-preview" data-stage={stage}>
      {appId ? (
        <div className="hairline-b flex h-10 shrink-0 items-center gap-2 pr-2 pl-3">
          <AppWindow className="size-3.5 text-hue-apps" aria-hidden />
          <span className="text-[13px] font-medium">Preview</span>
          <div className="flex-1" />
          <Segmented<'app' | 'api'>
            size="xs"
            value={view}
            onChange={setView}
            aria-label="App or API"
            options={[
              { key: 'app', label: 'App', icon: <AppWindow className="size-3" />, testId: 'studio-view-app' },
              { key: 'api', label: 'API', icon: <Plug className="size-3" />, testId: 'studio-view-api' },
            ]}
          />
        </div>
      ) : null}
      <div className="relative min-h-0 flex-1">
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={content}
            className="absolute inset-0"
            initial={calm ? { opacity: 0 } : { opacity: 0, scale: 0.99 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
          >
            {content === 'waiting' ? (
              <div className="flex h-full items-center justify-center" data-testid="new-app-placeholder">
                <div className="flex max-w-72 flex-col items-center gap-2 text-center">
                  <AppWindow className="size-6 text-muted-foreground" aria-hidden />
                  <span className="text-[13px] font-medium">Your app appears here as it is built</span>
                  <span className="text-[12px] leading-[17px] text-muted-foreground">
                    Once you approve the plan, the backend is built first, its routes lighting up here, then the app.
                  </span>
                </div>
              </div>
            ) : content === 'api' ? (
              <ApiPanel build={build} live={live} planned={planned} />
            ) : null}
          </motion.div>
        </AnimatePresence>
        {/* the app stays mounted under the API view so its sandbox does not reload on every switch */}
        {appId ? (
          <div className={cn('absolute inset-0', content !== 'app' && 'invisible')}>
            <StudioAppPreview appId={appId} previewTick={live.preview?.n ?? 0} />
          </div>
        ) : null}
      </div>
    </div>
  )
}
