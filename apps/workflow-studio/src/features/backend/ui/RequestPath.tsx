/**
 * The request path (DESIGN §6, backend mockup): client → route → sandbox (the focal node) → the node calls the
 * handler made (DataTable / HttpRequest …) → their upstream. Drawn from the real trace, so the labels and the
 * timing are the run's. Wires are still when idle; packets fire once per real event: one into the sandbox when
 * Send starts, then one per recorded call (at its offset in the trace) and one back when the response lands.
 */
import { useEffect, useMemo, useRef } from 'react'
import type { RunTrace } from '@/shared/api/types'
import { pulseWire } from '@/shared/components/art/pulse'
import { cn } from '@/shared/lib/utils'
import { callEndpoint, callLabel, callOffsets } from './traceModel'

const METHOD_VAR: Record<string, string> = {
  GET: 'var(--method-get)',
  POST: 'var(--method-post)',
  PUT: 'var(--method-put)',
  PATCH: 'var(--method-patch)',
  DELETE: 'var(--method-delete)',
}

const MAX_LEAVES = 3
const ms = (n: number) => `${Math.round(n)} ms`
const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s)
const reduced = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

export function RequestPath({
  route,
  trace,
  sandboxMs,
  sending,
  resultKey,
  className,
}: {
  route: { method: string; path: string; name: string } | null
  trace: RunTrace | null | undefined
  sandboxMs: number | null
  sending: boolean
  /** changes when a new response lands (packets for the recorded calls fire once) */
  resultKey: string | null
  className?: string
}) {
  const root = useRef<SVGSVGElement>(null)
  const calls = useMemo(() => trace?.broker_calls ?? [], [trace])
  const offsets = useMemo(() => callOffsets(trace), [trace])
  const shown = calls.slice(0, MAX_LEAVES)
  const more = calls.length - shown.length
  const n = shown.length
  const leafY = (i: number) => (n === 1 ? 34 : n === 2 ? (i ? 58 : 12) : 10 + i * 24)
  const sb = sandboxMs ?? trace?.sandbox?.duration_ms ?? null
  const lit = useRef<Array<SVGGElement | null>>([])
  const sandbox = useRef<SVGGElement>(null)

  // Send: one packet into the sandbox; it glows until the response lands.
  useEffect(() => {
    if (!sending) return
    pulseWire(root.current, 'main')
    sandbox.current?.classList.add('a-glow')
    return () => sandbox.current?.classList.remove('a-glow')
  }, [sending])

  // Response: each call fires at its (compressed) offset in the trace, then the answer returns.
  const first = useRef(true)
  useEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    if (!resultKey) return
    const span = Math.max(sb ?? 1, 1)
    const timers: number[] = []
    shown.forEach((c, i) => {
      const at = Math.min(600, (offsets[i] / span) * 600)
      const dur = Math.max(160, Math.min(420, (c.duration_ms / span) * 600))
      timers.push(window.setTimeout(() => pulseWire(root.current, `b${i}`, { duration: 220 }), at))
      if (callEndpoint(c)) timers.push(window.setTimeout(() => pulseWire(root.current, `ep${i}`, { duration: 200 }), at + 220))
      if (!reduced()) {
        timers.push(window.setTimeout(() => lit.current[i]?.setAttribute('data-on', ''), at + 200))
        timers.push(window.setTimeout(() => lit.current[i]?.removeAttribute('data-on'), at + 200 + dur))
      }
    })
    timers.push(window.setTimeout(() => pulseWire(root.current, 'back', { duration: 260 }), 720))
    return () => timers.forEach(clearTimeout)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resultKey])

  const method = route?.method ?? 'GET'
  const path = route?.path ?? '/'
  const mx = method.length > 3 ? 40 : 35
  const label = route
    ? `${method} ${path} runs in the sandbox${calls.length ? `, which made ${calls.length} node call${calls.length === 1 ? '' : 's'}: ${calls.map(callLabel).join(', ')}` : ''}.`
    : 'No route selected.'

  return (
    <svg
      ref={root}
      viewBox="0 0 520 72"
      className={cn('art block h-auto w-full overflow-visible [&_.a-leaf]:stroke-art-line [&_.a-leaf]:stroke-1 [&_[data-on]_.a-leaf]:stroke-brand-1 [&_[data-on]_text:first-of-type]:fill-foreground', className)}
      role="img"
      aria-label={label}
      data-testid="request-path"
    >
      <path data-wire="main" d="M9 34 H164" className={cn('a-wire', sending && 'a-hot')} />
      <path data-wire="back" d="M164 34 H9" fill="none" stroke="none" />
      {shown.map((c, i) => {
        const y = leafY(i)
        return (
          <g key={c.id}>
            <path data-wire={`b${i}`} d={`M232 34 C 245 34, 241 ${y}, 254 ${y}`} className="a-wire" />
            {callEndpoint(c) && <path data-wire={`ep${i}`} d={`M386 ${y} H398`} className="a-wire" strokeDasharray="2 2.5" />}
          </g>
        )
      })}

      <circle cx="5" cy="34" r="4" className="a-card" />
      <text x="0" y="65" className="a-label">
        client
      </text>

      <g transform="translate(20 23)">
        <title>{`${method} ${path}`}</title>
        <rect width="132" height="22" rx="6" className="a-card" />
        <text x="9" y="14.5" className="a-mono a-strong" style={{ fill: METHOD_VAR[method.toUpperCase()] ?? 'var(--muted-foreground)' }}>
          {method}
        </text>
        <text x={mx} y="14.5" className="a-mono">
          {clip(path, method.length > 3 ? 13 : 14)}
        </text>
      </g>
      <text x="86" y="65" textAnchor="middle" className="a-label">
        {clip(route?.name ?? '', 20)}
      </text>

      <g ref={sandbox}>
        <rect x="164" y="23" width="68" height="22" rx="6" className="a-card a-stroke-brand" />
        <text x="198" y="37.5" textAnchor="middle" className="a-mono a-strong">
          sandbox
        </text>
      </g>
      <text x="198" y="65" textAnchor="middle" className="a-label tabular-nums" data-testid="request-path-ms">
        {sb != null ? ms(sb) : '—'}
      </text>

      {shown.map((c, i) => {
        const y = leafY(i)
        const name = callLabel(c)
        const x = 273 + name.length * 6.3 + 8
        const bad = c.status !== 'ok'
        const kind = c.node_type
        return (
          <g
            key={c.id}
            ref={(el) => {
              lit.current[i] = el
            }}
            className="group/leaf"
            data-testid="request-path-call"
            data-status={c.status}
          >
            <title>{name}</title>
            {kind === 'DataTable' ? (
              <g>
                <path d={`M254 ${y - 5.5} v11 a6 2.2 0 0 0 12 0 v-11`} className="a-solid a-leaf" />
                <ellipse cx="260" cy={y - 5.5} rx="6" ry="2.2" className="a-solid a-leaf" />
              </g>
            ) : kind === 'HttpRequest' ? (
              <g>
                <circle cx="260" cy={y} r="6" className="a-solid a-leaf" />
                <ellipse cx="260" cy={y} rx="2.4" ry="6" className="a-leaf" fill="none" />
                <path d={`M254 ${y} h12`} className="a-leaf" fill="none" />
              </g>
            ) : (
              <rect x="254" y={y - 6} width="12" height="12" rx="3" className="a-solid a-leaf" />
            )}
            <text x="273" y={y + 3.5} className="a-mono" style={bad ? { fill: 'var(--destructive)' } : undefined}>
              {name}
            </text>
            <text x={x} y={y + 3.5} className="a-label tabular-nums">
              {ms(c.duration_ms)}
            </text>
          </g>
        )
      })}
      {more > 0 && (
        <text x="273" y="70" className="a-label">
          +{more} more
        </text>
      )}
      {shown.map((c, i) => {
        const ep = callEndpoint(c)
        if (!ep) return null
        const y = leafY(i)
        return (
          <g key={`ep-${c.id}`}>
            <circle cx="402" cy={y} r="3.5" className="a-card" />
            <text x="410" y={y + 3.5} className="a-mono" style={{ fill: 'var(--muted-foreground)' }}>
              {clip(ep, 14)}
            </text>
          </g>
        )
      })}
      {!n && sb != null && (
        <text x="254" y="37.5" className="a-label">
          no node calls
        </text>
      )}
    </svg>
  )
}
