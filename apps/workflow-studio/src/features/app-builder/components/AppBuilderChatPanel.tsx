/**
 * "Build with AI" (UI-DESIGN §5.2): chat with tool rows (Wrote files +N, Uses ops, Declared
 * METHOD /path stub), a composer with the route-picker chip, and the stream/save error state
 * (`builder-stream-error`) with Retry.
 */
import { useEffect, useRef, useState, forwardRef, useImperativeHandle, type ReactNode } from 'react'
import {
  AlertTriangle,
  ArrowUp,
  Brain,
  Check,
  FilePlus2,
  Paperclip,
  Plug,
  PlugZap,
  RotateCw,
  Send,
  Sparkles,
  Square,
  SquarePen,
  Wrench,
} from 'lucide-react'
import { useAppBuilderChatStore, type AppChatMessage } from '../stores/appBuilderChatStore'
import { PlanRevisionCard } from '@/features/plan/PlanRevisionCard'
import { usePreviewSession } from '../stores/previewSessionStore'
import { apiTesterApi, type ApiTestExecutionListItem } from '@/shared/lib/api'
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from '@/shared/components/ui/collapsible'
import { Button } from '@/shared/components/ui/button'
import { Badge } from '@/shared/components/ui/badge'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { Input } from '@/shared/components/ui/input'
import { Callout, IconTile } from '@/shared/components/ui/surface'
import { Message, MessageContent, MessageResponse } from '@/shared/components/ai-elements/message'
import { Shimmer } from '@/shared/components/ai-elements/shimmer'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/components/ui/tooltip'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import type { Route } from '@/shared/api/types'
import { RoutePicker } from './RoutePicker'

const TOOL_LABEL: Record<string, string> = {
  write_files: 'Writing',
  edit_files: 'Editing',
  read_files: 'Reading',
  delete_file: 'Deleting',
  list_files: 'Listing files',
  search_files: 'Searching',
  declare_endpoint: 'Declaring an endpoint',
}

function toolTarget(args: Record<string, unknown>): string {
  const files = (args.files ?? args.paths ?? args.edits) as unknown
  if (Array.isArray(files)) {
    const names = files
      .map((f) => (typeof f === 'string' ? f : (f as { path?: string })?.path))
      .filter(Boolean)
      .map((p) => String(p).split('/').pop())
    if (names.length) return [...new Set(names)].join(', ')
  }
  if (typeof args.path === 'string') return args.path
  if (typeof args.pattern === 'string') return args.pattern
  if (typeof args.method === 'string') return `${args.method} ${args.path ?? ''}`
  return ''
}

function activityLabel(msg: AppChatMessage): string {
  const running = [...(msg.toolCalls ?? [])].reverse().find((t) => t.status === 'running')
  if (running) {
    const target = toolTarget(running.args)
    return `${TOOL_LABEL[running.tool] ?? running.tool.replace(/_/g, ' ')}${target ? ` ${target}` : ''}…`
  }
  return msg.phase || 'Thinking…'
}

function ToolRow({ icon, children, meta, testId }: { icon: React.ReactNode; children: React.ReactNode; meta?: React.ReactNode; testId?: string }) {
  return (
    <div
      className="flex h-[30px] items-center gap-2 rounded-ctl bg-recess px-2.5 text-xs [&>svg]:size-3.5 [&>svg]:shrink-0 [&>svg]:text-muted-foreground"
      data-testid={testId}
    >
      {icon}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {meta && <span className="shrink-0 text-muted-foreground">{meta}</span>}
    </div>
  )
}

const mono = 'font-mono text-[11.5px]'

function AssistantMessage({
  msg,
  streaming,
  onRetry,
  onSend,
}: {
  msg: AppChatMessage
  streaming: boolean
  onRetry: () => void
  /** sends the ask again once a plan revision it needed is approved */
  onSend?: (t: string) => void
}) {
  const otherTools = (msg.toolCalls ?? []).filter((t) => !['write_files', 'edit_files', 'declare_endpoint'].includes(t.tool))
  return (
    <Message from="assistant" className="max-w-full" data-testid="chat-assistant-message">
      <MessageContent className="w-full">
        {!!msg.thinking?.length && (
          <Collapsible>
            <CollapsibleTrigger className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
              <Brain className="size-3" /> Reasoning
            </CollapsibleTrigger>
            <CollapsibleContent>
              <div className="mt-1.5 space-y-1 pl-3 text-xs shadow-[inset_2px_0_0_var(--hairline)] leading-relaxed text-muted-foreground">
                {msg.thinking.map((t, i) => (
                  <p key={i}>{t}</p>
                ))}
              </div>
            </CollapsibleContent>
          </Collapsible>
        )}
        {msg.wrote && msg.wrote.paths.length > 0 && (
          <ToolRow icon={<FilePlus2 />} meta={`+${msg.wrote.added}`} testId="chat-tool-wrote">
            Wrote{' '}
            {msg.wrote.paths.map((p, i) => (
              <span key={p}>
                {i > 0 && ', '}
                <span className={mono}>{p.split('/').pop()}</span>
              </span>
            ))}
          </ToolRow>
        )}
        {msg.uses && msg.uses.length > 0 && (
          <ToolRow icon={<Plug />} testId="chat-tool-uses">
            Uses{' '}
            {msg.uses.map((op, i) => (
              <span key={op}>
                {i > 0 && ', '}
                <span className={mono}>{op}</span>
              </span>
            ))}
          </ToolRow>
        )}
        {msg.declared?.map((d, i) => (
          <ToolRow
            key={i}
            icon={<PlugZap />}
            testId="chat-tool-declared"
            meta={d.status === 'created' ? <Badge variant="stub">stub</Badge> : d.status === 'existing' ? 'exists' : d.error ? 'failed' : null}
          >
            Declared <MethodBadge method={d.method} size="sm" className="mx-0.5 align-middle" /> <span className={mono}>{d.path}</span>
          </ToolRow>
        ))}
        {otherTools.length > 0 && !streaming && (
          <Collapsible>
            <CollapsibleTrigger className="flex items-center gap-1.5 text-[11.5px] text-muted-foreground hover:text-foreground">
              <Wrench className="size-3" /> {otherTools.length} more {otherTools.length === 1 ? 'step' : 'steps'}
            </CollapsibleTrigger>
            <CollapsibleContent>
              <div className="mt-1.5 flex flex-col gap-1">
                {otherTools.map((t) => (
                  <div key={t.id} className="flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
                    {t.status === 'error' ? <AlertTriangle className="size-3 text-destructive" /> : <Check className="size-3" />}
                    <span className="truncate">
                      {TOOL_LABEL[t.tool] ?? t.tool} {toolTarget(t.args)}
                    </span>
                  </div>
                ))}
              </div>
            </CollapsibleContent>
          </Collapsible>
        )}
        {msg.content.trim() && (
          <MessageResponse className="text-[13px] leading-[19px]" isAnimating={streaming}>
            {msg.content}
          </MessageResponse>
        )}
        {msg.planRevision ? <PlanRevisionCard revision={msg.planRevision} prompt={msg.prompt} onContinue={onSend} /> : null}
        {streaming && (
          <div className="flex items-center gap-2 text-muted-foreground" data-testid="chat-activity">
            <Sparkles className="size-3 shrink-0 text-ai" />
            <Shimmer className="truncate">{activityLabel(msg)}</Shimmer>
          </div>
        )}
        {msg.error && (
          <Callout
            tone="error"
            className="items-start text-xs"
            role="alert"
            data-testid="builder-stream-error"
            data-error-kind={msg.error.kind}
            icon={<AlertTriangle className="mt-0.5" />}
          >
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <div className="font-medium text-foreground">
                  {msg.error.kind === 'save' ? "This version wasn't saved" : 'The assistant stopped unexpectedly'}
                </div>
                <div className="mt-0.5 break-words text-muted-foreground">{msg.error.message}</div>
              </div>
              <Button variant="outline" size="xs" onClick={onRetry} data-testid="builder-stream-retry">
                <RotateCw /> Retry
              </Button>
            </div>
          </Callout>
        )}
      </MessageContent>
    </Message>
  )
}

// ── Saved API attachments (the existing API tester integration) ─────────────

function AttachApiPopover({ selected, onChange }: { selected: string[]; onChange: (ids: string[]) => void }) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<ApiTestExecutionListItem[] | null>(null)
  const [search, setSearch] = useState('')
  useEffect(() => {
    if (!open) return
    let cancelled = false
    apiTesterApi
      .list()
      .then((d) => !cancelled && setItems(d))
      .catch(() => !cancelled && setItems([]))
    return () => {
      cancelled = true
    }
  }, [open])
  const q = search.toLowerCase()
  const filtered = (items ?? []).filter((e) => !q || (e.name || '').toLowerCase().includes(q) || e.url.toLowerCase().includes(q))
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="Attach a saved external API"
            title="Attach a saved external API (API tester)"
            data-testid="chat-attach-api"
            className="relative"
          />
        }
      >
        <Paperclip />
        {selected.length > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex size-3.5 items-center justify-center rounded-full bg-primary text-[9px] font-bold text-primary-foreground">
            {selected.length}
          </span>
        )}
      </PopoverTrigger>
      <PopoverContent side="top" align="end" className="w-80 p-0">
        <div className="hairline-b flex items-center justify-between px-3 py-2">
          <span className="text-[11.5px] font-medium text-muted-foreground">Saved external APIs</span>
          <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
            <Send className="size-3" /> from the API tester
          </span>
        </div>
        <div className="p-2">
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search…" className="h-7 text-xs" />
        </div>
        <div className="max-h-64 overflow-y-auto px-2 pb-2">
          {items === null ? (
            <div className="py-3 text-center text-xs text-muted-foreground">Loading…</div>
          ) : filtered.length === 0 ? (
            <div className="py-3 text-center text-xs text-muted-foreground">
              {search ? 'No matches' : 'No saved APIs. Capture one in the API tester (App settings).'}
            </div>
          ) : (
            filtered.map((ex) => (
              <label key={ex.id} className="flex cursor-pointer items-start gap-2 rounded-ctl px-2 py-1.5 text-xs hover:bg-(--hover-fill)">
                <Checkbox
                  className="mt-0.5"
                  checked={selected.includes(ex.id)}
                  onCheckedChange={(c) => onChange(c ? [...selected, ex.id] : selected.filter((s) => s !== ex.id))}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono text-[10px] font-bold">{ex.method}</span>
                    <span className="truncate">{ex.name || ex.url}</span>
                  </div>
                  {ex.name && <div className="truncate text-[10px] text-muted-foreground">{ex.url}</div>}
                </div>
              </label>
            ))
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}

// ── Composer ────────────────────────────────────────────────────────────────

export interface ComposerHandle {
  focus: () => void
}

const Composer = forwardRef<
  ComposerHandle,
  {
    streaming: boolean
    onSend: (t: string) => void
    onStop: () => void
    routes: Route[]
    routeIds: string[]
    onSaveRoutes: (ids: string[]) => Promise<unknown>
    attached: string[]
    onAttach: (ids: string[]) => void
    /** while set, the composer is closed and says why (a build from the plan is making this app) */
    locked?: string
  }
>(function Composer({ streaming, onSend, onStop, routes, routeIds, onSaveRoutes, attached, onAttach, locked }, ref) {
  const [text, setText] = useState('')
  const taRef = useRef<HTMLTextAreaElement>(null)
  const draft = usePreviewSession((s) => s.composerDraft)
  useImperativeHandle(ref, () => ({ focus: () => taRef.current?.focus() }))
  useEffect(() => {
    if (draft) {
      setText(draft.text)
      requestAnimationFrame(() => {
        const ta = taRef.current
        if (ta) {
          ta.focus()
          ta.setSelectionRange(ta.value.length, ta.value.length)
        }
      })
    }
  }, [draft])
  const submit = () => {
    const t = text.trim()
    if (!t || streaming || locked) return
    onSend(t)
    setText('')
  }
  return (
    <div className="mx-3 mb-3 rounded-panel bg-recess has-[textarea:focus-visible]:shadow-focus" data-recess>
      <textarea
        ref={taRef}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            submit()
          }
        }}
        rows={2}
        disabled={!!locked}
        placeholder={locked ?? 'Ask for a change…'}
        className="block max-h-40 min-h-11 w-full resize-none bg-transparent px-3 pt-2.5 text-[13px] leading-5 outline-none placeholder:text-muted-foreground"
        data-testid="chat-input"
      />
      <div className="flex items-center gap-1.5 px-1.5 pb-1.5 pl-2 pt-1">
        <RoutePicker routes={routes} value={routeIds} onSave={onSaveRoutes} />
        <div className="flex-1" />
        <AttachApiPopover selected={attached} onChange={onAttach} />
        {streaming ? (
          <Button size="icon-sm" variant="secondary" onClick={onStop} aria-label="Stop" data-testid="chat-stop">
            <Square className="fill-current" />
          </Button>
        ) : (
          <Button size="icon-sm" onClick={submit} disabled={!text.trim() || !!locked} aria-label="Send" data-testid="chat-send">
            <ArrowUp />
          </Button>
        )}
      </div>
    </div>
  )
})

// ── Panel ───────────────────────────────────────────────────────────────────

export const AppBuilderChatPanel = forwardRef<
  ComposerHandle,
  {
    projectName: string
    routes: Route[]
    routeIds: string[]
    onSaveRoutes: (ids: string[]) => Promise<unknown>
    attached: string[]
    onAttach: (ids: string[]) => void
    isStreaming: boolean
    onSend: (t: string) => void
    onRetry: () => void
    onStop: () => void
    /** the plan-and-build conversation that made this app, ahead of the chat about changes */
    prelude?: ReactNode
    locked?: string
  }
>(function AppBuilderChatPanel(
  { projectName, routes, routeIds, onSaveRoutes, attached, onAttach, isStreaming, onSend, onRetry, onStop, prelude, locked },
  ref,
) {
  const messages = useAppBuilderChatStore((s) => s.messages)
  const clear = useAppBuilderChatStore((s) => s.clearHistory)
  const scrollRef = useRef<HTMLDivElement>(null)
  const attachedCount = routeIds.filter((id) => routes.some((r) => r.id === id)).length

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="hairline-b flex h-11 shrink-0 items-center gap-2 pl-3.5 pr-2.5">
        <IconTile hue="ai" className="size-6">
          <Sparkles />
        </IconTile>
        <span className="text-panel-title font-semibold tracking-[-0.01em]">Build with AI</span>
        <div className="flex-1" />
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={clear}
                disabled={isStreaming || messages.length === 0}
                aria-label="New chat"
                data-testid="chat-new"
              />
            }
          >
            <SquarePen />
          </TooltipTrigger>
          <TooltipContent>New chat (the app keeps its code)</TooltipContent>
        </Tooltip>
      </div>
      <div ref={scrollRef} className="flex min-h-0 flex-1 flex-col gap-3.5 overflow-y-auto p-3.5" data-testid="chat-messages">
        <div className="flex items-center justify-center gap-2 text-[11.5px] text-muted-foreground" data-testid="chat-context">
          <PlugZap className="size-3" />
          {attachedCount > 0
            ? `Started from ${projectName} · ${attachedCount} ${attachedCount === 1 ? 'route' : 'routes'} attached`
            : `${projectName} · no routes attached`}
        </div>
        {prelude}
        {messages.length === 0 && !prelude && (
          <div className="mt-6 text-center text-xs leading-5 text-muted-foreground" data-testid="chat-empty">
            Describe the UI you want. The AI writes React on your typed <span className="font-mono">api</span> client and the
            preview calls your backend.
          </div>
        )}
        {messages.map((m, i) =>
          m.role === 'user' ? (
            <Message key={m.id} from="user" className="max-w-[88%]" data-testid="chat-user-message">
              <MessageContent className="whitespace-pre-wrap text-[13px] leading-[19px]">{m.content}</MessageContent>
            </Message>
          ) : (
            <AssistantMessage key={m.id} msg={m} streaming={isStreaming && i === messages.length - 1} onRetry={onRetry} onSend={onSend} />
          ),
        )}
      </div>
      <Composer
        ref={ref}
        streaming={isStreaming}
        onSend={onSend}
        onStop={onStop}
        routes={routes}
        routeIds={routeIds}
        onSaveRoutes={onSaveRoutes}
        attached={attached}
        onAttach={onAttach}
        locked={locked}
      />
    </div>
  )
})
