/**
 * Trace waterfall (UI-DESIGN §3.6, §4.7): a sandbox bar plus one row per broker call. Long
 * detail lines truncate with an ellipsis and carry the full text as a tooltip (H2). Denied
 * calls show their `error_code` and, when given, an inline fix.
 */
import { Box, TerminalSquare } from 'lucide-react'
import type { BrokerCall, RunTrace } from '@/shared/api/types'
import { StatusDot } from '@/shared/components/ui/status-dot'
import { formatMs } from '@/features/project/lib/format'
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

export function TraceWaterfall({
  trace,
  renderFix,
  className,
}: {
  trace: RunTrace | null | undefined
  /** inline fix for a denied/failed call (e.g. missing_secret → CreateSecretPopover) */
  renderFix?: (call: BrokerCall) => React.ReactNode
  className?: string
}) {
  if (!trace) return <div className="text-xs text-muted-foreground">No trace recorded.</div>
  const sb = trace.sandbox
  const calls = trace.broker_calls ?? []
  const total = Math.max(sb?.duration_ms ?? 0, ...calls.map((c) => c.duration_ms ?? 0), 1)
  return (
    <div className={cn('flex flex-col gap-1.5 text-xs', className)} data-testid="trace">
      {sb && (
        <Row
          icon={<Box className="size-3.5 text-muted-foreground" />}
          label="Sandbox"
          ms={sb.duration_ms}
          total={total}
          tone={sb.exit_code === 0 && !sb.timed_out ? 'neutral' : 'err'}
          testId="trace-sandbox"
        />
      )}
      {calls.map((c) => (
        <div key={c.id} className="flex flex-col gap-0.5" data-testid="trace-call" data-status={c.status}>
          <Row
            icon={<StatusDot tone={c.status === 'ok' ? 'ok' : 'err'} className="shadow-none" />}
            label={callLabel(c)}
            ms={c.duration_ms}
            total={total}
            tone={c.status === 'ok' ? 'primary' : 'err'}
          />
          <div className="truncate pl-5 font-mono text-[11px] text-muted-foreground" title={callDetail(c)}>
            {callDetail(c)}
          </div>
          {c.status !== 'ok' && (
            <div className="pl-5 text-[11.5px] text-destructive" data-testid="trace-denied">
              <span className="font-mono">{c.error_code ?? 'error'}</span>
              {c.error ? <span className="text-muted-foreground"> · {c.error}</span> : null}
              {renderFix?.(c)}
            </div>
          )}
        </div>
      ))}
      {sb && (
        <div className="flex items-center gap-2 pt-1 text-[11.5px] text-muted-foreground">
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
  total,
  tone,
  testId,
}: {
  icon: React.ReactNode
  label: string
  ms: number
  total: number
  tone: 'neutral' | 'primary' | 'err'
  testId?: string
}) {
  const w = Math.max(2, Math.round((ms / total) * 100))
  return (
    <div className="flex items-center gap-2" data-testid={testId}>
      <span className="inline-flex w-3.5 justify-center">{icon}</span>
      <span className="w-32 shrink-0 truncate font-mono text-[11.5px]" title={label}>
        {label}
      </span>
      <div className="relative h-1.5 min-w-0 flex-1 rounded-full bg-muted">
        <div
          className={cn(
            'absolute inset-y-0 left-0 rounded-full',
            tone === 'neutral' && 'bg-muted-foreground/40',
            tone === 'primary' && 'bg-primary',
            tone === 'err' && 'bg-destructive',
          )}
          style={{ width: `${w}%` }}
        />
      </div>
      <span className="w-16 shrink-0 text-right tabular-nums text-muted-foreground">{formatMs(ms)}</span>
    </div>
  )
}
