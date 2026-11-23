/**
 * The Projects home / landing sky: a seeded field of SVG stars — four-point sparkles, thin crosses and dots —
 * in a small colour palette (`--star-*` tokens, both themes). Static (no twinkle loops). Only a band at the top
 * of the page: full strength near the top, fading to nothing by ~460px. Working pages have no sky at all.
 * Put it as the first child of the page's scroll container (which must be `relative`).
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { rng } from '@/shared/lib/starfield'

type Kind = 'sparkle' | 'cross' | 'dot'
interface Star {
  x: number
  y: number
  size: number
  kind: Kind
  color: string
  opacity: number
  rotate: number
  /** sparkle squash: 1 = even arms, < 1 = one axis shorter */
  squash: number
}

/** colour shares: mostly white/blue, a little cyan and violet, a touch of pink and amber */
const PALETTE: Array<[string, number]> = [
  ['var(--star-white)', 0.22],
  ['var(--star-blue)', 0.2],
  ['var(--star-cyan)', 0.19],
  ['var(--star-violet)', 0.19],
  ['var(--star-pink)', 0.11],
  ['var(--star-amber)', 0.09],
]

function pick(u: number) {
  let a = 0
  for (const [c, share] of PALETTE) {
    a += share
    if (u < a) return c
  }
  return PALETTE[0][0]
}

/** a concave four-point sparkle in a unit box centred on 0,0 */
const SPARKLE = 'M0 -1 C0.12 -0.12 0.12 -0.12 1 0 C0.12 0.12 0.12 0.12 0 1 C-0.12 0.12 -0.12 0.12 -1 0 C-0.12 -0.12 -0.12 -0.12 0 -1Z'

/** the sky is a band at the top of the page: full strength to TOP_FULL, gone by BAND */
const TOP_FULL = 110
const BAND = 460
const fade = (y: number) => Math.max(0, 1 - Math.max(0, y - TOP_FULL) / (BAND - TOP_FULL))

function makeStars(w: number): Star[] {
  const R = rng(73)
  // the reading column (content is centred, ~1130px wide): stars stay sparse there so text stays clean
  const colL = Math.max(0, (w - 1160) / 2)
  const colR = w - colL
  const inColumn = (x: number, y: number) => x > colL && x < colR && y > 70
  const out: Star[] = []
  const n = Math.round((w * BAND) / 5200)
  for (let i = 0; i < n; i++) {
    const x = R() * w
    const y = Math.pow(R(), 1.35) * BAND
    const keep = R()
    const k = R()
    const kind: Kind = k < 0.32 ? 'sparkle' : k < 0.42 ? 'cross' : 'dot'
    const big = Math.pow(R(), 2.4)
    const color = pick(R())
    const o = R()
    const rot = R()
    const f = fade(y)
    if (f < 0.06) continue
    // inside the reading column only a few small dots: no sparkle ever sits behind text or a control
    if (inColumn(x, y) && (kind !== 'dot' || keep < 0.75)) continue
    // mostly small; a long tail of slightly larger ones (sparkles 2.5–7.5px, crosses 2–4.5px, dots 0.7–2px)
    const size = kind === 'sparkle' ? 2.5 + big * 5 : kind === 'cross' ? 2 + big * 2.5 : 0.7 + big * 1.3
    out.push({ x, y, size, kind, color, opacity: (0.45 + o * 0.55) * f, rotate: rot * 90, squash: 0.6 + R() * 0.4 })
  }
  // a few feature sparkles in the margins and the top band
  const F = rng(911)
  const features = Math.max(3, Math.round(w / 320))
  for (let i = 0; i < features; i++) {
    const side = i % 3
    const x = side === 0 ? F() * Math.max(80, colL + 40) : side === 1 ? w - F() * Math.max(80, colL + 40) : colL + F() * (colR - colL)
    const y = side === 2 ? 14 + F() * 50 : 30 + F() * (BAND * 0.45)
    out.push({ x, y, size: 6 + F() * 4, kind: 'sparkle', color: pick(0.22 + F() * 0.78), opacity: 0.85 * fade(y), rotate: F() * 90, squash: 0.7 + F() * 0.3 })
  }
  return out
}

export function HomeSky({ className }: { className?: string }) {
  const ref = useRef<SVGSVGElement>(null)
  const [box, setBox] = useState<{ w: number; h: number } | null>(null)

  useEffect(() => {
    const host = ref.current?.parentElement
    if (!host) return
    const measure = () => {
      const w = Math.round(host.clientWidth / 40) * 40
      setBox((b) => (b && b.w === w ? b : { w, h: BAND }))
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    let raf = 0
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(measure)
    })
    ro.observe(host)
    return () => {
      ro.disconnect()
      cancelAnimationFrame(raf)
    }
  }, [])

  const stars = useMemo(() => (box ? makeStars(box.w) : []), [box])

  return (
    <svg
      ref={ref}
      aria-hidden="true"
      data-testid="home-sky"
      className={className ?? 'pointer-events-none absolute inset-x-0 top-0 z-0 w-full'}
      style={{ height: box?.h ?? 0 }}
      viewBox={box ? `0 0 ${box.w} ${box.h}` : undefined}
      preserveAspectRatio="xMidYMin slice"
    >
      {stars.map((s, i) =>
        s.kind === 'dot' ? (
          <circle key={i} cx={s.x} cy={s.y} r={s.size} style={{ fill: s.color }} opacity={s.opacity} />
        ) : s.kind === 'cross' ? (
          <g key={i} transform={`translate(${s.x} ${s.y}) rotate(${s.rotate})`} opacity={s.opacity * 0.85}>
            <path d={`M${-s.size} 0 H${s.size} M0 ${-s.size} V${s.size}`} strokeWidth="0.9" strokeLinecap="round" style={{ stroke: s.color }} />
          </g>
        ) : (
          <path
            key={i}
            d={SPARKLE}
            transform={`translate(${s.x} ${s.y}) rotate(${s.rotate}) scale(${s.size} ${s.size * s.squash})`}
            style={{ fill: s.color }}
            opacity={s.opacity}
          />
        ),
      )}
    </svg>
  )
}
