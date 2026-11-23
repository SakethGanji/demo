/**
 * New app (`/p/$project/apps/new`) as one screen from the first sentence to a working app, and on after it.
 *
 *   hero        "What do you want to build?": a centred prompt with example chips. Sending it flies the prompt into
 *               the chat column (a shared layout) while the workspace slides in.
 *   workspace   Code | Preview | Chat. The chat is the plan-and-build conversation (questions, the plan to approve,
 *               the live build timeline); Code shows the files as the builders write them; Preview shows the routes
 *               being built, then the app. A build that made its app ends on "Your app is ready".
 *   after       the same screen: the chat becomes the app builder chat for the new app (one send path; its builders
 *               stream the same file / route / preview events, which land in the same Code and Preview panes), and
 *               modules and app files edit in place in Code.
 *
 * Below 1280px Code and Preview share one pane (a switch in the top bar); on a phone Code, Preview and Chat are tabs.
 * Reduced motion keeps plain fades. A late joiner (a reload mid-build) lands in the workspace: the build's snapshot
 * fills the timeline, the files API the tree.
 */
import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { AnimatePresence, LayoutGroup, motion } from 'motion/react'
import { AppWindow, ArrowUp, CodeXml, Eye, ExternalLink, MessageSquare, Sparkles } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { HomeNav } from '@/features/project/components/HomeNav'
import { ThemeToggle, TopBarFrame } from '@/features/project/components/TopBar'
import { useProjectSummary, useRoutes } from '@/features/project/hooks/useProjectData'
import { PlanFlowThread } from '@/features/plan/PlanFlow'
import { BuildStatusBadge } from '@/features/plan/BuildTimeline'
import { buildCounts, isActive } from '@/features/plan/buildLogic'
import type { Build, BuildEvent } from '@/features/plan/buildTypes'
import { applyLiveEvent, EMPTY_LIVE, mergeLive, type LiveState } from '@/features/plan/liveBuild'
import { GLIDE, useCalm } from '@/features/plan/motion'
import { composerHint, usePlanFlow, type PlanFlowState } from '@/features/plan/usePlanFlow'
import { SourcesPicker } from '@/features/connections/SourcesPicker'
import { buildSources, type SourceSelection } from '@/features/connections/connectionsLogic'
import { usePlanFlowStore } from '@/features/plan/planFlowStore'
import { useLiveBuild } from '@/features/plan/useBuild'
import { AppBuilderChatPanel, type ComposerHandle } from '@/features/app-builder/components/AppBuilderChatPanel'
import { PublishDialog } from '@/features/app-builder/components/PublishDialog'
import { useApiUsage, useSaveApp } from '@/features/app-builder/hooks/useAppData'
import { useAppBuilderChat } from '@/features/app-builder/hooks/useAppBuilderChat'
import { routeIdsOf } from '@/features/app-builder/lib/appModel'
import { useAppBuilderChatStore } from '@/features/app-builder/stores/appBuilderChatStore'
import { appsApi } from '@/shared/lib/api'
import { qk } from '@/shared/api/queryKeys'
import { errorMessage } from '@/shared/api/http'
import { Button } from '@/shared/components/ui/button'
import { Segmented } from '@/shared/components/ui/segmented'
import { Spinner } from '@/shared/components/ui/spinner'
import { Tile } from '@/shared/components/ui/surface'
import { useViewportWidth } from '@/shared/hooks/useViewport'
import { cn } from '@/shared/lib/utils'
import { LiveCodePane } from './LiveCodePane'
import { LivePreviewPane } from './LivePreviewPane'
import { ReadyCard } from './ReadyCard'
import { builtAppId, EXAMPLES, readyApp, studioView } from './studioLogic'

const PROMPT = 'studio-prompt'
type Tab = 'code' | 'preview' | 'chat'

const useApp = (appId: string | null) =>
  useQuery({ queryKey: qk.app(appId ?? ''), queryFn: () => appsApi.get(appId as string), enabled: !!appId, staleTime: 30_000 })

/* ── hero ─────────────────────────────────────────────────────────────────────────────────────────────────────── */

function Composer({
  value,
  onChange,
  onSend,
  disabled,
  placeholder,
  rows,
  autoFocus,
  className,
}: {
  value: string
  onChange: (v: string) => void
  onSend: () => void
  disabled?: boolean
  placeholder: string
  rows: number
  autoFocus?: boolean
  className?: string
}) {
  return (
    <div className={cn('rounded-panel bg-recess has-[textarea:focus-visible]:shadow-focus', className)} data-recess>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            onSend()
          }
        }}
        rows={rows}
        disabled={disabled}
        placeholder={placeholder}
        className="block max-h-48 w-full resize-none bg-transparent px-3.5 pt-3 text-[13.5px] leading-5 outline-none placeholder:text-muted-foreground"
        data-testid="chat-input"
        autoFocus={autoFocus}
      />
      <div className="flex items-center justify-end px-2 pt-1 pb-2">
        <Button size="icon-sm" onClick={onSend} disabled={!value.trim() || disabled} aria-label="Send" data-testid="chat-send">
          <ArrowUp />
        </Button>
      </div>
    </div>
  )
}

function Hero({ f }: { f: PlanFlowState }) {
  const { slug } = useProjectCtx()
  const calm = useCalm()
  const [text, setText] = useState('')
  const [sources, setSources] = useState<SourceSelection>({})
  const send = () => {
    const t = text.trim()
    if (t && f.canDescribe) f.describe(t, buildSources(sources))
  }
  const fade = (delay: number) =>
    calm
      ? { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 }, transition: { duration: 0.18 } }
      : { initial: { opacity: 0, y: 8 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -6 }, transition: { duration: 0.28, delay, ease: [0.16, 1, 0.3, 1] as const } }
  return (
    <div className="absolute inset-x-2 top-14 bottom-2 flex items-center justify-center overflow-y-auto px-4" data-testid="studio-hero">
      <div className="flex w-full max-w-[640px] flex-col items-center gap-5 pb-[8vh]">
        <motion.div {...fade(0)} className="flex flex-col items-center gap-2 text-center">
          <span className="grid size-10 place-items-center rounded-full bg-ai/12 text-ai">
            <Sparkles className="size-4.5" />
          </span>
          <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.03em]">What do you want to build?</h1>
          <p className="max-w-md text-[13.5px] leading-5 text-muted-foreground">
            Describe the app. I'll ask what I need, show you the plan, then build the backend and the app while you watch.
          </p>
        </motion.div>
        <motion.div layoutId={calm ? undefined : PROMPT} transition={GLIDE} className="w-full">
          <Composer
            value={text}
            onChange={setText}
            onSend={send}
            disabled={!f.canDescribe}
            placeholder={composerHint(f)}
            rows={3}
            autoFocus
            className="glass-chrome"
          />
          <SourcesPicker pid={f.pid} value={sources} onChange={setSources} disabled={!f.canDescribe} className="mt-2 px-1" />
        </motion.div>
        <motion.div {...fade(0.06)} className="flex flex-wrap justify-center gap-2" data-testid="studio-examples">
          {EXAMPLES.map((e) => (
            <Button
              key={e.label}
              type="button"
              variant="outline"
              onClick={() => setText(e.prompt)}
              className="text-muted-foreground hover:text-foreground"
              data-testid={`studio-example-${e.label}`}
            >
              {e.label}
            </Button>
          ))}
        </motion.div>
        <motion.div {...fade(0.1)}>
          <Link
            to="/p/$project"
            params={{ project: slug }}
            search={{ dialog: 'create-ui' }}
            className="text-[12px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            data-testid="new-app-direct"
          >
            Or build a screen straight on your existing routes
          </Link>
        </motion.div>
      </div>
    </div>
  )
}

/* ── chat ─────────────────────────────────────────────────────────────────────────────────────────────────────── */

/** The user's description: the hero's prompt, landed in the chat. */
function PromptBubble({ prompt }: { prompt: string }) {
  const calm = useCalm()
  return (
    <motion.div
      layoutId={calm ? undefined : PROMPT}
      transition={GLIDE}
      className="ml-auto max-w-[88%] rounded-panel bg-raise px-3.5 py-2.5 text-[13px] leading-[19px] whitespace-pre-wrap shadow-raise-sm"
      data-testid="flow-user-message"
    >
      {prompt}
    </motion.div>
  )
}

function ChatHeader() {
  return (
    <div className="hairline-b flex h-11 shrink-0 items-center gap-2 pr-2.5 pl-3.5">
      <Sparkles className="size-3.5 text-ai" />
      <span className="text-panel-title font-semibold tracking-[-0.01em]">Build with AI</span>
    </div>
  )
}

/** The conversation before the app exists: the plan-and-build thread and the description composer. */
function FlowChat({ f, thread }: { f: PlanFlowState; thread: ReactNode }) {
  const [text, setText] = useState('')
  const [sources, setSources] = useState<SourceSelection>({})
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [f.phase])
  const send = () => {
    const t = text.trim()
    if (!t || !f.canDescribe) return
    f.describe(t, buildSources(sources))
    setText('')
    setSources({})
  }
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <ChatHeader />
      <div ref={scrollRef} className="flex min-h-0 flex-1 flex-col gap-3.5 overflow-y-auto p-3.5" data-testid="chat-messages">
        {f.loading ? (
          <div className="flex justify-center py-6">
            <Spinner className="text-muted-foreground" />
          </div>
        ) : (
          thread
        )}
      </div>
      {f.canDescribe && <SourcesPicker pid={f.pid} value={sources} onChange={setSources} className="mx-4 mb-2" />}
      <Composer value={text} onChange={setText} onSend={send} disabled={!f.canDescribe} placeholder={composerHint(f)} rows={2} className="mx-3 mb-3" />
    </div>
  )
}

/** After the build: the app builder chat for the app it made (the one send path), the build conversation above it. */
function AppChat({
  appId,
  thread,
  composerRef,
  onLive,
}: {
  appId: string
  thread: ReactNode
  composerRef: React.Ref<ComposerHandle>
  onLive: (ev: BuildEvent) => void
}) {
  const { project, pid } = useProjectCtx()
  const app = useApp(appId).data
  const routes = useRoutes(pid).data
  const save = useSaveApp(appId)
  const execIds = app?.api_execution_ids
  const attached = useMemo(() => execIds ?? [], [execIds])
  const chat = useAppBuilderChat({ appId, pid, apiExecutionIds: attached, onLive })
  return (
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
      prelude={thread}
    />
  )
}

function ShareDialog({ appId, onClose }: { appId: string; onClose: () => void }) {
  const { project, pid } = useProjectCtx()
  const app = useApp(appId).data
  const summary = useProjectSummary(pid)
  const routes = useRoutes(pid)
  const usage = useApiUsage(appId)
  if (!app) return null
  return (
    <PublishDialog open onClose={onClose} app={app} project={project} summary={summary.data} routes={routes.data} usage={usage.data} usageLoading={usage.isLoading} />
  )
}

/* ── workspace ────────────────────────────────────────────────────────────────────────────────────────────────── */

function Workspace({ f }: { f: PlanFlowState }) {
  const { pid, slug } = useProjectCtx()
  const navigate = useNavigate()
  const calm = useCalm()
  const width = useViewportWidth()
  const phone = width < 768
  const wide = width >= 1280
  const [tab, setTab] = useState<Tab>('preview')
  const [shareOpen, setShareOpen] = useState(false)
  const composerRef = useRef<ComposerHandle>(null)

  // one event stream for the timeline, the code and the preview
  const run = useLiveBuild(pid, f.buildId)
  const build = run.build
  // the app chat's builders stream the same events after the build
  const [chatLive, dispatchChat] = useReducer((s: LiveState, ev: BuildEvent) => applyLiveEvent(s, ev), EMPTY_LIVE)
  const live = useMemo(() => mergeLive(run.files, chatLive), [run.files, chatLive])
  const chatBusy = useAppBuilderChatStore((s) => s.isStreaming)

  const appId = builtAppId(build) ?? f.flow?.appId ?? null
  const ready = readyApp(build)
  const app = useApp(appId).data
  const onAppReady = useCallback((id: string) => usePlanFlowStore.getState().setApp(pid, id), [pid])

  const open = useCallback(() => {
    if (ready) navigate({ to: '/p/$project/apps/$appId', params: { project: slug, appId: ready } })
  }, [navigate, ready, slug])
  const keepEditing = useCallback(() => {
    setTab('chat')
    requestAnimationFrame(() => composerRef.current?.focus())
  }, [])
  const finish = useCallback(
    (b: Build) => (readyApp(b) ? <ReadyCard build={b} onOpen={open} onShare={() => setShareOpen(true)} onKeepEditing={keepEditing} /> : null),
    [open, keepEditing],
  )

  const thread = (
    <>
      {f.flow?.prompt ? <PromptBubble prompt={f.flow.prompt} /> : null}
      <PlanFlowThread f={f} slug={slug} onAppReady={onAppReady} live={run} finish={finish} hidePrompt />
    </>
  )

  const planned = f.approved?.body.routes ?? []
  const pane = (k: 'code' | 'preview') =>
    k === 'code' ? (
      <LiveCodePane pid={pid} build={build} live={live} chatBusy={chatBusy} narrow={!wide} />
    ) : (
      <LivePreviewPane build={build} live={live} appId={appId} planned={planned} />
    )

  const slide = (n: number) =>
    calm
      ? { initial: { opacity: 0 }, animate: { opacity: 1 }, transition: { duration: 0.18 } }
      : { initial: { opacity: 0, x: -28, scale: 0.985 }, animate: { opacity: 1, x: 0, scale: 1 }, transition: { ...GLIDE, delay: 0.05 + n * 0.06 } }

  const active = !!build && isActive(build.status)
  const counts = build ? buildCounts(build) : null
  const name = app?.name ?? f.approved?.body.apps.find((a) => a.app_id === appId)?.name ?? 'New app'
  const show = (k: Tab) => (phone ? tab === k : k === 'chat' || wide || tab === k || (tab === 'chat' && k === 'preview'))

  return (
    <div className="relative h-full w-full overflow-hidden" data-testid="app-builder-new" data-studio="workspace">
      <TopBarFrame data-testid="builder-topbar" className="pl-1.5">
        <HomeNav />
        <span className="mx-0.5 h-4 w-px shrink-0 bg-hairline" aria-hidden />
        <span className="inline-flex min-w-0 items-center gap-1.5 px-2 font-medium" data-testid="app-name">
          <AppWindow className="size-3.5 shrink-0 text-muted-foreground" /> <span className="truncate">{name}</span>
        </span>
        {build && !phone ? (
          <span className="inline-flex items-center gap-2 text-[12px] text-muted-foreground tabular-nums" data-testid="studio-build-pill">
            <BuildStatusBadge status={build.status} />
            {active && counts ? `${counts.finished} of ${counts.total}` : null}
          </span>
        ) : null}
        <div className="flex-1" />
        {!wide ? (
          <Segmented<Tab>
            value={phone ? tab : tab === 'chat' ? 'preview' : tab}
            onChange={setTab}
            aria-label="View"
            data-testid="studio-tabs"
            options={[
              { key: 'code', label: 'Code', icon: <CodeXml className="size-3" />, testId: 'studio-tab-code' },
              { key: 'preview', label: 'Preview', icon: <Eye className="size-3" />, testId: 'studio-tab-preview' },
              ...(phone ? [{ key: 'chat' as const, label: 'Chat', icon: <MessageSquare className="size-3" />, testId: 'studio-tab-chat' }] : []),
            ]}
          />
        ) : null}
        <ThemeToggle />
        {ready ? (
          <Button size="sm" className="ml-1 max-md:hidden" onClick={open} data-testid="studio-open">
            <ExternalLink /> Open app
          </Button>
        ) : null}
      </TopBarFrame>

      <div className="absolute inset-x-2 top-14 bottom-2 flex gap-2">
        <motion.div {...slide(0)} className={cn('flex min-w-0 flex-1', !show('code') && 'hidden')}>
          <Tile editor as="section" className="min-w-0 flex-1 overflow-hidden" data-testid="studio-code-panel">
            {pane('code')}
          </Tile>
        </motion.div>
        <motion.div {...slide(1)} className={cn('flex min-w-0 flex-1', !show('preview') && 'hidden')}>
          <Tile editor as="section" className="min-w-0 flex-1 overflow-hidden" data-testid="builder-preview-panel">
            {pane('preview')}
          </Tile>
        </motion.div>
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.2 }}
          className={cn('flex', phone ? 'min-w-0 flex-1' : wide ? 'w-[400px] shrink-0' : 'w-[360px] shrink-0', !show('chat') && 'hidden')}
        >
          <Tile editor as="section" className="min-w-0 flex-1 overflow-hidden" data-testid="builder-chat-panel">
            {ready ? (
              <AppChat key={ready} appId={ready} thread={thread} composerRef={composerRef} onLive={dispatchChat} />
            ) : (
              <FlowChat f={f} thread={thread} />
            )}
          </Tile>
        </motion.div>
      </div>
      {shareOpen && ready ? <ShareDialog appId={ready} onClose={() => setShareOpen(false)} /> : null}
    </div>
  )
}

/* ── the page ─────────────────────────────────────────────────────────────────────────────────────────────────── */

export function BuildStudio() {
  const { pid } = useProjectCtx()
  const f = usePlanFlow(pid)
  const view = f.loading ? 'loading' : studioView(f.phase)
  return (
    <LayoutGroup>
      {view === 'hero' ? (
        <div className="relative h-full w-full overflow-hidden" data-testid="app-builder-new" data-studio="hero">
          <TopBarFrame data-testid="builder-topbar" className="pl-1.5">
            <HomeNav />
            <span className="mx-0.5 h-4 w-px shrink-0 bg-hairline" aria-hidden />
            <span className="inline-flex items-center gap-1.5 px-2 font-medium" data-testid="app-name">
              <AppWindow className="size-3.5 text-muted-foreground" /> New app
            </span>
            <div className="flex-1" />
            <ThemeToggle />
          </TopBarFrame>
          <AnimatePresence>
            <Hero f={f} />
          </AnimatePresence>
        </div>
      ) : view === 'loading' ? (
        <div className="flex h-full items-center justify-center" data-testid="app-builder-new">
          <Spinner className="text-muted-foreground" />
        </div>
      ) : (
        <Workspace f={f} />
      )}
    </LayoutGroup>
  )
}
