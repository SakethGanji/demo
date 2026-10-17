import { Switch as SwitchPrimitive } from '@base-ui/react/switch'
import { cn } from '@/shared/lib/utils'

function Switch({ className, ...props }: SwitchPrimitive.Root.Props) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        'inline-flex h-[18px] w-8 shrink-0 items-center rounded-full bg-recess p-px shadow-[inset_0_0_0_1px_var(--input)] outline-none transition-colors duration-(--d-base) focus-visible:shadow-focus disabled:opacity-50 data-[checked]:bg-primary data-[checked]:shadow-none',
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="block size-4 rounded-full bg-primary-foreground shadow-raise-sm transition-transform duration-(--d-base) ease-(--ease-out) data-[checked]:translate-x-3.5" />
    </SwitchPrimitive.Root>
  )
}

export { Switch }
