/**
 * Column rows (name · type · required · default) for the New table and Columns dialogs
 * (UI-DESIGN §3.4). `created_at`, `id` and `updated_at` are row metadata, never user columns.
 */
import { Plus, X } from 'lucide-react'
import type { ColumnType } from '@/shared/api/types'
import { Button } from '@/shared/components/ui/button'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { Input } from '@/shared/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { cn } from '@/shared/lib/utils'
import { COLUMN_TYPES, columnNameError, newColumnDraft, type ColumnDraft } from '../lib/columns'

export function ColumnsEditor({
  value,
  onChange,
  testId = 'columns',
}: {
  value: ColumnDraft[]
  onChange: (v: ColumnDraft[]) => void
  testId?: string
}) {
  const set = (key: string, patch: Partial<ColumnDraft>) => onChange(value.map((d) => (d.key === key ? { ...d, ...patch } : d)))
  return (
    <div className="flex flex-col gap-1.5" data-testid={testId}>
      <div className="grid grid-cols-[minmax(0,1fr)_112px_70px_minmax(0,0.8fr)_24px] gap-2 px-0.5 text-[11px] font-medium text-muted-foreground">
        <span>Name</span>
        <span>Type</span>
        <span>Required</span>
        <span>Default</span>
        <span />
      </div>
      {value.map((d, i) => {
        const err = columnNameError(d, value)
        return (
          <div key={d.key} className="flex flex-col gap-0.5" data-testid={`${testId}-row`}>
            <div className="grid grid-cols-[minmax(0,1fr)_112px_70px_minmax(0,0.8fr)_24px] items-center gap-2">
              <Input
                value={d.name}
                onChange={(e) => set(d.key, { name: e.target.value.replace(/\s/g, '_') })}
                placeholder={i === 0 ? 'reason' : 'column'}
                className={cn('h-7 font-mono text-xs')}
                aria-invalid={!!err}
                autoFocus={i === value.length - 1 && !d.name && value.length > 1}
                data-testid={`${testId}-name-${i}`}
              />
              <Select
                value={d.type}
                onValueChange={(v) => v && set(d.key, { type: v as ColumnType })}
                items={COLUMN_TYPES.map((t) => ({ value: t, label: t }))}
              >
                <SelectTrigger size="sm" className="h-7 w-full font-mono text-xs" data-testid={`${testId}-type-${i}`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {COLUMN_TYPES.map((t) => (
                    <SelectItem key={t} value={t} className="font-mono text-xs" data-testid={`${testId}-type-${i}-${t}`}>
                      {t}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span className="flex justify-center">
                <Checkbox
                  checked={d.required}
                  onCheckedChange={(c) => set(d.key, { required: !!c })}
                  aria-label={`${d.name || 'column'} required`}
                  data-testid={`${testId}-required-${i}`}
                />
              </span>
              <Input
                value={d.def}
                onChange={(e) => set(d.key, { def: e.target.value })}
                placeholder="none"
                className="h-7 font-mono text-xs"
                data-testid={`${testId}-default-${i}`}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label={`Remove ${d.name || 'column'}`}
                onClick={() => onChange(value.filter((x) => x.key !== d.key))}
                data-testid={`${testId}-remove-${i}`}
              >
                <X />
              </Button>
            </div>
            {err && <span className="text-[11.5px] text-destructive">{d.name}: {err}</span>}
          </div>
        )
      })}
      <div>
        <Button
          type="button"
          variant="ghost"
          size="xs"
          onClick={() => onChange([...value, newColumnDraft()])}
          data-testid={`${testId}-add`}
        >
          <Plus /> Add column
        </Button>
      </div>
    </div>
  )
}
