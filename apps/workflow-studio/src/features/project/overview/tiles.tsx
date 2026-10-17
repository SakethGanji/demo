/**
 * Overview bento pieces: the 12-column grid (6 below ~1100px of container width, by container query on the
 * scrolling page) and the shared tile header. Tiles are `Tile`s, text sits in `Well`s.
 */
import { IconTile, type EntityHue } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'

/** span helpers (literal class names so Tailwind sees them) */
export const span = {
  hero: 'col-span-6 @min-[981px]/bt:col-span-8',
  runs: 'col-span-6 @min-[981px]/bt:col-span-4',
  mini: 'col-span-3',
  routes: 'col-span-6 @min-[981px]/bt:col-span-7',
  recent: 'col-span-6 @min-[981px]/bt:col-span-5',
  third: 'col-span-6 @min-[981px]/bt:col-span-4',
  half: 'col-span-3 @min-[981px]/bt:col-span-4',
}

export function TileHead({
  icon,
  hue,
  title,
  actions,
  className,
}: {
  icon: React.ReactNode
  hue: EntityHue
  title: React.ReactNode
  actions?: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex h-12 shrink-0 items-center gap-2.5 px-5', className)}>
      <IconTile hue={hue}>{icon}</IconTile>
      <h3 className="text-panel-title font-semibold tracking-[-0.01em]">{title}</h3>
      <div className="flex-1" />
      {actions}
    </div>
  )
}
