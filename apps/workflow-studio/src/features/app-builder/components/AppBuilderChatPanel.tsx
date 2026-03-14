import { useState, useMemo, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Link2, Check, Plus, Trash2 } from 'lucide-react'
import { ChatMessageList, ChatInput, ChatPanel, ChatPanelFooter } from '@/shared/components/chat'
import { useAppBuilderChatStore, type AppChatMessage } from '../stores/appBuilderChatStore'
import { useAppBuilderChat } from '../hooks/useAppBuilderChat'
import { workflowsApi } from '@/shared/lib/api'
import type { ApiWorkflowSummary } from '@/shared/lib/backendTypes'

export interface ApiEndpoint {
  curl: string
  response: string
}

type ContextMode = 'workflows' | 'api'

export function AppBuilderChatPanel({
  appId,
  onClose,
}: {
  appId?: string
  onClose?: () => void
}) {
  const messages = useAppBuilderChatStore((s) => s.messages)
  const [contextMode, setContextMode] = useState<ContextMode>('workflows')
  const [selectedWorkflowIds, setSelectedWorkflowIds] = useState<string[]>([])
  const [apiEndpoints, setApiEndpoints] = useState<ApiEndpoint[]>([])
  const [contextOpen, setContextOpen] = useState(false)

  const chatOptions = useMemo(
    () => ({ appId, workflowIds: selectedWorkflowIds }),
    [appId, selectedWorkflowIds],
  )
  const { sendMessage, isStreaming, cancelStream } = useAppBuilderChat(chatOptions)

  const contextCount = contextMode === 'workflows' ? selectedWorkflowIds.length : apiEndpoints.length

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
            title="Add context"
          >
            <Link2 size={14} />
            {contextCount > 0 && (
              <span className="absolute -top-0.5 -right-0.5 h-3.5 w-3.5 rounded-full bg-primary text-[9px] font-bold text-primary-foreground flex items-center justify-center">
                {contextCount}
              </span>
            )}
          </button>

          {contextOpen && (
            <ContextPopover
              mode={contextMode}
              onModeChange={(m) => {
                setContextMode(m)
                // Clear the other when switching
                if (m === 'workflows') setApiEndpoints([])
                else setSelectedWorkflowIds([])
              }}
              selectedWorkflowIds={selectedWorkflowIds}
              onWorkflowsChange={setSelectedWorkflowIds}
              apiEndpoints={apiEndpoints}
              onApiEndpointsChange={setApiEndpoints}
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
        filterMessage={(msg) => !(msg.role === 'assistant' && !msg.content && !msg.appPayload)}
        renderAssistantContent={(msg) => (
          <div className="prose prose-sm prose-neutral dark:prose-invert max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0 [&_pre]:bg-background/50 [&_pre]:rounded [&_pre]:p-2 [&_pre]:text-xs [&_code]:text-xs [&_code]:bg-background/50 [&_code]:px-1 [&_code]:rounded [&_p]:my-1.5 [&_ul]:my-1.5 [&_ol]:my-1.5 [&_li]:my-0.5">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
          </div>
        )}
        renderExtra={(msg) =>
          msg.appPayload ? (
            <div className="rounded-md border border-border/50 bg-muted/50 px-2.5 py-1.5 text-xs text-muted-foreground">
              App updated
            </div>
          ) : null
        }
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

// ── Context Popover ──────────────────────────────────────────────────────────
// Absolute popover anchored to the link icon. Either workflows OR api, not both.

function ContextPopover({
  mode,
  onModeChange,
  selectedWorkflowIds,
  onWorkflowsChange,
  apiEndpoints,
  onApiEndpointsChange,
  onClose,
}: {
  mode: ContextMode
  onModeChange: (mode: ContextMode) => void
  selectedWorkflowIds: string[]
  onWorkflowsChange: (ids: string[]) => void
  apiEndpoints: ApiEndpoint[]
  onApiEndpointsChange: (endpoints: ApiEndpoint[]) => void
  onClose: () => void
}) {
  const [workflows, setWorkflows] = useState<ApiWorkflowSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (mode !== 'workflows') return
    let cancelled = false
    setLoading(true)
    workflowsApi.list().then((data) => {
      if (!cancelled) { setWorkflows(data); setLoading(false) }
    }).catch(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [mode])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  const toggleWorkflow = (id: string) => {
    onWorkflowsChange(
      selectedWorkflowIds.includes(id)
        ? selectedWorkflowIds.filter((s) => s !== id)
        : [...selectedWorkflowIds, id],
    )
  }

  const addEndpoint = useCallback(() => {
    onApiEndpointsChange([...apiEndpoints, { curl: '', response: '' }])
  }, [apiEndpoints, onApiEndpointsChange])

  const updateEndpoint = useCallback((index: number, updates: Partial<ApiEndpoint>) => {
    const next = [...apiEndpoints]
    next[index] = { ...next[index], ...updates }
    onApiEndpointsChange(next)
  }, [apiEndpoints, onApiEndpointsChange])

  const removeEndpoint = useCallback((index: number) => {
    onApiEndpointsChange(apiEndpoints.filter((_, i) => i !== index))
  }, [apiEndpoints, onApiEndpointsChange])

  const filtered = search
    ? workflows.filter((w) => w.name.toLowerCase().includes(search.toLowerCase()))
    : workflows

  const modeBtn = (m: ContextMode, label: string) => (
    <button
      onClick={() => onModeChange(m)}
      className={`flex-1 py-1.5 text-[11px] font-medium rounded-md transition-colors ${
        mode === m
          ? 'bg-accent text-foreground'
          : 'text-muted-foreground hover:text-foreground'
      }`}
    >
      {label}
    </button>
  )

  return (
    <div
      ref={containerRef}
      className="absolute left-0 top-full mt-1 w-80 rounded-lg border border-border bg-popover shadow-lg z-50"
    >
      {/* Mode toggle */}
      <div className="flex gap-0.5 p-1.5 border-b border-border/50 bg-muted/30">
        {modeBtn('workflows', 'Workflows')}
        {modeBtn('api', 'API Schema')}
      </div>

      {/* Workflows mode */}
      {mode === 'workflows' && (
        <>
          <div className="p-2">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search workflows..."
              className="w-full h-7 rounded-md border border-border bg-background px-2 text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              autoFocus
            />
          </div>
          <div className="max-h-48 overflow-y-auto px-2 pb-2">
            {loading ? (
              <div className="py-3 text-center text-xs text-muted-foreground">Loading...</div>
            ) : filtered.length === 0 ? (
              <div className="py-3 text-center text-xs text-muted-foreground">
                {search ? 'No matches' : 'No workflows found'}
              </div>
            ) : (
              <div className="space-y-0.5">
                {filtered.map((wf) => {
                  const isSelected = selectedWorkflowIds.includes(wf.id)
                  return (
                    <button
                      key={wf.id}
                      onClick={() => toggleWorkflow(wf.id)}
                      className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-xs text-left transition-colors ${
                        isSelected ? 'bg-primary/10 text-primary' : 'text-foreground hover:bg-accent'
                      }`}
                    >
                      <div className={`h-3.5 w-3.5 rounded-sm border flex items-center justify-center shrink-0 ${
                        isSelected ? 'border-primary bg-primary text-primary-foreground' : 'border-border'
                      }`}>
                        {isSelected && <Check size={10} />}
                      </div>
                      <span className="truncate flex-1">{wf.name}</span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        </>
      )}

      {/* API Schema mode */}
      {mode === 'api' && (
        <div className="p-2 space-y-2">
          {apiEndpoints.map((ep, i) => (
            <div key={i} className="rounded-md border border-border p-2 space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">curl</span>
                <button
                  onClick={() => removeEndpoint(i)}
                  className="h-5 w-5 flex items-center justify-center text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded transition-colors"
                >
                  <Trash2 size={10} />
                </button>
              </div>
              <textarea
                value={ep.curl}
                onChange={(e) => updateEndpoint(i, { curl: e.target.value })}
                placeholder={'curl -X POST https://api.example.com/users \\\n  -H "Content-Type: application/json" \\\n  -d \'{"name": "..."}\''}
                rows={3}
                className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-[11px] font-mono placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring resize-none"
              />
              <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">Response</span>
              <textarea
                value={ep.response}
                onChange={(e) => updateEndpoint(i, { response: e.target.value })}
                placeholder={'{"id": 1, "name": "...", "email": "..."}'}
                rows={3}
                className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-[11px] font-mono placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring resize-none"
              />
            </div>
          ))}
          <button
            onClick={addEndpoint}
            className="w-full h-7 flex items-center justify-center gap-1 rounded-md border border-dashed border-border text-xs text-muted-foreground hover:text-foreground hover:border-border hover:bg-accent transition-colors"
          >
            <Plus size={11} />
            Add endpoint
          </button>
          {apiEndpoints.length === 0 && (
            <p className="text-[10px] text-muted-foreground text-center py-1">
              Paste a curl command and its response so the AI knows how to wire up your app.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
