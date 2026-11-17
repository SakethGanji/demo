import { useEffect, useRef } from 'react'
import { useWidth } from '@/shared/hooks/useWidth'
import { TENSION, reducedMotion, usePrefs } from '@/shared/lib/prefs'

/** A taut string pinned at both ends. Change `pluck` to pluck it: a damped standing wave, then rest. */
export function PluckString({ pluck, at = 0.35, height = 14, stroke = 'url(#pluck-grad)', width: sw = 2, rest = 'var(--border-track)' }: { pluck: number; at?: number; height?: number; stroke?: string; width?: number; rest?: string }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const path = useRef<SVGPathElement>(null)
  const { prefs } = usePrefs()
  const first = useRef(true)
  useEffect(() => {
    const el = path.current!
    const mid = height / 2
    const flat = `M0,${mid} L${w},${mid}`
    el.setAttribute('d', flat)
    if (first.current) { first.current = false; return }
    if (prefs.motion === 'Still' || reducedMotion()) return
    const T = TENSION[prefs.tension]
    const A = (height / 2 - 1.5) * Math.min(1, T.amp)
    const t0 = performance.now()
    let raf = 0
    el.style.stroke = stroke
    const tick = (now: number) => {
      const t = (now - t0) / 1000
      const env = Math.exp(-t / T.decay)
      if (env < 0.02) { el.setAttribute('d', flat); el.style.stroke = ''; return }
      let d = ''
      for (let i = 0; i <= 48; i++) {
        const x = i / 48
        // fundamental plus the harmonic excited by where the string was plucked
        const y = A * env * (Math.sin(Math.PI * x) * Math.cos(2 * Math.PI * T.freq * t) * 0.75 + Math.sin(2 * Math.PI * x) * Math.cos(4 * Math.PI * T.freq * t) * (0.5 - at) * 0.9)
        d += `${i ? 'L' : 'M'}${(x * w).toFixed(1)},${(mid + y).toFixed(2)}`
      }
      el.setAttribute('d', d)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => { cancelAnimationFrame(raf); el.style.stroke = '' }
  }, [pluck, w])
  return (
    <div ref={ref} style={{ height, width: '100%' }} aria-hidden>
      <svg width={w} height={height} style={{ display: 'block', overflow: 'visible' }}>
        <defs><linearGradient id="pluck-grad" x1="0" x2="1"><stop offset="0" stopColor="#0D9488" /><stop offset="1" stopColor="#38BDF8" /></linearGradient></defs>
        <path ref={path} fill="none" stroke={rest} strokeWidth={sw} strokeLinecap="round" style={{ transition: 'stroke var(--duration-snap-max) var(--ease-pluck)' }} />
        <circle cx={1.5} cy={height / 2} r={2.5} fill="var(--accent-primary)" />
        <circle cx={w - 1.5} cy={height / 2} r={2.5} fill="var(--accent-primary)" />
      </svg>
    </div>
  )
}
