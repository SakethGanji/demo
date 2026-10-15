/**
 * App builder (UI-DESIGN §5.2, `builder.png`): `[Backend 288] [Preview] [Chat 360]` under a
 * floating top bar `luna / Project / App ⇕ · vN · saved · Preview|Code · history · settings ·
 * Publish`. Below 1280px the Backend panel is a 44px rail, chat is 320 and the URL pill hides.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import {
  AppWindow,
  Check,
  ChevronsUpDown,
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
import { useProjectApps, useProjectSummary, useRoutes } from '@/features/project/hooks/useProjectData'
import { Brand, ThemeToggle, healthTone } from '@/features/project/components/ProjectTopBar'
import { appsApi, type ApiAppDetail } from '@/shared/lib/api'
import { backends } from '@/shared/lib/config'
import { errorMessage } from '@/shared/api/http'
import { qk } from '@/shared/api/queryKeys'
import { Button } from '@/shared/components/ui/button'
import { Badge } from '@/shared/components/ui/badge'
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

const panel = 'rounded-xl border border-panel-border bg-card shadow-xs overflow-hidden'

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
        className="inline-flex h-7 min-w-0 items-center gap-1.5 rounded-md px-2 font-medium hover:bg-accent"
      >
        <AppWindow className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="max-w-48 truncate" data-testid="app-name">
          {app.name}
        </span>
        <ChevronsUpDown className="size-3 shrink-0 text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent className="w-64 p-1" align="start">
        <div className="px-2 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">Apps</div>
        <div className="max-h-72 overflow-y-auto">
          {(apps.data ?? []).map((a) => (
            <button
              key={a.id}
              type="button"
              onClick={() => {
                setOpen(false)
                if (a.id !== app.id) navigate({ to: '/p/$project/apps/$appId', params: { project: slug, appId: a.id } })
              }}
              className="flex h-8 w-full items-center gap-2 rounded-md px-2 text-left hover:bg-accent"
              data-testid={`app-switcher-item-${a.id}`}
            >
              <AppWindow className="size-3.5 text-muted-foreground" />
              <span className="flex-1 truncate">{a.name}</span>
              {a.active && <Badge variant="success" className="h-[18px] px-1.5 text-[10.5px]">live</Badge>}
              {a.id === app.id && <Check className="size-3.5 text-muted-foreground" />}
            </button>
          ))}
        </div>
        <div className="my-1 h-px bg-border" />
        <button
          type="button"
          onClick={() => {
            setOpen(false)
            navigate({ to: '/p/$project', params: { project: slug }, search: { dialog: 'create-ui' } })
          }}
          className="flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-muted-foreground hover:bg-accent hover:text-foreground"
          data-testid="app-switcher-new"
        >
          <Plus className="size-3.5" /> New app from the backend
        </button>
        <button
          type="button"
          onClick={() => {
            setOpen(false)
            onDelete()
          }}
          className="flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-destructive hover:bg-destructive/10"
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
              'ml-1 inline-flex h-5 items-center gap-1 rounded-full bg-secondary px-2 text-xs font-medium whitespace-nowrap text-secondary-foreground hover:bg-secondary/70 disabled:cursor-default disabled:hover:bg-secondary',
              (error || dirty) && 'bg-warning/13 text-warning-text',
              error && 'bg-destructive/10 text-destructive',
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
  const [mode, setMode] = useState<BuilderMode>('preview')
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
    setMode('preview')
    setHistoryOpen(false)
  }, [appId])

  // Seed the document once per app from the query.
  useEffect(() => {
    if (!app || loadedFor === appId || app.id !== appId) return
    const doc = useAppDocumentStore.getState()
    const appFiles = stripClient(app.files)
    if (appFiles.length) doc.setFiles(appFiles)
    else if (app.source_code) doc.setSourceCode(app.source_code)
    doc.setCurrentVersion(app.current_version ?? null)
    setLoadedFor(appId)
  }, [app, appId, loadedFor])
  const loaded = loadedFor === appId

  const execIds = app?.api_execution_ids
  const attached = useMemo(() => execIds ?? [], [execIds])
  const chat = useAppBuilderChat({ appId, pid, apiExecutionIds: attached })
  const sendMessage = chat.sendMessage

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
      <div className="flex h-full items-center justify-center bg-canvas" data-testid="builder-load-error">
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
    <div className="relative h-full w-full overflow-hidden bg-canvas" data-testid="app-builder">
      {/* top bar */}
      <header
        data-testid="builder-topbar"
        className="absolute inset-x-3 top-3 z-20 flex h-11 items-center gap-1 rounded-xl border border-panel-border bg-card/82 pl-2.5 pr-2 shadow-lg backdrop-blur-xl"
      >
        <Brand />
        <span className="mx-0.5 text-base text-border">/</span>
        <Link
          to="/p/$project"
          params={{ project: slug }}
          className="inline-flex h-7 min-w-0 items-center gap-1.5 rounded-md px-2 text-muted-foreground hover:bg-accent hover:text-foreground"
          data-testid="builder-project-link"
        >
          <span className={cn('size-1.5 shrink-0 rounded-full', tone === 'ok' ? 'bg-success' : tone === 'warn' ? 'bg-warning' : tone === 'err' ? 'bg-destructive' : tone === 'running' ? 'bg-primary' : 'bg-border')} />
          <span className="max-w-40 truncate">{project.name}</span>
        </Link>
        <span className="mx-0.5 text-base text-border">/</span>
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
        <Segmented<BuilderMode>
          value={mode}
          onChange={(m) => {
            setMode(m)
            if (m === 'code') setCodeFocus(null)
          }}
          aria-label="View"
          options={[
            { key: 'preview', label: 'Preview', icon: <Eye className="size-3" />, testId: 'builder-mode-preview' },
            { key: 'code', label: 'Code', icon: <CodeXml className="size-3" />, testId: 'builder-mode-code' },
          ]}
        />
        <span className="mx-1.5 h-4 w-px bg-border" />
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                className={cn(historyOpen && 'bg-muted text-foreground')}
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
      </header>

      {/* work area */}
      <div className="absolute inset-x-3 bottom-3 top-[68px] flex gap-3">
        <section className={cn(panel, 'relative shrink-0', narrow ? 'w-11' : 'w-[288px]')} data-testid="builder-backend">
          {narrow ? (
            <BackendRail used={used} total={total} open={railOpen} onToggle={() => setRailOpen((o) => !o)} onOpenClient={openClient} />
          ) : (
            backendPanel
          )}
        </section>
        {narrow && railOpen && (
          <section className={cn(panel, 'absolute bottom-0 left-14 top-0 z-30 w-[288px] shadow-2xl')} data-testid="builder-backend-overlay">
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
          </section>
        )}

        <section className={cn(panel, 'relative min-w-0 flex-1')} data-testid="builder-preview-panel">
          {!loaded ? (
            <div className="flex h-full items-center justify-center" data-testid="builder-loading">
              <Spinner className="text-muted-foreground" />
            </div>
          ) : (
            <AppPreviewPanel
              files={previewFiles}
              mode={mode}
              slug={publishSlug}
              env={env}
              publishedUrl={app?.active && app.slug ? publicUrl(app.slug) : null}
              showUrl={!narrow}
              getToken={token.get}
              refreshToken={token.refresh}
              onApiCall={onApiCall}
              codeFocus={codeFocus}
            />
          )}
          {historyOpen && (
            <div className="absolute bottom-2 right-2 top-2 z-30 w-[248px] overflow-hidden rounded-lg border border-border shadow-lg">
              <VersionHistory
                appId={appId}
                currentVersionId={currentVersion?.id ?? null}
                publishedVersionId={app?.published_version?.id ?? null}
                onRevert={handleRevert}
                onClose={() => setHistoryOpen(false)}
              />
            </div>
          )}
        </section>

        <section className={cn(panel, 'shrink-0', narrow ? 'w-[320px]' : 'w-[360px]')} data-testid="builder-chat-panel">
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
          />
        </section>

        {testerOpen && (
          <div className={cn(panel, 'absolute inset-0 z-40 flex shadow-2xl')} data-testid="builder-api-tester">
            <div className="w-[260px] shrink-0 overflow-hidden border-r border-border">
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
