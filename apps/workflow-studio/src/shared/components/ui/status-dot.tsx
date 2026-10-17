import { cn } from '@/shared/lib/utils'

export type DotTone = 'ok' | 'warn' | 'err' | 'idle' | 'running'

/** Status colour means status only (DESIGN §3). `running` rings while something is in flight. */
export function StatusDot({ tone, className, flat, ...props }: { tone: DotTone; flat?: boolean } & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      data-tone={tone}
      className={cn(
        'inline-block size-1.5 shrink-0 rounded-full',
        tone === 'ok' && 'bg-success text-success',
        tone === 'ok' && !flat && 'shadow-[0_0_0_3px_var(--success-soft)]',
        tone === 'warn' && 'bg-warning text-warning',
        tone === 'err' && 'bg-destructive text-destructive',
        tone === 'idle' && 'bg-faint',
        tone === 'running' && 'animate-live-ring bg-primary text-primary',
        className,
      )}
      {...props}
    />
  )
}

/** Map an execution status to a dot tone. */
export function runTone(status: string | null | undefined): DotTone {
  if (status === 'success') return 'ok'
  if (status === 'running' || status === 'waiting') return 'running'
  if (status === 'error' || status === 'failed' || status === 'crashed' || status === 'canceled') return 'err'
  return 'idle'
}
