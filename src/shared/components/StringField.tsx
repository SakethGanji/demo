import { useEffect, useRef, useState } from 'react'
import { useWidth } from '@/shared/hooks/useWidth'
import { TENSION, reducedMotion, usePrefs } from '@/shared/lib/prefs'

/** Five strings in a mirrored standing wave: the middle one still, each pair swinging outward equally.
 *  Moving the pointer across the field adds energy; its position picks the harmonic. */
export function StringField({ height = 120 }: { height?: number }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const paths = useRef<(SVGPathElement | null)[]>([])
  const state = useRef({ energy: 0.35, n: 2, lastX: 0 })
  const { prefs } = usePrefs()
  const [n, setN] = useState(2)
  useEffect(() => {
    const still = prefs.motion === 'Still' || reducedMotion()
    const T = TENSION[prefs.tension]
    const gap = 9, mid = height / 2
    let raf = 0
    const draw = (now: number) => {
      const s = state.current
      const t = now / 1000
      s.energy = Math.max(0.3, s.energy * 0.985)
      for (let k = 0; k < 5; k++) {
        const side = (k - 2) / 2
        let d = ''
        for (let i = 0; i <= 96; i++) {
          const x = i / 96
          const sw = still ? 0.6 : Math.cos(2 * Math.PI * (T.freq / 9) * t)
          const y = mid + side * gap * 2 + side * 20 * T.amp * s.energy * Math.sin(Math.PI * s.n * x) * sw
          d += `${i ? 'L' : 'M'}${(x * w).toFixed(1)},${y.toFixed(2)}`
        }
        paths.current[k]?.setAttribute('d', d)
      }
      if (!still) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [w, prefs.motion, prefs.tension, height])
  const onMove = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect()
    const x = (e.clientX - r.left) / r.width
    const s = state.current
    s.energy = Math.min(1, s.energy + Math.abs(e.clientX - s.lastX) / 260)
    s.lastX = e.clientX
    const nn = 1 + Math.min(5, Math.floor(x * 6))
    if (nn !== s.n) { s.n = nn; setN(nn) }
  }
  const inks = ['#14B8A6', '#2DD4BF', '#99F6E4', '#2DD4BF', '#14B8A6']
  return (
    <div ref={ref} className="string-field" style={{ height }} onPointerMove={onMove}>
      <svg width={w} height={height} style={{ display: 'block' }} aria-hidden>
        {inks.map((c, k) => <path key={k} ref={(el) => { paths.current[k] = el }} fill="none" stroke={c} strokeWidth={k === 2 ? 1.5 : 1.25} strokeOpacity={k === 2 ? 0.9 : 0.7} />)}
        {Array.from({ length: n + 1 }, (_, i) => <circle key={i} cx={Math.min(w - 3, Math.max(3, (i / n) * w))} cy={height / 2} r={2.5} fill="#99F6E4" />)}
      </svg>
      <span className="string-field-read">mode <i>n</i> = {n} · {n - 1} interior node{n === 2 ? '' : 's'}</span>
    </div>
  )
}
