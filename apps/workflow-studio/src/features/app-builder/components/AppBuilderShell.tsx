import { useEffect, useState, useCallback, useRef } from 'react'
import { Link } from '@tanstack/react-router'
import {
  ChevronLeft,
  Save,
  Loader2,
  Sparkles,
  ScrollText,
  History,
  MessageSquare,
  Globe,
  Send,
} from 'lucide-react'
import { toast } from 'sonner'
import { ToolbarSeparator } from '@/shared/components/ui/toolbar'
import { useAppDocumentStore } from '../stores'
import { useAppBuilderChatStore } from '../stores/appBuilderChatStore'
import { AppPreviewPanel } from './AppPreviewPanel'
import { AppBuilderChatPanel } from './AppBuilderChatPanel'
import { ApiTesterPanel, SavedQueriesPanel } from './ApiTesterPanel'
import { BottomPanel } from '../panels'
import { appsApi, type ApiAppDetail } from '@/shared/lib/api'
import { backends } from '@/shared/lib/config'
import { VersionHistory } from './VersionHistory'
import { PublishDialog } from './PublishDialog'

/**
 * Build the absolute public URL for a published app. Uses the same engine
 * origin the rest of the studio talks to (`backends.workflow`) so dev and
 * deployed environments both work without extra config. Falls back to the
 * current document origin so a misconfigured backends.workflow doesn't
 * produce a broken `/a/...` link.
 */
function buildPublicUrl(slug: string): string {
  const base = backends.workflow || window.location.origin
  return `${base.replace(/\/+$/, '')}/a/${slug}`
}

const btnClass =
  'h-8 w-8 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors disabled:opacity-40 disabled:cursor-not-allowed'

const floatingPanel = 'bg-card rounded-xl shadow-lg border border-border overflow-hidden'
const panelTransition = 'transition-all duration-300 ease-out'

/**
 * App builder shell — LLM-driven editor layout:
 *   - Center: live app preview (iframe sandbox) — full viewport
 *   - Right: AI Chat panel (floating)
 *   - Bottom: Console (floating)
 */
export function AppBuilderShell({ appId }: { appId?: string }) {
  const files = useAppDocumentStore((s) => s.files)
  const sourceCode = useAppDocumentStore((s) => s.sourceCode)
  const setSourceCode = useAppDocumentStore((s) => s.setSourceCode)
  const setCurrentVersion = useAppDocumentStore((s) => s.setCurrentVersion)
  const currentVersion = useAppDocumentStore((s) => s.currentVersion)
  const [appName, setAppName] = useState('Untitled App')
  const [isEditingName, setIsEditingName] = useState(false)
  const [editedName, setEditedName] = useState('')
  const nameInputRef = useRef<HTMLInputElement>(null)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const [bottomPanelOpen, setBottomPanelOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [chatOpen, setChatOpen] = useState(true)
  const [testerOpen, setTesterOpen] = useState(false)
  const [publishOpen, setPublishOpen] = useState(false)
  const [appDetail, setAppDetail] = useState<ApiAppDetail | null>(null)

  // Auto-focus name input when editing
  useEffect(() => {
    if (isEditingName && nameInputRef.current) {
      nameInputRef.current.focus()
      nameInputRef.current.select()
    }
  }, [isEditingName])

  // Submit name change
  const handleNameSubmit = useCallback(() => {
    const trimmed = editedName.trim()
    if (trimmed && trimmed !== appName) {
      setAppName(trimmed)
      if (appId) {
        appsApi.update(appId, { name: trimmed }).catch(() => {
          // revert on failure
          setAppName(appName)
        })
      }
    } else {
      setEditedName(appName)
    }
    setIsEditingName(false)
  }, [editedName, appName, appId])

  // Reset stores + load app whenever appId changes
  useEffect(() => {
    useAppDocumentStore.getState().reset()
    useAppBuilderChatStore.getState().clearHistory()
    setLoaded(false)
    setError(null)

    if (!appId) {
      setLoaded(true)
      return
    }

    let cancelled = false
    appsApi.get(appId).then((data) => {
      if (cancelled) return
      setAppName(data.name)
      setAppDetail(data)
      const setFiles = useAppDocumentStore.getState().setFiles
      if (data.files && data.files.length > 0) {
        setFiles(data.files)
      } else if (data.source_code) {
        setSourceCode(data.source_code)
      }
      if (data.current_version) {
        setCurrentVersion(data.current_version)
      }
      setLoaded(true)
    }).catch((err) => {
      if (cancelled) return
      setError(err instanceof Error ? err.message : 'Failed to load app')
      setLoaded(true)
    })
    return () => { cancelled = true }
  }, [appId, setSourceCode, setCurrentVersion])

  // Manual save — creates a version with trigger='manual'
  const handleSave = useCallback(async () => {
    if (!appId || isSaving) return
    setIsSaving(true)
    try {
      const currentFiles = useAppDocumentStore.getState().files
      const result = await appsApi.update(appId, {
        source_code: sourceCode ?? '',
        files: currentFiles ?? undefined,
        create_version: true,
        version_trigger: 'manual',
      })
      if (result.current_version) {
        setCurrentVersion(result.current_version)
      }
      toast.success('App saved')
    } catch (err) {
      toast.error('Failed to save', {
        description: err instanceof Error ? err.message : 'Unknown error',
      })
    } finally {
      setIsSaving(false)
    }
  }, [appId, isSaving, sourceCode, setCurrentVersion])

  // Handle version revert from history panel
  const handleRevert = useCallback(async (versionId: number) => {
    if (!appId) return
    try {
      const result = await appsApi.revertToVersion(appId, versionId)
      const setFiles = useAppDocumentStore.getState().setFiles
      if (result.files && result.files.length > 0) {
        setFiles(result.files)
      } else if (result.source_code) {
        setSourceCode(result.source_code)
      }
      if (result.current_version) {
        setCurrentVersion(result.current_version)
      }
      toast.success(`Reverted to v${result.current_version?.version_number ?? '?'}`)
    } catch (err) {
      toast.error('Failed to revert', {
        description: err instanceof Error ? err.message : 'Unknown error',
      })
    }
  }, [appId, setSourceCode, setCurrentVersion])

  // ── Canvas content ──
  const renderCanvas = () => {
    if (error) {
      return (
        <div className="relative h-full w-full text-foreground flex items-center justify-center">
          <div className="text-center space-y-2">
            <p className="text-destructive font-medium">Failed to load app</p>
            <p className="text-sm text-muted-foreground">{error}</p>
          </div>
        </div>
      )
    }

    if (!loaded) {
      return (
        <div className="h-full w-full text-foreground flex items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      )
    }

    if (!sourceCode) {
      return (
        <div
          className="relative h-full w-full text-foreground flex items-center justify-center"
        >
          <div className="text-center space-y-3 px-6" style={{ maxWidth: 280 }}>
            <div className="w-12 h-12 rounded-full bg-muted flex items-center justify-center mx-auto">
              <Sparkles size={24} className="text-muted-foreground" />
            </div>
            <p className="text-sm font-medium text-foreground">No app yet</p>
            <p className="text-xs text-muted-foreground leading-relaxed">
              Describe what you want to build in the AI chat and it will generate your app here.
            </p>
          </div>
        </div>
      )
    }

    return (
      <AppPreviewPanel files={files} />
    )
  }

  // CSS expressions for responsive panel widths
  const chatWidth = 'min(384px, 45vw)'
  const historyWidth = 'min(220px, 20vw)'
  const savedWidth = 'min(260px, 24vw)'

  // Build bottom panel right offset using CSS calc so it adapts to viewport
  let bottomRight = '12px'
  if (chatOpen && historyOpen && appId) {
    bottomRight = `calc(${chatWidth} + ${historyWidth} + 48px)` // 3 gaps + base
  } else if (chatOpen) {
    bottomRight = `calc(${chatWidth} + 24px)`
  } else if (historyOpen && appId) {
    bottomRight = `calc(${historyWidth} + 24px)`
  }

  return (
    <div className="h-full w-full relative bg-muted dark:bg-black/60">
      {/* Layer 0: Preview area — fills left of chat, below navbar */}
      <div
        className={[
          'absolute top-[68px] left-3 bottom-3 z-0',
          floatingPanel,
          panelTransition,
        ].join(' ')}
        style={{ right: chatOpen ? `calc(${chatWidth} + 24px)` : '12px' }}
      >
        {renderCanvas()}
      </div>

      {/* Layer 2: Floating navbar */}
      <div className="absolute top-3 left-3 right-3 z-20">
        <div className={`flex items-center h-11 px-3 ${floatingPanel} gap-1`}>
          <Link
            to="/projects"
            className="h-8 px-1.5 flex items-center gap-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors text-xs"
            title="Back to projects"
          >
            <ChevronLeft size={14} />
            <span className="hidden sm:inline">Projects</span>
          </Link>

          <ToolbarSeparator />

          {/* App name — click to rename */}
          {isEditingName ? (
            <input
              ref={nameInputRef}
              type="text"
              value={editedName}
              onChange={(e) => setEditedName(e.target.value)}
              onBlur={handleNameSubmit}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleNameSubmit()
                if (e.key === 'Escape') {
                  setEditedName(appName)
                  setIsEditingName(false)
                }
              }}
              className="h-8 w-36 px-2 text-[13px] font-medium text-foreground bg-transparent outline-none border border-border rounded-md"
            />
          ) : (
            <button
              onClick={() => {
                setEditedName(appName)
                setIsEditingName(true)
              }}
              className="h-8 px-2 text-[13px] font-medium text-foreground hover:bg-accent transition-colors rounded-md truncate max-w-40"
              title="Click to rename"
            >
              {appName}
            </button>
          )}

          {/* Version indicator — distinguishes the version being edited from
              the version live at /a/{slug}. Collapses to a single label when
              they're the same. */}
          {currentVersion && (() => {
            const liveVersion = appDetail?.published_version
            const liveDiffers = liveVersion && liveVersion.id !== currentVersion.id
            return (
              <span className="ml-2 text-[11px] text-muted-foreground bg-muted px-1.5 py-0.5 rounded font-mono whitespace-nowrap">
                {liveDiffers ? (
                  <>editing v{currentVersion.version_number} · live v{liveVersion.version_number}</>
                ) : (
                  <>v{currentVersion.version_number}</>
                )}
              </span>
            )
          })()}

          {/* Live-URL pill — persistent affordance for the public link. Only
              shown when the app is currently published with a slug. */}
          {appDetail?.active && appDetail.slug && (
            <a
              href={buildPublicUrl(appDetail.slug)}
              target="_blank"
              rel="noreferrer noopener"
              className="ml-1.5 inline-flex items-center gap-1 h-6 px-2 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 text-[11px] font-medium hover:bg-emerald-500/15 transition-colors max-w-[180px]"
              title={`Open ${buildPublicUrl(appDetail.slug)}`}
            >
              <Globe size={10} className="shrink-0" />
              <span className="truncate font-mono">/a/{appDetail.slug}</span>
            </a>
          )}

          <div className="flex-1" />

          {/* Version history toggle */}
          {appId && (
            <button
              onClick={() => setHistoryOpen(!historyOpen)}
              className={btnClass + (historyOpen ? ' !text-primary' : '')}
              title="Version history"
            >
              <History size={14} />
            </button>
          )}

          {/* API Tester toggle */}
          <button
            onClick={() => setTesterOpen(!testerOpen)}
            className={btnClass + (testerOpen ? ' !text-primary' : '')}
            title="API Tester"
          >
            <Send size={14} />
          </button>

          {/* AI Chat toggle */}
          <button
            onClick={() => setChatOpen(!chatOpen)}
            className={btnClass + (chatOpen ? ' !text-primary' : '')}
            title="Toggle AI chat"
          >
            <MessageSquare size={14} />
          </button>

          {/* Console toggle */}
          <button
            onClick={() => setBottomPanelOpen(!bottomPanelOpen)}
            className={btnClass + (bottomPanelOpen ? ' !text-primary' : '')}
            title="Toggle console"
          >
            <ScrollText size={14} />
          </button>

          <ToolbarSeparator />

          {/* Save */}
          {appId && (
            <button
              onClick={handleSave}
              disabled={isSaving}
              className={btnClass}
              title="Save (Ctrl+S)"
            >
              {isSaving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            </button>
          )}

          {/* Publish */}
          {appId && appDetail && sourceCode && (
            <button
              onClick={() => setPublishOpen(true)}
              className="ml-1 h-8 px-2.5 flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 transition-colors"
              title="Publish app"
            >
              <Globe size={12} />
              {appDetail.active ? 'Republish' : 'Publish'}
            </button>
          )}
        </div>
      </div>

      {/* Publish dialog */}
      {appId && appDetail && publishOpen && (
        <PublishDialog
          appId={appId}
          app={appDetail}
          onClose={() => setPublishOpen(false)}
          onPublished={(updated) => setAppDetail(updated)}
        />
      )}

      {/* Layer 1: Floating AI Chat panel (right) */}
      <div
        className={[
          'absolute top-[68px] right-3 bottom-3 z-10',
          floatingPanel,
          panelTransition,
          chatOpen ? 'translate-x-0 opacity-100' : 'translate-x-[calc(100%+12px)] opacity-0 pointer-events-none',
        ].join(' ')}
        style={{ width: chatWidth }}
      >
        <AppBuilderChatPanel appId={appId} onClose={() => setChatOpen(false)} />
      </div>

      {/* Layer 1: Floating Version History panel */}
      {appId && (
        <div
          className={[
            'absolute top-[68px] bottom-3 z-10',
            floatingPanel,
            panelTransition,
            historyOpen ? 'translate-x-0 opacity-100' : 'translate-x-[calc(100%+12px)] opacity-0 pointer-events-none',
          ].join(' ')}
          style={{
            width: historyWidth,
            right: chatOpen ? `calc(${chatWidth} + 24px)` : 12,
          }}
        >
          <VersionHistory
            appId={appId}
            currentVersionId={currentVersion?.id ?? null}
            publishedVersionId={appDetail?.published_version?.id ?? null}
            onRevert={handleRevert}
            onClose={() => setHistoryOpen(false)}
          />
        </div>
      )}

      {/* Layer 1: Saved queries — docked rail on the left, flush against
          the navbar/viewport edges. Border on the right marks the seam. */}
      <div
        className={[
          'absolute top-[68px] left-0 bottom-0 z-10',
          'bg-card border-r border-border overflow-hidden',
          panelTransition,
          testerOpen ? 'translate-x-0 opacity-100' : '-translate-x-full opacity-0 pointer-events-none',
        ].join(' ')}
        style={{ width: savedWidth }}
      >
        {testerOpen && <SavedQueriesPanel onClose={() => setTesterOpen(false)} />}
      </div>

      {/* Layer 1: API Tester — docked, flat. Sits to the right of the saved
          rail and left of the chat panel (when open). */}
      <div
        className={[
          'absolute top-[68px] bottom-0 z-10',
          'bg-card border-l border-border overflow-hidden',
          panelTransition,
          testerOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none',
        ].join(' ')}
        style={{
          left: savedWidth,
          right: chatOpen ? `calc(${chatWidth} + 24px)` : '0',
        }}
      >
        {testerOpen && <ApiTesterPanel onClose={() => setTesterOpen(false)} />}
      </div>

      {/* Layer 1: Floating bottom panel (console) */}
      <div
        className={[
          'absolute left-3 bottom-3 z-10 h-[min(240px,40vh)]',
          floatingPanel,
          panelTransition,
          bottomPanelOpen ? 'translate-y-0 opacity-100' : 'translate-y-[calc(100%+12px)] opacity-0 pointer-events-none',
        ].join(' ')}
        style={{ right: bottomRight }}
      >
        <BottomPanel />
      </div>
    </div>
  )
}
