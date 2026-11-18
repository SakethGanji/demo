import { useState } from 'react'

/** A numeric box you can type freely into. The value is clamped (and snapped to `step`) when you leave it or press Enter. */
export function NumberInput({ value, onChange, min, max, step, format = String, label }: {
  value: number; onChange: (v: number) => void; min: number; max: number; step?: number; format?: (v: number) => string; label: string
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const commit = () => {
    if (draft === null) return
    const v = Number(draft)
    if (draft.trim() !== '' && !Number.isNaN(v)) {
      const snapped = step ? Number((Math.round(v / step) * step).toFixed(6)) : v
      onChange(Math.min(max, Math.max(min, snapped)))
    }
    setDraft(null)
  }
  return (
    <input className="num-input" inputMode="decimal" aria-label={label} value={draft ?? format(value)}
      onChange={(e) => setDraft(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()} />
  )
}
