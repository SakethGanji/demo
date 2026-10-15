import { cn } from '@/shared/lib/utils'

export type DotTone = 'ok' | 'warn' | 'err' | 'idle' | 'running'

export function StatusDot({ tone, className, ...props }: { tone: DotTone } & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      data-tone={tone}
      className={cn(
        'inline-block size-1.5 shrink-0 rounded-full',
        tone === 'ok' && 'bg-success shadow-[0_0_0_3px_color-mix(in_srgb,var(--success)_18%,transparent)]',
        tone === 'warn' && 'bg-warning',
        tone === 'err' && 'bg-destructive',
        tone === 'idle' && 'bg-border',
        tone === 'running' && 'animate-pulse bg-primary',
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
