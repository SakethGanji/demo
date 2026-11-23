/**
 * A CSS colour expression (`var(--brand-1)`, a `color-mix()`) resolved to sRGB channels 0..1, for WebGL. The
 * browser resolves it on a hidden probe and paints it onto a 1×1 canvas, so any token syntax comes back exact.
 */
export type RGB = [r: number, g: number, b: number]

export function tokenRGB(expr: string, host: HTMLElement = document.body): RGB {
  const probe = document.createElement('span')
  probe.style.display = 'none'
  probe.style.color = expr
  host.appendChild(probe)
  const css = getComputedStyle(probe).color
  probe.remove()
  const c = document.createElement('canvas')
  c.width = 1
  c.height = 1
  const ctx = c.getContext('2d', { willReadFrequently: true })
  if (!ctx) return [0.5, 0.5, 0.5]
  ctx.fillStyle = css
  ctx.fillRect(0, 0, 1, 1)
  const d = ctx.getImageData(0, 0, 1, 1).data
  return [d[0] / 255, d[1] / 255, d[2] / 255]
}

/** True while the dark theme is on (the theme provider puts `dark` on <html>). */
export function isDarkTheme(): boolean {
  return document.documentElement.classList.contains('dark')
}
