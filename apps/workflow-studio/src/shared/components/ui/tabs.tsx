/**
 * Underline tabs (Base UI Tabs; the editor-tabs.tsx look), UI-DESIGN §7.1.
 */
import { Tabs as TabsPrimitive } from '@base-ui/react/tabs'
import { cn } from '@/shared/lib/utils'

function Tabs({ className, ...props }: TabsPrimitive.Root.Props) {
  return <TabsPrimitive.Root data-slot="tabs" className={cn('flex flex-col min-h-0', className)} {...props} />
}

function TabsList({ className, ...props }: TabsPrimitive.List.Props) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      className={cn('relative flex h-10 shrink-0 items-stretch gap-0.5 border-b border-border px-2', className)}
      {...props}
    />
  )
}

function TabsTab({ className, ...props }: TabsPrimitive.Tab.Props) {
  return (
    <TabsPrimitive.Tab
      data-slot="tabs-tab"
      className={cn(
        '-mb-px inline-flex items-center gap-1.5 border-b-2 border-transparent px-2.5 text-[12.5px] font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:text-foreground data-[active]:border-primary data-[active]:text-foreground [&_svg]:size-3.5',
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
