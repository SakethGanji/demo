import { Switch as SwitchPrimitive } from '@base-ui/react/switch'
import { cn } from '@/shared/lib/utils'

function Switch({ className, ...props }: SwitchPrimitive.Root.Props) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        'inline-flex h-[18px] w-8 shrink-0 items-center rounded-full border border-transparent bg-input p-px outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50 data-[checked]:bg-primary',
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="block size-4 rounded-full bg-background shadow-xs transition-transform data-[checked]:translate-x-3.5" />
    </SwitchPrimitive.Root>
  )
}

export { Switch }
