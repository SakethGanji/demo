/**
 * Surfaces (DESIGN §2, §4). Feature code builds every box from these instead of its own classes:
 *   Tile         glass card / bento tile / pane (the `tile` recipe). `interactive` adds the `glass-hover` lift and press.
 *   Well         a recess inside the glass for text: tables, lists, code (the `well` recipe). Never a well in a well.
 *   WellList/Row a list in one Well with hairline rows (never a box per row).
 *   Page         the scrolling page body (the one page padding).
 *   PageHeader   the 26px page title with a meta row and actions.
 *   SectionHeader a tile header: entity-hued icon tile, 13.5px title, sub, actions.
 */
import * as React from 'react'
import { cn } from '@/shared/lib/utils'

/**
 * The scrolling body of a page inside the workspace sheet: transparent, 32/28px padding (the one page inset).
 * Full-height pages that scroll inside a pane (Runs, Data) keep their own frame but use the same px-8 / pt-7.
 */
export const Page = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(function Page(
  { className, ...props },
  ref,
) {
  return <div ref={ref} data-slot="page" className={cn('h-full min-h-0 overflow-x-hidden overflow-y-auto px-8 py-7', className)} {...props} />
})

export type EntityHue = 'backend' | 'data' | 'runs' | 'apps' | 'secrets' | 'ai' | 'flow' | 'release' | 'home'

/** text colour class per entity hue (icons and small tints only) */
export const hueText: Record<EntityHue, string> = {
  backend: 'text-hue-backend',
  data: 'text-hue-data',
  runs: 'text-hue-runs',
  apps: 'text-hue-apps',
  secrets: 'text-hue-secrets',
  ai: 'text-hue-ai',
  flow: 'text-hue-flow',
  release: 'text-hue-release',
  home: 'text-hue-home',
}

export const Tile = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & { interactive?: boolean; editor?: boolean; as?: 'div' | 'section' | 'article' }
>(function Tile({ className, interactive, editor, as: As = 'div', ...props }, ref) {
  return (
    <As
      ref={ref}
      data-slot="tile"
      className={cn(
        editor ? 'glass-editor rounded-panel' : 'tile',
        'relative min-w-0',
        interactive && 'glass-hover',
        className,
      )}
      {...props}
    />
  )
})

export const Well = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(function Well(
  { className, ...props },
  ref,
) {
  return <div ref={ref} data-slot="well" className={cn('well min-w-0', className)} {...props} />
})

/** A list inside one Well: hairline rows, never a glass box per row. */
export function WellList({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <Well className={cn('flex flex-col divide-y divide-hairline p-0', className)} {...props} /> // design-ok: the WellList recipe itself
}

export function WellRow({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex items-center gap-2 px-3 py-2 text-[12.5px]', className)} {...props} />
}

/** Icon in a 10% tint of its entity hue. */
export function IconTile({ hue, children, className }: { hue: EntityHue; children: React.ReactNode; className?: string }) {
  return (
    <span className={cn('icon-tile [&_svg]:size-[15px] [&_svg]:stroke-[1.75]', hueText[hue], className)} aria-hidden>
      {children}
    </span>
  )
}

export function SectionHeader({
  title,
  sub,
  actions,
  icon,
  hue,
  className,
  titleClassName,
}: {
  title: React.ReactNode
  sub?: React.ReactNode
  actions?: React.ReactNode
  icon?: React.ReactNode
  hue?: EntityHue
  className?: string
  titleClassName?: string
}) {
  return (
    <div className={cn('flex min-h-7 items-center gap-2.5', className)} data-slot="section-header">
      {icon && hue && <IconTile hue={hue}>{icon}</IconTile>}
      {icon && !hue && <span className="icon-tile text-muted-foreground [&_svg]:size-[15px]">{icon}</span>}
      <span className={cn('text-panel-title font-semibold tracking-[-0.01em]', titleClassName)}>{title}</span>
      {sub && <span className="min-w-0 truncate text-[12.5px] text-muted-foreground">{sub}</span>}
      <div className="flex-1" />
      {actions}
    </div>
  )
}

export function PageHeader({
  title,
  meta,
  actions,
  icon,
  hue,
  className,
  children,
  ...props
}: {
  title: React.ReactNode
  meta?: React.ReactNode
  actions?: React.ReactNode
  icon?: React.ReactNode
  hue?: EntityHue
  className?: string
  children?: React.ReactNode
} & Omit<React.HTMLAttributes<HTMLElement>, 'title'>) {
  return (
    <header className={cn('flex flex-col gap-1.5', className)} data-slot="page-header" {...props}>
      <div className="flex min-w-0 items-center gap-3">
        {icon && hue && <IconTile hue={hue} className="size-8 [&_svg]:size-[17px]">{icon}</IconTile>}
        <h1 className="min-w-0 truncate text-title leading-[1.1] font-[650] tracking-[-0.03em]">{title}</h1>
        <div className="flex-1" />
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      {meta && <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-muted-foreground">{meta}</div>}
      {children}
    </header>
  )
}

/** "·" separator for meta rows */
export function Dot() {
  return <span className="text-faint" aria-hidden>·</span>
}

/** A flat tinted line of text (no ring): warn, ai, info, error, ok. */
export function Callout({
  tone = 'info',
  icon,
  children,
  className,
  ...props
}: { tone?: 'warn' | 'ai' | 'info' | 'error' | 'ok'; icon?: React.ReactNode; children: React.ReactNode; className?: string } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'flex min-w-0 items-center gap-2 rounded-ctl px-3 py-2 text-[12.5px] leading-[18px] [&_svg]:size-3.5 [&_svg]:shrink-0',
        tone === 'warn' && 'bg-warning-soft text-warning-text',
        tone === 'ai' && 'bg-ai-soft text-ai-text',
        tone === 'info' && 'bg-brand-soft text-foreground [&_svg]:text-primary',
        tone === 'error' && 'bg-destructive-soft text-destructive',
        tone === 'ok' && 'bg-success-soft text-success-text',
        className,
      )}
      {...props}
    >
      {icon}
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

/** A thin progress track (recessed) with a primary fill. */
export function Progress({ value, className }: { value: number; className?: string }) {
  return (
    <div className={cn('h-1.5 w-full overflow-hidden rounded-full bg-recess', className)} role="progressbar" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100}>
      <div className="h-full rounded-full bg-primary transition-[width] duration-(--d-slow) ease-(--ease-out)" style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  )
}

/** A stat: label, a hero value (optionally rolling) and a meta line. */
export function Stat({
  label,
  value,
  meta,
  icon,
  hue,
  className,
  valueClassName,
  testId,
}: {
  label: React.ReactNode
  value: React.ReactNode
  meta?: React.ReactNode
  icon?: React.ReactNode
  hue?: EntityHue
  className?: string
  valueClassName?: string
  testId?: string
}) {
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', className)} data-testid={testId}>
      <div className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
        {icon && hue ? <IconTile hue={hue} className="size-6">{icon}</IconTile> : icon}
        {label}
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className={cn('text-[26px] leading-none font-semibold tracking-[-0.03em] tabular-nums', valueClassName)} data-testid={testId ? `${testId}-value` : undefined}>
          {value}
        </span>
        {meta && <span className="truncate text-[12px] text-muted-foreground">{meta}</span>}
      </div>
    </div>
  )
}
