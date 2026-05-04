/** API Tester panel — Postman-lite, lives inside the app-builder workspace.
 *
 * Captured executions persist server-side and become attachable LLM context
 * for the chat panel (see EndpointPopover in AppBuilderChatPanel.tsx). The
 * panel is tab-organized: composer (Params / Headers / Body) on top, response
 * with sub-tabs (Pretty / Raw / Headers) below, saved-history rail on the left.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Check,
  Copy,
  Download,
  FileDown,
  FileText,
  Inbox,
  Loader2,
  Pencil,
  Plus,
  Send,
  Trash2,
  X,
} from 'lucide-react'
import { toast } from 'sonner'
import { create } from 'zustand'

import { Input } from '@/shared/components/ui/input'
import JsonViewer from '@/shared/components/ui/json-viewer'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { ToolbarSeparator } from '@/shared/components/ui/toolbar'
import {
  apiTesterApi,
  type ApiTestExecuteBody,
  type ApiTestExecution,
  type ApiTestExecutionListItem,
} from '@/shared/lib/api'

// ── Cross-panel state ─────────────────────────────────────────────────────
//
// `SavedQueriesPanel` and `ApiTesterPanel` are sibling floating panels in the
// app-builder shell. They share a tiny zustand store so the saved-queries
// rail can request the composer to load a row, and the composer can publish
// which row is "active" for highlight purposes.

interface ApiTesterStore {
  /** id of the saved execution currently shown in the response/composer */
  selectedId: string | null
  /** set by SavedQueriesPanel; ApiTesterPanel watches this and clears it
   *  after applying the load. */
  pendingLoadId: string | null
  setSelected: (id: string | null) => void
  requestLoad: (id: string) => void
  clearLoad: () => void
}

const useApiTesterStore = create<ApiTesterStore>((set) => ({
  selectedId: null,
  pendingLoadId: null,
  setSelected: (id) => set({ selectedId: id }),
  requestLoad: (id) => set({ pendingLoadId: id }),
  clearLoad: () => set({ pendingLoadId: null }),
}))

// ── Types & helpers ───────────────────────────────────────────────────────

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'] as const
type Method = (typeof METHODS)[number]
type BodyMode = 'json' | 'text' | 'form' | 'none'
type RequestTab = 'params' | 'headers' | 'body'
type ResponseTab = 'pretty' | 'raw' | 'headers'

interface KV {
  key: string
  value: string
  on?: boolean // disable a row without deleting it
}

const ICON_BTN =
  'h-7 w-7 inline-flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors'

/** Backgrounded pill variant for use as a chip — bg-{color}/10 + text-{color}. */
function methodPillClass(m: string) {
  switch (m.toUpperCase()) {
    case 'GET': return 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
    case 'POST': return 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
    case 'PUT': return 'bg-blue-500/10 text-blue-600 dark:text-blue-400'
    case 'PATCH': return 'bg-violet-500/10 text-violet-600 dark:text-violet-400'
    case 'DELETE': return 'bg-red-500/10 text-red-600 dark:text-red-400'
    case 'HEAD': return 'bg-slate-500/10 text-slate-600 dark:text-slate-400'
    case 'OPTIONS': return 'bg-slate-500/10 text-slate-600 dark:text-slate-400'
    default: return 'bg-muted text-muted-foreground'
  }
}

function statusPillClass(s: number | null | undefined) {
  if (s == null) return 'bg-muted text-muted-foreground'
  if (s < 300) return 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
  if (s < 400) return 'bg-blue-500/10 text-blue-600 dark:text-blue-400'
  if (s < 500) return 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
  return 'bg-red-500/10 text-red-600 dark:text-red-400'
}

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(2)} MB`
}

function isText(ctype: string | null | undefined) {
  if (!ctype) return false
  const c = ctype.toLowerCase()
  return c.includes('json') || c.startsWith('text/') || c.includes('xml') || c.includes('yaml') || c.includes('javascript')
}

function isJson(ctype: string | null | undefined) {
  return !!ctype && ctype.toLowerCase().includes('json')
}

function decodeB64(b64: string): { text: string; bytes: Uint8Array } {
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  let text = ''
  try {
    text = new TextDecoder('utf-8', { fatal: false }).decode(bytes)
  } catch {
    /* leave empty */
  }
  return { text, bytes }
}

function prettyJson(s: string) {
  try {
    return JSON.stringify(JSON.parse(s), null, 2)
  } catch {
    return s
  }
}

function filenameFromDisp(disp: string | null | undefined) {
  if (!disp) return null
  const m = /filename="?([^";]+)"?/i.exec(disp)
  return m ? m[1] : null
}

/** Split URL into base + parsed query rows. The user can edit either side and
 *  we keep them in sync — the URL field is the source of truth on send. */
function splitUrl(url: string): { base: string; params: KV[] } {
  const idx = url.indexOf('?')
  if (idx < 0) return { base: url, params: [] }
  const base = url.slice(0, idx)
  const qs = url.slice(idx + 1)
  if (!qs) return { base, params: [] }
  const params: KV[] = []
  for (const pair of qs.split('&')) {
    if (!pair) continue
    const eq = pair.indexOf('=')
    const k = decodeURIComponent(eq < 0 ? pair : pair.slice(0, eq))
    const v = eq < 0 ? '' : decodeURIComponent(pair.slice(eq + 1))
    params.push({ key: k, value: v, on: true })
  }
  return { base, params }
}

function joinUrl(base: string, params: KV[]): string {
  const live = params.filter((p) => p.on !== false && p.key.trim())
  if (live.length === 0) return base
  const qs = live.map((p) => `${encodeURIComponent(p.key.trim())}=${encodeURIComponent(p.value)}`).join('&')
  return `${base.split('?')[0]}?${qs}`
}

// ── Hooks ─────────────────────────────────────────────────────────────────

function useExecutions() {
  return useQuery<ApiTestExecutionListItem[]>({
    queryKey: ['api-tester', 'executions'],
    queryFn: () => apiTesterApi.list(),
    staleTime: 1000 * 30,
  })
}

function useExecuteMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (b: ApiTestExecuteBody) => apiTesterApi.execute(b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['api-tester', 'executions'] }),
  })
}

function useDeleteMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiTesterApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['api-tester', 'executions'] }),
  })
}

function useRenameMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, name }: { id: string; name: string | null }) => apiTesterApi.rename(id, name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['api-tester', 'executions'] }),
  })
}

// ── Panel ─────────────────────────────────────────────────────────────────

export function ApiTesterPanel({ onClose }: { onClose?: () => void }) {
  // Composer state
  const [method, setMethod] = useState<Method>('POST')
  const [url, setUrl] = useState('http://localhost:8765/generate-excel')
  const [name, setName] = useState('')
  const [params, setParams] = useState<KV[]>([])
  const [headers, setHeaders] = useState<KV[]>([
    { key: 'Content-Type', value: 'application/json', on: true },
  ])
  const [bodyMode, setBodyMode] = useState<BodyMode>('json')
  const [bodyText, setBodyText] = useState(
    '{\n  "filename": "report",\n  "rows": 10,\n  "title": "Test"\n}',
  )
  const [formRows, setFormRows] = useState<KV[]>([{ key: '', value: '', on: true }])

  // UI tabs + selected response
  const [reqTab, setReqTab] = useState<RequestTab>('body')
  const [resTab, setResTab] = useState<ResponseTab>('pretty')
  const [response, setResponse] = useState<ApiTestExecution | null>(null)

  // Inline name-popover open state — keeps the URL row clean
  const [nameOpen, setNameOpen] = useState(false)
  const nameInputRef = useRef<HTMLInputElement>(null)

  const executeMut = useExecuteMutation()
  const pendingLoadId = useApiTesterStore((s) => s.pendingLoadId)
  const clearLoad = useApiTesterStore((s) => s.clearLoad)
  const setSelected = useApiTesterStore((s) => s.setSelected)

  // ── derived: full URL with params merged in ──
  const finalUrl = useMemo(() => joinUrl(url, params), [url, params])

  // ── handlers ──
  const handleSend = async () => {
    if (!finalUrl.trim()) return toast.error('URL required')

    const headerObj: Record<string, string> = {}
    for (const h of headers) {
      if (h.on === false) continue
      if (!h.key.trim()) continue
      headerObj[h.key.trim()] = h.value
    }

    let bodyToSend: string | null = null
    if (!['GET', 'HEAD'].includes(method)) {
      if (bodyMode === 'json' || bodyMode === 'text') {
        bodyToSend = bodyText || null
        if (bodyMode === 'json' && !headerObj['Content-Type']) headerObj['Content-Type'] = 'application/json'
      } else if (bodyMode === 'form') {
        const live = formRows.filter((r) => r.on !== false && r.key.trim())
        bodyToSend = live
          .map((r) => `${encodeURIComponent(r.key.trim())}=${encodeURIComponent(r.value)}`)
          .join('&')
        if (!headerObj['Content-Type']) headerObj['Content-Type'] = 'application/x-www-form-urlencoded'
      }
    }

    try {
      const result = await executeMut.mutateAsync({
        name: name.trim() || null,
        method,
        url: finalUrl.trim(),
        headers: headerObj,
        body: bodyToSend,
      })
      setResponse(result)
      setSelected(result.id)
      setResTab(isText(result.response_content_type) ? 'pretty' : 'raw')
      if (result.error) toast.error(result.error)
      else toast.success(`${result.response_status} • ${result.latency_ms?.toFixed(0)}ms`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Request failed')
    }
  }

  const handleSelectExecution = async (id: string) => {
    try {
      const exec = await apiTesterApi.get(id)
      setResponse(exec)
      setSelected(exec.id)
      setResTab(isText(exec.response_content_type) ? 'pretty' : 'raw')
      const m = (METHODS.find((mm) => mm === exec.method) as Method) ?? 'POST'
      setMethod(m)
      // Split URL back into base + params for the editor
      const { base, params: parsedParams } = splitUrl(exec.url)
      setUrl(base)
      setParams(parsedParams)
      setName(exec.name ?? '')
      setHeaders(
        Object.entries(exec.request_headers || {}).map(([k, v]) => ({ key: k, value: String(v), on: true })),
      )
      // Try to detect body mode from content-type
      const ct = String((exec.request_headers as Record<string, string>)?.['Content-Type'] || '').toLowerCase()
      if (ct.includes('form')) {
        setBodyMode('form')
        const rows: KV[] = []
        for (const pair of (exec.request_body_text || '').split('&')) {
          if (!pair) continue
          const eq = pair.indexOf('=')
          rows.push({
            key: decodeURIComponent(eq < 0 ? pair : pair.slice(0, eq)),
            value: eq < 0 ? '' : decodeURIComponent(pair.slice(eq + 1)),
            on: true,
          })
        }
        setFormRows(rows.length ? rows : [{ key: '', value: '', on: true }])
      } else if (ct.includes('json')) {
        setBodyMode('json')
        setBodyText(exec.request_body_text ?? '')
      } else if (exec.request_body_text) {
        setBodyMode('text')
        setBodyText(exec.request_body_text)
      } else {
        setBodyMode('none')
      }
    } catch {
      toast.error('Failed to load')
    }
  }

  // Send on Cmd/Ctrl+Enter
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault()
        handleSend()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [method, url, name, headers, params, bodyMode, bodyText, formRows])

  // Close the name popover on outside click
  useEffect(() => {
    if (!nameOpen) return
    const onDoc = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest('[data-name-popover]')) setNameOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [nameOpen])

  // Apply load requests from the SavedQueriesPanel sibling.
  useEffect(() => {
    if (!pendingLoadId) return
    const id = pendingLoadId
    clearLoad()
    void handleSelectExecution(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingLoadId])

  return (
    <div className="h-full w-full flex flex-col text-foreground">
      {/* Header bar — mirrors the parent navbar's chrome density. */}
      <div className="h-11 px-3 border-b border-border/40 flex items-center gap-2">
        <span className="inline-flex items-center justify-center h-6 w-6 rounded-md bg-gradient-to-br from-primary/15 to-primary/5 text-primary">
          <Send className="h-3.5 w-3.5" />
        </span>
        <span className="text-[13px] font-medium text-foreground">API Tester</span>
        <span className="text-[10px] uppercase tracking-wider font-semibold px-1.5 py-0.5 rounded-full bg-muted/60 text-muted-foreground">
          beta
        </span>
        <span className="hidden md:inline text-[11px] text-muted-foreground/80 truncate">
          Postman-lite — attach captured calls to chat as endpoint context
        </span>

        <div className="flex-1" />

        {onClose && (
          <>
            <ToolbarSeparator />
            <button onClick={onClose} className={ICON_BTN} title="Close">
              <X className="h-3.5 w-3.5" />
            </button>
          </>
        )}
      </div>

      <div className="flex-1 min-h-0">
        {/* Composer + response stack — saved queries live in a sibling
            floating panel managed by the app-builder shell. */}
        <div className="h-full grid grid-rows-[auto_minmax(0,1fr)_minmax(0,1fr)] min-h-0">
          {/* URL row — composer card */}
          <div className="p-3 border-b border-border/40">
            <div className="flex items-center gap-2 bg-muted/30 border border-border/40 rounded-lg p-1.5 focus-within:border-border/70 transition-colors">
              {/* Method select — styled to look like a method pill, native select for a11y. */}
              <div className={`relative h-7 inline-flex items-center rounded-md px-2 font-mono text-[11px] font-bold ${methodPillClass(method)}`}>
                <select
                  aria-label="HTTP method"
                  value={method}
                  onChange={(e) => setMethod(e.target.value as Method)}
                  className="absolute inset-0 opacity-0 cursor-pointer"
                >
                  {METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
                <span className="pointer-events-none">{method}</span>
              </div>

              <Input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://api.example.com/resource"
                className="h-7 text-xs flex-1 font-mono bg-transparent border-none shadow-none focus-visible:ring-0 px-1"
              />

              {/* Name & save popover trigger */}
              <div className="relative" data-name-popover>
                <button
                  type="button"
                  onClick={() => {
                    setNameOpen((v) => !v)
                    setTimeout(() => nameInputRef.current?.focus(), 0)
                  }}
                  className={`h-7 px-2 inline-flex items-center gap-1 rounded-md text-[11px] transition-colors ${
                    name
                      ? 'bg-primary/10 text-primary hover:bg-primary/15'
                      : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                  }`}
                  title="Name & save"
                >
                  <FileText className="h-3 w-3" />
                  <span className="max-w-[110px] truncate">
                    {name || 'Name'}
                  </span>
                </button>
                {nameOpen && (
                  <div
                    className="absolute right-0 top-[calc(100%+6px)] z-30 w-64 p-2 rounded-md border border-border/50 bg-popover shadow-lg"
                  >
                    <label className="block text-[10px] uppercase tracking-wider font-semibold text-muted-foreground mb-1.5">
                      Name & save
                    </label>
                    <Input
                      ref={nameInputRef}
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === 'Escape') {
                          e.preventDefault()
                          setNameOpen(false)
                        }
                      }}
                      placeholder="Describe this request"
                      className="h-7 text-xs"
                    />
                    <p className="text-[10px] text-muted-foreground mt-1.5 leading-relaxed">
                      A friendly label for the saved entry. Optional.
                    </p>
                  </div>
                )}
              </div>

              <ToolbarSeparator />

              {/* Send — primary CTA. */}
              <button
                onClick={handleSend}
                disabled={executeMut.isPending}
                className="h-7 pl-2.5 pr-2 rounded-md bg-primary text-primary-foreground text-[11px] font-medium hover:bg-primary/90 transition-colors inline-flex items-center gap-1.5 disabled:opacity-50"
                title="Send (Cmd/Ctrl+Enter)"
              >
                {executeMut.isPending
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  : <Send className="h-3.5 w-3.5" />}
                Send
                {!executeMut.isPending && (
                  <span
                    className="ml-1 hidden lg:inline-flex items-center gap-0.5 px-1 py-px rounded bg-primary-foreground/15 text-[9px] font-mono text-primary-foreground/80"
                    aria-hidden="true"
                  >
                    <span className="text-[10px] leading-none">{'⌘'}</span>
                    <span>Enter</span>
                  </span>
                )}
              </button>
            </div>
          </div>

          {/* Composer tabs */}
          <div className="flex flex-col min-h-0 border-b border-border/40">
            <div className="flex items-center gap-2 px-3 pt-2.5 pb-1.5 shrink-0">
              <PillTabs<RequestTab>
                value={reqTab}
                onChange={setReqTab}
                options={[
                  { key: 'params', label: 'Params', count: params.filter((p) => p.on !== false && p.key).length },
                  { key: 'headers', label: 'Headers', count: headers.filter((h) => h.on !== false && h.key).length },
                  { key: 'body', label: 'Body' },
                ]}
              />
            </div>
            <div className="flex-1 overflow-auto px-3 pb-3 min-h-0">
              {reqTab === 'params' && <KvEditor rows={params} onChange={setParams} addLabel="Add param" emptyLabel="No params yet" />}
              {reqTab === 'headers' && <KvEditor rows={headers} onChange={setHeaders} addLabel="Add header" emptyLabel="No headers yet" />}
              {reqTab === 'body' && (
                <BodyEditor
                  mode={bodyMode}
                  onModeChange={setBodyMode}
                  text={bodyText}
                  onTextChange={setBodyText}
                  rows={formRows}
                  onRowsChange={setFormRows}
                  method={method}
                />
              )}
            </div>
          </div>

          {/* Response panel */}
          <ResponsePanel response={response} isLoading={executeMut.isPending} tab={resTab} onTab={setResTab} />
        </div>
      </div>
    </div>
  )
}

// ── Pill-tab group (matches projects.tsx type-filter pattern) ─────────────

interface PillTabOption<K extends string> {
  key: K
  label: string
  count?: number
}

function PillTabs<K extends string>({
  value,
  onChange,
  options,
}: {
  value: K
  onChange: (k: K) => void
  options: PillTabOption<K>[]
}) {
  return (
    <div className="flex items-center gap-0.5 bg-muted/50 rounded-md p-0.5">
      {options.map((opt) => {
        const active = value === opt.key
        return (
          <button
            key={opt.key}
            type="button"
            onClick={() => onChange(opt.key)}
            className={`px-2.5 h-6 inline-flex items-center gap-1 text-[11px] font-medium rounded-md transition-all duration-150 ${
              active ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            <span>{opt.label}</span>
            {opt.count != null && opt.count > 0 && (
              <span
                className={`min-w-[16px] h-[15px] inline-flex items-center justify-center px-1 rounded-full text-[9px] font-mono ${
                  active ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'
                }`}
              >
                {opt.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

// ── Saved row (with inline rename) ────────────────────────────────────────

function SavedRow({
  row,
  active,
  onSelect,
  onDelete,
  onRename,
}: {
  row: ApiTestExecutionListItem
  active: boolean
  onSelect: () => void
  onDelete: () => void
  onRename: (name: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(row.name ?? '')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing) inputRef.current?.focus()
  }, [editing])

  return (
    <li
      onClick={() => !editing && onSelect()}
      className={`group relative px-2 py-1.5 cursor-pointer rounded-md border transition-colors ${
        active
          ? 'bg-primary/10 border-primary/20'
          : 'border-transparent hover:bg-accent hover:border-border/40'
      }`}
    >
      {/* Active accent stripe */}
      {active && (
        <span className="absolute left-0 top-1.5 bottom-1.5 w-0.5 rounded-full bg-primary" aria-hidden="true" />
      )}

      <div className="flex items-center gap-1.5 min-w-0 pl-1.5">
        <span className={`shrink-0 inline-flex items-center justify-center h-[16px] px-1.5 rounded-sm text-[9px] font-mono font-bold ${methodPillClass(row.method)}`}>
          {row.method}
        </span>
        <span className={`shrink-0 inline-flex items-center justify-center h-[16px] min-w-[24px] px-1.5 rounded-sm text-[9px] font-mono font-semibold ${statusPillClass(row.response_status)}`}>
          {row.response_status ?? 'ERR'}
        </span>
        {editing ? (
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onBlur={() => { setEditing(false); if (draft !== (row.name ?? '')) onRename(draft) }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { (e.target as HTMLInputElement).blur() }
              else if (e.key === 'Escape') { setDraft(row.name ?? ''); setEditing(false) }
            }}
            className="text-[11px] flex-1 bg-background border border-border rounded px-1 py-0.5 focus:outline-none focus:ring-1 focus:ring-ring min-w-0"
          />
        ) : (
          <span className="text-[11px] truncate flex-1 font-medium" title={row.url}>
            {row.name || row.url}
          </span>
        )}
        <button
          onClick={(e) => { e.stopPropagation(); setDraft(row.name ?? ''); setEditing(true) }}
          className="opacity-0 group-hover:opacity-100 h-5 w-5 flex items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-accent transition-all"
          title="Rename"
        >
          <Pencil className="h-3 w-3" />
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onDelete() }}
          className="opacity-0 group-hover:opacity-100 h-5 w-5 flex items-center justify-center rounded text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-all"
          title="Delete"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>
      {row.name && (
        <div className="text-[10px] text-muted-foreground/80 truncate mt-0.5 ml-[78px] font-mono">
          {row.url}
        </div>
      )}
    </li>
  )
}

// ── Key/Value editor (Params, Headers) ────────────────────────────────────

function KvEditor({
  rows,
  onChange,
  addLabel = 'Add',
  emptyLabel = 'No entries',
}: {
  rows: KV[]
  onChange: (rows: KV[]) => void
  addLabel?: string
  emptyLabel?: string
}) {
  const update = (i: number, patch: Partial<KV>) => onChange(rows.map((r, idx) => idx === i ? { ...r, ...patch } : r))
  const add = () => onChange([...rows, { key: '', value: '', on: true }])
  const remove = (i: number) => onChange(rows.filter((_, idx) => idx !== i))

  return (
    <div className="space-y-1.5">
      {rows.length === 0 ? (
        <div className="flex flex-col items-center justify-center text-center py-8 px-3 rounded-md border border-dashed border-border/40">
          <div className="w-8 h-8 rounded-lg bg-muted/40 flex items-center justify-center mb-2">
            <Plus className="h-3.5 w-3.5 text-muted-foreground/70" />
          </div>
          <p className="text-[11px] font-medium text-foreground">{emptyLabel}</p>
          <button
            onClick={add}
            className="mt-2 h-7 px-2.5 inline-flex items-center gap-1 rounded-md text-[11px] text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
          >
            <Plus className="h-3 w-3" />
            {addLabel}
          </button>
        </div>
      ) : (
        <>
          {rows.map((row, i) => {
            const enabled = row.on !== false
            return (
              <div key={i} className="group flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => update(i, { on: !enabled })}
                  className={`shrink-0 h-5 w-5 rounded-md border transition-colors inline-flex items-center justify-center ${
                    enabled
                      ? 'bg-primary/10 border-primary/30 text-primary'
                      : 'bg-transparent border-border/50 text-transparent hover:bg-accent'
                  }`}
                  title={enabled ? 'Disable row' : 'Enable row'}
                  aria-pressed={enabled}
                >
                  <Check className="h-3 w-3" />
                </button>
                <Input
                  value={row.key}
                  onChange={(e) => update(i, { key: e.target.value })}
                  placeholder="key"
                  className={`h-7 text-xs w-[40%] font-mono transition-opacity ${enabled ? '' : 'opacity-50 line-through decoration-muted-foreground/40'}`}
                />
                <Input
                  value={row.value}
                  onChange={(e) => update(i, { value: e.target.value })}
                  placeholder="value"
                  className={`h-7 text-xs flex-1 font-mono transition-opacity ${enabled ? '' : 'opacity-50 line-through decoration-muted-foreground/40'}`}
                />
                <button
                  onClick={() => remove(i)}
                  className={ICON_BTN + ' opacity-0 group-hover:opacity-100 transition-opacity'}
                  title="Remove row"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            )
          })}
          <button
            onClick={add}
            className="w-full h-7 px-2 inline-flex items-center justify-center gap-1 rounded-md border border-dashed border-border/50 text-[11px] text-muted-foreground hover:text-foreground hover:border-border hover:bg-accent transition-colors"
          >
            <Plus className="h-3 w-3" />
            {addLabel}
          </button>
        </>
      )}
    </div>
  )
}

// ── Body editor with mode tabs ─────────────────────────────────────────────

function BodyEditor({
  mode,
  onModeChange,
  text,
  onTextChange,
  rows,
  onRowsChange,
  method,
}: {
  mode: BodyMode
  onModeChange: (m: BodyMode) => void
  text: string
  onTextChange: (t: string) => void
  rows: KV[]
  onRowsChange: (r: KV[]) => void
  method: string
}) {
  const noBody = method === 'GET' || method === 'HEAD'

  if (noBody) {
    return (
      <div className="flex flex-col items-center justify-center text-center py-10 px-3 rounded-md border border-dashed border-border/40">
        <p className="text-[11px] font-medium text-foreground">No body for {method} requests</p>
        <p className="text-[10px] text-muted-foreground mt-1">Switch to POST, PUT, PATCH, or DELETE to attach a body.</p>
      </div>
    )
  }

  const modeOptions: { key: BodyMode; label: string }[] = [
    { key: 'none', label: 'None' },
    { key: 'json', label: 'JSON' },
    { key: 'text', label: 'Raw' },
    { key: 'form', label: 'Form' },
  ]

  return (
    <div className="h-full flex flex-col gap-2 min-h-0">
      <div className="flex items-center gap-2">
        <PillTabs<BodyMode>
          value={mode}
          onChange={onModeChange}
          options={modeOptions}
        />
        <div className="flex-1" />
        {mode === 'json' && (
          <button
            onClick={() => onTextChange(prettyJson(text))}
            className="h-6 px-2 text-[10px] font-medium rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            title="Pretty-print JSON"
          >
            Format
          </button>
        )}
      </div>
      {mode === 'none' ? (
        <div className="flex flex-col items-center justify-center text-center py-10 px-3 rounded-md border border-dashed border-border/40">
          <p className="text-[11px] font-medium text-foreground">No body will be sent</p>
          <p className="text-[10px] text-muted-foreground mt-1">Pick JSON, Raw, or Form to add request data.</p>
        </div>
      ) : mode === 'form' ? (
        <KvEditor rows={rows} onChange={onRowsChange} addLabel="Add field" emptyLabel="No form fields yet" />
      ) : (
        <textarea
          value={text}
          onChange={(e) => onTextChange(e.target.value)}
          spellCheck={false}
          placeholder={mode === 'json' ? '{ "key": "value" }' : 'raw text body'}
          className="flex-1 min-h-0 bg-muted/20 border border-border/40 rounded-md p-2 text-xs font-mono resize-none focus:outline-none focus:ring-1 focus:ring-ring focus:bg-background transition-colors"
        />
      )}
    </div>
  )
}

// ── Response panel ─────────────────────────────────────────────────────────

function ResponsePanel({
  response,
  isLoading,
  tab,
  onTab,
}: {
  response: ApiTestExecution | null
  isLoading: boolean
  tab: ResponseTab
  onTab: (t: ResponseTab) => void
}) {
  const decoded = useMemo(() => {
    if (!response?.response_body_b64) return { text: '', bytes: new Uint8Array() }
    return decodeB64(response.response_body_b64)
  }, [response])

  const [copied, setCopied] = useState(false)

  const handleDownload = () => {
    if (!response?.response_body_b64) return
    const { bytes } = decodeB64(response.response_body_b64)
    const blob = new Blob([bytes.buffer as ArrayBuffer], { type: response.response_content_type || 'application/octet-stream' })
    const headers = response.response_headers as Record<string, string> | undefined
    const filename =
      filenameFromDisp(headers?.['content-disposition']) || `response-${response.id}.bin`
    const u = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = u
    a.download = filename
    a.click()
    URL.revokeObjectURL(u)
  }

  if (isLoading && !response) {
    return (
      <div className="flex items-center justify-center min-h-0">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }
  if (!response) {
    return (
      <div className="flex flex-col items-center justify-center min-h-0 text-center px-6">
        <div className="w-9 h-9 rounded-xl bg-muted/40 flex items-center justify-center mb-3">
          <Send className="h-4 w-4 text-muted-foreground/60" />
        </div>
        <p className="text-[11px] font-medium text-foreground">No response yet</p>
        <p className="text-[10px] text-muted-foreground mt-1">
          Send a request to see status, headers, and body here.
        </p>
      </div>
    )
  }

  const ctype = response.response_content_type
  const showText = isText(ctype)
  const isBinary = !!response.response_body_b64 && !showText
  const filename = filenameFromDisp(
    (response.response_headers as Record<string, string> | undefined)?.['content-disposition'],
  )

  const handleCopy = async () => {
    const payload =
      tab === 'headers'
        ? Object.entries(response.response_headers || {}).map(([k, v]) => `${k}: ${v}`).join('\n')
        : tab === 'pretty' && isJson(ctype)
          ? prettyJson(decoded.text)
          : decoded.text
    if (!payload) return
    try {
      await navigator.clipboard.writeText(payload)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {
      toast.error('Copy failed')
    }
  }

  const status = response.response_status
  const statusLabel = status == null ? 'ERR' : String(status)

  return (
    <div className="flex flex-col min-h-0">
      {/* Status pill bar */}
      <div className="px-3 h-10 border-b border-border/40 flex items-center gap-1.5 shrink-0">
        <span
          className={`inline-flex items-center h-6 px-2 rounded-md text-[11px] font-mono font-semibold ${statusPillClass(status)}`}
          title={status != null ? `HTTP ${status}` : 'No status'}
        >
          {statusLabel}
        </span>
        {response.latency_ms !== null && (
          <span
            className="inline-flex items-center h-6 px-2 rounded-md bg-muted/60 text-muted-foreground text-[11px] font-mono"
            title="Latency"
          >
            {response.latency_ms.toFixed(0)} ms
          </span>
        )}
        <span
          className="inline-flex items-center h-6 px-2 rounded-md bg-muted/60 text-muted-foreground text-[11px] font-mono"
          title={`Response size: ${response.response_size} bytes`}
        >
          {formatBytes(response.response_size)}
        </span>
        {ctype && (
          <span
            className="inline-flex items-center h-6 px-2 rounded-md bg-muted/40 text-muted-foreground text-[11px] font-mono truncate max-w-[260px]"
            title={ctype}
          >
            {ctype}
          </span>
        )}
        <div className="flex-1" />
        {isBinary && (
          <button
            onClick={handleDownload}
            className="h-6 px-2 inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground text-[11px] font-medium hover:bg-primary/90 transition-colors"
          >
            <Download className="h-3 w-3" />
            Download
          </button>
        )}
      </div>

      {/* Response tabs */}
      <div className="flex items-center gap-2 px-3 pt-2 pb-1.5 shrink-0 border-b border-border/30">
        <PillTabs<ResponseTab>
          value={tab}
          onChange={onTab}
          options={[
            { key: 'pretty', label: 'Pretty' },
            { key: 'raw', label: 'Raw' },
            { key: 'headers', label: 'Headers' },
          ]}
        />
      </div>

      {/* Body — wrapped in a subtle card */}
      <div className="flex-1 min-h-0 p-3">
        <div className="group relative h-full rounded-md border border-border/40 bg-muted/20 overflow-hidden">
          {/* Hover-revealed copy button — top-right of body. */}
          {!response.error && (tab === 'headers' || showText) && (
            <button
              onClick={handleCopy}
              className="absolute top-1.5 right-1.5 z-10 h-6 px-1.5 inline-flex items-center gap-1 rounded-md bg-card/80 border border-border/40 text-[10px] text-muted-foreground hover:text-foreground hover:bg-card transition-all opacity-0 group-hover:opacity-100 focus:opacity-100"
              style={{ opacity: copied ? 1 : undefined }}
              title="Copy"
            >
              {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
              {copied ? 'Copied' : 'Copy'}
            </button>
          )}
          <div className="h-full overflow-auto text-xs font-mono">
            {response.error ? (
              <pre className="text-destructive whitespace-pre-wrap p-3">{response.error}</pre>
            ) : tab === 'headers' ? (
              <pre className="whitespace-pre-wrap text-muted-foreground p-3">
                {Object.entries(response.response_headers || {})
                  .map(([k, v]) => `${k}: ${v}`)
                  .join('\n') || '(no headers)'}
              </pre>
            ) : tab === 'pretty' && showText && isJson(ctype) ? (
              <PrettyJson text={decoded.text} />
            ) : showText ? (
              <pre className="whitespace-pre-wrap p-3">{decoded.text}</pre>
            ) : isBinary ? (
              <div className="h-full flex flex-col items-center justify-center text-center px-4">
                <div className="w-10 h-10 rounded-xl bg-muted/50 flex items-center justify-center mb-3">
                  <FileDown className="h-4 w-4 text-muted-foreground/70" />
                </div>
                <p className="text-[11px] font-medium text-foreground">Binary response</p>
                {filename && (
                  <p className="text-[10px] text-muted-foreground mt-1 font-mono truncate max-w-full">
                    {filename}
                  </p>
                )}
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  {formatBytes(response.response_size)}
                </p>
                <button
                  onClick={handleDownload}
                  className="mt-3 h-7 px-3 inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground text-[11px] font-medium hover:bg-primary/90 transition-colors"
                >
                  <Download className="h-3.5 w-3.5" />
                  Download
                </button>
              </div>
            ) : (
              <div className="h-full flex items-center justify-center text-[11px] text-muted-foreground italic">
                No body.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Pretty-JSON wrapper using the shared JsonViewer (CodeMirror) ──────────
//
// JsonViewer expects a parsed value. If parsing fails, fall back to the raw
// text in a <pre> so we never show an empty pane on malformed JSON.

function PrettyJson({ text }: { text: string }) {
  const parsed = useMemo<{ ok: true; value: unknown } | { ok: false }>(() => {
    try {
      return { ok: true, value: JSON.parse(text) }
    } catch {
      return { ok: false }
    }
  }, [text])

  if (!parsed.ok) {
    return <pre className="whitespace-pre-wrap p-3">{text}</pre>
  }
  return (
    <JsonViewer
      value={parsed.value}
      className="!border-0 !rounded-none h-full"
      maxHeight="100%"
    />
  )
}

// ── Saved Queries — sibling floating panel ────────────────────────────────
//
// Rendered as its own floating panel by the app-builder shell. Mirrors the
// shell's top-bar chrome (h-11 header with brand icon, separator, count) and
// communicates with `ApiTesterPanel` via the shared zustand store above.

export function SavedQueriesPanel({ onClose }: { onClose?: () => void }) {
  const { data: list, isLoading } = useExecutions()
  const deleteMut = useDeleteMutation()
  const renameMut = useRenameMutation()

  const selectedId = useApiTesterStore((s) => s.selectedId)
  const requestLoad = useApiTesterStore((s) => s.requestLoad)
  const setSelected = useApiTesterStore((s) => s.setSelected)

  const count = list?.length ?? 0

  return (
    <div className="h-full w-full flex flex-col text-foreground">
      {/* Header — mirrors the app-builder navbar's chrome density. */}
      <div className="h-11 px-3 border-b border-border/40 flex items-center gap-2 shrink-0">
        <span className="inline-flex items-center justify-center h-6 w-6 rounded-md bg-gradient-to-br from-primary/15 to-primary/5 text-primary">
          <Inbox className="h-3.5 w-3.5" />
        </span>
        <span className="text-[13px] font-medium text-foreground">Saved</span>
        {count > 0 && (
          <span className="text-[10px] text-muted-foreground/70 bg-muted/60 px-1.5 py-0.5 rounded-full font-mono">
            {count}
          </span>
        )}
        <div className="flex-1" />
        {onClose && (
          <>
            <ToolbarSeparator />
            <button onClick={onClose} className={ICON_BTN} title="Close">
              <X className="h-3.5 w-3.5" />
            </button>
          </>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-2 py-2">
        {isLoading ? (
          <div className="space-y-1.5">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-md" />
            ))}
          </div>
        ) : !list || list.length === 0 ? (
          <div className="flex flex-col items-center justify-center text-center py-12 px-3">
            <div className="w-9 h-9 rounded-xl bg-muted/50 flex items-center justify-center mb-3">
              <Inbox className="h-4 w-4 text-muted-foreground/60" />
            </div>
            <p className="text-[11px] font-medium text-foreground">No saved requests</p>
            <p className="text-[10px] text-muted-foreground mt-1 leading-relaxed">
              Send a request and it will appear here.
            </p>
          </div>
        ) : (
          <ul className="space-y-1">
            {list.map((row) => (
              <SavedRow
                key={row.id}
                row={row}
                active={selectedId === row.id}
                onSelect={() => requestLoad(row.id)}
                onDelete={async () => {
                  try {
                    await deleteMut.mutateAsync(row.id)
                    if (selectedId === row.id) setSelected(null)
                  } catch {
                    toast.error('Delete failed')
                  }
                }}
                onRename={async (newName) => {
                  try {
                    await renameMut.mutateAsync({ id: row.id, name: newName || null })
                  } catch {
                    toast.error('Rename failed')
                  }
                }}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
