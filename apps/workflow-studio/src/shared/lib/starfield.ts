/* Command Studio · starfield (ported from demo/design/handoff/starfield.js; only types added).
   The canvas sky: power-law star sizes (mostly pinpricks, a rare bright star), colour temperature in dark,
   a soft halo on the bigger stars and diffraction spikes on the rarest. Deterministic: the same seed always
   paints the same sky.

   Presets:
     SKY   a fixed full-screen canvas behind the app inside a project, CSS opacity var(--sky-opacity)
           (.16 dark, .4 light). Light paints the same stars in navy ink.
     SHEET inside the content sheet (not used by the final design)
     DUST  a 480px tile for empty fields, used as a background-image (the `stardust` utility)

   Projects home paints its own fuller sky with paintHomeSky(). */

type Canvas = HTMLCanvasElement | OffscreenCanvas
type Ctx = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D
type Temp = readonly [number, number, number, number]
type Ink = readonly [number, number, number] | Temp

export interface StarfieldOptions {
  width: number
  height: number
  /** CSS px² per star (higher = sparser) */
  density?: number
  seed?: number
  maxRadius?: number
  /** colour temperatures, halos and spikes; light paints navy ink */
  dark?: boolean
  spikes?: boolean
}

export const SKY = { density: 2400, seed: 11, maxRadius: 2.2, spikes: true }
export const SHEET = { density: 3200, seed: 29, maxRadius: 1.8, spikes: true }
export const DUST = { width: 480, height: 480, density: 1500, seed: 47, maxRadius: 1.5, spikes: false }

/* star colour temperatures in dark: [r, g, b, share]. Light draws every star in one navy ink. */
export const TEMPS: readonly Temp[] = [[255, 255, 255, .55], [205, 222, 255, .25], [255, 242, 224, .14], [255, 208, 170, .06]]
const LIGHT_INK = [44, 46, 52] as const // neutral graphite: no navy cast
const LIGHT_ALPHA = 0.7

const MIN_RADIUS = 0.28
const SIZE_POWER = 16 // r = MIN_RADIUS + u^16 · maxRadius
const HALO_AT = 1.05 // dark: stars with r > 1.05 get a soft halo …
const HALO_SPREAD = 4.5 // … r · 4.5 wide, at 30% of the star's alpha
const SPIKE_AT = 2.45 // dark: stars with r > 2.45 get cross spikes …
const SPIKE_LENGTH = 7 // … r · 7 each way, 0.6px, at 70% alpha

/* Park–Miller minimal standard PRNG. */
export function rng(seed: number) {
  let x = seed % 2147483647
  if (x <= 0) x += 2147483646
  return () => (x = (x * 16807) % 2147483647) / 2147483647
}

export function temp(u: number): Temp {
  let a = 0
  for (const t of TEMPS) {
    a += t[3]
    if (u < a) return t
  }
  return TEMPS[0]
}

function context(canvas: Canvas): Ctx | null {
  return canvas.getContext('2d') as Ctx | null
}

function dprOf() {
  return Math.min(2, (typeof devicePixelRatio === 'number' && devicePixelRatio) || 1)
}

/** Paint a starfield into a canvas, sized for the device pixel ratio (capped at 2). */
export function paintStarfield(
  canvas: Canvas,
  { width, height, density = SKY.density, seed = SKY.seed, maxRadius = SKY.maxRadius, dark = true, spikes = true }: StarfieldOptions,
) {
  const dpr = dprOf()
  const ctx = context(canvas)
  if (!ctx) return canvas
  canvas.width = Math.round(width * dpr)
  canvas.height = Math.round(height * dpr)
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, width, height)
  const R = rng(seed)
  const n = Math.round((width * height) / density)
  for (let k = 0; k < n; k++) {
    const x = R() * width
    const y = R() * height
    const u = R()
    const r = MIN_RADIUS + Math.pow(u, SIZE_POWER) * maxRadius
    let a = 0.14 + Math.pow(R(), 2.2) * 0.5 + (r > 1 ? 0.3 : 0)
    const t: Ink = dark ? temp(R()) : LIGHT_INK
    if (!dark) a *= LIGHT_ALPHA
    const col = (al: number) => `rgba(${t[0]},${t[1]},${t[2]},${Math.min(1, al)})`
    if (dark && r > HALO_AT) {
      const g = ctx.createRadialGradient(x, y, 0, x, y, r * HALO_SPREAD)
      g.addColorStop(0, col(a * 0.3))
      g.addColorStop(1, col(0))
      ctx.fillStyle = g
      ctx.beginPath()
      ctx.arc(x, y, r * HALO_SPREAD, 0, 7)
      ctx.fill()
    }
    if (dark && spikes && r > SPIKE_AT) {
      ctx.lineWidth = 0.6
      for (const [dx, dy] of [[1, 0], [0, 1]] as const) {
        const L = r * SPIKE_LENGTH
        const g = ctx.createLinearGradient(x - dx * L, y - dy * L, x + dx * L, y + dy * L)
        g.addColorStop(0, col(0))
        g.addColorStop(0.5, col(a * 0.7))
        g.addColorStop(1, col(0))
        ctx.strokeStyle = g
        ctx.beginPath()
        ctx.moveTo(x - dx * L, y - dy * L)
        ctx.lineTo(x + dx * L, y + dy * L)
        ctx.stroke()
      }
    }
    ctx.fillStyle = col(a)
    ctx.beginPath()
    ctx.arc(x, y, r, 0, 7)
    ctx.fill()
  }
  return canvas
}

/** A star-dust tile as a PNG data URL, for `--star-dust` (the `stardust` utility tiles it at 480px). */
export function starDustDataURL(opts: Partial<StarfieldOptions> = {}) {
  const c = document.createElement('canvas')
  paintStarfield(c, { ...DUST, ...opts })
  return c.toDataURL()
}

/* ── Projects home sky ── */

const HOME_TEMPS: readonly Temp[] = [[255, 255, 255, .55], [205, 222, 255, .27], [255, 242, 224, .13], [255, 208, 170, .05]]
function homeTemp(u: number): Temp {
  let a = 0
  for (const t of HOME_TEMPS) {
    a += t[3]
    if (u < a) return t
  }
  return HOME_TEMPS[0]
}
/* Fade a colour to itself at 0 alpha. Fading to transparent black turns a glow grey on white. */
const clear = (c: string) => c.replace(/[\d.]+\)$/, '0)')

function homeStar(ctx: Ctx, x: number, y: number, r: number, a: number, t: Ink) {
  const col = (al: number) => `rgba(${t[0]},${t[1]},${t[2]},${Math.min(1, al)})`
  if (r > 1.05) {
    const g = ctx.createRadialGradient(x, y, 0, x, y, r * 4.5)
    g.addColorStop(0, col(a * 0.32))
    g.addColorStop(1, col(0))
    ctx.fillStyle = g
    ctx.beginPath()
    ctx.arc(x, y, r * 4.5, 0, 7)
    ctx.fill()
  }
  if (r > 2.3) {
    ctx.lineWidth = 0.6
    for (const [dx, dy] of [[1, 0], [0, 1]] as const) {
      const L = r * 8
      const g = ctx.createLinearGradient(x - dx * L, y - dy * L, x + dx * L, y + dy * L)
      g.addColorStop(0, col(0))
      g.addColorStop(0.5, col(a * 0.75))
      g.addColorStop(1, col(0))
      ctx.strokeStyle = g
      ctx.beginPath()
      ctx.moveTo(x - dx * L, y - dy * L)
      ctx.lineTo(x + dx * L, y + dy * L)
      ctx.stroke()
    }
  }
  ctx.fillStyle = col(a)
  ctx.beginPath()
  ctx.arc(x, y, r, 0, 7)
  ctx.fill()
}

/**
 * The Projects home sky: a slight top gradient, the logo's two colours as blooms, a denser starfield that
 * thins toward the cards, and a faint Milky Way band. The same composition in both themes.
 * Width is the page width, height the full scroll height.
 */
export function paintHomeSky(canvas: Canvas, { width: w, height: h, dark = true }: { width: number; height: number; dark?: boolean }) {
  const dpr = dprOf()
  const ctx = context(canvas)
  if (!ctx) return canvas
  canvas.width = Math.round(w * dpr)
  canvas.height = Math.round(h * dpr)
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, w, h)

  // 1. a slight gradient: a tint at the top in dark, flat white in light
  const g = ctx.createLinearGradient(0, 0, 0, 760)
  if (dark) {
    // dark is a plain neutral theme: the canvas colour, no navy tint
    g.addColorStop(0, '#08090a')
    g.addColorStop(1, '#08090a')
  } else {
    g.addColorStop(0, '#ffffff')
    g.addColorStop(1, '#ffffff')
  }
  ctx.fillStyle = g
  ctx.fillRect(0, 0, w, h)

  // 2. steel blue from the top left, aqua from the right, a brighter meeting point behind the greeting
  const bloom = (x: number, y: number, r: number, c: string) => {
    const gr = ctx.createRadialGradient(x, y, 0, x, y, r)
    gr.addColorStop(0, c)
    gr.addColorStop(1, clear(c))
    ctx.fillStyle = gr
    ctx.fillRect(x - r, y - r, r * 2, r * 2)
  }
  // no coloured blooms in either theme: the sky is neutral (Linear-like), colour is reserved for meaning
  void bloom

  // stars are full strength at the top and thin out toward the cards
  const fall = (y: number) => 1 - 0.65 * Math.min(1, Math.max(0, (y - 120) / 640))
  const k = dark ? 0.55 : 0.5

  // 3. the sky
  const S = rng(101)
  const n = Math.round((w * h) / 1100)
  for (let i = 0; i < n; i++) {
    const x = S() * w
    const y = S() * h
    const u = S()
    const a = (0.16 + Math.pow(S(), 2) * 0.55) * k * fall(y)
    const t = homeTemp(S())
    homeStar(ctx, x, y, 0.25 + Math.pow(u, 14) * 2.6, a, dark ? t : LIGHT_INK)
  }

  // 4. a faint Milky Way band across the top, rising to the left
  const top = 520
  const band = (x: number) => top * 0.12 + (x / w) * top * 0.55
  for (let i = 0; i < 6; i++) {
    const x = ((i + 0.5) * w) / 6
    const y = band(x)
    const r = w * 0.16
    const gl = ctx.createRadialGradient(x, y, 0, x, y, r)
    const bc = dark ? 'rgba(255,255,255,.02)' : 'rgba(0,0,0,.025)'
    gl.addColorStop(0, bc)
    gl.addColorStop(1, clear(bc))
    ctx.fillStyle = gl
    ctx.fillRect(x - r, y - r, r * 2, r * 2)
  }
  const B = rng(202)
  const dust = Math.round(w * 1.4)
  for (let i = 0; i < dust; i++) {
    const x = B() * w
    const y = band(x) + (B() + B() + B() - 1.5) * top * 0.16
    const r = 0.22 + Math.pow(B(), 10) * 1.3
    const a = (0.1 + B() * 0.35) * k * fall(y)
    const t = homeTemp(B())
    if (y < 0) continue
    homeStar(ctx, x, y, r, a, dark ? t : LIGHT_INK)
  }
  return canvas
}
