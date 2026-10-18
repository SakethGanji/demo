import { Switch as SwitchPrimitive } from '@base-ui/react/switch'
import { cn } from '@/shared/lib/utils'

function Switch({ className, ...props }: SwitchPrimitive.Root.Props) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        'inline-flex h-[22px] w-[38px] shrink-0 items-center rounded-full bg-recess p-[3px] shadow-[inset_0_1px_3px_var(--shade)] outline-none transition-colors duration-(--d-base) focus-visible:shadow-focus disabled:opacity-50 data-[checked]:brand-fill data-[checked]:shadow-none',
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="block size-4 rounded-full bg-primary-foreground shadow-raise-sm transition-transform duration-(--d-base) ease-(--ease-out) data-[checked]:translate-x-4" />
    </SwitchPrimitive.Root>
  )
}

export { Switch }
