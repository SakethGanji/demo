import { cn } from '@/shared/lib/utils'

/** Recessed key cap (DESIGN §4). Inside a recess (a search field) it flips to raised; on a primary button it's a white tint. */
export function Kbd({ className, ...props }: React.HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      data-slot="kbd"
      className={cn(
        'inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-kbd bg-recess px-1 text-[10.5px] font-medium leading-none text-muted-foreground',
        'in-data-[slot=input-group]:bg-raise in-data-[slot=input-group]:shadow-raise-sm in-data-[recess]:bg-raise in-data-[recess]:shadow-raise-sm',
        'in-data-[slot=button]:bg-transparent in-data-[variant=default]:bg-primary-foreground/15 in-data-[variant=default]:text-primary-foreground/85 in-data-[variant=ai]:bg-primary-foreground/15 in-data-[variant=ai]:text-primary-foreground/85',
        className,
      )}
      {...props}
    />
  )
}
