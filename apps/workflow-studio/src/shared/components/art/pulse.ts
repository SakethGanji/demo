/**
 * Packets are events, not decoration (DESIGN §6): `pulse(path)` sends one dot along an SVG path, once,
 * for a real event (a run arrived, a row was written, a secret was set). At most one every 280ms per path;
 * the dot never outlives its trip. Under reduced motion the wire flashes once instead.
 * Port of the mockups' `LunaArt.pulse` / `pulseWire`.
 */
export type PulseColor = 'brand' | 'warn' | 'secret' | 'error'

const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim()
const ms = (n: string, d: number) => parseFloat(css(n)) || d
const reduced = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

function bezier(str: string) {
  const m = /cubic-bezier\(([^)]+)\)/.exec(str || '')
  const [x1, y1, x2, y2] = m ? m[1].split(',').map(Number) : [0.65, 0, 0.35, 1]
  const B = (t: number, a: number, b: number) => 3 * a * t * (1 - t) * (1 - t) + 3 * b * t * t * (1 - t) + t * t * t
  return (x: number) => {
    let lo = 0
    let hi = 1
    let t = x
    for (let i = 0; i < 24; i++) {
      t = (lo + hi) / 2
      if (B(t, x1, x2) < x) lo = t
      else hi = t
    }
    return B(t, y1, y2)
  }
}

const last = new WeakMap<SVGGeometryElement, number>()
const GAP = 280
const FILL: Record<string, string> = { brand: 'var(--brand-1)', warn: 'var(--warning)', error: 'var(--destructive)' }
const GLOW: Record<string, string> = {
  brand: 'drop-shadow(0 0 2.5px var(--brand-1)) drop-shadow(0 0 6px var(--glow))',
  warn: 'drop-shadow(0 0 2.5px var(--warning))',
  error: 'drop-shadow(0 0 2.5px var(--destructive))',
}
const mixAt = (u: number) => `color-mix(in srgb, var(--success) ${Math.round(u * 100)}%, var(--warning))`

export function pulse(path: SVGGeometryElement | null, opts: { color?: PulseColor; delay?: number; duration?: number; r?: number } = {}) {
  if (!path || typeof path.getTotalLength !== 'function') return
  if (opts.delay) {
    setTimeout(() => pulse(path, { ...opts, delay: 0 }), opts.delay)
    return
  }
  const now = performance.now()
  if (now - (last.get(path) ?? -1e9) < GAP) return
  last.set(path, now)
  const color = opts.color ?? 'brand'
  if (reduced()) {
    const to = getComputedStyle(path).stroke
    const from = color === 'brand' ? css('--brand-1') : color === 'warn' ? css('--warning') : color === 'error' ? css('--destructive') : css('--success')
    path.animate([{ stroke: from }, { stroke: to }], { duration: ms('--d-base', 200), easing: css('--ease-out') || 'ease-out' })
    return
  }
  const NS = 'http://www.w3.org/2000/svg'
  const dot = document.createElementNS(NS, 'circle')
  const len = path.getTotalLength()
  const D = opts.duration ?? ms('--d-slow', 280) * 2
  const ease = bezier(css('--ease-io'))
  dot.setAttribute('r', String(opts.r ?? 2.6))
  dot.setAttribute('aria-hidden', 'true')
  dot.style.fill = FILL[color] ?? mixAt(0)
  dot.style.filter = GLOW[color] ?? GLOW.brand
  path.parentNode?.insertBefore(dot, path.nextSibling)
  const t0 = performance.now()
  setTimeout(() => dot.remove(), D + 120)
  const tick = (t: number) => {
    const x = Math.min(1, (t - t0) / D)
    const u = ease(x)
    const pt = path.getPointAtLength(u * len)
    dot.setAttribute('cx', String(pt.x))
    dot.setAttribute('cy', String(pt.y))
    dot.style.opacity = String(x < 0.12 ? x / 0.12 : x > 0.88 ? (1 - x) / 0.12 : 1)
    if (color === 'secret') {
      dot.style.fill = mixAt(u)
      dot.style.filter = `drop-shadow(0 0 2.5px ${mixAt(u)})`
    }
    if (x < 1 && dot.isConnected) requestAnimationFrame(tick)
    else dot.remove()
  }
  requestAnimationFrame(tick)
}

/** Pulses every `[data-wire="<name>"]` path inside `root`. */
export function pulseWire(root: Element | null, name: string, opts?: Parameters<typeof pulse>[1]) {
  root?.querySelectorAll<SVGGeometryElement>(`[data-wire="${CSS.escape(name)}"]`).forEach((p) => pulse(p, opts))
}
