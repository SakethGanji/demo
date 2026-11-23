/**
 * Trace waterfall for the test panel (backend mockup): a ruler, the sandbox span and one 22px row per node
 * call, placed at its offset with a drop line up to the sandbox bar. Fixed row height lets the drop lines be
 * pure CSS. Denied calls keep their error line and inline fix. Bars grow in once per new response.
 */
import { useEffect, useMemo, useState } from 'react'
import { Box } from 'lucide-react'
import type { BrokerCall, RunTrace } from '@/shared/api/types'
import { StatusDot } from '@/shared/components/ui/status-dot'
import { formatMs } from '@/features/project/lib/format'
import { cn } from '@/shared/lib/utils'
import { callDetail, callLabel, callOffsets } from './traceModel'

/** ticks at a round step so the ruler reads 0 · 50 · 100 ms */
function ticks(span: number): number[] {
  const steps = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000]
  const step = steps.find((s) => span / s <= 3) ?? 10000
  const out: number[] = []
  for (let t = 0; t <= span; t += step) out.push(t)
  return out
}

export function TraceView({
  trace,
  renderFix,
  replayKey,
}: {
  trace: RunTrace | null | undefined
  renderFix?: (call: BrokerCall) => React.ReactNode
  /** changes with each new response so the bars grow in again */
  replayKey?: string
}) {
  const [grown, setGrown] = useState(false)
  useEffect(() => {
    setGrown(false)
    const raf = requestAnimationFrame(() => requestAnimationFrame(() => setGrown(true)))
    return () => cancelAnimationFrame(raf)
  }, [replayKey, trace])

  const calls = trace?.broker_calls ?? []
  const sb = trace?.sandbox
  const offsets = useMemo(() => callOffsets(trace), [trace])
  if (!trace) return <div className="px-1 py-1 text-[12px] text-muted-foreground">No trace recorded.</div>
  const span = Math.max(sb?.duration_ms ?? 0, ...calls.map((c, i) => (offsets[i] ?? 0) + (c.duration_ms ?? 0)), 1)
  const pc = (v: number) => `${((v / span) * 100).toFixed(2)}%`
  const unit = ticks(span)
  const last = unit[unit.length - 1]

  return (
    <div className="flex flex-col gap-1" data-testid="trace">
      <div className="grid grid-cols-[150px_1fr_56px] gap-x-2 text-[12px]">
        {/* ruler */}
        <span />
        <div className="relative mb-0.5 h-4 shadow-[inset_0_-1px_0_var(--hairline)]">
          {unit.map((t) => (
            <span key={t} className="absolute bottom-0 h-1 border-l border-art-line" style={{ left: pc(t) }}>
              <span
                className={cn(
                  'absolute bottom-[5px] font-mono text-[10px] leading-none whitespace-nowrap text-muted-foreground',
                  t ? 'left-0 -translate-x-1/2' : '-left-px',
                )}
              >
                {t}
                {t === last ? ' ms' : ''}
              </span>
            </span>
          ))}
        </div>
        <span />

        {/* sandbox */}
        {sb && (
          <div className="contents" data-testid="trace-sandbox" data-state={sb.exit_code === 0 && !sb.timed_out ? 'ok' : 'err'}>
            <span className="flex h-[22px] items-center gap-1.5 whitespace-nowrap">
              <Box className="size-3.5 text-muted-foreground" />
              Sandbox
            </span>
            <div className="relative h-[22px]">
              <span className="absolute inset-x-0 top-2 h-1.5 rounded-full bg-foreground/6" />
              <i
                className={cn(
                  'absolute top-2 h-1.5 origin-left rounded-full transition-transform duration-(--d-slow) ease-(--ease-out)',
                  sb.exit_code === 0 && !sb.timed_out ? 'bg-foreground/26' : 'bg-destructive',
                )}
                style={{ left: 0, width: pc(sb.duration_ms), transform: grown ? 'scaleX(1)' : 'scaleX(0)' }}
              />
            </div>
            <span className="h-[22px] text-right leading-[22px] text-foreground tabular-nums">{formatMs(sb.duration_ms)}</span>
          </div>
        )}

        {/* node calls */}
        {calls.map((c, i) => {
          const ok = c.status === 'ok'
          const detail = callDetail(c)
          return (
            <div key={c.id} className="contents" data-testid="trace-call" data-status={c.status}>
              <span className="flex h-[22px] min-w-0 items-center gap-1.5 whitespace-nowrap" title={detail}>
                <StatusDot tone={ok ? 'ok' : 'err'} flat />
                <span className="truncate">{callLabel(c)}</span>
                <span className="sr-only">{detail}</span>
              </span>
              <div className="relative h-[22px]">
                <span
                  className="absolute w-px bg-art-line transition-opacity duration-(--d-base) ease-(--ease-out)"
                  style={{
                    left: pc(offsets[i] ?? 0),
                    top: `${14 - 22 * (i + 1)}px`,
                    height: `${22 * (i + 1) - 6}px`,
                    opacity: grown ? 1 : 0,
                    transitionDelay: `${i * 40 + 120}ms`,
                  }}
                />
                <i
                  className={cn(
                    'absolute top-2 h-1.5 origin-left rounded-full transition-transform duration-(--d-slow) ease-(--ease-out)',
                    ok ? 'bg-brand-1' : 'bg-destructive',
                  )}
                  style={{
                    left: pc(offsets[i] ?? 0),
                    width: pc(Math.max(c.duration_ms ?? 0, span * 0.012)),
                    transform: grown ? 'scaleX(1)' : 'scaleX(0)',
                    transitionDelay: `${(i + 1) * 40}ms`,
                  }}
                />
              </div>
              <span className="h-[22px] text-right leading-[22px] text-muted-foreground tabular-nums">{formatMs(c.duration_ms)}</span>
              {!ok && (
                <div className="col-span-3 -mt-0.5 mb-1 pl-5 text-[11.5px] text-destructive" data-testid="trace-denied">
                  <span className="font-mono">{c.error_code ?? 'error'}</span>
                  {c.error ? <span className="text-muted-foreground"> · {c.error}</span> : null}
                  {renderFix?.(c)}
                </div>
              )}
            </div>
          )
        })}
      </div>
      {sb && (
        <div className="flex items-center gap-2 pt-1 text-[11.5px] text-muted-foreground">
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
