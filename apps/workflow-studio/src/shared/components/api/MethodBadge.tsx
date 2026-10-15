/**
 * HTTP method badge: method colour text on a 10% tint of itself (UI-DESIGN §7.2/§7.3).
 */
import { cn } from '@/shared/lib/utils'

const STYLES: Record<string, string> = {
  GET: 'text-method-get bg-method-get/10',
  POST: 'text-method-post bg-method-post/10',
  PUT: 'text-method-put bg-method-put/10',
  PATCH: 'text-method-patch bg-method-patch/10',
  DELETE: 'text-method-delete bg-method-delete/10',
}

export function MethodBadge({
  method,
  size = 'md',
  className,
}: {
  method: string
  size?: 'sm' | 'md'
  className?: string
}) {
  const m = method.toUpperCase()
  return (
    <span
      data-method={m}
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-[5px] font-mono font-semibold tracking-[0.02em]',
        size === 'sm' ? 'h-4 w-[34px] text-[9px]' : 'h-[18px] w-11 text-[10px]',
        STYLES[m] ?? 'bg-muted text-muted-foreground',
        className,
      )}
    >
      {m}
    </span>
  )
}
