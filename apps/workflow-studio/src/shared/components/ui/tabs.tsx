/**
 * Underline tabs (Base UI Tabs): no rule under the list; one 2px primary indicator slides between tabs
 * (`Tabs.Indicator` exposes `--active-tab-left/width`).
 */
import { Tabs as TabsPrimitive } from '@base-ui/react/tabs'
import { cn } from '@/shared/lib/utils'

function Tabs({ className, ...props }: TabsPrimitive.Root.Props) {
  return <TabsPrimitive.Root data-slot="tabs" className={cn('flex flex-col min-h-0', className)} {...props} />
}

function TabsList({ className, children, ...props }: TabsPrimitive.List.Props) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      className={cn('relative flex h-10 shrink-0 items-stretch gap-0.5 px-2 shadow-[inset_0_-1px_0_var(--hairline)]', className)}
      {...props}
    >
      {children}
      <TabsPrimitive.Indicator
        data-slot="tabs-indicator"
        className="absolute bottom-0 left-(--active-tab-left) h-0.5 w-(--active-tab-width) rounded-full bg-primary transition-[left,width] duration-(--d-base) ease-(--ease-io)"
      />
    </TabsPrimitive.List>
  )
}

function TabsTab({ className, ...props }: TabsPrimitive.Tab.Props) {
  return (
    <TabsPrimitive.Tab
      data-slot="tabs-tab"
      className={cn(
        'inline-flex items-center gap-1.5 px-2.5 text-[12.5px] font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:text-foreground data-[active]:text-foreground [&_svg]:size-3.5',
        className,
      )}
      {...props}
    />
  )
}

function TabsPanel({ className, ...props }: TabsPrimitive.Panel.Props) {
  return <TabsPrimitive.Panel data-slot="tabs-panel" className={cn('min-h-0 flex-1 outline-none', className)} {...props} />
}

export { Tabs, TabsList, TabsTab, TabsPanel }
