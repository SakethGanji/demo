/**
 * UI-DESIGN §7.3: every text/background pair the new tokens create meets its minimum
 * contrast in light and dark. Parses src/index.css and mixes tints the way CSS
 * `color-mix(in srgb, X p%, transparent)` composites over the card surface.
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(path.resolve(__dirname, '../../../index.css'), 'utf8')

function block(selector: ':root' | '.dark'): Record<string, string> {
  const start = css.indexOf(selector === ':root' ? ':root {' : '.dark {')
  const end = css.indexOf('\n}', start)
  const body = css.slice(start, end)
  const out: Record<string, string> = {}
  for (const m of body.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) out[m[1]] = m[2].trim()
  return out
}

const light = block(':root')
const dark = { ...light, ...block('.dark') }

type RGB = [number, number, number]
function hex(v: string): RGB {
  const h = v.replace('#', '')
  const n = h.length === 3 ? h.split('').map((c) => c + c).join('') : h
  return [0, 2, 4].map((i) => parseInt(n.slice(i, i + 2), 16)) as RGB
}
function resolve(tokens: Record<string, string>, name: string): RGB {
  let v = tokens[name]
  for (let i = 0; i < 5 && v?.startsWith('var('); i++) v = tokens[v.slice(6, -1)]
  if (!v || !v.startsWith('#')) throw new Error(`token --${name} is not a hex colour: ${v}`)
  return hex(v)
}
/** color-mix(in srgb, fg p%, transparent) over bg */
function tint(fg: RGB, p: number, bg: RGB): RGB {
  return fg.map((c, i) => c * p + bg[i] * (1 - p)) as RGB
}
function lum([r, g, b]: RGB): number {
  const f = (c: number) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}
function ratio(a: RGB, b: RGB): number {
  const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m)
  return (x + 0.05) / (y + 0.05)
}

/** p% from `color-mix(in srgb, var(--X) p%, transparent)` in the given token */
function mixPercent(tokens: Record<string, string>, name: string): number {
  const m = /(\d+(?:\.\d+)?)%/.exec(tokens[name] ?? '')
  if (!m) throw new Error(`--${name} is not a color-mix`)
  return Number(m[1]) / 100
}

for (const [mode, t] of [['light', light], ['dark', dark]] as const) {
  describe(`contrast (${mode})`, () => {
    const card = resolve(t, 'card')
    it.each(['get', 'post', 'put', 'patch', 'delete'])('method %s on its 10%% tint ≥ 4.5', (m) => {
      const c = resolve(t, `method-${m}`)
      expect(ratio(c, tint(c, 0.1, card))).toBeGreaterThanOrEqual(4.5)
    })
    it('warning text on warning 13% tint ≥ 4.5', () => {
      expect(ratio(resolve(t, 'warning-text'), tint(resolve(t, 'warning'), 0.13, card))).toBeGreaterThanOrEqual(4.5)
    })
    it('success text on success 11% tint ≥ 4.5', () => {
      expect(ratio(resolve(t, 'success-text'), tint(resolve(t, 'success'), 0.11, card))).toBeGreaterThanOrEqual(4.5)
    })
    it('ai text on ai-soft ≥ 4.5', () => {
      const soft = tint(resolve(t, 'ai'), mixPercent(t, 'ai-soft'), card)
      expect(ratio(resolve(t, 'ai-text'), soft)).toBeGreaterThanOrEqual(4.5)
    })
    it('syntax keyword ≥ 4.5 and comment ≥ 3.5 on code-bg', () => {
      const bg = resolve(t, 'code-bg')
      expect(ratio(resolve(t, 'syn-keyword'), bg)).toBeGreaterThanOrEqual(4.5)
      expect(ratio(resolve(t, 'syn-string'), bg)).toBeGreaterThanOrEqual(4.5)
      expect(ratio(resolve(t, 'syn-fn'), bg)).toBeGreaterThanOrEqual(4.5)
      expect(ratio(resolve(t, 'syn-comment'), bg)).toBeGreaterThanOrEqual(3.5)
    })
    it('gutter numbers on code-bg ≥ 3.0', () => {
      expect(ratio(resolve(t, 'code-gutter'), resolve(t, 'code-bg'))).toBeGreaterThanOrEqual(3.0)
    })
  })
}
