/**
 * Empty state: a raised icon tile, a title, a short reason and actions. (`dust` is accepted for compatibility
 * and ignored: working pages have no star texture.) `compact` is the in-pane size (tester panes, side panels).
 */
import { cn } from '@/shared/lib/utils'

export function EmptyState({
  icon,
  title,
  children,
  actions,
  className,
  dust,
  compact,
  ...props
}: {
  icon?: React.ReactNode
  title?: React.ReactNode
  children?: React.ReactNode
  actions?: React.ReactNode
  className?: string
  dust?: boolean
  compact?: boolean
} & Omit<React.HTMLAttributes<HTMLDivElement>, 'title'>) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center text-center',
        compact ? 'gap-1.5 px-3 py-8' : 'gap-2 px-6 py-10',
        dust && 'rounded-well',
        className,
      )}
      {...props}
    >
      {icon && (
        <div
          className={cn(
            'mb-1 inline-flex items-center justify-center rounded-panel bg-raise text-muted-foreground shadow-raise-sm [&_svg]:stroke-[1.75]',
            compact ? 'size-9 [&_svg]:size-4' : 'size-10 [&_svg]:size-[18px]',
          )}
        >
          {icon}
        </div>
      )}
      {title && <div className={cn('font-semibold text-foreground', compact ? 'text-[12.5px]' : 'text-[13.5px]')}>{title}</div>}
      {children && <div className={cn('max-w-md leading-5 text-muted-foreground', compact ? 'text-[12px]' : 'text-[12.5px]')}>{children}</div>}
      {actions && <div className="mt-2 flex flex-wrap items-center justify-center gap-2">{actions}</div>}
    </div>
  )
}
