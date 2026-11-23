import { useState } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/shared/lib/utils'

/** Host-chip input (allowed hosts, egress allowlist). Enter, comma or blur commits a chip. */
export function ChipsInput({
  value,
  onChange,
  placeholder = 'add host…',
  className,
  testId,
  validate,
}: {
  value: string[]
  onChange: (v: string[]) => void
  placeholder?: string
  className?: string
  testId?: string
  validate?: (s: string) => boolean
}) {
  const [draft, setDraft] = useState('')
  const commit = () => {
    const parts = draft
      .split(/[\s,]+/)
      .map((p) => p.trim().replace(/^https?:\/\//, '').replace(/\/.*$/, ''))
      .filter(Boolean)
      .filter((p) => !validate || validate(p))
    if (parts.length) onChange([...new Set([...value, ...parts])])
    setDraft('')
  }
  return (
    <div
      className={cn(
        'flex min-h-8 flex-wrap items-center gap-1 rounded-ctl bg-recess px-1.5 py-1 focus-within:shadow-focus',
        className,
      )}
      data-testid={testId}
    >
      {value.map((h) => (
        <span key={h} className="inline-flex h-[22px] items-center gap-1 rounded-chip bg-raise px-1.5 font-mono text-[11.5px] shadow-raise-sm">
          {h}
          <button
            type="button"
            aria-label={`Remove ${h}`}
            className="text-muted-foreground hover:text-foreground"
            onClick={() => onChange(value.filter((x) => x !== h))}
          >
            <X className="size-3" />
          </button>
        </span>
      ))}
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault()
            commit()
          } else if (e.key === 'Backspace' && !draft && value.length) {
            onChange(value.slice(0, -1))
          }
        }}
        onBlur={commit}
        placeholder={placeholder}
        className="min-w-24 flex-1 bg-transparent px-1 text-[13px] outline-none placeholder:text-muted-foreground/70"
        data-testid={testId ? `${testId}-input` : undefined}
      />
    </div>
  )
}
