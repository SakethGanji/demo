/**
 * The inline quick preview under a run row (`runs.html`): meta, a mini trace and, for a failed run, its stack
 * trace, side by side so the detail stays short. Opens with a height + opacity transition. "Open details"
 * (or double-click / ⇧↵ on the row) goes to the run sheet route.
 */
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight } from 'lucide-react'
import { useProjectCtx } from '@/features/project/ProjectContext'
import type { RunListItem } from '@/shared/api/types'
import { runsApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import { Button } from '@/shared/components/ui/button'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { StackTrace } from '@/shared/components/ui/stack-trace'
import { Tip } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'
import { GhostCopy } from './GhostCopy'
import { TraceWaterfall } from './TraceWaterfall'
import { isUserFrame, moduleOfFrame } from './RunErrorBox'
import { exactTime, isFailed } from '../lib/runStats'
import { isRunning, runError, shortId, triggerType } from '../lib/runLabels'

export function RunInline({
  run,
  source,
  colSpan,
  onOpenDetails,
  onOpenFrame,
}: {
  run: RunListItem
  source: string
  colSpan: number
  onOpenDetails: () => void
  onOpenFrame: (module: string, line: number) => void
}) {
  const { pid } = useProjectCtx()
  const failed = isFailed(run.status)
  const q = useQuery({
    queryKey: qk.run(pid, run.id),
    queryFn: () => runsApi.get(pid, run.id),
    refetchInterval: (query) => (isRunning(query.state.data?.status) ? 2_000 : false),
  })
  const [shown, setShown] = useState(false)
  useEffect(() => {
    const raf = requestAnimationFrame(() => setShown(true))
    return () => cancelAnimationFrame(raf)
  }, [])
  const d = q.data
  const err = d ? runError(d) : null
  const resp = d?.metadata?.response as Record<string, unknown> | undefined
  const code = resp && typeof resp.status === 'number' ? resp.status : null
  const sb = d?.trace?.sandbox
  const calls = d?.trace?.broker_calls ?? []
  const hasStack = !!err?.traceback
  return (
    <tr
      data-testid="run-detail"
      data-run-id={run.id}
      className={cn(
        '[&>td]:p-0 [&>td]:shadow-[inset_2px_0_0_var(--primary)]',
        failed ? 'bg-destructive/6 [&>td]:shadow-[inset_2px_0_0_var(--destructive)]' : 'bg-primary/7',
        'border-b border-hairline',
      )}
    >
      <td colSpan={colSpan}>
        <div
          className={cn(
            'grid transition-[grid-template-rows,opacity] duration-(--d-base) ease-(--ease-io)',
            shown ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0',
          )}
        >
          <div className="min-h-0 overflow-hidden">
            <div
              className={cn(
                'grid cursor-default gap-x-5 gap-y-4 px-4 pt-1 pb-4',
                hasStack
                  ? 'grid-cols-[212px_minmax(0,.8fr)_minmax(0,1.2fr)_auto]'
                  : 'grid-cols-[220px_minmax(0,560px)_1fr] gap-x-6',
              )}
              onClick={(e) => e.stopPropagation()}
              onDoubleClick={(e) => e.stopPropagation()}
            >
              <dl className="m-0 grid grid-cols-[64px_minmax(0,1fr)] content-start gap-y-1.5 text-[12px]" data-testid="run-detail-meta">
                <dt className="text-muted-foreground">Run</dt>
                <dd className="m-0 min-w-0 truncate font-mono text-[11.5px]" title={run.id}>
                  {shortId(run.id)}
                </dd>
                <dt className="text-muted-foreground">Status</dt>
                <dd className={cn('m-0 min-w-0', failed && 'text-destructive')}>{code ?? run.status}</dd>
                <dt className="text-muted-foreground">Started</dt>
                <dd className="m-0 min-w-0">{exactTime(run.start_time)}</dd>
                <dt className="text-muted-foreground">Trigger</dt>
                <dd className="m-0 min-w-0 truncate" title={`${source} · ${triggerType(run)}`}>
                  {source}
                  {run.environment && (
                    <>
                      {' · '}
                      <span className="font-mono text-[11.5px]">{run.environment}</span>
                    </>
                  )}
                </dd>
              </dl>

              <div className="min-w-0 text-xs">
                <div className="mb-1 flex items-center gap-1.5 text-muted-foreground">
                  <b className="font-semibold text-foreground">Trace</b>
                  <span className="truncate">
                    {q.isPending
                      ? '· loading'
                      : `· ${calls.length ? `${calls.length} node call${calls.length > 1 ? 's' : ''}` : 'stopped before any node call'}${hasStack || !sb ? '' : ` · sandbox ${Math.round(sb.duration_ms)} ms`}`}
                  </span>
                </div>
                {q.isPending ? (
                  <Skeleton className="h-12 w-full" />
                ) : d?.trace ? (
                  <TraceWaterfall trace={d.trace} startedAt={run.start_time} compact />
                ) : (
                  <div className="text-muted-foreground">No trace recorded.</div>
                )}
                {err && !hasStack && (
                  <div className="mt-2 truncate font-mono text-[11.5px] leading-normal text-destructive" title={`${err.type}: ${err.message}`}>
                    {err.type}: {err.message}
                  </div>
                )}
              </div>

              {hasStack && err?.traceback && (
                <StackTrace
                  traceback={err.traceback}
                  isOwn={isUserFrame}
                  onOpenFrame={(f) => onOpenFrame(moduleOfFrame(f.file), f.line)}
                  testId="run-detail-stack"
                />
              )}

              <div className="flex items-start justify-end gap-0.5">
                <GhostCopy text={run.id} what="run ID" testId="run-copy-id" />
                <Tip label="Open details" kbd="⇧↵">
                  <Button variant="ghost" size="icon-xs" aria-label="Open details" data-testid="run-open-details" onClick={onOpenDetails}>
                    <ArrowUpRight />
                  </Button>
                </Tip>
              </div>
            </div>
          </div>
        </div>
      </td>
    </tr>
  )
}

