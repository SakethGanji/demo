import { useEffect, useRef } from 'react'
import { useWidth } from '@/shared/hooks/useWidth'
import { reducedMotion, usePrefs } from '@/shared/lib/prefs'

/** Boosting: the residual is a wave that every new tree damps; trees stack up as frets on the round string. */
export function Boosting({ frac, M, active, done }: { frac: number; M: number; active: boolean; done: boolean }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const res = useRef<SVGPathElement>(null)
  const live = useRef({ frac, active }); live.current = { frac, active }
  const { prefs } = usePrefs()
  const H = 150, mid = 70
  useEffect(() => {
    const still = prefs.motion === 'Still' || reducedMotion()
    let raf = 0
    const draw = (now: number) => {
      const t = now / 1000, f0 = live.current.frac
      const amp = live.current.active || f0 > 0 ? 52 * Math.exp(-f0 * 3.2) + 1.5 : 0
      let d = ''
      for (let i = 0; i <= 140; i++) {
        const f = i / 140
        const yv = amp * (Math.sin(f * 9 + t * 2.2) * 0.55 + Math.sin(f * 23 - t * 3.4) * 0.3 * (1 - f0) + Math.sin(f * 47 + t * 5) * 0.15 * (1 - f0) ** 2)
        d += `${i ? 'L' : 'M'}${(f * w).toFixed(1)},${(mid + (still ? yv * 0.6 : yv)).toFixed(2)}`
      }
      res.current?.setAttribute('d', d)
      if (!still) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [w, prefs.motion])
  const m = Math.round(frac * M)
  const shown = Math.min(14, Math.floor(m / 4))
  return (
    <div ref={ref} className="boost">
      <div className="boost-head">
        <span className="cls-name">Residual<small>y − F<sub>m</sub>(x)</small></span>
        <span className="cls-read">{(0.69 * Math.exp(-frac * 3.2) + 0.21).toFixed(3)}<small>{done ? '● converged' : active ? 'logloss, falling' : 'waiting'}</small></span>
      </div>
      <svg width={w} height={H} style={{ display: 'block' }} aria-hidden>
        <line x1={0} x2={w} y1={mid} y2={mid} stroke="#334155" strokeDasharray="4 5" />
        <path ref={res} fill="none" stroke={done ? '#2DD4BF' : '#38BDF8'} strokeWidth={2} strokeLinecap="round" />
        <line x1={0} x2={w} y1={H - 16} y2={H - 16} stroke="#334155" strokeWidth={1.5} />
        <line x1={0} x2={frac * w} y1={H - 16} y2={H - 16} stroke="#2DD4BF" strokeWidth={2} />
        {Array.from({ length: 31 }, (_, i) => <line key={i} x1={(i / 30) * w} x2={(i / 30) * w} y1={H - 12} y2={H - (i % 5 === 0 ? 4 : 7)} stroke={i / 30 <= frac ? '#2DD4BF' : '#475569'} />)}
        <circle cx={Math.max(4, Math.min(w - 4, frac * w))} cy={H - 16} r={5} fill="#0A1120" stroke="#38BDF8" strokeWidth={2} />
      </svg>
      <div className="boost-foot">
        <span className="ps-note" style={{ fontFamily: 'var(--font-mono)' }}>m = {m} of {M} trees</span>
        <div className="trees">
          {Array.from({ length: shown }, (_, i) => (
            <svg key={i} width="26" height="22" viewBox="0 0 26 22" className={i === shown - 1 && active ? 'tree new' : 'tree'} aria-hidden>
              <path d="M13 3L6 11M13 3L20 11M6 11L3 19M6 11L9 19M20 11L17 19M20 11L23 19" stroke="currentColor" strokeWidth="1.25" fill="none" />
              {[[13, 3], [6, 11], [20, 11], [3, 19], [9, 19], [17, 19], [23, 19]].map(([cx, cy], j) => <circle key={j} cx={cx} cy={cy} r={j < 3 ? 2 : 1.6} fill="currentColor" />)}
            </svg>
          ))}
          {m > 56 && <span className="ps-note">+{m - 56} earlier</span>}
        </div>
      </div>
    </div>
  )
}
