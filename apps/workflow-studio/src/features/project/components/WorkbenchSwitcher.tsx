/**
 * The workbench index (modules, their routes, tables, drafts, "new …") as a switcher that opens from the page's
 * own header, the file tab on Backend and the "Tables" crumb on Data. Every project page shares one frame with
 * no side panel, so the index lives here instead of in a column. It closes when you pick something.
 */
import { useState, type ReactNode } from 'react'
import { useRouterState } from '@tanstack/react-router'
import { ChevronsUpDown } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { cn } from '@/shared/lib/utils'
import { WorkbenchIndex } from './WorkbenchIndex'

export function WorkbenchSwitcher({ children, className, label, testId }: { children: ReactNode; className?: string; label: string; testId?: string }) {
  // open for the page it was opened on: picking anything navigates, which closes it
  const href = useRouterState({ select: (s) => s.location.href })
  const [openOn, setOpenOn] = useState<string | null>(null)
  return (
    <Popover open={openOn === href} onOpenChange={(o) => setOpenOn(o ? href : null)}>
      <PopoverTrigger
        className={cn('group/sw inline-flex min-w-0 items-center gap-1.5 rounded-ctl outline-none hover:bg-foreground/5 focus-visible:shadow-focus data-[popup-open]:bg-foreground/5', className)}
        aria-label={label}
        data-testid={testId}
      >
        {children}
        <ChevronsUpDown className="size-3 shrink-0 text-muted-foreground group-hover/sw:text-foreground" />
      </PopoverTrigger>
      <PopoverContent side="bottom" align="start" sideOffset={8} className="flex max-h-[min(560px,70vh)] w-[min(300px,calc(100vw-16px))] flex-col p-0" data-testid="outline-panel">
        <WorkbenchIndex />
      </PopoverContent>
    </Popover>
  )
}
