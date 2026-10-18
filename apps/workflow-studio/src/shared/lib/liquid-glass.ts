/**
 * Liquid glass (after kube.io/blog/liquid-glass-css-svg): real refraction for small, fixed-shape glass.
 * A displacement map is computed from a convex-squircle bezel (height y = ⁴√(1 − (1 − x)⁴), Snell's law with
 * n = 1.5, rays orthogonal to the background), encoded in R/G around 128, and applied with an SVG
 * `feDisplacementMap` through `backdrop-filter: url(#id)`.
 *
 * Chromium only (SVG filters as backdrop-filter are non-standard); everything else keeps the plain blur.
 * Maps are cached per size because rebuilding is the expensive part; use it only on small controls.
 * Budget: at most MAX_LIVE refracting surfaces at once (extra ones keep their plain recipe), and none on
 * machines with 4 or fewer cores: every refracting surface re-filters its backdrop each frame it moves.
 */

import { useEffect, useMemo, useState } from 'react'

const N_GLASS = 1.5
const MAX_LIVE = 4
let live = 0

let supported: boolean | null = null
export function liquidGlassSupported() {
  if (supported != null) return supported
  if (typeof window === 'undefined' || typeof document === 'undefined') return (supported = false)
  const uaData = (navigator as Navigator & { userAgentData?: { brands?: Array<{ brand: string }> } }).userAgentData
  const chromium = !!uaData?.brands?.some((b) => /Chromium/i.test(b.brand))
  const calm = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || window.matchMedia?.('(prefers-reduced-transparency: reduce)').matches
  const strong = (navigator.hardwareConcurrency ?? 8) > 4
  return (supported = chromium && !calm && strong)
}

/** convex squircle bezel profile and its slope */
const height = (x: number) => Math.pow(1 - Math.pow(1 - x, 4), 1 / 4)
function slope(x: number) {
  const d = 0.001
  return (height(Math.min(1, x + d)) - height(Math.max(0, x - d))) / (2 * d)
}

/** lateral shift of a vertical ray refracted at a surface with the given slope (relative units) */
function refractShift(s: number) {
  const theta1 = Math.atan(s) // angle between the ray and the surface normal
  const theta2 = Math.asin(Math.sin(theta1) / N_GLASS)
  return Math.tan(theta1 - theta2)
}

/** distance from (x, y) to the edge of a rounded rect, positive inside */
function sdf(x: number, y: number, w: number, h: number, r: number) {
  const qx = Math.abs(x - w / 2) - (w / 2 - r)
  const qy = Math.abs(y - h / 2) - (h / 2 - r)
  const outside = Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - r
  return -outside
}

interface GlassMap {
  url: string
  scale: number
}

const cache = new Map<string, GlassMap>()

/** Builds (or reuses) the displacement map for a w×h rounded rect with corner radius r and bezel width b. */
export function glassMap(w: number, h: number, r: number, b: number, strength: number): GlassMap {
  const W = Math.max(1, Math.round(w))
  const H = Math.max(1, Math.round(h))
  const key = `${W}x${H}r${r}b${b}s${strength}`
  const hit = cache.get(key)
  if (hit) return hit
  const c = document.createElement('canvas')
  c.width = W
  c.height = H
  const ctx = c.getContext('2d')
  if (!ctx) return { url: '', scale: 0 }
  const img = ctx.createImageData(W, H)
  // normalise against the strongest shift in the bezel
  let maxShift = 0
  for (let i = 1; i <= 64; i++) maxShift = Math.max(maxShift, Math.abs(refractShift(slope(i / 64))))
  const R = Math.min(r, W / 2, H / 2)
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const px = x + 0.5
      const py = y + 0.5
      const d = sdf(px, py, W, H, R)
      let dx = 0
      let dy = 0
      if (d > 0 && d < b) {
        const t = d / b // 0 at the rim, 1 where the flat top starts
        const m = refractShift(slope(t)) / maxShift
        // inward normal from the distance field's gradient
        const gx = sdf(px + 0.5, py, W, H, R) - sdf(px - 0.5, py, W, H, R)
        const gy = sdf(px, py + 0.5, W, H, R) - sdf(px, py - 0.5, W, H, R)
        const gl = Math.hypot(gx, gy) || 1
        dx = (gx / gl) * m
        dy = (gy / gl) * m
      }
      const o = (y * W + x) * 4
      img.data[o] = 128 + dx * 127
      img.data[o + 1] = 128 + dy * 127
      img.data[o + 2] = 128
      img.data[o + 3] = 255
    }
  }
  ctx.putImageData(img, 0, 0)
  const map = { url: c.toDataURL(), scale: strength }
  if (cache.size > 64) cache.clear()
  cache.set(key, map)
  return map
}

let host: SVGSVGElement | null = null
function svgHost() {
  if (host?.isConnected) return host
  host = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  host.setAttribute('aria-hidden', 'true')
  host.setAttribute('width', '0')
  host.setAttribute('height', '0')
  host.style.position = 'absolute'
  host.style.pointerEvents = 'none'
  document.body.appendChild(host)
  return host
}

let seq = 0
/**
 * Attaches a liquid-glass backdrop filter to `el` sized w×h. Returns a cleanup. `extra` is appended to the
 * backdrop-filter (e.g. a light blur). Does nothing where unsupported.
 */
export function attachLiquidGlass(el: HTMLElement, w: number, h: number, { radius = 6, bezel = 6, strength = 10, extra = 'saturate(150%)' } = {}) {
  if (!liquidGlassSupported() || w < 4 || h < 4 || live >= MAX_LIVE) return () => {}
  const NS = 'http://www.w3.org/2000/svg'
  const id = `lg-${++seq}`
  const { url, scale } = glassMap(w, h, radius, bezel, strength)
  if (!url) return () => {}
  const filter = document.createElementNS(NS, 'filter')
  filter.setAttribute('id', id)
  filter.setAttribute('x', '0')
  filter.setAttribute('y', '0')
  filter.setAttribute('width', String(Math.round(w)))
  filter.setAttribute('height', String(Math.round(h)))
  filter.setAttribute('filterUnits', 'userSpaceOnUse')
  filter.setAttribute('color-interpolation-filters', 'sRGB')
  const image = document.createElementNS(NS, 'feImage')
  image.setAttribute('href', url)
  image.setAttribute('x', '0')
  image.setAttribute('y', '0')
  image.setAttribute('width', String(Math.round(w)))
  image.setAttribute('height', String(Math.round(h)))
  image.setAttribute('preserveAspectRatio', 'none')
  image.setAttribute('result', 'map')
  const disp = document.createElementNS(NS, 'feDisplacementMap')
  disp.setAttribute('in', 'SourceGraphic')
  disp.setAttribute('in2', 'map')
  disp.setAttribute('scale', String(scale))
  disp.setAttribute('xChannelSelector', 'R')
  disp.setAttribute('yChannelSelector', 'G')
  filter.append(image, disp)
  svgHost().appendChild(filter)
  const filterValue = `url(#${id}) ${extra}`
  el.style.backdropFilter = filterValue
  el.style.setProperty('-webkit-backdrop-filter', filterValue)
  el.dataset.liquid = ''
  live++
  let done = false
  return () => {
    if (done) return
    done = true
    live--
    filter.remove()
    el.style.backdropFilter = ''
    el.style.removeProperty('-webkit-backdrop-filter')
    delete el.dataset.liquid
  }
}

/**
 * React helper: keeps a liquid-glass backdrop on `ref` while it is mounted, rebuilt when its size changes
 * (maps are cached per size). Unsupported browsers keep whatever backdrop the element's classes give it.
 */
export function useLiquidGlass(
  ref: { current: HTMLElement | null },
  opts: { radius?: number; bezel?: number; strength?: number; extra?: string } = {},
  enabled = true,
) {
  const { radius, bezel, strength, extra } = opts
  useEffect(() => {
    const el = ref.current
    if (!enabled || !el || !liquidGlassSupported()) return
    let detach = () => {}
    let last = ''
    const apply = () => {
      const w = Math.round(el.offsetWidth)
      const h = Math.round(el.offsetHeight)
      const key = `${w}x${h}`
      if (key === last || w < 4 || h < 4) return
      last = key
      detach()
      detach = attachLiquidGlass(el, w, h, { radius, bezel, strength, extra })
    }
    apply()
    if (typeof ResizeObserver === 'undefined') return detach
    let raf = 0
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(apply)
    })
    ro.observe(el)
    return () => {
      ro.disconnect()
      cancelAnimationFrame(raf)
      detach()
    }
  }, [ref, enabled, radius, bezel, strength, extra])
}

/**
 * For popups that mount after their component does (menus, popovers render into a portal only while open):
 * returns a callback ref for the popup element. The glass attaches when the element appears and detaches
 * when it goes, which a plain `useRef` + `useLiquidGlass` cannot do (its effect ran once, before the popup existed).
 */
export function useLiquidGlassRef(opts: { radius?: number; bezel?: number; strength?: number; extra?: string } = {}) {
  const [el, setEl] = useState<HTMLElement | null>(null)
  const ref = useMemo(() => ({ current: el }), [el])
  useLiquidGlass(ref, opts)
  return setEl
}
