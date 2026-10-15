/**
 * Generalised empty state (from ConversationEmptyState, UI-DESIGN §7.1).
 */
import { cn } from '@/shared/lib/utils'

export function EmptyState({
  icon,
  title,
  children,
  actions,
  className,
  ...props
}: {
  icon?: React.ReactNode
  title?: React.ReactNode
  children?: React.ReactNode
  actions?: React.ReactNode
  className?: string
} & Omit<React.HTMLAttributes<HTMLDivElement>, 'title'>) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-2 px-6 py-10 text-center', className)} {...props}>
      {icon && <div className="mb-1 text-muted-foreground [&_svg]:size-5">{icon}</div>}
      {title && <div className="text-[13px] font-medium text-foreground">{title}</div>}
      {children && <div className="max-w-md text-xs leading-5 text-muted-foreground">{children}</div>}
      {actions && <div className="mt-2 flex flex-wrap items-center justify-center gap-2">{actions}</div>}
    </div>
  )
}
