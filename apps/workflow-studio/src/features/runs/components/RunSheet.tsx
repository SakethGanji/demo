/**
 * Run sheet (UI-DESIGN §3.6, `runs.png`): a 580px sheet over the Runs list at
 * `/p/$project/runs/$executionId`. Status, target, ids, `Open in editor`, the error box, the trace
 * with inline fixes for denied calls, Request/Response (`metadata.request|response`, redacted and
 * capped at 16 KB), stdout/stderr and the release number.
 */
import { useRef, useState } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, Code2, Workflow } from 'lucide-react'
import { Well } from '@/shared/components/ui/surface'
import { GhostCopy } from './GhostCopy'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useModules, useRoutes } from '@/features/project/hooks/useProjectData'
import { formatMs, timeAgo } from '@/features/project/lib/format'
import { moduleNameOf } from '@/features/project/lib/routes'
import { runsApi, releasesApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import { errorMessage, isApiError } from '@/shared/api/http'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/shared/components/ui/sheet'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { RunErrorBox } from './RunErrorBox'
import { TraceWaterfall } from './TraceWaterfall'
import { BrokerFix } from './BrokerFix'
import { exactTime } from '../lib/runStats'
import { isRunning, runError, runTarget, shortId, triggerType } from '../lib/runLabels'
import type { RunDetail } from '@/shared/api/types'
import { cn } from '@/shared/lib/utils'

export function RunSheet({ executionId, onClose }: { executionId: string | undefined; onClose: () => void }) {
  const focusRef = useRef<HTMLDivElement>(null)
  return (
    <Sheet open={!!executionId} onOpenChange={(o) => !o && onClose()}>
      <SheetContent
        side="right"
        showCloseButton
        initialFocus={focusRef}
        overlayClassName="bg-transparent supports-backdrop-filter:backdrop-blur-none"
        className="gap-0 overflow-hidden rounded-xl p-0 data-[side=right]:inset-y-3 data-[side=right]:right-3 data-[side=right]:h-auto data-[side=right]:w-[580px] data-[side=right]:max-w-[calc(100vw-24px)] data-[side=right]:sm:max-w-[580px]"
        data-testid="run-sheet"
      >
        <div ref={focusRef} tabIndex={-1} className="flex min-h-0 flex-1 flex-col outline-none">
          {executionId && <RunSheetBody executionId={executionId} />}
        </div>
      </SheetContent>
    </Sheet>
  )
}

function RunSheetBody({ executionId }: { executionId: string }) {
  const { pid } = useProjectCtx()
  const q = useQuery({
    queryKey: qk.run(pid, executionId),
    queryFn: () => runsApi.get(pid, executionId),
    refetchInterval: (query) => (isRunning(query.state.data?.status) ? 2_000 : false),
  })
  if (q.isPending)
    return (
      <div className="flex flex-col gap-3 p-5" data-testid="run-sheet-loading">
        <SheetTitle className="sr-only">Run {executionId}</SheetTitle>
        <Skeleton className="h-6 w-64" />
        <Skeleton className="h-4 w-80" />
        <Skeleton className="mt-4 h-24 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  if (q.isError)
    return (
      <div className="flex flex-col items-start gap-2 p-5" data-testid="run-sheet-error">
        <SheetTitle>Run {shortId(executionId)}</SheetTitle>
        <p className="text-[13px] text-muted-foreground">
          {isApiError(q.error, undefined, 404) ? 'This run no longer exists (the engine keeps the newest 100).' : errorMessage(q.error)}
        </p>
        {!isApiError(q.error, undefined, 404) && (
          <Button size="sm" variant="outline" onClick={() => q.refetch()}>
            Retry
          </Button>
        )}
      </div>
    )
  return <RunDetailView run={q.data} />
}

function statusTone(code: number | null, status: string) {
  if (code != null) return code >= 500 ? 'err' : code >= 400 ? 'warn' : 'ok'
  if (status === 'success') return 'ok'
  if (status === 'waiting') return 'warn'
  if (isRunning(status)) return 'running'
  return 'err'
}

function responseStatus(run: RunDetail): number | null {
  const r = run.metadata?.response as Record<string, unknown> | undefined
  const s = r && (r.status ?? r.status_code)
  return typeof s === 'number' ? s : null
}

function RunDetailView({ run }: { run: RunDetail }) {
  const { pid, slug } = useProjectCtx()
  const navigate = useNavigate()
  const routes = useRoutes(pid)
  const modules = useModules(pid)
  const target = runTarget(run)
  const err = runError(run)
  const code = responseStatus(run)
  const tone = statusTone(code, run.status)
  const releaseId = run.trigger?.release_id ?? null
  const embedded = (run as { release?: { number: number | null } | null }).release
  const release = useQuery({
    queryKey: qk.release(pid, releaseId ?? ''),
    queryFn: () => releasesApi.get(pid, releaseId!),
    enabled: !!releaseId && embedded?.number == null,
  })
  const releaseNo = embedded?.number ?? release.data?.number ?? null

  // Open in editor: error.line in a user module, else the python route's handler line.
  const route = routes.data?.find((r) => r.id === run.route_id)
  const routeModule = route ? moduleNameOf(route, modules.data) : null
  const editor: { module: string; line?: number } | null =
    err?.module && err.line != null
      ? { module: err.module, line: err.line }
      : route && routeModule && route.handler.type === 'python'
        ? { module: routeModule, line: route.handler.line }
        : null

  const goToLine = (line: number, module: string | null | undefined) => {
    const m = module ?? routeModule
    if (!m) return
    navigate({ to: '/p/$project/backend/$module', params: { project: slug, module: m }, search: { line } })
  }

  const trace = run.trace
  const sb = trace?.sandbox
  const calls = trace?.broker_calls ?? []
  const request = run.metadata?.request
  const response = run.metadata?.response
  const output = Object.entries(run.node_data ?? {}).filter(([, v]) => v.length > 0)

  return (
    <>
      <div className="hairline-b px-5 pt-4 pb-3 pr-12">
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            data-testid="run-status"
            data-status={run.status}
            className={cn(
              'inline-flex h-[22px] shrink-0 items-center rounded-md px-2 font-mono text-xs font-semibold',
              tone === 'ok' && 'bg-success/11 text-success-text',
              tone === 'warn' && 'bg-warning/13 text-warning-text',
              tone === 'err' && 'bg-destructive/10 text-destructive',
              tone === 'running' && 'bg-primary/11 text-method-post',
            )}
          >
            {code ?? run.status}
          </span>
          <SheetTitle className="flex min-w-0 items-center gap-2 text-[14px]">
            {target.method ? (
              <>
                <MethodBadge method={target.method} />
                <span className="truncate font-mono" data-testid="run-path">
                  {target.path}
                </span>
              </>
            ) : (
              <>
                <Workflow className="size-4 shrink-0 text-muted-foreground" />
                <span className="truncate font-mono" data-testid="run-path">
                  {target.workflow}
                </span>
              </>
            )}
          </SheetTitle>
        </div>
        <SheetDescription render={<div />} className="sr-only">
          Run {run.id}
        </SheetDescription>
        <Well className="mt-3 grid grid-cols-[64px_minmax(0,1fr)_64px_minmax(0,1fr)] items-center gap-x-3 gap-y-1.5 px-3 py-2.5 text-[12px]" data-testid="run-meta">
          <span className="text-muted-foreground">Run</span>
          <span className="flex min-w-0 items-center gap-1 font-mono text-[11.5px]">
            <span className="truncate" title={run.id} data-testid="run-id">
              {shortId(run.id)}
            </span>
            <GhostCopy text={run.id} what="run ID" testId="run-copy-id" className="size-5" />
          </span>
          <span className="text-muted-foreground">Trigger</span>
          <span className="truncate font-mono text-[11.5px]" data-testid="run-trigger">
            {triggerType(run)}
          </span>
          <span className="text-muted-foreground">Env</span>
          <span className="truncate font-mono text-[11.5px]" data-testid="run-env">
            {run.environment ?? '—'}
          </span>
          <span className="text-muted-foreground">Duration</span>
          <span className="tabular-nums" data-testid="run-duration">
            {run.status === 'waiting' ? 'waiting for approval' : isRunning(run.status) ? 'running…' : formatMs(run.duration_ms)}
          </span>
          <span className="text-muted-foreground">Started</span>
          <span className="truncate" title={exactTime(run.start_time)}>
            {exactTime(run.start_time)} · {timeAgo(run.start_time)}
          </span>
          {releaseId ? (
            <>
              <span className="text-muted-foreground">Release</span>
              <span data-testid="run-release">{releaseNo != null ? `release #${releaseNo}` : 'release'}</span>
            </>
          ) : null}
        </Well>
        {!target.method && run.workflow_id && (
          <Link
            to="/p/$project/workflows/$workflowId"
            params={{ project: slug, workflowId: run.workflow_id }}
            search={{ execution: run.id }}
            className="mt-2.5 mr-3 inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
            data-testid="run-open-in-editor"
          >
            <Workflow className="size-3.5" /> Open in editor
          </Link>
        )}
        {editor && (
          <Link
            to="/p/$project/backend/$module"
            params={{ project: slug, module: editor.module }}
            search={editor.line != null ? { line: editor.line } : {}}
            className="mt-2.5 inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
            data-testid="run-open-editor"
          >
            <Code2 className="size-3.5" /> Open in editor
          </Link>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4" data-testid="run-sheet-panel">
        <div className="flex flex-col gap-4">
          {err && (
            <RunErrorBox
              error={err}
              onGoToLine={err.module || routeModule ? goToLine : undefined}
              onOpenFrame={(m, line) => goToLine(line, m)}
              goToLabel={err.line != null ? `${err.module ?? routeModule}.py line ${err.line}` : undefined}
            />
          )}

          {(sb || calls.length > 0) && (
            <Section
              title="Trace"
              meta={[sb ? `sandbox ${formatMs(sb.duration_ms)}` : null, `${calls.length} node call${calls.length === 1 ? '' : 's'}`]
                .filter(Boolean)
                .join(' · ')}
              defaultOpen
              testId="run-trace"
            >
              <Well className="p-3">
              <TraceWaterfall trace={trace} startedAt={run.start_time} renderFix={(c) => <BrokerFix call={c} environment={run.environment} />} />
              </Well>
            </Section>
          )}

          <Section
            title="Request"
            meta={request == null ? 'not recorded' : isTruncated(request) ? 'truncated at 16 KB' : undefined}
            defaultOpen={request != null}
            disabled={request == null}
            testId="run-request"
          >
            <KeyValues value={request} />
          </Section>

          <Section
            title="Response"
            meta={response == null ? 'not recorded' : oneLine(responseBody(response))}
            defaultOpen={false}
            disabled={response == null}
            testId="run-response"
          >
            {isTruncated(response) && (
              <Badge variant="warning" className="mb-2">
                truncated at 16 KB
              </Badge>
            )}
            <KeyValues value={response} />
          </Section>

          {output.length > 0 && !target.method && (
            <Section title="Output" meta={`${output.length} node${output.length === 1 ? '' : 's'}`} defaultOpen testId="run-output">
              <div className="flex flex-col gap-2">
                {output.map(([node, items]) => (
                  <div key={node} className="min-w-0">
                    <div className="mb-1 text-xs font-medium">{node}</div>
                    <Pre>{pretty(items.length === 1 ? items[0].json : items.map((i) => i.json))}</Pre>
                  </div>
                ))}
              </div>
            </Section>
          )}

          <Section
            title="stdout / stderr"
            meta={!sb ? 'no sandbox' : sb.stdout || sb.stderr ? undefined : 'empty'}
            defaultOpen={!!(sb?.stdout || sb?.stderr)}
            disabled={!sb || (!sb.stdout && !sb.stderr)}
            testId="run-stdio"
          >
            {sb?.stdout ? (
              <>
                <div className="mb-1 text-[11px] text-muted-foreground">stdout</div>
                <Pre testId="run-stdout">{sb.stdout}</Pre>
              </>
            ) : null}
            {sb?.stderr ? (
              <>
                <div className="mt-2 mb-1 text-[11px] text-muted-foreground">stderr</div>
                <Pre testId="run-stderr">{sb.stderr}</Pre>
              </>
            ) : null}
          </Section>

          <p className="text-[11.5px] text-muted-foreground">
            Request/response are stored redacted and capped at 16 KB each. Secret values show as ***.
          </p>
        </div>
      </div>
    </>
  )
}

function isTruncated(v: unknown): boolean {
  return !!v && typeof v === 'object' && (v as Record<string, unknown>).truncated === true
}

function responseBody(v: unknown): unknown {
  if (v && typeof v === 'object' && 'body' in (v as object)) return (v as Record<string, unknown>).body
  return v
}

function pretty(v: unknown): string {
  if (typeof v === 'string') return v
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
}

function oneLine(v: unknown): string {
  const s = typeof v === 'string' ? v : JSON.stringify(v)
  if (!s) return ''
  return s.length > 90 ? `${s.slice(0, 90)}…` : s
}

function Pre({ children, testId, bare }: { children: React.ReactNode; testId?: string; bare?: boolean }) {
  return (
    <pre
      className={cn(bare ? 'mt-0.5' : 'well p-2.5', ' max-h-64 overflow-auto font-mono text-[11.5px] leading-[18px] whitespace-pre-wrap break-words')}
      data-scroll-x="allowed"
      data-testid={testId}
    >
      {children}
    </pre>
  )
}

/** `metadata.request|response` as a key/value grid (method, path, query, headers, body…). */
function KeyValues({ value }: { value: unknown }) {
  if (value == null) return null
  if (typeof value !== 'object' || Array.isArray(value)) return <Pre>{pretty(value)}</Pre>
  const entries = Object.entries(value as Record<string, unknown>).filter(([k]) => k !== 'truncated')
  return (
    <Well className="grid grid-cols-[90px_minmax(0,1fr)] gap-x-3 gap-y-1.5 p-3 text-[12px]">
      {entries.map(([k, v]) => {
        const simple = v == null || typeof v !== 'object'
        const flat =
          !simple && k !== 'body' && !Array.isArray(v) && Object.values(v as object).every((x) => x == null || typeof x !== 'object')
        return (
          <div key={k} className="contents" data-testid={`kv-${k}`}>
            <div className="pt-0.5 text-muted-foreground">{k}</div>
            {simple ? (
              <div className="min-w-0 break-words font-mono">{v == null ? '—' : String(v)}</div>
            ) : flat && Object.keys(v as object).length <= 8 ? (
              <div className="min-w-0 break-words font-mono">
                {Object.keys(v as object).length === 0
                  ? '—'
                  : Object.entries(v as Record<string, unknown>)
                      .map(([a, b]) => `${a}: ${String(b)}`)
                      .join(' · ')}
              </div>
            ) : (
              <Pre bare>{pretty(v)}</Pre>
            )}
          </div>
        )
      })}
    </Well>
  )
}

function Section({
  title,
  meta,
  defaultOpen = true,
  disabled = false,
  children,
  testId,
}: {
  title: string
  meta?: string
  defaultOpen?: boolean
  disabled?: boolean
  children?: React.ReactNode
  testId?: string
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section data-testid={testId} data-open={open && !disabled}>
      <button
        type="button"
        className="flex w-full min-w-0 items-center gap-1.5 text-left text-[13px] disabled:cursor-default"
        onClick={() => setOpen((o) => !o)}
        disabled={disabled}
        aria-expanded={open && !disabled}
      >
        <ChevronRight
          className={cn('size-3.5 shrink-0 text-muted-foreground transition-transform', open && !disabled && 'rotate-90')}
        />
        <span className="shrink-0 font-medium">{title}</span>
        {meta && <span className="truncate text-xs text-muted-foreground">· {meta}</span>}
      </button>
      {open && !disabled && <div className="mt-2.5 pl-5">{children}</div>}
    </section>
  )
}
