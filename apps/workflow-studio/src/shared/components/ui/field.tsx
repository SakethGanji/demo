import { cn } from '@/shared/lib/utils'

/** Label + control + help or inline error (field errors render inline, never as toasts, §8.1). */
export function Field({
  label,
  help,
  error,
  children,
  className,
  htmlFor,
  errorTestId,
}: {
  label?: React.ReactNode
  help?: React.ReactNode
  error?: React.ReactNode
  children: React.ReactNode
  className?: string
  htmlFor?: string
  errorTestId?: string
}) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      {label && (
        <label htmlFor={htmlFor} className="text-[12.5px] font-medium text-foreground">
          {label}
        </label>
      )}
      {children}
      {error ? (
        <div className="text-[12px] leading-4 text-destructive" role="alert" data-testid={errorTestId}>
          {error}
        </div>
      ) : help ? (
        <div className="text-[12px] leading-4 text-muted-foreground">{help}</div>
      ) : null}
    </div>
  )
}
