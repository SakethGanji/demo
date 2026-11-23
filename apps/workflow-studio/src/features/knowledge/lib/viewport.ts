/**
 * Pan and zoom maths for the knowledge canvas. A transform maps world to screen: screen = world × k + (x, y).
 */
import type { Bounds, Point } from './layout'

export interface Transform {
  x: number
  y: number
  k: number
}
export interface Size {
  width: number
  height: number
}

export const MIN_K = 0.15
export const MAX_K = 4
export const IDENTITY: Transform = { x: 0, y: 0, k: 1 }

export const clampK = (k: number, min = MIN_K, max = MAX_K) => Math.min(max, Math.max(min, k))

/** The transform that fits `b` inside `size` with `pad` px around it; never zooms in past `maxK`. */
export function fitTransform(b: Bounds | null, size: Size, pad = 48, maxK = 1.6): Transform {
  if (!b || size.width <= 0 || size.height <= 0) return { x: size.width / 2, y: size.height / 2, k: 1 }
  const w = Math.max(1, b.maxX - b.minX)
  const h = Math.max(1, b.maxY - b.minY)
  const k = clampK(Math.min((size.width - pad * 2) / w, (size.height - pad * 2) / h, maxK))
  const cx = (b.minX + b.maxX) / 2
  const cy = (b.minY + b.maxY) / 2
  return { x: size.width / 2 - cx * k, y: size.height / 2 - cy * k, k }
}

/** Zoom by `factor` keeping the screen point `p` fixed. */
export function zoomAround(t: Transform, factor: number, p: Point): Transform {
  const k = clampK(t.k * factor)
  const f = k / t.k
  return { k, x: p.x - (p.x - t.x) * f, y: p.y - (p.y - t.y) * f }
}

/** Put world point `w` at the centre of the view, at zoom `k` (default: keep the current zoom). */
export function centerOn(t: Transform, w: Point, size: Size, k = t.k): Transform {
  const kk = clampK(k)
  return { k: kk, x: size.width / 2 - w.x * kk, y: size.height / 2 - w.y * kk }
}

/**
 * Zoom factor for one wheel event. Trackpad pinches arrive as ctrl+wheel with small deltas, so they get more gain;
 * line-mode wheels (Firefox) report lines, not pixels.
 */
export function wheelFactor(deltaY: number, deltaMode = 0, ctrlKey = false): number {
  const px = deltaMode === 1 ? deltaY * 16 : deltaMode === 2 ? deltaY * 400 : deltaY
  const gain = ctrlKey ? 0.01 : 0.0018
  return Math.exp(-Math.max(-120, Math.min(120, px)) * gain)
}

export const toWorld = (t: Transform, p: Point): Point => ({ x: (p.x - t.x) / t.k, y: (p.y - t.y) / t.k })

/** Linear blend between two transforms (zoom blends geometrically so the motion feels even). */
export function lerpTransform(a: Transform, b: Transform, u: number): Transform {
  return { x: a.x + (b.x - a.x) * u, y: a.y + (b.y - a.y) * u, k: a.k * Math.pow(b.k / a.k, u) }
}

/** ease-out cubic, the camera's curve */
export const easeOut = (u: number) => 1 - Math.pow(1 - Math.min(1, Math.max(0, u)), 3)
