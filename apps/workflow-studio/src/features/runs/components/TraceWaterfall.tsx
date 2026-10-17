/**
 * Trace waterfall (DESIGN §8, `runs.html`): a ruler, a sandbox bar plus one 22px row per broker call, laid on
 * a shared time axis with a drop line from each call up to the sandbox. Long detail lines truncate with an
 * ellipsis and carry the full text as a tooltip (H2). Denied calls show their `error_code` and, when given,
 * an inline fix. `compact` (the inline run preview) leaves out the call details and the stdio footer.
 * Also used by the Backend test panel.
 */
import { useEffect, useState } from 'react'
import { Box, TerminalSquare } from 'lucide-react'
import type { BrokerCall, RunTrace } from '@/shared/api/types'
import { StatusDot } from '@/shared/components/ui/status-dot'
import { formatMs, parseTs } from '@/features/project/lib/format'
import { cn } from '@/shared/lib/utils'

function callLabel(c: BrokerCall): string {
  const p = (c.parameters ?? c.params_redacted ?? {}) as Record<string, unknown>
  if (c.node_type === 'DataTable') return `DataTable.${String(p.operation ?? 'call')}`
  return c.node_type
}

function callDetail(c: BrokerCall): string {
  const p = (c.parameters ?? c.params_redacted ?? {}) as Record<string, unknown>
  if (c.node_type === 'HttpRequest') {
    const parts = [`${String(p.method ?? 'GET')} ${String(p.url ?? '')}`]
    if (p.headers) parts.push(JSON.stringify(p.headers))
    return parts.join(' · ')
  }
  if (c.node_type === 'DataTable') return `table ${String(p.table ?? '')}${p.data ? ` · ${JSON.stringify(p.data)}` : ''}`
  return JSON.stringify(p)
}

const pc = (v: number, total: number) => `${Math.max(0, Math.min(100, (v / total) * 100)).toFixed(2)}%`

interface Placed {
  call: BrokerCall
  start: number
  ms: number
}

/** Calls are logged when they finish, so the start is that moment minus the duration (offset from the run start). */
function place(calls: BrokerCall[], startedAt?: string | null): Placed[] {
  const t0 = parseTs(startedAt)?.getTime()
  const ends = calls.map((c) => parseTs(c.created_at)?.getTime())
  const raw = calls.map((c, i) => (t0 != null && ends[i] != null ? Math.max(0, ends[i]! - t0 - c.duration_ms) : null))
  if (raw.every((v) => v != null)) return calls.map((call, i) => ({ call, start: raw[i]!, ms: call.duration_ms }))
  // no run start known (test panel): lay the calls out one after another in their logged order
  let at = 0
  return calls.map((call) => {
    const p = { call, start: at, ms: call.duration_ms }
    at += call.duration_ms
    return p
  })
}

export function TraceWaterfall({
  trace,
  renderFix,
  className,
  compact,
  startedAt,
}: {
  trace: RunTrace | null | undefined
  /** inline fix for a denied/failed call (e.g. missing_secret → CreateSecretPopover) */
  renderFix?: (call: BrokerCall) => React.ReactNode
  className?: string
  /** the inline preview: no call details, no stdio footer, narrower name column */
  compact?: boolean
  /** the run's `start_time`, to place each call at its real offset */
  startedAt?: string | null
}) {
  const [on, setOn] = useState(false)
  useEffect(() => {
    const raf = requestAnimationFrame(() => setOn(true))
    return () => cancelAnimationFrame(raf)
  }, [])
  if (!trace) return <div className="text-xs text-muted-foreground">No trace recorded.</div>
  const sb = trace.sandbox
  const calls = place(trace.broker_calls ?? [], startedAt)
  const total = Math.max(sb?.duration_ms ?? 0, ...calls.map((c) => c.start + c.ms), 1)
  const cols = compact ? 'grid-cols-[96px_minmax(0,1fr)_44px]' : 'grid-cols-[132px_minmax(0,1fr)_52px]'
  const half = Math.round(total / 2 / 10) * 10 || Math.round(total / 2)
  return (
    <div className={cn('flex flex-col text-xs', className)} data-testid="trace">
      <div className={cn('grid gap-x-2', cols)}>
        {/* ruler */}
        <span />
        <div className="relative mb-0.5 h-4">
          {[
            [0, 'left-0', ''],
            [half, '', '-translate-x-1/2'],
            [total, 'right-0', ''],
          ].map(([t, , tr], i, a) => (
            <span
              key={i}
              className="absolute bottom-0 h-1 border-l border-art-line"
              style={{ left: i === a.length - 1 ? undefined : pc(t as number, total), right: i === a.length - 1 ? 0 : undefined }}
            >
              <span
                className={cn(
                  'absolute bottom-1.5 font-mono text-[10px] leading-none whitespace-nowrap text-muted-foreground',
                  i === 0 ? 'left-[-1px]' : i === a.length - 1 ? 'right-0' : tr,
                )}
              >
                {Math.round(t as number)}
                {i === a.length - 1 ? ' ms' : ''}
              </span>
            </span>
          ))}
        </div>
        <span />
        {sb && (
          <Row
            icon={<Box className="size-3.5 text-muted-foreground" />}
            label="Sandbox"
            ms={sb.duration_ms}
            start={0}
            total={total}
            tone={sb.exit_code === 0 && !sb.timed_out ? 'neutral' : 'err'}
            testId="trace-sandbox"
            idx={0}
            on={on}
            strong
          />
        )}
        {calls.map(({ call: c, start, ms }, i) => (
          <Row
            key={c.id}
            icon={<StatusDot tone={c.status === 'ok' ? 'ok' : 'err'} flat />}
            label={callLabel(c)}
            ms={ms}
            start={start}
            total={total}
            tone={c.status === 'ok' ? 'primary' : 'err'}
            idx={i + 1}
            on={on}
            drop={sb ? i + 1 : i}
            testId={compact ? 'trace-call' : undefined}
            status={compact ? c.status : undefined}
          />
        ))}
      </div>
      {!compact && calls.length > 0 && (
        <div className="mt-2 flex flex-col gap-1.5">
          {calls.map(({ call: c }) => (
            <div key={c.id} className="flex min-w-0 flex-col gap-0.5" data-testid="trace-call" data-status={c.status}>
              <div className="truncate pl-3.5 font-mono text-[11px] text-muted-foreground" title={callDetail(c)}>
                <span className="text-foreground">{callLabel(c)}</span> · {callDetail(c)}
              </div>
              {c.status !== 'ok' && (
                <div className="pl-3.5 text-[11.5px] text-destructive" data-testid="trace-denied">
                  <span className="font-mono">{c.error_code ?? 'error'}</span>
                  {c.error ? <span className="text-muted-foreground"> · {c.error}</span> : null}
                  {renderFix?.(c)}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {!compact && sb && (
        <div className="flex items-center gap-2 pt-2 text-[11.5px] text-muted-foreground">
          <TerminalSquare className="size-3.5" />
          {sb.stdout || sb.stderr ? (
            <span>
              stdout {sb.stdout ? `${sb.stdout.length} chars` : 'empty'} · stderr {sb.stderr ? `${sb.stderr.length} chars` : 'empty'}
            </span>
          ) : (
            <span>stdout empty</span>
          )}
          <span>· exit {sb.exit_code ?? '—'}</span>
          {sb.timed_out && <span className="text-destructive">· timed out</span>}
          {sb.truncated && <span>· truncated</span>}
        </div>
      )}
    </div>
  )
}

function Row({
  icon,
  label,
  ms,
  start,
  total,
  tone,
  testId,
  status,
  idx,
  on,
  drop,
  strong,
}: {
  icon: React.ReactNode
  label: string
  ms: number
  start: number
  total: number
  tone: 'neutral' | 'primary' | 'err'
  testId?: string
  status?: string
  idx: number
  on: boolean
  /** how many rows above the sandbox row this call sits (draws the drop line) */
  drop?: number
  strong?: boolean
}) {
  const left = pc(start, total)
  const width = `max(3px, ${pc(ms, total)})`
  return (
    <div className="contents" data-testid={testId} data-status={status}>
      <span className="flex h-[22px] items-center gap-1.5 whitespace-nowrap" title={label}>
        <span className="inline-flex w-3.5 justify-center">{icon}</span>
        <span className="truncate text-[12px]">{label}</span>
      </span>
      <div className="relative h-[22px]">
        <span className="absolute inset-x-0 top-2 h-1.5 rounded-chip bg-foreground/6" />
        {drop != null && drop > 0 && (
          <span
            className={cn('absolute w-px bg-art-line transition-opacity duration-(--d-base) ease-(--ease-out)', on ? 'opacity-100' : 'opacity-0')}
            style={{ left, top: `calc(14px - 22px * ${drop})`, height: `calc(22px * ${drop} - 6px)`, transitionDelay: `${idx * 40 + 140}ms` }}
          />
        )}
        <i
          className={cn(
            'absolute top-2 h-1.5 origin-left rounded-chip transition-transform duration-(--d-slow) ease-(--ease-out)',
            on ? 'scale-x-100' : 'scale-x-0',
            tone === 'neutral' && 'bg-foreground/26',
            tone === 'primary' && 'bg-primary',
            tone === 'err' && 'bg-destructive',
          )}
          style={{ left, width, transitionDelay: `${idx * 40 + 60}ms` }}
        />
      </div>
      <span className={cn('h-[22px] text-right leading-[22px] tabular-nums', strong ? 'text-foreground' : 'text-muted-foreground')}>{formatMs(ms)}</span>
    </div>
  )
}
