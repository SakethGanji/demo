/**
 * Builder Backend panel (UI-DESIGN §5.2): connection card, Operations with usage from
 * `GET /api/apps/{A}/api-usage` (✓ only after an observed 2xx in this preview session, review H6),
 * and the api/client.ts excerpt + Copy usage. Below 1280 it collapses to a 44px rail.
 */
import { useMemo, useState } from 'react'
import { Link } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { CircleCheck, Copy, FileCode2, PlugZap, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import type { ApiUsage, Route } from '@/shared/api/types'
import { routesApi } from '@/shared/api/endpoints'
import { errorMessage } from '@/shared/api/http'
import { invalidate } from '@/shared/api/queryKeys'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { StatusDot, type DotTone } from '@/shared/components/ui/status-dot'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/components/ui/tooltip'
import { Segmented } from '@/shared/components/ui/segmented'
import { formatMs } from '@/features/project/lib/format'
import { cn } from '@/shared/lib/utils'
import { usePreviewSession, type ObservedCall } from '../stores/previewSessionStore'

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

interface OpRow {
  key: string
  name: string
  method: string
  path: string | null
  route: Route | null
  usage: ApiUsage[]
  status: 'used' | 'available' | 'missing'
}

function buildRows(routes: Route[], usage: ApiUsage[]): OpRow[] {
  const rows: OpRow[] = []
  const byRoute = new Map<string, ApiUsage[]>()
  const missing = new Map<string, ApiUsage[]>()
  for (const u of usage) {
    if (u.route_id) byRoute.set(u.route_id, [...(byRoute.get(u.route_id) ?? []), u])
    else {
      const k = u.operation ?? `${u.method ?? ''} ${u.path ?? ''}`
      missing.set(k, [...(missing.get(k) ?? []), u])
    }
  }
  for (const r of routes) {
    const used = byRoute.get(r.id) ?? []
    rows.push({ key: r.id, name: r.name, method: r.method, path: r.path, route: r, usage: used, status: used.length ? 'used' : 'available' })
  }
  // used first, then available, in route order
  rows.sort((a, b) => (a.status === b.status ? 0 : a.status === 'used' ? -1 : 1))
  for (const [k, us] of missing) {
    rows.push({ key: `missing:${k}`, name: us[0].operation ?? k, method: us[0].method ?? 'GET', path: us[0].path, route: null, usage: us, status: 'missing' })
  }
  return rows
}

/** `refundStats` → `/refund-stats` (a starting point for a stub path). */
function pathFromName(name: string): string {
  return '/' + name.replace(/([a-z0-9])([A-Z])/g, '$1-$2').replace(/[_\s]+/g, '-').toLowerCase()
}

export function CreateStubPopover({
  pid,
  appId,
  row,
  testIdPrefix = 'builder-op',
}: {
  pid: string
  appId: string
  row: Pick<OpRow, 'name' | 'method' | 'path'>
  testIdPrefix?: string
}) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [method, setMethod] = useState<Method>((['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(row.method) ? row.method : 'GET') as Method)
  const [path, setPath] = useState(row.path ?? pathFromName(row.name))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await routesApi.createStubs(pid, { app_id: appId, routes: [{ method, path, name: row.name }] })
      if (!res.created.length) {
        setError(`${method} ${path} already exists`)
        return
      }
      await invalidate.routesChanged(qc, pid)
      setOpen(false)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={<Button variant="outline" size="xs" className="h-5 px-1.5 text-[11px]" data-testid={`${testIdPrefix}-create-stub-${row.name}`} />}
      >
        Create stub
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72">
        <form
          className="flex flex-col gap-2.5"
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
        >
          <div className="font-medium">
            Stub for <span className="font-mono text-[12px]">api.{row.name}</span>
          </div>
          <Segmented<Method>
            value={method}
            onChange={setMethod}
            size="xs"
            aria-label="Method"
            options={(['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as Method[]).map((m) => ({ key: m, label: m }))}
          />
          <Input value={path} onChange={(e) => setPath(e.target.value)} className="h-7 font-mono text-xs" data-testid="builder-stub-path" />
          <div className="text-[11.5px] leading-4 text-muted-foreground">
            It answers 501 until you implement it in the backend. The client gets <span className="font-mono">api.{row.name}</span> back.
          </div>
          {error && (
            <div className="text-[11.5px] text-destructive" role="alert" data-testid="builder-stub-error">
              {error}
            </div>
          )}
          <div className="flex justify-end">
            <Button size="xs" type="submit" disabled={busy || !path.startsWith('/')} data-testid="builder-stub-submit">
              Create stub
            </Button>
          </div>
        </form>
      </PopoverContent>
    </Popover>
  )
}

function OpRowView({
  row,
  observed,
  pid,
  appId,
  onUse,
}: {
  row: OpRow
  observed: { ok?: ObservedCall; last?: ObservedCall }
  pid: string
  appId: string
  onUse: (row: OpRow) => void
}) {
  const u = row.usage[0]
  const status = u?.status
  const serverOnly = row.route?.auth === 'api_key'
  const isStub = row.route?.handler.type === 'stub' || status === 'stub'
  const where = u ? `${u.file.split('/').pop()}:${u.line}` : null
  let right: React.ReactNode = null
  let line2: React.ReactNode = null
  let state = row.status as string
  if (row.status === 'missing') {
    state = 'missing'
    right = <CreateStubPopover pid={pid} appId={appId} row={row} />
    line2 = (
      <>
        <span className="font-mono">{where}</span>
        <span>·</span>
        <span className="text-destructive">missing</span>
      </>
    )
  } else if (row.status === 'used') {
    if (status === 'changed') {
      state = 'changed'
      right = <Badge variant="warning">changed</Badge>
      line2 = (
        <>
          <span className="font-mono">{where}</span>
          <span>·</span>
          <span className="truncate text-warning-text">{u?.message || 'response changed since this version'}</span>
        </>
      )
    } else if (isStub) {
      state = 'stub'
      right = <Badge variant="stub">stub</Badge>
      line2 = (
        <>
          <span className="font-mono">{where}</span>
          <span>·</span>
          <span>answers 501 until implemented</span>
        </>
      )
    } else if (observed.ok) {
      state = 'ok'
      right = <CircleCheck className="size-3.5 text-success" aria-label="called in preview" />
      line2 = (
        <>
          <span className="font-mono">{where}</span>
          <span>·</span>
          <span className="truncate">
            {observed.ok.status} · {formatMs(observed.ok.ms)} in preview
          </span>
        </>
      )
    } else if (observed.last) {
      state = 'failed'
      right = <StatusDot tone="err" aria-label="failed in preview" />
      line2 = (
        <>
          <span className="font-mono">{where}</span>
          <span>·</span>
          <span className="truncate text-destructive">{observed.last.status} in preview</span>
        </>
      )
    } else {
      state = 'not-called'
      right = <StatusDot tone="idle" aria-label="not called yet" />
      line2 = (
        <>
          <span className="font-mono">{where}</span>
          <span>·</span>
          <span>not called yet</span>
        </>
      )
    }
    if (row.usage.length > 1) line2 = <>{line2}<span className="shrink-0">+{row.usage.length - 1}</span></>
  } else {
    right = serverOnly ? (
      <Badge variant="neutral">server-only</Badge>
    ) : (
      <span className="flex items-center gap-1">
        {isStub && <Badge variant="stub">stub</Badge>}
        <Button variant="ghost" size="xs" className="h-5 px-1.5 text-[11px]" onClick={() => onUse(row)} data-testid={`builder-op-use-${row.name}`}>
          Use
        </Button>
      </span>
    )
    line2 = <span>{serverOnly ? 'api_key routes are not in the browser client' : 'available · not used'}</span>
  }
  return (
    <div
      className="flex flex-col gap-[3px] border-b border-border/65 px-3 py-[9px] last:border-b-0"
      data-testid={`builder-op-${row.name}`}
      data-state={state}
    >
      <div className="flex min-w-0 items-center gap-2">
        <MethodBadge method={row.method} />
        <Tooltip>
          <TooltipTrigger render={<span className="min-w-0 flex-1 truncate font-mono text-[12px] text-foreground" />}>
            {row.name}
          </TooltipTrigger>
          <TooltipContent>
            {row.method} {row.path ?? '(unknown path)'}
          </TooltipContent>
        </Tooltip>
        {right}
      </div>
      <div className="flex min-w-0 items-center gap-1.5 overflow-hidden whitespace-nowrap pl-[52px] text-[11.5px] text-muted-foreground">
        {line2}
      </div>
    </div>
  )
}

/** A short excerpt of the generated client for the first used (or first) operation. */
function clientExcerpt(client: string, op: string | null, revision: number | null): string {
  const head = `/*! @generated · spec rev ${revision ?? 'draft'} */`
  if (!client || !op) return head
  const P = op[0].toUpperCase() + op.slice(1)
  const typeLine = client.split('\n').find((l) => l.startsWith(`export type ${P}Response`))
  const sig = client.split('\n').find((l) => l.trimStart().startsWith(`${op}: (`))
  const args = sig ? (sig.match(/^\s*\w+: \(([^)]*)\)/)?.[1] ?? '') : ''
  const needsBody = /\bbody\??:/.test(args)
  const needsParams = /\bparams:/.test(args)
  const call = `const r = await api.${op}(${needsParams ? '{ params }' : ''}${needsParams && needsBody ? ', ' : ''}${needsBody ? 'body' : ''})`
  return [head, typeLine ? typeLine.replace(/^export /, '') : null, call].filter(Boolean).join('\n')
}

export function usageSnippet(op: string | null): string {
  return [`import { api } from './api/client'`, '', op ? `const data = await api.${op}()` : `// api.<operation>(...)`].join('\n')
}

export interface BackendPanelProps {
  pid: string
  appId: string
  projectName: string
  projectSlug: string
  healthTone: DotTone
  env: string
  routes: Route[] | undefined
  routesLoading: boolean
  usage: ApiUsage[] | undefined
  usageLoading: boolean
  usageError: unknown
  onRetryUsage: () => void
  onRefresh: () => void
  client: string | undefined
  revision: number | null
  onOpenClient: () => void
  onUse: (op: string, route: Route | null) => void
}

export function useOpCounts(routes: Route[] | undefined, usage: ApiUsage[] | undefined) {
  return useMemo(() => {
    const rows = buildRows(routes ?? [], usage ?? [])
    const used = rows.filter((r) => r.status === 'used').length
    return { rows, used, total: (routes ?? []).length }
  }, [routes, usage])
}

export function BackendPanel(p: BackendPanelProps) {
  const { rows, used, total } = useOpCounts(p.routes, p.usage)
  const ok = usePreviewSession((s) => s.ok)
  const byRoute = usePreviewSession((s) => s.byRoute)
  const firstUsed = rows.find((r) => r.status === 'used' && r.route)?.name ?? rows.find((r) => r.route && r.route.auth !== 'api_key')?.name ?? null
  const loading = p.routesLoading || (p.usageLoading && !p.usage)

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="builder-backend-panel">
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border pl-3.5 pr-2.5">
        <PlugZap className="size-3.5 text-muted-foreground" />
        <span className="font-semibold">Backend</span>
        <div className="flex-1" />
        <Tooltip>
          <TooltipTrigger render={<Button variant="ghost" size="icon-xs" onClick={p.onRefresh} aria-label="Refresh backend" data-testid="builder-backend-refresh" />}>
            <RefreshCw />
          </TooltipTrigger>
          <TooltipContent>Refresh routes and usage</TooltipContent>
        </Tooltip>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div
          className="m-3 rounded-[10px] border border-success/25 bg-success/5 p-3"
          data-testid="builder-connection-card"
        >
          <div className="flex items-center gap-2">
            <StatusDot tone={p.healthTone} />
            <span className="truncate font-medium">{p.projectName}</span>
            <div className="flex-1" />
            {p.revision != null && <Badge variant="secondary">rev {p.revision}</Badge>}
          </div>
          <div className="mt-1.5 truncate font-mono text-[11.5px] text-muted-foreground">/api/p/{p.projectSlug}</div>
          <div className="mt-1.5 text-[11.5px] leading-4 text-muted-foreground">
            Typed client regenerates when routes change. Preview uses <b className="font-medium text-foreground">{p.env}</b>.
          </div>
        </div>

        <div className="flex items-center px-3 pb-1.5 pt-0.5">
          <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">Operations</span>
          <div className="flex-1" />
          {!loading && total > 0 && (
            <span className="text-[11.5px] text-muted-foreground" data-testid="builder-ops-count">
              {used} of {total} used
            </span>
          )}
        </div>
        <div className="border-t border-border">
          {loading ? (
            <div className="flex flex-col gap-3 p-3" data-testid="builder-ops-loading">
              {[0, 1, 2].map((i) => (
                <div key={i} className="flex flex-col gap-1.5">
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="ml-[52px] h-3 w-28" />
                </div>
              ))}
            </div>
          ) : p.usageError && !p.usage ? (
            <div className="flex items-center gap-2 px-3 py-3 text-xs" data-testid="builder-usage-error">
              <span className="flex-1 text-destructive">Couldn't load API usage: {errorMessage(p.usageError)}</span>
              <Button variant="outline" size="xs" onClick={p.onRetryUsage}>
                Retry
              </Button>
            </div>
          ) : total === 0 && rows.length === 0 ? (
            <div className="px-3 py-4 text-xs leading-5 text-muted-foreground" data-testid="builder-no-routes">
              This project has no routes. Generate a backend first.{' '}
              <Link
                to="/p/$project/backend/$module"
                params={{ project: p.projectSlug, module: '~new' }}
                className="font-medium text-primary hover:underline"
                data-testid="builder-no-routes-link"
              >
                Open Backend
              </Link>
            </div>
          ) : (
            rows.map((r) => (
              <OpRowView
                key={r.key}
                row={r}
                observed={{ ok: r.route ? ok[r.route.id] : undefined, last: r.route ? byRoute[r.route.id] : undefined }}
                pid={p.pid}
                appId={p.appId}
                onUse={(row) => p.onUse(row.name, row.route)}
              />
            ))
          )}
        </div>

        {total > 0 && (
          <>
            <div className="flex items-center px-3 pb-1.5 pt-3.5">
              <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">api/client.ts</span>
              <div className="flex-1" />
              <Button
                variant="ghost"
                size="xs"
                data-testid="builder-copy-usage"
                onClick={() => {
                  void navigator.clipboard
                    .writeText(usageSnippet(firstUsed))
                    .then(() => toast.success('Usage copied'))
                    .catch(() => toast.error("Couldn't copy"))
                }}
              >
                <Copy /> Copy usage
              </Button>
            </div>
            <button
              type="button"
              onClick={p.onOpenClient}
              className="mx-3 mb-3 block w-[calc(100%-24px)] overflow-hidden whitespace-pre rounded-lg border border-border bg-code-bg px-2.5 py-2 text-left font-mono text-[11px] leading-[17px] text-foreground hover:border-ring"
              title="Open api/client.ts"
              data-testid="builder-client-preview"
            >
              {p.client === undefined ? 'Loading client…' : clientExcerpt(p.client, firstUsed, p.revision)}
            </button>
          </>
        )}
      </div>
    </div>
  )
}

/** The 44px rail below 1280px: plug (opens the panel), `used/total`, client.ts. */
export function BackendRail({
  used,
  total,
  open,
  onToggle,
  onOpenClient,
}: {
  used: number
  total: number
  open: boolean
  onToggle: () => void
  onOpenClient: () => void
}) {
  return (
    <div className="flex flex-col items-center gap-1.5 py-2" data-testid="builder-backend-rail">
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon"
              className={cn(open && 'bg-muted text-foreground')}
              onClick={onToggle}
              aria-label="Backend"
              aria-pressed={open}
              data-testid="builder-rail-backend"
            />
          }
        >
          <PlugZap />
        </TooltipTrigger>
        <TooltipContent side="right">Backend</TooltipContent>
      </Tooltip>
      <Badge variant="secondary" className="h-[18px] px-[5px] text-[10px]" data-testid="builder-rail-usage">
        {used}/{total}
      </Badge>
      <Tooltip>
        <TooltipTrigger
          render={<Button variant="ghost" size="icon" onClick={onOpenClient} aria-label="api/client.ts" data-testid="builder-rail-client" />}
        >
          <FileCode2 />
        </TooltipTrigger>
        <TooltipContent side="right">api/client.ts</TooltipContent>
      </Tooltip>
    </div>
  )
}
