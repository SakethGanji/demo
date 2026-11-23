/**
 * "Where do you want to start?": six cards with small line art. One component for both places:
 *  - `guide`: raised cards inside the setup guide's Well; the chosen one gets the brand ring and its panel opens below;
 *  - `home`: glass Tiles in "Start something new", in the same grid language as the project cards.
 * Choosing a card never navigates by itself; the caller opens that card's first action in place (`panel`).
 */
import { AppWindow, ArrowRight, Database, LayoutTemplate, Route as RouteIcon, Sparkles, Workflow } from 'lucide-react'
import { Tile, Well } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import type { StartKind } from '../../lib/nextSteps'
import { StartArt } from '../../home/startArt'
import { START_CARDS } from './startCards'

const ICON: Record<StartKind, { icon: typeof AppWindow; hue: string }> = {
  interface: { icon: AppWindow, hue: 'text-hue-apps' },
  backend: { icon: RouteIcon, hue: 'text-hue-backend' },
  workflow: { icon: Workflow, hue: 'text-hue-flow' },
  dataset: { icon: Database, hue: 'text-hue-data' },
  template: { icon: LayoutTemplate, hue: 'text-hue-release' },
  ai: { icon: Sparkles, hue: 'text-hue-ai' },
}

export function StartPicker({
  variant,
  value,
  onPick,
  panel,
  disabled,
}: {
  variant: 'guide' | 'home'
  value: StartKind | null
  onPick: (kind: StartKind) => void
  /** the chosen card's first action, shown right under the grid */
  panel?: React.ReactNode
  disabled?: boolean
}) {
  const pick = (k: StartKind) => !disabled && onPick(k)
  return (
    <div className="flex flex-col gap-3">
      <div
        className={cn('grid gap-2', variant === 'guide' ? 'grid-cols-2 sm:grid-cols-3' : 'mt-3 grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3')}
        data-testid="setup-start-picker"
      >
        {START_CARDS.map((c) => {
          const on = value === c.kind
          const { icon: Icon, hue } = ICON[c.kind]
          const common = {
            'aria-expanded': on,
            'data-testid': `setup-start-${c.kind}`,
            'data-selected': on ? 'true' : undefined,
          }
          if (variant === 'guide')
            return (
              <button
                key={c.kind}
                type="button"
                onClick={() => pick(c.kind)}
                disabled={disabled}
                className={cn(
                  'group flex flex-col gap-1.5 rounded-ctl bg-raise p-2.5 text-left shadow-raise-sm transition-[box-shadow,transform] duration-(--d-fast) ease-(--ease-out) hover:-translate-y-px focus-visible:shadow-focus focus-visible:outline-none motion-reduce:hover:translate-y-0',
                  on && 'shadow-[0_0_0_1.5px_var(--ring)]',
                  disabled && !on && 'opacity-60',
                )}
                {...common}
              >
                <span className={cn('opacity-80 transition-opacity duration-(--d-fast) group-hover:opacity-100', on && 'opacity-100')}>
                  <StartArt kind={c.kind} />
                </span>
                <span className="flex items-center gap-1.5 text-[13px] font-semibold tracking-[-0.01em]">
                  <Icon className={cn('size-3.5 shrink-0', hue)} />
                  {c.title}
                </span>
                <span className="text-[11.5px] leading-[1.45] text-muted-foreground">{c.blurb}</span>
              </button>
            )
          return (
            <Tile
              key={c.kind}
              interactive
              role="button"
              tabIndex={0}
              onClick={() => pick(c.kind)}
              onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), pick(c.kind))}
              className={cn(
                'group flex flex-col focus-visible:shadow-focus focus-visible:outline-none',
                on && 'shadow-[0_0_0_1.5px_var(--ring)]',
                disabled && !on && 'opacity-60',
              )}
              {...common}
            >
              <Well className="mx-2 mt-2 grid h-24 place-items-center overflow-hidden px-6 py-3">
                <StartArt kind={c.kind} />
              </Well>
              <div className="flex flex-1 flex-col gap-[3px] px-4 pt-3 pb-4">
                <b className="flex items-center gap-1.5 font-semibold tracking-[-0.01em]">
                  <Icon className={cn('size-3.5 shrink-0', hue)} />
                  {c.title}
                </b>
                <span className="text-[12px] leading-[1.45] text-muted-foreground">{c.blurb}</span>
                <span className="mt-auto ml-auto inline-flex -translate-x-1 items-center gap-1 pt-2 text-[12px] font-medium text-primary opacity-0 transition-[opacity,transform] duration-(--d-fast) ease-(--ease-out) group-hover:translate-x-0 group-hover:opacity-100 group-focus-visible:translate-x-0 group-focus-visible:opacity-100 motion-reduce:translate-x-0">
                  {on ? 'Chosen' : 'Start here'}
                  <ArrowRight className="size-[13px]" />
                </span>
              </div>
            </Tile>
          )
        })}
      </div>
      {panel}
    </div>
  )
}
