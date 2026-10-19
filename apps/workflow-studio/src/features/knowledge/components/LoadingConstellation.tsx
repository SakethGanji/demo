/**
 * The loading state: a faint constellation in the kind colours whose stars twinkle out of step, so the canvas
 * looks like it is gathering rather than blank. Deterministic (seeded), and still under reduced motion.
 */
import { useMemo } from 'react'
import { KIND_ORDER, KIND_STYLE } from '../lib/kinds'
import { mulberry32 } from '../lib/layout'

export function LoadingConstellation() {
  const { stars, links } = useMemo(() => {
    const rand = mulberry32(20261018)
    const stars = Array.from({ length: 46 }, (_, i) => {
      const a = rand() * Math.PI * 2
      const d = Math.sqrt(rand())
      return {
        x: 50 + Math.cos(a) * d * 42,
        y: 50 + Math.sin(a) * d * 40,
        r: 0.5 + rand() * 0.9,
        color: KIND_STYLE[KIND_ORDER[i % 5]].color,
        delay: (rand() * 1.8).toFixed(2),
      }
    })
    const links: [number, number][] = []
    for (let i = 0; i < stars.length; i++) {
      let best = -1
      let bd = Infinity
      for (let j = 0; j < i; j++) {
        const dd = Math.hypot(stars[i].x - stars[j].x, stars[i].y - stars[j].y)
        if (dd < bd) {
          bd = dd
          best = j
        }
      }
      if (best >= 0 && bd < 16) links.push([i, best])
    }
    return { stars, links }
  }, [])
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3" role="status" aria-live="polite" data-testid="knowledge-loading">
      <svg viewBox="0 0 100 100" className="size-[min(60%,420px)] overflow-visible" aria-hidden>
        {links.map(([a, b]) => (
          <line key={`${a}-${b}`} x1={stars[a].x} y1={stars[a].y} x2={stars[b].x} y2={stars[b].y} stroke="var(--muted-foreground)" strokeWidth={0.15} opacity={0.35} />
        ))}
        {stars.map((s, i) => (
          <circle key={i} cx={s.x} cy={s.y} r={s.r} fill={s.color} className="kg-twinkle" style={{ animationDelay: `${s.delay}s` }} />
        ))}
      </svg>
      <span className="text-[12.5px] text-muted-foreground">Mapping what the studio knows…</span>
    </div>
  )
}
