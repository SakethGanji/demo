import { cn } from '@/shared/lib/utils'

export function Kbd({ className, ...props }: React.HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn(
        'inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-[4px] border border-panel-border bg-foreground/5 px-1 text-[10.5px] font-medium leading-none text-muted-foreground',
        'in-data-[slot=button]:border-transparent in-data-[variant=default]:bg-white/15 in-data-[variant=default]:text-white/85',
        className,
      )}
      {...props}
    />
  )
}
