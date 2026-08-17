"use client"

import { Tabs as TabsPrimitive } from "@base-ui/react/tabs"

import { cn } from "@/shared/lib/utils"

/**
 * Tabs.
 *
 * The active tab is REPEATED state — there is one on every lens strip, every
 * filter bar, every dock — so it does not take the accent. INSTRUMENT rule 1:
 * the accent means scope and liveness at roughly four uses per screen, and a
 * signal that appears seven times in one strip is a texture, not a signal.
 * Active is bought with value (`--t1`), weight and an elevated indicator
 * instead. The one tab strip that legitimately spends the accent is the global
 * route nav in the app shell, which marks *where you are* — and it draws its
 * own underline rather than using this component.
 */
function Tabs({ className, ...props }: TabsPrimitive.Root.Props) {
  return (
    <TabsPrimitive.Root
      data-slot="tabs"
      className={cn("flex flex-col gap-2", className)}
      {...props}
    />
  )
}

function TabsList({ className, ...props }: TabsPrimitive.List.Props) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      className={cn("relative flex items-center gap-1", className)}
      {...props}
    />
  )
}

function TabsTab({ className, ...props }: TabsPrimitive.Tab.Props) {
  return (
    <TabsPrimitive.Tab
      data-slot="tabs-tab"
      className={cn(
        "relative z-10 inline-flex h-7 shrink-0 cursor-pointer items-center justify-center rounded-md px-2.5 text-body font-normal whitespace-nowrap text-muted-foreground transition-colors select-none hover:text-foreground data-selected:font-medium data-selected:text-foreground disabled:pointer-events-none disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
}

/**
 * The moving surface behind the selected tab. Elevation, not a border —
 * rule 3 deletes ~90% of borders and groups by elevation and space instead.
 */
function TabsIndicator({ className, ...props }: TabsPrimitive.Indicator.Props) {
  return (
    <TabsPrimitive.Indicator
      data-slot="tabs-indicator"
      className={cn(
        "absolute top-0 left-0 z-0 h-7 w-(--active-tab-width) translate-x-(--active-tab-left) rounded-md bg-accent transition-all duration-200 ease-out",
        className
      )}
      {...props}
    />
  )
}

function TabsPanel({ className, ...props }: TabsPrimitive.Panel.Props) {
  return (
    <TabsPrimitive.Panel
      data-slot="tabs-panel"
      className={cn("min-h-0 flex-1 outline-none", className)}
      {...props}
    />
  )
}

export { Tabs, TabsList, TabsTab, TabsIndicator, TabsPanel }
