/** Small shared controls for the wiring sections: a labelled section, a select over a label map, a one-line note. */
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'

export function Section({ title, hint, children, testId }: { title: string; hint?: React.ReactNode; children: React.ReactNode; testId: string }) {
  return (
    <div className="flex flex-col gap-1.5" data-testid={testId}>
      <div className="flex items-center gap-2 text-[11.5px]">
        <span className="font-semibold">{title}</span>
        {hint && <span className="min-w-0 truncate text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </div>
  )
}

/**
 * A select over `items` (value → label); `placeholder` shows in the trigger until one is picked.
 * Nothing picked is `value={null}`, never a disabled sentinel item: Base UI aligns the popup on the selected item, and a
 * disabled selected item left the list opened onto (and scrolled to) the placeholder alone.
 */
export function Pick({ value, items, onChange, testId, disabled, placeholder }: {
  value: string | null
  items: Record<string, string>
  onChange: (v: string) => void
  testId: string
  disabled?: boolean
  placeholder?: string
}) {
  const current = value !== null && Object.hasOwn(items, value) ? value : null
  return (
    <Select value={current} onValueChange={(v) => typeof v === 'string' && Object.hasOwn(items, v) && onChange(v)} items={items} disabled={disabled}>
      <SelectTrigger size="sm" className="w-full min-w-0" data-testid={testId}>
        <SelectValue placeholder={placeholder ?? 'Choose…'} />
      </SelectTrigger>
      <SelectContent alignItemWithTrigger={false}>
        {Object.entries(items).map(([k, label]) => (
          <SelectItem key={k} value={k}>
            {label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

export function Note({ children, testId, tone = 'muted' }: { children: React.ReactNode; testId?: string; tone?: 'muted' | 'error' }) {
  return (
    <p className={tone === 'error' ? 'text-[11.5px] text-destructive' : 'text-[11.5px] text-muted-foreground'} data-testid={testId}>
      {children}
    </p>
  )
}
