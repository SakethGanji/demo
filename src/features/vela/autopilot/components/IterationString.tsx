import { useEffect, useRef } from 'react'
import { useWidth } from '@/shared/hooks/useWidth'
import { reducedMotion, usePrefs } from '@/shared/lib/prefs'
import type { Step } from '../steps'

/** The run as one string. Kept steps are taut nodes, a dropped experiment hangs off as a dashed catenary,
 *  a question is a diamond, and the step in progress is a travelling wave. */
export function IterationString({ steps, shown, working, total }: { steps: Step[]; shown: number; working: boolean; total: number }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const wave = useRef<SVGPathElement>(null)
  const { prefs } = usePrefs()
  const pad = 52, y = 34
  const slots = Math.max(total, steps.length)
  // dropped branches do not take a slot on the main string
  let col = -1
  const pos = steps.map((s) => { if (s.kind !== 'branch') col++; return col })
  const cols = Math.max(slots - steps.filter((s) => s.kind === 'branch').length, col + 1, 2)
  const x = (c: number) => pad + (c / (cols - 1)) * (w - pad * 2)
  const lastKept = (() => { for (let i = Math.min(shown, steps.length) - 1; i >= 0; i--) if (steps[i].kind !== 'branch') return pos[i]; return 0 })()
  const next = steps[shown]
  useEffect(() => {
    const el = wave.current
    if (!el || !working || !next) return
    const a = x(lastKept), bx = next.kind === 'branch' ? x(lastKept) + (x(1) - x(0)) * 0.5 : x(pos[shown])
    const by = next.kind === 'branch' ? y + 44 : y
    const still = prefs.motion === 'Still' || reducedMotion()
    let raf = 0
    const draw = (now: number) => {
      let d = ''
      for (let i = 0; i <= 50; i++) {
        const f = i / 50
        d += `${i ? 'L' : 'M'}${(a + f * (bx - a)).toFixed(1)},${(y + f * (by - y) + Math.sin(Math.PI * f) * 8 * Math.sin(f * 16 - (still ? 0 : now / 110))).toFixed(2)}`
      }
      el.setAttribute('d', d)
      if (!still) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [shown, working, w, prefs.motion, steps.length])
  return (
    <div ref={ref} className="pipe-string iter-string">
      <svg width={w} height={118} aria-hidden style={{ display: 'block' }}>
        <defs><linearGradient id="it-g" x1="0" x2="1"><stop offset="0" stopColor="#2DD4BF" /><stop offset="1" stopColor="#38BDF8" /></linearGradient></defs>
        <line x1={x(0)} x2={x(cols - 1)} y1={y} y2={y} stroke="#334155" strokeWidth={1.5} strokeDasharray="4 5" />
        <line x1={x(0)} x2={x(lastKept)} y1={y} y2={y} stroke="#2DD4BF" strokeWidth={2} />
        {working && next && <path ref={wave} fill="none" stroke="url(#it-g)" strokeWidth={2.25} strokeLinecap="round" />}
        {steps.slice(0, shown).map((s, i) => {
          if (s.kind === 'branch') {
            const px = x(pos[i]), bx = px + (x(1) - x(0)) * 0.5
            return (
              <g key={s.id}>
                <path d={`M${px},${y} C${px + 40},${y + 2} ${px + 26},${y + 44} ${bx},${y + 44}`} fill="none" stroke="#64748B" strokeWidth={1.5} strokeDasharray="4 4" />
                <circle cx={bx} cy={y + 44} r={5} fill="#0A1120" stroke="#64748B" strokeWidth={1.5} />
                <text x={bx + 11} y={y + 42} className="ps-name" style={{ fill: '#94A3B8', fontWeight: 500 }}>{s.short}</text>
                <text x={bx + 11} y={y + 57} className="ps-note">dropped · {s.tried!.f1.toFixed(3)}</text>
              </g>
            )
          }
          const cx = x(pos[i])
          const isAsk = s.kind === 'ask', waiting = isAsk && !s.answer
          return (
            <g key={s.id}>
              {waiting && <circle cx={cx} cy={y} r={13} fill="#FBBF24" opacity={0.2} className="live-dot" />}
              {isAsk
                ? <rect x={cx - 6} y={y - 6} width={12} height={12} transform={`rotate(45 ${cx} ${y})`} fill={waiting ? '#FBBF24' : '#0A1120'} stroke={waiting ? '#FBBF24' : '#2DD4BF'} strokeWidth={2} />
                : <circle cx={cx} cy={y} r={6} fill="#2DD4BF" stroke="#2DD4BF" strokeWidth={2} />}
              <text x={cx} y={y - 16} textAnchor="middle" className="ps-name" style={{ fill: '#F8FAFC' }}>{s.short}</text>
              <text x={cx} y={y + 24} textAnchor="middle" className="ps-note">{isAsk ? (waiting ? 'needs you' : s.auto ? 'Vela decided' : 'you answered') : s.after!.f1 ? s.after!.f1.toFixed(3) : 'data'}</text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
