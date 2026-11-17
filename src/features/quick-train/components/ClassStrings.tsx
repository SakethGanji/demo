import { useEffect, useRef } from 'react'
import { useWidth } from '@/shared/hooks/useWidth'
import { reducedMotion, usePrefs } from '@/shared/lib/prefs'

/** One-vs-Rest: one string per classifier. It rings while its loss is high and settles taut as it converges. */
export function ClassStrings({ classes, conv, f1, active }: { classes: string[]; conv: number[]; f1: number[]; active: boolean }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const paths = useRef<(SVGPathElement | null)[]>([])
  const live = useRef({ conv, active }); live.current = { conv, active }
  const { prefs } = usePrefs()
  const rowH = Math.max(34, Math.min(56, 300 / classes.length))
  useEffect(() => {
    const still = prefs.motion === 'Still' || reducedMotion()
    let raf = 0
    const draw = (now: number) => {
      const t = now / 1000
      classes.forEach((_, k) => {
        const c = live.current.conv[k] ?? 0
        const amp = live.current.active ? (rowH / 2 - 2) * Math.pow(1 - c, 0.9) : 0
        const n = 2 + (k % 3)
        let d = ''
        for (let i = 0; i <= 80; i++) {
          const f = i / 80
          const yv = amp * (Math.sin(Math.PI * n * f) * Math.cos(t * (5 + k * 0.7)) * 0.8 + Math.sin(Math.PI * (n + 3) * f) * Math.sin(t * (9 + k)) * 0.35 * (1 - c))
          d += `${i ? 'L' : 'M'}${(f * w).toFixed(1)},${(rowH / 2 + (still ? yv * 0.5 : yv)).toFixed(2)}`
        }
        paths.current[k]?.setAttribute('d', d)
      })
      if (!still) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [w, classes.length, prefs.motion, rowH])
  return (
    <div className="cls-strings">
      {classes.map((c, k) => {
        const done = conv[k] >= 0.999, idle = !active && !done
        return (
          <div key={c} className="cls-row" style={{ height: rowH }}>
            <span className="cls-name">{c}<small>vs rest</small></span>
            <div ref={k === 0 ? ref : undefined} className="cls-track">
              <svg width={w} height={rowH} style={{ display: 'block', overflow: 'visible' }} aria-hidden>
                <path ref={(el) => { paths.current[k] = el }} fill="none" stroke={done ? '#2DD4BF' : idle ? '#475569' : '#38BDF8'} strokeWidth={done ? 2 : 1.75} strokeDasharray={idle ? '4 5' : undefined} strokeLinecap="round" />
                <circle cx={2} cy={rowH / 2} r={3} fill={idle ? '#475569' : '#2DD4BF'} /><circle cx={w - 2} cy={rowH / 2} r={3} fill={idle ? '#475569' : '#2DD4BF'} />
              </svg>
            </div>
            <span className="cls-read">{idle ? '—' : f1[k].toFixed(3)}<small>{done ? '● converged' : idle ? 'waiting' : `loss ${(0.69 * Math.pow(1 - conv[k], 1.3) + 0.08).toFixed(2)}`}</small></span>
          </div>
        )
      })}
    </div>
  )
}
