/**
 * SearchMenu (DESIGN §8): a glass popover with a recessed search box, group labels, hairline separators
 * and a raised highlighted item. Built on Popover + cmdk, so it is keyboard-driven (↑↓ ↵ esc).
 * `RoutePicker` is the route combobox (grouped by module, with method chips) built on it.
 */
import * as React from 'react'
import { Command as Cmdk } from 'cmdk'
import { Check, ChevronDown, Search } from 'lucide-react'
import { cn } from '@/shared/lib/utils'
import { MethodChip } from '@/shared/components/api/MethodBadge'
import { Popover, PopoverContent, PopoverTrigger } from './popover'

export interface SearchMenuItem {
  key: string
  label: React.ReactNode
  /** text matched by the search (defaults to the key) */
  keywords?: string
  icon?: React.ReactNode
  sub?: React.ReactNode
  checked?: boolean
  disabled?: boolean
  testId?: string
  onSelect: () => void
}

export interface SearchMenuGroup {
  label?: string
  items: SearchMenuItem[]
}

export function SearchMenuList({
  groups,
  placeholder = 'Search…',
  empty = 'Nothing found',
  footer,
  className,
  testId,
}: {
  groups: SearchMenuGroup[]
  placeholder?: string
  empty?: React.ReactNode
  footer?: React.ReactNode
  className?: string
  testId?: string
}) {
  return (
    <Cmdk className={cn('flex max-h-[min(420px,var(--available-height,420px))] flex-col', className)} loop data-testid={testId}>
      <div className="p-1 pb-0">
        <div className="flex h-8 items-center gap-2 rounded-ctl bg-recess px-2.5" data-recess>
          <Search className="size-3.5 shrink-0 text-muted-foreground" />
          <Cmdk.Input
            placeholder={placeholder}
            className="h-full min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground"
            data-testid={testId ? `${testId}-search` : undefined}
          />
        </div>
      </div>
      <Cmdk.List className="min-h-0 flex-1 overflow-y-auto p-1">
        <Cmdk.Empty className="py-6 text-center text-[12.5px] text-muted-foreground">{empty}</Cmdk.Empty>
        {groups.map((g, gi) => (
          <React.Fragment key={g.label ?? gi}>
            {gi > 0 && <Cmdk.Separator className="-mx-1 my-1 h-px bg-hairline" />}
            <Cmdk.Group
              heading={g.label}
              className="**:[[cmdk-group-heading]]:px-2 **:[[cmdk-group-heading]]:pt-1.5 **:[[cmdk-group-heading]]:pb-1 **:[[cmdk-group-heading]]:text-[11.5px] **:[[cmdk-group-heading]]:font-medium **:[[cmdk-group-heading]]:text-muted-foreground"
            >
              {g.items.map((it) => (
                <Cmdk.Item
                  key={it.key}
                  value={`${it.key} ${it.keywords ?? ''}`}
                  disabled={it.disabled}
                  onSelect={it.onSelect}
                  data-testid={it.testId}
                  className="flex h-8 cursor-default items-center gap-2 rounded-chip px-2 text-[13px] outline-none select-none data-[disabled=true]:opacity-50 data-[selected=true]:bg-raise data-[selected=true]:shadow-raise-sm [&_svg]:size-3.5"
                >
                  {it.icon}
                  <span className="min-w-0 flex-1 truncate">{it.label}</span>
                  {it.sub && <span className="shrink-0 text-[11.5px] text-muted-foreground">{it.sub}</span>}
                  {it.checked && <Check className="shrink-0 text-muted-foreground" />}
                </Cmdk.Item>
              ))}
            </Cmdk.Group>
          </React.Fragment>
        ))}
      </Cmdk.List>
      {footer && <div className="flex items-center gap-3 px-3 py-2 text-[11px] text-muted-foreground shadow-[inset_0_1px_0_var(--hairline)]">{footer}</div>}
    </Cmdk>
  )
}

export interface PickerRoute {
  key: string
  method: string
  path: string
  module?: string
  sub?: React.ReactNode
  testId?: string
}

/**
 * Route combobox: the trigger shows the current route (method chip + path); the menu is searchable and
 * grouped by module. Pass `triggerTestId` / `currentTestId` to keep existing test ids.
 */
export function RoutePicker({
  routes,
  value,
  onSelect,
  triggerTestId,
  currentTestId,
  className,
  extra,
}: {
  routes: PickerRoute[]
  value: string | null
  onSelect: (key: string) => void
  triggerTestId?: string
  currentTestId?: string
  className?: string
  /** rendered after the path in the trigger (e.g. a draft badge) */
  extra?: React.ReactNode
}) {
  const [open, setOpen] = React.useState(false)
  const current = routes.find((r) => r.key === value) ?? null
  const groups: SearchMenuGroup[] = []
  for (const r of routes) {
    const label = r.module ? `${r.module}.py` : undefined
    let g = groups.find((x) => x.label === label)
    if (!g) groups.push((g = { label, items: [] }))
    g.items.push({
      key: r.key,
      keywords: `${r.method} ${r.path}`,
      icon: <MethodChip method={r.method} size="sm" />,
      label: <span className="font-mono text-[12px]">{r.path}</span>,
      sub: r.sub,
      checked: r.key === value,
      testId: r.testId,
      onSelect: () => {
        onSelect(r.key)
        setOpen(false)
      },
    })
  }
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        data-testid={triggerTestId}
        className={cn('flex h-[34px] w-full items-center gap-2 rounded-ctl bg-recess pr-2 pl-2.5 text-left hover:bg-(--hover-fill)', className)}
      >
        {current && <MethodChip method={current.method} />}
        <span className="min-w-0 flex-1 truncate font-mono text-[12.5px]" data-testid={currentTestId}>
          {current?.path ?? 'Pick a route'}
        </span>
        {extra}
        <ChevronDown className="size-3.5 text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent className="w-[380px] p-0" align="start">
        <SearchMenuList groups={groups} placeholder="Find a route…" empty="No route matches" />
      </PopoverContent>
    </Popover>
  )
}
