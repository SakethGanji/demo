/**
 * Design tokens, read at runtime for the WebGL scene. Any CSS colour expression (`var(--viz-1)`, a `color-mix()`,
 * a translucent `--well`) is resolved by the browser on a hidden probe and painted onto a 1×1 canvas, so the scene
 * gets exact sRGB bytes whatever syntax the token uses. `reset()` after a theme change re-reads every token.
 */
import * as THREE from 'three'

export type RGBA = readonly [r: number, g: number, b: number, a: number]

export class Palette {
  private cache = new Map<string, RGBA>()
  private probe: HTMLSpanElement
  private ctx: CanvasRenderingContext2D | null

  constructor(host: HTMLElement) {
    this.probe = document.createElement('span')
    this.probe.style.display = 'none'
    this.probe.setAttribute('aria-hidden', 'true')
    host.appendChild(this.probe)
    const c = document.createElement('canvas')
    c.width = 1
    c.height = 1
    this.ctx = c.getContext('2d', { willReadFrequently: true })
  }

  /** The computed CSS colour string of `expr` (for painting label canvases). */
  css(expr: string): string {
    this.probe.style.color = ''
    this.probe.style.color = expr
    return getComputedStyle(this.probe).color
  }

  /** `expr` as sRGB bytes (0..255) and alpha (0..1). */
  rgba(expr: string): RGBA {
    const hit = this.cache.get(expr)
    if (hit) return hit
    let out: RGBA = [128, 128, 128, 1]
    const ctx = this.ctx
    if (ctx) {
      ctx.clearRect(0, 0, 1, 1)
      ctx.fillStyle = this.css(expr)
      ctx.fillRect(0, 0, 1, 1)
      const d = ctx.getImageData(0, 0, 1, 1).data
      out = [d[0], d[1], d[2], d[3] / 255]
    }
    this.cache.set(expr, out)
    return out
  }

  /** `expr` as an opaque three.js colour (in the linear working space). */
  color(expr: string): THREE.Color {
    const [r, g, b] = this.rgba(expr)
    return new THREE.Color().setRGB(r / 255, g / 255, b / 255, THREE.SRGBColorSpace)
  }

  /** `top` (possibly translucent) laid over the opaque `under`, as a three.js colour. */
  over(top: string, under: string): THREE.Color {
    const [r1, g1, b1, a] = this.rgba(top)
    const [r0, g0, b0] = this.rgba(under)
    const mix = (x: number, y: number) => (x * a + y * (1 - a)) / 255
    return new THREE.Color().setRGB(mix(r1, r0), mix(g1, g0), mix(b1, b0), THREE.SRGBColorSpace)
  }

  reset() {
    this.cache.clear()
  }

  dispose() {
    this.probe.remove()
    this.cache.clear()
    this.ctx = null
  }
}
