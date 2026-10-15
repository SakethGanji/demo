/**
 * Segmented control (promoted from ApiTesterPanel's PillTabs, UI-DESIGN §7.1), built on
 * Base UI ToggleGroup so it is a real single-select group with roving focus.
 */
import { ToggleGroup } from '@base-ui/react/toggle-group'
import { Toggle } from '@base-ui/react/toggle'
import { cn } from '@/shared/lib/utils'

export interface SegmentedOption<K extends string> {
  key: K
  label: React.ReactNode
  count?: number
  icon?: React.ReactNode
  disabled?: boolean
  testId?: string
}

export function Segmented<K extends string>({
  value,
  onChange,
  options,
  size = 'sm',
  className,
  'aria-label': ariaLabel,
  ...rest
}: {
  value: K
  onChange: (k: K) => void
  options: SegmentedOption<K>[]
  size?: 'xs' | 'sm'
  className?: string
  'aria-label'?: string
  'data-testid'?: string
}) {
  return (
    <ToggleGroup
      value={[value]}
      onValueChange={(v) => {
        const next = v[0] as K | undefined
        if (next) onChange(next)
      }}
      aria-label={ariaLabel}
      className={cn('inline-flex items-center gap-0.5 rounded-lg bg-muted p-0.5', className)}
      {...rest}
    >
      {options.map((opt) => (
        <Toggle
          key={opt.key}
          value={opt.key}
          disabled={opt.disabled}
          data-testid={opt.testId}
          className={cn(
            'inline-flex items-center gap-1 rounded-md font-medium text-muted-foreground transition-all duration-150 hover:text-foreground disabled:opacity-50 data-[pressed]:bg-card data-[pressed]:text-foreground data-[pressed]:shadow-xs',
            size === 'xs' ? 'h-6 px-2 text-[11px]' : 'h-6 px-2.5 text-xs',
          )}
        >
          {opt.icon}
          <span>{opt.label}</span>
          {opt.count != null && opt.count > 0 && <span className="text-[11px] opacity-60">{opt.count}</span>}
        </Toggle>
      ))}
    </ToggleGroup>
  )
}
