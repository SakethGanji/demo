import { useEffect, useRef } from 'react'
import { useWidth } from '@/shared/hooks/useWidth'
import { reducedMotion, usePrefs } from '@/shared/lib/prefs'

/** The pipeline as one string. Done spans are taut, the active span carries a travelling wave, the rest is unstrung. */
export function PipelineString({ stages, phase, notes }: { stages: string[]; phase: number; notes: string[] }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const wave = useRef<SVGPathElement>(null)
  const { prefs } = usePrefs()
  const pad = 56, y = 30
  const x = (i: number) => pad + (i / (stages.length - 1)) * (w - pad * 2)
  // phase p is the work that ends at node p: the span between node p-1 and node p (node 0 is its own load pulse)
  useEffect(() => {
    const el = wave.current
    if (!el) return
    const a = phase === 0 ? x(0) - 40 : x(phase - 1), b = x(Math.min(phase, stages.length - 1))
    const still = prefs.motion === 'Still' || reducedMotion()
    let raf = 0
    const draw = (now: number) => {
      let d = ''
      for (let i = 0; i <= 60; i++) {
        const f = i / 60
        d += `${i ? 'L' : 'M'}${(a + f * (b - a)).toFixed(1)},${(y + Math.sin(Math.PI * f) * 9 * Math.sin(f * 18 - (still ? 0 : now / 110))).toFixed(2)}`
      }
      el.setAttribute('d', d)
      if (!still) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [phase, w, prefs.motion, stages.length])
  return (
    <div ref={ref} className="pipe-string">
      <svg width={w} height={84} aria-hidden style={{ display: 'block' }}>
        <defs><linearGradient id="ps-g" x1="0" x2="1"><stop offset="0" stopColor="#2DD4BF" /><stop offset="1" stopColor="#38BDF8" /></linearGradient></defs>
        <line x1={x(0)} x2={x(stages.length - 1)} y1={y} y2={y} stroke="#334155" strokeWidth={1.5} strokeDasharray="4 5" />
        {phase > 0 && <line x1={x(0)} x2={x(Math.min(phase, stages.length) - 1)} y1={y} y2={y} stroke="#2DD4BF" strokeWidth={2} />}
        {phase < stages.length && <path ref={wave} fill="none" stroke="url(#ps-g)" strokeWidth={2.25} strokeLinecap="round" />}
        {stages.map((s, i) => {
          const done = i < phase, active = i === phase
          return (
            <g key={s}>
              {active && <circle cx={x(i)} cy={y} r={11} fill="#38BDF8" opacity={0.2} className="live-dot" />}
              <circle cx={x(i)} cy={y} r={6} fill={done ? '#2DD4BF' : active ? '#38BDF8' : '#0A1120'} stroke={done ? '#2DD4BF' : active ? '#38BDF8' : '#475569'} strokeWidth={2} />
              <text x={x(i)} y={y + 28} textAnchor="middle" className="ps-name" style={{ fill: done || active ? '#F8FAFC' : '#94A3B8' }}>{s}</text>
              <text x={x(i)} y={y + 44} textAnchor="middle" className="ps-note">{notes[i]}</text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
