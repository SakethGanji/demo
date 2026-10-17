/**
 * HTTP method chip (DESIGN §3): the method colour as text on a 13% tint of itself, no ring, `--r-kbd`.
 * Keeps `data-method` (asserted by the e2e specs) and the fixed widths so route lists align.
 */
import { cn } from '@/shared/lib/utils'

const STYLES: Record<string, string> = {
  GET: 'text-method-get',
  POST: 'text-method-post',
  PUT: 'text-method-put',
  PATCH: 'text-method-patch',
  DELETE: 'text-method-delete',
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
        'inline-flex shrink-0 items-center justify-center rounded-kbd bg-(--method-tint) font-mono font-semibold tracking-[0.02em]',
        size === 'sm' ? 'h-4 w-[34px] text-[9px]' : 'h-[18px] w-[38px] text-[9.5px]',
        STYLES[m] ?? 'text-muted-foreground',
        className,
      )}
    >
      {m}
    </span>
  )
}

/** The shared name used by new code. */
export const MethodChip = MethodBadge
