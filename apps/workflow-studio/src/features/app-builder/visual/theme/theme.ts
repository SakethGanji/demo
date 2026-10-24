/**
 * The app theme model: a handful of choices (colour, accent, background tone, radius, font, colour scheme) compiled into
 * CSS variable overrides for the app-owned `theme.css`. The file is the only thing written; the choices travel inside it
 * as a JSON comment so the panel can read them back. Derived tokens (on-colour text, soft fill, ring, dark-mode primary) are
 * computed from the chosen colour with a contrast check and CSS colour-mix, so any colour stays readable.
 */

export const THEME_PATH = 'theme.css'

export type ToneId = 'white' | 'slate' | 'warm' | 'cool' | 'gray'
export type RadiusId = 'none' | 'sm' | 'md' | 'lg' | 'xl'
export type FontId = 'sans' | 'serif' | 'rounded' | 'mono'
export type SchemeId = 'system' | 'light' | 'dark'

export interface ThemeChoice {
  primary: string
  accent: string
  tone: ToneId
  radius: RadiusId
  font: FontId
  scheme: SchemeId
}

export const DEFAULT_THEME: ThemeChoice = { primary: '#2563eb', accent: '#059669', tone: 'slate', radius: 'md', font: 'sans', scheme: 'system' }

export const SWATCHES: { label: string; hex: string }[] = [
  { label: 'Blue', hex: '#2563eb' },
  { label: 'Indigo', hex: '#4f46e5' },
  { label: 'Violet', hex: '#7c3aed' },
  { label: 'Rose', hex: '#e11d48' },
  { label: 'Orange', hex: '#ea580c' },
  { label: 'Amber', hex: '#d97706' },
  { label: 'Emerald', hex: '#059669' },
  { label: 'Teal', hex: '#0d9488' },
  { label: 'Slate', hex: '#334155' },
]

export const TONES: { id: ToneId; label: string; light: Record<string, string>; dark: Record<string, string> }[] = [
  { id: 'white', label: 'White', light: { bg: '#ffffff', card: '#ffffff', muted: '#f4f4f5', border: '#e4e4e7' }, dark: { bg: '#09090b', card: '#111113', muted: '#1c1c1f', border: '#27272a' } },
  { id: 'slate', label: 'Slate', light: { bg: '#f8fafc', card: '#ffffff', muted: '#f1f5f9', border: '#e2e8f0' }, dark: { bg: '#0b1020', card: '#131a2e', muted: '#1c2540', border: '#263152' } },
  { id: 'warm', label: 'Warm', light: { bg: '#faf8f5', card: '#ffffff', muted: '#f3efe9', border: '#e7e0d6' }, dark: { bg: '#14110e', card: '#1c1814', muted: '#27221c', border: '#383028' } },
  { id: 'cool', label: 'Cool', light: { bg: '#f3f7fc', card: '#ffffff', muted: '#e9f0f8', border: '#d7e2ef' }, dark: { bg: '#0a1218', card: '#111c25', muted: '#192832', border: '#243746' } },
  { id: 'gray', label: 'Gray', light: { bg: '#f3f4f6', card: '#ffffff', muted: '#e9eaee', border: '#d9dbe0' }, dark: { bg: '#101114', card: '#181a1f', muted: '#22252b', border: '#31353d' } },
]

export const RADII: { id: RadiusId; label: string; value: string }[] = [
  { id: 'none', label: 'None', value: '0rem' },
  { id: 'sm', label: 'Small', value: '0.25rem' },
  { id: 'md', label: 'Medium', value: '0.5rem' },
  { id: 'lg', label: 'Large', value: '0.75rem' },
  { id: 'xl', label: 'Extra large', value: '1rem' },
]

export const FONTS: { id: FontId; label: string; stack: string }[] = [
  { id: 'sans', label: 'System sans', stack: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif' },
  { id: 'serif', label: 'Serif', stack: 'ui-serif, Georgia, Cambria, "Times New Roman", serif' },
  { id: 'rounded', label: 'Rounded', stack: 'ui-rounded, "SF Pro Rounded", "Hiragino Maru Gothic ProN", Nunito, Quicksand, system-ui, sans-serif' },
  { id: 'mono', label: 'Mono', stack: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' },
]

export const PRESETS: { id: string; label: string; theme: ThemeChoice }[] = [
  { id: 'clean', label: 'Clean', theme: { primary: '#2563eb', accent: '#059669', tone: 'white', radius: 'md', font: 'sans', scheme: 'light' } },
  { id: 'bold', label: 'Bold', theme: { primary: '#7c3aed', accent: '#ea580c', tone: 'slate', radius: 'sm', font: 'sans', scheme: 'light' } },
  { id: 'soft', label: 'Soft', theme: { primary: '#0d9488', accent: '#e11d48', tone: 'warm', radius: 'xl', font: 'rounded', scheme: 'light' } },
  { id: 'dark', label: 'Dark', theme: { primary: '#6366f1', accent: '#0d9488', tone: 'slate', radius: 'lg', font: 'sans', scheme: 'dark' } },
]

export const isHex = (s: string) => /^#[0-9a-f]{6}$/i.test(s)

/** Black or white, whichever reads better on `hex` (WCAG relative luminance). */
function onColour(hex: string, light: string, dark: string): string {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
  return lum > 0.4 ? dark : light
}

function tokens(t: ThemeChoice, mode: 'light' | 'dark'): Record<string, string> {
  const tone = (TONES.find((x) => x.id === t.tone) ?? TONES[1])[mode]
  const p = t.primary
  if (mode === 'light') {
    return {
      '--background': tone.bg,
      '--card': tone.card,
      '--muted': tone.muted,
      '--subtle': tone.bg,
      '--border': tone.border,
      '--input': `color-mix(in oklab, ${tone.border} 80%, #0f172a)`,
      '--primary': p,
      '--primary-foreground': onColour(p, '#ffffff', '#0f172a'),
      '--primary-soft': `color-mix(in oklab, ${p} 10%, ${tone.card})`,
      '--ring': `color-mix(in oklab, ${p} 25%, transparent)`,
      '--info': p,
      '--info-soft': `color-mix(in oklab, ${p} 10%, ${tone.card})`,
      '--accent': t.accent,
      '--chart-1': p,
      '--chart-2': t.accent,
    }
  }
  // dark: lift the colour toward white so it keeps its contrast on a dark surface
  const lifted = `color-mix(in oklab, ${p} 70%, white)`
  const liftedHex = mixHex(p, '#ffffff', 0.3)
  return {
    '--background': tone.bg,
    '--card': tone.card,
    '--muted': tone.muted,
    '--subtle': tone.bg,
    '--border': tone.border,
    '--input': `color-mix(in oklab, ${tone.border} 75%, white)`,
    '--primary': lifted,
    '--primary-foreground': onColour(liftedHex, '#ffffff', tone.bg),
    '--primary-soft': `color-mix(in oklab, ${p} 22%, ${tone.card})`,
    '--ring': `color-mix(in oklab, ${lifted} 35%, transparent)`,
    '--info': lifted,
    '--info-soft': `color-mix(in oklab, ${p} 22%, ${tone.card})`,
    '--accent': `color-mix(in oklab, ${t.accent} 70%, white)`,
    '--chart-1': lifted,
    '--chart-2': `color-mix(in oklab, ${t.accent} 70%, white)`,
  }
}

function mixHex(a: string, b: string, w: number): string {
  const ch = (h: string, i: number) => parseInt(h.slice(i, i + 2), 16)
  return '#' + [1, 3, 5].map((i) => Math.round(ch(a, i) * (1 - w) + ch(b, i) * w).toString(16).padStart(2, '0')).join('')
}

const block = (sel: string, vars: Record<string, string>) => `${sel} {\n${Object.entries(vars).map(([k, v]) => `  ${k}: ${v};`).join('\n')}\n}\n`

const MARK = /^\/\* theme:(\{.*\}) \*\/$/m

/** The contents of theme.css for a choice. A forced scheme puts that mode's tokens on :root (and the other mode's on .dark for light). */
export function themeCss(t: ThemeChoice): string {
  const light = tokens(t, 'light')
  const dark = tokens(t, 'dark')
  const shared = {
    '--radius': (RADII.find((r) => r.id === t.radius) ?? RADII[2]).value,
    '--font-sans': (FONTS.find((f) => f.id === t.font) ?? FONTS[0]).stack,
  }
  const root = t.scheme === 'dark' ? dark : light
  return (
    `/* Theme overrides, written by the theme panel; loaded after the kit theme and index.css. */\n/* theme:${JSON.stringify(t)} */\n` +
    block(':root', { ...root, ...shared }) +
    block('.dark', t.scheme === 'light' ? light : dark)
  )
}

/** The choices stored in a theme.css (null: none, e.g. an empty or hand-written file). */
export function readTheme(css: string | undefined): ThemeChoice | null {
  const m = css ? MARK.exec(css) : null
  if (!m) return null
  try {
    const raw = JSON.parse(m[1]) as Partial<ThemeChoice>
    const t = { ...DEFAULT_THEME, ...raw }
    return isHex(t.primary) && isHex(t.accent) ? t : null
  } catch {
    return null
  }
}
