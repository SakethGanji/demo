/**
 * Empty state: a raised icon tile, a title, a short reason and actions. (`dust` is accepted for compatibility
 * and ignored: working pages have no star texture.)
 */
import { cn } from '@/shared/lib/utils'

export function EmptyState({
  icon,
  title,
  children,
  actions,
  className,
  dust,
  ...props
}: {
  icon?: React.ReactNode
  title?: React.ReactNode
  children?: React.ReactNode
  actions?: React.ReactNode
  className?: string
  dust?: boolean
} & Omit<React.HTMLAttributes<HTMLDivElement>, 'title'>) {
  return (
    <div
      className={cn('flex flex-col items-center justify-center gap-2 px-6 py-10 text-center', dust && 'rounded-well', className)}
      {...props}
    >
      {icon && (
        <div className="mb-1 inline-flex size-10 items-center justify-center rounded-panel bg-raise text-muted-foreground shadow-raise-sm [&_svg]:size-[18px] [&_svg]:stroke-[1.75]">
          {icon}
        </div>
      )}
      {title && <div className="text-[13.5px] font-semibold text-foreground">{title}</div>}
      {children && <div className="max-w-md text-[12.5px] leading-5 text-muted-foreground">{children}</div>}
      {actions && <div className="mt-2 flex flex-wrap items-center justify-center gap-2">{actions}</div>}
    </div>
  )
}
