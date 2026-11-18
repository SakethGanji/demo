import { useEffect, useRef } from 'react'
import { useWidth } from '@/shared/hooks/useWidth'
import { reducedMotion, usePrefs } from '@/shared/lib/prefs'

export type RunState = { id: string; label: string; status: 'Training' | 'Completed' | 'Queued' | 'Failed'; progress: number; readout: string }

/** One run as one string: frets are folds, the node is progress, the live span is a travelling wave. */
export function RunString({ run, folds = 5, onClick }: { run: RunState; folds?: number; onClick?: () => void }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const live = useRef<SVGPathElement>(null)
  const { prefs } = usePrefs()
  const H = 26, mid = 13, x = Math.max(4, Math.min(w - 4, run.progress * w))
  const x0 = (Math.floor(run.progress * folds - 1e-6) / folds) * w
  useEffect(() => {
    if (run.status !== 'Training' || !live.current) return
    const el = live.current
    const still = prefs.motion === 'Still' || reducedMotion()
    let raf = 0
    const draw = (now: number) => {
      const t = now / 1000
      let d = ''
      const n = 40
      for (let i = 0; i <= n; i++) {
        const f = i / n
        const y = mid + Math.sin(Math.PI * f) * 5 * Math.sin(f * 14 - (still ? 0 : t * 6))
        d += `${i ? 'L' : 'M'}${(x0 + f * (x - x0)).toFixed(1)},${y.toFixed(2)}`
      }
      el.setAttribute('d', d)
      if (!still) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [w, run.progress, run.status, prefs.motion])
  const ink = run.status === 'Failed' ? 'var(--status-danger)' : 'var(--accent-primary)'
  const glyph = { Training: '', Completed: '●', Queued: '○', Failed: '✕' }[run.status]
  return (
    <button className="run-string" onClick={onClick}>
      <span className="rs-id">{run.id}</span>
      <span className="rs-label" title={run.label}>{run.label}</span>
      <div ref={ref} className="rs-track">
        <svg width={w} height={H} style={{ display: 'block', overflow: 'visible' }} aria-hidden>
          <line x1={run.status === 'Failed' ? x + 14 : x} x2={w} y1={mid} y2={mid} stroke="var(--border-track)" strokeWidth={1.5} strokeDasharray="4 4" />
          {run.status === 'Training'
            ? <><line x1={0} x2={x0} y1={mid} y2={mid} stroke={ink} strokeWidth={2} /><path ref={live} fill="none" stroke="var(--accent-secondary)" strokeWidth={2} strokeLinecap="round" /></>
            : run.progress > 0 && <line x1={0} x2={x} y1={mid} y2={mid} stroke={ink} strokeWidth={2} />}
          {Array.from({ length: folds + 1 }, (_, i) => <line key={i} x1={(i / folds) * w} x2={(i / folds) * w} y1={mid + 4} y2={mid + 9} stroke="var(--border-track)" />)}
          <circle cx={1} cy={mid} r={3} fill={run.progress > 0 ? ink : 'var(--bg-surface)'} stroke={run.progress > 0 ? ink : 'var(--border-track)'} strokeWidth={1.5} />
          {run.status === 'Training' && <circle cx={x} cy={mid} r={8} fill="var(--accent-secondary)" opacity={0.18} className="live-dot" />}
          {run.status === 'Failed'
            ? <path d={`M${x + 2},${mid - 5} l8,10 m0,-10 l-8,10`} stroke="var(--status-danger)" strokeWidth={2} strokeLinecap="round" />
            : <circle cx={x} cy={mid} r={4.5} fill={run.status === 'Queued' ? 'var(--bg-surface)' : run.status === 'Training' ? 'var(--accent-secondary)' : ink} stroke={run.status === 'Queued' ? 'var(--border-track)' : 'var(--bg-surface)'} strokeWidth={1.5} />}
        </svg>
      </div>
      <span className={`rs-status s-${run.status.toLowerCase()}`}>{run.status === 'Training' ? <i className="live-dot" /> : <span aria-hidden>{glyph}</span>}{run.status}</span>
      <span className="rs-read" title={run.readout}>{run.readout}</span>
    </button>
  )
}
