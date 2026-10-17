import { Checkbox as CheckboxPrimitive } from '@base-ui/react/checkbox'
import { Check, Minus } from 'lucide-react'
import { cn } from '@/shared/lib/utils'

function Checkbox({ className, ...props }: CheckboxPrimitive.Root.Props) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      className={cn(
        'peer inline-flex size-4 shrink-0 items-center justify-center rounded-chip bg-recess shadow-[inset_0_0_0_1px_var(--input)] outline-none transition-colors focus-visible:shadow-focus disabled:cursor-not-allowed disabled:opacity-50 data-[checked]:bg-primary data-[checked]:text-primary-foreground data-[checked]:shadow-none data-[indeterminate]:bg-primary data-[indeterminate]:text-primary-foreground data-[indeterminate]:shadow-none',
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator
        className="flex items-center justify-center [&_svg]:size-3"
        render={(p, state) => <span {...p}>{state.indeterminate ? <Minus strokeWidth={3} /> : <Check strokeWidth={3} />}</span>}
      />
    </CheckboxPrimitive.Root>
  )
}

export { Checkbox }
