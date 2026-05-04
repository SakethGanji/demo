import { useState, useMemo, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Link2, Check, Brain, Send } from 'lucide-react'
import { ChatMessageList, ChatInput, ChatPanel, ChatPanelFooter } from '@/shared/components/chat'
import { useAppBuilderChatStore, type AppChatMessage, type ToolCallEntry } from '../stores/appBuilderChatStore'
import { useAppBuilderChat } from '../hooks/useAppBuilderChat'
import { apiTesterApi, appsApi, type ApiTestExecutionListItem } from '@/shared/lib/api'
import { Tool, ToolHeader, ToolContent, ToolInput, ToolOutput } from '@/components/ai-elements/tool'
import { Task, TaskTrigger, TaskContent, TaskItem, TaskItemFile } from '@/components/ai-elements/task'
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from '@/shared/components/ui/collapsible'
import type { ToolPart } from '@/components/ai-elements/tool'

// ── Helpers ──────────────────────────────────────────────────────────────────

const FRIENDLY_NAMES: Record<string, string> = {
  write_files: 'Write Files',
  read_files: 'Read Files',
  edit_files: 'Edit Files',
  delete_file: 'Delete File',
  list_files: 'List Files',
  search_files: 'Search Files',
  escalate: 'Escalate',
}

function friendlyName(tool: string): string {
  return FRIENDLY_NAMES[tool] ?? tool.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function toolStatusToState(status: ToolCallEntry['status']): ToolPart['state'] {
  switch (status) {
    case 'running': return 'input-available'
    case 'completed': return 'output-available'
    case 'error': return 'output-error'
  }
}

// ── Tool Call Rendering ──────────────────────────────────────────────────────

function renderToolCalls(msg: AppChatMessage) {
  if (!msg.toolCalls?.length) return null

  return (
    <div className="space-y-2">
      {msg.toolCalls.map((tc) => {
        if (tc.tool === 'search_files') {
          return (
            <Task key={tc.id} defaultOpen={false}>
              <TaskTrigger title={`Search: ${tc.args.pattern ?? '...'}`} />
              <TaskContent>
                {tc.result && (
                  <TaskItem>
                    <pre className="whitespace-pre-wrap text-xs text-muted-foreground font-mono">
                      {tc.result.slice(0, 500)}
                    </pre>
                  </TaskItem>
                )}
              </TaskContent>
            </Task>
          )
        }

        return (
          <Tool key={tc.id} defaultOpen={false}>
            <ToolHeader
              type="dynamic-tool"
              toolName={friendlyName(tc.tool)}
              state={toolStatusToState(tc.status)}
            />
            <ToolContent>
              <ToolInput input={tc.args} />
              {(tc.status === 'completed' || tc.status === 'error') && (
                <ToolOutput
                  output={tc.result ?? null}
                  errorText={tc.status === 'error' ? tc.result ?? 'Unknown error' : undefined}
                />
              )}
            </ToolContent>
          </Tool>
        )
      })}
    </div>
  )
}

// ── Reasoning Rendering ──────────────────────────────────────────────────────

function renderReasoning(msg: AppChatMessage, isStreaming: boolean) {
  if (!msg.thinking?.length) return null

  return (
    <Collapsible defaultOpen={isStreaming}>
      <CollapsibleTrigger className="group flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer mb-2">
        <Brain size={12} />
        <span>Reasoning</span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mb-3 space-y-1 border-l-2 border-muted pl-3">
          {msg.thinking.map((t, i) => (
            <p key={i} className="text-xs text-muted-foreground leading-relaxed">
              {t}
            </p>
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

// ── Extra Rendering (app updated badge with file list) ───────────────────────

function renderExtra(msg: AppChatMessage) {
  if (!msg.appPayload) return null

  const payload = msg.appPayload as { type?: string; files?: { path: string }[] }
  const files = payload?.files

  return (
    <div className="rounded-md border border-border/50 bg-muted/50 px-2.5 py-1.5 flex items-center gap-2 flex-wrap">
      <span className="text-xs text-muted-foreground">App updated</span>
      {files?.map((f) => (
        <TaskItemFile key={f.path}>
          {f.path.split('/').pop()}
        </TaskItemFile>
      ))}
    </div>
  )
}

// ── Chat Panel ───────────────────────────────────────────────────────────────

export function AppBuilderChatPanel({
  appId,
  onClose,
}: {
  appId?: string
  onClose?: () => void
}) {
  const messages = useAppBuilderChatStore((s) => s.messages)
  const [selectedExecutionIds, setSelectedExecutionIds] = useState<string[]>([])
  const [contextOpen, setContextOpen] = useState(false)

  // Track whether selectedExecutionIds has been initialized from the app row.
  // Without this, the empty-array initial render would PATCH and clobber any
  // existing allow-list before we've loaded it.
  const initializedRef = useRef(false)

  // Load existing allow-list when the app changes — so the popover shows the
  // saved selection, and so we don't overwrite it on first render.
  useEffect(() => {
    if (!appId) {
      initializedRef.current = true
      return
    }
    initializedRef.current = false
    let cancelled = false
    appsApi.get(appId).then((app) => {
      if (cancelled) return
      setSelectedExecutionIds(app.api_execution_ids ?? [])
      initializedRef.current = true
    }).catch(() => {
      if (cancelled) return
      initializedRef.current = true
    })
    return () => { cancelled = true }
  }, [appId])

  // Persist the allow-list whenever the selection changes — so the published
  // app's runtime gets the captured-URL → execution-id map without requiring
  // the user to send a chat message first.
  useEffect(() => {
    if (!appId || !initializedRef.current) return
    appsApi.update(appId, { api_execution_ids: selectedExecutionIds }).catch(() => {
      // Silent — the next chat send will retry via auto-grant.
    })
  }, [appId, selectedExecutionIds])

  const chatOptions = useMemo(
    () => ({ appId, apiExecutionIds: selectedExecutionIds }),
    [appId, selectedExecutionIds],
  )
  const { sendMessage, isStreaming, cancelStream } = useAppBuilderChat(chatOptions)

  const contextCount = selectedExecutionIds.length

  return (
    <ChatPanel
      onClose={onClose}
      actions={
        <div className="relative">
          <button
            onClick={() => setContextOpen(!contextOpen)}
            className={`relative p-1 rounded transition-colors ${
              contextCount > 0
                ? 'text-primary hover:bg-primary/10'
                : 'text-muted-foreground hover:text-foreground hover:bg-accent'
            }`}
            title="Attach saved API endpoint"
          >
            <Link2 size={14} />
            {contextCount > 0 && (
              <span className="absolute -top-0.5 -right-0.5 h-3.5 w-3.5 rounded-full bg-primary text-[9px] font-bold text-primary-foreground flex items-center justify-center">
                {contextCount}
              </span>
            )}
          </button>

          {contextOpen && (
            <EndpointPopover
              selectedIds={selectedExecutionIds}
              onChange={setSelectedExecutionIds}
              onClose={() => setContextOpen(false)}
            />
          )}
        </div>
      }
    >
      <ChatMessageList<AppChatMessage>
        messages={messages}
        isStreaming={isStreaming}
        emptyTitle="App Builder"
        emptyDescription="Describe the app you want to build and I'll generate it for you."
        filterMessage={(msg) =>
          !(msg.role === 'assistant' && !msg.content && !msg.appPayload && !msg.toolCalls?.length && !msg.thinking?.length)
        }
        renderReasoning={renderReasoning}
        renderToolCalls={renderToolCalls}
        renderAssistantContent={(msg) => (
          <div className="prose prose-sm prose-neutral dark:prose-invert max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0 [&_pre]:bg-background/50 [&_pre]:rounded [&_pre]:p-2 [&_pre]:text-xs [&_code]:text-xs [&_code]:bg-background/50 [&_code]:px-1 [&_code]:rounded [&_p]:my-1.5 [&_ul]:my-1.5 [&_ol]:my-1.5 [&_li]:my-0.5">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
          </div>
        )}
        renderExtra={renderExtra}
      />

      <ChatPanelFooter>
        <ChatInput
          onSend={sendMessage}
          isStreaming={isStreaming}
          onCancel={cancelStream}
          placeholder="Describe your app..."
        />
      </ChatPanelFooter>
    </ChatPanel>
  )
}

// ── Endpoint Popover ─────────────────────────────────────────────────────────
// Lists saved API tester executions. Selecting one attaches its captured
// request/response as context for the LLM.

function EndpointPopover({
  selectedIds,
  onChange,
  onClose,
}: {
  selectedIds: string[]
  onChange: (ids: string[]) => void
  onClose: () => void
}) {
  const [executions, setExecutions] = useState<ApiTestExecutionListItem[]>([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    apiTesterApi
      .list()
      .then((data) => {
        if (!cancelled) {
          setExecutions(data)
          setLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  const toggle = (id: string) => {
    onChange(
      selectedIds.includes(id)
        ? selectedIds.filter((s) => s !== id)
        : [...selectedIds, id],
    )
  }

  const filtered = search
    ? executions.filter(
        (e) =>
          (e.name || '').toLowerCase().includes(search.toLowerCase()) ||
          e.url.toLowerCase().includes(search.toLowerCase()),
      )
    : executions

  return (
    <div
      ref={containerRef}
      className="absolute left-0 top-full mt-1 w-80 rounded-lg border border-border bg-popover shadow-lg z-50"
    >
      <div className="px-2 py-1.5 border-b border-border/50 flex items-center justify-between">
        <span className="text-[11px] font-medium text-muted-foreground">Saved endpoints</span>
        <a
          href="/api-tester"
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
        >
          <Send size={10} />
          Open tester
        </a>
      </div>

      <div className="p-2">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search..."
          className="w-full h-7 rounded-md border border-border bg-background px-2 text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          autoFocus
        />
      </div>

      <div className="max-h-64 overflow-y-auto px-2 pb-2">
        {loading ? (
          <div className="py-3 text-center text-xs text-muted-foreground">Loading...</div>
        ) : filtered.length === 0 ? (
          <div className="py-3 text-center text-xs text-muted-foreground">
            {search
              ? 'No matches'
              : 'No saved endpoints. Use the API Tester to capture one first.'}
          </div>
        ) : (
          <div className="space-y-0.5">
            {filtered.map((ex) => {
              const isSelected = selectedIds.includes(ex.id)
              return (
                <button
                  key={ex.id}
                  onClick={() => toggle(ex.id)}
                  className={`w-full flex items-start gap-2 px-2 py-1.5 rounded-md text-xs text-left transition-colors ${
                    isSelected ? 'bg-primary/10 text-primary' : 'text-foreground hover:bg-accent'
                  }`}
                >
                  <div
                    className={`mt-0.5 h-3.5 w-3.5 rounded-sm border flex items-center justify-center shrink-0 ${
                      isSelected
                        ? 'border-primary bg-primary text-primary-foreground'
                        : 'border-border'
                    }`}
                  >
                    {isSelected && <Check size={10} />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono font-bold text-[10px]">{ex.method}</span>
                      <span className="truncate">{ex.name || ex.url}</span>
                    </div>
                    {ex.name && (
                      <div className="text-[10px] text-muted-foreground truncate">{ex.url}</div>
                    )}
                  </div>
                </button>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
