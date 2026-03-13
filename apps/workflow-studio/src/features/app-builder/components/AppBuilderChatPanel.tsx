import { useState, useRef, useCallback, useEffect, useMemo, memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Send, Square, Bot, User, Sparkles, Link2, X, Check, ChevronsUpDown } from 'lucide-react'
import { useAppBuilderChatStore, type AppChatMessage } from '../stores/appBuilderChatStore'
import { useAppBuilderChat } from '../hooks/useAppBuilderChat'
import { workflowsApi } from '@/shared/lib/api'
import type { ApiWorkflowSummary } from '@/shared/lib/backendTypes'

export function AppBuilderChatPanel({ appId }: { appId?: string }) {
  const messages = useAppBuilderChatStore((s) => s.messages)
  const [selectedWorkflowIds, setSelectedWorkflowIds] = useState<string[]>([])

  const chatOptions = useMemo(
    () => ({ appId, workflowIds: selectedWorkflowIds }),
    [appId, selectedWorkflowIds],
  )
  const { sendMessage, isStreaming, cancelStream } = useAppBuilderChat(chatOptions)

  return (
    <div className="h-full flex flex-col overflow-hidden bg-card">
      {/* Header */}
      <div className="flex flex-col border-b border-border shrink-0">
        <div className="flex items-center gap-2 px-4 py-3">
          <Sparkles size={16} className="text-primary" />
          <span className="text-sm font-medium flex-1">AI App Builder</span>
          <WorkflowPicker
            selected={selectedWorkflowIds}
            onChange={setSelectedWorkflowIds}
          />
        </div>
      </div>

      {/* Messages */}
      <MessageList messages={messages} isStreaming={isStreaming} />

      {/* Input */}
      <div className="p-3 border-t border-border shrink-0">
        <ChatInput
          onSend={sendMessage}
          isStreaming={isStreaming}
          onCancel={cancelStream}
        />
      </div>
    </div>
  )
}

// ── Message List ──────────────────────────────────────────────────────────────

function MessageList({
  messages,
  isStreaming,
}: {
  messages: AppChatMessage[]
  isStreaming: boolean
}) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, messages[messages.length - 1]?.content])

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="text-center text-muted-foreground max-w-[220px]">
          <Bot className="h-10 w-10 mx-auto mb-3 opacity-50" />
          <p className="text-sm font-medium">App Builder</p>
          <p className="text-xs mt-1">
            Describe the app you want to build and I'll generate it for you.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto p-3 space-y-3">
      {messages.map((msg) => (
        msg.role === 'assistant' && !msg.content && !msg.appPayload ? null : (
          <MessageBubble key={msg.id} message={msg} />
        )
      ))}
      {isStreaming && (
        <div className="flex gap-2 pl-8">
          <div className="bg-muted rounded-lg px-3 py-2 flex items-center gap-1.5 h-8">
            <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-pulse" />
            <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-pulse [animation-delay:300ms]" />
            <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-pulse [animation-delay:600ms]" />
          </div>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  )
}

const MessageBubble = memo(function MessageBubble({
  message,
}: {
  message: AppChatMessage
}) {
  const isUser = message.role === 'user'

  return (
    <div className={`flex gap-2 ${isUser ? 'flex-row-reverse' : ''}`}>
      <div
        className={`h-6 w-6 rounded-full flex items-center justify-center shrink-0 ${
          isUser
            ? 'bg-primary text-primary-foreground'
            : 'bg-muted text-muted-foreground'
        }`}
      >
        {isUser ? <User size={12} /> : <Bot size={12} />}
      </div>

      <div className={`flex flex-col gap-1 max-w-[85%] min-w-0 ${isUser ? 'items-end' : ''}`}>
        {/* Text bubble — hide when assistant has no real text (code-only response) */}
        {(isUser || message.content.trim()) && (
          <div
            className={`rounded-lg px-3 py-2 text-sm break-words ${
              isUser
                ? 'bg-primary text-primary-foreground whitespace-pre-wrap'
                : 'bg-muted text-foreground'
            }`}
          >
            {isUser ? (
              message.content
            ) : (
              <div className="prose prose-sm prose-neutral dark:prose-invert max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0 [&_pre]:bg-background/50 [&_pre]:rounded [&_pre]:p-2 [&_pre]:text-xs [&_code]:text-xs [&_code]:bg-background/50 [&_code]:px-1 [&_code]:rounded [&_p]:my-1.5 [&_ul]:my-1.5 [&_ol]:my-1.5 [&_li]:my-0.5">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
              </div>
            )}
          </div>
        )}

        {message.appPayload && (
          <div className="rounded-md border border-border bg-card px-2.5 py-1.5 text-xs text-muted-foreground">
            App updated
          </div>
        )}
      </div>
    </div>
  )
})

// ── Chat Input ────────────────────────────────────────────────────────────────

function ChatInput({
  onSend,
  isStreaming,
  onCancel,
}: {
  onSend: (message: string) => void
  isStreaming: boolean
  onCancel: () => void
}) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleSend = useCallback(() => {
    const trimmed = value.trim()
    if (!trimmed || isStreaming) return
    onSend(trimmed)
    setValue('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }, [value, isStreaming, onSend])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleInput = () => {
    const textarea = textareaRef.current
    if (textarea) {
      textarea.style.height = 'auto'
      textarea.style.height = Math.min(textarea.scrollHeight, 150) + 'px'
    }
  }

  return (
    <div className="flex gap-2 items-end">
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        onInput={handleInput}
        placeholder="Describe your app..."
        rows={1}
        className="flex-1 resize-none rounded-lg border border-border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
      />
      {isStreaming ? (
        <button
          onClick={onCancel}
          className="h-9 w-9 flex items-center justify-center rounded-lg bg-destructive text-destructive-foreground hover:bg-destructive/90 transition-colors shrink-0"
          title="Stop"
        >
          <Square size={14} fill="currentColor" />
        </button>
      ) : (
        <button
          onClick={handleSend}
          disabled={!value.trim()}
          className="h-9 w-9 flex items-center justify-center rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-40 shrink-0"
          title="Send (Ctrl+Enter)"
        >
          <Send size={14} />
        </button>
      )}
    </div>
  )
}

// ── Workflow Picker ──────────────────────────────────────────────────────────

function WorkflowPicker({
  selected,
  onChange,
}: {
  selected: string[]
  onChange: (ids: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const [workflows, setWorkflows] = useState<ApiWorkflowSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)

  // Load workflows when dropdown opens
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    workflowsApi.list().then((data) => {
      if (!cancelled) {
        setWorkflows(data)
        setLoading(false)
      }
    }).catch(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [open])

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const toggle = (id: string) => {
    onChange(
      selected.includes(id)
        ? selected.filter((s) => s !== id)
        : [...selected, id],
    )
  }

  const filtered = search
    ? workflows.filter((w) => w.name.toLowerCase().includes(search.toLowerCase()))
    : workflows

  const selectedNames = workflows.filter((w) => selected.includes(w.id)).map((w) => w.name)

  return (
    <div ref={containerRef} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={`h-7 px-2 flex items-center gap-1.5 rounded-md text-xs transition-colors border ${
          selected.length > 0
            ? 'border-primary/30 bg-primary/10 text-primary hover:bg-primary/15'
            : 'border-border text-muted-foreground hover:text-foreground hover:bg-accent'
        }`}
        title={selected.length > 0 ? `Linked: ${selectedNames.join(', ')}` : 'Link workflows'}
      >
        <Link2 size={12} />
        {selected.length > 0 ? (
          <span className="max-w-[80px] truncate">{selected.length} workflow{selected.length > 1 ? 's' : ''}</span>
        ) : (
          <>
            <span>Workflows</span>
            <ChevronsUpDown size={10} />
          </>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 w-64 rounded-lg border border-border bg-popover shadow-lg z-50">
          <div className="p-2 border-b border-border">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search workflows..."
              className="w-full h-7 rounded-md border border-border bg-background px-2 text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              autoFocus
            />
          </div>
          <div className="max-h-48 overflow-y-auto p-1">
            {loading ? (
              <div className="py-4 text-center text-xs text-muted-foreground">Loading...</div>
            ) : filtered.length === 0 ? (
              <div className="py-4 text-center text-xs text-muted-foreground">
                {search ? 'No matches' : 'No workflows found'}
              </div>
            ) : (
              filtered.map((wf) => {
                const isSelected = selected.includes(wf.id)
                return (
                  <button
                    key={wf.id}
                    onClick={() => toggle(wf.id)}
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
              })
            )}
          </div>
          {selected.length > 0 && (
            <div className="p-2 border-t border-border">
              <button
                onClick={() => { onChange([]); setOpen(false) }}
                className="w-full h-7 rounded-md text-xs text-muted-foreground hover:text-foreground hover:bg-accent flex items-center justify-center gap-1 transition-colors"
              >
                <X size={10} />
                Clear selection
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
