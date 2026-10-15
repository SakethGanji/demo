/**
 * Structured runtime error (UI-DESIGN §4.4, D16): `type: message`, the redacted traceback and
 * a "Go to line N" button when the error points at a user module.
 */
import { Bug } from 'lucide-react'
import type { RunError } from '@/shared/api/types'
import { Button } from '@/shared/components/ui/button'

export function RunErrorBox({
  error,
  onGoToLine,
  goToLabel,
}: {
  error: RunError
  onGoToLine?: (line: number, module: string | null | undefined) => void
  goToLabel?: string
}) {
  return (
    <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3" data-testid="run-error" data-code={error.code ?? ''}>
      <div className="flex items-start gap-2.5">
        <Bug className="mt-0.5 size-4 shrink-0 text-destructive" />
        <div className="min-w-0 flex-1">
          <div className="break-words text-[13px] font-medium" data-testid="run-error-message">
            {error.type}: {error.message}
          </div>
          {error.traceback && (
            <pre
              className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-4 text-muted-foreground"
              data-scroll-x="allowed"
            >
              {error.traceback}
            </pre>
          )}
        </div>
        {error.line != null && onGoToLine && (
          <Button
            variant="link"
            size="sm"
            className="shrink-0"
            data-testid="go-to-line"
            onClick={() => onGoToLine(error.line!, error.module)}
          >
            {goToLabel ?? `Go to line ${error.line}`}
          </Button>
        )}
      </div>
    </div>
  )
}
