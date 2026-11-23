/**
 * The theme panel: restyles the whole app from a few choices (presets, colour, accent, background, corners, font, scheme).
 * Every change rewrites the app-owned theme.css as one visual edit (shared undo history; the saver follows the history),
 * never the kit. Mount it in a popover or a side panel; it reads the app's files from the document store.
 */
import { useAppDocumentStore } from '../../stores'
import { useEditHistory } from '../history'
import { cn } from '@/shared/lib/utils'
import { DEFAULT_THEME, FONTS, PRESETS, RADII, SWATCHES, THEME_PATH, TONES, isHex, readTheme, themeCss, type SchemeId, type ThemeChoice } from './theme'

function Label({ children }: { children: React.ReactNode }) {
  return <div className="text-[11.5px] font-medium text-foreground">{children}</div>
}

function Chip({ on, onClick, disabled, children, testid }: { on: boolean; onClick: () => void; disabled: boolean; children: React.ReactNode; testid?: string }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      disabled={disabled}
      onClick={onClick}
      data-testid={testid}
      className={cn('h-6 rounded-chip px-2 text-[11.5px] text-muted-foreground hover:bg-raise disabled:cursor-not-allowed disabled:opacity-60', on && 'bg-raise text-foreground')}
    >
      {children}
    </button>
  )
}

function ColourRow({ label, value, onChange, disabled, swatches, id }: { label: string; value: string; onChange: (hex: string) => void; disabled: boolean; swatches: boolean; id: string }) {
  const known = SWATCHES.some((s) => s.hex.toLowerCase() === value.toLowerCase())
  return (
    <div className="flex flex-col gap-1">
      <Label>{label}</Label>
      <div className="flex flex-wrap items-center gap-1" role="radiogroup" aria-label={label}>
        {swatches &&
          SWATCHES.map((s) => {
            const on = s.hex.toLowerCase() === value.toLowerCase()
            return (
              <button
                key={s.hex}
                type="button"
                role="radio"
                aria-checked={on}
                aria-label={s.label}
                title={s.label}
                disabled={disabled}
                onClick={() => onChange(s.hex)}
                className={cn('size-5 rounded-chip shadow-[inset_0_0_0_1px_var(--hairline)] disabled:cursor-not-allowed', on && 'outline-2 outline-offset-1 outline-ring')}
                style={{ background: s.hex }}
                data-testid={`theme-${id}-${s.label}`}
              />
            )
          })}
        <label
          title="Custom colour"
          className={cn('relative size-5 cursor-pointer overflow-hidden rounded-chip shadow-[inset_0_0_0_1px_var(--hairline)]', !known && 'outline-2 outline-offset-1 outline-ring', disabled && 'cursor-not-allowed opacity-60')}
          style={{ background: known ? 'conic-gradient(#ef4444, #eab308, #22c55e, #06b6d4, #6366f1, #ec4899, #ef4444)' : value }}
        >
          <input type="color" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} className="absolute inset-0 size-full cursor-pointer opacity-0" aria-label={`${label}, custom`} data-testid={`theme-${id}-custom`} />
        </label>
        <span className="font-mono text-[10.5px] text-muted-foreground">{value}</span>
      </div>
    </div>
  )
}

export function ThemePanel({ disabled = false }: { disabled?: boolean }) {
  const css = useAppDocumentStore((s) => s.files?.find((f) => f.path === THEME_PATH)?.content)
  const theme = readTheme(css) ?? DEFAULT_THEME
  const custom = css != null && css.trim() !== '' && !readTheme(css)

  const apply = (next: ThemeChoice, label: string, coalesce?: string) => {
    if (disabled) return
    if (!isHex(next.primary) || !isHex(next.accent)) return
    const before = useAppDocumentStore.getState().files?.find((f) => f.path === THEME_PATH)?.content ?? null
    const after = themeCss(next)
    if (before === after) return
    useEditHistory.getState().commit(label, [{ path: THEME_PATH, before, after }], coalesce)
  }
  const set = (patch: Partial<ThemeChoice>, label: string, coalesce?: string) => apply({ ...theme, ...patch }, label, coalesce)
  const preset = PRESETS.find((p) => themeCss(p.theme) === css)?.id

  return (
    <div className="flex w-[260px] flex-col gap-3 p-1" data-testid="theme-panel">
      {custom && <div className="text-[11px] text-muted-foreground">theme.css was edited by hand; picking anything here replaces it.</div>}

      <div className="flex flex-col gap-1">
        <Label>Presets</Label>
        <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Presets">
          {PRESETS.map((p) => (
            <Chip key={p.id} on={preset === p.id} disabled={disabled} onClick={() => apply(p.theme, `theme ${p.label.toLowerCase()}`)} testid={`theme-preset-${p.id}`}>
              {p.label}
            </Chip>
          ))}
        </div>
      </div>

      <ColourRow id="primary" label="Brand colour" value={theme.primary} swatches disabled={disabled} onChange={(hex) => set({ primary: hex }, 'brand colour', 'theme:primary')} />
      <ColourRow id="accent" label="Accent" value={theme.accent} swatches disabled={disabled} onChange={(hex) => set({ accent: hex }, 'accent colour', 'theme:accent')} />

      <div className="flex flex-col gap-1">
        <Label>Background</Label>
        <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Background">
          {TONES.map((t) => (
            <button
              key={t.id}
              type="button"
              role="radio"
              aria-checked={theme.tone === t.id}
              aria-label={t.label}
              title={t.label}
              disabled={disabled}
              onClick={() => set({ tone: t.id }, `background ${t.label.toLowerCase()}`)}
              className={cn('size-5 rounded-chip shadow-[inset_0_0_0_1px_var(--hairline)] disabled:cursor-not-allowed', theme.tone === t.id && 'outline-2 outline-offset-1 outline-ring')}
              style={{ background: t.light.bg }}
              data-testid={`theme-tone-${t.id}`}
            />
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <Label>Corners</Label>
        <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Corners">
          {RADII.map((r) => (
            <Chip key={r.id} on={theme.radius === r.id} disabled={disabled} onClick={() => set({ radius: r.id }, `corners ${r.label.toLowerCase()}`)} testid={`theme-radius-${r.id}`}>
              {r.label}
            </Chip>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <Label>Font</Label>
        <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Font">
          {FONTS.map((f) => (
            <Chip key={f.id} on={theme.font === f.id} disabled={disabled} onClick={() => set({ font: f.id }, `font ${f.label.toLowerCase()}`)} testid={`theme-font-${f.id}`}>
              <span style={{ fontFamily: f.stack }}>{f.label}</span>
            </Chip>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <Label>Default mode</Label>
        <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Default mode">
          {(['system', 'light', 'dark'] as SchemeId[]).map((s) => (
            <Chip key={s} on={theme.scheme === s} disabled={disabled} onClick={() => set({ scheme: s }, `default mode ${s}`)} testid={`theme-scheme-${s}`}>
              {s === 'system' ? 'Follow system' : s === 'light' ? 'Light' : 'Dark'}
            </Chip>
          ))}
        </div>
      </div>
    </div>
  )
}
