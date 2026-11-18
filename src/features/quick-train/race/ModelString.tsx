import { useEffect, useId, useRef } from 'react'
import { useWidth } from '@/shared/hooks/useWidth'
import { TENSION, reducedMotion, usePrefs } from '@/shared/lib/prefs'

/**
 * One model as a string on the shared score axis. While it fits, the whole string rings (a standing wave, never
 * at rest). When it finishes, it is plucked once and settles taut from the left pin to its validation score
 * (x = where the score falls on the axis, right = better), with its fold scores as beads and μ ± σ as a band.
 */
export function ModelString({ status, x, folds, band, seed, height = 26, best = false }: {
  status: 'fitting' | 'done' | 'failed' | 'skipped'
  /** Score position in [0, 1] (1 = best end of the axis); null until done. */
  x: number | null
  folds?: number[]
  band?: [number, number] | null
  seed: number
  height?: number
  best?: boolean
}) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const path = useRef<SVGPathElement>(null)
  const { prefs } = usePrefs()
  const gid = `ms-${useId().replace(/:/g, '')}`
  const live = useRef({ status, x })
  live.current = { status, x }
  const settledAt = useRef<number | null>(null)

  useEffect(() => {
    if (status === 'done') settledAt.current = performance.now()
  }, [status])

  useEffect(() => {
    const still = prefs.motion === 'Still' || reducedMotion()
    const T = TENSION[prefs.tension]
    const mid = height / 2
    let raf = 0
    const draw = (now: number) => {
      const el = path.current
      if (!el) return
      const { status: st, x: xf } = live.current
      const t = now / 1000
      let d = ''
      if (st === 'fitting') {
        // ringing: fundamental + a few harmonics, phase-shifted per model so the strings don't move in lockstep
        const A = (mid - 2) * Math.min(1, T.amp)
        const n = 2 + (seed % 3)
        for (let i = 0; i <= 96; i++) {
          const f = i / 96
          const y = A * (Math.sin(Math.PI * n * f) * Math.cos(t * (6 + (seed % 5)) + seed) * 0.7
            + Math.sin(Math.PI * (n + 3) * f) * Math.sin(t * (11 + (seed % 7))) * 0.3)
          d += `${i ? 'L' : 'M'}${(f * w).toFixed(1)},${(mid + (still ? y * 0.4 : y)).toFixed(2)}`
        }
      } else {
        const end = Math.max(6, (xf ?? 1) * (w - 4))
        const since = settledAt.current == null ? 99 : (now - settledAt.current) / 1000
        const env = still ? 0 : Math.exp(-since / T.decay)
        const A = (mid - 3) * Math.min(1, T.amp) * env
        for (let i = 0; i <= 64; i++) {
          const f = i / 64
          const y = A * Math.sin(Math.PI * f) * Math.cos(2 * Math.PI * T.freq * since)
          d += `${i ? 'L' : 'M'}${(f * end).toFixed(1)},${(mid + y).toFixed(2)}`
        }
        el.setAttribute('d', d)
        if (env > 0.01) raf = requestAnimationFrame(draw)
        return
      }
      el.setAttribute('d', d)
      if (!still) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [status, x, w, height, seed, prefs.motion, prefs.tension])

  const mid = height / 2
  const end = Math.max(6, (x ?? 1) * (w - 4))
  const fitting = status === 'fitting'
  const failed = status === 'failed' || status === 'skipped'
  return (
    <div ref={ref} className="ms" style={{ height }} aria-hidden>
      <svg width={w} height={height} style={{ display: 'block', overflow: 'visible' }}>
        <defs><linearGradient id={gid} gradientUnits="userSpaceOnUse" x1={0} x2={Math.max(8, end)} y1={0} y2={0}><stop offset="0" stopColor="#2DD4BF" /><stop offset="1" stopColor="#38BDF8" /></linearGradient></defs>
        {!fitting && !failed && <line x1={end} x2={w} y1={mid} y2={mid} stroke="#334155" strokeWidth={1} strokeDasharray="2 4" />}
        {band && !fitting && <rect x={Math.max(0, band[0] * (w - 4))} width={Math.max(2, (band[1] - band[0]) * (w - 4))} y={mid - 7} height={14} rx={2} fill="rgba(56,189,248,0.14)" stroke="rgba(56,189,248,0.35)" strokeWidth={0.75} />}
        {failed
          ? <line x1={0} x2={w} y1={mid} y2={mid} stroke="#475569" strokeWidth={1.25} strokeDasharray="4 5" />
          : <path ref={path} fill="none" stroke={fitting ? '#38BDF8' : `url(#${gid})`} strokeWidth={best ? 2.6 : fitting ? 1.6 : 2} strokeLinecap="round" />}
        {!fitting && !failed && folds?.map((f, i) => <circle key={i} cx={f * (w - 4)} cy={mid + ((i % 2) * 2 - 1) * 4} r={2} fill="#F8FAFC" fillOpacity={0.85} stroke="#0A1120" strokeWidth={0.5} />)}
        <circle cx={2} cy={mid} r={3} fill={failed ? '#475569' : '#2DD4BF'} />
        {fitting && <circle cx={w - 2} cy={mid} r={3} fill="#2DD4BF" />}
        {!fitting && !failed && <circle cx={end} cy={mid} r={best ? 5 : 4} fill={best ? '#2DD4BF' : '#38BDF8'} stroke="#0A1120" strokeWidth={1.5} />}
      </svg>
    </div>
  )
}
