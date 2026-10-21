/**
 * App builder (UI-DESIGN §5.2): `[Backend 288] [Preview] [Chat 360]` as editor-glass tiles (8px gutters)
 * under the shared 48px top bar `Command Studio / Project / App ⇕ · vN · saved · Preview|Code · history ·
 * settings · Publish`. Below 1280px the Backend panel is a 44px rail, chat is 320 and the URL pill hides.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import {
  AppWindow,
  Check,
  ChevronsUpDown,
  ClipboardList,
  CodeXml,
  Eye,
  History,
  Plus,
  Send,
  Settings2,
  Trash2,
  X,
} from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { PlanFlowThread } from '@/features/plan/PlanFlow'
import { PlanWorkspace } from '@/features/plan/PlanView'
import { usePlanFlow } from '@/features/plan/usePlanFlow'
import { usePlan } from '@/features/plan/usePlan'
import { useBuilds } from '@/features/plan/useBuild'
import { useProjectApps, useProjectSummary, useRoutes } from '@/features/project/hooks/useProjectData'
import { HomeNav } from '@/features/project/components/HomeNav'
import { healthTone } from '@/features/project/lib/healthTone'
import { ThemeToggle, TopBarFrame } from '@/features/project/components/TopBar'
import { Tile } from '@/shared/components/ui/surface'
import { appsApi, type ApiAppDetail } from '@/shared/lib/api'
import { backends } from '@/shared/lib/config'
import { errorMessage } from '@/shared/api/http'
import { qk } from '@/shared/api/queryKeys'
import { Button } from '@/shared/components/ui/button'
import { Badge, badgeVariants } from '@/shared/components/ui/badge'
import { Segmented } from '@/shared/components/ui/segmented'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/components/ui/tooltip'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Spinner } from '@/shared/components/ui/spinner'
import { Kbd } from '@/shared/components/ui/kbd'
import { useViewportWidth } from '@/shared/hooks/useViewport'
import { cn } from '@/shared/lib/utils'
import type { AppSearch } from '@/app/routes/project'
import { useAppDocumentStore, useConsoleStore } from '../stores'
import { useAppBuilderChatStore } from '../stores/appBuilderChatStore'
import { restoreChatMessages } from '../lib/restoreChat'
import { fixRequestText, shouldAutoFix, type PreviewError } from '../lib/previewErrors'
import { autotestFromBuild } from '../lib/autotest'
import { ChecksChip } from './ChecksChip'
import { usePreviewSession } from '../stores/previewSessionStore'
import { useApiUsage, useApp, useClientTs, useSaveApp, useSpecRevision } from '../hooks/useAppData'
import { usePreviewToken } from '../hooks/usePreviewToken'
import { useAppBuilderChat } from '../hooks/useAppBuilderChat'
import { CLIENT_PATH, entrySource, matchRoute, routeIdsOf, slugify, stripClient } from '../lib/appModel'
import type { BridgeCall } from '../sandbox/IframeSandbox'
import { AppPreviewPanel, type BuilderMode } from './AppPreviewPanel'
import { AppBuilderChatPanel, type ComposerHandle } from './AppBuilderChatPanel'
import { BackendPanel, BackendRail, useOpCounts } from './BackendPanel'
import { ApiTesterPanel, SavedQueriesPanel } from './ApiTesterPanel'
import { VersionHistory } from './VersionHistory'
import { PublishDialog } from './PublishDialog'
import { AppSettingsSheet } from './AppSettingsSheet'
import { DeleteAppDialog, UnpublishDialog } from './AppDialogs'

/** A builder pane: an editor-glass tile (DESIGN §1, editor pages split the sheet into panes). */
const panel = 'overflow-hidden'

/** The builder's centre view: the plan, the running app, or its code. */
type ShellMode = BuilderMode | 'plan'

/** Prompts already auto-sent (module scope: survives StrictMode remounts and URL rewrites). */
const autoSent = new Set<string>()

function publicUrl(slug: string): string {
  const base = backends.workflow || window.location.origin
  return `${base.replace(/\/+$/, '')}/a/${slug}`
}

// ── top bar pieces ──────────────────────────────────────────────────────────

function AppSwitcher({ app, onDelete }: { app: ApiAppDetail; onDelete: () => void }) {
  const { pid, slug } = useProjectCtx()
  const apps = useProjectApps(pid)
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        data-testid="app-switcher"
        className="inline-flex h-7 min-w-0 items-center gap-1.5 rounded-ctl px-2 font-medium hover:bg-(--hover-fill)"
      >
        <AppWindow className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="max-w-48 truncate" data-testid="app-name">
          {app.name}
        </span>
        <ChevronsUpDown className="size-3 shrink-0 text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent className="w-64 p-1" align="start">
        <div className="px-2 pb-1 pt-1.5 text-[12px] font-medium text-muted-foreground">Interface</div>
        <div className="max-h-72 overflow-y-auto">
          {(apps.data ?? []).map((a) => (
            <button
              key={a.id}
              type="button"
              onClick={() => {
                setOpen(false)
                if (a.id !== app.id) navigate({ to: '/p/$project/apps/$appId', params: { project: slug, appId: a.id } })
              }}
              className="flex h-8 w-full items-center gap-2 rounded-ctl px-2 text-left hover:bg-(--hover-fill)"
              data-testid={`app-switcher-item-${a.id}`}
            >
              <AppWindow className="size-3.5 text-muted-foreground" />
              <span className="flex-1 truncate">{a.name}</span>
              {a.active && <Badge variant="success" className="h-[18px] px-1.5 text-[10.5px]">live</Badge>}
              {a.id === app.id && <Check className="size-3.5 text-muted-foreground" />}
            </button>
          ))}
        </div>
        <div className="my-1 h-px bg-hairline" />
        <button
          type="button"
          onClick={() => {
            setOpen(false)
            navigate({ to: '/p/$project', params: { project: slug }, search: { dialog: 'create-ui' } })
          }}
          className="flex h-8 w-full items-center gap-2 rounded-ctl px-2 text-left text-muted-foreground hover:bg-(--hover-fill) hover:text-foreground"
          data-testid="app-switcher-new"
        >
          <Plus className="size-3.5" /> New interface from the backend
        </button>
        <button
          type="button"
          onClick={() => {
            setOpen(false)
            onDelete()
          }}
          className="flex h-8 w-full items-center gap-2 rounded-ctl px-2 text-left text-destructive hover:bg-destructive-soft"
          data-testid="app-delete"
        >
          <Trash2 className="size-3.5" /> Delete {app.name}…
        </button>
      </PopoverContent>
    </Popover>
  )
}

function VersionBadge({ saving, error, onSave }: { saving: boolean; error: string | null; onSave: () => void }) {
  const version = useAppDocumentStore((s) => s.currentVersion)
  const dirty = useAppDocumentStore((s) => s.dirty)
  const files = useAppDocumentStore((s) => s.files)
  const label = saving
    ? 'saving…'
    : error
      ? 'save failed'
      : dirty
        ? 'unsaved'
        : version
          ? 'saved'
          : files?.length
            ? 'not saved'
            : 'empty'
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <button
            type="button"
            onClick={onSave}
            disabled={saving || !files?.length}
            className={cn(
              badgeVariants({ variant: error ? 'error' : dirty ? 'warning' : 'neutral' }),
              'ml-1 outline-none disabled:cursor-default',
            )}
            data-testid="builder-save"
            data-state={label}
          />
        }
      >
        <span data-testid="builder-version">{version ? `v${version.version_number}` : 'draft'}</span> · {label}
      </TooltipTrigger>
      <TooltipContent>
        {error ? `Couldn't save: ${error}. Click to retry.` : 'Save a version'} <Kbd>⌘S</Kbd>
      </TooltipContent>
    </Tooltip>
  )
}

// ── shell ───────────────────────────────────────────────────────────────────

export function AppBuilderShell({ appId }: { appId: string }) {
  const { project, pid, slug } = useProjectCtx()
  const search = useSearch({ strict: false }) as AppSearch
  const navigate = useNavigate()
  const qc = useQueryClient()
  const width = useViewportWidth()
  const narrow = width < 1280

  const appQ = useApp(appId)
  const app = appQ.data
  const routesQ = useRoutes(pid)
  const usageQ = useApiUsage(appId)
  const env = project.default_environment
  const clientQ = useClientTs(pid, env)
  const revision = useSpecRevision(pid).data ?? null
  const summary = useProjectSummary(pid)
  const token = usePreviewToken(pid, appId)
  const save = useSaveApp(appId)

  const files = useAppDocumentStore((s) => s.files)
  const currentVersion = useAppDocumentStore((s) => s.currentVersion)
  const [loadedFor, setLoadedFor] = useState<string | null>(null)
  // `?view=plan` (the Overview's Plan button) opens on the plan
  const openOnPlan = (useSearch({ strict: false }) as { view?: string }).view === 'plan'
  const [mode, setModeState] = useState<ShellMode>(openOnPlan ? 'plan' : 'preview')
  // set once the user picks a view themselves; until then the preview opens by itself when the app is ready
  const userPickedMode = useRef(openOnPlan)
  const openOnPlanRef = useRef(openOnPlan)
  openOnPlanRef.current = openOnPlan
  const setMode = useCallback((m: ShellMode) => setModeState(m), [])
  const [previewErrors, setPreviewErrors] = useState<PreviewError[]>([])
  const fixedVersions = useRef<Set<string>>(new Set())
  const [codeFocus, setCodeFocus] = useState<string | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [publishOpen, setPublishOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [unpublishOpen, setUnpublishOpen] = useState(false)
  const [testerOpen, setTesterOpen] = useState(false)
  const [railOpen, setRailOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const composerRef = useRef<ComposerHandle>(null)

  // Reset per app.
  useEffect(() => {
    useAppDocumentStore.getState().reset()
    useAppBuilderChatStore.getState().clearHistory()
    usePreviewSession.getState().reset(appId)
    useConsoleStore.getState().clear()
    setLoadedFor(null)
    // a deep link to the Plan tab (the Overview's "Plan" button) opens there and stays until the user switches
    setMode(openOnPlanRef.current ? 'plan' : 'preview')
    userPickedMode.current = openOnPlanRef.current
    fixedVersions.current = new Set()
    setPreviewErrors([])
    setHistoryOpen(false)
  }, [appId, setMode])

  // A reload empties the in-memory chat: rebuild it from the app's AI versions (the engine keeps no transcript).
  // The plan the app came from supplies the original request and the checklist answers a build's versions lack.
  const planQ = usePlan(pid)
  const planFetched = planQ.isFetched
  const planBody = (planQ.data?.approved ?? planQ.data?.draft)?.body
  useEffect(() => {
    if (!planFetched) return
    let live = true
    appsApi
      .listVersions(appId)
      .then((vs) => {
        const msgs = restoreChatMessages(vs, {
          title: planBody?.title,
          summary: planBody?.summary,
          request: planBody?.requirements?.request,
          answers: planBody?.requirements?.answers,
        })
        if (live && msgs.length) useAppBuilderChatStore.getState().restore(msgs)
      })
      .catch(() => {})
    return () => {
      live = false
    }
  }, [appId, planFetched, planBody])

  // The plan-and-build conversation, when this app came out of it: shown ahead of the chat, and the chat waits while the
  // build is still making the app. As the build's UI step saves versions, the preview follows them.
  const flow = usePlanFlow(pid, appId)
  const building = flow.visible && (flow.phase === 'building' || flow.phase === 'input')
  const seededVersion = useRef<number | null>(null)
  const onAppActivity = useCallback(() => {
    void qc.invalidateQueries({ queryKey: qk.app(appId) })
  }, [qc, appId])
  useEffect(() => {
    if (!flow.visible || !app || loadedFor !== appId || app.id !== appId) return
    const v = app.current_version?.id ?? null
    if (v == null || v === seededVersion.current) return
    seededVersion.current = v
    const doc = useAppDocumentStore.getState()
    if (doc.dirty) return
    const appFiles = stripClient(app.files)
    if (appFiles.length) doc.setFiles(appFiles)
    else if (app.source_code) doc.setSourceCode(app.source_code)
    doc.setCurrentVersion(app.current_version ?? null)
  }, [flow.visible, app, appId, loadedFor])

  // Seed the document once per app from the query.
  useEffect(() => {
    if (!app || loadedFor === appId || app.id !== appId) return
    const doc = useAppDocumentStore.getState()
    const appFiles = stripClient(app.files)
    if (appFiles.length) doc.setFiles(appFiles)
    else if (app.source_code) doc.setSourceCode(app.source_code)
    doc.setCurrentVersion(app.current_version ?? null)
    seededVersion.current = app.current_version?.id ?? null
    setLoadedFor(appId)
  }, [app, appId, loadedFor])
  const loaded = loadedFor === appId

  // The preview opens by itself once the app is ready (files seeded or a build finished), unless the user chose a view.
  const hasFiles = !!files?.length
  useEffect(() => {
    if (loaded && hasFiles && !userPickedMode.current) setMode('preview')
  }, [loaded, hasFiles, building, setMode])

  // the build that made this app (the one this session started, else the latest that touched it) carries the scenario checks
  const builds = useBuilds(pid).data
  const checks = useMemo(() => {
    const b = (builds ?? []).find((x) => x.id === flow.buildId) ?? (builds ?? []).find((x) => x.steps.some((st) => st.app_id === appId))
    return autotestFromBuild(b)
  }, [builds, flow.buildId, appId])

  const execIds = app?.api_execution_ids
  const attached = useMemo(() => execIds ?? [], [execIds])
  const chat = useAppBuilderChat({ appId, pid, apiExecutionIds: attached })
  const sendMessage = chat.sendMessage

  // Preview errors -> one automatic fix per version (never while a build or edit runs), else a manual "Fix with AI".
  const busy = chat.isStreaming || building
  const versionKey = currentVersion?.id ?? null
  const wasBusy = useRef(false)
  useEffect(() => {
    // the version an automatic fix produced counts as fixed: no chain of automatic fixes
    if (wasBusy.current && !busy && autoFixPending.current && versionKey != null) {
      fixedVersions.current.add(String(versionKey))
      autoFixPending.current = false
    }
    wasBusy.current = busy
  }, [busy, versionKey])
  const autoFixPending = useRef(false)
  const sendFix = useCallback(
    (auto: boolean) => {
      if (!previewErrors.length) return
      if (auto) autoFixPending.current = true
      if (versionKey != null) fixedVersions.current.add(String(versionKey))
      void sendMessage(fixRequestText(previewErrors))
    },
    [previewErrors, versionKey, sendMessage],
  )
  useEffect(() => {
    if (loaded && shouldAutoFix({ errors: previewErrors, versionKey, fixed: fixedVersions.current, busy })) sendFix(true)
  }, [loaded, previewErrors, versionKey, busy, sendFix])

  // Auto-send the Create UI prompt once, then drop it from the URL (§5.1 step 3).
  useEffect(() => {
    if (!loaded || !search.prompt) return
    const key = `${appId}\0${search.prompt}`
    if (!autoSent.has(key)) {
      autoSent.add(key)
      void sendMessage(search.prompt)
    }
    navigate({
      to: '.',
      search: (s: Record<string, unknown>) => {
        const next = { ...s }
        delete next.prompt
        return next
      },
      replace: true,
    } as never)
  }, [loaded, search.prompt, appId, sendMessage, navigate])

  // Preview files: the app's files + the current generated client (§5.2).
  const previewFiles = useMemo(() => {
    if (!files?.length) return files
    const own = stripClient(files)
    return clientQ.data ? [...own, { path: CLIENT_PATH, content: clientQ.data }] : own
  }, [files, clientQ.data])

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

  const refreshUsage = useCallback(() => {
    void qc.invalidateQueries({ queryKey: qk.apiUsage(appId) })
  }, [qc, appId])

  const handleSave = useCallback(async () => {
    const cur = useAppDocumentStore.getState().files
    if (!cur?.length || saving) return
    setSaving(true)
    setSaveError(null)
    try {
      const own = stripClient(cur)
      const res = await save({ source_code: entrySource(own), files: own, create_version: true, version_trigger: 'manual' })
      if (res.current_version) useAppDocumentStore.getState().setCurrentVersion(res.current_version)
      useAppDocumentStore.getState().setDirty(false)
    } catch (e) {
      setSaveError(errorMessage(e))
    } finally {
      setSaving(false)
      refreshUsage()
    }
  }, [save, saving, refreshUsage])

  const handleRevert = useCallback(
    async (versionId: number) => {
      try {
        const res = await appsApi.revertToVersion(appId, versionId)
        const doc = useAppDocumentStore.getState()
        const own = stripClient(res.files)
        if (own.length) doc.setFiles(own)
        else if (res.source_code) doc.setSourceCode(res.source_code)
        if (res.current_version) doc.setCurrentVersion(res.current_version)
        doc.setDirty(false)
        qc.setQueryData(qk.app(appId), (old: ApiAppDetail | undefined) => (old ? { ...old, ...res, definition: old.definition } : old))
        void qc.invalidateQueries({ queryKey: qk.app(appId) })
        refreshUsage()
        toast.success(`Reverted to v${res.current_version?.version_number ?? '?'}`)
      } catch (e) {
        toast.error("Couldn't revert", { description: errorMessage(e) })
      }
    },
    [appId, qc, refreshUsage],
  )

  // ⌘S save, ⌘I focus the composer (§8.2).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey) return
      const k = e.key.toLowerCase()
      if (k === 's') {
        e.preventDefault()
        void handleSave()
      } else if (k === 'i') {
        e.preventDefault()
        composerRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [handleSave])

  const openClient = useCallback(() => {
    setCodeFocus(CLIENT_PATH)
    setMode('code')
    setRailOpen(false)
  }, [])

  const onUse = useCallback((op: string) => {
    usePreviewSession.getState().setDraft(`Use api.${op}() to `)
    setRailOpen(false)
  }, [])

  const { used, total } = useOpCounts(routes, usageQ.data)
  const publishSlug = app?.slug ?? search.slug ?? slugify(app?.name ?? '')
  const tone = healthTone(summary.data?.health.status)

  if (appQ.isError) {
    return (
      <div className="flex h-full items-center justify-center" data-testid="builder-load-error">
        <EmptyState
          title="Couldn't load this app"
          actions={
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => appQ.refetch()}>
                Retry
              </Button>
              <Button variant="ghost" size="sm" render={<Link to="/p/$project" params={{ project: slug }} />}>
                Back to {project.name}
              </Button>
            </div>
          }
        >
          {errorMessage(appQ.error)}
        </EmptyState>
      </div>
    )
  }

  const backendPanel = (
    <BackendPanel
      pid={pid}
      appId={appId}
      projectName={project.name}
      projectSlug={slug}
      healthTone={tone}
      env={env}
      routes={routes}
      routesLoading={routesQ.isLoading}
      usage={usageQ.data}
      usageLoading={usageQ.isLoading}
      usageError={usageQ.error}
      onRetryUsage={() => usageQ.refetch()}
      onRefresh={() => {
        void routesQ.refetch()
        void usageQ.refetch()
        void clientQ.refetch()
      }}
      client={clientQ.data}
      revision={revision}
      onOpenClient={openClient}
      onUse={onUse}
    />
  )

  return (
    <div className="relative h-full w-full overflow-hidden" data-testid="app-builder">
      {/* top bar */}
      <TopBarFrame data-testid="builder-topbar" className="pl-1.5">
        <HomeNav />
        <span className="mx-0.5 h-4 w-px shrink-0 bg-hairline" aria-hidden />
        {app ? <AppSwitcher app={app} onDelete={() => setDeleteOpen(true)} /> : <span className="px-2 text-muted-foreground">Loading…</span>}
        {app && <VersionBadge saving={saving} error={saveError} onSave={() => void handleSave()} />}
        {app && (
          <Badge
            variant={app.active ? 'success' : 'neutral'}
            className="ml-1"
            data-testid="app-publish-status"
            data-state={app.active ? 'published' : 'draft'}
          >
            {app.active ? 'Published' : 'Draft'}
          </Badge>
        )}
        <div className="flex-1" />
        <Segmented<ShellMode>
          value={mode}
          onChange={(m) => {
            userPickedMode.current = true
            setMode(m)
            if (m === 'code') setCodeFocus(null)
          }}
          aria-label="View"
          options={[
            { key: 'plan', label: 'Plan', icon: <ClipboardList className="size-3" />, testId: 'builder-mode-plan' },
            { key: 'preview', label: 'Preview', icon: <Eye className="size-3" />, testId: 'builder-mode-preview' },
            { key: 'code', label: 'Code', icon: <CodeXml className="size-3" />, testId: 'builder-mode-code' },
          ]}
        />
        <span className="mx-1.5 h-4 w-px bg-hairline" />
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                className={cn(historyOpen && 'bg-raise text-foreground shadow-raise-sm')}
                onClick={() => setHistoryOpen((o) => !o)}
                aria-label="Version history"
                aria-pressed={historyOpen}
                data-testid="builder-history"
              />
            }
          >
            <History />
          </TooltipTrigger>
          <TooltipContent>Version history</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger
            render={
              <Button variant="ghost" size="icon-sm" onClick={() => setSettingsOpen(true)} disabled={!app} aria-label="App settings" data-testid="builder-settings" />
            }
          >
            <Settings2 />
          </TooltipTrigger>
          <TooltipContent>App settings</TooltipContent>
        </Tooltip>
        <ThemeToggle />
        <Button
          size="sm"
          className="ml-1"
          onClick={() => setPublishOpen(true)}
          disabled={!app || !files?.length}
          data-testid="builder-publish"
        >
          <Send /> {app?.active ? 'Republish' : 'Publish'}
        </Button>
      </TopBarFrame>

      {/* work area: editor-glass panes, 8px gutters (the workspace sheet geometry) */}
      <div className="absolute inset-x-2 bottom-2 top-14 flex gap-2">
        <Tile editor as="section" className={cn(panel, 'shrink-0', narrow ? 'w-11' : 'w-[288px]')} data-testid="builder-backend">
          {narrow ? (
            <BackendRail used={used} total={total} open={railOpen} onToggle={() => setRailOpen((o) => !o)} onOpenClient={openClient} />
          ) : (
            backendPanel
          )}
        </Tile>
        {narrow && railOpen && (
          <Tile editor as="section" className={cn(panel, 'absolute bottom-0 left-[52px] top-0 z-30 w-[288px]')} data-testid="builder-backend-overlay">
            <Button
              variant="ghost"
              size="icon-xs"
              className="absolute right-9 top-2.5 z-10"
              onClick={() => setRailOpen(false)}
              aria-label="Close backend panel"
            >
              <X />
            </Button>
            {backendPanel}
          </Tile>
        )}

        <Tile editor as="section" className={cn(panel, 'min-w-0 flex-1')} data-testid="builder-preview-panel">
          {mode === 'plan' && !historyOpen && !testerOpen && (
            <div className="absolute inset-0 z-20 overflow-y-auto px-6 py-5" data-testid="builder-plan">
              <div className="mx-auto max-w-3xl">
                <PlanWorkspace compact />
              </div>
            </div>
          )}
          <div className={cn('h-full', (historyOpen || testerOpen || mode === 'plan') && 'invisible')} data-testid="builder-preview-content">
          {!loaded ? (
            <div className="flex h-full items-center justify-center" data-testid="builder-loading">
              <Spinner className="text-muted-foreground" />
            </div>
          ) : (
            <AppPreviewPanel
              key={appId}
              appId={appId}
              projectSlug={slug}
              workflowPaths={(app?.workflow_ids ?? []).map((id) => `/api/apps/${encodeURIComponent(appId)}/workflow/${encodeURIComponent(id)}/run`)}
              allowedRoutes={(routes ?? []).filter((route) => routeIdsOf(app).includes(route.id))}
              files={previewFiles}
              clientLoading={clientQ.isPending}
              mode={mode === 'plan' ? 'preview' : mode}
              slug={publishSlug}
              env={env}
              publishedUrl={app?.active && app.slug ? publicUrl(app.slug) : null}
              showUrl={!narrow}
              getToken={token.get}
              refreshToken={token.refresh}
              onApiCall={onApiCall}
              previewErrors={previewErrors}
              onPreviewErrors={setPreviewErrors}
              onFixErrors={() => sendFix(false)}
              fixDisabled={busy}
              autoFixed={versionKey != null && fixedVersions.current.has(String(versionKey))}
              codeFocus={codeFocus}
            />
          )}
          </div>
          {historyOpen && (
            <div className="glass-overlay absolute bottom-2 right-2 top-2 z-30 w-[248px] overflow-hidden">
              <VersionHistory
                appId={appId}
                currentVersionId={currentVersion?.id ?? null}
                publishedVersionId={app?.published_version?.id ?? null}
                onRevert={handleRevert}
                onClose={() => setHistoryOpen(false)}
              />
            </div>
          )}
        </Tile>

        <Tile editor as="section" className={cn(panel, 'shrink-0', narrow ? 'w-[320px]' : 'w-[360px]')} data-testid="builder-chat-panel">
          <AppBuilderChatPanel
            ref={composerRef}
            projectName={project.name}
            routes={routes ?? []}
            routeIds={routeIdsOf(app)}
            onSaveRoutes={(ids) => save({ studio: { route_ids: ids } })}
            attached={attached}
            onAttach={(ids) => {
              void save({ api_execution_ids: ids }).catch((e) => toast.error("Couldn't attach", { description: errorMessage(e) }))
            }}
            isStreaming={chat.isStreaming}
            onSend={(t) => void chat.sendMessage(t)}
            onRetry={() => void chat.retry()}
            onStop={chat.cancelStream}
            prelude={
              flow.visible ? (
                <>
                  <PlanFlowThread f={flow} slug={slug} onAppActivity={onAppActivity} />
                  {checks && <ChecksChip autotest={checks} />}
                </>
              ) : undefined
            }
            locked={building ? 'The build is still making this app; ask for changes when it is done' : undefined}
          />
        </Tile>

        {/* the tester covers the (white) sandbox iframe: frost over it reads as a grey slab, so it uses the solid overlay surface */}
        {testerOpen && (
          <div className="glass-overlay absolute inset-0 z-40 flex overflow-hidden" data-testid="builder-api-tester">
            <div className="w-[260px] shrink-0 overflow-hidden shadow-[inset_-1px_0_0_var(--hairline)]">
              <SavedQueriesPanel onClose={() => setTesterOpen(false)} />
            </div>
            <div className="relative min-w-0 flex-1 overflow-hidden">
              <ApiTesterPanel onClose={() => setTesterOpen(false)} />
            </div>
          </div>
        )}
      </div>

      {app && (
        <>
          <PublishDialog
            open={publishOpen}
            onClose={() => setPublishOpen(false)}
            app={app}
            project={project}
            summary={summary.data}
            routes={routes}
            usage={usageQ.data}
            usageLoading={usageQ.isLoading}
            slugHint={search.slug}
          />
          <AppSettingsSheet
            open={settingsOpen}
            onClose={() => setSettingsOpen(false)}
            app={app}
            pid={pid}
            routes={routes ?? []}
            onOpenTester={() => {
              setSettingsOpen(false)
              setTesterOpen(true)
            }}
            onUnpublish={() => setUnpublishOpen(true)}
            onDelete={() => setDeleteOpen(true)}
          />
          <DeleteAppDialog app={app} pid={pid} projectSlug={slug} open={deleteOpen} onOpenChange={setDeleteOpen} />
          <UnpublishDialog app={app} pid={pid} open={unpublishOpen} onOpenChange={setUnpublishOpen} onDone={() => setSettingsOpen(false)} />
        </>
      )}
    </div>
  )
}
